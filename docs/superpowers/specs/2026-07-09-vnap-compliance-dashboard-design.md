# VNAP Compliance — Dashboard Table + Radar, Owner Crowdsourcing, Sidebar — Design

**Date:** 2026-07-09
**Branch:** codex/production-observability
**Status:** Approved (design), pending phased implementation plans

## Goal

Measure how well repeat-offender aircraft follow Vance Brand Airport's (KLMO)
Voluntary Noise Abatement Procedures (VNAP), classify each aircraft's owner, let
the community correct/annotate that classification, and surface it two ways:

- **Dashboard (`/stats`)**: an aircraft-centric master table (one row per tail,
  many sortable columns) beside a **spiderweb/radar** that plots the selected
  aircraft's per-dimension compliance **against the set average**.
- **Main page (`/`)**: rework the live Worst-Offenders sidebar with VNAP-derived
  columns and a hover card (aircraft type, owner class, flight-school flag).

Source of the rules: <https://longmontcolorado.gov/airport/voluntary-noise-abatement-procedures/general-aviation-noise-abatement-procedures-pilot-information/>

## Locked decisions

1. **VNAP score is 0–100 where 100 = fully compliant** (bigger radar polygon =
   better behaved).
2. **"Over town" = the whole area** — the ≥1000 ft AGL floor applies everywhere in
   the scan ring; no separate town polygon is seeded. Min AGL over the ring is the
   altitude signal.
3. **Equal weights** across axes for the composite score (tunable in the airport's
   VNAP config).
4. **Crowdsourcing is aircraft/owner-class only** — correct the owner bucket, flag
   "this is a flight school," and add notes about the *operation/aircraft*. No
   named-individual fields (doxxing/defamation avoidance).
5. **Single airport at a time; additive on `/stats`** — the new master table + radar
   are added alongside existing sections; existing per-metric tables stay for now
   and get weeded out later.
6. The radar **average baseline is computed over the aircraft in the currently
   selected window** (`/stats`'s existing 1d/7d/30d/all selector), so it moves with
   the window and any filter.

## VNAP rules → measurable axes

Each axis yields a per-aircraft **0–100 sub-score** over the selected window.

| Axis | VNAP rule | Signal | Data status |
|---|---|---|---|
| `tightness` | "tight left downwind ½–¾ mi from runway" | `AVG(operations.deviation_mean_nm)` | **stored** (`operations.deviation_mean_nm`) |
| `altitude` | "not lower than 1000 ft AGL over the city"; "crosswind ≥700 AGL" | min/typical AGL over the ring | **new** light track/agg (or `min_altitude_over_user` proxy) |
| `timeofday` | "avoid ops before 8 AM / after 8 PM" | % ops within 08:00–20:00 local | **new** per-aircraft time bucketing (reuse `local_hour`) |
| `tg_volume` | "≤10 touch-and-gos per training flight" | T&G per session vs 10 | **new** session grouping |
| `circle_restraint` | "no continuous circles over the City" | circles per session vs limit | **new** session grouping |
| `left_traffic` | "left traffic pattern required" | % ops `turn_direction='left'` | **stored** (`operations.turn_direction`) |
| `runway29` | "Runway 29 preferred when wind permits" | % on rwy 29 when wind favors it | **stored** (`runway_id` + wind cols) |

**Composite `vnap_score`** = equal-weight mean of the sub-scores that have data for
that aircraft (axes with no data for an aircraft are skipped, not scored 0).

### Sub-score formulas (all clamped to [0,100]; defined in one `vnap.py` module)

- `tightness = 100 * clamp(1 - (avg_dev_nm / DEV_FLOOR_NM), 0, 1)`, `DEV_FLOOR_NM = 1.0`.
- `altitude = 100 * clamp((typical_agl - AGL_ZERO) / (AGL_TARGET - AGL_ZERO), 0, 1)`,
  `AGL_TARGET = 1000`, `AGL_ZERO = 0`; `typical_agl` = median AGL of the aircraft's
  in-ring, non-runway samples (v1 may use `min_altitude_over_user_ft_agl`).
- `timeofday = 100 * (ops_in_quiet_window_fraction)` where the window is 08:00–20:00
  local (compliant = inside).
- `tg_volume`: per session `s = 100 if tg<=10 else 100*10/tg`; axis = mean over sessions.
- `circle_restraint`: per session `s = 100 if circles<=CIRCLE_LIMIT else 100*CIRCLE_LIMIT/circles`,
  `CIRCLE_LIMIT = 4`; axis = mean over sessions.
- `left_traffic = 100 * (left_ops / ops_with_known_direction)`.
- `runway29 = 100 * (ops_on_29_when_favored / ops_when_29_favored)`; "favored" =
  headwind component on 29 ≥ 0 (from `operations.wind_from_deg`/`headwind_kt`).

**Session grouping:** order an aircraft's ops by time; a gap > `SESSION_GAP_MIN = 60`
minutes starts a new session. Used by `tg_volume` and `circle_restraint`.

All thresholds live in a per-airport `VnapRuleset` config so KLMO's numbers are
data, not magic constants, and other airports can differ.

## Architecture — four phases (build in dependency order)

Each phase gets its own implementation plan and ships independently.

### Phase 1 — VNAP compliance engine (backend foundation)

- New `backend/app/vnap.py`: `VnapRuleset` (per-airport config, seeded for KLMO),
  the sub-score functions, session grouping, and
  `compute_aircraft_compliance(conn, icao, start_ts, end_ts) -> {aircraft:[...], averages:{...}}`.
- Reuses: `operations` deviation/turn/runway/wind columns; the `local_hour`/
  `airport_timezone` helpers (shipped in the operations-trends feature); the
  per-aircraft AVG-deviation query pattern (`db.py:731-736`); cowboy attribution
  (`runway_changes.cowboy_icao24`); report counts (`aircraft_report_counts`).
- Per aircraft the engine also returns the raw **counts** the master table shows:
  `reports, operations, touch_and_gos, cowboy_count, deviation_mean_nm, circles`,
  plus resolved `owner_class` (Phase 2, inferred-only until then) and `aircraft_type`.
- New endpoint `GET /airports/{icao}/vnap-compliance?window=1d|7d|30d|all` → api.ts
  `getVnapCompliance` + `VnapComplianceResponse`.
- Response shape:
  ```
  { airport_icao, window,
    axes: [ "tightness","altitude","timeofday","tg_volume","circle_restraint","left_traffic","runway29" ],
    averages: { <axis>: number, composite: number },
    aircraft: [ { icao24, callsign, registration, tail, aircraft_type,
                  owner_class, owner_source,       // owner_source = "inferred" until Phase 2
                  vnap_score, reports, operations, touch_and_gos, cowboy_count,
                  deviation_mean_nm, circles,
                  scores: { <axis>: number|null } } ] }
  ```

### Phase 2 — Owner override + crowdsourcing

Mirror the existing `runway_patterns` editor flow (visitor-versioned, rate-limited,
`change_note`, lockable — `db.py:283-285`, `main.py:1520-1562`).

- New tables:
  - `aircraft_owner_overrides(id, icao24, owner_type, editor_visitor_id, editor_ip,
     change_note, version, is_current, locked, created_at)`.
  - `aircraft_community_notes(id, icao24, note, is_flight_school, editor_visitor_id,
     editor_ip, created_at, hidden DEFAULT 0)`.
- `resolve_owner_class(conn, icao24)`: current override if present, else
  `infer_owner_type` from the registry. Buckets are the existing `OwnerType` enum
  (`api.ts:499-508`).
- Endpoints (rate-limited like pattern edits):
  `PUT /aircraft/{icao24}/owner-class` (set bucket + note),
  `POST /aircraft/{icao24}/notes`, `GET /aircraft/{icao24}/notes`.
- Privacy guardrails: only the bucket, the flight-school flag, and operation/aircraft
  notes are storable; a disclaimer + report affordance ship with the UI; the notes
  UI copy explicitly asks for aircraft/operation info, not personal info.
- Resolved class + `owner_source` ("inferred" | "community") flow into the profile
  card, the master table, and the sidebar.

### Phase 3 — Dashboard master table + radar (frontend, additive on `/stats`)

- New `frontend/src/components/AircraftDashboard.tsx`:
  - **Left**: sortable table, one row per tail. Columns: VNAP score · owner-class
    badge (inline-editable dropdown → Phase 2 `PUT`) · aircraft type · #reports ·
    #operations · T&G · cowboy count · deviation · circles · altitude. Client-side
    sort by any column; driven by `getVnapCompliance` over the selected window.
  - **Right**: Recharts `RadarChart`. Axes = the sub-scores; two polygons — the
    **selected aircraft** and the **set average** baseline. Clicking a table row
    updates the selection. (Optional stretch: multi-select 2–3 to overlay.)
- Reuses `/stats`'s existing window selector and `stats-card`/`stats-table` styles.
- Existing `/stats` sections remain untouched (additive).

### Phase 4 — Live Worst-Offenders sidebar (main page `/`)

- Extend `enrich_offenders` (`services.py:966`) with a **batched** registry/cache
  join to attach `owner_class`, `owner_source`, `aircraft_type`, `is_flight_school`
  to each offender (batched icao_hex join pattern exists at `db.py:771-781`).
- Rework `OffenderTable` (`App.tsx:1145`) columns (today: Callsign/Origin/Score/Cir/
  TG/Dev/Pass/Avg-over-you) toward VNAP-derived values (e.g. add VNAP score; keep
  Callsign/Origin/Score). **On hover** show aircraft type · owner class ·
  flight-school association, via the app's established `title=` tooltip idiom (or a
  small popover if richer layout is wanted).

## Data flow

```
operations (+deviation/turn/runway/wind) ┐
track_archive (AGL over ring)            ├─ vnap.compute_aircraft_compliance(window)
runway_changes (cowboy), report_counts   ┘        │
aircraft_registry.infer_owner_type ─ resolve_owner_class ─┘ (override > inferred)
     │                                                     │
GET /airports/{icao}/vnap-compliance ──> AircraftDashboard table + Radar (/stats)
enrich_offenders (+owner/type) ─────────> OffenderTable sidebar (/) hover card
PUT owner-class / POST notes ───────────> aircraft_owner_overrides / _community_notes
```

## Migration

Additive only: two new tables (`aircraft_owner_overrides`, `aircraft_community_notes`)
created via `CREATE TABLE IF NOT EXISTS` in `db.SCHEMA`. No column changes to existing
tables in Phases 1–4 (the compliance engine reads existing columns). Sequenced after
the pending `runway_patterns` UNIQUE migration and the operations-trends columns.

## Testing (per phase)

- **P1**: unit tests for each sub-score formula (boundary + clamp), session grouping
  (gap boundary), `runway29` "when favored" logic, composite skip-missing-axes, and
  the endpoint (matching the `test_api.py` monkeypatch pattern). Deterministic time
  via a `_now` override like the operations-trends route.
- **P2**: override resolution (override > inferred), rate-limit enforcement,
  versioning/`is_current`, notes privacy scoping, endpoint tests.
- **P3**: component renders table + radar; row-click updates the radar's selected
  polygon; average polygon computed over the set; sort works. Typecheck + build.
- **P4**: `enrich_offenders` attaches owner/type without N+1 (batched); OffenderTable
  renders new columns + hover; build.

## Out of scope

- A town/city polygon (decision #2 treats the whole ring as town).
- Named-individual pilot data (decision #4).
- Cross-airport comparison (single airport at a time).
- Removing existing `/stats` sections (deferred; additive for now).
- Persisting raw circle radius (`avg_loop_radius_nm`) — `deviation_mean_nm` is the
  tightness signal; radius persistence can be a later refinement if needed.

## Open follow-ups (not blocking)

- Weight tuning once real scores are seen (config already supports it).
- Moderation queue for community notes if free-text abuse appears (v1 ships a
  `hidden` flag + report affordance, no active queue).
