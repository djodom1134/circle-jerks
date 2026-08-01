# Capturing the `history_store` cold-store feature into version control

**Date:** 2026-07-23
**Status:** Approved design, ready for planning
**Branch base:** `feat/public-api-keys` (current tip, carries the `/v1` reshape).

## Problem

Production serves a full year of KLMO flight history — **121,325 operations and
47,097,370 track-archive rows** (a 7.4 GB SQLite file, `2025-07-21` to `2026-07-23`,
actively maintained) — through `/v1/operations` and `/v1/tracks`, via a `history_store`
"cold store" feature. **That feature exists in no git branch.** It was written directly on
the droplet's rsync-deployed tree (`/srv/circlejerk/app`, which is not a git repository)
and never committed. Its presence was discovered only when a routine `/v1` deploy's
`rsync --delete` dry-run listed `history_store.py` for deletion.

Two problems follow:

1. **The `/v1` reshape branch cannot deploy.** Its `rsync --delete` would remove
   `history_store.py` and overwrite the two handlers that read the cold store, silently
   making a year of history unreachable via the API.
2. **The feature is one accident from total loss.** No git history, no tests, no review.
   The next rebuild-from-a-clean-checkout, or the next `--delete` rsync, erases it. The
   backfill *data* is on the droplet's data volume, but the *code that serves it* lives
   only in a mutable working tree that has already been backed up into `.bak-*` files by
   hand.

This design captures the feature into git, with tests, and reconciles it with the `/v1`
reshape so both ship together.

## What the feature is (reverse-engineered from production)

A second, never-pruned SQLite file on the same schema as `track_archive`, holding the
backfilled year plus everything that ages out of the operational (hot) 7-day archive. Read
paths fall through to it for windows older than the hot horizon.

It spans six files, all diverging from the same SSO-era base that both production and the
reshape branch descend from — in **non-overlapping** ways except two handlers:

| File | The `history_store` delta |
|---|---|
| `backend/app/history_store.py` | **New**, 33 lines. `available(settings)`, `airport_allowed(icao, settings)`, `horizon_seconds(settings)`. Every helper is `getattr`-defensive: if unconfigured, `available` returns `False` and callers behave exactly as before. |
| `backend/app/settings.py` | **+2 fields**: `history_database_path: str \| None = None` and `permanent_history_airports: list[str] = ["KLMO"]`. |
| `backend/app/db.py` | `read_operations_page` and `read_track_archive_page` gain `history_path: str \| None = None` and `hot_cutoff_ts: int \| None = None`, plus inner `_page`/`_filters` helpers. When `history_path` is set and the window reaches older than `hot_cutoff_ts`, the page merges hot (`timestamp >= cutoff`) and cold (`< cutoff`) — time-disjoint, merged by `(timestamp, id)`, accessed by column name so column order need not match. With no `history_path`, byte-identical to the operational-only read. |
| `backend/app/public_api.py` | Two call sites — `list_operations` and `list_tracks` — compute `hist_path = settings.history_database_path if history_store.available(settings) and history_store.airport_allowed(icao, settings) else None` and pass `history_path=hist_path, hot_cutoff_ts=now - track_archive_horizon_days*86400` into the db read. **These are the two handlers the `/v1` reshape rewrote.** |
| `backend/app/archive.py` | **+1 function** `archive_to_history_once(settings)` **and its call site** inside the existing `archive_loop` — on the prune interval the loop syncs the aging tail of the hot archive into the cold store before prune. No-op if the store is unavailable or no allowlist. |
| `docker-compose.prod.yml` | **+1 env line** on the api service: `CIRCLEJERK_HISTORY_DATABASE_PATH: /app/data/klmo_history.sqlite3`. The file lives in the already-mounted `/srv/circlejerk/data:/app/data` volume — **no new volume mount**. |

`worker.py` is **unchanged** and already schedules `archive_loop` (branch `worker.py:247`).
The loop already exists on the branch; the capture adds the `archive_to_history_once`
function and the single call to it inside the loop. So the sync **is** active in
production, gated by `history_store.available()` — inert in local/test where no
`history_database_path` is configured, which is what keeps the existing suite green.

## Goals

1. Every line of the `history_store` feature is committed to `feat/public-api-keys`, with
   tests, reviewed.
2. `/v1/operations` and `/v1/tracks` retain the cold-store fallthrough on top of their
   reshaped response models — a request reaching older than the hot horizon still returns
   the historical rows, now mapped through `operation_out`/`track_sample_out`.
3. After deploy, the combined branch is the source of truth: production stops carrying
   uncommitted source, and an `rsync --delete` from this branch is a no-op on the feature.
4. No behavior change to any `/v1` response *shape* — the reshape's contract is unchanged;
   history_store only affects which rows populate it.

## Non-goals

- Redesigning the cold store or its schema. Capture what runs.
- Extending the cold-store fallthrough to the aggregate endpoints (D6). The year stays
  reachable row-by-row via `/v1/operations` and `/v1/tracks`, not as aggregate stats.
- Deriving the missing quality layers (deviation/VNAP pattern-fit, wind, origin trace-back,
  runway rollups). Those are a data-pipeline effort; this capture serves whatever is present.
- Migrating or touching the 7.4 GB cold DB itself. It is data, correctly not in git; the
  code points at it via `history_database_path`.
- Any change to the reshape's field names, models, or the drift gate.

## Decisions

### D1. Capture verbatim; the reconciliation is a graft, not a rewrite

The production implementation is proven against 47M rows. Bring each delta across as it
runs, adapting only where it meets the reshape. The two handler collisions are the only
real merge, and they are **orthogonal**: the reshape changed how rows become the response
body (`operation_out`/`OperationPage`); history_store changes which rows the db read
returns. So the merge is: the reshaped handler computes `hist_path`/`hot_cutoff_ts` and
passes them into the read exactly as production does, then maps the returned rows through
its model builder exactly as the reshape does. Neither touches the other's concern.

### D2. `db.py` deltas graft cleanly onto the reshape branch

The reshape did not touch `db.read_operations_page`/`read_track_archive_page` — it wrapped
their output at the `public_api` layer. Production's versions of those functions equal the
branch's versions plus the cold-merge. So the cold-merge grafts onto the branch's copies
with no conflict. **The implementer must confirm this by diffing** the branch's two read
functions against production's and verifying the only delta is the history logic — if
production's db.py carries any other divergence, that is a finding to surface, not to
silently absorb.

### D3. Tests are the point of capture

The feature shipped with none. This design adds them at the layer that matters — the db
read's hot/cold merge — because that is where correctness lives and where a regression
would silently drop or duplicate historical rows:

- **Cold fallthrough**: a window older than `hot_cutoff_ts`, with a `history_path`
  pointing at a seeded cold DB, returns cold rows the hot archive does not contain.
- **Time-disjointness**: a window straddling the cutoff returns hot rows `>= cutoff` and
  cold rows `< cutoff` with no duplication at the boundary.
- **Keyset order preserved across the merge**: the merged page is ordered by
  `(timestamp, id)` and paginates correctly across the hot/cold seam.
- **Defensive no-op**: `history_path=None` (or an absent/empty file) yields a result
  byte-identical to the operational-only read — the property `available()` guarantees.
- **Grant still enforced**: an airport-restricted key still cannot reach another airport's
  cold data; `airport_allowed` is an *additional* gate, not a replacement for the existing
  `_known_airport` / airport-restriction checks. An `icao24`-only query from a restricted
  key is still refused as the reshape's tests already assert.
- **Reshaped mapping over cold rows**: a cold-store row surfaces through
  `operation_out`/`track_sample_out` with the reshaped field names (`fraction_off_pattern`,
  `timestamp_ts`, `altitude_datum`, …) — proving the two features compose.

### D4. `permanent_history_airports` default and the settings source of truth

The field defaults to `["KLMO"]` in code, and production relies on that default (no compose
override for it). Capture the default as-is. `history_database_path` defaults to `None`
(feature off) and is switched on per-environment by the compose env line — so the feature
is inert in local/test unless a path is set, which is exactly what keeps the existing 814
tests green.

### D5. Fix `altitude_datum` for the backfill source string

Probing the live cold store showed its 47M track rows carry `source='adsblol_globe_history'`,
which is **not** in the reshape's `_ALTITUDE_DATUM_BY_SOURCE` map (that maps the live
`adsb_lol`, not the backfill string). So `/v1/tracks` over the historical year currently
reports `altitude_datum: "unknown"` for every row, though the value is barometric. The
capture adds `'adsblol_globe_history' → 'barometric'` to the map, with a test, and audits
any other backfill source strings the same way. This is a data-correctness fix that belongs
with the feature that surfaces the data.

### D6. Aggregates stay hot-only — the cold fallthrough is not extended

Only `/v1/operations` and `/v1/tracks` route to the cold store, matching production. The
aggregate endpoints (`stats`, `operations-trends`, `worst-offenders`, `vnap-compliance`,
`flow`, `patterns`) read the hot 7-day window only, so the historical year is reachable
row-by-row but not as aggregates. This is a deliberate scope boundary, not an omission:
extending the fallthrough is new scope, and it is largely moot until the derived layers
that feed those aggregates are computed (see the reconciliation below). Recorded so a future
reader knows the year's invisibility to aggregates is by decision.

## Data reconciliation — what the API can query vs. what the cold store holds

Grounded in a live probe of the 7.4 GB cold store (121,325 operations; 47M track rows):

**Tier A — richly queryable over the year (the two cold-fallthrough endpoints):**
- `/v1/tracks`: all 47M raw positions (lat/lon, heading, callsign, icao24, barometric
  altitude). `geo_altitude_ft`/`vertical_rate_fpm` are sparse in the backfill; the parser
  fix applies to live ingest, not this historical data.
- `/v1/operations`: `type` 100%, `runway_id`/`min_altitude_ft_agl` 74%, `turn_direction`
  50%, `registration` 44%, `operator` 42%.

**The nullable-field work is load-bearing here.** The derived-quality fields are near-empty
in the cold store — `fraction_off_pattern`/`deviation_*`/`time_*` ~0%, `wind_*` ~1%,
`origin_airport_icao`/`flight_school` 0%, `emitter_category` ~0%. Because Tasks 2–7 made
every one of these nullable (against the plan's original non-nullable examples), a year-old
row serializes cleanly with nulls instead of raising a `ValidationError` and 500-ing the
page. Had the model kept the plan's types, the historical year would be un-queryable.

**Tier B — the year is invisible (aggregates are hot-only, per D6):** `stats`,
`operations-trends`, `worst-offenders`, `vnap-compliance`, `flow`, `patterns` see only the
last 7 days.

**Tier C — derived layers not yet computed:** `worst-offenders`/`vnap-compliance` rank on
`vnap_score` (deviation/pattern-fit derived, ~0% populated); `flow`/`patterns` need the
runway rollups (~0%). So even where Tier B could reach the year, the analytical values would
be empty until those layers are derived — a data-pipeline task outside this capture.

## Architecture

```
request → /v1/operations|tracks handler (reshaped)
   ├─ compute hist_path = history_database_path if history_store.available(s)
   │                        and history_store.airport_allowed(icao, s) else None
   ├─ db.read_*_page(..., history_path=hist_path,
   │                       hot_cutoff_ts=now - track_archive_horizon_days*86400)
   │     └─ hot (ts >= cutoff)  ⊎  cold (ts < cutoff, from history_path)  → merge by (ts,id)
   └─ rows → operation_out / track_sample_out → OperationPage / TrackPage   (reshape, unchanged)

archive.archive_to_history_once(settings)   # captured, not scheduled — feeds the cold store
```

The trust boundary is unchanged: `available()`/`airport_allowed()` are an *additional*
gate layered before the existing scope and airport-grant checks, never a bypass of them.

## Rollout

The combined branch deploys by the standard DEPLOY.md path (rsync, then rebuild api and web
one service at a time on the 2GB box). Because the feature is now in the branch, the
`rsync --delete` that previously threatened `history_store.py` becomes a no-op on it. The
production `.env`/compose already carry `CIRCLEJERK_HISTORY_DATABASE_PATH`, so the deployed
code finds the existing 7.4 GB cold DB unchanged.

**Order constraint:** this branch must deploy the reconciled code, not the reshape alone.
A deploy of the reshape without this capture is the regression this whole design exists to
prevent.

## Risks

- **The graft absorbs an unnoticed production divergence.** Mitigated by D2's explicit
  diff-and-confirm: the implementer proves the only db.py delta is the history logic.
- **The cold-merge SQL is subtly wrong at the hot/cold boundary** (duplicate or dropped
  rows at `timestamp == cutoff`). Mitigated by D3's time-disjointness and boundary tests —
  the tests the feature never had.
- **The captured code drifts from what production runs** if production is hand-edited again
  before this deploys. Mitigated by deploying promptly after review and by making this
  branch the source of truth. The root cause — hand-editing prod — is outside this
  design's scope but is the reason it exists; worth its own follow-up (deploy only from
  git, never edit the droplet tree).

## Open items carried forward

- `archive_to_history_once` runs from `archive_loop` on the prune interval, matching
  production. Its behavior under the operational load of a fresh deploy (it holds a write
  lock while syncing the aging tail) is worth watching on the first post-deploy prune cycle.
- The broader "production carries uncommitted source" problem is not solved by this one
  capture; a deploy discipline that forbids editing `/srv/circlejerk/app` by hand is the
  real fix.
