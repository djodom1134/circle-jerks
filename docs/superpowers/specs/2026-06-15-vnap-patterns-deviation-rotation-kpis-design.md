# VNAP Patterns, Deviation, Rotation & Airport KPIs — Design

- **Date:** 2026-06-15
- **Status:** Approved (design); pending implementation plan
- **Scope:** One phased spec covering four interrelated features on a shared persistence foundation.

## Context & problem

The app (circlejerks.live) tracks circling / pattern-flying aircraft near airports for
noise-complaint purposes. Today, detected events (circles, touch-and-gos, low approaches,
passes-over-user) are **recomputed on every `/scan`** and live only in the hot store (Redis/Valkey
~4h) and cold archive (SQLite `track_archive` ~24h). Only user-submitted complaints persist
long-term. There is no notion of a recommended noise-abatement route, no measure of how far an
aircraft strays from one, no tracking of which runway direction the field is using, and no
airport-level statistics page.

This spec adds:

1. A map-based tool to **draw a VNAP (Voluntary Noise Abatement Procedure) pattern** per runway
   direction, stored in the DB so anyone can view and edit it.
2. **Deviation quantification** — how far each circling aircraft strays from the matched pattern.
3. **Rotation / "cowboy" detection** — the active runway end in use, when it changes, and who
   caused the change.
4. A **KPIs / report page** per airport aggregating all of the above plus existing metrics.

## Decisions (locked during brainstorming)

| # | Decision | Choice | Rationale |
|---|----------|--------|-----------|
| 1 | Work structure | One phased spec | Features are tightly coupled (deviation needs patterns; KPIs need everything). |
| 2 | Historical data | Build a **persistent operations log** in SQLite | KPIs/trends need history beyond the ephemeral 4–24h windows. |
| 3 | Pattern edit access | **Open editing + version history** | User wants it collaborative ("for others to see and modify"); no user login exists. Guardrails + revert mitigate vandalism. |
| 4 | Rotation definition | **Active runway end** | The change that flips headwind/pattern for everyone; turn direction is usually fixed per runway. |
| 5 | Deviation metrics | **Defaults** | A = time & % beyond a ~0.25 nm corridor; B = mean perpendicular distance (nm); peak/max as secondary. |
| 6 | Pattern matching | **Direction-matched** | Match aircraft travel direction to the pattern's arrows, then nearest among those. |
| 7 | Cowboy labelling | **Always cowboy** | First aircraft to flip the active end is tagged regardless of wind; wind still stored + `wind_favored_new` recorded for context. |
| 8 | "Established" threshold | **≥3 consecutive same-end ops OR ~10 min agreement** | Change fires only when the new dominant end holds — avoids single-go-around noise. |
| 9 | Drawing model | **Click-to-place Catmull-Rom control points** | Simplest; the point is the handle. "Generate from runway" template as a one-click accelerator. Bézier tangent handles excluded by default. |
| 10 | KPIs layout | **Scrollable dashboard** | Fits the existing plain-CSS app; fastest to ship; handles many metrics without tab/grid plumbing. |
| 11 | Charting | **Recharts** | Lightweight, React-friendly; no charting lib currently installed. |
| 12 | Patterns per end | **One recommended pattern per runway end**, versioned | YAGNI on multiple named patterns; revert covers iteration. |

## Architecture overview

```
ADS-B feeds → worker (poll loop) → detectors (stateless geometry)
                                       │
                                       ├─ finalize completed ops ─┐
                                       │                          ▼
                                  deviation.py            operations (SQLite, durable)
                                  (match pattern,                  │
                                   perp distance)         flow.py (active end, changes)
                                                                   │
                          runway_patterns (drawn shapes) ──────────┤
                                                                   ▼
FastAPI endpoints  ◄──────────────────────────────  runway_flow / runway_changes
   /runways/.../pattern (CRUD + history + revert)
   /airports/{icao}/stats   /airports/{icao}/flow
                                       │
React + OpenLayers SPA ────────────────┘
   PatternEditor (Draw/Modify)   StatsPage (/stats, Recharts)   map flow badge
```

Build order (phases):

- **Phase 1 — Foundation:** `operations` persistence + worker write path; `runway_patterns` data
  model; pattern drawing tool + map overlay; pattern CRUD/history/revert API.
- **Phase 2 — Deviation:** `deviation.py`; pattern matching + perpendicular-distance metrics;
  stored on each op; per-aircraft surfacing.
- **Phase 3 — Rotation/cowboy:** `flow.py`; active-end timeline; `runway_changes` + cowboy
  attribution; wind snapshot; into-headwind metric; map badge.
- **Phase 4 — KPIs page:** `/stats` scrollable dashboard + `GET /airports/{icao}/stats`; Recharts.

## Data model (new SQLite tables)

All tables created in `backend/app/db.py` `SCHEMA`, keyed by `icao` for multi-airport support.
Existing `airports` and `runways` tables are reused; a "runway end" is an existing `runways` row
(`(icao, runway_id)` with `heading_deg`, `lat_threshold`, `lon_threshold`, `length_ft`).

### `runway_patterns` (append-only, versioned)
| Column | Type | Notes |
|--------|------|-------|
| `id` | INTEGER PK | autoincrement |
| `icao` | TEXT | FK → airports.icao |
| `runway_id` | TEXT | FK → runways.runway_id (the end) |
| `geometry_json` | TEXT | `{ "points":[{lat,lon}…], "closed":bool, "spline":"catmull-rom" }` (point order defines direction) |
| `name` | TEXT | optional label |
| `version` | INTEGER | monotonically increasing per `(icao,runway_id)` |
| `is_current` | INTEGER | 1 for the active row, 0 for history |
| `locked` | INTEGER | admin-only lock against edits (default 0) |
| `editor_visitor_id` | TEXT | from existing `visitor_id` |
| `editor_ip` | TEXT | |
| `change_note` | TEXT | optional |
| `created_at` | INTEGER | unix |

Index: `(icao, runway_id, is_current)`. Saving a new version inserts a new row, flips the prior
`is_current` to 0. Revert = clone an older version's `geometry_json` into a new current version.

### `operations` (durable ops log — the foundation)
| Column | Type | Notes |
|--------|------|-------|
| `id` | TEXT PK | deterministic hash (matches detector event id) → idempotent upsert |
| `icao` | TEXT | airport |
| `icao24` | TEXT | aircraft hex |
| `callsign` / `registration` | TEXT | denormalized snapshot |
| `type` | TEXT | circle \| touch_and_go \| low_approach \| pass_over_user |
| `timestamp` | INTEGER | unix |
| `runway_id` / `runway_heading_deg` | TEXT / INTEGER | assigned end if applicable |
| `turn_direction` | TEXT | left/right (circles) |
| `min_altitude_ft_agl` | INTEGER | |
| `matched_pattern_id` | INTEGER | FK → runway_patterns.id, nullable |
| `deviation_mean_nm` | REAL | KPI B (nullable when no pattern) |
| `deviation_peak_nm` | REAL | secondary |
| `time_off_pattern_s` | INTEGER | KPI A absolute |
| `time_total_s` | INTEGER | denominator |
| `pct_off_pattern` | REAL | KPI A percentage |
| `wind_from_deg` / `wind_speed_kt` | INTEGER / REAL | snapshot at op time |
| `headwind_kt` | REAL | signed component along the runway |
| `origin_airport_icao` / `origin_label` | TEXT | best-effort |
| `operator` / `flight_school` | TEXT | best-effort, confidence-tagged |
| `created_at` | INTEGER | unix |

Indexes: `(icao, timestamp)`, `(icao24)`, `(type)`.

### `runway_flow` (active-end timeline)
`id` PK, `icao`, `active_runway_id`, `established_at`, `ended_at` (null = current),
`wind_from_deg`, `wind_speed_kt`, `op_count`. Index `(icao, established_at)`.

### `runway_changes` (cowboy log)
`id` PK, `icao`, `from_runway_id`, `to_runway_id`, `changed_at`,
`cowboy_icao24`, `cowboy_callsign`, `cowboy_registration`,
`wind_from_deg`, `wind_speed_kt`, `wind_favored_new` (INTEGER, context only),
`trigger_op_id` (FK → operations.id). Index `(icao, changed_at)`.

## Feature 1 — Pattern drawing tool

**Frontend** (`frontend/src/components/MapView.tsx` + new `PatternEditor.tsx`):

- **Edit patterns** mode toggle. Select a runway end (dropdown of the airport's `runways`).
- **Generate from runway**: builds a standard rectangular circuit (upwind/crosswind/downwind/
  base/final) from the end's `heading_deg`, `lat_threshold`, `lon_threshold` at configurable
  pattern width/leg lengths, with left- or right-traffic toggle. Pre-seeds editable control points.
- **Click-to-place**: drop control points; preview smoothed with the existing Catmull-Rom
  `smoothSegment()`; drag points via OL `Modify`; **direction arrows** rendered along the spline;
  optional close-loop toggle.
- **Save** → `PUT /runways/{icao}/{runway_id}/pattern`. **History** list + **Revert**.
- Patterns render as a toggleable map overlay (own `VectorLayer`) even outside edit mode.

**API** (`backend/app/main.py`):

- `GET /airports/{icao}/patterns` → current pattern per runway end (geometry + version meta).
- `GET /runways/{icao}/{runway_id}/pattern` → current pattern.
- `PUT /runways/{icao}/{runway_id}/pattern` — body `{ points, closed, name, change_note, visitor_id }`.
  Open editing. Validates: point count ≤ 50, each point within 10 nm of the airport, not `locked`.
  Rate-limited per `visitor_id`/IP. Inserts new version.
- `GET /runways/{icao}/{runway_id}/pattern/history` → version list.
- `POST /runways/{icao}/{runway_id}/pattern/revert` — body `{ version, visitor_id }` → new current.

**Guardrails (open editing):** per-visitor/IP rate limit; max control-point count; geometry bounds
check; full version history for revert; optional admin `locked` flag.

## Feature 2 — Deviation from pattern

**Backend** (`backend/app/deviation.py`):

1. **Pattern matching (direction-matched):** compute the aircraft segment's net direction of
   travel (sequence of bearings). For each runway-end pattern, compare travel direction to the
   pattern's inherent direction (point order / arrows). Choose the directional match; among ties,
   pick the pattern with the lowest mean distance (nearest). Fallback: active-runway pattern →
   nearest. If no pattern exists, deviation fields stay null.
2. **Densify** the matched pattern's control points to a polyline via a Python port of Catmull-Rom
   (mirrors the frontend `smoothSegment` math).
3. **Perpendicular distance** of each track sample = min distance to any polyline segment
   (haversine-based, reusing `geo.py` helpers).
4. **Metrics:**
   - **KPI A — time-off-pattern:** `time_off_pattern_s` = Σ dt where distance > **0.25 nm**
     corridor; `pct_off_pattern` = time_off / total.
   - **KPI B — mean deviation:** time-weighted average perpendicular distance (nm);
     `deviation_peak_nm` = max as secondary.
5. Written onto the `operations` row when the op is finalized.

## Feature 3 — Rotation / "cowboy" detection

**Backend** (`backend/app/flow.py`, run in the worker after ops are persisted):

- Read recent `operations` with a `runway_id` for the airport (rolling window). Determine the
  **dominant active end**. Mark **established** when ≥3 consecutive same-end ops *or* ~10 min of
  agreement; persist to `runway_flow`.
- When the dominant end flips **and holds** (passes the same established threshold), write a
  `runway_changes` row attributing it to the **first aircraft of the new sustained flow** = the
  **cowboy** (always tagged). Snapshot wind via `weather.get_wind_summary` (cached); compute
  `wind_favored_new` (does the new end give a positive headwind component?) for context only.
- **Into-headwind metric:** per op, `headwind_kt = wind_speed_kt · cos(runway_heading_deg −
  wind_from_deg)` (positive = into wind). Stored on the op; aggregated by the KPIs endpoint.

**Frontend:** a small badge on the main map — *"Active: RWY 30R · changed 12m ago by N1234 🤠"* —
fed by `GET /airports/{icao}/flow`.

## Feature 4 — KPIs page

**Route:** `/stats` (path-branch like `/admin`, `/about`), new `StatsPage.tsx`. Time-range
selector: Today / 7d / 30d / All. Fed by `GET /airports/{icao}/stats?window=…`.

**Sections (scrollable dashboard, top → bottom):**
1. **Stat tiles:** T&Gs, circles, passes, unique aircraft, runway changes.
2. **Ops over time** — Recharts bar/area.
3. **Runway changes + cowboy leaderboard** — count, who flipped the runway most, wind at each change.
4. **Wind & rotation** — time/ops into-headwind vs not; current + window-averaged wind.
5. **Deviation** — distribution of mean-deviation (nm), worst-deviation aircraft, total time-off-pattern.
6. **Repeat offenders** (existing `aircraft_report_counts`) + **origin airport / flight school**
   breakdown (best-effort from `aircraft_registry` registrant / operator, confidence-tagged).

**API:** `GET /airports/{icao}/stats?window=…` returns the aggregated payload above, computed from
`operations`, `runway_changes`, `runway_flow`, and existing offender tables.

**Frontend dependency:** add **Recharts** to `frontend/package.json`.

**Client (`frontend/src/lib/api.ts`):** add `getPatterns`, `getPattern`, `savePattern`,
`getPatternHistory`, `revertPattern`, `getAirportStats`, `getAirportFlow`.

## Worker integration

After `run_detectors_for_monitor`, finalize completed ops and upsert into `operations`
(idempotent by id). When a current pattern exists for the airport, compute deviation
(Feature 2) before writing. Then update `runway_flow` / `runway_changes` (Feature 3). Wind and
origin lookups reuse existing cached paths — no new external paid calls.

## Edge cases & guardrails

- **No pattern drawn:** deviation fields null; KPIs section shows "no pattern set."
- **Cowboy false positives:** change requires the new end to hold (≥3 ops / 10 min) before firing.
- **Wind gaps:** snapshot fields nullable; store what's available.
- **Flight school:** best-effort from FAA registrant/operator + small heuristic; confidence-tagged;
  never blocks an op.
- **Vandalism:** open editing mitigated by rate limits, geometry bounds, version history/revert,
  admin lock.
- **Multi-airport:** everything keyed by `icao`; `/stats` is per selected airport.
- **Idempotency:** `operations.id` deterministic so re-scans don't double-count.

## Testing strategy (pytest, matching existing suite)

- Catmull-Rom densification + point-to-polyline perpendicular distance (geometry unit tests).
- Deviation metrics: time-off corridor, mean/peak distance on synthetic tracks.
- Direction-matched pattern selection (correct end chosen for each travel direction).
- Flow detection: established threshold, change firing, cowboy attribution on synthetic op sequences.
- Headwind component sign/magnitude.
- Pattern versioning: save → new version, revert, lock enforcement, guardrail rejections.
- Endpoint tests for all new routes and the `/stats` aggregation.

## Deferred / YAGNI

- Multiple named patterns per runway end (single recommended pattern + history is enough now).
- Bézier tangent-handle editing (Catmull-Rom control points only).
- "Pioneer vs cowboy" dual labelling (always-cowboy chosen; `wind_favored_new` still stored).
- Cross-airport comparison views on `/stats` (per-airport only for now).
