"""One physical pattern lap must produce exactly one circle event, no matter how
many times the sliding-window detector re-sees it across scans.

Fixture: 313 real 10s ADS-B samples of N737JR (a9e5e4) flying the KLMO pattern
for 74 minutes, captured from the production hot tier. Ground truth by eye and
by closest-approach-to-field: 10 runway passes.

Before the fix the circle id bucketed the lap-END time (`lap_end // 120`). The
lap end drifts sample-to-sample as the detection window slides, so one lap
smeared across several buckets -> several ids -> several rows (measured 2.8x).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app import db, detectors
from app.domain import ScanParams
from app.geo import Point, distance_nm

FIXTURE = Path(__file__).parent / "fixtures" / "n737jr_track.json"


@pytest.fixture
def track():
    return json.loads(FIXTURE.read_text())


@pytest.fixture
def conn():
    c = db.connect(":memory:")
    c.executescript(db.SCHEMA)
    db.seed_db(c)
    c.commit()
    return c


def _ground_truth_passes(track, airport) -> int:
    apt = Point(airport.lat, airport.lon)
    near = False
    passes = 0
    for x in track:
        d = distance_nm(Point(x["lat"], x["lon"]), apt)
        if d < 0.35 and not near:
            near = True
            passes += 1
        elif d > 0.7:
            near = False
    return passes


def _replay_scans(conn, track, airport, runways, params, step=10, window_s=20 * 60):
    """Mimic the live worker: every `step` seconds, run the detectors over the
    trailing `window_s` and persist — exactly the accumulation prod does."""
    t_first, t_last = track[0]["timestamp"], track[-1]["timestamp"]
    for t_now in range(t_first + 120, t_last + 1, step):
        window = [x for x in track if t_now - window_s <= x["timestamp"] <= t_now]
        for e in detectors.detect_circles(window, airport, params):
            db.persist_events(conn, [e])
        for e in detectors.detect_touch_and_gos_over_period(
            window, airport, runways, t_now - window_s, t_now
        ):
            db.persist_events(conn, [e])
    conn.commit()


def test_one_circle_row_per_physical_lap(conn, track):
    airport = db.get_airport(conn, "KLMO")
    runways = db.runways_for_airport(conn, "KLMO")
    params = ScanParams(airport_icao="KLMO", user_lat=40.1672, user_lon=-105.0997, ring_nm=8.0)

    passes = _ground_truth_passes(track, airport)
    assert passes == 10, "fixture sanity: 10 runway passes"

    _replay_scans(conn, track, airport, runways, params)

    circles = conn.execute("SELECT COUNT(*) FROM operations WHERE type='circle'").fetchone()[0]
    tgs = conn.execute("SELECT COUNT(*) FROM operations WHERE type='touch_and_go'").fetchone()[0]

    # One row per real lap, allowing at most one straddle-boundary duplicate.
    assert passes <= circles <= passes + 1, f"circle over-count: {circles} rows for {passes} laps"
    assert tgs <= passes + 1, f"touch-and-go over-count: {tgs} rows for {passes} laps"


def test_one_low_approach_row_per_physical_touchdown(conn, track):
    """A low approach's id must anchor on the touchdown's deepest sample, not on
    the merged low-run's FIRST bucket. The first bucket grows earlier as the
    sliding window feeds in more of the descent, so a first-bucket id smears one
    physical touchdown across several rows (measured 11 rows for 8 touchdowns)."""
    airport = db.get_airport(conn, "KLMO")
    runways = db.runways_for_airport(conn, "KLMO")
    params = ScanParams(airport_icao="KLMO", user_lat=40.1672, user_lon=-105.0997, ring_nm=8.0)

    _replay_scans(conn, track, airport, runways, params)

    rows = conn.execute(
        "SELECT icao24, timestamp FROM operations WHERE type='low_approach'"
    ).fetchall()
    keys = [(r["icao24"], r["timestamp"]) for r in rows]
    # One deepest sample per touchdown -> one stable id -> one row. No two rows
    # for one aircraft may share a timestamp.
    assert len(keys) == len(set(keys)), (
        f"low_approach over-count: {len(keys)} rows for {len(set(keys))} distinct touchdowns"
    )
