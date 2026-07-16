# Landing Classification & "Do Not Stop" Stats — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Classify runway operations as landings (the plane stopped) vs. non-stops (circle / touch-and-go / low-approach), and surface a "% that do not stop" metric on the `/stats` page — aggregate and per-day.

**Architecture:** Add a new `landing` operation type detected by a `detect_landings_over_period` detector that shares a runway-low-episode helper with the existing touch-and-go detector, so the two can never disagree. A landing = reached the runway low and did NOT climb back out within 5 min. `db.airport_stats` gains a `landings` counter, a `stop_classification` aggregate, and a per-day `stop_over_time` series; the `/stats` React page renders a "Landings" tile and a "Do they actually stop?" card. A one-time backfill re-runs runway-op detection over the 24 h `track_archive`.

**Tech Stack:** Python 3.12 / FastAPI / SQLite (backend), React 18 / TypeScript / recharts / vitest (frontend), pytest (backend tests).

## Global Constraints

- No DB schema migration — `landing` is a new value in the existing `operations.type` TEXT column.
- "Do not stop" % numerator = `circles + touch_and_gos + low_approaches`; denominator = numerator + `landings`. Passes-over-home (`pass_over_user`) are EXCLUDED from this ratio.
- Landing definition: reached ≤200 ft AGL within 1.5 nm of a runway, did NOT climb back out within 5 min (300 s), having descended into the field from ≥500 ft AGL first. Surfaced in UI as "≈ stayed ≥5 min".
- `stop_over_time` is ALWAYS bucketed by calendar day (`(timestamp/86400)*86400`), regardless of the window's chart `bucket_seconds`.
- Idempotency: detector event ids are stable sha256 of `(type, icao24, airport, bucket)`; `operations` upsert is `ON CONFLICT(id) DO NOTHING`. Re-running detection must never double-count.
- Backend tests: `cd backend && python -m pytest`. Frontend: `cd frontend && npm test` and `npm run build`.
- Commit message trailer (every commit): `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`

---

### Task 1: `landing` detector + shared runway-low episode helper

**Files:**
- Modify: `backend/app/detectors.py` (add constants + `_runway_low_episodes`, `_after_samples`, `_climbed_out`, `_approached_from_altitude`, `detect_landings_over_period`; refactor `detect_touch_and_gos_over_period`; wire into `detect_events_over_period`; extend `event_counts`)
- Test: `backend/tests/test_operations.py` (add landing fixtures + tests)

**Interfaces:**
- Consumes: existing `altitude_agl(sample, airport)`, `_nearest_runway`, `_runway_for_direction`, `_event_id`, `MAX_SEGMENT_GAP_SECONDS`.
- Produces:
  - `_runway_low_episodes(track, airport, runways, start_ts, end_ts) -> tuple[list[dict], list[dict]]` returning `(recent, episodes)`, each episode `{"bucket": int, "lowest_sample": dict, "lowest_agl": float, "runway": dict|None, "speed": float|None, "is_first_low": bool}`.
  - `detect_landings_over_period(track, airport, runways, start_ts, end_ts) -> list[dict]` emitting `type="landing"` events shaped like touch-and-go events.
  - `event_counts(events)` dict gains key `"landings"`.

- [ ] **Step 1: Add landing test fixtures**

Add near the other fixtures in `backend/tests/test_operations.py` (below `closed_loop_track`). These build tracks over KBJC runway 12L (threshold `39.9215, -105.1321`, heading `120`, elevation `5673`).

```python
from app.detectors import (
    detect_landings_over_period,
    detect_touch_and_gos_over_period,
)

KBJC_RUNWAYS = [
    {"runway_id": "12L", "lat_threshold": 39.9215, "lon_threshold": -105.1321, "heading_deg": 120, "length_ft": 9000},
    {"runway_id": "30R", "lat_threshold": 39.8962, "lon_threshold": -105.1013, "heading_deg": 300, "length_ft": 9000},
]


def _rwy_sample(ts, agl, icao24="land01", vr=0, on_ground=False):
    # All samples sit on the 12L threshold so _nearest_runway distance ~= 0.
    return {
        "icao24": icao24,
        "callsign": "N9LAND",
        "timestamp": ts,
        "lat": 39.9215,
        "lon": -105.1321,
        "geo_altitude_ft": 5673 + agl,
        "heading_deg": 120,
        "vertical_rate_fpm": vr,
        "velocity_kt": 70,
        "on_ground": on_ground,
    }


def landing_then_silence_track(t0=18000, icao24="land01"):
    # Descend into the field, touch down, then the track ends (went to the ramp).
    rows = [(0, 1500), (30, 1000), (60, 600), (90, 30)]
    return [_rwy_sample(t0 + dt, agl, icao24, vr=-500 if agl > 0 else 0) for dt, agl in rows]


def touch_and_go_track(t0=18000, icao24="tag01"):
    # Descend, touch ≤50 AGL, climb straight back out.
    rows = [(0, 1200, -500), (30, 600, -500), (60, 200, -400), (90, 20, 0),
            (120, 200, 600), (150, 600, 700), (180, 1000, 700)]
    return [_rwy_sample(t0 + dt, agl, icao24, vr=vr) for dt, agl, vr in rows]


def departure_track(t0=18000, icao24="dep01"):
    # Starts on the runway, climbs away — never approached from altitude.
    rows = [(0, 0, 0), (30, 50, 500), (60, 300, 800), (90, 800, 900), (120, 1500, 900)]
    return [_rwy_sample(t0 + dt, agl, icao24, vr=vr, on_ground=(agl == 0)) for dt, agl, vr in rows]


def long_ground_presence_track(t0=18000, icao24="long01"):
    # Land, then keep transmitting 0 AGL for ~8 min. Must yield exactly ONE landing.
    approach = [(0, 1500), (30, 1000), (60, 600), (90, 30)]
    rows = [_rwy_sample(t0 + dt, agl, icao24, vr=-500 if agl > 0 else 0) for dt, agl in approach]
    for dt in range(120, 540, 30):
        rows.append(_rwy_sample(t0 + dt, 0, icao24, on_ground=True))
    return rows
```

- [ ] **Step 2: Write the failing landing tests**

Add to `backend/tests/test_operations.py`:

```python
def test_landing_detected_when_no_climb_out():
    ap = airport_kbjc()
    track = landing_then_silence_track(t0=18000)
    # end_ts well past touchdown so the 5-min settle window is satisfied.
    events = detect_landings_over_period(track, ap, KBJC_RUNWAYS, 18000, 18900)
    landings = [e for e in events if e["type"] == "landing"]
    assert len(landings) == 1
    assert landings[0]["runway_id"] == "12L"
    assert landings[0]["min_altitude_ft_agl"] <= 50


def test_touch_and_go_is_not_a_landing():
    ap = airport_kbjc()
    track = touch_and_go_track(t0=18000)
    events = detect_landings_over_period(track, ap, KBJC_RUNWAYS, 18000, 18900)
    assert [e for e in events if e["type"] == "landing"] == []


def test_departure_is_not_a_landing():
    ap = airport_kbjc()
    track = departure_track(t0=18000)
    events = detect_landings_over_period(track, ap, KBJC_RUNWAYS, 18000, 18900)
    assert [e for e in events if e["type"] == "landing"] == []


def test_no_landing_before_settle_window():
    ap = airport_kbjc()
    track = landing_then_silence_track(t0=18000)
    # Only ~100s observed past the touchdown at 18090 -> not settled yet.
    events = detect_landings_over_period(track, ap, KBJC_RUNWAYS, 18000, 18190)
    assert [e for e in events if e["type"] == "landing"] == []


def test_long_ground_presence_yields_single_landing():
    ap = airport_kbjc()
    track = long_ground_presence_track(t0=18000)
    events = detect_landings_over_period(track, ap, KBJC_RUNWAYS, 18000, 18900)
    assert len([e for e in events if e["type"] == "landing"]) == 1


def test_touch_and_go_still_detected_after_refactor():
    ap = airport_kbjc()
    track = touch_and_go_track(t0=18000)
    events = detect_touch_and_gos_over_period(track, ap, KBJC_RUNWAYS, 18000, 18900)
    assert any(e["type"] == "touch_and_go" for e in events)
    assert all(e["type"] != "landing" for e in events)
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_operations.py -k "landing or touch_and_go_still" -v`
Expected: FAIL — `ImportError: cannot import name 'detect_landings_over_period'`.

- [ ] **Step 4: Add detector constants**

In `backend/app/detectors.py`, add below the existing circle constants (after line ~29, `MAX_CIRCLE_ALTITUDE_FT_AGL = 2000`):

```python
# --- Runway-contact / landing classification ---
# Look this far past a touchdown to decide touch-and-go vs. landing. Widened
# from the old 120s so touch-and-go and landing are exact complements over the
# same 5-minute window.
CLIMB_OUT_LOOKAHEAD_SECONDS = 300
# A landing is only *decided* once we've observed this long past the touchdown
# with no climb-out — prevents emitting a "landing" a later scan would find was
# a touch-and-go.
LANDING_SETTLE_SECONDS = 300
# The aircraft must have descended INTO the field: at least one sample this far
# before touchdown was at/above this AGL. Rejects parked/taxiing transponders.
APPROACH_LOOKBACK_SECONDS = 300
APPROACH_MIN_AGL_FT = 500
```

- [ ] **Step 5: Add the shared helper + small utilities**

In `backend/app/detectors.py`, add these functions just above `detect_touch_and_gos` (line ~534):

```python
def _runway_low_episodes(
    track: list[dict],
    airport: Airport,
    runways: list[dict],
    start_ts: int,
    end_ts: int,
) -> tuple[list[dict], list[dict]]:
    """Find each runway-contact episode (≤200 ft AGL within 1.5 nm of a runway).

    Shared by the touch-and-go and landing detectors so they classify the exact
    same episodes and can never disagree. Returns (recent, episodes). Each
    episode is the lowest sample in a 180 s bucket, tagged `is_first_low` when
    the previous 180 s window held no runway-low sample for this aircraft (i.e.
    this bucket is an arrival, not a continuation of an on-ground presence).
    """
    recent = [
        sample for sample in sorted(track, key=lambda row: row["timestamp"])
        if start_ts - 120 <= sample["timestamp"] <= end_ts + CLIMB_OUT_LOOKAHEAD_SECONDS
    ]
    if len(recent) < 4:
        return recent, []

    low_by_bucket: dict[int, list[tuple[dict, float, dict | None, float | None]]] = defaultdict(list)
    for sample in recent:
        if sample["timestamp"] < start_ts or sample["timestamp"] > end_ts:
            continue
        agl = altitude_agl(sample, airport)
        runway, runway_dist = _nearest_runway(sample, runways)
        speed = sample.get("velocity_kt")
        if agl is not None and runway_dist <= 1.5:
            low_by_bucket[int(sample["timestamp"] // 180)].append((sample, agl, runway, speed))

    low_buckets = set(low_by_bucket.keys())
    episodes = []
    for bucket, low_samples in sorted(low_by_bucket.items()):
        if not low_samples:
            continue
        lowest_sample, lowest_agl, runway, speed = min(low_samples, key=lambda row: row[1])
        episodes.append({
            "bucket": bucket,
            "lowest_sample": lowest_sample,
            "lowest_agl": lowest_agl,
            "runway": runway,
            "speed": speed,
            "is_first_low": (bucket - 1) not in low_buckets,
        })
    return recent, episodes


def _after_samples(recent: list[dict], lowest_sample: dict, seconds: int) -> list[dict]:
    lt = lowest_sample["timestamp"]
    return [s for s in recent if lt < s["timestamp"] <= lt + seconds]


def _climbed_out(after: list[dict], lowest_agl: float, airport: Airport) -> bool:
    if not after:
        return False
    latest_agl = altitude_agl(after[-1], airport)
    climbed = latest_agl is not None and latest_agl - lowest_agl >= 150
    vertical_up = any((s.get("vertical_rate_fpm") or 0) > 250 for s in after)
    return climbed or vertical_up


def _approached_from_altitude(recent: list[dict], lt: int, airport: Airport) -> bool:
    for sample in recent:
        ts = sample["timestamp"]
        if ts >= lt or ts < lt - APPROACH_LOOKBACK_SECONDS:
            continue
        agl = altitude_agl(sample, airport)
        if agl is not None and agl >= APPROACH_MIN_AGL_FT:
            return True
    return False
```

- [ ] **Step 6: Refactor `detect_touch_and_gos_over_period` onto the shared helper**

Replace the body of `detect_touch_and_gos_over_period` (currently lines ~545-616) with:

```python
def detect_touch_and_gos_over_period(
    track: list[dict],
    airport: Airport,
    runways: list[dict],
    start_ts: int,
    end_ts: int,
) -> list[dict]:
    recent, episodes = _runway_low_episodes(track, airport, runways, start_ts, end_ts)
    events = []
    for ep in episodes:
        lowest_sample = ep["lowest_sample"]
        lowest_agl = ep["lowest_agl"]
        speed = ep["speed"]
        after = _after_samples(recent, lowest_sample, CLIMB_OUT_LOOKAHEAD_SECONDS)
        if not after:
            continue
        on_ground_seconds = sum(1 for sample in after if sample.get("on_ground")) * 30
        # Speed filter is best-effort: archive samples have no velocity, so trust
        # the altitude + climb-back-up signature when speed is absent.
        speed_ok = speed is None or speed <= 90
        if not (lowest_agl <= 200 and speed_ok and _climbed_out(after, lowest_agl, airport)
                and on_ground_seconds <= 60):
            continue
        event_type = "touch_and_go" if lowest_agl <= 50 else "low_approach"

        directional = _runway_for_direction(lowest_sample, runways)
        used_runway = directional or ep["runway"]
        runway_heading = used_runway.get("heading_deg") if used_runway else None
        events.append({
            "id": _event_id(event_type, lowest_sample["icao24"], airport.icao, ep["bucket"]),
            "type": event_type,
            "icao24": lowest_sample["icao24"],
            "callsign": lowest_sample.get("callsign") or lowest_sample["icao24"].upper(),
            "timestamp": int(lowest_sample["timestamp"]),
            "airport_icao": airport.icao,
            "runway_id": used_runway["runway_id"] if used_runway else None,
            "runway_heading_deg": int(runway_heading) if runway_heading is not None else None,
            "runway_used": used_runway["runway_id"] if used_runway else None,
            "min_altitude_ft_agl": int(lowest_agl),
        })
    return events
```

- [ ] **Step 7: Add `detect_landings_over_period`**

Add immediately after `detect_touch_and_gos_over_period`:

```python
def detect_landings_over_period(
    track: list[dict],
    airport: Airport,
    runways: list[dict],
    start_ts: int,
    end_ts: int,
) -> list[dict]:
    """A landing = reached the runway low and did NOT climb back out within 5 min.

    The complement of a touch-and-go over the same runway-low episodes. Guards:
    only the first low bucket of an arrival (`is_first_low`), the aircraft
    descended in from ≥500 ft AGL, and we've observed ≥5 min past touchdown
    (settle) so we won't retract it as a touch-and-go on a later scan.
    """
    recent, episodes = _runway_low_episodes(track, airport, runways, start_ts, end_ts)
    events = []
    for ep in episodes:
        if not ep["is_first_low"]:
            continue
        lowest_sample = ep["lowest_sample"]
        lowest_agl = ep["lowest_agl"]
        lt = int(lowest_sample["timestamp"])
        if lowest_agl > 200:
            continue
        if end_ts - lt < LANDING_SETTLE_SECONDS:
            continue  # not enough post-touchdown data yet — decide on a later pass
        after = _after_samples(recent, lowest_sample, LANDING_SETTLE_SECONDS)
        if _climbed_out(after, lowest_agl, airport):
            continue  # climbed back out -> touch-and-go, not a landing
        if not _approached_from_altitude(recent, lt, airport):
            continue  # never descended in (parked/taxiing) -> not a landing

        directional = _runway_for_direction(lowest_sample, runways)
        used_runway = directional or ep["runway"]
        runway_heading = used_runway.get("heading_deg") if used_runway else None
        events.append({
            "id": _event_id("landing", lowest_sample["icao24"], airport.icao, ep["bucket"]),
            "type": "landing",
            "icao24": lowest_sample["icao24"],
            "callsign": lowest_sample.get("callsign") or lowest_sample["icao24"].upper(),
            "timestamp": lt,
            "airport_icao": airport.icao,
            "runway_id": used_runway["runway_id"] if used_runway else None,
            "runway_heading_deg": int(runway_heading) if runway_heading is not None else None,
            "runway_used": used_runway["runway_id"] if used_runway else None,
            "min_altitude_ft_agl": int(lowest_agl),
        })
    return events
```

- [ ] **Step 8: Wire landing detection into `detect_events_over_period` and `event_counts`**

In `detect_events_over_period` (line ~717), add after the touch-and-gos loop:

```python
    for event in detect_touch_and_gos_over_period(samples, airport, runways, start_ts, end_ts):
        events_by_id[event["id"]] = event
    for event in detect_landings_over_period(samples, airport, runways, start_ts, end_ts):
        events_by_id[event["id"]] = event
```

In `event_counts` (line ~724), add the landings key:

```python
    return {
        "circles": counts["circle"],
        "touch_and_gos": counts["touch_and_go"],
        "low_approaches": counts["low_approach"],
        "landings": counts["landing"],
        "passes": counts["pass_over_user"],
    }
```

- [ ] **Step 9: Run the tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_operations.py tests/test_core.py -v`
Expected: PASS — the six new tests plus all existing operation/core tests (including `test_touch_and_go_event_includes_runway_id_from_heading` and `test_detected_circle_is_persisted`) stay green.

- [ ] **Step 10: Commit**

```bash
git add backend/app/detectors.py backend/tests/test_operations.py
git commit -m "feat(detectors): landing classification via shared runway-low episodes

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: Stats aggregation — landings counter, `stop_classification`, `stop_over_time`

**Files:**
- Modify: `backend/app/db.py` (`airport_stats`, lines ~621-765)
- Test: `backend/tests/test_stats.py`

**Interfaces:**
- Consumes: existing `operations` table rows with `type` in {circle, touch_and_go, low_approach, landing, pass_over_user}.
- Produces: `airport_stats(...)` return dict gains `counters["landings"]`, `stop_classification`, and `stop_over_time` (shapes below). `main.get_airport_stats` already spreads `**stats`, so no endpoint change.

- [ ] **Step 1: Write the failing stats tests**

Add to `backend/tests/test_stats.py` (follow the existing `_mk_op`/seeded-conn pattern used by `test_airport_stats`; insert ops via `db.upsert_operation`). Match the helper already used in the file — if `test_airport_stats` builds ops inline, mirror that. Assume a local helper `_op(id, ts, type_, icao24="a")` returning a dict for `db.upsert_operation`:

```python
def _op(id_, ts, type_, icao24="a"):
    return {"id": id_, "icao": "KBJC", "icao24": icao24, "callsign": "N1",
            "registration": None, "type": type_, "timestamp": ts, "runway_id": None,
            "runway_heading_deg": None, "turn_direction": None, "min_altitude_ft_agl": None}


def test_stop_classification_includes_circles_and_excludes_passes(tmp_path):
    conn = seeded_conn(tmp_path / "s.sqlite3")
    day = 86400
    for i, t in enumerate(("circle", "circle", "touch_and_go", "low_approach", "landing")):
        db.upsert_operation(conn, _op(f"a{i}", day + i, t))
    db.upsert_operation(conn, _op("p0", day + 9, "pass_over_user"))  # excluded from ratio
    conn.commit()

    stats = db.airport_stats(conn, "KBJC", 0, 10 * day, bucket_seconds=day)
    sc = stats["stop_classification"]
    assert stats["counters"]["landings"] == 1
    assert sc["did_not_stop"] == 4          # 2 circles + 1 t&g + 1 low approach
    assert sc["landed"] == 1
    assert sc["total"] == 5
    assert sc["did_not_stop_pct"] == 80.0


def test_stop_classification_pct_null_when_empty(tmp_path):
    conn = seeded_conn(tmp_path / "s.sqlite3")
    stats = db.airport_stats(conn, "KBJC", 0, 86400)
    assert stats["stop_classification"]["total"] == 0
    assert stats["stop_classification"]["did_not_stop_pct"] is None


def test_stop_over_time_buckets_by_day(tmp_path):
    conn = seeded_conn(tmp_path / "s.sqlite3")
    day = 86400
    # Day 1: 2 non-stop, 0 landed. Day 2: 1 non-stop, 1 landed.
    db.upsert_operation(conn, _op("d1a", day + 10, "circle"))
    db.upsert_operation(conn, _op("d1b", day + 20, "touch_and_go"))
    db.upsert_operation(conn, _op("d2a", 2 * day + 10, "touch_and_go"))
    db.upsert_operation(conn, _op("d2b", 2 * day + 20, "landing"))
    conn.commit()

    stats = db.airport_stats(conn, "KBJC", 0, 10 * day)
    rows = {r["day"]: r for r in stats["stop_over_time"]}
    assert rows[day]["did_not_stop"] == 2 and rows[day]["landed"] == 0 and rows[day]["pct"] == 100.0
    assert rows[2 * day]["did_not_stop"] == 1 and rows[2 * day]["landed"] == 1 and rows[2 * day]["pct"] == 50.0
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_stats.py -k "stop_" -v`
Expected: FAIL — `KeyError: 'stop_classification'` / `KeyError: 'landings'`.

- [ ] **Step 3: Add the `landings` counter**

In `db.airport_stats`, extend the `counters` dict (line ~637) with the landings line:

```python
    counters = {
        "circles": type_counts.get("circle", 0),
        "touch_and_gos": type_counts.get("touch_and_go", 0),
        "low_approaches": type_counts.get("low_approach", 0),
        "landings": type_counts.get("landing", 0),
        "passes": type_counts.get("pass_over_user", 0),
        "unique_aircraft": unique_aircraft,
        "runway_changes": change_count,
    }
```

- [ ] **Step 4: Build `stop_classification` and `stop_over_time`**

In `db.airport_stats`, add just before the final `return {` (line ~755):

```python
    did_not_stop = counters["circles"] + counters["touch_and_gos"] + counters["low_approaches"]
    landed = counters["landings"]
    stop_total = did_not_stop + landed
    stop_classification = {
        "total": stop_total,
        "did_not_stop": did_not_stop,
        "landed": landed,
        "did_not_stop_pct": round(100 * did_not_stop / stop_total, 1) if stop_total else None,
    }

    # Always by calendar day (UTC epoch days), independent of the chart bucket.
    stop_over_time = []
    for r in conn.execute(
        "SELECT (timestamp/86400)*86400 AS day, "
        "SUM(CASE WHEN type='landing' THEN 1 ELSE 0 END) AS landed, "
        "SUM(CASE WHEN type IN ('circle','touch_and_go','low_approach') THEN 1 ELSE 0 END) AS did_not_stop "
        "FROM operations WHERE icao=? AND timestamp BETWEEN ? AND ? "
        "AND type IN ('circle','touch_and_go','low_approach','landing') "
        "GROUP BY day ORDER BY day", win,
    ).fetchall():
        total = r["did_not_stop"] + r["landed"]
        stop_over_time.append({
            "day": r["day"],
            "did_not_stop": r["did_not_stop"],
            "landed": r["landed"],
            "total": total,
            "pct": round(100 * r["did_not_stop"] / total, 1) if total else None,
        })
```

Then add both keys to the returned dict:

```python
    return {
        "counters": counters,
        "ops_over_time": ops_over_time,
        "stop_classification": stop_classification,
        "stop_over_time": stop_over_time,
        "wind": wind,
        "deviation": deviation,
        "cowboys": cowboys,
        "recent_changes": recent_changes,
        "repeat_offenders": repeat_offenders,
        "flight_schools": flight_schools,
        "runway_usage": runway_usage,
    }
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_stats.py -v`
Expected: PASS — the three new tests plus all existing stats tests (incl. `test_stats_endpoint`) stay green.

- [ ] **Step 6: Commit**

```bash
git add backend/app/db.py backend/tests/test_stats.py
git commit -m "feat(stats): landings counter + do-not-stop classification and per-day series

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: Frontend — Landings tile + "Do they actually stop?" card

**Files:**
- Modify: `frontend/src/lib/api.ts` (`AirportStatsResponse`, lines ~836-854)
- Modify: `frontend/src/components/StatsPage.tsx`
- Modify: `frontend/src/styles.css` (hero styles)

**Interfaces:**
- Consumes: `AirportStatsResponse` from Task 2's payload (`counters.landings`, `stop_classification`, `stop_over_time`).
- Produces: rendered "Landings" tile + "Do they actually stop?" card. No new exports.

- [ ] **Step 1: Extend the API type**

In `frontend/src/lib/api.ts`, update `AirportStatsResponse`:
- add `landings: number;` to the `counters` object (after `low_approaches: number;`):

```ts
  counters: {
    circles: number; touch_and_gos: number; low_approaches: number;
    landings: number;
    passes: number; unique_aircraft: number; runway_changes: number;
  };
```

- add these two members after the `ops_over_time` line:

```ts
  stop_classification: { total: number; did_not_stop: number; landed: number; did_not_stop_pct: number | null };
  stop_over_time: { day: number; did_not_stop: number; landed: number; total: number; pct: number | null }[];
```

- [ ] **Step 2: Verify the type change compiles**

Run: `cd frontend && npm run build`
Expected: PASS (tsc clean). The existing `statsApi.test.ts` mock returns a partial object cast to the response type, so it is unaffected.

- [ ] **Step 3: Add the recharts `Legend` import + Landings tile**

In `frontend/src/components/StatsPage.tsx`, update the recharts import (line 2) to include `Legend`:

```tsx
import { BarChart, Bar, XAxis, YAxis, Tooltip, Legend, ResponsiveContainer } from "recharts";
```

Add a Landings tile in the `stats-tiles` section (after the "Low approaches" tile, line ~46):

```tsx
            <Tile label="Landings" value={data.counters.landings} />
```

- [ ] **Step 4: Add the "Do they actually stop?" card**

In `frontend/src/components/StatsPage.tsx`, insert this section immediately after the `stats-tiles` `</section>` (line ~50), before "Operations over time":

```tsx
          <section className="stats-card">
            <h2>Do they actually stop?</h2>
            {data.stop_classification.total === 0 ? (
              <p className="stats-empty">No runway operations in this window.</p>
            ) : (
              <>
                <p className="stats-hero">
                  <span className="stats-hero-pct">{data.stop_classification.did_not_stop_pct}%</span>
                  <span className="stats-hero-label">did not stop</span>
                </p>
                <p className="stats-besteffort">
                  {data.stop_classification.did_not_stop} fly-throughs (circles + touch-and-gos) vs{" "}
                  {data.stop_classification.landed} landing{data.stop_classification.landed === 1 ? "" : "s"} · this window.
                  A “landing” means the aircraft reached the runway and did not climb back out within 5 min (≈ stayed ≥5 min).
                </p>
                {data.stop_over_time.length > 0 && (
                  <ResponsiveContainer width="100%" height={220}>
                    <BarChart data={data.stop_over_time.map((d) => ({
                      t: new Date(d.day * 1000).toLocaleDateString([], { month: "numeric", day: "numeric" }),
                      "Did not stop": d.did_not_stop,
                      "Landed": d.landed,
                    }))}>
                      <XAxis dataKey="t" fontSize={11} interval="preserveStartEnd" minTickGap={24} />
                      <YAxis allowDecimals={false} fontSize={11} />
                      <Tooltip />
                      <Legend />
                      <Bar dataKey="Did not stop" stackId="a" fill="#b3231f" />
                      <Bar dataKey="Landed" stackId="a" fill="#1b3a6b" />
                    </BarChart>
                  </ResponsiveContainer>
                )}
              </>
            )}
          </section>
```

- [ ] **Step 5: Add hero styles**

In `frontend/src/styles.css`, add after the `.stats-besteffort` rule (line ~2974):

```css
.stats-hero { display: flex; align-items: baseline; gap: 10px; margin: 4px 0 8px; }
.stats-hero-pct { font-size: 40px; font-weight: 800; color: #b3231f; line-height: 1; }
.stats-hero-label { font-size: 14px; font-weight: 700; color: var(--muted); }
```

- [ ] **Step 6: Verify build + tests**

Run: `cd frontend && npm run build && npm test`
Expected: PASS — tsc clean, vite build succeeds, `statsApi.test.ts` still green.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/lib/api.ts frontend/src/components/StatsPage.tsx frontend/src/styles.css
git commit -m "feat(stats-page): landings tile + do-not-stop card with per-day chart

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: One-time 24 h landing backfill script

**Files:**
- Create: `backend/scripts/backfill_landings.py`
- Test: `backend/tests/test_backfill_landings.py`

**Interfaces:**
- Consumes: `db.get_airport`, `db.runways_for_airport`, `db.read_track_archive_bbox`, `db.persist_events`, `geo.bbox_for_radius`, `detectors.detect_touch_and_gos_over_period`, `detectors.detect_landings_over_period`.
- Produces: `backfill_landings(conn, icao, now, lookback_s=86400) -> dict` (stats dict), plus a `main()` CLI entrypoint (`--db`, `--icao`, `--now`).

- [ ] **Step 1: Write the failing backfill test**

Create `backend/tests/test_backfill_landings.py`:

```python
from __future__ import annotations

from app import db
from scripts.backfill_landings import backfill_landings
from tests.test_operations import landing_then_silence_track, seeded_conn


def test_backfill_emits_landing_from_archive(tmp_path):
    conn = seeded_conn(tmp_path / "b.sqlite3")
    t0 = 18000
    now = t0 + 4000  # well past the 5-min settle window
    # Archive a landing track for an aircraft near the KBJC field.
    db.archive_track_samples(conn, "land01", landing_then_silence_track(t0=t0, icao24="land01"))
    conn.commit()

    stats = backfill_landings(conn, "KBJC", now=now, lookback_s=86400)

    rows = db.read_operations(conn, "KBJC", 0, now, types=["landing"])
    assert len(rows) == 1
    assert stats["landings"] >= 1


def test_backfill_is_idempotent(tmp_path):
    conn = seeded_conn(tmp_path / "b.sqlite3")
    t0 = 18000
    now = t0 + 4000
    db.archive_track_samples(conn, "land01", landing_then_silence_track(t0=t0, icao24="land01"))
    conn.commit()

    backfill_landings(conn, "KBJC", now=now)
    backfill_landings(conn, "KBJC", now=now)  # second run must not duplicate

    rows = db.read_operations(conn, "KBJC", 0, now, types=["landing"])
    assert len(rows) == 1
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd backend && python -m pytest tests/test_backfill_landings.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'scripts.backfill_landings'`.

- [ ] **Step 3: Write the backfill script**

Create `backend/scripts/backfill_landings.py`:

```python
"""One-time backfill: re-run runway-op detection over the 24 h track archive.

Landings were never detected before this feature shipped, so historical `/stats`
windows read ~100% "do not stop" until detection has been running for a while.
This re-runs the touch-and-go + landing detectors over the cold `track_archive`
for an airport and upserts any operations it finds. Idempotent via the stable
event ids + `ON CONFLICT(id) DO NOTHING`.

Usage:
    python -m scripts.backfill_landings --db data/circlejerk.sqlite3 --icao KBJC
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
import time
from collections import defaultdict
from pathlib import Path

from app import db
from app.detectors import detect_landings_over_period, detect_touch_and_gos_over_period
from app.geo import bbox_for_radius

RING_NM = 8.0


def backfill_landings(conn: sqlite3.Connection, icao: str, now: int, lookback_s: int = 86400) -> dict:
    icao = icao.upper()
    airport = db.get_airport(conn, icao)
    if airport is None:
        raise ValueError(f"airport not found: {icao}")
    runways = db.runways_for_airport(conn, icao)
    start_ts = now - lookback_s
    min_lat, min_lon, max_lat, max_lon = bbox_for_radius(airport.lat, airport.lon, RING_NM)
    rows = db.read_track_archive_bbox(conn, min_lat, max_lat, min_lon, max_lon, start_ts, now)

    tracks: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        tracks[row["icao24"]].append(row)

    events: list[dict] = []
    for track in tracks.values():
        events.extend(detect_touch_and_gos_over_period(track, airport, runways, start_ts, now))
        events.extend(detect_landings_over_period(track, airport, runways, start_ts, now))

    db.persist_events(conn, events)
    conn.commit()

    counts: dict[str, int] = defaultdict(int)
    for event in events:
        counts[event["type"]] += 1
    return {
        "aircraft_scanned": len(tracks),
        "events_found": len(events),
        "landings": counts.get("landing", 0),
        "touch_and_gos": counts.get("touch_and_go", 0),
        "low_approaches": counts.get("low_approach", 0),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True, help="Path to circlejerk.sqlite3")
    parser.add_argument("--icao", required=True, help="Airport ICAO, e.g. KBJC")
    parser.add_argument("--now", type=int, default=None, help="Override 'now' epoch (default: current time)")
    args = parser.parse_args()

    db_path = Path(args.db)
    if not db_path.exists():
        print(f"db not found: {db_path}", file=sys.stderr)
        return 1

    conn = db.connect(str(db_path))
    try:
        stats = backfill_landings(conn, args.icao, now=args.now or int(time.time()))
    finally:
        conn.close()

    for key, value in stats.items():
        print(f"  {key}: {value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd backend && python -m pytest tests/test_backfill_landings.py -v`
Expected: PASS — both tests green (one landing emitted, second run does not duplicate).

- [ ] **Step 5: Run the full backend suite**

Run: `cd backend && python -m pytest`
Expected: PASS — entire suite green.

- [ ] **Step 6: Commit**

```bash
git add backend/scripts/backfill_landings.py backend/tests/test_backfill_landings.py
git commit -m "feat(scripts): 24h track-archive backfill for landing operations

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Deployment note (post-merge, manual)

After deploy, run the backfill once against production so `1d`/`7d` numbers are
immediately meaningful (see `DEPLOY.md` for the container/db path):

```bash
python -m scripts.backfill_landings --db /path/to/circlejerk.sqlite3 --icao KBJC
```

Longer windows fill in organically as live detection runs.

---

## Self-Review

**Spec coverage:**
- New `landing` operation type + inferred definition → Task 1 (`detect_landings_over_period`, constants, guards). ✓
- Shared runway-low trigger so detectors can't disagree → Task 1 (`_runway_low_episodes`). ✓
- Widen climb-out window 120→300 s; touch-and-go behavior preserved → Task 1 Step 6 + `test_touch_and_go_still_detected_after_refactor`. ✓
- Approach guard, settle guard, first-low dedup → Task 1 Steps 5/7 + dedicated tests. ✓
- `counters.landings`, `stop_classification` (circles included, passes excluded), `stop_over_time` per-day, null pct on empty → Task 2 + tests. ✓
- No endpoint change (spread `**stats`) → confirmed in Task 2 interfaces. ✓
- Frontend Landings tile + "Do they actually stop?" hero % + stacked-by-day chart + honest "≈ stayed ≥5 min" copy → Task 3. ✓
- 24 h backfill, idempotent → Task 4 + idempotency test + deploy note. ✓
- No schema migration → confirmed (Global Constraints; `landing` is a `type` value). ✓

**Placeholder scan:** No TBD/TODO; every code step contains complete code and exact commands. ✓

**Type consistency:** `_runway_low_episodes` returns `(recent, episodes)` and every consumer (`detect_touch_and_gos_over_period`, `detect_landings_over_period`) unpacks the tuple and reads the same episode keys (`bucket`, `lowest_sample`, `lowest_agl`, `runway`, `speed`, `is_first_low`). `event_counts` key `landings` matches `counters["landings"]` in `airport_stats`, which matches `counters.landings` in the TS type and `data.counters.landings` in the tile. `stop_classification` / `stop_over_time` field names (`total`, `did_not_stop`, `landed`, `did_not_stop_pct`, `day`, `pct`) are identical across db.py, api.ts, and StatsPage.tsx. ✓

**Known residual (documented in spec §5):** `is_first_low` uses `bucket-1` membership, so a mid-approach signal gap spanning a full 180 s bucket could in rare cases emit a second landing; acceptable for MVP.
