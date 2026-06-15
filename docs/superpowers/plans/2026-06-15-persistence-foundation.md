# Persistence Foundation (Operations Log) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Persist every detected aircraft operation (circle / touch-and-go / low-approach / pass-over-user) into a durable SQLite `operations` table so later phases (deviation, rotation/cowboy, the KPIs page) can read airport history spanning days/weeks.

**Architecture:** Add an `operations` table to the existing SQLite schema with the full column set the later phases need (deviation, wind, and origin columns are created now but left NULL until those phases populate them via targeted UPDATEs). Detected events already flow through `services.run_detectors_for_monitor(store, settings, conn, monitor)`, which has a live DB connection in hand. We map each newly-detected event dict to an operation row and idempotently upsert it (keyed on the event's stable `sha256` id, so re-scans and worker restarts never double-count).

**Tech Stack:** Python 3, SQLite (`sqlite3`, WAL), FastAPI app package `app`, pytest. Tests run from `backend/` via `python -m pytest`. Use the project venv at `/Users/d/Code/FAA_circle_jerk/.venv`.

This is Plan 1 of 6 for the VNAP/deviation/rotation/KPIs spec
(`docs/superpowers/specs/2026-06-15-vnap-patterns-deviation-rotation-kpis-design.md`).
It produces working, testable software on its own: detected ops are durably logged.

---

### Background facts (so you don't have to rediscover them)

- The schema is one big SQL string `SCHEMA` in `backend/app/db.py` (`CREATE TABLE IF NOT EXISTS …`).
  `init_db(path)` runs `conn.executescript(SCHEMA)` then `_migrate(conn)` then `seed_db(conn)`. Adding a
  new `CREATE TABLE IF NOT EXISTS` to `SCHEMA` is the correct way to add a table — no migration needed.
- Event dicts are produced by `app.detectors.detect_events(track, airport, runways, params)` and
  `detect_events_over_period(...)`. Each event has a stable string `id` from
  `_event_id(...) = hashlib.sha256(payload)[:20]`, plus keys: `type`, `icao24`, `callsign`,
  `timestamp`, `airport_icao`, and (per type) `runway_id`, `runway_heading_deg`, `turn_direction`,
  `min_altitude_ft_agl`. Circles carry `turn_direction`; T&Gs/low-approaches carry `runway_id`,
  `runway_heading_deg`, `min_altitude_ft_agl`; passes carry `min_altitude_ft_agl`.
- `services.run_detectors_for_monitor` already imports `db` (it calls `db.get_airport`,
  `db.runways_for_airport`). Its detection loop adds new events to the Redis store and increments
  `written`. We add a persistence call there.
- Test conventions (`backend/tests/test_core.py`): a local `seeded_conn(path)` helper does
  `db.connect(str(path)); conn.executescript(db.SCHEMA); db.seed_db(conn); conn.commit()`. Tests use
  the `tmp_path` fixture. `app.domain.ScanParams` is constructed as
  `ScanParams(airport_icao="KBJC", user_lat=…, user_lon=…)`.

---

## Task 1: Add the `operations` table to the schema

**Files:**
- Test: `backend/tests/test_operations.py` (create)
- Modify: `backend/app/db.py` (the `SCHEMA` string, before the closing `"""` at line ~235)

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_operations.py`:

```python
from __future__ import annotations

import pytest

from app import db
from app.db import Airport
from app.detectors import detect_events
from app.domain import ScanParams


def seeded_conn(path):
    conn = db.connect(str(path))
    conn.executescript(db.SCHEMA)
    db.seed_db(conn)
    conn.commit()
    return conn


def airport_kbjc() -> Airport:
    return Airport(
        icao="KBJC",
        iata="BJC",
        name="Rocky Mountain Metropolitan Airport",
        city="Broomfield, CO",
        country="US",
        lat=39.9088,
        lon=-105.1172,
        elevation_ft=5673,
        is_towered=True,
    )


def sample(ts: int, lat: float, lon: float, alt_agl: int = 900, icao24: str = "abc123") -> dict:
    return {
        "icao24": icao24,
        "callsign": "N123AB",
        "timestamp": ts,
        "lat": lat,
        "lon": lon,
        "geo_altitude_ft": airport_kbjc().elevation_ft + alt_agl,
        "velocity_kt": 80,
        "vertical_rate_fpm": 0,
        "on_ground": False,
    }


def closed_loop_track() -> list[dict]:
    points = [
        (39.9238, -105.1172),
        (39.9194, -105.1013),
        (39.9088, -105.0950),
        (39.8982, -105.1013),
        (39.8938, -105.1172),
        (39.8982, -105.1331),
        (39.9088, -105.1394),
        (39.9194, -105.1331),
        (39.9238, -105.1172),
    ]
    return [sample(1000 + i * 30, lat, lon) for i, (lat, lon) in enumerate(points)]


def test_operations_table_exists(tmp_path):
    conn = seeded_conn(tmp_path / "t.sqlite3")
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='operations'"
    ).fetchone()
    assert row is not None
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(operations)").fetchall()}
    assert {"id", "icao", "icao24", "type", "timestamp", "deviation_mean_nm",
            "wind_from_deg", "origin_airport_icao", "flight_school"} <= cols
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/d/Code/FAA_circle_jerk/backend && /Users/d/Code/FAA_circle_jerk/.venv/bin/python -m pytest tests/test_operations.py::test_operations_table_exists -v`
Expected: FAIL — `assert row is not None` fails (table does not exist).

- [ ] **Step 3: Add the table to `SCHEMA`**

In `backend/app/db.py`, immediately before the closing `"""` that ends the `SCHEMA` string (after the
`idx_aircraft_registry_imports_started` index, line ~234), insert:

```sql

CREATE TABLE IF NOT EXISTS operations (
  id TEXT PRIMARY KEY,
  icao TEXT NOT NULL,
  icao24 TEXT,
  callsign TEXT,
  registration TEXT,
  type TEXT NOT NULL,
  timestamp INTEGER NOT NULL,
  runway_id TEXT,
  runway_heading_deg REAL,
  turn_direction TEXT,
  min_altitude_ft_agl INTEGER,
  matched_pattern_id INTEGER,
  deviation_mean_nm REAL,
  deviation_peak_nm REAL,
  time_off_pattern_s INTEGER,
  time_total_s INTEGER,
  pct_off_pattern REAL,
  wind_from_deg INTEGER,
  wind_speed_kt REAL,
  headwind_kt REAL,
  origin_airport_icao TEXT,
  origin_label TEXT,
  operator TEXT,
  flight_school TEXT,
  created_at INTEGER NOT NULL DEFAULT (CAST(strftime('%s','now') AS INTEGER)),
  FOREIGN KEY (icao) REFERENCES airports(icao)
);
CREATE INDEX IF NOT EXISTS idx_operations_icao_ts ON operations(icao, timestamp);
CREATE INDEX IF NOT EXISTS idx_operations_icao24 ON operations(icao24);
CREATE INDEX IF NOT EXISTS idx_operations_type ON operations(type);
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/d/Code/FAA_circle_jerk/backend && /Users/d/Code/FAA_circle_jerk/.venv/bin/python -m pytest tests/test_operations.py::test_operations_table_exists -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/db.py backend/tests/test_operations.py
git commit -m "feat: add operations table for durable ops log"
```

---

## Task 2: Map an event dict to an operation row (`operation_from_event`)

**Files:**
- Modify: `backend/app/db.py` (add function near the other query helpers, after `row_to_airport`)
- Test: `backend/tests/test_operations.py`

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_operations.py`:

```python
def test_operation_from_event_maps_circle_fields():
    event = {
        "id": "deadbeefcafe00000001",
        "type": "circle",
        "icao24": "a4c1d8",
        "callsign": "N4052F",
        "timestamp": 1718464800,
        "airport_icao": "KBJC",
        "turn_direction": "left",
    }
    op = db.operation_from_event(event)
    assert op["id"] == "deadbeefcafe00000001"
    assert op["icao"] == "KBJC"
    assert op["icao24"] == "a4c1d8"
    assert op["callsign"] == "N4052F"
    assert op["type"] == "circle"
    assert op["timestamp"] == 1718464800
    assert op["turn_direction"] == "left"
    # runway/altitude absent on this circle event → None
    assert op["runway_id"] is None
    assert op["min_altitude_ft_agl"] is None


def test_operation_from_event_maps_touch_and_go_fields():
    event = {
        "id": "deadbeefcafe00000002",
        "type": "touch_and_go",
        "icao24": "a4c1d8",
        "callsign": "N4052F",
        "timestamp": 1718464900,
        "airport_icao": "KLMO",
        "runway_id": "29",
        "runway_heading_deg": 290,
        "min_altitude_ft_agl": 35,
    }
    op = db.operation_from_event(event)
    assert op["type"] == "touch_and_go"
    assert op["runway_id"] == "29"
    assert op["runway_heading_deg"] == 290
    assert op["min_altitude_ft_agl"] == 35
    assert op["turn_direction"] is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/d/Code/FAA_circle_jerk/backend && /Users/d/Code/FAA_circle_jerk/.venv/bin/python -m pytest tests/test_operations.py -k operation_from_event -v`
Expected: FAIL — `AttributeError: module 'app.db' has no attribute 'operation_from_event'`.

- [ ] **Step 3: Implement `operation_from_event`**

In `backend/app/db.py`, after the `row_to_airport` function, add:

```python
def operation_from_event(event: dict) -> dict:
    """Map a detector event dict to an `operations` row dict.

    Only the core (always-known) columns are populated here. Deviation, wind,
    and origin columns are filled in by later phases via targeted UPDATEs.
    """
    return {
        "id": event["id"],
        "icao": event.get("airport_icao"),
        "icao24": event.get("icao24"),
        "callsign": event.get("callsign"),
        "registration": event.get("registration"),
        "type": event.get("type"),
        "timestamp": int(event["timestamp"]),
        "runway_id": event.get("runway_id"),
        "runway_heading_deg": event.get("runway_heading_deg"),
        "turn_direction": event.get("turn_direction"),
        "min_altitude_ft_agl": event.get("min_altitude_ft_agl"),
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/d/Code/FAA_circle_jerk/backend && /Users/d/Code/FAA_circle_jerk/.venv/bin/python -m pytest tests/test_operations.py -k operation_from_event -v`
Expected: PASS (both tests).

- [ ] **Step 5: Commit**

```bash
git add backend/app/db.py backend/tests/test_operations.py
git commit -m "feat: map detector events to operation rows"
```

---

## Task 3: Idempotent upsert (`upsert_operation`)

**Files:**
- Modify: `backend/app/db.py` (after `operation_from_event`)
- Test: `backend/tests/test_operations.py`

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_operations.py`:

```python
def test_upsert_operation_is_idempotent(tmp_path):
    conn = seeded_conn(tmp_path / "t.sqlite3")
    op = db.operation_from_event({
        "id": "dup00000000000000001",
        "type": "circle",
        "icao24": "a4c1d8",
        "callsign": "N4052F",
        "timestamp": 1718464800,
        "airport_icao": "KBJC",
        "turn_direction": "left",
    })
    db.upsert_operation(conn, op)
    db.upsert_operation(conn, op)  # second insert of same id must not duplicate
    conn.commit()
    rows = conn.execute("SELECT * FROM operations WHERE id = ?", (op["id"],)).fetchall()
    assert len(rows) == 1
    assert rows[0]["icao"] == "KBJC"
    assert rows[0]["type"] == "circle"
    assert rows[0]["turn_direction"] == "left"
    assert rows[0]["created_at"] is not None
    # enrichment columns default to NULL until later phases populate them
    assert rows[0]["deviation_mean_nm"] is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/d/Code/FAA_circle_jerk/backend && /Users/d/Code/FAA_circle_jerk/.venv/bin/python -m pytest tests/test_operations.py::test_upsert_operation_is_idempotent -v`
Expected: FAIL — `AttributeError: module 'app.db' has no attribute 'upsert_operation'`.

- [ ] **Step 3: Implement `upsert_operation`**

In `backend/app/db.py`, after `operation_from_event`, add:

```python
def upsert_operation(conn: sqlite3.Connection, op: dict) -> None:
    """Insert an operation row, ignoring rows whose id already exists.

    The event id is a stable sha256 hash of (type, icao24, airport, time-bucket),
    so DO NOTHING gives cross-restart, cross-monitor idempotency without
    clobbering enrichment columns (deviation/wind/origin) filled by later phases.
    """
    conn.execute(
        """
        INSERT INTO operations
          (id, icao, icao24, callsign, registration, type, timestamp,
           runway_id, runway_heading_deg, turn_direction, min_altitude_ft_agl)
        VALUES
          (:id, :icao, :icao24, :callsign, :registration, :type, :timestamp,
           :runway_id, :runway_heading_deg, :turn_direction, :min_altitude_ft_agl)
        ON CONFLICT(id) DO NOTHING
        """,
        op,
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/d/Code/FAA_circle_jerk/backend && /Users/d/Code/FAA_circle_jerk/.venv/bin/python -m pytest tests/test_operations.py::test_upsert_operation_is_idempotent -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/db.py backend/tests/test_operations.py
git commit -m "feat: idempotent upsert for operations"
```

---

## Task 4: Read operations by window and type (`read_operations`)

**Files:**
- Modify: `backend/app/db.py` (after `upsert_operation`)
- Test: `backend/tests/test_operations.py`

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_operations.py`:

```python
def _mk_op(id_, ts, type_="circle", icao="KBJC"):
    return db.operation_from_event({
        "id": id_, "type": type_, "icao24": "a4c1d8", "callsign": "N1",
        "timestamp": ts, "airport_icao": icao,
    })


def test_read_operations_filters_by_window_and_type(tmp_path):
    conn = seeded_conn(tmp_path / "t.sqlite3")
    db.upsert_operation(conn, _mk_op("op1", 1000, "circle"))
    db.upsert_operation(conn, _mk_op("op2", 2000, "touch_and_go"))
    db.upsert_operation(conn, _mk_op("op3", 3000, "circle"))
    db.upsert_operation(conn, _mk_op("op4", 9999, "circle", icao="KLMO"))  # other airport
    conn.commit()

    # window only
    rows = db.read_operations(conn, "KBJC", 1500, 3500)
    assert [r["id"] for r in rows] == ["op2", "op3"]

    # window + type filter
    rows = db.read_operations(conn, "KBJC", 0, 10000, types=["circle"])
    assert [r["id"] for r in rows] == ["op1", "op3"]

    # other airport isolated
    rows = db.read_operations(conn, "KLMO", 0, 10000)
    assert [r["id"] for r in rows] == ["op4"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/d/Code/FAA_circle_jerk/backend && /Users/d/Code/FAA_circle_jerk/.venv/bin/python -m pytest tests/test_operations.py::test_read_operations_filters_by_window_and_type -v`
Expected: FAIL — `AttributeError: module 'app.db' has no attribute 'read_operations'`.

- [ ] **Step 3: Implement `read_operations`**

In `backend/app/db.py`, after `upsert_operation`, add:

```python
def read_operations(
    conn: sqlite3.Connection,
    icao: str,
    start_ts: int,
    end_ts: int,
    types: list[str] | None = None,
) -> list[sqlite3.Row]:
    query = (
        "SELECT * FROM operations "
        "WHERE icao = ? AND timestamp >= ? AND timestamp <= ?"
    )
    args: list = [icao, start_ts, end_ts]
    if types:
        placeholders = ",".join("?" for _ in types)
        query += f" AND type IN ({placeholders})"
        args.extend(types)
    query += " ORDER BY timestamp ASC"
    return conn.execute(query, args).fetchall()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/d/Code/FAA_circle_jerk/backend && /Users/d/Code/FAA_circle_jerk/.venv/bin/python -m pytest tests/test_operations.py::test_read_operations_filters_by_window_and_type -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/db.py backend/tests/test_operations.py
git commit -m "feat: read operations by airport, window, and type"
```

---

## Task 5: Persist a batch of events (`persist_events`)

**Files:**
- Modify: `backend/app/db.py` (after `read_operations`)
- Test: `backend/tests/test_operations.py`

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_operations.py`:

```python
def test_persist_events_writes_and_dedupes(tmp_path):
    conn = seeded_conn(tmp_path / "t.sqlite3")
    events = [
        {"id": "e1", "type": "circle", "icao24": "a", "callsign": "N1",
         "timestamp": 1000, "airport_icao": "KBJC"},
        {"id": "e2", "type": "touch_and_go", "icao24": "a", "callsign": "N1",
         "timestamp": 1100, "airport_icao": "KBJC", "runway_id": "12L"},
        {"id": "e1", "type": "circle", "icao24": "a", "callsign": "N1",
         "timestamp": 1000, "airport_icao": "KBJC"},  # duplicate id
    ]
    processed = db.persist_events(conn, events)
    conn.commit()
    assert processed == 3  # all three considered
    rows = db.read_operations(conn, "KBJC", 0, 10000)
    assert len(rows) == 2  # but only two distinct rows persisted


def test_persist_events_skips_events_without_id_or_airport(tmp_path):
    conn = seeded_conn(tmp_path / "t.sqlite3")
    events = [
        {"type": "circle", "icao24": "a", "timestamp": 1000, "airport_icao": "KBJC"},  # no id
        {"id": "x", "type": "circle", "icao24": "a", "timestamp": 1000},               # no airport
    ]
    processed = db.persist_events(conn, events)
    conn.commit()
    assert processed == 0
    assert db.read_operations(conn, "KBJC", 0, 10000) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/d/Code/FAA_circle_jerk/backend && /Users/d/Code/FAA_circle_jerk/.venv/bin/python -m pytest tests/test_operations.py -k persist_events -v`
Expected: FAIL — `AttributeError: module 'app.db' has no attribute 'persist_events'`.

- [ ] **Step 3: Implement `persist_events`**

In `backend/app/db.py`, after `read_operations`, add:

```python
def persist_events(conn: sqlite3.Connection, events) -> int:
    """Upsert each detector event as an operation row.

    Returns the number of events processed (events missing an id or airport are
    skipped). Idempotency is enforced by `upsert_operation`'s ON CONFLICT.
    """
    processed = 0
    for event in events:
        if not event.get("id") or not event.get("airport_icao"):
            continue
        upsert_operation(conn, operation_from_event(event))
        processed += 1
    return processed
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/d/Code/FAA_circle_jerk/backend && /Users/d/Code/FAA_circle_jerk/.venv/bin/python -m pytest tests/test_operations.py -k persist_events -v`
Expected: PASS (both tests).

- [ ] **Step 5: Commit**

```bash
git add backend/app/db.py backend/tests/test_operations.py
git commit -m "feat: persist_events batch helper"
```

---

## Task 6: Wire persistence into the detector run path

**Files:**
- Modify: `backend/app/services.py` (`run_detectors_for_monitor`, lines ~352-368)
- Test: `backend/tests/test_operations.py`

- [ ] **Step 1: Write the failing test (end-to-end on a real detected event)**

This exercises the exact production path: real detector output → `persist_events` → `read_operations`.

Append to `backend/tests/test_operations.py`:

```python
def test_detected_circle_is_persisted(tmp_path):
    conn = seeded_conn(tmp_path / "t.sqlite3")
    ap = airport_kbjc()
    runways = db.runways_for_airport(conn, "KBJC")
    params = ScanParams(airport_icao="KBJC", user_lat=40.0, user_lon=-105.2)

    events = detect_events(closed_loop_track(), ap, runways, params)
    assert any(e["type"] == "circle" for e in events)  # sanity: detector fired

    db.persist_events(conn, events)
    conn.commit()

    rows = db.read_operations(conn, "KBJC", 0, 10_000)
    assert any(r["type"] == "circle" for r in rows)
    assert all(r["icao"] == "KBJC" for r in rows)
```

- [ ] **Step 2: Run test to verify it passes already (it exercises code from Tasks 1-5)**

Run: `cd /Users/d/Code/FAA_circle_jerk/backend && /Users/d/Code/FAA_circle_jerk/.venv/bin/python -m pytest tests/test_operations.py::test_detected_circle_is_persisted -v`
Expected: PASS. (This test validates the mapping against a *real* event shape. If it fails because
`detect_events` returns no circle, re-check the `closed_loop_track` coordinates match
`test_core.py::test_circle_detector_detects_closed_loop`.)

- [ ] **Step 3: Wire `persist_events` into `run_detectors_for_monitor`**

In `backend/app/services.py`, find the detection loop in `run_detectors_for_monitor` (around lines
352-368). Replace this block:

```python
    existing_ids = await store.existing_event_ids(monitor["hash"])
    for icao24, track in zip(icao24s, tracks):
        if not track_intersects_bbox(track, tuple(monitor["bbox"])):
            continue
        events = (
            detect_events_over_period(track, airport, runways, params, start_ts, end_ts)
            if start_ts is not None and end_ts is not None
            else detect_events(track, airport, runways, params)
        )
        for event in events:
            if event["id"] not in existing_ids:
                await store.add_event(monitor["hash"], event, settings.event_ttl_seconds)
                existing_ids.add(event["id"])
                written += 1
    return written
```

with:

```python
    existing_ids = await store.existing_event_ids(monitor["hash"])
    new_events: list[dict] = []
    for icao24, track in zip(icao24s, tracks):
        if not track_intersects_bbox(track, tuple(monitor["bbox"])):
            continue
        events = (
            detect_events_over_period(track, airport, runways, params, start_ts, end_ts)
            if start_ts is not None and end_ts is not None
            else detect_events(track, airport, runways, params)
        )
        for event in events:
            if event["id"] not in existing_ids:
                await store.add_event(monitor["hash"], event, settings.event_ttl_seconds)
                existing_ids.add(event["id"])
                new_events.append(event)
                written += 1
    # Durably log the ops so the KPIs page / deviation / rotation phases have
    # history beyond the ephemeral Redis (~4h) + archive (~24h) windows.
    if new_events:
        db.persist_events(conn, new_events)
    return written
```

(`db` is already imported in `services.py`.)

- [ ] **Step 4: Run the full backend test suite to confirm no regression**

Run: `cd /Users/d/Code/FAA_circle_jerk/backend && /Users/d/Code/FAA_circle_jerk/.venv/bin/python -m pytest tests/ -v`
Expected: PASS — all existing tests (`test_core.py`, `test_weather.py`, `test_archive.py`,
`test_registry.py`, `test_adsblol_historical.py`) plus the new `test_operations.py` pass.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services.py backend/tests/test_operations.py
git commit -m "feat: persist detected operations from the worker run path"
```

---

## Self-review

**Spec coverage (foundation slice only):** The spec's "persistent operations log" is the foundation
for Features 2–4. This plan creates the `operations` table with the full column set (Task 1),
populates the core columns from the worker path (Tasks 2-6), and provides `read_operations` for
downstream phases (Task 4). Deviation columns (`deviation_mean_nm`, `deviation_peak_nm`,
`time_off_pattern_s`, `time_total_s`, `pct_off_pattern`, `matched_pattern_id`), wind columns
(`wind_from_deg`, `wind_speed_kt`, `headwind_kt`), and origin columns (`origin_airport_icao`,
`origin_label`, `operator`, `flight_school`) exist but are intentionally NULL — they are populated
by Plans 4 (deviation), 5 (rotation/wind), and the origin-enrichment step. This is by design, not a gap.

**Placeholder scan:** No TBD/TODO/"handle errors"/"similar to" placeholders. Every code step shows
complete code; every run step shows the exact command and expected result.

**Type consistency:** `operation_from_event` returns a dict whose keys exactly match the named
parameters in `upsert_operation`'s SQL (`:id`, `:icao`, … `:min_altitude_ft_agl`). `persist_events`
calls `operation_from_event` then `upsert_operation`. `read_operations` selects `*` and tests read by
column name. The `operations.id` PK matches the detector event `id` (sha256 string). All function
names are stable across tasks: `operation_from_event`, `upsert_operation`, `read_operations`,
`persist_events`.

**Idempotency note:** Persistence is gated on `event["id"] not in existing_ids` (new-this-scan per
monitor) AND `ON CONFLICT(id) DO NOTHING` (global/table-level). After a worker restart with a warm
Redis, an event already in `operations` but re-added to a monitor's set re-triggers an upsert that is
a harmless no-op. Correct, just slightly redundant — acceptable.
