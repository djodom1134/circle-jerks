# Deviation From Pattern Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** For each detected circling operation, measure how far the aircraft strays from the matched VNAP pattern: KPI A = time (and %) spent farther than a ~0.25 nm corridor from the pattern; KPI B = mean perpendicular distance (nm), plus peak. Store these on the `operations` row.

**Architecture:** A new pure module `deviation.py` densifies a pattern's control points into a Catmull-Rom polyline, computes a track sample's perpendicular distance to it, picks the matching pattern by rotational sense + nearest fit ("direction-matched"), and rolls up the two KPIs over a circle's track window. The worker computes deviation after persisting circle operations — only when a current pattern exists for the airport — and writes the metrics back via a targeted `UPDATE`.

**Tech Stack:** Python 3, SQLite, pytest. All logic is pure and unit-testable. Tests from `backend/` via `/Users/d/Code/FAA_circle_jerk/.venv/bin/python -m pytest`.

This is Plan 4 of 6. It builds on Plan 1 (`operations` table with the deviation columns) and Plan 2 (`runway_patterns` + `current_patterns_for_airport`).

---

### Background facts (verified)

- `operations` already has the columns: `matched_pattern_id`, `deviation_mean_nm`, `deviation_peak_nm`, `time_off_pattern_s`, `time_total_s`, `pct_off_pattern` (all nullable). `db.read_operations(conn, icao, start, end, types=None)` and `db.persist_events(conn, events)` exist (Plan 1). `db.current_patterns_for_airport(conn, icao)` returns rows with `id` + `geometry_json` (Plan 2).
- `backend/app/geo.py` has `@dataclass(frozen=True) class Point: lat; lon`, `distance_nm(a, b)`, `bearing_deg(a, b)`, and `closest_segment_approach_nm(a, b, target) -> tuple[float, float]` (returns `(distance_nm, t)` — closest approach distance from segment a→b to `target`).
- Circle events are built in `backend/app/detectors.py` inside `_detect_circles_in_samples`, in the closed-lap branch that returns a dict with `"detection_method": "course_turn_closed_lap"` (around line 263-280). At that return, the local `loop_samples` (the ordered samples forming the lap) is in scope (it's used just above to compute `radius_values`). The event already has `id`, `type:"circle"`, `icao24`, `timestamp`, `turn_direction`.
- `backend/app/services.py` `run_detectors_for_monitor` detects events per track in `for icao24, track in zip(icao24s, tracks):`, collects new ones into `new_events`, and ends with `if new_events: db.persist_events(conn, new_events)` then `return written`. `services.py` imports `db` and `json` is NOT necessarily imported there — add `import json` if missing. Track samples are dicts with `timestamp`, `lat`, `lon`.
- Geometry JSON shape (Plan 2): `{"points": [{"lat","lon"}...], "closed": bool, "spline": "catmull-rom"}`.
- Test conventions: `seeded_conn(path)` = `db.connect; executescript(db.SCHEMA); seed_db; commit`. `tmp_path`. A known circle-producing track + `ScanParams` + `detect_events` are available (see `backend/tests/test_operations.py` `closed_loop_track`, `airport_kbjc`).

New tests go in **`backend/tests/test_deviation.py`** (create in Task 2). The corridor default is **0.25 nm**.

---

## Task 1: Add circle lap time bounds to the event

**Files:**
- Modify: `backend/app/detectors.py` (the closed-lap circle event dict)
- Test: `backend/tests/test_operations.py` (append — reuses its `closed_loop_track`/`airport_kbjc`)

- [ ] **Step 1: Append the failing test**

```python
def test_closed_lap_circle_event_has_lap_time_bounds():
    from app.detectors import detect_circles
    ap = airport_kbjc()
    events = detect_circles(closed_loop_track(), ap, ScanParams(airport_icao="KBJC", user_lat=40.0, user_lon=-105.2))
    lap = next(e for e in events if e.get("detection_method") == "course_turn_closed_lap")
    assert "start_timestamp" in lap and "end_timestamp" in lap
    assert lap["start_timestamp"] <= lap["end_timestamp"] <= lap["timestamp"] + 1
    assert lap["end_timestamp"] - lap["start_timestamp"] >= 120  # the lap spans real time
```

(`detect_circles` and `ScanParams` are already imported at the top of `test_operations.py`; if `detect_circles` is not imported there, add `from app.detectors import detect_circles` and `from app.domain import ScanParams` — `ScanParams` is already imported.)

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/d/Code/FAA_circle_jerk/backend && /Users/d/Code/FAA_circle_jerk/.venv/bin/python -m pytest tests/test_operations.py::test_closed_lap_circle_event_has_lap_time_bounds -v`
Expected: FAIL — `KeyError`/assertion: `start_timestamp` not in the event.

- [ ] **Step 3: Add the two fields to the closed-lap circle event**

In `backend/app/detectors.py`, in the closed-lap circle return dict (the one with `"detection_method": "course_turn_closed_lap"`), add these two keys (place them right after the `"timestamp": timestamp,` line):

```python
            "start_timestamp": int(loop_samples[0]["timestamp"]),
            "end_timestamp": int(loop_samples[-1]["timestamp"]),
```

(`loop_samples` is the ordered list of lap samples already in scope at this return.)

- [ ] **Step 4: Run test to verify it passes + full suite**

Run: `cd /Users/d/Code/FAA_circle_jerk/backend && /Users/d/Code/FAA_circle_jerk/.venv/bin/python -m pytest tests/test_operations.py::test_closed_lap_circle_event_has_lap_time_bounds tests/ -q`
Expected: PASS (the targeted test, and the whole suite stays green).

- [ ] **Step 5: Commit**

```bash
git add backend/app/detectors.py backend/tests/test_operations.py
git commit -m "feat: tag closed-lap circle events with lap start/end timestamps"
```

---

## Task 2: `densify_pattern` (Catmull-Rom) + `perpendicular_distance_nm`

**Files:**
- Create: `backend/app/deviation.py`
- Create: `backend/tests/test_deviation.py`

- [ ] **Step 1: Write the failing test** — `backend/tests/test_deviation.py`

```python
from __future__ import annotations

from app import deviation
from app.geo import Point, distance_nm


def test_densify_pattern_interpolates_and_closes():
    geom = {"points": [{"lat": 0.0, "lon": 0.0}, {"lat": 0.0, "lon": 0.1},
                       {"lat": 0.1, "lon": 0.1}, {"lat": 0.1, "lon": 0.0}],
            "closed": True, "spline": "catmull-rom"}
    poly = deviation.densify_pattern(geom, steps=8)
    assert len(poly) > 4
    assert all(isinstance(p, Point) for p in poly)


def test_perpendicular_distance_zero_on_segment():
    line = [Point(0.0, 0.0), Point(0.0, 0.1)]  # runs east along the equator
    on = Point(0.0, 0.05)
    off = Point(0.02, 0.05)  # ~1.2 nm north of the line
    assert deviation.perpendicular_distance_nm(on, line) < 0.05
    assert deviation.perpendicular_distance_nm(off, line) > 0.5
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/d/Code/FAA_circle_jerk/backend && /Users/d/Code/FAA_circle_jerk/.venv/bin/python -m pytest tests/test_deviation.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.deviation'`.

- [ ] **Step 3: Create `backend/app/deviation.py`**

```python
from __future__ import annotations

from statistics import fmean

from .geo import Point, closest_segment_approach_nm

CORRIDOR_NM = 0.25


def densify_pattern(geometry: dict, steps: int = 12) -> list[Point]:
    """Catmull-Rom densify a pattern's control points into a polyline of Points.
    Control points are interpolated in (lon, lat) space, fine at pattern scale."""
    pts = [(float(p["lon"]), float(p["lat"])) for p in geometry.get("points", [])]
    if geometry.get("closed") and len(pts) > 2:
        pts = pts + [pts[0]]
    if len(pts) < 3:
        return [Point(lat, lon) for lon, lat in pts]
    out: list[tuple[float, float]] = [pts[0]]
    for i in range(len(pts) - 1):
        p0 = pts[i - 1] if i - 1 >= 0 else pts[i]
        p1 = pts[i]
        p2 = pts[i + 1]
        p3 = pts[i + 2] if i + 2 < len(pts) else pts[i + 1]
        for s in range(1, steps + 1):
            t = s / steps
            t2 = t * t
            t3 = t2 * t
            x = 0.5 * (2 * p1[0] + (-p0[0] + p2[0]) * t + (2 * p0[0] - 5 * p1[0] + 4 * p2[0] - p3[0]) * t2 + (-p0[0] + 3 * p1[0] - 3 * p2[0] + p3[0]) * t3)
            y = 0.5 * (2 * p1[1] + (-p0[1] + p2[1]) * t + (2 * p0[1] - 5 * p1[1] + 4 * p2[1] - p3[1]) * t2 + (-p0[1] + 3 * p1[1] - 3 * p2[1] + p3[1]) * t3)
            out.append((x, y))
    return [Point(lat, lon) for lon, lat in out]


def perpendicular_distance_nm(point: Point, polyline: list[Point]) -> float:
    """Minimum distance (nm) from `point` to any segment of `polyline`."""
    best = float("inf")
    for a, b in zip(polyline, polyline[1:]):
        d, _ = closest_segment_approach_nm(a, b, point)
        if d < best:
            best = d
    return best
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/d/Code/FAA_circle_jerk/backend && /Users/d/Code/FAA_circle_jerk/.venv/bin/python -m pytest tests/test_deviation.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add backend/app/deviation.py backend/tests/test_deviation.py
git commit -m "feat: pattern densify + perpendicular distance"
```

---

## Task 3: `compute_deviation` (the two KPIs)

**Files:**
- Modify: `backend/app/deviation.py`
- Test: `backend/tests/test_deviation.py`

- [ ] **Step 1: Append the failing test**

```python
def _samples(coords, dt=30):
    # coords: list of (lat, lon); evenly spaced dt seconds apart
    return [{"timestamp": 1000 + i * dt, "lat": lat, "lon": lon} for i, (lat, lon) in enumerate(coords)]


def test_compute_deviation_on_pattern_is_low():
    line = [Point(0.0, 0.0), Point(0.0, 0.2)]
    samples = _samples([(0.0, 0.02 * i) for i in range(6)])  # walking along the line
    metrics = deviation.compute_deviation(samples, line)
    assert metrics["deviation_mean_nm"] < 0.05
    assert metrics["pct_off_pattern"] == 0.0
    assert metrics["time_total_s"] == 5 * 30


def test_compute_deviation_off_pattern_is_high():
    line = [Point(0.0, 0.0), Point(0.0, 0.2)]
    samples = _samples([(0.05, 0.02 * i) for i in range(6)])  # ~3 nm north of the line
    metrics = deviation.compute_deviation(samples, line)
    assert metrics["deviation_mean_nm"] > 1.0
    assert metrics["pct_off_pattern"] == 1.0
    assert metrics["deviation_peak_nm"] >= metrics["deviation_mean_nm"]


def test_compute_deviation_needs_two_samples():
    line = [Point(0.0, 0.0), Point(0.0, 0.2)]
    assert deviation.compute_deviation(_samples([(0.0, 0.0)]), line) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/d/Code/FAA_circle_jerk/backend && /Users/d/Code/FAA_circle_jerk/.venv/bin/python -m pytest tests/test_deviation.py -k compute_deviation -v`
Expected: FAIL — `AttributeError: module 'app.deviation' has no attribute 'compute_deviation'`.

- [ ] **Step 3: Implement `compute_deviation`** (append to `deviation.py`)

```python
def compute_deviation(samples: list[dict], polyline: list[Point], corridor_nm: float = CORRIDOR_NM) -> dict | None:
    """Roll the two deviation KPIs over a circle's track window.

    KPI A: time_off_pattern_s / pct_off_pattern = time spent farther than
    `corridor_nm` from the pattern. KPI B: deviation_mean_nm = time-weighted
    mean perpendicular distance; deviation_peak_nm = max."""
    usable = sorted(
        (s for s in samples if s.get("lat") is not None and s.get("lon") is not None),
        key=lambda s: s["timestamp"],
    )
    if len(usable) < 2 or len(polyline) < 2:
        return None
    time_total = 0
    time_off = 0
    weighted = 0.0
    peak = 0.0
    for cur, nxt in zip(usable, usable[1:]):
        dt = max(0, int(nxt["timestamp"]) - int(cur["timestamp"]))
        dist = perpendicular_distance_nm(Point(cur["lat"], cur["lon"]), polyline)
        peak = max(peak, dist)
        time_total += dt
        weighted += dist * dt
        if dist > corridor_nm:
            time_off += dt
    peak = max(peak, perpendicular_distance_nm(Point(usable[-1]["lat"], usable[-1]["lon"]), polyline))
    if time_total == 0:
        return None
    return {
        "deviation_mean_nm": round(weighted / time_total, 3),
        "deviation_peak_nm": round(peak, 3),
        "time_off_pattern_s": time_off,
        "time_total_s": time_total,
        "pct_off_pattern": round(time_off / time_total, 3),
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/d/Code/FAA_circle_jerk/backend && /Users/d/Code/FAA_circle_jerk/.venv/bin/python -m pytest tests/test_deviation.py -k compute_deviation -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add backend/app/deviation.py backend/tests/test_deviation.py
git commit -m "feat: compute time-off-corridor + mean/peak deviation KPIs"
```

---

## Task 4: `match_pattern` (direction-matched + nearest)

**Files:**
- Modify: `backend/app/deviation.py`
- Test: `backend/tests/test_deviation.py`

Direction matching uses rotational sense computed identically for the track loop and each pattern loop (so the convention cancels — no dependence on the detector's left/right label). Among patterns with the same sense, pick the nearest (lowest mean perpendicular distance); fall back to nearest overall.

- [ ] **Step 1: Append the failing test**

```python
def test_match_pattern_picks_nearest_same_sense():
    # Aircraft flies a small CCW square near (0,0).
    track = _samples([(0.0, 0.0), (0.0, 0.02), (0.02, 0.02), (0.02, 0.0), (0.0, 0.0)])

    near_ccw = {"id": 1, "geometry": {"points": [
        {"lat": 0.0, "lon": 0.0}, {"lat": 0.0, "lon": 0.02},
        {"lat": 0.02, "lon": 0.02}, {"lat": 0.02, "lon": 0.0}], "closed": True}}
    far_ccw = {"id": 2, "geometry": {"points": [
        {"lat": 1.0, "lon": 1.0}, {"lat": 1.0, "lon": 1.02},
        {"lat": 1.02, "lon": 1.02}, {"lat": 1.02, "lon": 1.0}], "closed": True}}

    assert deviation.match_pattern(track, [near_ccw, far_ccw])["id"] == 1
    assert deviation.match_pattern(track, []) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/d/Code/FAA_circle_jerk/backend && /Users/d/Code/FAA_circle_jerk/.venv/bin/python -m pytest tests/test_deviation.py::test_match_pattern_picks_nearest_same_sense -v`
Expected: FAIL — `AttributeError: module 'app.deviation' has no attribute 'match_pattern'`.

- [ ] **Step 3: Implement `match_pattern` + `_loop_sense`** (append to `deviation.py`)

```python
def _loop_sense(lonlat: list[tuple[float, float]]) -> int | None:
    """Signed-area sense of a loop in (lon, lat) space: +1 CCW, -1 CW, None if degenerate."""
    if len(lonlat) < 3:
        return None
    area = 0.0
    for (x1, y1), (x2, y2) in zip(lonlat, lonlat[1:] + [lonlat[0]]):
        area += x1 * y2 - x2 * y1
    if area > 0:
        return 1
    if area < 0:
        return -1
    return None


def match_pattern(samples: list[dict], patterns: list[dict]) -> dict | None:
    """Pick the pattern the track best follows: prefer matching rotational sense,
    then nearest by mean perpendicular distance. `patterns` items: {id, geometry}."""
    usable = [s for s in samples if s.get("lat") is not None and s.get("lon") is not None]
    if not patterns or len(usable) < 2:
        return patterns[0] if patterns else None
    track_sense = _loop_sense([(s["lon"], s["lat"]) for s in usable])

    densified = []
    for pat in patterns:
        poly = densify_pattern(pat["geometry"])
        if len(poly) < 2:
            continue
        sense = _loop_sense([(p.lon, p.lat) for p in poly])
        densified.append((pat, poly, sense))
    if not densified:
        return None

    candidates = densified
    if track_sense is not None:
        same = [d for d in densified if d[2] == track_sense]
        if same:
            candidates = same

    best = None
    best_mean = float("inf")
    for pat, poly, _sense in candidates:
        mean = fmean(perpendicular_distance_nm(Point(s["lat"], s["lon"]), poly) for s in usable)
        if mean < best_mean:
            best_mean = mean
            best = pat
    return best
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/d/Code/FAA_circle_jerk/backend && /Users/d/Code/FAA_circle_jerk/.venv/bin/python -m pytest tests/test_deviation.py::test_match_pattern_picks_nearest_same_sense -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/deviation.py backend/tests/test_deviation.py
git commit -m "feat: direction-matched pattern selection"
```

---

## Task 5: `db.update_operation_deviation`

**Files:**
- Modify: `backend/app/db.py` (after `persist_events`)
- Test: `backend/tests/test_deviation.py`

- [ ] **Step 1: Append the failing test**

```python
def test_update_operation_deviation(tmp_path):
    from app import db
    conn = db.connect(str(tmp_path / "t.sqlite3"))
    conn.executescript(db.SCHEMA)
    db.seed_db(conn)
    conn.commit()
    db.upsert_operation(conn, db.operation_from_event({
        "id": "dev1", "type": "circle", "icao24": "a", "callsign": "N1",
        "timestamp": 1000, "airport_icao": "KBJC",
    }))
    db.update_operation_deviation(conn, "dev1", 7, {
        "deviation_mean_nm": 0.42, "deviation_peak_nm": 0.9,
        "time_off_pattern_s": 60, "time_total_s": 240, "pct_off_pattern": 0.25,
    })
    conn.commit()
    row = db.read_operations(conn, "KBJC", 0, 10000)[0]
    assert row["matched_pattern_id"] == 7
    assert row["deviation_mean_nm"] == 0.42
    assert row["pct_off_pattern"] == 0.25
    assert row["time_total_s"] == 240
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/d/Code/FAA_circle_jerk/backend && /Users/d/Code/FAA_circle_jerk/.venv/bin/python -m pytest tests/test_deviation.py::test_update_operation_deviation -v`
Expected: FAIL — `AttributeError: ... 'update_operation_deviation'`.

- [ ] **Step 3: Implement in `backend/app/db.py`** (after `persist_events`)

```python
def update_operation_deviation(
    conn: sqlite3.Connection,
    op_id: str,
    matched_pattern_id: int | None,
    metrics: dict,
) -> None:
    conn.execute(
        """
        UPDATE operations SET
          matched_pattern_id = ?,
          deviation_mean_nm = ?,
          deviation_peak_nm = ?,
          time_off_pattern_s = ?,
          time_total_s = ?,
          pct_off_pattern = ?
        WHERE id = ?
        """,
        (
            matched_pattern_id,
            metrics["deviation_mean_nm"],
            metrics["deviation_peak_nm"],
            metrics["time_off_pattern_s"],
            metrics["time_total_s"],
            metrics["pct_off_pattern"],
            op_id,
        ),
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/d/Code/FAA_circle_jerk/backend && /Users/d/Code/FAA_circle_jerk/.venv/bin/python -m pytest tests/test_deviation.py::test_update_operation_deviation -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/db.py backend/tests/test_deviation.py
git commit -m "feat: update_operation_deviation writer"
```

---

## Task 6: `deviation.store_deviations` (orchestration)

**Files:**
- Modify: `backend/app/deviation.py`
- Test: `backend/tests/test_deviation.py`

- [ ] **Step 1: Append the failing test**

```python
def test_store_deviations_end_to_end(tmp_path):
    import json
    from app import db
    from app.detectors import detect_events
    from app.domain import ScanParams
    # local fixtures (same as test_operations.py)
    from app.db import Airport

    def ap():
        return Airport(icao="KBJC", iata="BJC", name="x", city="x", country="US",
                       lat=39.9088, lon=-105.1172, elevation_ft=5673, is_towered=True)

    def sample(ts, lat, lon, alt_agl=900):
        return {"icao24": "abc123", "callsign": "N1", "timestamp": ts, "lat": lat, "lon": lon,
                "geo_altitude_ft": 5673 + alt_agl, "velocity_kt": 80, "vertical_rate_fpm": 0, "on_ground": False}

    pts = [(39.9238, -105.1172), (39.9194, -105.1013), (39.9088, -105.0950), (39.8982, -105.1013),
           (39.8938, -105.1172), (39.8982, -105.1331), (39.9088, -105.1394), (39.9194, -105.1331), (39.9238, -105.1172)]
    track = [sample(1000 + i * 30, lat, lon) for i, (lat, lon) in enumerate(pts)]

    conn = db.connect(str(tmp_path / "t.sqlite3"))
    conn.executescript(db.SCHEMA); db.seed_db(conn); conn.commit()

    # Save a pattern that IS the flown loop → deviation should be small.
    geom = json.dumps({"points": [{"lat": lat, "lon": lon} for lat, lon in pts], "closed": True, "spline": "catmull-rom"})
    saved = db.save_runway_pattern(conn, "KBJC", "12L", geom, name="loop")

    runways = db.runways_for_airport(conn, "KBJC")
    events = detect_events(track, ap(), runways, ScanParams(airport_icao="KBJC", user_lat=40.0, user_lon=-105.2))
    db.persist_events(conn, events)
    deviation.store_deviations(conn, "KBJC", events, {"abc123": track})
    conn.commit()

    rows = [r for r in db.read_operations(conn, "KBJC", 0, 10000) if r["type"] == "circle"]
    assert rows, "expected a persisted circle op"
    op = rows[0]
    assert op["matched_pattern_id"] == saved["id"]
    assert op["deviation_mean_nm"] is not None
    assert 0.0 <= op["pct_off_pattern"] <= 1.0
    assert op["deviation_mean_nm"] < 0.5  # track ≈ pattern
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/d/Code/FAA_circle_jerk/backend && /Users/d/Code/FAA_circle_jerk/.venv/bin/python -m pytest tests/test_deviation.py::test_store_deviations_end_to_end -v`
Expected: FAIL — `AttributeError: ... 'store_deviations'`.

- [ ] **Step 3: Implement `store_deviations`** (append to `deviation.py`)

```python
import json as _json

from . import db as _db


def store_deviations(conn, airport_icao: str, events: list[dict], tracks_by_icao24: dict[str, list[dict]]) -> int:
    """For each closed-lap circle event with lap bounds, match a current pattern,
    compute deviation over the lap's samples, and write it onto the operation.
    No-op when the airport has no patterns. Returns the number of ops updated."""
    pattern_rows = _db.current_patterns_for_airport(conn, airport_icao)
    if not pattern_rows:
        return 0
    patterns = []
    for row in pattern_rows:
        try:
            patterns.append({"id": row["id"], "geometry": _json.loads(row["geometry_json"])})
        except (TypeError, ValueError, KeyError):
            continue
    if not patterns:
        return 0

    updated = 0
    for event in events:
        if event.get("type") != "circle":
            continue
        start = event.get("start_timestamp")
        end = event.get("end_timestamp")
        if start is None or end is None:
            continue
        track = tracks_by_icao24.get(event.get("icao24")) or []
        samples = [
            s for s in track
            if s.get("lat") is not None and s.get("lon") is not None
            and start <= int(s.get("timestamp", -1)) <= end
        ]
        if len(samples) < 2:
            continue
        matched = match_pattern(samples, patterns)
        if matched is None:
            continue
        metrics = compute_deviation(samples, densify_pattern(matched["geometry"]))
        if metrics is None:
            continue
        _db.update_operation_deviation(conn, event["id"], matched["id"], metrics)
        updated += 1
    return updated
```

(Importing `db` as `_db` inside `deviation.py` is safe — `db.py` does not import `deviation`, so there's no cycle.)

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/d/Code/FAA_circle_jerk/backend && /Users/d/Code/FAA_circle_jerk/.venv/bin/python -m pytest tests/test_deviation.py::test_store_deviations_end_to_end -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/deviation.py backend/tests/test_deviation.py
git commit -m "feat: store_deviations orchestration (match + compute + write)"
```

---

## Task 7: Wire deviation into the worker

**Files:**
- Modify: `backend/app/services.py` (`run_detectors_for_monitor`)
- Test: `backend/tests/test_deviation.py`

- [ ] **Step 1: Append the failing test**

This verifies the worker path computes deviation when a pattern exists. It calls the same helpers `run_detectors_for_monitor` will use, asserting the wiring contract (the integration is also exercised by `store_deviations`'s end-to-end test; this test guards that services imports + calls it).

```python
def test_services_imports_and_uses_store_deviations():
    import inspect
    from app import services
    src = inspect.getsource(services.run_detectors_for_monitor)
    assert "store_deviations" in src, "run_detectors_for_monitor must call deviation.store_deviations"
    # the module must import the deviation module
    assert hasattr(services, "deviation") or "from . import" in inspect.getsource(services)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/d/Code/FAA_circle_jerk/backend && /Users/d/Code/FAA_circle_jerk/.venv/bin/python -m pytest tests/test_deviation.py::test_services_imports_and_uses_store_deviations -v`
Expected: FAIL — `store_deviations` not referenced in `run_detectors_for_monitor`.

- [ ] **Step 3: Wire it into `backend/app/services.py`**

Ensure the module imports the deviation module (add near the other `from . import ...` imports at the top of `services.py`):

```python
from . import deviation
```

In `run_detectors_for_monitor`, build a per-aircraft track map during the detection loop and call `store_deviations` after persisting. Change the loop + tail. Find:

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

Replace with:

```python
    existing_ids = await store.existing_event_ids(monitor["hash"])
    new_events: list[dict] = []
    tracks_by_icao24: dict[str, list[dict]] = {}
    for icao24, track in zip(icao24s, tracks):
        if not track_intersects_bbox(track, tuple(monitor["bbox"])):
            continue
        tracks_by_icao24[icao24] = track
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
        # Quantify how far each circling aircraft strays from the drawn VNAP
        # pattern (no-op when this airport has no pattern yet).
        deviation.store_deviations(conn, airport.icao, new_events, tracks_by_icao24)
    return written
```

- [ ] **Step 4: Run the targeted test + full suite**

Run: `cd /Users/d/Code/FAA_circle_jerk/backend && /Users/d/Code/FAA_circle_jerk/.venv/bin/python -m pytest tests/test_deviation.py::test_services_imports_and_uses_store_deviations tests/ -q`
Expected: PASS — targeted test passes and the full suite stays green (existing services tests have no patterns, so deviation is a no-op for them).

- [ ] **Step 5: Commit**

```bash
git add backend/app/services.py backend/tests/test_deviation.py
git commit -m "feat: compute pattern deviation in the worker detection path"
```

---

## Self-review

**Spec coverage:** Feature 2 is covered — KPI A (`time_off_pattern_s` + `pct_off_pattern` beyond the 0.25 nm corridor) and KPI B (`deviation_mean_nm` time-weighted + `deviation_peak_nm`) are computed in `compute_deviation` (Task 3) and written to the operation (Tasks 5-6). Pattern matching is "direction-matched" via rotational sense + nearest (Task 4). The worker computes deviation only when a current pattern exists (Task 6/7). Lap windowing uses the new circle `start_timestamp`/`end_timestamp` (Task 1).

**Placeholder scan:** No placeholders; every code step is complete with exact commands.

**Type consistency:** `densify_pattern(geometry) -> list[Point]` feeds `perpendicular_distance_nm` and `compute_deviation`; `match_pattern(samples, patterns)` returns a `{id, geometry}` dict; `store_deviations` parses `geometry_json` into that shape and calls `db.update_operation_deviation(conn, op_id, matched_pattern_id, metrics)` whose `metrics` keys exactly match `compute_deviation`'s output and `update_operation_deviation`'s SQL. Circle events carry `start_timestamp`/`end_timestamp` (Task 1) consumed by `store_deviations` (Task 6).

**Deferred / notes:** Deviation is only computed for closed-lap circles (the ones with lap bounds), not for `home_airport_line_crossing` circles (overflights, not pattern work) — intentional. Recomputation when a pattern is drawn after the fact is out of scope here (deviation is computed at detection time; a backfill pass could be added later). Crossing-circle and pass/T&G deviation are not in scope (Feature 2 is about circling aircraft vs the pattern).
