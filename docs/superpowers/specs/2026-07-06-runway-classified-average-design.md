# Runway-Classified Average Loops + Std-Dev Band — Design

**Date:** 2026-07-06
**Status:** Approved
**Branch context:** `codex/production-observability`
**Builds on:** the Historical Track Density overlay
(`2026-07-06-historical-track-density-design.md`)

## Problem

The historical overlay's **Average** mode currently groups tracks by the
compass bearing from the airport to each track's centroid and averages them —
which lumps arrivals and departures together, ignores runways, and shows
nothing until a bearing sector has ≥3 tracks. Users find it confusing and
arbitrary: it doesn't correspond to how planes actually fly the pattern.

Rework Average so it reflects real pattern work:
1. **Classify each circuit (loop)** by the runway it uses — a touch-and-go or
   low-approach on runway "29" → class `29`; a circuit that only circles with
   no runway touch → class `"area"`.
2. **Average within each class** into a representative mean loop.
3. **Draw a configurable standard-deviation band** around each mean loop showing
   the spread of the circuits in that class.

## What already exists (and what we reuse)

- **`operations` table** (`backend/app/db.py`): one row per detected operation
  with `type` ∈ {`circle`, `touch_and_go`, `low_approach`, `pass_over_user`},
  `timestamp`, `icao24`, `runway_id` (populated for touch_and_go / low_approach;
  NULL for circle), `turn_direction`, etc. Read via
  `db.read_operations(conn, icao, start_ts, end_ts, types=None)`.
- **`track_archive`** cold tier with 7-day retention (from the density feature).
  Read per-aircraft via `db.bulk_read_track_archive(conn, icao24s, start, end)`.
- **`simplify.rdp_keep_mask`** — RDP polyline simplification.
- **Frontend `averagePath.ts`** — `resamplePath`, orientation, averaging
  helpers. The **Average** mode lives in `MapView.tsx` (`history_avg` features)
  and `App.tsx` (controls + fetch).
- Track-history fetch/render plumbing (`getTrackHistory`, `historyMode`,
  overlay state machine, controls panel).

## Decisions (approved)

1. **Per-circuit, classified by runway touched.** Each circuit's class is the
   `runway_id` of its touch-and-go / low-approach; a circuit that only circles
   (no runway touch) is class `"area"`. One combined `"area"` class in v1
   (left/right turn split deferred).
2. **Circuit geometry = a fixed ±75 s window** of the aircraft's archived track
   centered on each detected operation's `timestamp`. Robust and yields
   consistent circuit slices without brittle loop-boundary logic. Overlapping
   windows for the same aircraft+class are deduped (see below).
3. **Std-dev band = a cross-track corridor** around each class's mean loop: at
   each point along the mean, compute the perpendicular spread σ of the member
   circuits, offset the mean ±k·σ along the local normal, and fill between the
   two offset polylines. **k configurable** (default **1σ**, presets 1σ/2σ/3σ)
   via a controls-panel control — σ/band computed client-side so k adjusts with
   no refetch.
4. **Classes rendered together**, each a distinct color with a small key
   (e.g. Rwy 29, Rwy 11, Area) — bold mean line + translucent band per class.

## Approach (chosen)

Split by what each side knows:
- **Backend classifies.** It owns the `operations` + `track_archive` data, so a
  new endpoint returns **classified circuit polylines**: for each qualifying
  operation, the ±75 s archived-track slice, RDP-simplified, tagged with its
  class.
- **Frontend does the geometry.** Extends `averagePath.ts` to group circuits by
  class, resample + orient + average into a mean loop, compute per-point
  cross-track σ, and draw mean line + ±k·σ band. Keeping σ/band on the client
  makes the k control instant (no refetch).

Rejected: computing mean+σ on the backend (duplicates resample/orient geometry
in Python; makes k a round-trip). Rejected: per-flight classification (means of
multi-lap flights are messy and unlike a single pattern).

## Architecture

### Backend

**New module `backend/app/pattern_circuits.py`:**
- `CIRCUIT_WINDOW_S = 75`, `DEFAULT_CIRCUIT_CAP = 2000`,
  `SIMPLIFY_EPSILON_DEG = 1e-4`, `CIRCUIT_TYPES = ("touch_and_go",
  "low_approach", "circle")`.
- `classify(op) -> tuple[str, str | None]` → `(class_label, runway_id)`:
  runway ops → `(runway_id, runway_id)`; circle → `("area", None)`. A
  runway op with a NULL `runway_id` falls back to `("area", None)`.
- **icao24 case:** `track_archive` stores lowercased `icao24` keys
  (`archive_track_samples` lowercases), so operations' `icao24` must be
  `.lower()`-normalized before joining to `tracks_by_icao` — otherwise the join
  silently yields zero circuits.
- `build_circuits(operations, tracks_by_icao, *, window_s, epsilon, cap)
  -> tuple[list[dict], dict, int]` → `(circuits, counts_by_class, total)`:
  for each operation, slice its aircraft's samples to
  `[timestamp - window_s, timestamp + window_s]`, drop slices < 2 points,
  RDP-simplify, tag `class`/`runway_id`. **Dedup:** operations of the same
  `(icao24, class)` whose windows overlap collapse to one circuit (keeps the
  earliest; avoids double-counting a single lap that trips multiple detectors).
  Cap total circuits (most recent first); report `total` before cap.
- Each circuit dict: `{"class": str, "runway_id": str | None, "icao24": str,
  "samples": [{"lat","lon","timestamp"}]}`.

**New endpoint `GET /airports/{icao}/pattern-circuits?days=<1-7>`** (in
`main.py`, after the track-history endpoint):
- Validate `days` ∈ [1,7]; resolve airport (404 if missing); optional `_now`
  test hook (same pattern as track-history).
- `ops = db.read_operations(conn, icao, start_ts, now, types=list(CIRCUIT_TYPES))`.
- `tracks = db.bulk_read_track_archive(conn, [op.icao24 …unique], start_ts − window, now + window)`.
- `circuits, counts_by_class, total = pattern_circuits.build_circuits(...)`.
- Response: `{ airport{icao,lat,lon,elevation_ft}, days, window{start_ts,end_ts},
  circuits, counts_by_class, total_circuits, truncated }`.

### Frontend

- **API client** `getPatternCircuits(icao, days)` + types (`PatternCircuit`,
  `PatternCircuitsResponse`) in `lib/api.ts`.
- **Geometry** — extend `lib/averagePath.ts` with
  `meanAndBand(circuits, opts) -> ClassAverage[]` where
  `ClassAverage = { class: string; runwayId: string | null; count: number;
  mean: LonLat[]; band: LonLat[] }`:
  - group circuits by `class`; per class with ≥ `minCount` (default 3):
    resample each to `resampleN` (default 48), orient farthest-point-first,
    average → `mean`.
  - per mean point i, cross-track σ = stddev of members' signed perpendicular
    offset from the mean at i (project member point onto the local normal).
  - `band` = mean offset by `+k·σ` forward then `−k·σ` back along local normals,
    as one closed ring (a filled corridor). `k = opts.sigmaK` (default 1).
- **State (App.tsx):** `historySigmaK` (default 1); fetch pattern-circuits when
  `mapOverlay==="history" && historyMode==="average"` (separate from the
  track-history fetch used by Lines/Density); pass `circuits` + `sigmaK` to
  MapView; caption shows per-class counts (or "not enough circuits yet").
- **Controls:** a σ control (presets **1σ / 2σ / 3σ**) shown in Average mode,
  same panel pattern as Days/Opacity. A small class **key** (color swatch +
  "Rwy 29" / "Area").
- **MapView Average mode:** replace the bearing-bucket render. Draw per class a
  `history_band` filled polygon (translucent, class color) beneath a
  `history_avg` mean line (bold, class color). Assign class colors from a small
  ordered palette (runways first by number, "area" last/neutral).
- **Remove** the now-unused bearing-bucket `averagePaths` and its
  `averageGroupCount` usage in App (replaced by the class-based path). Keep
  `resamplePath` (reused by `meanAndBand`).

## Testing
- **Backend:** `classify` (runway vs area vs null-runway fallback); circuit
  windowing (slice bounds, <2-point drop); dedup of overlapping same-class
  windows; cap + `total`; endpoint (class tagging, counts_by_class, truncated,
  404, 422 out-of-range).
- **Frontend:** `meanAndBand` — mean of parallel circuits lands between them;
  band half-width scales linearly with `sigmaK` and with the members' spread;
  a class below `minCount` is omitted; orientation consistent. Band **rendering**
  verified visually.

## Out of scope (v1)
- Left/right (turn-direction) sub-classes.
- True loop-boundary segmentation (fixed ±75 s window instead).
- Runway-heading-aware alignment of the mean (raw geographic mean is enough).
- Backfilling operations for older days (coverage grows forward).
