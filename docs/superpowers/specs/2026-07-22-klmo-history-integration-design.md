# Integrating the KLMO Year-History into circlejerks (have · access · maintain)

- **Date:** 2026-07-22
- **Status:** Design for review (precursor to an implementation plan)
- **Depends on:** the completed year backfill — `klmo_history.sqlite3` (~47M samples, 32,328 aircraft, 2025-07-21 → 2026-07-20, `track_archive` schema, `source='adsblol_globe_history'`). See `2026-07-20-klmo-year-backfill-design.md`.

## Goal

Make the year of KLMO tracks a first-class part of circlejerks so that the app:
1. **has** the data (owns it, deploys with it),
2. **can access** it through the existing API/read paths, and
3. **maintains** it — the live pipeline keeps appending new days indefinitely and never prunes the history away —

*without* wrecking the lean operational database, its backups, or live-query latency.

## Current state (what we're integrating into)

- **Operational DB** `data/circlejerk.sqlite3` (prod): airports, submissions, operations, `aircraft_registry`, and `track_archive`. Crucially, `track_archive` is a **rolling 7‑day hot tier**: `archive.archive_once` copies Redis → SQLite every ~5 min, and `archive.prune_once` **deletes anything older than `track_archive_horizon_days` (7)** hourly.
- **Read paths:** `db.read_track_archive_bbox`, `bulk_read_track_archive`, and `archive.merge_hot_and_cold_samples` all read the operational `track_archive` and union it with Redis "hot" samples.
- **History store** `klmo_history.sqlite3`: identical `track_archive` schema, 47M rows, KLMO bbox only, never pruned — currently a standalone file with zero wiring into the app.

## The core tension

"Merge into the circlejerks DB" pulls one way; keeping the operational DB lean pulls the other. Loading 47M rows / ~7 GB into `circlejerk.sqlite3` would:
- bloat every backup (`scripts/backup_sqlite.sh` copies the whole file),
- slow live reads/writes (larger indexes, WAL contention), and
- conflate a permanent forensic archive with the churning 7‑day hot tier.

So the design keeps them **physically separate but logically unified**: circlejerks still *has, accesses, and maintains* the data — it's just held in a dedicated append‑only store the app owns, rather than dumped into the operational file.

## The model: two databases, reads routed by window

circlejerks runs **two** SQLite stores, split by query profile:

- **Hot DB** — `circlejerk.sqlite3` (the existing operational file). A **7‑day rolling** `track_archive`, small and fast, serving live scans and all short windows (live / 1h / 6h / today / 7d). Unchanged: still fed by `archive_once` every ~5 min, still pruned at the 7‑day horizon.
- **Long‑term DB** — `klmo_history.sqlite3`. **Permanent**, append‑only, holding the backfilled year plus everything that ages out of the hot DB. It serves **"big" queries whose window reaches past 7 days** (a month, a season, the full year). Maintained by a background job (below).

**Read routing is by window length.** A request whose window is ≤ the hot horizon (7 days) touches only the hot DB — the fast path is completely unchanged and never pays for the 47M‑row store. Only a request that reaches **older than 7 days** additionally opens the long‑term DB and unions the results. Big historical queries are naturally heavier (they scan a big table) — but they're rare, opt‑in by window, and never slow down the live map.

## Recommended architecture

### 1. Storage — a permanent "cold history" store the app owns

Promote `klmo_history.sqlite3` to a first-class secondary store:
- Path from settings (`history_database_path`, default `data/klmo_history.sqlite3`), same `track_archive` schema, **never pruned**.
- The app `ATTACH`es it (read side) and writes to it (maintain side). It deploys alongside `circlejerk.sqlite3`.
- Add a tiny `history_coverage(airport_icao, min_ts, max_ts, updated_at)` table in the history store so the read path knows which windows it can serve and skips it for out-of-range queries.

**Alternative considered — one merged file:** load the 47M rows into `circlejerk.sqlite3`'s `track_archive` and exempt `source='adsblol_globe_history'` (and future permanent rows) from `prune_once`. Simpler mental model, but it's the bloat problem above. *Recommend the separate store unless you specifically want a single file; the requirements ("has / accesses / maintains") are all met either way.*

### 2. Access — window-routed reads that union the long-term DB only when needed

Introduce `read_cold_samples_bbox(op_conn, hist_conn, bbox, start_ts, end_ts, ...)` that routes by how far back the window reaches:
- **Window within the hot horizon** (`start_ts >= now - 7d`): query the operational `track_archive` **only**. Identical to today's fast path — the long-term DB is never opened.
- **Window reaches older than 7d**: query the operational `track_archive` **and** (when `[start_ts, end_ts]` overlaps `history_coverage` for the airport) the attached long-term store, then union + dedupe by `(icao24, timestamp)`.

Extend `merge_hot_and_cold_samples` to three tiers — Redis-hot, operational-cold, history-cold — with hot winning on collision. With this, the existing map / stats / track-density endpoints can serve a year back for KLMO by simply *allowing a wider window*; no new endpoints. (Exposing year-scale windows in the **frontend UX** is a separate, later change; this plan only makes the data reachable through the API.)

### 3. Maintain — the live pipeline extends the history permanently

Today samples flow Redis → operational `track_archive` (pruned at 7 days). Make the history store the **permanent tail** of that flow, for an explicit allowlist of airports:
- Add `permanent_history_airports` (setting, default `["KLMO"]`).
- In the archive loop, **before** `prune_once` deletes operational rows older than the horizon, copy the soon-to-be-pruned rows *for allowlisted airports' bboxes* into the history store (`INSERT OR IGNORE`), then let prune proceed. Update `history_coverage.max_ts`.
- Net effect: the operational `track_archive` stays a 7‑day rolling head; the history store grows by ~one day of KLMO traffic per day, forever. **circlejerks is now the maintainer.**

Restricting to an allowlist prevents unbounded growth (we don't want *every* monitored airport's traffic archived permanently — only the ones we've committed to, starting with KLMO).

## One-time migration

1. **Ship the file to prod:** rsync `klmo_history.sqlite3` (~7 GB) to the prod droplet's `data/` dir. *Prerequisite: confirm prod disk headroom (+~7–10 GB incl. indexes/WAL).*
2. **Wire settings:** set `history_database_path`; add `permanent_history_airports=["KLMO"]`.
3. **No schema migration** (same `track_archive`). Create `history_coverage` and seed it from the store's actual min/max timestamp for KLMO.
4. **Close the seam:** the history store ends 2026-07-20; live archiving has been filling the operational DB since. Verify there's no gap between `history_coverage.max_ts` and the operational archive's oldest retained day; run a short catch-up backfill for any missing days so the timeline is continuous.

## Deployment & ops

- **Backups:** the history store is append-only and changes slowly — back it up on a **slower cadence** than the operational DB (or incrementally), rather than copying 7 GB every operational backup.
- **Indexing / latency:** the store has `(icao24, timestamp)` PK + a `timestamp` index. Bbox‑over‑a‑year queries across 47M rows may need a better access path (a covering index, or coarse lat/lon bucketing) for interactive latency — **measure with the real read patterns before adding indexes.**
- **Multi-airport / mirror:** the AirfieldEconomics mirror is multi-airport; the allowlist model generalizes to per-airport permanent history later (each airport a separate backfill + coverage row). Out of scope now.

## Decisions to confirm before implementation

1. **Separate attached store (recommended) vs. one merged `circlejerk.sqlite3`.**
2. **Airport allowlist for permanent maintenance** — start `["KLMO"]`?
3. **Prod disk headroom** for +7 GB (needs a check on the droplet).
4. **Do the maps/API actually expose year-scale windows now, or just make the data reachable** and defer the UX?

## Testing

- Read-path union: hot + operational-cold + history-cold, with dedup correctness and out-of-range skipping via `history_coverage`.
- Maintain-path: rows aging past the horizon land in the history store *before* prune deletes them; idempotent under re-runs; allowlist respected.
- Migration dry-run: attach the store, verify coverage, run a sample bbox+year query.

## Out of scope

- Frontend UX for year-scale map/stat views (separate design).
- Permanent history for airports other than KLMO (generalize later).
- Any change to how the year was *sourced* (adsb.lol) — this is purely integration.
