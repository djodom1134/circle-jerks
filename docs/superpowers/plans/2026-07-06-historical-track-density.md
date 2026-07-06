# Historical Track Density View Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a map overlay that renders 1–7 days of archived aircraft tracks around an airport as thinned individual lines, a traffic-density heatmap, or averaged representative paths — toggled by a new button with a timeframe + mode selector.

**Architecture:** Extend the SQLite cold-tier retention to 7 days. Add a new `GET /airports/{icao}/track-history` endpoint that reads all archived samples in the airport bbox over N days, splits each aircraft into flights, RDP-simplifies them, and returns capped per-flight polylines. The React map renders all three visualization modes client-side from that single payload (density reuses the existing heatmap-canvas rasterization pattern; average-path is a tested geometry function).

**Tech Stack:** Backend — Python 3, FastAPI, SQLite (`app/db.py`, `app/settings.py`, `app/archive.py`, `app/main.py`). Frontend — React + TypeScript + OpenLayers + Vite + Vitest (`frontend/src`).

## Global Constraints

- Backend env prefix is `CIRCLEJERK_` (pydantic-settings `env_prefix`). New settings map automatically, e.g. `track_archive_horizon_days` → `CIRCLEJERK_TRACK_ARCHIVE_HORIZON_DAYS`.
- Backend tests run from the `backend/` directory: `python -m pytest tests/... -v` (config: `pythonpath=["."]`, `asyncio_mode=auto`).
- Frontend tests run from `frontend/`: `npm run test` (Vitest). Build/typecheck: `npm run build` (`tsc && vite build`).
- Frontend API calls go through `getJson<T>(path)` in `frontend/src/lib/api.ts`; `API_BASE` defaults to `/api`, so a client path `/airports/KBJC/track-history` fetches `/api/airports/KBJC/track-history`.
- Altitude ceiling is expressed **AGL** at the API boundary and converted to **MSL** (`airport.elevation_ft + ceiling_agl`) before the archive query, because `track_archive.altitude_ft` is MSL.
- Track archive columns available per sample: `icao24, timestamp, lat, lon, altitude_ft, baro_altitude_ft, geo_altitude_ft, heading_deg, vertical_rate_fpm, callsign, in_window, source`.
- History accrues **forward from deploy** — the endpoint returns only what is already archived; older days are legitimately empty until data accumulates.
- Commit after every task. Commit message convention ends with the repo's `Co-Authored-By` trailer used on recent commits.

---

### Task 1: Configurable cold-archive retention horizon (7 days)

**Files:**
- Modify: `backend/app/settings.py` (add setting near `track_ttl_seconds` ~line 72)
- Modify: `backend/app/archive.py` (add `horizon_seconds` helper near the top-level constants ~line 39)
- Modify: `backend/app/worker.py:103` (pass the horizon into `archive_loop`)
- Test: `backend/tests/test_archive.py`

**Interfaces:**
- Produces: `settings.track_archive_horizon_days: int` (default `7`); `archive.horizon_seconds(settings: Settings) -> int` returning `track_archive_horizon_days * 86400`.

- [ ] **Step 1: Write the failing test**

Add to `backend/tests/test_archive.py`:

```python
def test_horizon_seconds_defaults_to_seven_days():
    from app.settings import Settings
    s = Settings(database_path=":memory:")
    assert s.track_archive_horizon_days == 7
    assert archive.horizon_seconds(s) == 7 * 86400


def test_horizon_seconds_honors_override():
    from app.settings import Settings
    s = Settings(database_path=":memory:", track_archive_horizon_days=3)
    assert archive.horizon_seconds(s) == 3 * 86400
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_archive.py::test_horizon_seconds_defaults_to_seven_days -v`
Expected: FAIL — `AttributeError: 'Settings' object has no attribute 'track_archive_horizon_days'`.

- [ ] **Step 3: Add the setting**

In `backend/app/settings.py`, immediately after the `track_ttl_seconds` field (~line 72):

```python
    # Cold-tier (SQLite track_archive) retention. The historical track-density
    # view reads up to this many days back; prune deletes older. Bounded by
    # disk, not Redis memory — safe to keep a week of a single field's traffic.
    track_archive_horizon_days: int = 7
```

- [ ] **Step 4: Add the helper**

In `backend/app/archive.py`, after `DEFAULT_ARCHIVE_HORIZON_SECONDS` (~line 40):

```python
def horizon_seconds(settings: Settings) -> int:
    """Cold-archive retention horizon in seconds, from the configurable
    day count. Used for both the archive tail-window and the prune cutoff."""
    return int(settings.track_archive_horizon_days) * 86400
```

- [ ] **Step 5: Wire it into the worker**

In `backend/app/worker.py`, change line 103 from:

```python
    archive_task = asyncio.create_task(track_archive.archive_loop(store, settings))
```

to:

```python
    archive_task = asyncio.create_task(
        track_archive.archive_loop(
            store,
            settings,
            archive_horizon_seconds=track_archive.horizon_seconds(settings),
        )
    )
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_archive.py -v`
Expected: PASS (all archive tests, including the two new ones).

- [ ] **Step 7: Commit**

```bash
git add backend/app/settings.py backend/app/archive.py backend/app/worker.py backend/tests/test_archive.py
git commit -m "feat(archive): configurable 7-day cold-tier retention horizon

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: `read_track_archive_bbox` — area-wide archive query

**Files:**
- Modify: `backend/app/db.py` (add after `bulk_read_track_archive`, before `prune_track_archive`)
- Test: `backend/tests/test_archive.py`

**Interfaces:**
- Consumes: `_TRACK_ARCHIVE_COLUMNS`, `_track_archive_row_to_sample` (existing in `db.py`).
- Produces:
  ```python
  def read_track_archive_bbox(
      conn: sqlite3.Connection,
      min_lat: float, max_lat: float, min_lon: float, max_lon: float,
      start_ts: int, end_ts: int,
      ceiling_ft_msl: float | None = None,
      row_cap: int = 200_000,
  ) -> list[dict]
  ```
  Returns sample dicts (via `_track_archive_row_to_sample`) for **all** icao24 in the bbox + time range, ordered `icao24 ASC, timestamp ASC`. Samples with `altitude_ft` above `ceiling_ft_msl` are excluded; NULL-altitude samples are **kept**. Hard `LIMIT row_cap`.

- [ ] **Step 1: Write the failing test**

Add to `backend/tests/test_archive.py`:

```python
def test_read_track_archive_bbox_filters_area_time_and_ceiling(temp_db_path):
    with db.db_session(temp_db_path) as conn:
        db.archive_track_samples(conn, "AAA111", [
            {"timestamp": 1000, "lat": 40.10, "lon": -105.10, "altitude_ft": 4000},
            {"timestamp": 1030, "lat": 40.11, "lon": -105.11, "altitude_ft": 4200},
        ])
        # Outside bbox (lon far west):
        db.archive_track_samples(conn, "BBB222", [
            {"timestamp": 1000, "lat": 40.10, "lon": -108.00, "altitude_ft": 4000},
        ])
        # Above ceiling:
        db.archive_track_samples(conn, "CCC333", [
            {"timestamp": 1000, "lat": 40.10, "lon": -105.10, "altitude_ft": 35000},
        ])
        # Unknown altitude — should be kept:
        db.archive_track_samples(conn, "DDD444", [
            {"timestamp": 1000, "lat": 40.10, "lon": -105.10, "altitude_ft": None},
        ])
        conn.commit()
        rows = db.read_track_archive_bbox(
            conn,
            min_lat=40.0, max_lat=40.2, min_lon=-105.3, max_lon=-105.0,
            start_ts=900, end_ts=1100, ceiling_ft_msl=6000,
        )
    icaos = {r["icao24"] for r in rows}
    assert icaos == {"aaa111", "ddd444"}
    assert [r["timestamp"] for r in rows if r["icao24"] == "aaa111"] == [1000, 1030]


def test_read_track_archive_bbox_row_cap(temp_db_path):
    with db.db_session(temp_db_path) as conn:
        db.archive_track_samples(conn, "AAA111", [
            {"timestamp": 1000 + i, "lat": 40.1, "lon": -105.1, "altitude_ft": 3000}
            for i in range(10)
        ])
        conn.commit()
        rows = db.read_track_archive_bbox(
            conn, 40.0, 40.2, -105.3, -105.0, 900, 2000, row_cap=4,
        )
    assert len(rows) == 4
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_archive.py::test_read_track_archive_bbox_filters_area_time_and_ceiling -v`
Expected: FAIL — `AttributeError: module 'app.db' has no attribute 'read_track_archive_bbox'`.

- [ ] **Step 3: Implement the query**

In `backend/app/db.py`, add after `bulk_read_track_archive` (before `def prune_track_archive`):

```python
def read_track_archive_bbox(
    conn: sqlite3.Connection,
    min_lat: float,
    max_lat: float,
    min_lon: float,
    max_lon: float,
    start_ts: int,
    end_ts: int,
    ceiling_ft_msl: float | None = None,
    row_cap: int = 200_000,
) -> list[dict]:
    """All archived samples inside a bbox + time range, across every aircraft.

    Ordered by (icao24, timestamp) so callers can group consecutive rows into
    per-aircraft tracks. Samples above `ceiling_ft_msl` are dropped; samples
    with NULL altitude are kept (unknown altitude shouldn't hide a track).
    `row_cap` is a hard LIMIT so a runaway range can't wedge the process.
    """
    ceiling_clause = ""
    params: list = [
        float(min_lat), float(max_lat), float(min_lon), float(max_lon),
        int(start_ts), int(end_ts),
    ]
    if ceiling_ft_msl is not None:
        ceiling_clause = " AND (altitude_ft IS NULL OR altitude_ft <= ?)"
        params.append(float(ceiling_ft_msl))
    params.append(int(row_cap))
    rows = conn.execute(
        f"""
        SELECT {', '.join(_TRACK_ARCHIVE_COLUMNS)}
        FROM track_archive
        WHERE lat BETWEEN ? AND ?
          AND lon BETWEEN ? AND ?
          AND timestamp BETWEEN ? AND ?
          {ceiling_clause}
        ORDER BY icao24 ASC, timestamp ASC
        LIMIT ?
        """,
        params,
    ).fetchall()
    return [_track_archive_row_to_sample(row) for row in rows]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_archive.py -k bbox -v`
Expected: PASS (both bbox tests).

- [ ] **Step 5: Commit**

```bash
git add backend/app/db.py backend/tests/test_archive.py
git commit -m "feat(db): read_track_archive_bbox area-wide archived-track query

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: Ramer–Douglas–Peucker polyline simplification

**Files:**
- Create: `backend/app/simplify.py`
- Test: `backend/tests/test_simplify.py`

**Interfaces:**
- Produces: `simplify.rdp_keep_mask(points: list[tuple[float, float]], epsilon: float) -> list[bool]` — returns one bool per input point (True = keep). First and last points are always kept. `epsilon` is the max perpendicular distance (same units as the coordinates; used in degrees here) below which intermediate points are dropped. Inputs of length ≤ 2 return all-True.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_simplify.py`:

```python
from app.simplify import rdp_keep_mask


def test_collinear_points_reduce_to_endpoints():
    pts = [(0.0, 0.0), (1.0, 1.0), (2.0, 2.0), (3.0, 3.0), (4.0, 4.0)]
    assert rdp_keep_mask(pts, epsilon=0.01) == [True, False, False, False, True]


def test_point_off_line_is_kept():
    pts = [(0.0, 0.0), (1.0, 5.0), (2.0, 0.0)]
    assert rdp_keep_mask(pts, epsilon=0.5) == [True, True, True]


def test_short_inputs_all_kept():
    assert rdp_keep_mask([], 0.1) == []
    assert rdp_keep_mask([(0.0, 0.0)], 0.1) == [True]
    assert rdp_keep_mask([(0.0, 0.0), (1.0, 1.0)], 0.1) == [True, True]


def test_endpoints_always_kept_even_below_epsilon():
    pts = [(0.0, 0.0), (0.5, 0.0001), (1.0, 0.0)]
    mask = rdp_keep_mask(pts, epsilon=0.01)
    assert mask[0] is True and mask[-1] is True
    assert mask[1] is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_simplify.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.simplify'`.

- [ ] **Step 3: Implement the module**

Create `backend/app/simplify.py`:

```python
"""Ramer–Douglas–Peucker polyline simplification.

Reduces a track's point count while preserving its shape within a tolerance,
so the track-history endpoint can ship far fewer points without visibly
changing the path. Operates on plain (x, y) tuples — the caller passes
(lat, lon) and interprets epsilon in degrees.
"""

from __future__ import annotations


def _perpendicular_distance(
    pt: tuple[float, float],
    line_start: tuple[float, float],
    line_end: tuple[float, float],
) -> float:
    (x, y), (x1, y1), (x2, y2) = pt, line_start, line_end
    dx, dy = x2 - x1, y2 - y1
    if dx == 0 and dy == 0:
        return ((x - x1) ** 2 + (y - y1) ** 2) ** 0.5
    # Distance from point to the infinite line through (x1,y1)-(x2,y2).
    num = abs(dy * x - dx * y + x2 * y1 - y2 * x1)
    den = (dx * dx + dy * dy) ** 0.5
    return num / den


def rdp_keep_mask(points: list[tuple[float, float]], epsilon: float) -> list[bool]:
    """Return a keep/drop mask (one bool per point). Endpoints always kept."""
    n = len(points)
    if n <= 2:
        return [True] * n
    keep = [False] * n
    keep[0] = True
    keep[n - 1] = True
    # Iterative RDP over index ranges to avoid recursion-depth limits on long
    # tracks.
    stack: list[tuple[int, int]] = [(0, n - 1)]
    while stack:
        start, end = stack.pop()
        if end <= start + 1:
            continue
        dmax = -1.0
        index = start
        for i in range(start + 1, end):
            d = _perpendicular_distance(points[i], points[start], points[end])
            if d > dmax:
                dmax = d
                index = i
        if dmax > epsilon:
            keep[index] = True
            stack.append((start, index))
            stack.append((index, end))
    return keep
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_simplify.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add backend/app/simplify.py backend/tests/test_simplify.py
git commit -m "feat(simplify): RDP polyline simplification for track history

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: `track_history` module — flight splitting + track building

**Files:**
- Create: `backend/app/track_history.py`
- Test: `backend/tests/test_track_history.py`

**Interfaces:**
- Consumes: `simplify.rdp_keep_mask` (Task 3).
- Produces:
  - Constants: `GAP_SECONDS = 1200`, `DEFAULT_CEILING_FT_AGL = 5000`, `DEFAULT_TRACK_CAP = 1500`, `SIMPLIFY_EPSILON_DEG = 1e-4`, `MIN_DAYS = 1`, `MAX_DAYS = 7`.
  - `split_into_flights(samples: list[dict], gap_seconds: int = GAP_SECONDS) -> list[list[dict]]` — splits a single aircraft's time-sorted samples into separate flights wherever consecutive timestamps differ by more than `gap_seconds`.
  - `build_tracks(rows: list[dict], *, epsilon: float = SIMPLIFY_EPSILON_DEG, gap_seconds: int = GAP_SECONDS, track_cap: int = DEFAULT_TRACK_CAP) -> tuple[list[dict], int]` — groups `rows` (already ordered by icao24, timestamp) by icao24, splits each into flights, simplifies each flight, and returns `(tracks, total_tracks)` where `tracks` is capped to the most recent `track_cap` flights and each track is `{"icao24": str, "callsign": str | None, "samples": [{"lat","lon","altitude_ft","timestamp"}]}`. `total_tracks` is the count before capping.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_track_history.py`:

```python
from app import track_history as th


def _s(ts, lat=40.1, lon=-105.1, alt=3000, icao="aaa111", cs="N1"):
    return {"icao24": icao, "timestamp": ts, "lat": lat, "lon": lon,
            "altitude_ft": alt, "callsign": cs}


def test_split_into_flights_breaks_on_gap():
    samples = [_s(1000), _s(1030), _s(1060), _s(9000), _s(9030)]
    flights = th.split_into_flights(samples, gap_seconds=1200)
    assert [len(f) for f in flights] == [3, 2]


def test_split_into_flights_single_flight_when_dense():
    samples = [_s(1000), _s(1030), _s(1060)]
    assert len(th.split_into_flights(samples, gap_seconds=1200)) == 1


def test_build_tracks_groups_splits_and_simplifies():
    rows = [
        # aaa111: one flight, collinear middle point should be simplified out
        _s(1000, lat=40.10, lon=-105.10),
        _s(1030, lat=40.11, lon=-105.11),
        _s(1060, lat=40.12, lon=-105.12),
        # bbb222: two flights (gap)
        _s(1000, icao="bbb222", cs="N2"),
        _s(9000, icao="bbb222", cs="N2"),
        _s(9030, icao="bbb222", cs="N2"),
    ]
    tracks, total = th.build_tracks(rows)
    # aaa111 -> 1 flight; bbb222 -> 2 flights (one of them a singleton, dropped)
    # Singleton flights (<2 points) are dropped.
    assert total == 2
    by_icao = {t["icao24"] for t in tracks}
    assert by_icao == {"aaa111", "bbb222"}
    aaa = next(t for t in tracks if t["icao24"] == "aaa111")
    # collinear -> only endpoints survive
    assert len(aaa["samples"]) == 2
    assert aaa["samples"][0]["timestamp"] == 1000
    assert aaa["samples"][-1]["timestamp"] == 1060


def test_build_tracks_caps_and_reports_total():
    rows = []
    for i in range(10):
        icao = f"a{i:05d}"
        rows.append(_s(1000, icao=icao))
        rows.append(_s(1030, icao=icao))
    tracks, total = th.build_tracks(rows, track_cap=4)
    assert total == 10
    assert len(tracks) == 4
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_track_history.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.track_history'`.

- [ ] **Step 3: Implement the module**

Create `backend/app/track_history.py`:

```python
"""Build simplified per-flight polylines for the historical track-density view.

Reads come from db.read_track_archive_bbox (all aircraft in a bbox over N
days). Here we group those rows into per-aircraft tracks, split each aircraft
into separate flights on time gaps, and simplify each flight's geometry.
"""

from __future__ import annotations

from itertools import groupby

from .simplify import rdp_keep_mask

GAP_SECONDS = 1200            # >20 min gap => a separate flight
DEFAULT_CEILING_FT_AGL = 5000
DEFAULT_TRACK_CAP = 1500
SIMPLIFY_EPSILON_DEG = 1e-4   # ~11 m; imperceptible at map zoom, big point savings
MIN_DAYS = 1
MAX_DAYS = 7


def split_into_flights(samples: list[dict], gap_seconds: int = GAP_SECONDS) -> list[list[dict]]:
    """Split one aircraft's time-sorted samples into flights on time gaps."""
    if not samples:
        return []
    flights: list[list[dict]] = []
    current: list[dict] = [samples[0]]
    for prev, sample in zip(samples, samples[1:]):
        if sample["timestamp"] - prev["timestamp"] > gap_seconds:
            flights.append(current)
            current = [sample]
        else:
            current.append(sample)
    flights.append(current)
    return flights


def _simplify_flight(flight: list[dict], epsilon: float) -> dict | None:
    """Simplify a single flight's polyline. Returns a track dict or None if it
    collapses below 2 points."""
    if len(flight) < 2:
        return None
    coords = [(s["lat"], s["lon"]) for s in flight]
    mask = rdp_keep_mask(coords, epsilon)
    kept = [s for s, keep in zip(flight, mask) if keep]
    if len(kept) < 2:
        return None
    return {
        "icao24": flight[0]["icao24"],
        "callsign": flight[0].get("callsign"),
        "samples": [
            {
                "lat": s["lat"],
                "lon": s["lon"],
                "altitude_ft": s.get("altitude_ft"),
                "timestamp": s["timestamp"],
            }
            for s in kept
        ],
    }


def build_tracks(
    rows: list[dict],
    *,
    epsilon: float = SIMPLIFY_EPSILON_DEG,
    gap_seconds: int = GAP_SECONDS,
    track_cap: int = DEFAULT_TRACK_CAP,
) -> tuple[list[dict], int]:
    """Group bbox rows into simplified per-flight tracks.

    `rows` must be ordered by (icao24, timestamp) — as read_track_archive_bbox
    returns them. Returns (tracks, total_tracks) where tracks is capped to the
    most recent `track_cap` flights (by last-sample timestamp).
    """
    tracks: list[dict] = []
    for _icao, group in groupby(rows, key=lambda r: r["icao24"]):
        aircraft_samples = list(group)
        for flight in split_into_flights(aircraft_samples, gap_seconds):
            track = _simplify_flight(flight, epsilon)
            if track is not None:
                tracks.append(track)
    total = len(tracks)
    if total > track_cap:
        # Keep the most recent flights by their last sample's timestamp.
        tracks.sort(key=lambda t: t["samples"][-1]["timestamp"], reverse=True)
        tracks = tracks[:track_cap]
    return tracks, total
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_track_history.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add backend/app/track_history.py backend/tests/test_track_history.py
git commit -m "feat(track-history): flight splitting + simplified track building

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: `GET /airports/{icao}/track-history` endpoint

**Files:**
- Modify: `backend/app/main.py` (add endpoint after `get_airport_stats`, ~line 675; imports at top)
- Test: `backend/tests/test_api.py`

**Interfaces:**
- Consumes: `db.get_airport`, `db.read_track_archive_bbox` (Task 2), `geo.bbox_for_radius` (returns `(min_lat, min_lon, max_lat, max_lon)`), `track_history.build_tracks` + its constants (Task 4).
- Produces: `GET /airports/{icao}/track-history?days=<1-7>&ceiling_ft=<int>` → JSON:
  ```json
  {
    "airport": {"icao": "KBJC", "lat": 0, "lon": 0, "elevation_ft": 0},
    "days": 7,
    "ceiling_ft": 5000,
    "window": {"start_ts": 0, "end_ts": 0},
    "tracks": [{"icao24": "...", "callsign": "...", "samples": [{"lat": 0, "lon": 0, "altitude_ft": 0, "timestamp": 0}]}],
    "total_tracks": 0,
    "truncated": false
  }
  ```
  Unknown airport → HTTP 404.

- [ ] **Step 1: Write the failing test**

Add to `backend/tests/test_api.py`:

```python
def test_track_history_endpoint(tmp_path, monkeypatch):
    monkeypatch.setenv("CIRCLEJERK_DATABASE_PATH", str(tmp_path / "circlejerk.sqlite3"))
    monkeypatch.setenv("CIRCLEJERK_REDIS_URL", "memory://")
    monkeypatch.setenv("CIRCLEJERK_ENVIRONMENT", "test")
    get_settings.cache_clear()
    from app import db
    settings = get_settings()
    db.init_db(settings.database_path)
    with db.db_session(settings.database_path) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO airports (icao, iata, name, city, country, lat, lon, elevation_ft, is_towered) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("KTST", "TST", "Test Field", "Testville", "US", 40.1, -105.1, 5000, 0),
        )
        # Anchor sample timestamps just before NOW so they fall inside the
        # [NOW - days*86400, NOW] window.
        now = 2000000000
        db.archive_track_samples(conn, "AAA111", [
            {"timestamp": now - 3600, "lat": 40.10, "lon": -105.10, "altitude_ft": 6000},
            {"timestamp": now - 3570, "lat": 40.11, "lon": -105.11, "altitude_ft": 6100},
            {"timestamp": now - 3540, "lat": 40.12, "lon": -105.12, "altitude_ft": 6200},
        ])
        # Airliner far above the AGL ceiling (elev 5000 + 5000 = 10000 MSL):
        db.archive_track_samples(conn, "BBB222", [
            {"timestamp": now - 3600, "lat": 40.10, "lon": -105.10, "altitude_ft": 35000},
            {"timestamp": now - 3570, "lat": 40.11, "lon": -105.11, "altitude_ft": 35000},
        ])
        conn.commit()

    with TestClient(app) as client:
        resp = client.get(
            "/api/airports/KTST/track-history?days=7&ceiling_ft=5000&_now=2000000000"
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["airport"]["icao"] == "KTST"
        assert body["days"] == 7
        assert body["ceiling_ft"] == 5000
        icaos = {t["icao24"] for t in body["tracks"]}
        assert icaos == {"aaa111"}          # airliner filtered by ceiling
        assert body["total_tracks"] == 1
        assert body["truncated"] is False

    with TestClient(app) as client:
        assert client.get("/api/airports/ZZZZ/track-history").status_code == 404
    get_settings.cache_clear()
```

Note: the endpoint accepts an optional `_now` query param (epoch seconds) used **only in tests** to make the time window deterministic; in production it defaults to `time.time()`.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_api.py::test_track_history_endpoint -v`
Expected: FAIL — 404 or 422 (route not defined).

- [ ] **Step 3: Add imports**

In `backend/app/main.py`, ensure these are imported near the existing `from . import db, patterns` (line 29) and other imports:

```python
from . import db, patterns, track_history
from .geo import bbox_for_radius
```

(If `geo` or `track_history` is already imported, don't duplicate.)

- [ ] **Step 4: Implement the endpoint**

In `backend/app/main.py`, after `get_airport_stats` (~line 675):

```python
# Radius (nm) of the area we pull historical tracks for — matches the scan ring.
_TRACK_HISTORY_RING_NM = 8.0


@app.get("/airports/{icao}/track-history")
async def get_airport_track_history(
    icao: str,
    settings: Annotated[Settings, Depends(settings_dep)],
    days: Annotated[int, Query(ge=track_history.MIN_DAYS, le=track_history.MAX_DAYS)] = 7,
    ceiling_ft: Annotated[int, Query(ge=0, le=60000)] = track_history.DEFAULT_CEILING_FT_AGL,
    _now: Annotated[int | None, Query()] = None,
):
    """Simplified per-flight polylines for all traffic in the airport ring over
    the last `days` days, below `ceiling_ft` AGL. Feeds the historical
    track-density map overlay."""
    now = int(_now) if _now is not None else int(time.time())
    start_ts = now - days * 86400
    with db_session(settings.database_path) as conn:
        airport = db.get_airport(conn, icao)
        if airport is None:
            raise HTTPException(status_code=404, detail="airport not found")
        min_lat, min_lon, max_lat, max_lon = bbox_for_radius(
            airport.lat, airport.lon, _TRACK_HISTORY_RING_NM
        )
        ceiling_msl = airport.elevation_ft + ceiling_ft
        rows = db.read_track_archive_bbox(
            conn, min_lat, max_lat, min_lon, max_lon, start_ts, now,
            ceiling_ft_msl=ceiling_msl,
        )
    tracks, total_tracks = track_history.build_tracks(rows)
    return {
        "airport": {
            "icao": airport.icao,
            "lat": airport.lat,
            "lon": airport.lon,
            "elevation_ft": airport.elevation_ft,
        },
        "days": days,
        "ceiling_ft": ceiling_ft,
        "window": {"start_ts": start_ts, "end_ts": now},
        "tracks": tracks,
        "total_tracks": total_tracks,
        "truncated": total_tracks > len(tracks),
    }
```

Confirm `HTTPException` and `Query` are already imported in `main.py` (they are used by existing endpoints). If not, add `from fastapi import HTTPException, Query` to the existing FastAPI imports.

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_api.py::test_track_history_endpoint -v`
Expected: PASS.

- [ ] **Step 6: Run the full backend suite (no regressions)**

Run: `cd backend && python -m pytest -q`
Expected: PASS (all tests).

- [ ] **Step 7: Commit**

```bash
git add backend/app/main.py backend/tests/test_api.py
git commit -m "feat(api): GET /airports/{icao}/track-history endpoint

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: Frontend API client + types

**Files:**
- Modify: `frontend/src/lib/api.ts` (add types + function near `getAirportStats`, ~line 856)
- Test: `frontend/src/lib/trackHistoryApi.test.ts`

**Interfaces:**
- Consumes: `getJson<T>` (existing).
- Produces:
  ```ts
  export interface HistoricalTrackSample { lat: number; lon: number; altitude_ft: number | null; timestamp: number; }
  export interface HistoricalTrack { icao24: string; callsign: string | null; samples: HistoricalTrackSample[]; }
  export interface TrackHistoryResponse {
    airport: { icao: string; lat: number; lon: number; elevation_ft: number };
    days: number; ceiling_ft: number;
    window: { start_ts: number; end_ts: number };
    tracks: HistoricalTrack[]; total_tracks: number; truncated: boolean;
  }
  export function getTrackHistory(icao: string, days: number, ceilingFt?: number): Promise<TrackHistoryResponse>;
  ```

- [ ] **Step 1: Write the failing test**

Create `frontend/src/lib/trackHistoryApi.test.ts`:

```ts
import { afterEach, describe, expect, it, vi } from "vitest";
import { getTrackHistory } from "./api";

afterEach(() => vi.unstubAllGlobals());

describe("getTrackHistory", () => {
  it("requests the track-history path with days and ceiling params", async () => {
    const fn = vi.fn(async () => ({ ok: true, json: async () => ({ tracks: [] }) } as Response));
    vi.stubGlobal("fetch", fn);
    await getTrackHistory("KBJC", 3, 4000);
    const url = String((fn.mock.calls as unknown[][])[0][0]);
    expect(url).toContain("/api/airports/KBJC/track-history?days=3&ceiling_ft=4000");
  });

  it("defaults the ceiling to 5000", async () => {
    const fn = vi.fn(async () => ({ ok: true, json: async () => ({ tracks: [] }) } as Response));
    vi.stubGlobal("fetch", fn);
    await getTrackHistory("KBJC", 7);
    const url = String((fn.mock.calls as unknown[][])[0][0]);
    expect(url).toContain("ceiling_ft=5000");
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npm run test -- trackHistoryApi`
Expected: FAIL — `getTrackHistory` is not exported.

- [ ] **Step 3: Implement the client**

In `frontend/src/lib/api.ts`, after `getAirportStats` (~line 858):

```ts
export interface HistoricalTrackSample {
  lat: number;
  lon: number;
  altitude_ft: number | null;
  timestamp: number;
}

export interface HistoricalTrack {
  icao24: string;
  callsign: string | null;
  samples: HistoricalTrackSample[];
}

export interface TrackHistoryResponse {
  airport: { icao: string; lat: number; lon: number; elevation_ft: number };
  days: number;
  ceiling_ft: number;
  window: { start_ts: number; end_ts: number };
  tracks: HistoricalTrack[];
  total_tracks: number;
  truncated: boolean;
}

export function getTrackHistory(icao: string, days: number, ceilingFt = 5000) {
  return getJson<TrackHistoryResponse>(
    `/airports/${encodeURIComponent(icao)}/track-history?days=${days}&ceiling_ft=${ceilingFt}`
  );
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npm run test -- trackHistoryApi`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add frontend/src/lib/api.ts frontend/src/lib/trackHistoryApi.test.ts
git commit -m "feat(api-client): getTrackHistory + track history types

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 7: Average-path geometry library

**Files:**
- Create: `frontend/src/lib/averagePath.ts`
- Test: `frontend/src/lib/averagePath.test.ts`

**Interfaces:**
- Produces:
  ```ts
  export type LonLat = [number, number];
  export interface AveragedPath { points: LonLat[]; count: number; }
  export function resamplePath(points: LonLat[], n: number): LonLat[];
  export function bearingBucket(bearingDeg: number, buckets: number): number;
  export function averagePaths(
    tracks: Array<{ samples: Array<{ lon: number; lat: number }> }>,
    airport: { lat: number; lon: number },
    opts?: { buckets?: number; resampleN?: number; minCount?: number }
  ): AveragedPath[];
  ```
  `resamplePath` returns `n` points evenly spaced by arc length (first/last preserved; a degenerate all-same-point input returns that point repeated). `averagePaths` buckets tracks by the compass bearing from the airport to each track's centroid, orients each track so index 0 is the endpoint farther from the airport, resamples to `resampleN`, and averages component-wise per bucket that has ≥ `minCount` tracks. Result sorted by `count` descending.

- [ ] **Step 1: Write the failing test**

Create `frontend/src/lib/averagePath.test.ts`:

```ts
import { describe, expect, it } from "vitest";
import { averagePaths, bearingBucket, resamplePath } from "./averagePath";

describe("resamplePath", () => {
  it("resamples a straight line to evenly spaced points", () => {
    const out = resamplePath([[0, 0], [0, 10]], 3);
    expect(out).toHaveLength(3);
    expect(out[0]).toEqual([0, 0]);
    expect(out[2]).toEqual([0, 10]);
    expect(out[1][1]).toBeCloseTo(5, 5);
  });

  it("handles a degenerate single-point path", () => {
    const out = resamplePath([[2, 3], [2, 3]], 4);
    expect(out).toHaveLength(4);
    out.forEach((p) => expect(p).toEqual([2, 3]));
  });
});

describe("bearingBucket", () => {
  it("maps bearings into sector indices", () => {
    expect(bearingBucket(0, 8)).toBe(0);
    expect(bearingBucket(359, 8)).toBe(0);
    expect(bearingBucket(90, 8)).toBe(2);
    expect(bearingBucket(180, 4)).toBe(2);
  });
});

describe("averagePaths", () => {
  it("averages two parallel tracks into a mid path", () => {
    const airport = { lat: 0, lon: 0 };
    const tracks = [
      { samples: [{ lon: 1, lat: 1.0 }, { lon: 2, lat: 1.0 }] },
      { samples: [{ lon: 1, lat: 1.2 }, { lon: 2, lat: 1.2 }] },
    ];
    const out = averagePaths(tracks, airport, { buckets: 8, resampleN: 2, minCount: 2 });
    expect(out).toHaveLength(1);
    expect(out[0].count).toBe(2);
    // both endpoints averaged to lat 1.1
    out[0].points.forEach((p) => expect(p[1]).toBeCloseTo(1.1, 5));
  });

  it("drops buckets below minCount", () => {
    const airport = { lat: 0, lon: 0 };
    const tracks = [{ samples: [{ lon: 1, lat: 1 }, { lon: 2, lat: 1 }] }];
    expect(averagePaths(tracks, airport, { minCount: 2 })).toHaveLength(0);
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npm run test -- averagePath`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement the library**

Create `frontend/src/lib/averagePath.ts`:

```ts
// Groups many tracks into a few "typical" representative paths for the
// historical-density Average mode. Bucketing is by the compass bearing from
// the airport to each track's centroid, so inbound corridors from different
// directions don't get averaged together into mush.

export type LonLat = [number, number];

export interface AveragedPath {
  points: LonLat[];
  count: number;
}

function dist2(a: { lon: number; lat: number }, b: { lon: number; lat: number }): number {
  const dx = a.lon - b.lon;
  const dy = a.lat - b.lat;
  return dx * dx + dy * dy;
}

export function resamplePath(points: LonLat[], n: number): LonLat[] {
  if (n < 2 || points.length === 0) {
    return points.slice(0, Math.max(1, n));
  }
  if (points.length === 1) {
    return Array.from({ length: n }, () => [points[0][0], points[0][1]] as LonLat);
  }
  const cum: number[] = [0];
  for (let i = 1; i < points.length; i += 1) {
    const dx = points[i][0] - points[i - 1][0];
    const dy = points[i][1] - points[i - 1][1];
    cum.push(cum[i - 1] + Math.hypot(dx, dy));
  }
  const total = cum[cum.length - 1];
  if (total === 0) {
    return Array.from({ length: n }, () => [points[0][0], points[0][1]] as LonLat);
  }
  const out: LonLat[] = [];
  let seg = 1;
  for (let i = 0; i < n; i += 1) {
    const target = (total * i) / (n - 1);
    while (seg < points.length - 1 && cum[seg] < target) seg += 1;
    const segStart = cum[seg - 1];
    const segLen = cum[seg] - segStart || 1;
    const t = Math.max(0, Math.min(1, (target - segStart) / segLen));
    out.push([
      points[seg - 1][0] + (points[seg][0] - points[seg - 1][0]) * t,
      points[seg - 1][1] + (points[seg][1] - points[seg - 1][1]) * t,
    ]);
  }
  return out;
}

export function bearingBucket(bearingDeg: number, buckets: number): number {
  const norm = ((bearingDeg % 360) + 360) % 360;
  return Math.floor((norm / 360) * buckets) % buckets;
}

function bearingFromAirport(
  airport: { lat: number; lon: number },
  point: { lat: number; lon: number }
): number {
  const dLon = ((point.lon - airport.lon) * Math.PI) / 180;
  const lat1 = (airport.lat * Math.PI) / 180;
  const lat2 = (point.lat * Math.PI) / 180;
  const y = Math.sin(dLon) * Math.cos(lat2);
  const x = Math.cos(lat1) * Math.sin(lat2) - Math.sin(lat1) * Math.cos(lat2) * Math.cos(dLon);
  return (Math.atan2(y, x) * 180) / Math.PI;
}

export function averagePaths(
  tracks: Array<{ samples: Array<{ lon: number; lat: number }> }>,
  airport: { lat: number; lon: number },
  opts?: { buckets?: number; resampleN?: number; minCount?: number }
): AveragedPath[] {
  const buckets = opts?.buckets ?? 8;
  const resampleN = opts?.resampleN ?? 40;
  const minCount = opts?.minCount ?? 3;

  const groups = new Map<number, LonLat[][]>();
  for (const track of tracks) {
    const samples = track.samples;
    if (samples.length < 2) continue;
    const centroid = {
      lon: samples.reduce((s, p) => s + p.lon, 0) / samples.length,
      lat: samples.reduce((s, p) => s + p.lat, 0) / samples.length,
    };
    const bucket = bearingBucket(bearingFromAirport(airport, centroid), buckets);
    // Orient so index 0 is the endpoint farther from the airport.
    const oriented = dist2(samples[0], airport) >= dist2(samples[samples.length - 1], airport)
      ? samples
      : samples.slice().reverse();
    const resampled = resamplePath(oriented.map((p) => [p.lon, p.lat] as LonLat), resampleN);
    const list = groups.get(bucket) ?? [];
    list.push(resampled);
    groups.set(bucket, list);
  }

  const out: AveragedPath[] = [];
  for (const list of groups.values()) {
    if (list.length < minCount) continue;
    const avg: LonLat[] = [];
    for (let i = 0; i < resampleN; i += 1) {
      let sx = 0;
      let sy = 0;
      for (const path of list) {
        sx += path[i][0];
        sy += path[i][1];
      }
      avg.push([sx / list.length, sy / list.length]);
    }
    out.push({ points: avg, count: list.length });
  }
  out.sort((a, b) => b.count - a.count);
  return out;
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd frontend && npm run test -- averagePath`
Expected: PASS (all describe blocks).

- [ ] **Step 5: Commit**

```bash
git add frontend/src/lib/averagePath.ts frontend/src/lib/averagePath.test.ts
git commit -m "feat(geometry): averagePath library for representative tracks

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 8: MapView — history props, overlay gating, Lines + Average rendering

**Files:**
- Modify: `frontend/src/components/MapView.tsx`

**Interfaces:**
- Consumes: `HistoricalTrack` (Task 6), `averagePaths` (Task 7), existing `colorFromAltitudeAgl`, `smoothSegment`, `lineFeature`, `fromLonLat`.
- Produces: `MapView` accepts new props `historyTracks?: HistoricalTrack[] | null` and `historyMode?: "lines" | "density" | "average" | null`. When a history mode is active, live aged-tracks and aircraft markers are suppressed; a dedicated vector layer renders `history_line` (Lines) or `history_avg` (Average) features. (Density canvas is Task 9.)

- [ ] **Step 1: Add imports and prop types**

At the top of `MapView.tsx`, extend the api type import (line 19) to include `HistoricalTrack`:

```ts
import type { Airport, HistoricalTrack, Offender, RunwayPattern, ScanResponse, TrackSample } from "../lib/api";
```

Add below the existing `smoothSegment` import:

```ts
import { averagePaths } from "../lib/averagePath";
```

Extend the `Props` interface (after `showHeatmap?: boolean;`):

```ts
  historyTracks?: HistoricalTrack[] | null;
  historyMode?: "lines" | "density" | "average" | null;
```

- [ ] **Step 2: Destructure new props and derive `historyActive`**

In the `MapView(...)` signature (line 516), add `historyTracks = null, historyMode = null` to the destructured props. Immediately after the `groundElevFt` line (~line 583) add:

```ts
  const historyActive = historyMode != null && historyTracks != null;
```

- [ ] **Step 3: Add the `history_line` / `history_avg` styles**

In `styleForFeature`, before the `if (kind === "aircraft")` block, add:

```ts
  if (kind === "history_line") {
    const altAgl = feature.get("alt_agl_ft");
    const [r, g, b] = colorFromAltitudeAgl(typeof altAgl === "number" ? altAgl : null);
    return new Style({
      stroke: new Stroke({ color: `rgba(${r}, ${g}, ${b}, 0.14)`, width: 1 }),
    });
  }
  if (kind === "history_avg") {
    return new Style({
      stroke: new Stroke({ color: "rgba(27, 58, 107, 0.9)", width: 4 }),
    });
  }
```

- [ ] **Step 4: Suppress live layers when a history mode is active**

In the `features` useMemo (line 585), change the heatmap early-return (line 597) from:

```ts
    if (showHeatmap) return rows;
```

to:

```ts
    if (showHeatmap || historyActive) return rows;
```

and add `historyActive` to its dependency array (line 620).

In the `aircraftTracks` useMemo (line 622), change:

```ts
    if (showHeatmap) return [];
```

to:

```ts
    if (showHeatmap || historyActive) return [];
```

and add `historyActive` to its dependency array (line 641).

- [ ] **Step 5: Add the history vector source + layer**

Add a ref alongside the other source refs (~line 521):

```ts
  const historySourceRef = useRef<VectorSource | null>(null);
```

In the map-init effect (line 643), after `editSourceRef.current = new VectorSource();`, add:

```ts
    historySourceRef.current = new VectorSource();
```

Create the layer after `editLayer` is created (~line 664):

```ts
    const historyLayer = new VectorLayer({
      source: historySourceRef.current,
      style: (feature) => styleForFeature(feature as Feature),
      zIndex: 4,
    });
```

Insert `historyLayer` into the `layers` array (line 674) right before `patternLayer`:

```ts
      layers: [
        new TileLayer({ source: new OSM({ crossOrigin: "anonymous" }) }),
        vectorLayer,
        historyLayer,
        patternLayer,
        editLayer,
        aircraftLayer
      ],
```

- [ ] **Step 6: Render Lines / Average features**

Add a new effect after the pattern-rendering effect (~line 753):

```ts
  // Historical track overlay: Lines draws every simplified flight as a faint
  // altitude-colored hairline; Average collapses them to a few bold
  // representative centerlines. Density is drawn on its own canvas (separate
  // effect), so here we clear when the mode isn't line/average.
  useEffect(() => {
    const source = historySourceRef.current;
    if (!mapReady || !source) return;
    source.clear();
    if (!historyActive || !historyTracks) return;

    if (historyMode === "lines") {
      for (const track of historyTracks) {
        if (track.samples.length < 2) continue;
        const coords = track.samples.map((s) => fromLonLat([s.lon, s.lat]));
        const altVals = track.samples
          .map((s) => (s.altitude_ft != null ? s.altitude_ft - groundElevFt : null))
          .filter((v): v is number => v != null && Number.isFinite(v));
        const avgAltAglFt = altVals.length > 0
          ? altVals.reduce((a, b) => a + b, 0) / altVals.length
          : null;
        source.addFeature(lineFeature(smoothSegment(coords), {
          kind: "history_line",
          alt_agl_ft: avgAltAglFt,
        }));
      }
    } else if (historyMode === "average") {
      if (!airport) return;
      const averaged = averagePaths(
        historyTracks.map((t) => ({ samples: t.samples })),
        { lat: airport.lat, lon: airport.lon },
      );
      for (const path of averaged) {
        const coords = path.points.map(([lon, lat]) => fromLonLat([lon, lat]));
        source.addFeature(lineFeature(smoothSegment(coords), { kind: "history_avg" }));
      }
    }
  }, [mapReady, historyActive, historyMode, historyTracks, airport, groundElevFt]);
```

- [ ] **Step 7: Typecheck / build**

Run: `cd frontend && npm run build`
Expected: PASS (tsc + vite build, no type errors). Density mode renders nothing yet — that's Task 9.

- [ ] **Step 8: Commit**

```bash
git add frontend/src/components/MapView.tsx
git commit -m "feat(map): historical track overlay — Lines and Average modes

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 9: MapView — Density mode canvas + legend

**Files:**
- Modify: `frontend/src/components/MapView.tsx`

**Interfaces:**
- Consumes: `historyTracks`, `historyMode` (Task 8), map instance, `fromLonLat`.
- Produces: a `<canvas className="history-canvas">` overlay that rasterizes distinct-flight counts per cell into a blue→red density field when `historyMode === "density"`, plus a `HistoryDensityLegend` shown in that mode.

- [ ] **Step 1: Add the density color ramp helper**

In `MapView.tsx`, after `rgbFromDb` (~line 126), add:

```ts
// Traffic-density color ramp (distinct flights per cell): cool blue for
// lightly used cells → warm red for heavily overflown corridors. Independent
// of the dB ramp — this is geometric density, not loudness.
function rgbFromDensity(t: number): [number, number, number] {
  const STOPS: Array<[number, [number, number, number]]> = [
    [0, [37, 99, 235]],    // blue-600
    [0.35, [6, 182, 212]], // cyan-500
    [0.6, [34, 197, 94]],  // green-500
    [0.8, [250, 204, 21]], // yellow-400
    [1, [239, 68, 68]],    // red-500
  ];
  const v = clamp01(t);
  if (v <= STOPS[0][0]) return STOPS[0][1];
  if (v >= STOPS[STOPS.length - 1][0]) return STOPS[STOPS.length - 1][1];
  for (let i = 1; i < STOPS.length; i += 1) {
    if (v <= STOPS[i][0]) {
      const [loT, lo] = STOPS[i - 1];
      const [hiT, hi] = STOPS[i];
      const f = (v - loT) / (hiT - loT);
      return [
        Math.round(lo[0] + (hi[0] - lo[0]) * f),
        Math.round(lo[1] + (hi[1] - lo[1]) * f),
        Math.round(lo[2] + (hi[2] - lo[2]) * f),
      ];
    }
  }
  return STOPS[STOPS.length - 1][1];
}
```

- [ ] **Step 2: Add the density canvas refs**

Alongside the heatmap canvas refs (~line 528):

```ts
  const historyCanvasRef = useRef<HTMLCanvasElement | null>(null);
  const historyOffscreenRef = useRef<HTMLCanvasElement | null>(null);
```

- [ ] **Step 3: Add the density-drawing effect**

After the noise-heatmap effect (~line 1026), add:

```ts
  // Traffic-density canvas. Each simplified flight deposits a Gaussian halo
  // along its interpolated sub-points, but counts DISTINCT flights per cell
  // (a per-cell "last flight index" stamp) so a corridor flown by 50 planes
  // reads hotter than one plane looping 50 times in place.
  useEffect(() => {
    if (!mapReady || !mapRef.current) return;
    const map = mapRef.current;
    const canvas = historyCanvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    if (!historyOffscreenRef.current) {
      historyOffscreenRef.current = document.createElement("canvas");
    }
    const off = historyOffscreenRef.current;
    const densityActive = historyActive && historyMode === "density" && !!historyTracks;

    const draw = () => {
      const size = map.getSize();
      if (!size) return;
      const dpr = window.devicePixelRatio || 1;
      const widthCss = size[0];
      const heightCss = size[1];
      if (canvas.width !== widthCss * dpr || canvas.height !== heightCss * dpr) {
        canvas.width = widthCss * dpr;
        canvas.height = heightCss * dpr;
        canvas.style.width = `${widthCss}px`;
        canvas.style.height = `${heightCss}px`;
      }
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.clearRect(0, 0, widthCss, heightCss);
      if (!densityActive || !historyTracks) return;

      const accScale = 4;
      const accW = Math.max(8, Math.floor(widthCss / accScale));
      const accH = Math.max(8, Math.floor(heightCss / accScale));
      const count = new Float32Array(accW * accH);
      const stamp = new Int32Array(accW * accH).fill(-1);
      const radiusPx = 3;
      const radiusInt = 3;
      const inv2Sigma2 = 1.0 / (radiusPx * radiusPx);

      for (let ti = 0; ti < historyTracks.length; ti += 1) {
        const samples = historyTracks[ti].samples;
        if (samples.length < 2) continue;
        for (let si = 0; si + 1 < samples.length; si += 1) {
          const a = samples[si];
          const b = samples[si + 1];
          const pa = map.getPixelFromCoordinate(fromLonLat([a.lon, a.lat]));
          const pb = map.getPixelFromCoordinate(fromLonLat([b.lon, b.lat]));
          if (!pa || !pb) continue;
          const segPx = Math.hypot(pb[0] - pa[0], pb[1] - pa[1]);
          const steps = Math.max(1, Math.min(40, Math.ceil(segPx / accScale)));
          for (let k = 0; k <= steps; k += 1) {
            const f = k / steps;
            const px = (pa[0] + (pb[0] - pa[0]) * f) / accScale;
            const py = (pa[1] + (pb[1] - pa[1]) * f) / accScale;
            if (px < -radiusInt || px >= accW + radiusInt) continue;
            if (py < -radiusInt || py >= accH + radiusInt) continue;
            const cx0 = Math.max(0, Math.floor(px - radiusInt));
            const cx1 = Math.min(accW, Math.ceil(px + radiusInt));
            const cy0 = Math.max(0, Math.floor(py - radiusInt));
            const cy1 = Math.min(accH, Math.ceil(py + radiusInt));
            for (let y = cy0; y < cy1; y += 1) {
              const dy = y - py;
              const dy2 = dy * dy;
              for (let x = cx0; x < cx1; x += 1) {
                const dx = x - px;
                const d2 = dx * dx + dy2;
                if (d2 > radiusInt * radiusInt) continue;
                const idx = y * accW + x;
                // Count each flight at most once per cell.
                if (stamp[idx] === ti) continue;
                stamp[idx] = ti;
                count[idx] += Math.exp(-d2 * inv2Sigma2);
              }
            }
          }
        }
      }

      let maxCount = 1;
      for (let i = 0; i < count.length; i += 1) if (count[i] > maxCount) maxCount = count[i];
      // Log scale so a few very hot corridors don't wash out the rest.
      const denom = Math.log1p(maxCount);
      const acc = ctx.createImageData(accW, accH);
      for (let i = 0; i < count.length; i += 1) {
        const c = count[i];
        if (c <= 0) continue;
        const t = denom > 0 ? Math.log1p(c) / denom : 0;
        const [r, g, b] = rgbFromDensity(t);
        const alpha = Math.round((0.15 + 0.6 * clamp01(t)) * 255);
        const o = i * 4;
        acc.data[o] = r;
        acc.data[o + 1] = g;
        acc.data[o + 2] = b;
        acc.data[o + 3] = alpha;
      }

      off.width = accW;
      off.height = accH;
      const offCtx = off.getContext("2d");
      if (!offCtx) return;
      offCtx.putImageData(acc, 0, 0);
      ctx.imageSmoothingEnabled = true;
      ctx.imageSmoothingQuality = "high";
      ctx.drawImage(off, 0, 0, widthCss, heightCss);
    };

    draw();
    map.on("postrender", draw);
    const onResize = () => draw();
    window.addEventListener("resize", onResize);
    return () => {
      map.un("postrender", draw);
      window.removeEventListener("resize", onResize);
    };
  }, [mapReady, historyActive, historyMode, historyTracks]);
```

- [ ] **Step 4: Render the canvas + legend in JSX**

In the returned JSX, after the existing `<canvas className="heatmap-canvas" ... />` (~line 1246), add:

```tsx
      <canvas className="history-canvas" ref={historyCanvasRef} aria-hidden="true" />
```

After the `{showHeatmap && <DbLegend />}` line (~line 1278), add:

```tsx
      {historyActive && historyMode === "density" && <HistoryDensityLegend />}
```

- [ ] **Step 5: Add the legend component**

At the bottom of the file, after `DbLegend`:

```tsx
function HistoryDensityLegend() {
  return (
    <div className="db-legend history-density-legend" role="figure" aria-label="Traffic density legend">
      <div className="db-legend-title">Flights over each spot</div>
      <div className="db-legend-bar history-density-bar" />
      <div className="db-legend-scale">
        <span>fewer</span>
        <span>more</span>
      </div>
      <div className="db-legend-note">
        Distinct flights crossing each area over the selected days. Log-scaled.
      </div>
    </div>
  );
}
```

- [ ] **Step 6: Add canvas + legend-bar styles**

In `frontend/src/styles.css`, find `.heatmap-canvas` and add a sibling rule with the same positioning. Add:

```css
.history-canvas {
  position: absolute;
  inset: 0;
  pointer-events: none;
  z-index: 1;
}
.history-density-bar {
  background: linear-gradient(
    to right,
    rgb(37, 99, 235),
    rgb(6, 182, 212),
    rgb(34, 197, 94),
    rgb(250, 204, 21),
    rgb(239, 68, 68)
  );
}
```

(If `.heatmap-canvas` sets a different `z-index`, match it so the density canvas sits directly over the basemap like the noise heatmap does.)

- [ ] **Step 7: Typecheck / build**

Run: `cd frontend && npm run build`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add frontend/src/components/MapView.tsx frontend/src/styles.css
git commit -m "feat(map): historical Density mode canvas + legend

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 10: App — overlay state machine, toggle button, controls, fetch

**Files:**
- Modify: `frontend/src/App.tsx`

**Interfaces:**
- Consumes: `getTrackHistory`, `TrackHistoryResponse` (Task 6); `MapView`'s `historyTracks` / `historyMode` props (Tasks 8–9).
- Produces: mutually-exclusive map overlays. The 🔥 button and a new history button drive a single `mapOverlay: "none" | "noise" | "history"`; a floating panel selects `historyDays` (1–7) and `historyMode`. Track history is fetched when the history overlay is active.

- [ ] **Step 1: Import the new icon, client, and type**

In `App.tsx` line 2, add `Route` to the lucide import list. In the api import block (lines 14–48), add `getTrackHistory,` and `type TrackHistoryResponse,`.

- [ ] **Step 2: Replace the `showHeatmap` boolean with an overlay state machine**

Replace line 105:

```ts
  const [showHeatmap, setShowHeatmap] = useState(false);
```

with:

```ts
  const [mapOverlay, setMapOverlay] = useState<"none" | "noise" | "history">("none");
  const [historyDays, setHistoryDays] = useState(3);
  const [historyMode, setHistoryMode] = useState<"lines" | "density" | "average">("lines");
  const [historyData, setHistoryData] = useState<TrackHistoryResponse | null>(null);
  const showHeatmap = mapOverlay === "noise";
```

- [ ] **Step 3: Fetch track history when the history overlay is active**

After the `refreshScan` effect (~line 342), add:

```ts
  useEffect(() => {
    if (mapOverlay !== "history" || !airport?.icao) {
      setHistoryData(null);
      return;
    }
    let cancelled = false;
    getTrackHistory(airport.icao, historyDays)
      .then((data) => { if (!cancelled) setHistoryData(data); })
      .catch(() => { if (!cancelled) setHistoryData(null); });
    return () => { cancelled = true; };
  }, [mapOverlay, airport?.icao, historyDays]);
```

- [ ] **Step 4: Update the 🔥 heatmap button to use the overlay state**

Replace the Flame button's `onClick` (line 466):

```tsx
              onClick={() => setShowHeatmap((value) => !value)}
```

with:

```tsx
              onClick={() => setMapOverlay((o) => (o === "noise" ? "none" : "noise"))}
```

- [ ] **Step 5: Add the history toggle button**

Immediately after the Flame button's closing `</button>` (~line 472), add:

```tsx
            <button
              type="button"
              className={`map-icon-toggle${mapOverlay === "history" ? " active" : ""}`}
              onClick={() => setMapOverlay((o) => (o === "history" ? "none" : "history"))}
              title={mapOverlay === "history" ? "Showing historical track density. Click to hide." : "Show 1–7 days of historical flight paths."}
              aria-label="Toggle historical track density"
              aria-pressed={mapOverlay === "history"}
            >
              <Route size={16} aria-hidden="true" />
            </button>
```

- [ ] **Step 6: Add the history controls panel**

Directly after the `<div className="map-controls">…</div>` block closes (~line 483), add:

```tsx
          {mapOverlay === "history" && (
            <div className="history-controls" role="group" aria-label="Historical track density controls">
              <div className="history-controls-row">
                <span className="history-controls-label">Days</span>
                <div className="segmented history-days">
                  {[1, 2, 3, 4, 5, 6, 7].map((d) => (
                    <button
                      key={d}
                      className={historyDays === d ? "active" : ""}
                      onClick={() => setHistoryDays(d)}
                    >
                      {d}
                    </button>
                  ))}
                </div>
              </div>
              <div className="history-controls-row">
                <span className="history-controls-label">View</span>
                <div className="segmented history-mode">
                  {([["lines", "Lines"], ["density", "Density"], ["average", "Average"]] as const).map(([mode, label]) => (
                    <button
                      key={mode}
                      className={historyMode === mode ? "active" : ""}
                      onClick={() => setHistoryMode(mode)}
                    >
                      {label}
                    </button>
                  ))}
                </div>
              </div>
              <div className="history-controls-caption">
                {historyData
                  ? historyData.truncated
                    ? `Showing ${historyData.tracks.length.toLocaleString()} of ${historyData.total_tracks.toLocaleString()} flights`
                    : `${historyData.tracks.length.toLocaleString()} flights`
                  : "Loading history…"}
              </div>
            </div>
          )}
```

- [ ] **Step 7: Pass history props to MapView**

In the `<MapView ... />` element (~line 490), after `showHeatmap={showHeatmap}`, add:

```tsx
            historyTracks={mapOverlay === "history" ? historyData?.tracks ?? null : null}
            historyMode={mapOverlay === "history" ? historyMode : null}
```

- [ ] **Step 8: Add controls-panel styles**

In `frontend/src/styles.css`, add:

```css
.history-controls {
  position: absolute;
  top: 12px;
  right: 12px;
  z-index: 5;
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 10px 12px;
  background: rgba(255, 255, 255, 0.94);
  border: 1px solid #e6e1d6;
  border-radius: 10px;
  box-shadow: 0 6px 20px rgba(15, 23, 42, 0.12);
  font-size: 12px;
}
.history-controls-row {
  display: flex;
  align-items: center;
  gap: 8px;
  justify-content: space-between;
}
.history-controls-label {
  font-weight: 700;
  color: #6b7185;
  text-transform: uppercase;
  letter-spacing: 0.04em;
}
.history-controls .segmented button {
  padding: 3px 7px;
  font-size: 12px;
}
.history-controls-caption {
  color: #6b7185;
  font-weight: 600;
}
```

(If `.history-controls` overlaps the existing `.map-controls`, adjust `top`/`right` so both are reachable — the map panel is `position: relative` per the existing `.heatmap-canvas` overlay.)

- [ ] **Step 9: Typecheck / build**

Run: `cd frontend && npm run build`
Expected: PASS.

- [ ] **Step 10: Run the full frontend test suite (no regressions)**

Run: `cd frontend && npm run test`
Expected: PASS (existing + new tests).

- [ ] **Step 11: Commit**

```bash
git add frontend/src/App.tsx frontend/src/styles.css
git commit -m "feat(app): historical track-density overlay toggle + controls

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 11: End-to-end manual verification

**Files:** none (verification only)

- [ ] **Step 1: Seed local data and start the stack**

Ensure a local backend + worker are running against a dev database (see `DEPLOY.md` / `docker-compose.prod.yml` for reference; locally the app auto-seeds via `POST /seed-demo` or an existing dev flow). Confirm `track_archive` has recent rows:

Run: `cd backend && python -c "import app.db as db; c=db.db_session('data/circlejerk.sqlite3').__enter__(); print(c.execute('SELECT COUNT(*) FROM track_archive').fetchone()[0])"`
Expected: a non-zero count (if zero, let the worker run a while or seed demo data first).

- [ ] **Step 2: Verify the endpoint directly**

Run: `curl -s "http://localhost:8000/api/airports/KBJC/track-history?days=3&ceiling_ft=5000" | python -m json.tool | head -40`
Expected: JSON with `airport`, `days: 3`, `tracks: [...]`, `total_tracks`, `truncated`.

- [ ] **Step 3: Verify the UI**

In the browser:
1. Click the new **Route** button in the map controls → the history controls panel appears; the 🔥 heatmap turns off if it was on (mutual exclusivity).
2. **Lines** mode → faint altitude-colored hairlines; streets and the VNAP oval remain visible underneath.
3. **Density** mode → a smooth blue→red field with the "Flights over each spot" legend; it re-sharpens on zoom/pan.
4. **Average** mode → a few bold representative centerlines.
5. Change **Days** 1→7 → the overlay refetches and updates; the caption shows the flight count (and "Showing X of Y" if truncated).
6. Toggle the Route button off → overlay clears and live tracks/aircraft return.

- [ ] **Step 4: Final full-suite check**

Run: `cd backend && python -m pytest -q && cd ../frontend && npm run test && npm run build`
Expected: all green.

- [ ] **Step 5: Commit any verification fixes**

If Steps 1–4 surfaced fixes, commit them:

```bash
git add -A
git commit -m "fix: historical track-density verification follow-ups

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Self-Review

**Spec coverage:**
- 7-day retention → Task 1. ✓
- All-traffic bbox query + altitude ceiling → Task 2. ✓
- New `track-history` endpoint (flight splitting, RDP, cap/truncation) → Tasks 3–5. ✓
- API client + types → Task 6. ✓
- Three modes: Lines (Task 8), Density (Task 9), Average (Tasks 7 + 8). ✓
- New toggle button + 1–7 day timeframe + mode selector, mutually exclusive with noise heatmap, "showing X of Y" caption → Task 10. ✓
- Suppress live layers, keep ring/home/patterns while active → Task 8 Step 4. ✓
- Testing (backend query/split/RDP/endpoint; frontend geometry) → Tasks 1–7. ✓
- Out-of-scope items (retroactive backfill, runway-accurate averaging, server density grid) correctly omitted. ✓

**Placeholder scan:** No TBD/TODO; every code step has complete code and exact run commands.

**Type consistency:** `historyTracks: HistoricalTrack[] | null` and `historyMode: "lines"|"density"|"average"|null` are consistent across MapView (Tasks 8, 9) and App (Task 10). `read_track_archive_bbox` signature matches between Task 2 (definition) and Task 5 (call). `build_tracks` return `(tracks, total)` matches Task 4 (definition) and Task 5 (usage). `getTrackHistory(icao, days, ceilingFt)` matches Task 6 (definition) and Task 10 (call). `averagePaths(tracks, airport, opts)` matches Task 7 (definition) and Task 8 (call). `bbox_for_radius` return order `(min_lat, min_lon, max_lat, max_lon)` is honored at the Task 5 call site.
