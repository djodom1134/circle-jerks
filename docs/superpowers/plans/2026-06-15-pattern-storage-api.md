# Pattern Storage + API Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Store a versioned, per-runway-end VNAP pattern (a Catmull-Rom spline of control points) in SQLite, and expose REST endpoints to list, fetch, generate-a-template-for, save (open editing with guardrails), view history of, and revert a pattern.

**Architecture:** A new append-only `runway_patterns` table holds every version of a pattern; the latest row per `(icao, runway_id)` has `is_current=1`. A pure-geometry module `patterns.py` generates a standard rectangular circuit from a runway's threshold + heading + length, and validates submitted geometry (≤50 points, each within 10 nm of the airport). Endpoints follow the existing FastAPI conventions (`Depends(settings_dep)` + `with db_session(...) as conn:` + plain-dict returns). Open editing is protected by a DB-based per-editor rate limit and an admin `locked` flag.

**Tech Stack:** Python 3, SQLite, FastAPI (`fastapi.testclient.TestClient` for endpoint tests), pytest. Tests run from `backend/` via `/Users/d/Code/FAA_circle_jerk/.venv/bin/python -m pytest`.

This is Plan 2 of 6. It builds on Plan 1 (the `operations` log) but is independent of it. It produces working, testable software: drawable/persisted patterns with a full CRUD+history API.

---

### Background facts (verified conventions — do not rediscover)

- **Endpoints** live in `backend/app/main.py`. Pattern: `@app.get(...)` / `@app.post(...)` / `@app.put(...)`, with `settings: Annotated[Settings, Depends(settings_dep)]` and a per-request `with db_session(settings.database_path) as conn:`. Handlers return plain dicts (no `response_model`). For the caller's IP use the existing `client_ip(request)` helper (needs `request: Request` param). `visitor_id` comes from the request body.
- **Pydantic** request models are plain `class X(BaseModel)` with `Field(...)` validators, declared as a handler param (FastAPI parses the JSON body).
- **No rate limiting exists** anywhere — this plan adds a simple DB-based one for pattern writes.
- **`backend/app/geo.py`** has `EARTH_RADIUS_NM = 3440.065`, `@dataclass(frozen=True) class Point: lat; lon`, `distance_nm(a,b)`, `bearing_deg(a,b)`. It does NOT have a "project a point by bearing+distance" function — Task 1 adds one. `geo.py` already `import math`.
- **`backend/app/db.py`** has `runways_for_airport(conn, icao) -> list[dict]` and `get_airport(conn, icao) -> Airport | None` (Airport has `.lat`, `.lon`). There is NO single-runway getter — Task 2 adds `get_runway`. Runway columns: `icao, runway_id, lat_threshold, lon_threshold, heading_deg, length_ft`. The schema is the `SCHEMA` string; add `CREATE TABLE IF NOT EXISTS` blocks to it (no migration needed). Operation helpers from Plan 1 live after `row_to_airport`.
- **Tests:** No `conftest.py`. Unit/db tests use `db.connect(str(path)); conn.executescript(db.SCHEMA); db.seed_db(conn); conn.commit()` with `tmp_path`. API tests use:
  ```python
  monkeypatch.setenv("CIRCLEJERK_DATABASE_PATH", str(tmp_path / "circlejerk.sqlite3"))
  monkeypatch.setenv("CIRCLEJERK_REDIS_URL", "memory://")
  monkeypatch.setenv("CIRCLEJERK_ENVIRONMENT", "test")
  get_settings.cache_clear()
  with TestClient(app) as client: ...
  ```
  The `lifespan` runs `db.init_db` (SCHEMA + seed) against the temp DB, so KBJC and its runways are seeded.
- Seeded runway for tests: `KBJC` `12L` has `heading_deg=120`, `lat_threshold=39.9215`, `lon_threshold=-105.1321`, `length_ft=9000`. Airport KBJC center is `(39.9088, -105.1172)`.

All new tests go in **`backend/tests/test_patterns.py`** (create in Task 1). Put this shared header at the top when you create the file:

```python
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from app import db, patterns
from app.geo import Point, distance_nm, destination_point
from app.main import app
from app.settings import get_settings


def seeded_conn(path):
    conn = db.connect(str(path))
    conn.executescript(db.SCHEMA)
    db.seed_db(conn)
    conn.commit()
    return conn


def api_client(tmp_path, monkeypatch):
    monkeypatch.setenv("CIRCLEJERK_DATABASE_PATH", str(tmp_path / "circlejerk.sqlite3"))
    monkeypatch.setenv("CIRCLEJERK_REDIS_URL", "memory://")
    monkeypatch.setenv("CIRCLEJERK_ENVIRONMENT", "test")
    get_settings.cache_clear()
    return TestClient(app)
```

(The `import patterns` / `import destination_point` lines will fail until Tasks 1 and 3 create them — that's expected for the early tests; write the header now and the imports resolve as you go. If you prefer, add the `patterns` import in Task 3 — but it is simplest to write the full header once and let Task 1's test be the first to run after Task 1 + Task 3 exist. To keep each task runnable in isolation, run the specific test named in each task with `-v`.)

---

## Task 1: `destination_point` geometry helper

**Files:**
- Modify: `backend/app/geo.py` (add after the `bearing_deg` function)
- Test: `backend/tests/test_patterns.py` (create with the shared header above, then append)

- [ ] **Step 1: Create the test file with the shared header, then append this test**

```python
def test_destination_point_projects_by_bearing_and_distance():
    start = Point(40.0, -105.0)
    north = destination_point(start, 0.0, 60.0)      # 60 nm due north ≈ +1° lat
    assert north.lat > start.lat
    assert abs(distance_nm(start, north) - 60.0) < 0.5
    assert abs(north.lon - start.lon) < 0.05         # due north → longitude ~unchanged

    east = destination_point(start, 90.0, 30.0)
    assert east.lon > start.lon
    assert abs(distance_nm(start, east) - 30.0) < 0.5
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/d/Code/FAA_circle_jerk/backend && /Users/d/Code/FAA_circle_jerk/.venv/bin/python -m pytest tests/test_patterns.py::test_destination_point_projects_by_bearing_and_distance -v`
Expected: FAIL — `ImportError: cannot import name 'destination_point'` (and `patterns` import will also be unresolved; that's fine for now — comment out the `from app import db, patterns` line's `patterns` and the `import patterns` is only needed from Task 3. To avoid churn, temporarily import only `from app import db` in the header and add `patterns` in Task 3.)

NOTE: To keep imports clean, in the header use `from app import db` for now; you will change it to `from app import db, patterns` in Task 3 when `patterns.py` exists.

- [ ] **Step 3: Implement `destination_point` in `backend/app/geo.py`**

Add immediately after the `bearing_deg` function:

```python
def destination_point(origin: Point, bearing_deg: float, distance_nm: float) -> Point:
    """Return the point reached by travelling `distance_nm` from `origin` along
    a constant `bearing_deg` (great-circle). Inverse of bearing_deg/distance_nm."""
    angular = distance_nm / EARTH_RADIUS_NM
    bearing = math.radians(bearing_deg)
    lat1 = math.radians(origin.lat)
    lon1 = math.radians(origin.lon)
    lat2 = math.asin(
        math.sin(lat1) * math.cos(angular)
        + math.cos(lat1) * math.sin(angular) * math.cos(bearing)
    )
    lon2 = lon1 + math.atan2(
        math.sin(bearing) * math.sin(angular) * math.cos(lat1),
        math.cos(angular) - math.sin(lat1) * math.sin(lat2),
    )
    return Point(math.degrees(lat2), math.degrees(lon2))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/d/Code/FAA_circle_jerk/backend && /Users/d/Code/FAA_circle_jerk/.venv/bin/python -m pytest tests/test_patterns.py::test_destination_point_projects_by_bearing_and_distance -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/geo.py backend/tests/test_patterns.py
git commit -m "feat: destination_point geometry helper"
```

---

## Task 2: `runway_patterns` table + `get_runway`

**Files:**
- Modify: `backend/app/db.py` (`SCHEMA` string; add `get_runway` near `runways_for_airport`)
- Test: `backend/tests/test_patterns.py`

- [ ] **Step 1: Append the failing test**

```python
def test_runway_patterns_table_and_get_runway(tmp_path):
    conn = seeded_conn(tmp_path / "t.sqlite3")
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(runway_patterns)").fetchall()}
    assert {"id", "icao", "runway_id", "geometry_json", "version",
            "is_current", "locked", "editor_visitor_id", "change_note", "created_at"} <= cols

    rwy = db.get_runway(conn, "KBJC", "12L")
    assert rwy is not None
    assert rwy["heading_deg"] == 120
    assert rwy["length_ft"] == 9000
    assert db.get_runway(conn, "KBJC", "ZZ") is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/d/Code/FAA_circle_jerk/backend && /Users/d/Code/FAA_circle_jerk/.venv/bin/python -m pytest tests/test_patterns.py::test_runway_patterns_table_and_get_runway -v`
Expected: FAIL — `PRAGMA table_info(runway_patterns)` returns nothing (table missing).

- [ ] **Step 3: Add the table and getter**

In `backend/app/db.py`, add to the `SCHEMA` string (immediately before the closing `"""`, after the `operations` indexes from Plan 1):

```sql

CREATE TABLE IF NOT EXISTS runway_patterns (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  icao TEXT NOT NULL,
  runway_id TEXT NOT NULL,
  geometry_json TEXT NOT NULL,
  name TEXT,
  version INTEGER NOT NULL,
  is_current INTEGER NOT NULL DEFAULT 1,
  locked INTEGER NOT NULL DEFAULT 0,
  editor_visitor_id TEXT,
  editor_ip TEXT,
  change_note TEXT,
  created_at INTEGER NOT NULL DEFAULT (CAST(strftime('%s','now') AS INTEGER)),
  FOREIGN KEY (icao, runway_id) REFERENCES runways(icao, runway_id)
);
CREATE INDEX IF NOT EXISTS idx_runway_patterns_current ON runway_patterns(icao, runway_id, is_current);
```

Then add this function next to `runways_for_airport`:

```python
def get_runway(conn: sqlite3.Connection, icao: str, runway_id: str) -> dict | None:
    row = conn.execute(
        "SELECT * FROM runways WHERE icao = ? AND runway_id = ?",
        (icao.upper(), runway_id),
    ).fetchone()
    return dict(row) if row else None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/d/Code/FAA_circle_jerk/backend && /Users/d/Code/FAA_circle_jerk/.venv/bin/python -m pytest tests/test_patterns.py::test_runway_patterns_table_and_get_runway -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/db.py backend/tests/test_patterns.py
git commit -m "feat: runway_patterns table and get_runway"
```

---

## Task 3: `patterns.py` — template generator + validator

**Files:**
- Create: `backend/app/patterns.py`
- Test: `backend/tests/test_patterns.py` (and update the header import)

- [ ] **Step 1: Update the test header and append the failing tests**

Change the header import line `from app import db` to `from app import db, patterns`. Then append:

```python
def test_generate_template_pattern_within_bounds(tmp_path):
    conn = seeded_conn(tmp_path / "t.sqlite3")
    runway = db.get_runway(conn, "KBJC", "12L")
    geom = patterns.generate_template_pattern(runway, side="left")
    assert geom["closed"] is True
    assert geom["spline"] == "catmull-rom"
    assert len(geom["points"]) == 5
    center = Point(39.9088, -105.1172)
    assert all(distance_nm(Point(p["lat"], p["lon"]), center) < 10 for p in geom["points"])
    # first control point is the runway threshold
    assert abs(geom["points"][0]["lat"] - runway["lat_threshold"]) < 1e-9
    assert abs(geom["points"][0]["lon"] - runway["lon_threshold"]) < 1e-9


def test_generate_template_rejects_bad_side(tmp_path):
    conn = seeded_conn(tmp_path / "t.sqlite3")
    runway = db.get_runway(conn, "KBJC", "12L")
    with pytest.raises(ValueError):
        patterns.generate_template_pattern(runway, side="sideways")


def test_validate_pattern_geometry():
    class _AP:
        lat = 39.9088
        lon = -105.1172
    ap = _AP()
    assert patterns.validate_pattern_geometry([{"lat": 39.91, "lon": -105.12}], ap) is None
    assert patterns.validate_pattern_geometry([], ap) is not None
    too_far = [{"lat": 41.5, "lon": -105.12}]  # ~90 nm north
    assert patterns.validate_pattern_geometry(too_far, ap) is not None
    too_many = [{"lat": 39.91, "lon": -105.12}] * 51
    assert patterns.validate_pattern_geometry(too_many, ap) is not None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/d/Code/FAA_circle_jerk/backend && /Users/d/Code/FAA_circle_jerk/.venv/bin/python -m pytest tests/test_patterns.py -k "template or validate_pattern" -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.patterns'`.

- [ ] **Step 3: Create `backend/app/patterns.py`**

```python
from __future__ import annotations

from .geo import Point, destination_point, distance_nm

PATTERN_SPLINE = "catmull-rom"

# Standard traffic-circuit geometry (editable starting shape, not exact TPP).
DEFAULT_PATTERN_WIDTH_NM = 0.7     # downwind offset from the runway centerline
DEFAULT_FINAL_NM = 1.0             # final-approach length beyond the threshold
FEET_PER_NM = 6076.12

MAX_PATTERN_POINTS = 50
MAX_PATTERN_RADIUS_NM = 10.0


def generate_template_pattern(runway: dict, side: str = "left") -> dict:
    """Build a standard rectangular circuit for one runway end as a closed
    control-point spline. `side` is the traffic direction ('left' or 'right')."""
    if side not in ("left", "right"):
        raise ValueError("side must be 'left' or 'right'")
    heading = float(runway["heading_deg"])
    length_nm = max(0.3, float(runway["length_ft"]) / FEET_PER_NM)
    width = DEFAULT_PATTERN_WIDTH_NM
    sign = -1 if side == "left" else 1
    side_bearing = (heading + sign * 90) % 360
    threshold = Point(float(runway["lat_threshold"]), float(runway["lon_threshold"]))

    upwind = destination_point(threshold, heading, length_nm)
    crosswind = destination_point(upwind, side_bearing, width)
    downwind_end = destination_point(crosswind, (heading + 180) % 360, length_nm + DEFAULT_FINAL_NM)
    final_pt = destination_point(downwind_end, (side_bearing + 180) % 360, width)

    points = [
        {"lat": threshold.lat, "lon": threshold.lon},
        {"lat": upwind.lat, "lon": upwind.lon},
        {"lat": crosswind.lat, "lon": crosswind.lon},
        {"lat": downwind_end.lat, "lon": downwind_end.lon},
        {"lat": final_pt.lat, "lon": final_pt.lon},
    ]
    return {"points": points, "closed": True, "spline": PATTERN_SPLINE}


def validate_pattern_geometry(
    points: list[dict],
    airport,
    max_points: int = MAX_PATTERN_POINTS,
    max_nm: float = MAX_PATTERN_RADIUS_NM,
) -> str | None:
    """Return an error message if the geometry is invalid, else None.
    `airport` must expose `.lat` and `.lon`."""
    if not points:
        return "pattern must have at least one point"
    if len(points) > max_points:
        return f"pattern has too many points (max {max_points})"
    center = Point(airport.lat, airport.lon)
    for index, point in enumerate(points):
        if distance_nm(Point(point["lat"], point["lon"]), center) > max_nm:
            return f"point {index} is more than {max_nm} nm from the airport"
    return None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/d/Code/FAA_circle_jerk/backend && /Users/d/Code/FAA_circle_jerk/.venv/bin/python -m pytest tests/test_patterns.py -k "template or validate_pattern" -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add backend/app/patterns.py backend/tests/test_patterns.py
git commit -m "feat: pattern template generator and geometry validation"
```

---

## Task 4: Pattern persistence — save / get-current / list-for-airport

**Files:**
- Modify: `backend/app/db.py` (add after `get_runway`)
- Test: `backend/tests/test_patterns.py`

- [ ] **Step 1: Append the failing test**

```python
def test_save_and_get_current_pattern_versions(tmp_path):
    conn = seeded_conn(tmp_path / "t.sqlite3")
    g1 = json.dumps({"points": [{"lat": 39.92, "lon": -105.13}], "closed": True, "spline": "catmull-rom"})
    g2 = json.dumps({"points": [{"lat": 39.93, "lon": -105.14}], "closed": True, "spline": "catmull-rom"})

    saved1 = db.save_runway_pattern(conn, "KBJC", "12L", g1, name="v1", editor_visitor_id="visitor-1", editor_ip="1.2.3.4")
    assert saved1["version"] == 1
    assert saved1["is_current"] == 1

    saved2 = db.save_runway_pattern(conn, "KBJC", "12L", g2, name="v2", editor_visitor_id="visitor-1", editor_ip="1.2.3.4")
    assert saved2["version"] == 2

    current = db.get_current_pattern(conn, "KBJC", "12L")
    assert current["version"] == 2
    assert json.loads(current["geometry_json"])["points"][0]["lat"] == 39.93

    # exactly one current row per runway end
    n_current = conn.execute(
        "SELECT COUNT(*) AS c FROM runway_patterns WHERE icao='KBJC' AND runway_id='12L' AND is_current=1"
    ).fetchone()["c"]
    assert n_current == 1

    # a different runway end is independent
    assert db.get_current_pattern(conn, "KBJC", "30R") is None

    db.save_runway_pattern(conn, "KBJC", "30R", g1)
    listed = db.current_patterns_for_airport(conn, "KBJC")
    assert {p["runway_id"] for p in listed} == {"12L", "30R"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/d/Code/FAA_circle_jerk/backend && /Users/d/Code/FAA_circle_jerk/.venv/bin/python -m pytest tests/test_patterns.py::test_save_and_get_current_pattern_versions -v`
Expected: FAIL — `AttributeError: module 'app.db' has no attribute 'save_runway_pattern'`.

- [ ] **Step 3: Implement the three functions in `backend/app/db.py`** (after `get_runway`)

```python
def save_runway_pattern(
    conn: sqlite3.Connection,
    icao: str,
    runway_id: str,
    geometry_json: str,
    name: str | None = None,
    editor_visitor_id: str | None = None,
    editor_ip: str | None = None,
    change_note: str | None = None,
) -> dict:
    """Append a new pattern version and make it the current one."""
    icao = icao.upper()
    row = conn.execute(
        "SELECT MAX(version) AS v FROM runway_patterns WHERE icao = ? AND runway_id = ?",
        (icao, runway_id),
    ).fetchone()
    next_version = (row["v"] or 0) + 1
    conn.execute(
        "UPDATE runway_patterns SET is_current = 0 WHERE icao = ? AND runway_id = ?",
        (icao, runway_id),
    )
    conn.execute(
        """
        INSERT INTO runway_patterns
          (icao, runway_id, geometry_json, name, version, is_current,
           editor_visitor_id, editor_ip, change_note)
        VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?)
        """,
        (icao, runway_id, geometry_json, name, next_version,
         editor_visitor_id, editor_ip, change_note),
    )
    return get_current_pattern(conn, icao, runway_id)


def get_current_pattern(conn: sqlite3.Connection, icao: str, runway_id: str) -> dict | None:
    row = conn.execute(
        "SELECT * FROM runway_patterns WHERE icao = ? AND runway_id = ? AND is_current = 1",
        (icao.upper(), runway_id),
    ).fetchone()
    return dict(row) if row else None


def current_patterns_for_airport(conn: sqlite3.Connection, icao: str) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM runway_patterns WHERE icao = ? AND is_current = 1 ORDER BY runway_id",
        (icao.upper(),),
    ).fetchall()
    return [dict(row) for row in rows]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/d/Code/FAA_circle_jerk/backend && /Users/d/Code/FAA_circle_jerk/.venv/bin/python -m pytest tests/test_patterns.py::test_save_and_get_current_pattern_versions -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/db.py backend/tests/test_patterns.py
git commit -m "feat: save/get-current/list runway patterns"
```

---

## Task 5: Pattern history, revert, and edit-rate count

**Files:**
- Modify: `backend/app/db.py` (after `current_patterns_for_airport`)
- Test: `backend/tests/test_patterns.py`

- [ ] **Step 1: Append the failing test**

```python
def test_pattern_history_revert_and_rate_count(tmp_path):
    conn = seeded_conn(tmp_path / "t.sqlite3")
    g1 = json.dumps({"points": [{"lat": 39.92, "lon": -105.13}], "closed": True, "spline": "catmull-rom"})
    g2 = json.dumps({"points": [{"lat": 39.93, "lon": -105.14}], "closed": True, "spline": "catmull-rom"})
    db.save_runway_pattern(conn, "KBJC", "12L", g1, name="v1", editor_visitor_id="visitor-1", editor_ip="1.1.1.1")
    db.save_runway_pattern(conn, "KBJC", "12L", g2, name="v2", editor_visitor_id="visitor-1", editor_ip="1.1.1.1")

    versions = db.list_pattern_versions(conn, "KBJC", "12L")
    assert [v["version"] for v in versions] == [2, 1]  # newest first

    reverted = db.revert_pattern(conn, "KBJC", "12L", 1, editor_visitor_id="visitor-2", editor_ip="2.2.2.2")
    assert reverted["version"] == 3
    assert json.loads(reverted["geometry_json"])["points"][0]["lat"] == 39.92  # v1 geometry
    assert "revert to v1" in reverted["change_note"]
    assert db.revert_pattern(conn, "KBJC", "12L", 99) is None  # missing version

    # rate count: 3 edits by visitor-1/1.1.1.1 so far (v1, v2, and revert used visitor-2)
    count_v1 = db.count_recent_pattern_edits(conn, "visitor-1", "1.1.1.1", 0)
    assert count_v1 == 2
    count_v2 = db.count_recent_pattern_edits(conn, "visitor-2", "2.2.2.2", 0)
    assert count_v2 == 1
    # window excludes old edits
    assert db.count_recent_pattern_edits(conn, "visitor-1", "1.1.1.1", 9_999_999_999) == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/d/Code/FAA_circle_jerk/backend && /Users/d/Code/FAA_circle_jerk/.venv/bin/python -m pytest tests/test_patterns.py::test_pattern_history_revert_and_rate_count -v`
Expected: FAIL — `AttributeError: module 'app.db' has no attribute 'list_pattern_versions'`.

- [ ] **Step 3: Implement the three functions in `backend/app/db.py`** (after `current_patterns_for_airport`)

```python
def list_pattern_versions(conn: sqlite3.Connection, icao: str, runway_id: str) -> list[dict]:
    rows = conn.execute(
        """
        SELECT id, version, name, is_current, locked, editor_visitor_id, change_note, created_at
        FROM runway_patterns
        WHERE icao = ? AND runway_id = ?
        ORDER BY version DESC
        """,
        (icao.upper(), runway_id),
    ).fetchall()
    return [dict(row) for row in rows]


def revert_pattern(
    conn: sqlite3.Connection,
    icao: str,
    runway_id: str,
    version: int,
    editor_visitor_id: str | None = None,
    editor_ip: str | None = None,
) -> dict | None:
    """Clone an earlier version's geometry into a new current version."""
    src = conn.execute(
        "SELECT geometry_json, name FROM runway_patterns WHERE icao = ? AND runway_id = ? AND version = ?",
        (icao.upper(), runway_id, version),
    ).fetchone()
    if src is None:
        return None
    return save_runway_pattern(
        conn, icao, runway_id, src["geometry_json"],
        name=src["name"],
        editor_visitor_id=editor_visitor_id,
        editor_ip=editor_ip,
        change_note=f"revert to v{version}",
    )


def count_recent_pattern_edits(
    conn: sqlite3.Connection,
    editor_visitor_id: str | None,
    editor_ip: str | None,
    since_ts: int,
) -> int:
    row = conn.execute(
        """
        SELECT COUNT(*) AS c FROM runway_patterns
        WHERE created_at >= ?
          AND ((editor_visitor_id IS NOT NULL AND editor_visitor_id = ?)
               OR (editor_ip IS NOT NULL AND editor_ip = ?))
        """,
        (since_ts, editor_visitor_id, editor_ip),
    ).fetchone()
    return row["c"]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/d/Code/FAA_circle_jerk/backend && /Users/d/Code/FAA_circle_jerk/.venv/bin/python -m pytest tests/test_patterns.py::test_pattern_history_revert_and_rate_count -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/db.py backend/tests/test_patterns.py
git commit -m "feat: pattern history, revert, and edit-rate count"
```

---

## Task 6: Read endpoints — list, get-current, template

**Files:**
- Modify: `backend/app/main.py` (add imports, Pydantic models, a `_pattern_response` helper, and three GET routes)
- Test: `backend/tests/test_patterns.py`

- [ ] **Step 1: Append the failing test**

```python
def test_pattern_read_endpoints(tmp_path, monkeypatch):
    with api_client(tmp_path, monkeypatch) as client:
        # template for a real runway
        resp = client.get("/runways/KBJC/12L/pattern/template", params={"side": "left"})
        assert resp.status_code == 200
        geom = resp.json()["geometry"]
        assert len(geom["points"]) == 5 and geom["closed"] is True

        # template for unknown runway → 404
        assert client.get("/runways/KBJC/ZZ/pattern/template").status_code == 404

        # no pattern yet
        resp = client.get("/runways/KBJC/12L/pattern")
        assert resp.status_code == 200 and resp.json()["pattern"] is None

        # airport patterns list is empty
        resp = client.get("/airports/KBJC/patterns")
        assert resp.status_code == 200 and resp.json()["patterns"] == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/d/Code/FAA_circle_jerk/backend && /Users/d/Code/FAA_circle_jerk/.venv/bin/python -m pytest tests/test_patterns.py::test_pattern_read_endpoints -v`
Expected: FAIL — 404 from FastAPI for the unregistered routes (assertion on `geom` fails / KeyError).

- [ ] **Step 3: Add imports, models, helper, and routes to `backend/app/main.py`**

First, ensure these imports exist near the top of `main.py` (add any that are missing — they are additive and safe):

```python
import json
from typing import Annotated
from fastapi import FastAPI, Query, Request, HTTPException, Depends
from pydantic import BaseModel, Field
from . import db, patterns
```

(Most are already imported; the key additions are `import json`, `patterns` in the `from . import ...` line, and `HTTPException` if absent. Do not duplicate existing imports.)

Add these module constants near the other module-level constants:

```python
PATTERN_EDIT_WINDOW_S = 3600
PATTERN_EDIT_MAX_PER_WINDOW = 30
```

Add the Pydantic models near the other request models (after `ActivitySubmissionRequest`):

```python
class PatternPoint(BaseModel):
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)


class PatternSaveRequest(BaseModel):
    points: list[PatternPoint] = Field(min_length=1, max_length=50)
    closed: bool = True
    name: str | None = Field(default=None, max_length=80)
    change_note: str | None = Field(default=None, max_length=200)
    visitor_id: str | None = Field(default=None, min_length=8, max_length=80)


class PatternRevertRequest(BaseModel):
    version: int = Field(ge=1)
    visitor_id: str | None = Field(default=None, min_length=8, max_length=80)
```

Add this helper near the other module-level helpers (e.g. after `client_ip`):

```python
def _pattern_response(row: dict) -> dict:
    return {
        "id": row["id"],
        "icao": row["icao"],
        "runway_id": row["runway_id"],
        "version": row["version"],
        "name": row["name"],
        "locked": bool(row["locked"]),
        "geometry": json.loads(row["geometry_json"]),
        "change_note": row.get("change_note"),
        "created_at": row["created_at"],
    }
```

Add the three GET routes (place them together, after an existing airports/runways route):

```python
@app.get("/airports/{icao}/patterns")
async def get_airport_patterns(
    icao: str,
    settings: Annotated[Settings, Depends(settings_dep)],
):
    with db_session(settings.database_path) as conn:
        patterns_out = [_pattern_response(row) for row in db.current_patterns_for_airport(conn, icao)]
    return {"airport_icao": icao.upper(), "patterns": patterns_out}


@app.get("/runways/{icao}/{runway_id}/pattern")
async def get_runway_pattern(
    icao: str,
    runway_id: str,
    settings: Annotated[Settings, Depends(settings_dep)],
):
    with db_session(settings.database_path) as conn:
        row = db.get_current_pattern(conn, icao, runway_id)
    return {"pattern": _pattern_response(row) if row else None}


@app.get("/runways/{icao}/{runway_id}/pattern/template")
async def get_runway_pattern_template(
    icao: str,
    runway_id: str,
    settings: Annotated[Settings, Depends(settings_dep)],
    side: Annotated[str, Query(pattern="^(left|right)$")] = "left",
):
    with db_session(settings.database_path) as conn:
        runway = db.get_runway(conn, icao, runway_id)
    if runway is None:
        raise HTTPException(status_code=404, detail="runway not found")
    return {"geometry": patterns.generate_template_pattern(runway, side=side)}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/d/Code/FAA_circle_jerk/backend && /Users/d/Code/FAA_circle_jerk/.venv/bin/python -m pytest tests/test_patterns.py::test_pattern_read_endpoints -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/main.py backend/tests/test_patterns.py
git commit -m "feat: pattern read endpoints (list, get, template)"
```

---

## Task 7: Write endpoints — save (PUT), history, revert

**Files:**
- Modify: `backend/app/main.py` (three routes; reuse models/helper/constants from Task 6)
- Test: `backend/tests/test_patterns.py`

- [ ] **Step 1: Append the failing tests**

```python
def test_pattern_save_history_revert_flow(tmp_path, monkeypatch):
    with api_client(tmp_path, monkeypatch) as client:
        body1 = {"points": [{"lat": 39.92, "lon": -105.13}, {"lat": 39.93, "lon": -105.14}],
                 "closed": True, "name": "v1", "visitor_id": "visitor-123456"}
        r1 = client.put("/runways/KBJC/12L/pattern", json=body1)
        assert r1.status_code == 200
        assert r1.json()["pattern"]["version"] == 1

        body2 = {"points": [{"lat": 39.94, "lon": -105.15}], "name": "v2", "visitor_id": "visitor-123456"}
        r2 = client.put("/runways/KBJC/12L/pattern", json=body2)
        assert r2.json()["pattern"]["version"] == 2

        # get-current reflects v2
        assert client.get("/runways/KBJC/12L/pattern").json()["pattern"]["version"] == 2

        # history has 2 versions, newest first
        hist = client.get("/runways/KBJC/12L/pattern/history").json()["versions"]
        assert [v["version"] for v in hist] == [2, 1]

        # revert to v1 → new v3 with v1 geometry
        rev = client.post("/runways/KBJC/12L/pattern/revert", json={"version": 1, "visitor_id": "visitor-123456"})
        assert rev.status_code == 200
        assert rev.json()["pattern"]["version"] == 3
        assert rev.json()["pattern"]["geometry"]["points"][0]["lat"] == 39.92

        # revert to missing version → 404
        assert client.post("/runways/KBJC/12L/pattern/revert", json={"version": 99, "visitor_id": "visitor-123456"}).status_code == 404


def test_pattern_save_rejects_out_of_bounds_point(tmp_path, monkeypatch):
    with api_client(tmp_path, monkeypatch) as client:
        body = {"points": [{"lat": 41.5, "lon": -105.13}], "visitor_id": "visitor-123456"}  # ~90 nm away
        r = client.put("/runways/KBJC/12L/pattern", json=body)
        assert r.status_code == 400


def test_pattern_save_unknown_runway_404(tmp_path, monkeypatch):
    with api_client(tmp_path, monkeypatch) as client:
        body = {"points": [{"lat": 39.92, "lon": -105.13}], "visitor_id": "visitor-123456"}
        assert client.put("/runways/KBJC/ZZ/pattern", json=body).status_code == 404


def test_pattern_save_rate_limited(tmp_path, monkeypatch):
    monkeypatch.setattr("app.main.PATTERN_EDIT_MAX_PER_WINDOW", 2)
    with api_client(tmp_path, monkeypatch) as client:
        body = {"points": [{"lat": 39.92, "lon": -105.13}], "visitor_id": "visitor-123456"}
        assert client.put("/runways/KBJC/12L/pattern", json=body).status_code == 200
        assert client.put("/runways/KBJC/12L/pattern", json=body).status_code == 200
        assert client.put("/runways/KBJC/12L/pattern", json=body).status_code == 429
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/d/Code/FAA_circle_jerk/backend && /Users/d/Code/FAA_circle_jerk/.venv/bin/python -m pytest tests/test_patterns.py -k "save_history or out_of_bounds or unknown_runway or rate_limited" -v`
Expected: FAIL — PUT/POST routes return 405/404 (not registered).

- [ ] **Step 3: Add the three write routes to `backend/app/main.py`** (after the GET routes from Task 6)

```python
@app.put("/runways/{icao}/{runway_id}/pattern")
async def save_runway_pattern_endpoint(
    icao: str,
    runway_id: str,
    payload: PatternSaveRequest,
    request: Request,
    settings: Annotated[Settings, Depends(settings_dep)],
):
    now = int(time.time())
    ip = client_ip(request)
    with db_session(settings.database_path) as conn:
        airport = db.get_airport(conn, icao)
        if airport is None:
            raise HTTPException(status_code=404, detail="airport not found")
        if db.get_runway(conn, icao, runway_id) is None:
            raise HTTPException(status_code=404, detail="runway not found")
        current = db.get_current_pattern(conn, icao, runway_id)
        if current and current["locked"]:
            raise HTTPException(status_code=409, detail="pattern is locked")
        points = [{"lat": p.lat, "lon": p.lon} for p in payload.points]
        error = patterns.validate_pattern_geometry(points, airport)
        if error:
            raise HTTPException(status_code=400, detail=error)
        recent = db.count_recent_pattern_edits(conn, payload.visitor_id, ip, now - PATTERN_EDIT_WINDOW_S)
        if recent >= PATTERN_EDIT_MAX_PER_WINDOW:
            raise HTTPException(status_code=429, detail="too many pattern edits; slow down")
        geometry = {"points": points, "closed": payload.closed, "spline": patterns.PATTERN_SPLINE}
        saved = db.save_runway_pattern(
            conn, icao, runway_id, json.dumps(geometry),
            name=payload.name, editor_visitor_id=payload.visitor_id,
            editor_ip=ip, change_note=payload.change_note,
        )
    return {"pattern": _pattern_response(saved)}


@app.get("/runways/{icao}/{runway_id}/pattern/history")
async def get_runway_pattern_history(
    icao: str,
    runway_id: str,
    settings: Annotated[Settings, Depends(settings_dep)],
):
    with db_session(settings.database_path) as conn:
        versions = db.list_pattern_versions(conn, icao, runway_id)
    return {"versions": [
        {**v, "is_current": bool(v["is_current"]), "locked": bool(v["locked"])}
        for v in versions
    ]}


@app.post("/runways/{icao}/{runway_id}/pattern/revert")
async def revert_runway_pattern_endpoint(
    icao: str,
    runway_id: str,
    payload: PatternRevertRequest,
    request: Request,
    settings: Annotated[Settings, Depends(settings_dep)],
):
    with db_session(settings.database_path) as conn:
        current = db.get_current_pattern(conn, icao, runway_id)
        if current and current["locked"]:
            raise HTTPException(status_code=409, detail="pattern is locked")
        saved = db.revert_pattern(
            conn, icao, runway_id, payload.version,
            editor_visitor_id=payload.visitor_id, editor_ip=client_ip(request),
        )
        if saved is None:
            raise HTTPException(status_code=404, detail="version not found")
    return {"pattern": _pattern_response(saved)}
```

(`time` is already imported in main.py — confirm; it is used by `/activity/submissions`.)

- [ ] **Step 4: Run the full suite to verify everything passes**

Run: `cd /Users/d/Code/FAA_circle_jerk/backend && /Users/d/Code/FAA_circle_jerk/.venv/bin/python -m pytest tests/ -q`
Expected: PASS — all prior tests (122 from Plan 1 + the new pattern tests) green.

- [ ] **Step 5: Commit**

```bash
git add backend/app/main.py backend/tests/test_patterns.py
git commit -m "feat: pattern write endpoints (save, history, revert)"
```

---

## Self-review

**Spec coverage:** Feature 1's backend is fully covered — versioned `runway_patterns` (Task 2/4/5), one current pattern per end with full history + revert (Task 5/7), open editing via `PUT` with guardrails (Task 7: ≤50 points via Pydantic `max_length`, ≤10 nm via `validate_pattern_geometry`, DB-based per-editor rate limit, `locked` enforcement), and the "generate standard pattern from runway" accelerator (Task 3 `generate_template_pattern` + Task 6 template endpoint). The frontend editor that consumes these endpoints is Plan 3.

**Placeholder scan:** No TBD/TODO/vague steps. Every code step is complete; every run step has a command + expected result.

**Type consistency:** `save_runway_pattern` returns the dict from `get_current_pattern`; `revert_pattern` calls `save_runway_pattern`; `_pattern_response` reads `geometry_json` (DB column) and emits `geometry` (parsed). Endpoint models (`PatternPoint`, `PatternSaveRequest`, `PatternRevertRequest`) feed `validate_pattern_geometry(points, airport)` where `points` is `list[{"lat","lon"}]` and `airport` is the `Airport` dataclass (has `.lat`/`.lon`). `generate_template_pattern` returns the same `{"points","closed","spline"}` shape that `PUT` stores. Function names are stable across tasks.

**Deferred (YAGNI / later plan):** the admin endpoint to toggle `locked` (column + enforcement exist now; the toggle is an admin-surface concern); moving `PATTERN_EDIT_*` constants into `settings.py`; pattern-geometry self-intersection checks. The frontend (Plan 3) will add the actual drawing UI.
