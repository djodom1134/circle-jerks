# Operations Trends Dashboard — Design

**Date:** 2026-07-09
**Branch:** codex/production-observability
**Status:** Approved (design), pending implementation plan

## Goal

Add a Superior-Air-Tracker-style "Operations Trends" section to the airport stats
page (`/stats`), reproducing the six visuals from
`https://superiorairtracker.com/airports/KLMO` using our own collected data.

Where a required metric has no data series today, we start measuring it.

## Target visuals (from KLMO reference page)

| # | Visual | Chart type | Window |
|---|--------|-----------|--------|
| 1 | Recent Operations Data — Date, # Operations, % T&G, % Light (A1) | Table | Last 10 days |
| 2 | Landings / Takeoffs / T&G / Total Operations | Multi-line | 12 months, monthly |
| 3 | % of Total Operations that were T&G | Single line | 12 months, monthly |
| 4 | % Operations by most-common aircraft type | Stacked % area | 12 months, monthly |
| 5 | % Operations by ADS-B emitter category | 100% stacked area | 12 months, monthly |
| 6 | Operations by Time of Day (0–23h, airport-local) | Bar (24 bars) | 12-month aggregate |

## Key definitions

- **"Operation"** in this section = **landing + takeoff + T&G**, matching Superior's
  FAA-style operations counting. Our noise-specific events (**circle**,
  **pass_over_user**, **low_approach**) are **deliberately excluded** from these six
  visuals; they remain on the existing stats sections. A one-line note on the page
  explains the counting so "operations" is not confused with our offender metrics.
- **Aircraft type** = FAA-registry `model` (already joined), top ~8 by volume with an
  **"Other"** bucket for the long tail (matches Superior's C172/C152/P28A/Other shape).
- **Emitter category** = ADS-B emitter category code (`A1` Light, `A2` Small,
  `A6` High-Performance, `B1` Glider, `B4` Paraglider, plus `Other`).

## Decisions (locked with user)

1. **Placement:** new section on `/stats` (not the main map, not a separate page).
2. **Takeoffs:** add real takeoff detection so "operations" matches Superior exactly.
3. **Emitter category:** start capturing from the feed now; accept forward-only history.
4. **History depth:** prod holds ~12+ months of `operations`, so the monthly charts fill
   immediately — except emitter-derived series (#5 and the table's "% Light" column),
   which are forward-only and grow from first-capture date.

## Grounding facts (verified against current code)

- Emitter category is present in **both** feed payloads but dropped by both parsers:
  - Readsb-shaped feeds (adsb.lol/adsb.fi/airplanes.live/adsbx/self-hosted):
    `row["category"]` (values `A1`/`A2`/`A6`/`B1`…) — parsed in
    `backend/app/live_sources.py:parse_readsb_aircraft` (~L94–111), field not read.
  - OpenSky `/states/all`: emitter category is state-vector **index 17** (numeric 0–20)
    — parsed in `backend/app/opensky.py:parse_state_vector` (~L127–150), which guards
    `len(row) < 17` and never reads index 17.
  - Not stored in `track_archive` (`db.py:202`) or `aircraft_cache` (`db.py:63`).
  - Note: existing `category_label` / `derived_category` (`registry/codes.py`) is a
    display label derived from the **FAA registry** type+engine, unrelated to the ADS-B
    emitter category.
- Existing ops-over-time histogram (`db.py airport_stats`, ~L647–655) buckets on **raw
  UTC epoch**; the `airports` table (`db.py:30–40`) has **no timezone column**; the stats
  page renders buckets in the **viewer's** browser tz (`StatsPage.tsx:106`). Matching
  Superior's airport-local time-of-day requires real tz handling.
- `StatsPage.tsx` uses **Recharts** (`BarChart`, `Bar`, `XAxis`, `YAxis`, `Tooltip`,
  `Legend`, `ResponsiveContainer`); data via `getAirportStats()` →
  `GET /airports/{icao}/stats` (`lib/api.ts:862`).

## Components

Five independently testable units.

### 1. Emitter-category capture (ingest)

- `live_sources.py parse_readsb_aircraft`: add `emitter_category = row.get("category")`
  to the per-sample dict.
- `opensky.py parse_state_vector`: relax the `len(row) < 17` guard; read index 17
  (numeric OpenSky category 0–20) and map to standard ADS-B codes
  (`A0`–`A7`, `B0`–`B7`, `C0`–`C7`) via a lookup table. Unknown/absent → `None`.
- Store on `aircraft_cache` (new nullable `emitter_category` column, keyed by icao24,
  last-seen wins).
- **Snapshot** the aircraft's `emitter_category` onto the `operations` row at detection
  time (new nullable column) so monthly aggregation is a pure `GROUP BY` immune to cache
  churn and re-classification.
- **Interface:** parsers gain one field; detectors read the current cache value when
  writing an operation. No behavior change when the field is absent (`None`).

### 2. Takeoff detector (`detectors.py`)

- New `event_type = "takeoff"`.
- **Definition:** a track that *begins* at the field — its earliest in-ring samples are
  runway-low AGL (reuse the existing runway-low-episode machinery and thresholds) — and
  then climbs out of the ring (sustained positive vertical rate / rising altitude past
  the ceiling), **with no preceding approach segment**. The absence of a prior
  descent/approach is what distinguishes a takeoff from a T&G (approach→touch→climb) and
  from a landing (approach→touch→stop).
- Attributed to the departure runway + heading, like other operations.
- **Non-goals / guards:** must not double-count a T&G's climb-out as a takeoff; must not
  fire on aircraft merely transiting the ring at low altitude without a runway-low
  origin.
- **Interface:** emits `operations` rows exactly like existing detectors (same schema),
  so downstream aggregation, scoring, and stats need no special-casing beyond counting
  the new type.

### 3. Airport-local timezone

- Add `timezone` (IANA name, e.g. `America/Denver`) column to `airports`.
- Seed the airports we serve (KLMO, KBJC → `America/Denver`); other rows default to
  `None` and fall back to UTC bucketing until seeded.
- Time-of-day bucketing converts each op's UTC epoch to airport-local hour via
  `zoneinfo.ZoneInfo`, DST-correct.
- **Bonus:** the existing ops-over-time chart can adopt the same local-day/local-hour
  boundaries (out of scope to redesign, but the tz column unblocks it).
- **Interface:** a helper `airport_local_hour(ts, tz)` / local-day boundary helper used
  by the rollup; falls back to UTC when `tz is None`.

### 4. Monthly rollup + endpoint

- `db.py airport_operations_trends(icao, months=12)` returns:
  - `recent_days`: last 10 calendar days (airport-local day boundaries) —
    `[{date, operations, pct_tg, pct_light}]`.
  - `monthly`: up to 12 entries —
    `[{month:"2026-07", landings, takeoffs, tg, total, pct_tg,
       by_type:{model:count}, by_emitter:{code:count}}]`.
  - `time_of_day`: 24 entries — `[{hour, operations}]` over trailing 12 months.
  - `meta`: `{timezone, data_since, emitter_since}`.
  - **`total` / operations denominator** = `landings + takeoffs + tg` only.
- New route `GET /airports/{icao}/operations-trends` in `main.py`.
- `lib/api.ts`: `getOperationsTrends(icao)` fetcher + `OperationsTrendsResponse` types.

### 5. Frontend section (`OperationsTrends.tsx`)

- New component rendered as a section on `StatsPage.tsx`.
- Recharts: multi-line `LineChart` (#2), single-line `LineChart` (#3), stacked-percent
  `AreaChart` (#4 aircraft type, #5 emitter), 24-bar `BarChart` (#6); plain HTML table
  (#1).
- Follow the `dataviz` skill for palette, legend, axis, and dark/light consistency with
  the rest of the page.
- **Empty states:** emitter charts (#5) and the table's "% Light" column render a
  "collecting since \<date\>" note keyed off `meta.emitter_since` until data accrues.

## Data flow

```
feed (readsb/opensky) --category--> parse_* --emitter_category--> aircraft_cache
                                                                       |
detectors (circle/tg/landing/PASS/TAKEOFF*) --snapshot emitter--> operations rows
                                                                       |
airport_operations_trends(icao,12) --monthly/day/hour rollup (airport-local)-->
   GET /airports/{icao}/operations-trends --> api.ts --> OperationsTrends.tsx (Recharts)
```
`*` = new detector.

## Migration

Three additive, backward-compatible schema changes:
- `operations.emitter_category` (nullable TEXT)
- `aircraft_cache.emitter_category` (nullable TEXT)
- `airports.timezone` (nullable TEXT, IANA)

Sequenced **after** the pending `runway_patterns` UNIQUE migration already queued for
prod. No backfill of emitter data is possible (forward-only). Takeoffs are detected
going forward; optionally re-run detection over retained `track_archive` history to
backfill takeoff operations (decide during planning).

## Testing

- **Takeoff detector:** synthetic tracks — pure departure (→ takeoff), T&G
  (→ touch_and_go, no takeoff), landing (→ landing, no takeoff), arrival-only, and
  low-altitude transit (→ nothing). Assert no double-count.
- **Rollup aggregation:** fixture `operations` spanning months — assert monthly bucket
  boundaries (airport-local), `pct_tg` / `pct_light` math, top-N-plus-Other typing,
  and the landing+takeoff+tg denominator.
- **Emitter capture:** parser tests for readsb `category` passthrough and OpenSky
  index-17 numeric→code mapping (incl. short-row / missing-field → `None`).
- **Timezone bucketing:** an op at a chosen UTC instant maps to the correct
  `America/Denver` local hour across a DST boundary.

## Out of scope

- Redesigning existing `/stats` sections (do-they-stop, deviation, runway/wind, etc.).
- Folding circles/passes/low-approaches into the operations denominator.
- KBJC-specific tower/FAA-published operations counts.
- Backfilling emitter category (impossible — forward-only).
