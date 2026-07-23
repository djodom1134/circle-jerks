# FAA Operation construct — design

**Date:** 2026-07-23
**Branch:** `feat/public-api-keys`
**Status:** approved, ready for plan

## Problem

The `/v1` API speaks in *detected events* (`operations.type ∈ {landing, takeoff,
touch_and_go, low_approach, circle, pass_over_user}`), one row per event. Nowhere
does it express the canonical FAA sense of an **operation** — a single takeoff or
a single landing — so consumers can't get an apples-to-apples movement count. The
existing aggregates count *records*, not operations, and do so inconsistently:
`airport_operations_trends` counts a touch-and-go as **1** (it is really 2), and
`airport_stats` tallies each type separately with no FAA total. An earlier KLMO
audit found the `low_approach` bucket is ~90% real touchdowns (i.e. touch-and-gos
worth 2 ops each), so a naive record count materially undercounts.

We want the API to carry a first-class **FAA operation** count, populated for the
full year of history and kept current going forward.

## The definition we encode

An FAA operation is one takeoff **or** one landing. Mapping our event types:

| `operations.type` | FAA operations | rationale |
|---|---|---|
| `takeoff` | 1 | one departure |
| `landing` | 1 | one arrival |
| `touch_and_go` | 2 | one arrival + one departure |
| `low_approach` | 2 if `min_altitude_ft_agl ≤ 25`, else 0 | ≤25 ft AGL ⇒ a real touchdown (a touch-and-go); a higher low pass / go-around completes neither a takeoff nor a landing |
| `circle` | 0 | a pattern lap, not a runway movement |
| `pass_over_user` | 0 | not an airport movement |

`min_altitude_ft_agl` is populated on every runway event by
`detectors._build_runway_event` (`int(ep["lowest_agl"])`) and was stored by the
cold-store backfill, so the low-approach split works across all of hot and cold.
`low_approach` is the *only* type whose weight depends on the altitude column, and
that column is never NULL for a `low_approach` row; the rule still treats a
missing value as 0 (`min_altitude_ft_agl IS NOT NULL AND … ≤ 25`) for safety.

`LOW_APPROACH_TOUCHDOWN_MAX_AGL_FT = 25` is a documented module constant (from the
audit). It can become a setting later if it ever needs tuning — not now (YAGNI).

## Architecture: derived at read time

The FAA count is a **pure function of data already stored on every row**
(`type` + `min_altitude_ft_agl`). It is computed at query time, never stored. This
is the decision that makes backfill and front-fill unnecessary (see below).

### Single source of truth — `backend/app/faa_operations.py` (new)

A small, isolated module that owns the weighting and nothing else:

- `LOW_APPROACH_TOUCHDOWN_MAX_AGL_FT = 25`
- `faa_operation_count(op_type: str, min_altitude_ft_agl: int | None) -> int` —
  the Python weighting used per record.
- Arrival / departure decomposition helpers (a touchdown-class event is
  1 arrival + 1 departure; `landing` is arrival-only; `takeoff` is
  departure-only) so `arrivals + departures == faa_operations` by construction.
- SQL fragments (`FAA_OPS_SUM_SQL`, `FAA_ARRIVALS_SUM_SQL`,
  `FAA_DEPARTURES_SUM_SQL`, `LOW_APPROACH_TOUCHDOWN_SUM_SQL`) built from the *same*
  constants, so the Python path and the SQL path cannot drift.

Both a `CASE`-expression string and the Python function derive from one weighting
table, and a unit test asserts they agree on a representative sample.

**What does it do / how do you use it / what does it depend on:** it converts an
event type (+ altitude) into an FAA operation count; callers are `operation_out`
(per record) and `db.faa_operations_summary` (aggregate); it depends only on the
two constants and the type→weight table — no DB, no settings, no I/O.

## Surface 1 — per-record field (additive)

- `v1_schemas.OperationOut` gains `faa_operation_count: int`.
- `v1_schemas.operation_out(row)` sets it via
  `faa_operations.faa_operation_count(row["type"], row["min_altitude_ft_agl"])`.
- Every `/v1/operations` row now self-describes its FAA weight (0/1/2). No
  storage, no migration, no route change.

## Surface 2 — summary aggregate (new)

`GET /v1/operations/summary` — scope `ops:read`, same auth/airport gate as
`/v1/operations` (`_known_airport` first, so an out-of-scope airport is a 403 that
never leaks existence).

Query params: `airport` (required), `since`, `until` (ISO-8601 like the paged
endpoint). Range resolved with `resolve_range(..., max_span=None)` — no span cap,
because the response is a single aggregate row, not a page, and the whole point is
long windows (a full year). Default lookback stays 7 days when `since` is omitted.

Hot+cold merged with the **exact** time-disjoint seam `read_operations_page` uses:
cold serves `timestamp < hot_cutoff_ts`, hot serves `timestamp ≥ hot_cutoff_ts`,
so summing weighted counts across the two slices never double-counts.
`hot_cutoff_ts = now - track_archive_horizon_days * 86400`; cold participation is
gated by `history_store.available(settings)` and
`history_store.airport_allowed(icao, settings)`, exactly as the paged endpoint.

New `db.faa_operations_summary(conn, *, icao, start_ts, end_ts, history_path,
hot_cutoff_ts) -> dict` runs, per slice (hot and, when in range, cold via
`ATTACH … AS hist`):

- one weighted `SUM(CASE …)` for `faa_operations`, `arrivals`, `departures`,
  `low_approach_touchdowns`, and
- a `GROUP BY type` `COUNT(*)` for `by_event_type`,

then adds the slice results together. Uses the `idx_operations_icao_ts` index; a
year is ~121k rows at KLMO — a cheap aggregate.

New `v1_schemas.OperationsSummaryOut`:

```jsonc
{
  "airport_icao": "KLMO",
  "since_ts": 1721692800, "until_ts": 1753228800,
  "faa_operations": 129543,
  "arrivals": 64800, "departures": 64743,   // sum to faa_operations
  "by_event_type": { "landing": …, "takeoff": …, "touch_and_go": …,
                     "low_approach": …, "circle": …, "pass_over_user": … },
  "low_approach_touchdowns": 27810,
  "low_approach_touchdown_max_agl_ft": 25
}
```

`by_event_type` echoes raw event counts for every observed type (including the
zero-weight `circle` / `pass_over_user`) so the derivation is transparent and
auditable against the headline number.

## Backfill / front-fill: none needed, by construction

The request asked for "the backfill and any logic to front-fill as needed." With a
derived-at-read count, the correct amount of both is **zero**:

- **Backfill** — the ~121k cold-store operations already carry `type` and
  `min_altitude_ft_agl`, so the year of history counts the moment this code ships.
  No migration, no job over prod data.
- **Front-fill** — every new hot operation carries the same two columns, so it
  counts the instant it is detected; the archive sync (`archive_to_history_once`)
  already copies operations (with `min_altitude_ft_agl`) hot→cold, so the cold
  slice stays complete without new work.

The only standing dependency is the one `/v1/operations` already relies on: the
archive sync keeps the cold store current up to `hot_cutoff_ts`. This is
documented, not re-implemented.

## Out of scope (explicitly)

- `airport_operations_trends` and `airport_stats` keep their current numbers —
  additive-only was the chosen constraint. A later change could add an FAA total
  to them, but not here.
- No detector change; no `low_approach → touch_and_go` reclassification and no
  `circle` de-dup. The AGL split gives the FAA count most of that accuracy at read
  time; a deeper reclassification remains a separate, deferred effort.
- **No prod code or prod data is edited.** This ships as ordinary branch code and
  is verified locally. Deploy is a later, separate step.

## Testing

- `faa_operation_count`: every type; the AGL boundary at 25 (→2), 26 (→0), and
  NULL (→0); unknown type (→0).
- Python-vs-SQL agreement: a table of (type, agl) rows scored both ways must match.
- `db.faa_operations_summary`: a fixture spanning the hot/cold seam asserts
  (a) the cold slice is included, (b) `arrivals + departures == faa_operations`,
  (c) a `low_approach` at 20 ft AGL adds 2 while one at 120 ft adds 0,
  (d) `by_event_type` totals reconcile with the weighted headline.
- Endpoint: `GET /v1/operations/summary` returns 200 with the schema for an
  in-scope airport, 403 for an out-of-scope one, 401 unauthenticated.
- Per-record: an `/v1/operations` row exposes the correct `faa_operation_count`.

## Files

- `backend/app/faa_operations.py` — new module (weighting + SQL fragments).
- `backend/app/v1_schemas.py` — `faa_operation_count` field on `OperationOut`;
  new `OperationsSummaryOut`.
- `backend/app/public_api.py` — new `GET /operations/summary` route.
- `backend/app/db.py` — new `faa_operations_summary()` read function.
- `docs/api/openapi.yaml` — document the new field and endpoint.
- `backend/tests/` — the tests above.
