# Rotation / "Cowboy" Detection Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Track which runway end the airport is actively using, detect when that flow flips, attribute each flip to the aircraft that caused it (the "cowboy"), snapshot the wind at the change, and tag each operation with its headwind component. Surface the active runway + last change as a small badge on the map.

**Architecture:** Two new tables — `runway_flow` (the active-end timeline) and `runway_changes` (the cowboy log). A pure `flow.py` computes the established active end from recent runway-tagged operations (≥3 consecutive same-end ops OR ~10 min of agreement), detects a flip, and finds the cowboy (the earliest op of the new sustained run). The worker fetches the current wind once per tick, tags new ops with their headwind component, and runs the flow update. A `GET /airports/{icao}/flow` endpoint + a small frontend `FlowBadge` expose it.

**Tech Stack:** Python 3, SQLite, pytest (backend, fully unit-testable); React + TS (the badge, gated on `tsc`/`build`).

This is Plan 5 of 6. Builds on Plan 1 (`operations` with `wind_from_deg`/`wind_speed_kt`/`headwind_kt` columns + `read_operations`) and the worker path from Plans 1/4.

---

### Background facts (verified)

- `operations` already has `runway_id`, `runway_heading_deg`, `icao24`, `callsign`, `registration`, `timestamp`, and the (currently-NULL) `wind_from_deg`, `wind_speed_kt`, `headwind_kt` columns. `db.read_operations(conn, icao, start, end, types=None)` returns `SELECT *` rows ordered by timestamp ASC. T&G/low-approach ops carry a `runway_id`; circles do not.
- `weather.get_wind_summary(store, airport_icao, hours) -> dict` (async, Redis-cached). Shape: `{"current": {"wind_from_dir_degrees": int|None, "wind_speed_kt": float|None, ...} | None, "average": {...}|None, "error": ...}`. Never raises.
- `services.run_detectors_for_monitor(store, settings, conn, monitor)` already has: `airport` (db.Airport), `runways = db.runways_for_airport(conn, airport.icao)` (list of dicts with `runway_id`, `heading_deg`), `now = int(datetime.now(timezone.utc).timestamp())`, and `new_events` (the freshly-detected events). It ends with `if new_events: db.persist_events(...); deviation.store_deviations(...)`. It imports `from . import adsbdb, archive, db, deviation` (Plan 4 added `deviation`).
- FastAPI endpoint conventions: `@app.get(...)`, `settings: Annotated[Settings, Depends(settings_dep)]`, `with db_session(settings.database_path) as conn:`, return plain dict. Endpoint tests: `TestClient(app)` + `monkeypatch.setenv(...)` + `get_settings.cache_clear()` (see `backend/tests/test_patterns.py::api_client`).
- Frontend: `api.ts` has `getJson<T>` + `API_BASE`; `App.tsx` renders `<MapView/>` inside `<section className="map-panel">`; `.wind-overlay` CSS is the model for a map overlay badge; `getAirportFlow` will be added.

Backend tests go in **`backend/tests/test_flow.py`** (create in Task 2). Window/threshold defaults: `FLOW_WINDOW_S = 3600`, `min_ops = 3`, `min_window_s = 600`.

---

## Task 1: `runway_flow` + `runway_changes` tables and DB helpers

**Files:**
- Modify: `backend/app/db.py` (SCHEMA + helpers after `update_operation_deviation`)
- Test: `backend/tests/test_flow.py` (create)

- [ ] **Step 1: Write the failing test** — create `backend/tests/test_flow.py`

```python
from __future__ import annotations

from app import db, flow


def seeded_conn(path):
    conn = db.connect(str(path))
    conn.executescript(db.SCHEMA)
    db.seed_db(conn)
    conn.commit()
    return conn


def test_flow_tables_and_helpers(tmp_path):
    conn = seeded_conn(tmp_path / "t.sqlite3")
    for table in ("runway_flow", "runway_changes"):
        assert conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone() is not None

    assert db.current_flow(conn, "KBJC") is None
    db.open_flow(conn, "KBJC", "30R", 1000, 300, 8.0)
    cur = db.current_flow(conn, "KBJC")
    assert cur["active_runway_id"] == "30R" and cur["ended_at"] is None

    # opening a new flow closes the previous open row
    db.close_open_flow(conn, "KBJC", 2000)
    db.open_flow(conn, "KBJC", "12L", 2000, 120, 9.0)
    assert db.current_flow(conn, "KBJC")["active_runway_id"] == "12L"
    closed = conn.execute(
        "SELECT ended_at FROM runway_flow WHERE icao='KBJC' AND active_runway_id='30R'"
    ).fetchone()
    assert closed["ended_at"] == 2000

    db.insert_runway_change(conn, "KBJC", "30R", "12L", 2000,
                            {"icao24": "a4c1d8", "callsign": "N4052F", "registration": "N4052F", "id": "op9"},
                            120, 9.0, wind_favored_new=0)
    changes = db.recent_runway_changes(conn, "KBJC", 10)
    assert len(changes) == 1
    assert changes[0]["to_runway_id"] == "12L"
    assert changes[0]["cowboy_callsign"] == "N4052F"
    assert changes[0]["trigger_op_id"] == "op9"
    assert changes[0]["wind_favored_new"] == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/d/Code/FAA_circle_jerk/backend && /Users/d/Code/FAA_circle_jerk/.venv/bin/python -m pytest tests/test_flow.py::test_flow_tables_and_helpers -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.flow'` (the import) / missing tables.

- [ ] **Step 3a: Add tables to `SCHEMA`** in `backend/app/db.py` (before the closing `"""`, after the `runway_patterns` index)

```sql

CREATE TABLE IF NOT EXISTS runway_flow (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  icao TEXT NOT NULL,
  active_runway_id TEXT NOT NULL,
  established_at INTEGER NOT NULL,
  ended_at INTEGER,
  wind_from_deg INTEGER,
  wind_speed_kt REAL,
  op_count INTEGER NOT NULL DEFAULT 0,
  FOREIGN KEY (icao) REFERENCES airports(icao)
);
CREATE INDEX IF NOT EXISTS idx_runway_flow_icao ON runway_flow(icao, established_at DESC);

CREATE TABLE IF NOT EXISTS runway_changes (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  icao TEXT NOT NULL,
  from_runway_id TEXT,
  to_runway_id TEXT NOT NULL,
  changed_at INTEGER NOT NULL,
  cowboy_icao24 TEXT,
  cowboy_callsign TEXT,
  cowboy_registration TEXT,
  wind_from_deg INTEGER,
  wind_speed_kt REAL,
  wind_favored_new INTEGER,
  trigger_op_id TEXT,
  FOREIGN KEY (icao) REFERENCES airports(icao)
);
CREATE INDEX IF NOT EXISTS idx_runway_changes_icao ON runway_changes(icao, changed_at DESC);
```

- [ ] **Step 3b: Add helpers to `backend/app/db.py`** (after `update_operation_deviation`)

```python
def current_flow(conn: sqlite3.Connection, icao: str) -> dict | None:
    row = conn.execute(
        "SELECT * FROM runway_flow WHERE icao = ? AND ended_at IS NULL "
        "ORDER BY established_at DESC LIMIT 1",
        (icao.upper(),),
    ).fetchone()
    return dict(row) if row else None


def close_open_flow(conn: sqlite3.Connection, icao: str, ended_at: int) -> None:
    conn.execute(
        "UPDATE runway_flow SET ended_at = ? WHERE icao = ? AND ended_at IS NULL",
        (ended_at, icao.upper()),
    )


def open_flow(conn: sqlite3.Connection, icao: str, runway_id: str, established_at: int,
              wind_from_deg: int | None, wind_speed_kt: float | None) -> None:
    conn.execute(
        """
        INSERT INTO runway_flow (icao, active_runway_id, established_at, wind_from_deg, wind_speed_kt)
        VALUES (?, ?, ?, ?, ?)
        """,
        (icao.upper(), runway_id, established_at, wind_from_deg, wind_speed_kt),
    )


def insert_runway_change(conn: sqlite3.Connection, icao: str, from_runway_id: str | None,
                         to_runway_id: str, changed_at: int, cowboy: dict | None,
                         wind_from_deg: int | None, wind_speed_kt: float | None,
                         wind_favored_new: int) -> None:
    cowboy = cowboy or {}
    conn.execute(
        """
        INSERT INTO runway_changes
          (icao, from_runway_id, to_runway_id, changed_at, cowboy_icao24, cowboy_callsign,
           cowboy_registration, wind_from_deg, wind_speed_kt, wind_favored_new, trigger_op_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (icao.upper(), from_runway_id, to_runway_id, changed_at,
         cowboy.get("icao24"), cowboy.get("callsign"), cowboy.get("registration"),
         wind_from_deg, wind_speed_kt, wind_favored_new, cowboy.get("id")),
    )


def recent_runway_changes(conn: sqlite3.Connection, icao: str, limit: int = 20) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM runway_changes WHERE icao = ? ORDER BY changed_at DESC LIMIT ?",
        (icao.upper(), limit),
    ).fetchall()
    return [dict(row) for row in rows]
```

(`backend/app/flow.py` doesn't exist yet — the test imports it; that's Task 2. To make THIS task's test runnable, also create a stub `backend/app/flow.py` containing just `from __future__ import annotations` now, OR run only the table/helper assertions. Simplest: create the empty-ish `flow.py` stub in this task so the import resolves; Task 2 fills it in.)

Create `backend/app/flow.py` with:
```python
from __future__ import annotations
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/d/Code/FAA_circle_jerk/backend && /Users/d/Code/FAA_circle_jerk/.venv/bin/python -m pytest tests/test_flow.py::test_flow_tables_and_helpers -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/db.py backend/app/flow.py backend/tests/test_flow.py
git commit -m "feat: runway_flow + runway_changes tables and DB helpers"
```

---

## Task 2: `flow.py` pure functions (active end, cowboy, headwind)

**Files:**
- Modify: `backend/app/flow.py`
- Test: `backend/tests/test_flow.py`

- [ ] **Step 1: Append the failing test**

```python
def _op(ts, rid, icao24="a", oid=None):
    return {"timestamp": ts, "runway_id": rid, "icao24": icao24, "callsign": "N1",
            "registration": "N1", "id": oid or f"op{ts}"}


def test_trailing_run_and_established_end():
    ops = [_op(0, "12L"), _op(60, "12L"), _op(120, "30R"), _op(180, "30R"), _op(240, "30R")]
    rid, run_len, span = flow.trailing_run(ops)
    assert rid == "30R" and run_len == 3 and span == 120

    # 3 consecutive 30R establishes 30R over a previous 12L
    assert flow.established_end(ops, "12L") == "30R"
    # only 2 consecutive, short span → not yet established, keep previous
    assert flow.established_end([_op(0, "12L"), _op(60, "30R"), _op(120, "30R")], "12L") == "12L"
    # 2 ops but spanning >= 10 min → established by time
    assert flow.established_end([_op(0, "12L"), _op(60, "30R"), _op(700, "30R")], "12L") == "30R"
    # empty → keep previous
    assert flow.established_end([], "12L") == "12L"


def test_cowboy_for_end_is_earliest_of_trailing_run():
    ops = [_op(0, "12L"), _op(120, "30R", oid="first30"), _op(180, "30R"), _op(240, "30R", oid="last30")]
    cowboy = flow.cowboy_for_end(ops, "30R")
    assert cowboy["id"] == "first30"


def test_headwind_component():
    # wind from 300 onto runway heading 300 → full headwind (+)
    assert flow.headwind_component(300, 300, 10.0) == 10.0
    # opposite runway 120 → tailwind (negative)
    assert flow.headwind_component(120, 300, 10.0) == -10.0
    assert flow.headwind_component(None, 300, 10.0) is None
    assert flow.headwind_component(300, None, 10.0) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/d/Code/FAA_circle_jerk/backend && /Users/d/Code/FAA_circle_jerk/.venv/bin/python -m pytest tests/test_flow.py -k "trailing_run or cowboy_for_end or headwind" -v`
Expected: FAIL — those functions don't exist.

- [ ] **Step 3: Implement in `backend/app/flow.py`**

```python
from __future__ import annotations

import math

FLOW_WINDOW_S = 3600
MIN_OPS = 3
MIN_WINDOW_S = 600


def trailing_run(ops: list[dict]) -> tuple[str | None, int, int]:
    """Length and time-span of the trailing run of same-runway ops (time-ordered asc)."""
    if not ops:
        return (None, 0, 0)
    last = ops[-1]["runway_id"]
    run = []
    for op in reversed(ops):
        if op["runway_id"] == last:
            run.append(op)
        else:
            break
    span = run[0]["timestamp"] - run[-1]["timestamp"]  # latest - earliest
    return (last, len(run), span)


def established_end(ops: list[dict], previous_end: str | None,
                    min_ops: int = MIN_OPS, min_window_s: int = MIN_WINDOW_S) -> str | None:
    """The currently-established active runway end. A new end only takes over once
    its trailing run reaches min_ops ops OR min_window_s of agreement."""
    rid, run_len, span = trailing_run(ops)
    if rid is None:
        return previous_end
    if rid == previous_end:
        return previous_end
    if run_len >= min_ops or span >= min_window_s:
        return rid
    return previous_end


def cowboy_for_end(ops: list[dict], end: str) -> dict | None:
    """The earliest op of the trailing run for `end` — the aircraft that started it."""
    run = []
    for op in reversed(ops):
        if op["runway_id"] == end:
            run.append(op)
        else:
            break
    return run[-1] if run else None


def headwind_component(heading_deg, wind_from_deg, wind_speed_kt) -> float | None:
    """Signed headwind along the runway heading. Positive = into the wind."""
    if heading_deg is None or wind_from_deg is None or wind_speed_kt is None:
        return None
    return round(float(wind_speed_kt) * math.cos(math.radians(float(heading_deg) - float(wind_from_deg))), 2)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/d/Code/FAA_circle_jerk/backend && /Users/d/Code/FAA_circle_jerk/.venv/bin/python -m pytest tests/test_flow.py -k "trailing_run or cowboy_for_end or headwind" -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/flow.py backend/tests/test_flow.py
git commit -m "feat: flow pure functions (active end, cowboy, headwind)"
```

---

## Task 3: `flow.process` orchestration

**Files:**
- Modify: `backend/app/flow.py` (+ `db.update_operation_wind`)
- Test: `backend/tests/test_flow.py`

- [ ] **Step 1: Append the failing test**

```python
def _wind(from_deg, speed):
    return {"current": {"wind_from_dir_degrees": from_deg, "wind_speed_kt": speed}, "average": None, "error": None}


def test_process_establishes_and_flips_with_cowboy(tmp_path):
    conn = seeded_conn(tmp_path / "t.sqlite3")
    runways = db.runways_for_airport(conn, "KBJC")  # 12L=120°, 30R=300°, ...

    def persist(ts, rid, icao24, oid):
        db.upsert_operation(conn, db.operation_from_event({
            "id": oid, "type": "touch_and_go", "icao24": icao24, "callsign": icao24.upper(),
            "timestamp": ts, "airport_icao": "KBJC", "runway_id": rid, "runway_heading_deg": 300,
        }))

    # Establish 30R with 3 ops.
    for i, ts in enumerate((100, 160, 220)):
        persist(ts, "30R", "a1", f"e{i}")
    flow.process(conn, "KBJC", runways, [], _wind(300, 10.0), now=240)
    conn.commit()
    assert db.current_flow(conn, "KBJC")["active_runway_id"] == "30R"
    assert db.recent_runway_changes(conn, "KBJC") == []  # first establishment is not a "change"

    # Now 3 ops on 12L flip the flow — the first one is the cowboy.
    persist(400, "12L", "cowboy1", "c0")
    persist(460, "12L", "a2", "c1")
    persist(520, "12L", "a3", "c2")
    flow.process(conn, "KBJC", runways, [], _wind(300, 10.0), now=540)
    conn.commit()
    assert db.current_flow(conn, "KBJC")["active_runway_id"] == "12L"
    changes = db.recent_runway_changes(conn, "KBJC")
    assert len(changes) == 1
    assert changes[0]["from_runway_id"] == "30R" and changes[0]["to_runway_id"] == "12L"
    assert changes[0]["cowboy_icao24"] == "cowboy1"
    # wind still from 300 → 12L (120°) is a tailwind → not wind-favored
    assert changes[0]["wind_favored_new"] == 0


def test_process_tags_op_headwind(tmp_path):
    conn = seeded_conn(tmp_path / "t.sqlite3")
    runways = db.runways_for_airport(conn, "KBJC")
    db.upsert_operation(conn, db.operation_from_event({
        "id": "w1", "type": "touch_and_go", "icao24": "a", "callsign": "N1",
        "timestamp": 100, "airport_icao": "KBJC", "runway_id": "30R",
    }))
    event = {"id": "w1", "type": "touch_and_go", "icao24": "a", "airport_icao": "KBJC", "runway_id": "30R"}
    flow.process(conn, "KBJC", runways, [event], _wind(300, 10.0), now=120)
    conn.commit()
    row = db.read_operations(conn, "KBJC", 0, 10000)[0]
    assert row["wind_from_deg"] == 300
    assert row["headwind_kt"] == 10.0  # 30R heading 300 into wind from 300
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/d/Code/FAA_circle_jerk/backend && /Users/d/Code/FAA_circle_jerk/.venv/bin/python -m pytest tests/test_flow.py -k process -v`
Expected: FAIL — `flow.process` / `db.update_operation_wind` not defined.

- [ ] **Step 3a: Add `db.update_operation_wind`** to `backend/app/db.py` (after `update_operation_deviation`)

```python
def update_operation_wind(conn: sqlite3.Connection, op_id: str,
                          wind_from_deg: int | None, wind_speed_kt: float | None,
                          headwind_kt: float | None) -> None:
    conn.execute(
        "UPDATE operations SET wind_from_deg = ?, wind_speed_kt = ?, headwind_kt = ? WHERE id = ?",
        (wind_from_deg, wind_speed_kt, headwind_kt, op_id),
    )
```

- [ ] **Step 3b: Implement `flow.process`** in `backend/app/flow.py` (import db lazily to avoid cycles)

```python
from . import db as _db


def process(conn, airport_icao: str, runways: list[dict], events: list[dict],
            wind: dict | None, now: int) -> None:
    """Tag new runway ops with headwind and update the active-runway flow / cowboy log."""
    headings = {r["runway_id"]: r["heading_deg"] for r in runways}
    current = (wind or {}).get("current") or {}
    wind_from = current.get("wind_from_dir_degrees")
    wind_speed = current.get("wind_speed_kt")

    # 1. Tag each new runway-tagged op with its headwind component.
    for event in events:
        rid = event.get("runway_id")
        if not rid or not event.get("id"):
            continue
        hk = headwind_component(headings.get(rid), wind_from, wind_speed)
        _db.update_operation_wind(conn, event["id"], wind_from, wind_speed, hk)

    # 2. Determine the established active end from recent runway-tagged ops.
    rows = _db.read_operations(conn, airport_icao, now - FLOW_WINDOW_S, now,
                               types=["touch_and_go", "low_approach"])
    ops = [
        {"timestamp": r["timestamp"], "runway_id": r["runway_id"], "icao24": r["icao24"],
         "callsign": r["callsign"], "registration": r["registration"], "id": r["id"]}
        for r in rows if r["runway_id"]
    ]
    prev = _db.current_flow(conn, airport_icao)
    prev_end = prev["active_runway_id"] if prev else None
    new_end = established_end(ops, prev_end)
    if new_end is None or new_end == prev_end:
        return

    cowboy = cowboy_for_end(ops, new_end)
    hk_new = headwind_component(headings.get(new_end), wind_from, wind_speed)
    wind_favored = 1 if (hk_new is not None and hk_new > 0) else 0

    _db.close_open_flow(conn, airport_icao, now)
    _db.open_flow(conn, airport_icao, new_end, now, wind_from, wind_speed)
    if prev_end is not None:  # a flip (not the first-ever establishment) → log the cowboy
        _db.insert_runway_change(conn, airport_icao, prev_end, new_end, now,
                                 cowboy, wind_from, wind_speed, wind_favored)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/d/Code/FAA_circle_jerk/backend && /Users/d/Code/FAA_circle_jerk/.venv/bin/python -m pytest tests/test_flow.py -k process -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add backend/app/flow.py backend/app/db.py backend/tests/test_flow.py
git commit -m "feat: flow.process — active-end tracking, cowboy attribution, op headwind"
```

---

## Task 4: Wire flow into the worker

**Files:**
- Modify: `backend/app/services.py` (`run_detectors_for_monitor` + import)
- Test: `backend/tests/test_flow.py`

- [ ] **Step 1: Append the failing test (wiring contract)**

```python
def test_services_calls_flow_process():
    import inspect
    from app import services
    src = inspect.getsource(services.run_detectors_for_monitor)
    assert "flow.process" in src
    assert "get_wind_summary" in src
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/d/Code/FAA_circle_jerk/backend && /Users/d/Code/FAA_circle_jerk/.venv/bin/python -m pytest tests/test_flow.py::test_services_calls_flow_process -v`
Expected: FAIL — `flow.process` not referenced.

- [ ] **Step 3: Wire it into `backend/app/services.py`**

Add to the imports (extend the existing `from . import ...` line that already has `deviation`):

```python
from . import adsbdb, archive, db, deviation, flow, weather
```

(Match the existing import line — add `flow, weather` to whatever is already imported; do not duplicate.)

In `run_detectors_for_monitor`, replace the persistence tail:

```python
    if new_events:
        db.persist_events(conn, new_events)
        # Quantify how far each circling aircraft strays from the drawn VNAP
        # pattern (no-op when this airport has no pattern yet).
        deviation.store_deviations(conn, airport.icao, new_events, tracks_by_icao24)
    return written
```

with:

```python
    if new_events:
        db.persist_events(conn, new_events)
        # Quantify how far each circling aircraft strays from the drawn VNAP
        # pattern (no-op when this airport has no pattern yet).
        deviation.store_deviations(conn, airport.icao, new_events, tracks_by_icao24)
        # Tag ops with headwind and update active-runway flow + cowboy log.
        try:
            wind = await weather.get_wind_summary(store, airport.icao, 1)
        except Exception:  # noqa: BLE001 — never let weather break detection
            wind = {}
        flow.process(conn, airport.icao, runways, new_events, wind, now)
    return written
```

- [ ] **Step 4: Run the targeted test + full suite**

Run: `cd /Users/d/Code/FAA_circle_jerk/backend && /Users/d/Code/FAA_circle_jerk/.venv/bin/python -m pytest tests/test_flow.py::test_services_calls_flow_process tests/ -q`
Expected: PASS — and the full suite stays green (existing tests with no ops produce no flow rows; `get_wind_summary` is awaited against the test's MemoryStore and returns gracefully).

- [ ] **Step 5: Commit**

```bash
git add backend/app/services.py backend/tests/test_flow.py
git commit -m "feat: run flow + wind tagging in the worker detection path"
```

---

## Task 5: `GET /airports/{icao}/flow` endpoint

**Files:**
- Modify: `backend/app/main.py` (one GET route)
- Test: `backend/tests/test_flow.py`

- [ ] **Step 1: Append the failing test**

```python
def test_flow_endpoint(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from app.main import app
    from app.settings import get_settings

    monkeypatch.setenv("CIRCLEJERK_DATABASE_PATH", str(tmp_path / "circlejerk.sqlite3"))
    monkeypatch.setenv("CIRCLEJERK_REDIS_URL", "memory://")
    monkeypatch.setenv("CIRCLEJERK_ENVIRONMENT", "test")
    get_settings.cache_clear()

    with TestClient(app) as client:
        empty = client.get("/airports/KBJC/flow")
        assert empty.status_code == 200
        assert empty.json()["active"] is None
        assert empty.json()["recent_changes"] == []

    # seed a flow + change directly, then read it back
    conn = db.connect(str(tmp_path / "circlejerk.sqlite3"))
    db.open_flow(conn, "KBJC", "30R", 1000, 300, 8.0)
    db.insert_runway_change(conn, "KBJC", "12L", "30R", 1000,
                            {"icao24": "a", "callsign": "N1", "registration": "N1", "id": "op1"}, 300, 8.0, 1)
    conn.commit(); conn.close()

    with TestClient(app) as client:
        resp = client.get("/airports/KBJC/flow")
        data = resp.json()
        assert data["active"]["active_runway_id"] == "30R"
        assert data["recent_changes"][0]["to_runway_id"] == "30R"
        assert data["recent_changes"][0]["wind_favored_new"] == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/d/Code/FAA_circle_jerk/backend && /Users/d/Code/FAA_circle_jerk/.venv/bin/python -m pytest tests/test_flow.py::test_flow_endpoint -v`
Expected: FAIL — route returns 404.

- [ ] **Step 3: Add the route to `backend/app/main.py`** (near the other `/airports/{icao}/...` routes)

```python
@app.get("/airports/{icao}/flow")
async def get_airport_flow(
    icao: str,
    settings: Annotated[Settings, Depends(settings_dep)],
):
    with db_session(settings.database_path) as conn:
        active = db.current_flow(conn, icao)
        changes = db.recent_runway_changes(conn, icao, 20)
    return {"airport_icao": icao.upper(), "active": active, "recent_changes": changes}
```

- [ ] **Step 4: Run test + full suite**

Run: `cd /Users/d/Code/FAA_circle_jerk/backend && /Users/d/Code/FAA_circle_jerk/.venv/bin/python -m pytest tests/test_flow.py::test_flow_endpoint tests/ -q`
Expected: PASS, full suite green.

- [ ] **Step 5: Commit**

```bash
git add backend/app/main.py backend/tests/test_flow.py
git commit -m "feat: GET /airports/{icao}/flow endpoint"
```

---

## Task 6: Frontend API client for flow

**Files:**
- Modify: `frontend/src/lib/api.ts` (types + `getAirportFlow`)
- Create: `frontend/src/lib/flowApi.test.ts`

- [ ] **Step 1: Write the failing test** — `frontend/src/lib/flowApi.test.ts`

```ts
import { afterEach, describe, expect, it, vi } from "vitest";
import { getAirportFlow } from "./api";

afterEach(() => vi.unstubAllGlobals());

describe("getAirportFlow", () => {
  it("requests the flow path", async () => {
    const fn = vi.fn(async () => ({ ok: true, json: async () => ({ airport_icao: "KBJC", active: null, recent_changes: [] }) } as Response));
    vi.stubGlobal("fetch", fn);
    const r = await getAirportFlow("KBJC");
    expect(fn.mock.calls[0][0]).toContain("/api/airports/KBJC/flow");
    expect(r.recent_changes).toEqual([]);
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/d/Code/FAA_circle_jerk/frontend && npx vitest run src/lib/flowApi.test.ts`
Expected: FAIL — `getAirportFlow` not exported.

- [ ] **Step 3: Add to `frontend/src/lib/api.ts`**

```ts
export interface RunwayFlow {
  id: number;
  icao: string;
  active_runway_id: string;
  established_at: number;
  ended_at: number | null;
  wind_from_deg: number | null;
  wind_speed_kt: number | null;
  op_count: number;
}

export interface RunwayChange {
  id: number;
  from_runway_id: string | null;
  to_runway_id: string;
  changed_at: number;
  cowboy_icao24: string | null;
  cowboy_callsign: string | null;
  cowboy_registration: string | null;
  wind_from_deg: number | null;
  wind_speed_kt: number | null;
  wind_favored_new: number | null;
  trigger_op_id: string | null;
}

export interface AirportFlowResponse {
  airport_icao: string;
  active: RunwayFlow | null;
  recent_changes: RunwayChange[];
}

export function getAirportFlow(icao: string) {
  return getJson<AirportFlowResponse>(`/airports/${encodeURIComponent(icao)}/flow`);
}
```

(Reuse the existing `enc`/`encodeURIComponent` style already in the file.)

- [ ] **Step 4: Run test + typecheck**

Run: `cd /Users/d/Code/FAA_circle_jerk/frontend && npx vitest run src/lib/flowApi.test.ts && npx tsc --noEmit`
Expected: PASS + clean.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/lib/api.ts frontend/src/lib/flowApi.test.ts
git commit -m "feat: getAirportFlow API client + types"
```

---

## Task 7: `FlowBadge` component + App wiring

**Files:**
- Create: `frontend/src/components/FlowBadge.tsx`
- Modify: `frontend/src/App.tsx`, `frontend/src/styles.css`

Verification: `npx tsc --noEmit && npm run build` (no canvas test harness; do NOT start a dev server).

- [ ] **Step 1: Create `frontend/src/components/FlowBadge.tsx`**

```tsx
import { useEffect, useState } from "react";
import { getAirportFlow, type AirportFlowResponse } from "../lib/api";

function minutesAgo(ts: number): string {
  const mins = Math.max(0, Math.round((Date.now() / 1000 - ts) / 60));
  return mins < 1 ? "just now" : `${mins}m ago`;
}

export default function FlowBadge({ airportIcao }: { airportIcao?: string | null }) {
  const [flow, setFlow] = useState<AirportFlowResponse | null>(null);

  useEffect(() => {
    if (!airportIcao) {
      setFlow(null);
      return;
    }
    let active = true;
    const load = () => getAirportFlow(airportIcao).then((f) => { if (active) setFlow(f); }).catch(() => undefined);
    load();
    const timer = setInterval(load, 60_000);
    return () => { active = false; clearInterval(timer); };
  }, [airportIcao]);

  if (!flow?.active) return null;
  const change = flow.recent_changes[0];
  return (
    <div className="flow-badge" title="Active runway in use">
      <span className="flow-badge-rwy">RWY {flow.active.active_runway_id}</span>
      {change && (
        <span className="flow-badge-change">
          changed {minutesAgo(change.changed_at)} by{" "}
          {change.cowboy_callsign ?? change.cowboy_icao24 ?? "unknown"} 🤠
        </span>
      )}
    </div>
  );
}
```

- [ ] **Step 2: Wire into `frontend/src/App.tsx`**

Add the import:
```ts
import FlowBadge from "./components/FlowBadge";
```

Render it inside `<section className="map-panel">` (near the other map overlays, e.g. just after `<MapView .../>`):
```tsx
  <FlowBadge airportIcao={airport?.icao} />
```

- [ ] **Step 3: Add styles to `frontend/src/styles.css`**

```css
.flow-badge {
  position: absolute;
  top: 16px;
  left: 16px;
  z-index: 6;
  display: flex;
  gap: 8px;
  align-items: baseline;
  padding: 8px 12px;
  background: rgba(255, 255, 255, 0.94);
  border: 1px solid var(--line);
  border-radius: 8px;
  box-shadow: 0 1px 3px rgba(15, 23, 42, 0.08);
  font-size: 12px;
  color: var(--text);
  pointer-events: none;
}
.flow-badge-rwy { font-weight: 800; color: var(--ink); }
.flow-badge-change { color: var(--muted); }
```

(If `.flow-badge`'s top-left position collides with the existing `.wind-overlay` or `.backfill` banner, nudge `top`/`left` to sit beside them — keep it out of the way of the runway selector.)

- [ ] **Step 4: Typecheck + build**

Run: `cd /Users/d/Code/FAA_circle_jerk/frontend && npx tsc --noEmit && npm run build`
Expected: clean typecheck, successful build.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/FlowBadge.tsx frontend/src/App.tsx frontend/src/styles.css
git commit -m "feat: active-runway + cowboy badge on the map"
```

---

## Self-review

**Spec coverage:** Feature 3 is covered — active runway end established by ≥3 consecutive same-end ops OR ~10 min of agreement (`established_end`, Task 2); flip detection + cowboy = earliest op of the new sustained run, always tagged regardless of wind (`flow.process`, Task 3); wind snapshot at the change + `wind_favored_new` for context (Task 3); per-op headwind component for the "time into headwind" KPI (Task 3, `update_operation_wind`); worker integration (Task 4); the `runway_flow`/`runway_changes` tables (Task 1); the `/flow` endpoint (Task 5) and the map badge (Tasks 6-7).

**Placeholder scan:** No placeholders; every backend step is TDD with exact commands; frontend integration is build-gated.

**Type consistency:** `flow.process(conn, icao, runways, events, wind, now)` reads `wind["current"]["wind_from_dir_degrees"]`/`["wind_speed_kt"]` (matching `weather.get_wind_summary`), maps runway `heading_deg`, and calls `db.update_operation_wind` + the flow helpers. `established_end`/`cowboy_for_end`/`trailing_run` all operate on `{timestamp, runway_id, icao24, callsign, registration, id}` dicts built from `read_operations` rows. The endpoint returns `current_flow` + `recent_runway_changes` rows, typed on the frontend as `RunwayFlow`/`RunwayChange`. Cowboy attribution stores `trigger_op_id` from the op's `id`.

**Deferred / notes:** "Into headwind vs not" aggregate stats are computed on the KPIs page (Plan 6) from the per-op `headwind_kt` this plan writes. The op-count column on `runway_flow` is initialized to 0 and not incrementally maintained (the KPIs page derives counts from `operations` directly) — left as 0 intentionally. Wind is snapshotted from the current METAR (close to op time); per-op historical METAR lookup is out of scope. Circles (no `runway_id`) don't drive flow; only T&G/low-approach ops do, which is the correct signal for runway-in-use.
