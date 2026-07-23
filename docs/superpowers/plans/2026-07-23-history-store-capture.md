# `history_store` Cold-Store Capture Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Bring the production-only `history_store` cold-store feature (a year of KLMO history served through `/v1/operations` and `/v1/tracks`) into version control with tests, reconciled with the `/v1` reshape that rewrote the same two handlers.

**Architecture:** The feature is captured by **transferring the exact production code, not retyping it** — it is proven against 47M rows, and the base logic in the two `db.py` read functions is byte-identical to the branch's copies except for the history additions. The two handler collisions are orthogonal: the reshape maps output through Pydantic models; history_store sources input rows. So each handler keeps its reshaped model mapping and gains only the cold-routing computation.

**Tech Stack:** FastAPI, SQLite via `sqlite3` (ATTACH-based hot/cold merge), pytest. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-07-23-history-store-capture-design.md`

## Global Constraints

- Base branch is `feat/public-api-keys` (current tip carries the `/v1` reshape). Not `main`.
- Backend tests: `cd backend && .venv/bin/python -m pytest -q`. Bare `python`/`pytest` are **not** on PATH.
- OpenAPI check (only relevant to Task 3's `altitude_datum` doc/model touch): `backend/.venv/bin/python scripts/verify_openapi_doc.py` from repo root, and regenerate with `scripts/build_openapi_json.py` if the YAML changes.
- **Transfer, do not retype.** The authoritative source for the large verbatim blocks is the production droplet. Pull with:
  `scp -i ~/.ssh/id_ed25519_dodom -o IdentitiesOnly=yes root@137.184.244.248:/srv/circlejerk/app/backend/app/<file> <dest>`
  Reference copies already exist in this session at
  `/private/tmp/claude-501/-Users-d-Code-FAA-circle-jerk/ea64346b-c55d-4539-aba6-4bde885596b5/scratchpad/prod-history-store/` (`*.prod` whole files, and `refs/*.prod.py` extracted functions).
- **Only the two read handlers move on the `/v1` side.** Do not change any reshaped model, field name, response shape, the drift gate, or any other `/v1` endpoint. history_store changes *which rows* populate operations/tracks, never their shape.
- The feature is **inert unless configured**: `history_database_path` defaults to `None`, `history_store.available()` returns `False`, and every code path behaves exactly as before. This is what keeps the existing 814 tests green — no test may set a real history path except the new ones that explicitly test the cold merge.
- No new backend dependencies. No frontend changes.
- Baseline before Task 1: backend **814 passed**, verifier clean, frontend 19 files / 104 tests.

---

### Task 1: settings fields and the `history_store` module

**Files:**
- Modify: `backend/app/settings.py` (add two fields near the other admin/feature settings)
- Create: `backend/app/history_store.py`
- Test: `backend/tests/test_history_store.py` (create)

**Interfaces:**
- Produces: `settings.history_database_path: str | None`, `settings.permanent_history_airports: list[str]`; `history_store.available(settings) -> bool`, `history_store.airport_allowed(icao: str | None, settings) -> bool`, `history_store.horizon_seconds(settings) -> int`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_history_store.py`:

```python
from __future__ import annotations

import sqlite3

from app import history_store
from app.settings import Settings


def _settings(tmp_path, path=None, airports=("KLMO",)):
    return Settings(
        history_database_path=path,
        permanent_history_airports=list(airports),
        track_archive_horizon_days=7,
    )


def test_available_is_false_when_unconfigured(tmp_path):
    assert history_store.available(_settings(tmp_path, path=None)) is False


def test_available_is_false_when_the_file_is_missing(tmp_path):
    missing = str(tmp_path / "nope.sqlite3")
    assert history_store.available(_settings(tmp_path, path=missing)) is False


def test_available_is_false_for_an_empty_file(tmp_path):
    empty = tmp_path / "empty.sqlite3"
    empty.write_bytes(b"")
    assert history_store.available(_settings(tmp_path, path=str(empty))) is False


def test_available_is_true_for_a_populated_file(tmp_path):
    db = tmp_path / "cold.sqlite3"
    sqlite3.connect(str(db)).execute("CREATE TABLE t (x)").connection.commit()
    assert history_store.available(_settings(tmp_path, path=str(db))) is True


def test_airport_allowed_gates_on_the_allowlist(tmp_path):
    s = _settings(tmp_path, airports=("KLMO",))
    assert history_store.airport_allowed("KLMO", s) is True
    assert history_store.airport_allowed("klmo", s) is True   # case-insensitive
    assert history_store.airport_allowed("KBJC", s) is False


def test_airport_allowed_permits_an_icao24_only_query(tmp_path):
    # A query with no airport carries None; the cold store is airport-scoped by
    # its own contents, so those are safe to serve. This is NOT a grant bypass —
    # the /v1/tracks handler still forces `airport` for restricted keys before
    # this is ever reached.
    assert history_store.airport_allowed(None, _settings(tmp_path)) is True


def test_horizon_seconds_is_days_times_86400(tmp_path):
    assert history_store.horizon_seconds(_settings(tmp_path)) == 7 * 86400
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && .venv/bin/python -m pytest tests/test_history_store.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.history_store'`

- [ ] **Step 3: Add the settings fields**

In `backend/app/settings.py`, next to the other feature settings (e.g. near `track_archive_horizon_days`), add verbatim:

```python
    # Long-term ("cold") track store: a separate, never-pruned SQLite file on the
    # same track_archive schema, holding the backfilled year + everything that
    # ages out of the hot archive. None = feature off (behaves as before).
    history_database_path: str | None = None
    permanent_history_airports: list[str] = ["KLMO"]
```

Confirm `track_archive_horizon_days` already exists in `settings.py` (it does — `horizon_seconds` reads it). Do not add it.

- [ ] **Step 4: Create the module**

Copy `history_store.py` verbatim from the droplet — do not retype it:

```bash
scp -i ~/.ssh/id_ed25519_dodom -o IdentitiesOnly=yes \
  root@137.184.244.248:/srv/circlejerk/app/backend/app/history_store.py \
  backend/app/history_store.py
```

(Reference copy: `…/scratchpad/prod-history-store/history_store.py.prod`.) It is 33 lines: a module docstring plus `available`, `airport_allowed`, and `horizon_seconds`. Read it after copying to confirm it imports only `os` and `.settings` — no other dependency.

- [ ] **Step 5: Run test to verify it passes**

Run: `cd backend && .venv/bin/python -m pytest tests/test_history_store.py -q`
Expected: PASS, 7 passed

- [ ] **Step 6: Full suite + commit**

Run: `cd backend && .venv/bin/python -m pytest -q`
Expected: 821 passed (814 + 7). The two new settings fields default to off, so nothing else changes.

```bash
git add backend/app/settings.py backend/app/history_store.py backend/tests/test_history_store.py
git commit -m "feat(history): capture the history_store module and settings"
```

---

### Task 2: the `db.py` hot/cold merge

The correctness-critical task. Both read functions gain the ATTACH-based merge; the tests are what production never had.

**Files:**
- Modify: `backend/app/db.py` — `read_operations_page` and `read_track_archive_page`
- Test: `backend/tests/test_history_store_merge.py` (create)

**Interfaces:**
- Consumes: nothing new
- Produces: `db.read_operations_page(conn, *, icao, start_ts, end_ts, types=None, icao24=None, runway_id=None, after=None, limit=500, history_path=None, hot_cutoff_ts=None)`; `db.read_track_archive_page(conn, *, start_ts, end_ts, icao24=None, bbox=None, after=None, limit=500, history_path=None, hot_cutoff_ts=None)`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_history_store_merge.py`:

```python
from __future__ import annotations

import time

from app import db


def _seed_operation(conn, *, id, icao, ts, type="landing", runway="29"):
    conn.execute(
        "INSERT INTO operations (id, icao, icao24, type, timestamp, runway_id) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (id, icao, "a26f5e", type, ts, runway),
    )
    conn.commit()


def _cold_db(tmp_path):
    path = str(tmp_path / "cold.sqlite3")
    db.init_db(path)          # same schema as hot
    return path


def test_no_history_path_is_operational_only(tmp_path):
    hot = str(tmp_path / "hot.sqlite3"); db.init_db(hot)
    with db.connect(hot) as conn:
        _seed_operation(conn, id="h1", icao="KLMO", ts=1000)
        rows = db.read_operations_page(
            conn, icao="KLMO", start_ts=0, end_ts=2000, history_path=None,
            hot_cutoff_ts=None,
        )
    assert [r["id"] for r in rows] == ["h1"]


def test_cold_rows_older_than_the_cutoff_are_served(tmp_path):
    hot = str(tmp_path / "hot.sqlite3"); db.init_db(hot)
    cold = _cold_db(tmp_path)
    cutoff = 10_000
    with db.connect(cold) as cconn:
        _seed_operation(cconn, id="c1", icao="KLMO", ts=5_000)   # older than cutoff
    with db.connect(hot) as conn:
        _seed_operation(conn, id="h1", icao="KLMO", ts=15_000)   # newer than cutoff
        rows = db.read_operations_page(
            conn, icao="KLMO", start_ts=0, end_ts=20_000,
            history_path=cold, hot_cutoff_ts=cutoff,
        )
    # both the cold and the hot row, merged, ordered by (timestamp, id)
    assert [r["id"] for r in rows] == ["c1", "h1"]


def test_the_hot_cold_boundary_does_not_duplicate(tmp_path):
    # A row exactly at the cutoff belongs to hot (>= cutoff); the same id in cold
    # (< cutoff would be a different ts) must not both appear. Seed the boundary.
    hot = str(tmp_path / "hot.sqlite3"); db.init_db(hot)
    cold = _cold_db(tmp_path)
    cutoff = 10_000
    with db.connect(cold) as cconn:
        _seed_operation(cconn, id="c1", icao="KLMO", ts=9_999)   # last cold ts
    with db.connect(hot) as conn:
        _seed_operation(conn, id="h1", icao="KLMO", ts=10_000)   # first hot ts
        rows = db.read_operations_page(
            conn, icao="KLMO", start_ts=0, end_ts=20_000,
            history_path=cold, hot_cutoff_ts=cutoff,
        )
    ids = [r["id"] for r in rows]
    assert ids == ["c1", "h1"]
    assert len(ids) == len(set(ids))   # no duplication at the seam


def test_a_fully_recent_window_never_touches_cold(tmp_path):
    hot = str(tmp_path / "hot.sqlite3"); db.init_db(hot)
    cold = _cold_db(tmp_path)
    cutoff = 10_000
    with db.connect(cold) as cconn:
        _seed_operation(cconn, id="c1", icao="KLMO", ts=5_000)
    with db.connect(hot) as conn:
        _seed_operation(conn, id="h1", icao="KLMO", ts=15_000)
        # window starts after the cutoff: cold slice is empty by construction
        rows = db.read_operations_page(
            conn, icao="KLMO", start_ts=12_000, end_ts=20_000,
            history_path=cold, hot_cutoff_ts=cutoff,
        )
    assert [r["id"] for r in rows] == ["h1"]


def test_the_limit_applies_across_the_merged_page(tmp_path):
    hot = str(tmp_path / "hot.sqlite3"); db.init_db(hot)
    cold = _cold_db(tmp_path)
    cutoff = 10_000
    with db.connect(cold) as cconn:
        for i in range(5):
            _seed_operation(cconn, id=f"c{i}", icao="KLMO", ts=1_000 + i)
    with db.connect(hot) as conn:
        for i in range(5):
            _seed_operation(conn, id=f"h{i}", icao="KLMO", ts=15_000 + i)
        rows = db.read_operations_page(
            conn, icao="KLMO", start_ts=0, end_ts=20_000,
            history_path=cold, hot_cutoff_ts=cutoff, limit=3,
        )
    # ordered by (timestamp, id), the first 3 are the earliest cold rows
    assert [r["id"] for r in rows] == ["c0", "c1", "c2"]
```

> If `db.connect`/`db.init_db` differ in name, use the actual helpers (read the top of `db.py` and `backend/tests/test_api_key_store.py` for the established open pattern). If the `operations` table requires more NOT NULL columns than the seed sets, add them to `_seed_operation` — read the `CREATE TABLE operations` in `db.py`'s `SCHEMA`.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && .venv/bin/python -m pytest tests/test_history_store_merge.py -q`
Expected: FAIL — `read_operations_page() got an unexpected keyword argument 'history_path'`

- [ ] **Step 3: Graft the production merge — verbatim, then verify (spec D2)**

Pull the two functions' exact production bodies and replace the branch's copies:

```bash
scp -i ~/.ssh/id_ed25519_dodom -o IdentitiesOnly=yes \
  root@137.184.244.248:/srv/circlejerk/app/backend/app/db.py /tmp/db.prod.py
```

Replace `read_operations_page` and `read_track_archive_page` in `backend/app/db.py` with the versions in `/tmp/db.prod.py` (reference: `…/scratchpad/prod-history-store/refs/read_operations_page.prod.py` and `read_track_archive_page.prod.py`). The mechanism, for your understanding: when `history_path` is set and `start_ts < hot_cutoff_ts`, the function `ATTACH ? AS hist`es the cold file on the same connection, reads the cold slice `[start_ts, cutoff-1]` from `hist.<table>` and the hot slice `[max(start_ts, cutoff), end_ts]` from the local table, merges, re-sorts by `(timestamp, id)` (operations) / `(timestamp, icao24)` (tracks), and applies `limit`. With no `history_path` it is the operational-only read.

**Then run spec D2's verification** — prove the only change to these two functions is the history logic:

```bash
cd /Users/d/Code/FAA_circle_jerk
git diff backend/app/db.py | grep '^[-+]' | grep -v '^[-+][-+]' | grep -vE 'history_path|hot_cutoff|hist|cold|_page|_filters|use_cold|ATTACH|DETACH|attached|cutoff|docstring|"""|# ' | head
```
Expected: nothing that isn't part of the history merge. If a line unrelated to the cold store changed, **stop and report it** — it means production's `db.py` carried an unexpected divergence, which is a finding, not something to absorb.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && .venv/bin/python -m pytest tests/test_history_store_merge.py -q`
Expected: PASS, 5 passed

- [ ] **Step 5: Full suite + commit**

Run: `cd backend && .venv/bin/python -m pytest -q`
Expected: 826 passed (821 + 5). The existing operations/tracks tests pass `history_path=None` implicitly (the new params default to `None`), so they are unaffected.

```bash
git add backend/app/db.py backend/tests/test_history_store_merge.py
git commit -m "feat(history): hot/cold merge in the operations and track reads"
```

---

### Task 3: wire the two `/v1` handlers to cold routing, and fix `altitude_datum`

The reconciliation with the reshape. Both handlers keep their reshaped model mapping and gain the cold-routing computation. Also the probe-found `altitude_datum` fix (spec D5).

**Files:**
- Modify: `backend/app/public_api.py` — the `history_store` import, `list_operations`, `list_tracks`
- Modify: `backend/app/v1_schemas.py` — `_ALTITUDE_DATUM_BY_SOURCE`
- Test: `backend/tests/test_public_api_contract.py` (append)

**Interfaces:**
- Consumes: `history_store` (Task 1), the merged reads (Task 2)

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_public_api_contract.py`:

```python
def test_backfill_source_reports_barometric_datum():
    # The cold store's 47M rows carry source='adsblol_globe_history'. Without
    # this mapping every historical /v1/tracks row reports altitude_datum
    # 'unknown' despite being barometric.
    from app import v1_schemas
    row = {
        "icao24": "a26f5e", "timestamp": 1784000000, "lat": 40.0, "lon": -105.1,
        "altitude_ft": 900.0, "baro_altitude_ft": 900.0, "geo_altitude_ft": None,
        "heading_deg": 290.0, "vertical_rate_fpm": None, "callsign": "N765J",
        "emitter_category": None, "source": "adsblol_globe_history",
    }
    out = v1_schemas.track_sample_out(row)
    assert out.altitude_datum == "barometric"
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd backend && .venv/bin/python -m pytest tests/test_public_api_contract.py -q -k backfill_source`
Expected: FAIL — `assert 'unknown' == 'barometric'`

- [ ] **Step 3: Add the datum mapping**

In `backend/app/v1_schemas.py`, in `_ALTITUDE_DATUM_BY_SOURCE`, add the backfill source alongside the live ones:

```python
    "adsblol_globe_history": "barometric",  # the KLMO year backfill (adsb.lol globe history)
```

This is a Python-internal map, not part of the wire schema — `altitude_datum`'s enum already includes `barometric`, so **the OpenAPI document does not change and must not be edited**. Confirm with `git status` that neither generated JSON moved.

- [ ] **Step 4: Wire `list_operations`**

In `backend/app/public_api.py`, add `history_store` to the existing `from . import ...` line (it currently imports `api_keys, db, vnap` and `v1_schemas` — add `history_store`). Then in `list_operations`, after `after = decode_cursor(cursor) if cursor else None`, insert the cold-routing computation and pass the two kwargs to the read — keeping the reshaped model mapping that follows unchanged:

```python
        hist_path = (
            settings.history_database_path
            if history_store.available(settings) and history_store.airport_allowed(icao, settings)
            else None
        )
        rows = db.read_operations_page(
            conn,
            icao=icao,
            start_ts=start_ts,
            end_ts=end_ts,
            types=[type] if type else None,
            icao24=icao24,
            runway_id=runway,
            after=after,
            limit=size,
            history_path=hist_path,
            hot_cutoff_ts=int(time.time()) - settings.track_archive_horizon_days * 86400,
        )
```

Leave the `[v1_schemas.operation_out({**dict(row), "airport_icao": row["icao"]}) for row in rows]` mapping and the `OperationPage(**paged(...))` return exactly as they are.

- [ ] **Step 5: Wire `list_tracks`**

In `list_tracks`, the airport icao is only bound inside `if airport:`. Track it in a variable and compute `hist_path` before the read. Change the `with db_session(...)` block so it reads:

```python
    bbox = None
    airport_icao = None
    with db_session(settings.database_path) as conn:
        if airport:
            icao = require_airport(ctx, airport)
            airport_icao = icao
            found = db.get_airport(conn, icao)
            if found is None:
                raise ApiError(404, "not_found", f"unknown airport {icao}")
            min_lat, min_lon, max_lat, max_lon = bbox_for_radius(
                found.lat, found.lon, TRACK_RING_NM
            )
            bbox = (min_lat, min_lon, max_lat, max_lon)

        hist_path = (
            settings.history_database_path
            if history_store.available(settings)
            and history_store.airport_allowed(airport_icao, settings)
            else None
        )
        rows = db.read_track_archive_page(
            conn,
            start_ts=start_ts,
            end_ts=end_ts,
            icao24=icao24,
            bbox=bbox,
            after=after,
            limit=size,
            history_path=hist_path,
            hot_cutoff_ts=int(time.time()) - settings.track_archive_horizon_days * 86400,
        )
```

Leave the reshaped `track_sample_out` mapping and `TrackPage` return that follow unchanged. **Do not touch** the restricted-key `forbidden_airport` guard above this block — it is the reshape's airport-restriction enforcement and a captured test depends on it.

- [ ] **Step 6: End-to-end — a cold row surfaces through the reshaped models (spec D3)**

This is the proof the two features compose: a `/v1/operations` query reaching into the
cold period returns the historical row mapped through `operation_out`, with the reshaped
field names present (and the un-derived quality fields null, not crashing).

First read `backend/tests/test_public_api.py` for the exact configuration/mint helpers it
exposes (the reshape's `v1_client_and_key` fixture in `test_public_api_contract.py` seeds
an airport and mints an unrestricted key using them). Append, using whatever those helpers
are actually named — the skeleton below shows the shape, adapt the helper calls:

```python
def test_operations_query_serves_cold_rows_through_the_reshaped_model(tmp_path, monkeypatch):
    from app import db
    hot = str(tmp_path / "hot.sqlite3"); db.init_db(hot)
    cold = str(tmp_path / "cold.sqlite3"); db.init_db(cold)
    old_ts = int(time.time()) - 60 * 86400            # 60 days ago, well past the 7-day horizon
    with db.connect(cold) as c:
        c.execute(
            "INSERT INTO operations (id, icao, icao24, type, timestamp, runway_id) "
            "VALUES ('cold1','KLMO','a26f5e','landing', ?, '29')", (old_ts,))
        c.commit()
    monkeypatch.setenv("CIRCLEJERK_DATABASE_PATH", hot)
    monkeypatch.setenv("CIRCLEJERK_HISTORY_DATABASE_PATH", cold)
    monkeypatch.setenv("CIRCLEJERK_ENVIRONMENT", "test")
    # mint an unrestricted key and build a TestClient the same way
    # test_public_api.py does (configure() + mint()); then:
    body = client.get(
        f"/v1/operations?airport=KLMO&since={old_ts-100}&until={int(time.time())}&limit=10",
        headers={"X-Api-Key": key},
    ).json()
    ids = [r["id"] for r in body["data"]]
    assert "cold1" in ids                              # the cold row was served
    cold_row = next(r for r in body["data"] if r["id"] == "cold1")
    assert cold_row["timestamp_ts"] == old_ts          # reshaped name, not `timestamp`
    assert "fraction_off_pattern" in cold_row          # present…
    assert cold_row["fraction_off_pattern"] is None     # …and null (un-derived), not a 500
```

- [ ] **Step 7: Verify auth is unweakened over cold data**

The cold routing must be an *addition* after the existing gates, never a bypass. Add a
test that a KLMO-restricted key still gets 403 for a foreign airport even with a cold store
configured — proving `history_store.airport_allowed` did not open a hole. Reuse the
restricted-key pattern from `backend/tests/test_admin_api_keys_scoped.py` (its
`become_partner(airports="KLMO")` helper demotes the session key to a KLMO-only grant):

```python
def test_a_restricted_key_still_403s_a_foreign_airport_with_cold_configured(tmp_path, monkeypatch):
    from app import db
    hot = str(tmp_path / "hot.sqlite3"); db.init_db(hot)
    cold = str(tmp_path / "cold.sqlite3"); db.init_db(cold)
    monkeypatch.setenv("CIRCLEJERK_DATABASE_PATH", hot)
    monkeypatch.setenv("CIRCLEJERK_HISTORY_DATABASE_PATH", cold)
    monkeypatch.setenv("CIRCLEJERK_ENVIRONMENT", "test")
    # mint a key restricted to KLMO (become_partner airports="KLMO"), then:
    r = client.get("/v1/operations?airport=KBJC&limit=1", headers={"X-Api-Key": key})
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "forbidden_airport"
```

Both tests must use the real mint/configure helpers rather than the pseudo-`client`/`key`
shown; the assertions are the load-bearing part.

- [ ] **Step 8: Run tests, verifier, full suite**

```bash
cd backend && .venv/bin/python -m pytest tests/test_public_api_contract.py tests/test_public_api_operations.py tests/test_public_api_tracks.py -q
cd /Users/d/Code/FAA_circle_jerk && backend/.venv/bin/python scripts/verify_openapi_doc.py
cd backend && .venv/bin/python -m pytest -q
```
Expected: verifier clean (no doc change); full suite 829+ passed. Confirm `git status` shows **no** change to either `generated/openapi.json` or `docs/api/openapi.yaml`.

- [ ] **Step 9: Commit**

```bash
git add backend/app/public_api.py backend/app/v1_schemas.py backend/tests/test_public_api_contract.py
git commit -m "feat(history): route /v1 operations and tracks to the cold store; fix backfill datum"
```

---

### Task 4: the archive sync loop and deploy config

Captures the writer side and the one compose env line, so the branch is a complete, deployable source of truth.

**Files:**
- Modify: `backend/app/archive.py` — the `history_store` import, `archive_to_history_once`, and its call inside `archive_loop`
- Modify: `docker-compose.prod.yml` — one env line on the api service
- Modify: `DEPLOY.md` — a note on the cold store
- Test: `backend/tests/test_history_store.py` (append)

**Interfaces:**
- Consumes: `history_store` (Task 1)
- Produces: `archive.archive_to_history_once(settings) -> dict`

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_history_store.py`:

```python
import asyncio

from app import archive as archive_mod


def test_archive_to_history_is_a_noop_when_unavailable(tmp_path):
    # The sync must be inert without a configured, present cold store — this is
    # what keeps it dormant in local/test and every existing suite green.
    s = _settings(tmp_path, path=None)
    result = asyncio.run(archive_mod.archive_to_history_once(s))
    assert result == {"skipped": "history_unavailable_or_no_allowlist"}
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd backend && .venv/bin/python -m pytest tests/test_history_store.py -q -k noop_when_unavailable`
Expected: FAIL — `AttributeError: module 'app.archive' has no attribute 'archive_to_history_once'`

- [ ] **Step 3: Add the sync function and wire the loop**

Add `history_store` to `archive.py`'s `from . import ...` imports. Copy `archive_to_history_once` verbatim from the droplet (reference: `…/scratchpad/prod-history-store/refs/archive_to_history_once.prod.py`):

```bash
scp -i ~/.ssh/id_ed25519_dodom -o IdentitiesOnly=yes \
  root@137.184.244.248:/srv/circlejerk/app/backend/app/archive.py /tmp/archive.prod.py
```

Insert the `archive_to_history_once` function (it early-returns `{"skipped": ...}` when `not history_store.available(settings) or not settings.permanent_history_airports`, then ATTACHes the cold store and `INSERT OR IGNORE`s the aging tail per permanent airport).

Then wire it into the existing `archive_loop`: production calls it on the prune interval. In your branch's `archive_loop`, find the prune-interval block (`if time.time() - last_prune >= prune_interval_seconds:`) and add, inside it, the call production has:

```python
                synced = await archive_to_history_once(settings)
                if synced.get("samples_written"):
                    LOGGER.info(
                        "history store: synced %d samples", synced["samples_written"]
                    )
```

Match production exactly — diff your `archive_loop` against `/tmp/archive.prod.py`'s and confirm the only additions are the `archive_to_history_once` call and its log line.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && .venv/bin/python -m pytest tests/test_history_store.py -q`
Expected: PASS (8 in the file now).

- [ ] **Step 5: The compose env line**

In `docker-compose.prod.yml`, on the **api** service's `environment:` block (next to the other `CIRCLEJERK_*` vars), add:

```yaml
      CIRCLEJERK_HISTORY_DATABASE_PATH: ${CIRCLEJERK_HISTORY_DATABASE_PATH:-/app/data/klmo_history.sqlite3}
```

The file lives in the already-mounted `/srv/circlejerk/data:/app/data` volume, so no new volume. Do **not** set it on any other service. (Production currently hardcodes the path; the `${...:-default}` form keeps it overridable and matches the compose style the SSO work established.)

- [ ] **Step 6: The deploy note**

In `DEPLOY.md`, add a short subsection under the deploy steps:

```markdown
### The KLMO cold store (history_store)

`/v1/operations` and `/v1/tracks` fall through to a long-term SQLite file
(`/app/data/klmo_history.sqlite3`, ~7.4 GB, a year of KLMO history) via
`CIRCLEJERK_HISTORY_DATABASE_PATH`. The file is **data, not code** — it lives in
the `/app/data` volume and is never in git or rsynced. The code that serves it
is now in the branch, so an `rsync --delete` no longer threatens it. After a
deploy, the first prune cycle runs `archive_to_history_once`, which holds a write
lock while syncing the aging tail — watch the first cycle on a fresh deploy.
```

- [ ] **Step 7: Full sweep + commit**

```bash
cd backend && .venv/bin/python -m pytest -q
cd /Users/d/Code/FAA_circle_jerk && backend/.venv/bin/python scripts/verify_openapi_doc.py
```
Expected: full suite green; verifier clean.

```bash
git add backend/app/archive.py docker-compose.prod.yml DEPLOY.md backend/tests/test_history_store.py
git commit -m "feat(history): archive sync loop, compose env, deploy note"
```

---

## Verification summary

| Check | Command |
|---|---|
| Backend | `cd backend && .venv/bin/python -m pytest -q` |
| OpenAPI (unchanged) | `backend/.venv/bin/python scripts/verify_openapi_doc.py` |
| Frontend (untouched, sanity) | `cd frontend && npx vitest run` |

After merge, deploy the **reconciled** branch (never the reshape alone). The `rsync --delete` that first surfaced this now deletes only stale `.bak-*` files, not `history_store.py`.

## Known gaps, deliberately left (from the spec)

- Aggregates stay hot-only (spec D6) — the year is reachable via operations/tracks, not as stats.
- The missing derived layers (deviation/VNAP, wind, origin, runway rollups) are a data-pipeline effort, not this capture.
- The root cause — production carrying hand-edited, uncommitted source — is not fixed by this capture; a "deploy only from git, never edit the droplet tree" discipline is the real fix, and worth its own follow-up.
