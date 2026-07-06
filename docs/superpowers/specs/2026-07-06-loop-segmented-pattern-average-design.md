# Loop-Segmented Pattern Averages — Design

**Date:** 2026-07-06
**Status:** Approved
**Branch context:** `codex/production-observability`
**Supersedes the averaging half of:** `2026-07-06-runway-classified-average-design.md`
(the runway classification and endpoint plumbing stay; only how a "circuit" is
sliced and averaged changes).

## Problem

Average mode is supposed to show, per runway, one continuous **closed oval**
(the traffic pattern racetrack, long side along the runway) plus a
standard-deviation band — the mean of that runway's repeated laps. Instead it
renders broken, non-closing arcs.

Root cause, confirmed against live KLMO data (`/api/airports/KLMO/pattern-circuits?days=7`,
765 circuits):

1. **Circuits are open arcs, never loops.** Each circuit is a fixed ±75 s window
   centered on one detected operation. Start→end closure distances run
   0.34–3.69 nm; not a single circuit closes. Averaging open arcs point-by-point
   cannot produce a closed oval, regardless of orientation. (The prior spec
   explicitly deferred "true loop-boundary segmentation" — this is that deferred
   work.)
2. **The `area` class dominates and is diffuse.** 486 of 765 circuits (64%) are
   no-runway "circle" ops, scattered across all 360° around the field — circling
   and overflights, not a coherent loop. They belong in the view only as faint
   context, not in an averaged oval.

Whole-loop coverage *does* exist in the data: around each runway class's own
centroid the arc fragments collectively span the loop (rwy 29: 33/36 sectors;
rwy 11: 28/36). And consecutive same-runway operations by one aircraft cluster
4–8 min apart (rwy 29: 78 such pairs; rwy 11: 59) — one GA pattern lap. So a lap
cut **touchdown → pattern → touchdown** between two consecutive operations is a
naturally closed, phase-aligned loop we can average directly.

## Decisions (approved)

1. **Lap segmentation on the backend.** A pattern lap = the archived-track slice
   between two **consecutive same-runway operations** (touch-and-go / low-approach)
   of one aircraft whose timestamps are **60–480 s** apart. That slice is a
   closed loop, classified by the runway, phase-aligned at the touchdown.
2. **`area` and leftovers become faint context, not ovals.** Circle ops and any
   runway op that isn't part of a consecutive same-runway pair keep a ±75 s
   window slice, flagged `is_loop: false`, and render as faint hairlines beneath
   the ovals. They are excluded from the mean/σ computation.
3. **Averaging stays on the frontend**, unchanged in spirit: closed, phase-aligned
   laps feed the existing `meanAndBand` (resample → point-wise mean →
   cross-track σ band), now yielding a genuinely closed oval per runway. The mean
   ring is explicitly closed for rendering.
4. **σ band** semantics unchanged: per-point cross-track ±k·σ corridor, k
   configurable client-side (1σ/2σ/3σ), no refetch.

## Parameters

- `MIN_LAP_S = 60`, `MAX_LAP_S = 480` — the gap window that counts a pair of
  consecutive same-runway ops as one lap. Tunable module constants; chosen from
  the 4–8 min GA-pattern cluster in the KLMO data (excludes 20+ min "left and
  came back" pairs).
- `CONTEXT_WINDOW_S = 75` — the ±window for context (area / unpaired) slices
  (the existing `CIRCUIT_WINDOW_S`).
- `MIN_LAP_POINTS = 4` — a lap must keep ≥4 points after RDP to be a usable loop
  (below that it's degenerate; drop it).

## Architecture

### Backend — `backend/app/pattern_circuits.py`

`build_circuits(operations, tracks_by_icao, *, ...)` is reworked to emit two
kinds of circuit and an `is_loop` flag on each. Signature and return tuple shape
(`(circuits, counts_by_class, total)`) are preserved; the endpoint call site is
unchanged.

- **Group operations by lowercased `icao24`** (icao24 case-normalization for the
  `track_archive` join stays exactly as today), then sort each aircraft's ops by
  timestamp.
- **Laps (`is_loop: true`):** walk each aircraft's ops; for each adjacent pair
  `(op_i, op_{i+1})` where both are runway ops (`touch_and_go`/`low_approach`)
  with a non-null runway, **same runway**, and `MIN_LAP_S ≤ gap ≤ MAX_LAP_S`,
  slice that aircraft's samples to `[op_i.timestamp, op_{i+1}.timestamp]`,
  RDP-simplify, require ≥ `MIN_LAP_POINTS`, tag
  `class = runway_id`, `is_loop = true`. An op consumed as the *end* of one lap
  may also open the next (chains of touch-and-goes → consecutive laps).
- **Context (`is_loop: false`):** every operation **not** captured inside a lap —
  all circle/`area` ops, and runway ops with no qualifying same-runway neighbor —
  gets the existing ±`CONTEXT_WINDOW_S` window slice, RDP-simplified,
  `class = runway_id or "area"`, `is_loop = false`. Overlapping same-`(icao, class)`
  context windows dedup as today (earliest kept).
- **Counts:** `counts_by_class` counts **laps only** (`is_loop: true`) per runway
  class — this is what drives "enough to draw an oval" and the key. Add
  `context_count` = number of `is_loop: false` circuits. `total` (→
  `total_circuits`) = laps + context.
- **Cap** unchanged (`DEFAULT_CIRCUIT_CAP`, most recent first, `total` reported
  pre-cap). Laps are preferred over context when capping (sort loops first, then
  by recency) so a busy field never caps away its ovals.

Each circuit dict: `{"class", "runway_id", "icao24", "is_loop", "samples":
[{"lat","lon","timestamp"}]}`.

### Backend — endpoint (`main.py`)

`GET /airports/{icao}/pattern-circuits` is unchanged in signature. Response gains
`is_loop` per circuit and a top-level `context_count`; `counts_by_class` now
means laps-per-runway. Additive and backward compatible.

### Frontend

- **API types (`lib/api.ts`):** `PatternCircuit` gains `is_loop: boolean`;
  `PatternCircuitsResponse` gains `context_count: number`.
- **Geometry (`lib/averagePath.ts`):** `meanAndBand` is unchanged in method
  (keep the farthest-point-first orientation added earlier as a safety net —
  laps are already same-direction, so it's a no-op for them). Callers pass only
  `is_loop` laps. Add an explicit close: the returned `mean` is rendered as a
  closed ring (append the first point, or draw the band/line closed).
- **MapView Average render:** partition `historyCircuits` into
  `loops = circuits.filter(c => c.is_loop)` and `context = circuits.filter(c => !c.is_loop)`.
  - `loops` → `meanAndBand(..., {sigmaK, origin:[airport.lon,airport.lat]})` →
    per class a translucent `history_band` polygon under a **closed** bold
    `history_avg` line (per-class color, existing styling).
  - `context` → faint altitude-neutral hairlines (`history_context` kind, a dim
    fixed alpha, reusing the Lines-mode `smoothSegment` path style at low
    opacity), drawn beneath the ovals.
- **App (`App.tsx`):** `averageDrawable` already keys off `counts_by_class`
  (now laps) ≥ `AVERAGE_MIN_PER_CLASS` — unchanged. The class **key** lists the
  runway ovals from `counts_by_class`; append a single muted "Area (context)"
  entry when `context_count > 0`. Caption reads e.g.
  `"78 laps · rwy 29, 11 · 486 context tracks"` (fall back to the existing
  "not enough repeated circuits" copy when no class reaches the threshold).

## Data flow

`operations + track_archive`
→ `build_circuits` (lap pairing + context windows, `is_loop` tag)
→ endpoint (`counts_by_class` = laps, `context_count`)
→ App fetch (`historyCircuits`)
→ MapView split: laps → `meanAndBand` → closed ovals + σ bands; context → faint
hairlines.

## Testing

**Backend (`test_pattern_circuits.py`):**
- Two consecutive same-runway ops 300 s apart → one `is_loop: true` lap sliced to
  `[t0, t1]`, class = runway.
- Gap > `MAX_LAP_S` (e.g. 1200 s) → no lap; both ops become context.
- Gap < `MIN_LAP_S` → no lap (context).
- Consecutive ops on **different** runways → no lap (context each).
- A chain of three same-runway ops → two consecutive laps.
- Circle op → `is_loop: false`, class `area`.
- Lone runway op (no neighbor) → context.
- Lap slice with < `MIN_LAP_POINTS` after RDP → dropped.
- `counts_by_class` counts laps only; `context_count` counts the rest;
  `total` = laps + context; cap keeps loops.

**Frontend:**
- `meanAndBand` on closed same-class laps → mean that closes (first ≈ last point)
  and a band that scales with `sigmaK` (existing tests hold; add a closed-loop
  case).
- MapView/render split (loops averaged, context excluded) covered by the
  partition logic; band rendering verified visually.

## Deploy

Backend change → rebuild **api** (worker shares the image context) **and web**,
one service at a time per `DEPLOY.md`. No schema/migration change (reads existing
`operations` + `track_archive`).

## Out of scope (v1)

- Left/right (turn-direction) sub-classes; `turn_direction` is available but unused.
- Winding-number / Poincaré-section lap detection for laps **not** bounded by two
  operations (e.g. a single uninterrupted multi-lap orbit) — consecutive-op
  pairing is enough for the observed pattern work.
- Averaging the `area` class into a shape (kept as faint context only).
- Runway-heading-aware alignment of the mean (the touchdown-anchored phase
  alignment is enough).
