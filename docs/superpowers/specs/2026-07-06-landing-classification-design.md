# Landing classification & "do not stop" stats — design spec

**Date:** 2026-07-06
**Branch:** codex/production-observability
**Status:** Approved (design), pending implementation plan

## Problem

The `/stats` report page classifies runway activity as circles, touch-and-gos,
low-approaches, and passes — but it has no notion of an aircraft that **actually
lands and stays**. Neighbours want to know what share of the traffic is planes
that just work the pattern and leave (the noise) versus planes that come to land.

Add a **landing** classification and surface a **"% that do not stop"** metric on
the report page, both **aggregate** and **by day**.

## Definitions (locked with product owner)

- **Unit of analysis:** per operation.
- **"Do not stop" %** = `(circles + touch_and_gos + low_approaches) / (circles +
  touch_and_gos + low_approaches + landings)`. Numerator is every runway/pattern
  operation **except** landings. Passes-over-home (`pass_over_user`) are **excluded**
  from this ratio — they measure overflight of the user's home, not airport ops.
- **Landing** = an aircraft reaches the runway at low altitude and does **not**
  climb back out within **5 minutes** (the complement of a touch-and-go). Because
  the 24 h `track_archive` does not store the `on_ground` flag, "stopped ≥5 min"
  is *inferred* as "did not leave the runway environment within 5 min." Surfaced
  honestly in UI copy as "landing (≈ stayed ≥5 min)."

## Architecture context (as-is)

- Worker polls ADS-B → live samples in Redis (hot, ~4 h) + `track_archive` SQLite
  (cold, ~24 h). Live samples carry `on_ground`/`velocity_kt`; **archived samples
  do not** (the `track_archive` schema omits both columns).
- `detectors.detect_events_over_period` runs over per-aircraft tracks and emits
  typed events: `circle`, `touch_and_go`, `low_approach`, `pass_over_user`.
- `db.persist_events` upserts each event into the durable `operations` table
  (idempotent via `ON CONFLICT(id) DO NOTHING`; id = sha256 of type/icao24/airport/
  time-bucket). This table is the source of truth for `/stats`.
- `db.airport_stats` reads `operations` and returns the stats payload;
  `main.get_airport_stats` spreads `**stats` into the HTTP response.

Today a real landing produces **no operation at all**: the touch-and-go detector
*requires* a climb-back-out (`climbed or vertical_up`), so a plane that stays down
is dropped.

## Design

### 1. New operation type: `landing`

No schema migration — `landing` is a new value in `operations.type`.

A landing shares the exact runway-low trigger as a touch-and-go, then resolves the
opposite way. To guarantee the two detectors can never disagree, factor the trigger
into a shared helper:

- **`_runway_low_episodes(track, airport, runways, start_ts, end_ts)`** → the list
  of runway-contact episodes (aircraft ≤ `200 ft` AGL within `1.5 nm` of a runway),
  bucketed as today (180 s buckets, lowest sample per bucket, directional runway
  from touchdown heading). Both detectors consume this.

- **Touch-and-go / low-approach** (behaviour preserved): emit as soon as a
  climb-back-out is observed (`climb ≥ 150 ft` OR sustained `vertical_rate > 250`).
  The look-ahead window widens from **120 s → 300 s** so touch-and-go and landing
  are exact complements over the same 5-minute window. (A climb-out that fires
  within the old 120 s still fires; the widening only additionally catches slow
  climb-outs between 2–5 min. Existing tests use fast climb-outs → stay green.)
  A touch-and-go is emitted the moment a climb-out is seen — it does **not** wait
  the full 5 min, so touch-and-go latency is unchanged.

- **`landing`** (new): emitted for an episode only when **all** hold:
  1. **No climb-out within 5 min** — no `climb ≥ 150 ft` and no sustained
     `vertical_rate > 250` in the 300 s after touchdown. (On live tracks a
     sustained `on_ground` signal also confirms a landing.)
  2. **Approach guard** — the aircraft descended *into* the field before the low
     point (at least one earlier sample in the same flight materially higher,
     e.g. > 500 ft AGL). Excludes ramp-parked transponders and departures that
     start low and climb away.
  3. **Settle guard** — we have actually observed ≥5 min past touchdown
     (`end_ts − touchdown_ts ≥ 300`) **or** the aircraft's track clearly ended at
     the field (no further samples within the flight-gap window). This prevents
     emitting a "landing" that a later scan would reclassify as a touch-and-go.

**Mutual exclusivity:** an episode either climbs out within 5 min (→ touch-and-go /
low-approach) or does not (→ landing) — never both. Combined with stable, per-episode
event ids and `ON CONFLICT DO NOTHING`, re-running detection over overlapping
windows is idempotent and cannot double-count.

**Live path:** `detect_events` (120 s recent window) can never satisfy the settle
guard, so it never emits a premature landing. Landings are emitted by the
period/backfill path (`detect_events_over_period`) once the 5-min window resolves
on a subsequent scan.

**Event shape** (mirrors touch-and-go): `type="landing"`, `min_altitude_ft_agl`
(≈0 at touchdown), `runway_id` + `runway_heading_deg` (directional runway),
`icao24`, `callsign`, `timestamp`, `airport_icao`, stable `id`.

Wiring: add `detect_landings_over_period` and call it from
`detect_events_over_period`. `db.operation_from_event` and `upsert_operation`
already handle any `type` string — no changes there.

### 2. Stats aggregation (`db.airport_stats`)

Add to the returned dict (no `main.py` change — response spreads `**stats`):

- `counters.landings` = `type_counts.get("landing", 0)`.
- `stop_classification` (aggregate over the window):
  ```
  {
    "total":             circles + touch_and_gos + low_approaches + landings,
    "did_not_stop":      circles + touch_and_gos + low_approaches,
    "landed":            landings,
    "did_not_stop_pct":  round(100 * did_not_stop / total, 1)  # null when total == 0
  }
  ```
- `stop_over_time`: **per-calendar-day** series, always bucketed by day
  (`(timestamp/86400)*86400`) regardless of the window's chart `bucket_seconds`,
  because the requirement is explicitly "by day":
  ```
  [ { "day": <unix day-start>, "did_not_stop": n, "landed": n,
      "total": n, "pct": <round(100*did_not_stop/total,1) | null> }, ... ]
  ```
  Only operation types in {circle, touch_and_go, low_approach, landing} are counted
  (passes excluded). UTC day-buckets, formatted client-side — matches the existing
  `ops_over_time` convention.

### 3. Frontend (`api.ts` + `StatsPage.tsx`)

`api.ts` — extend `AirportStatsResponse`:
- `counters` gains `landings: number`.
- add `stop_classification: { total: number; did_not_stop: number; landed: number; did_not_stop_pct: number | null }`.
- add `stop_over_time: { day: number; did_not_stop: number; landed: number; total: number; pct: number | null }[]`.

`StatsPage.tsx`:
- New **"Landings"** tile in the existing `stats-tiles` row.
- New **"Do they actually stop?"** card:
  - Hero: large `did_not_stop_pct%` with label "did not stop" and a sub-line
    (e.g. `225 fly-throughs (circles + touch-and-gos) vs 22 landings · this window`).
    Empty-state when `total == 0`.
  - **Stacked bar chart by day** from `stop_over_time` using the existing recharts
    dep: two stacked `Bar`s (`did_not_stop`, `landed`), x-axis = day (client-formatted),
    tooltip shows both counts and that day's `pct`.
  - Short legend + the honest "≈ stayed ≥5 min" note on what "landing" means.

Reuse existing `stats-card` / `stats-tile` / chart styling; no new CSS system.

### 4. One-time 24 h backfill

Landings were never detected historically, so `7d / 30d / all` windows would read
near-100% until detection has run for a while. Ship a small idempotent backfill
that re-runs runway-op detection over the existing 24 h `track_archive` and upserts
any `landing` (and touch-and-go/low-approach) operations it finds. This makes the
`1d` window meaningful immediately; longer windows fill in over time as detection
runs. Backfill is a standalone script (mirrors `backend/scripts/backfill_offender_counts.py`),
run once at deploy; idempotent via the same event ids.

### 5. Known limitations (documented, accepted)

- "Stopped ≥5 min" is inferred, not measured, for archived data (no `on_ground` in
  `track_archive`). A full-stop-taxi-back-and-depart inside 5 min reads as a landing
  (acceptable — it did stop). Copy says "≈ stayed ≥5 min."
- Day buckets are UTC epoch days (existing convention), not airport-local days.
- Historical windows undercount landings until backfill + ongoing detection catch up.

### 6. Testing

Detector (`test_operations.py` style):
- Approach → touchdown → climb-out ⇒ `touch_and_go` (unchanged, still green).
- Approach → touchdown → no climb-out, track ends at field ⇒ `landing`.
- Departure (starts low, climbs away) ⇒ not a landing (approach guard).
- Ramp-parked low samples, never approached ⇒ not a landing (approach guard).
- Touchdown with < 5 min following data ⇒ no landing yet (settle guard).

Stats (`test_stats.py` style):
- `stop_classification` math with circles included in the numerator.
- `counters.landings` populated.
- `stop_over_time` day-bucketing and per-day `pct`.
- `total == 0` ⇒ `did_not_stop_pct` is `null` (no divide-by-zero).

Frontend: extend the `AirportStatsResponse` shape in existing `lib` tests; light
component check that the hero % and stacked chart render for a populated payload
and show the empty-state at `total == 0`.

## Out of scope

- Per-aircraft / per-visit "do not stop" views (product owner chose per-operation).
- Airport-local-time day bucketing.
- Persisting `on_ground`/speed into `track_archive` (would improve landing fidelity;
  separate change).
