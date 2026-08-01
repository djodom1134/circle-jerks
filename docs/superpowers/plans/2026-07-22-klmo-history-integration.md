# KLMO History Integration — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the backfilled year of KLMO tracks (`klmo_history.sqlite3`, ~47M samples) a first-class part of circlejerks — owned, queryable through the existing API for windows longer than 7 days, and continuously extended by the live pipeline — without bloating the operational DB.

**Architecture:** Two SQLite stores. The **hot DB** (`circlejerk.sqlite3`) keeps its 7‑day rolling `track_archive` and serves all short/live queries unchanged. A **long‑term DB** (`klmo_history.sqlite3`, same `track_archive` schema, never pruned) holds the backfill + everything that ages out of the hot DB. Reads are **routed by window**: a request within 7 days touches only the hot DB (fast path untouched); a request reaching older than 7 days additionally `ATTACH`es the long‑term DB and unions the results (deduped by `(icao24, timestamp)`, hot wins). A background maintenance step copies each day's samples into the long‑term DB **before** the 7‑day prune deletes them, for an explicit airport allowlist.

**Tech Stack:** Python 3.12, FastAPI, sqlite3 (ATTACH), pytest. This builds on branch `feat/klmo-year-backfill` (which has the backfill code + full backend). Run backend tests from `backend/` with `.venv/bin/python -m pytest` (bare `python` is not on PATH).

## Global Constraints

- **Two stores, split by window.** Hot horizon = `settings.track_archive_horizon_days` (7). Windows with `start_ts >= now - horizon` → hot DB only. Windows reaching older → union hot + long-term.
- **Long-term store:** path `settings.history_database_path` (default `data/klmo_history.sqlite3`), identical `track_archive` schema, **never pruned**.
- **Airport allowlist:** `settings.permanent_history_airports` (default `["KLMO"]`) governs BOTH which airports the maintenance job archives permanently AND which airports may request windows longer than 7 days.
- **Graceful absence:** if the history file doesn't exist, every read/maintain path behaves exactly as today (operational-only). No hard dependency on the file.
- **Dedup:** union results deduped by `(icao24, timestamp)`; the operational (hot) row wins on collision.
- **Idempotent maintenance:** copy-to-history is `INSERT OR IGNORE`; safe to overrun/repeat.

---

## File Structure

- **Modify** `backend/app/settings.py` — add `history_database_path`, `permanent_history_airports`.
- **Create** `backend/app/history_store.py` — long-term store lifecycle: availability check, `ATTACH` helper, `history_coverage` table + read/upsert, and the window-routing gate.
- **Modify** `backend/app/db.py` — history-aware read variants (`*_unified`) that attach + union + dedup, plus a multi-aircraft dedup helper.
- **Modify** `backend/app/services.py` — route the scan cold-reads through the unified variants.
- **Modify** `backend/app/main.py` — route `/airports/{icao}/track-history` through the unified read; raise the `days` cap for allowlisted airports.
- **Modify** `backend/app/archive.py` — `archive_to_history_once` (copy-before-prune) + wire into `archive_loop`.
- **Create** `backend/scripts/seed_history_coverage.py` — one-time coverage seeding for migration.
- **Create** tests: `backend/tests/test_history_store.py`, `test_history_reads.py`, `test_history_maintain.py`.

---

## Task 1: Settings + `history_store` module (availability, attach, coverage, routing gate)

**Files:**
- Modify: `backend/app/settings.py`
- Create: `backend/app/history_store.py`
- Test: `backend/tests/test_history_store.py`

**Interfaces (produced):**
- `settings.history_database_path: str` (default `"data/klmo_history.sqlite3"`), `settings.permanent_history_airports: list[str]` (default `["KLMO"]`).
- `history_store.available(settings) -> bool` — the file exists and is non-empty.
- `history_store.window_reaches_history(start_ts, settings, *, now=None) -> bool` — `start_ts < now - horizon_seconds`.
- `history_store.airport_allowed(icao, settings) -> bool`.
- `history_store.attached(conn, settings)` — context manager: `ATTACH`es the history DB as schema `hist` if available, `DETACH`es on exit; no-op if unavailable.
- `history_store.HISTORY_COVERAGE_DDL`, `read_coverage(conn, icao) -> dict | None`, `upsert_coverage(conn, icao, min_ts, max_ts)` (operates on the history DB connection).

- [ ] **Step 1: Write the failing test** — `backend/tests/test_history_store.py`

```python
from __future__ import annotations
import sqlite3
from app import db, history_store
from app.settings import Settings


def _make_history(path):
    db.init_db(path)  # track_archive schema + seeds
    with db.db_session(path) as c:
        c.executescript(history_store.HISTORY_COVERAGE_DDL)
        history_store.upsert_coverage(c, "KLMO", 1000, 2000)


def test_available_and_allowed(tmp_path):
    hp = str(tmp_path / "hist.sqlite3")
    s = Settings(history_database_path=hp, permanent_history_airports=["KLMO"])
    assert history_store.available(s) is False           # not created yet
    _make_history(hp)
    assert history_store.available(s) is True
    assert history_store.airport_allowed("KLMO", s) is True
    assert history_store.airport_allowed("KBJC", s) is False


def test_window_routing_gate(tmp_path):
    s = Settings(track_archive_horizon_days=7)
    now = 10_000_000
    assert history_store.window_reaches_history(now - 6 * 86400, s, now=now) is False
    assert history_store.window_reaches_history(now - 8 * 86400, s, now=now) is True


def test_attach_and_read_coverage(tmp_path):
    hp = str(tmp_path / "hist.sqlite3")
    _make_history(hp)
    op = str(tmp_path / "op.sqlite3"); db.init_db(op)
    s = Settings(history_database_path=hp, permanent_history_airports=["KLMO"])
    with db.db_session(op) as conn:
        with history_store.attached(conn, s) as attached:
            assert attached is True
            cov = conn.execute("SELECT min_ts, max_ts FROM hist.history_coverage WHERE airport_icao='KLMO'").fetchone()
            assert (cov["min_ts"], cov["max_ts"]) == (1000, 2000)
```

- [ ] **Step 2: Run — expect fail** (`ModuleNotFoundError: app.history_store`).
`cd backend && .venv/bin/python -m pytest tests/test_history_store.py -v`

- [ ] **Step 3: Implement**

Settings additions (`backend/app/settings.py`, near `track_archive_horizon_days`):

```python
    history_database_path: str = "data/klmo_history.sqlite3"
    permanent_history_airports: list[str] = Field(default=["KLMO"])
```

`backend/app/history_store.py`:

```python
"""The long-term (permanent) track store: a second SQLite file on the
track_archive schema, holding the backfilled history plus everything that ages
out of the operational 7-day archive. Attached on demand for long-window reads.
"""
from __future__ import annotations

import os
from contextlib import contextmanager

from .settings import Settings

HISTORY_COVERAGE_DDL = """
CREATE TABLE IF NOT EXISTS history_coverage (
  airport_icao TEXT PRIMARY KEY,
  min_ts INTEGER,
  max_ts INTEGER,
  updated_at INTEGER NOT NULL DEFAULT (CAST(strftime('%s','now') AS INTEGER))
);
"""


def horizon_seconds(settings: Settings) -> int:
    return int(settings.track_archive_horizon_days) * 86400


def available(settings: Settings) -> bool:
    p = settings.history_database_path
    return bool(p) and os.path.exists(p) and os.path.getsize(p) > 0


def airport_allowed(icao: str, settings: Settings) -> bool:
    return icao.upper() in {a.upper() for a in settings.permanent_history_airports}


def window_reaches_history(start_ts: int, settings: Settings, *, now: int | None = None) -> bool:
    import time
    n = int(now) if now is not None else int(time.time())
    return int(start_ts) < n - horizon_seconds(settings)


@contextmanager
def attached(conn, settings: Settings):
    """ATTACH the history DB as `hist` for the duration; no-op if unavailable."""
    if not available(settings):
        yield False
        return
    conn.execute("ATTACH ? AS hist", (settings.history_database_path,))
    try:
        yield True
    finally:
        conn.execute("DETACH hist")


def read_coverage(conn, icao: str) -> dict | None:
    row = conn.execute(
        "SELECT airport_icao, min_ts, max_ts FROM history_coverage WHERE airport_icao = ?",
        (icao.upper(),),
    ).fetchone()
    return dict(row) if row else None


def upsert_coverage(conn, icao: str, min_ts: int, max_ts: int) -> None:
    conn.execute(
        """
        INSERT INTO history_coverage (airport_icao, min_ts, max_ts, updated_at)
        VALUES (?, ?, ?, CAST(strftime('%s','now') AS INTEGER))
        ON CONFLICT(airport_icao) DO UPDATE SET
          min_ts = MIN(history_coverage.min_ts, excluded.min_ts),
          max_ts = MAX(history_coverage.max_ts, excluded.max_ts),
          updated_at = excluded.updated_at
        """,
        (icao.upper(), int(min_ts), int(max_ts)),
    )
    conn.commit()
```

- [ ] **Step 4: Run — expect pass** (3 passed). Then full suite once: `.venv/bin/python -m pytest`.
- [ ] **Step 5: Commit** — `feat(history): long-term store module — attach, coverage, window-routing gate`.

---

## Task 2: History-aware unified reads (attach + union + dedup)

**Files:**
- Modify: `backend/app/db.py`
- Test: `backend/tests/test_history_reads.py`

**Interfaces (produced):**
- `db.dedup_samples_by_key(rows: list[dict], winners: set[tuple[str,int]] | None = None) -> list[dict]` — dedup a flat sample list by `(icao24, timestamp)`; earlier rows win.
- `db.read_track_archive_bbox_unified(conn, settings, min_lat, max_lat, min_lon, max_lon, start_ts, end_ts, *, ceiling_ft_msl=None, airport_icao=None) -> list[dict]` — operational rows always; when `window_reaches_history` AND `airport_allowed(airport_icao)` AND history available, ATTACH `hist` and UNION `hist.track_archive` over the same predicate, deduped by `(icao24, timestamp)` with operational winning; ordered `(icao24, timestamp)`.
- `db.bulk_read_track_archive_unified(conn, settings, icao24s, start_ts, end_ts, *, airport_icao=None) -> dict[str, list[dict]]` — same routing for the by-icao read.

**Consumes:** `history_store.{window_reaches_history, airport_allowed, available, attached}`, existing `_TRACK_ARCHIVE_COLUMNS`, `_track_archive_row_to_sample`.

- [ ] **Step 1: Write the failing test** — `backend/tests/test_history_reads.py`

```python
from __future__ import annotations
from app import db, history_store
from app.settings import Settings

# bbox covering KLMO
BOX = dict(min_lat=40.0, max_lat=40.4, min_lon=-105.4, max_lon=-104.9)
NOW = 20_000_000
HORIZON = 7 * 86400


def _sample(ts, lat=40.16, lon=-105.16, src="live"):
    return {"timestamp": ts, "lat": lat, "lon": lon, "altitude_ft": 3000,
            "baro_altitude_ft": 3000, "geo_altitude_ft": 3100, "heading_deg": 90.0,
            "vertical_rate_fpm": 0, "callsign": "T", "in_window": True, "source": src}


def _seed(op, hist):
    db.init_db(op); db.init_db(hist)
    with db.db_session(hist) as c:
        c.executescript(history_store.HISTORY_COVERAGE_DDL)
    # operational: a recent sample; history: an old sample + a duplicate of the recent one
    with db.db_session(op) as c:
        db.archive_track_samples(c, "aaaa01", [_sample(NOW - 3600, src="live")])       # recent
    with db.db_session(hist) as c:
        db.archive_track_samples(c, "aaaa01", [_sample(NOW - 30 * 86400, src="adsblol")])   # old
        db.archive_track_samples(c, "aaaa01", [_sample(NOW - 3600, src="adsblol")])         # dup of recent


def test_short_window_never_touches_history(tmp_path):
    op = str(tmp_path/"op.sqlite3"); hist = str(tmp_path/"h.sqlite3"); _seed(op, hist)
    s = Settings(history_database_path=hist, permanent_history_airports=["KLMO"], track_archive_horizon_days=7)
    with db.db_session(op) as c:
        rows = db.read_track_archive_bbox_unified(
            c, s, **BOX, start_ts=NOW - 2 * 3600, end_ts=NOW, airport_icao="KLMO")
    assert len(rows) == 1                      # only the recent operational sample; history not consulted


def test_long_window_unions_and_dedupes(tmp_path):
    op = str(tmp_path/"op.sqlite3"); hist = str(tmp_path/"h.sqlite3"); _seed(op, hist)
    s = Settings(history_database_path=hist, permanent_history_airports=["KLMO"], track_archive_horizon_days=7)
    with db.db_session(op) as c:
        rows = db.read_track_archive_bbox_unified(
            c, s, **BOX, start_ts=NOW - 60 * 86400, end_ts=NOW, airport_icao="KLMO")
    ts = sorted(r["timestamp"] for r in rows)
    assert ts == [NOW - 30 * 86400, NOW - 3600]      # old (history) + recent; the duplicate collapsed
    recent = [r for r in rows if r["timestamp"] == NOW - 3600][0]
    assert recent["source"] == "live"                # operational won the collision


def test_long_window_disallowed_airport_stays_operational(tmp_path):
    op = str(tmp_path/"op.sqlite3"); hist = str(tmp_path/"h.sqlite3"); _seed(op, hist)
    s = Settings(history_database_path=hist, permanent_history_airports=["KLMO"], track_archive_horizon_days=7)
    with db.db_session(op) as c:
        rows = db.read_track_archive_bbox_unified(
            c, s, **BOX, start_ts=NOW - 60 * 86400, end_ts=NOW, airport_icao="KBJC")
    assert [r["timestamp"] for r in rows] == [NOW - 3600]   # history skipped for non-allowlisted airport
```

- [ ] **Step 2: Run — expect fail** (`AttributeError: ...read_track_archive_bbox_unified`).

- [ ] **Step 3: Implement** — add to `backend/app/db.py`:

```python
from . import history_store  # top of file with the other intra-package imports


def dedup_samples_by_key(rows: list[dict]) -> list[dict]:
    """Dedup a flat sample list by (icao24, timestamp); first occurrence wins.
    Callers pass operational rows first so they win over history copies."""
    seen: set[tuple[str, int]] = set()
    out: list[dict] = []
    for r in rows:
        key = (str(r.get("icao24", "")).lower(), int(r["timestamp"]))
        if key in seen:
            continue
        seen.add(key)
        out.append(r)
    return out


def read_track_archive_bbox_unified(
    conn, settings, min_lat, max_lat, min_lon, max_lon, start_ts, end_ts,
    *, ceiling_ft_msl=None, airport_icao=None,
):
    op_rows = read_track_archive_bbox(
        conn, min_lat, max_lat, min_lon, max_lon, start_ts, end_ts, ceiling_ft_msl=ceiling_ft_msl)
    use_hist = (
        airport_icao is not None
        and history_store.window_reaches_history(start_ts, settings)
        and history_store.airport_allowed(airport_icao, settings)
        and history_store.available(settings)
    )
    if not use_hist:
        return op_rows
    ceiling_clause = "" if ceiling_ft_msl is None else " AND (altitude_ft IS NULL OR altitude_ft <= :ceil)"
    with history_store.attached(conn, settings):
        rows = conn.execute(
            f"""
            SELECT {', '.join(_TRACK_ARCHIVE_COLUMNS)} FROM hist.track_archive
            WHERE lat BETWEEN :minlat AND :maxlat AND lon BETWEEN :minlon AND :maxlon
              AND timestamp BETWEEN :start AND :end {ceiling_clause}
            ORDER BY icao24 ASC, timestamp ASC
            """,
            {"minlat": min_lat, "maxlat": max_lat, "minlon": min_lon, "maxlon": max_lon,
             "start": int(start_ts), "end": int(end_ts), "ceil": ceiling_ft_msl},
        ).fetchall()
    hist_rows = [_track_archive_row_to_sample(r) for r in rows]
    merged = dedup_samples_by_key(op_rows + hist_rows)   # op first -> op wins
    merged.sort(key=lambda s: (s["icao24"], s["timestamp"]))
    return merged


def bulk_read_track_archive_unified(conn, settings, icao24s, start_ts, end_ts, *, airport_icao=None):
    out = bulk_read_track_archive(conn, icao24s, start_ts, end_ts)
    use_hist = (
        airport_icao is not None
        and history_store.window_reaches_history(start_ts, settings)
        and history_store.airport_allowed(airport_icao, settings)
        and history_store.available(settings)
    )
    if not use_hist:
        return out
    lowered = [i.lower() for i in icao24s]
    placeholders = ",".join("?" for _ in lowered)
    with history_store.attached(conn, settings):
        rows = conn.execute(
            f"""SELECT {', '.join(_TRACK_ARCHIVE_COLUMNS)} FROM hist.track_archive
                WHERE icao24 IN ({placeholders}) AND timestamp BETWEEN ? AND ?
                ORDER BY icao24 ASC, timestamp ASC""",
            (*lowered, int(start_ts), int(end_ts)),
        ).fetchall()
    hist_by_icao: dict[str, list[dict]] = {}
    for row in rows:
        hist_by_icao.setdefault(row["icao24"], []).append(_track_archive_row_to_sample(row))
    for icao, hist_i in hist_by_icao.items():           # merge per-icao, operational wins
        out[icao] = dedup_samples_by_key(out.get(icao, []) + hist_i)
        out[icao].sort(key=lambda s: s["timestamp"])
    return out
```

*(Note the SQLite chunking caveat: `bulk_read_track_archive` already chunks the icao list at 500 for the operational read; the history read above assumes `len(icao24s) <= 999` — for the pattern/circuit callers that's always true. If a caller can exceed it, chunk identically. Call this out in the implementation.)*

- [ ] **Step 4: Run — expect pass** (3 passed) + full suite.
- [ ] **Step 5: Commit** — `feat(history): window-routed unified reads (attach + union + dedup)`.

---

## Task 3: Route the API/scan consumers through the unified reads

**Files:**
- Modify: `backend/app/main.py` (the `/airports/{icao}/track-history` endpoint, ~line 1490)
- Modify: `backend/app/track_history.py` (`MAX_DAYS`) and/or endpoint cap logic
- Modify: `backend/app/services.py` (scan cold reads at ~1047 and ~1069)
- Test: extend `backend/tests/test_history_reads.py` (endpoint-level) or a new `test_history_endpoint.py`

**Interfaces (consumes):** `db.read_track_archive_bbox_unified`, `db.bulk_read_track_archive_unified`, `history_store.airport_allowed`.

- [ ] **Step 1: Write the failing test** — assert `/airports/KLMO/track-history?days=60` returns tracks drawn from the history store (seed an operational + history DB via the app's settings override), and that `days` > 7 is rejected (422) for a non-allowlisted airport. (Use FastAPI `TestClient` with a `settings_dep` override pointing at temp DBs; follow the existing endpoint-test pattern in `tests/test_api.py`.)

- [ ] **Step 2: Run — expect fail.**

- [ ] **Step 3: Implement**
  - In the track-history endpoint (main.py:1490): replace `db.read_track_archive_bbox(conn, ...)` with `db.read_track_archive_bbox_unified(conn, settings, ..., airport_icao=airport.icao)`.
  - Raise the `days` ceiling for allowlisted airports. The current `Query(le=track_history.MAX_DAYS)` is a static bound; change to a generous static max (e.g. `le=track_history.MAX_HISTORY_DAYS = 366`) and enforce the per-airport rule in the handler: `if days > track_history.MAX_DAYS and not history_store.airport_allowed(icao, settings): raise HTTPException(422, "history beyond 7 days is not available for this airport")`. Keep the default `days=7`.
  - In `services.py`: the two cold reads (`bulk_read_track_archive` at ~1047, `read_track_archive` at ~1069) feed live scans, whose windows are ≤ retention — so routing them through the `_unified` variants (passing the scan's `airport_icao`) is a no-op for short windows but makes long-window scans correct too. Swap them to `bulk_read_track_archive_unified` / a `read_track_archive_unified` (add the latter mirroring the bulk one). Thread `airport_icao` from the scan params.

- [ ] **Step 4: Run — expect pass** + full suite.
- [ ] **Step 5: Commit** — `feat(history): serve >7-day track-history for allowlisted airports`.

---

## Task 4: Maintenance job — copy each day into the long-term store before prune

**Files:**
- Modify: `backend/app/archive.py`
- Test: `backend/tests/test_history_maintain.py`

**Interfaces (produced):** `archive.archive_to_history_once(settings) -> dict` — for each allowlisted airport, copy operational `track_archive` rows within the airport bbox and older than `now - (horizon - buffer)` into the history store (`INSERT OR IGNORE`), update `history_coverage`, return stats. Called in `archive_loop` immediately **before** `prune_once`.

- [ ] **Step 1: Write the failing test** — `backend/tests/test_history_maintain.py`
  - Seed an operational DB with KLMO-bbox samples spanning older-than-horizon and newer-than-horizon. Run `archive_to_history_once`. Assert: the older-than-horizon in-bbox samples now exist in the history store; newer ones do NOT (still hot); `history_coverage` for KLMO updated; a second run writes 0 new (idempotent); a sample outside every allowlisted bbox is not copied.

- [ ] **Step 2: Run — expect fail.**

- [ ] **Step 3: Implement** — in `backend/app/archive.py`:

```python
async def archive_to_history_once(settings) -> dict:
    """Copy soon-to-be-pruned operational samples into the permanent history
    store, for allowlisted airports. Runs just before prune_once so nothing is
    lost. Idempotent (INSERT OR IGNORE)."""
    from . import db, history_store
    from .geo import bbox_for_radius
    if not settings.permanent_history_airports:
        return {"skipped": "no_allowlist"}
    now = int(time.time())
    # copy anything older than (horizon - buffer) so it's captured before prune
    cutoff = now - (horizon_seconds(settings) - ARCHIVE_TAIL_BUFFER_SECONDS)
    total = 0
    # ensure history store + coverage table exist
    db.init_db(settings.history_database_path)
    with db.db_session(settings.history_database_path) as hconn:
        hconn.executescript(history_store.HISTORY_COVERAGE_DDL)
    with db.db_session(settings.database_path) as op, db.db_session(settings.history_database_path) as hist:
        for icao in settings.permanent_history_airports:
            airport = db.get_airport(op, icao)
            if airport is None:
                continue
            min_lat, min_lon, max_lat, max_lon = bbox_for_radius(airport.lat, airport.lon, HISTORY_ARCHIVE_RING_NM)
            rows = op.execute(
                f"""SELECT {', '.join(db._TRACK_ARCHIVE_COLUMNS)} FROM track_archive
                    WHERE lat BETWEEN ? AND ? AND lon BETWEEN ? AND ?
                      AND timestamp < ?
                    ORDER BY icao24, timestamp""",
                (min_lat, max_lat, min_lon, max_lon, cutoff),
            ).fetchall()
            if not rows:
                continue
            by_icao: dict[str, list[dict]] = {}
            for r in rows:
                by_icao.setdefault(r["icao24"], []).append(db._track_archive_row_to_sample(r))
            written = 0
            for icao24, samples in by_icao.items():
                written += db.archive_track_samples(hist, icao24, samples)
            hist.commit()
            ts_min = min(r["timestamp"] for r in rows)
            ts_max = max(r["timestamp"] for r in rows)
            history_store.upsert_coverage(hist, icao, ts_min, ts_max)
            total += written
    return {"samples_written": total}
```

Add a module constant `HISTORY_ARCHIVE_RING_NM = 12.0` (matches the backfill filter radius). Wire into `archive_loop`: inside the prune branch, call `await archive_to_history_once(settings)` **before** `prune_once(...)`. Log its stats.

- [ ] **Step 4: Run — expect pass** + full suite.
- [ ] **Step 5: Commit** — `feat(history): copy allowlisted airports into long-term store before prune`.

---

## Task 5: Migration — ship the store to prod, seed coverage, close the seam

**Files:**
- Create: `backend/scripts/seed_history_coverage.py`
- Doc: append a "History store migration" section to the deploy runbook.

This is an operational task (like the backfill's Task 7), run once against prod.

- [ ] **Step 1: `seed_history_coverage.py`** — opens the history DB, ensures `history_coverage` exists, computes actual `MIN(timestamp)/MAX(timestamp)` per airport from `track_archive` (grouping by the allowlisted airports' bboxes, or simply the whole store for a single-airport file), and upserts coverage. Include a `--db` arg. (Unit-test the compute against a tiny seeded DB.)

- [ ] **Step 2: Runbook steps** (documented, run by hand):
  1. **DONE (2026-07-22):** the ~7 GB file is already on prod. The Mac download was abandoned (home link ~9 Mbps). Instead the file was pulled **directly droplet → prod over the intra-sfo3 link in 33 s** (prod `rsync`-pulled from the backfill droplet `164.90.145.178` via SSH agent-forwarding — no keys on disk, no firewall change). It now lives at prod `/srv/circlejerk/data/klmo_history.sqlite3` = container path `/app/data/klmo_history.sqlite3`. Verified: 365 dates in `backfill_progress`, ts 2025‑07‑21→2026‑07‑20. Prod disk after: 36 G / 58 G (63%). **The backfill droplet (`586466666`) is intentionally left running** as the source-of-record + the only place to re-run the 9 gap days.
     - Real prod facts for wiring: operational DB is `211 MB` (`/app/data/circlejerk.sqlite3`, env `CIRCLEJERK_DATABASE_PATH`), 3 app containers (`app-api-1`, `app-worker-1`, `app-web-1`) share the bind mount. This confirms the **separate-store** choice — do NOT merge 47M rows into the live 211 MB file.
  2. Set `CIRCLEJERK_HISTORY_DATABASE_PATH=/app/data/klmo_history.sqlite3` and `CIRCLEJERK_PERMANENT_HISTORY_AIRPORTS=["KLMO"]` in the prod compose env; ensure `data/klmo_history.sqlite3*` is git-ignored (already is).
  3. Run `python -m scripts.seed_history_coverage --db /app/data/klmo_history.sqlite3` (inside the app container).
  4. **Close the seam:** compare `history_coverage.max_ts` (2026‑07‑20) against the operational archive's oldest retained day. If there's a gap (days between the backfill end and where live archiving began retaining), run the backfill CLI for those dates into the history store — the backfill droplet is still up for exactly this. Going forward, `archive_to_history_once` keeps them adjacent automatically.
  5. **Deploy the Task 1–4 code to prod**, restart the app, smoke-test `GET /airports/KLMO/track-history?days=60` returns history-era tracks and `?days=60` for a non-allowlisted airport returns 422.
  6. **9 gap days** (`2025-10-15/12-25/12-31`, `2026-04-30/05-04/05-05/05-06/06-11`, one more): 3 are a corrupt-member `zlib.error` the tar filter should catch (widen `except OSError` → `except (OSError, zlib.error)` and re-run those dates); 6 are prod-0-missing and may recover from a `--instances staging-0` pass. Optional cleanup; each is recorded `failed` in `backfill_progress`, not silently absent.

- [ ] **Step 3: Commit** — `feat(history): coverage-seeding script + migration runbook`.

---

## Self-Review notes (addressed in this plan)

- **Backups:** the operational backup script copies `circlejerk.sqlite3` only; the history store is append-only and should be backed up on a slower/incremental cadence — note in the runbook, not code.
- **Read latency:** bbox-over-a-year across 47M rows may need a better index than the existing `timestamp` / `(icao24, timestamp)`. Measure the real `track-history?days=365` latency after migration; only add a spatial/covering index if it's too slow. (Deliberately not pre-optimized.)
- **Connection reuse:** `attached()` ATTACH/DETACH per read is simple and correct; if profiling shows overhead, cache an attached connection. Not now.

## Out of scope

- **Operations-over-the-year** (re-running touch-and-go / landing detection across the backfilled year to populate the `operations` table for annual counts) — a natural follow-on using the same history reads + the existing `scripts/backfill_landings.py` pattern, but a separate plan.
- **Frontend UX** for year-scale map/stat windows (the data becomes API-reachable here; surfacing it in the UI is separate).
- **Permanent history for non-KLMO airports** (each needs its own backfill first; the allowlist already generalizes).
