"""A touch-and-go is a circle whose loop passes over the runway (any altitude).

Redefined per the user: N333RX flew 8 pattern loops over KLMO at ~1000 ft,
never touching down (0 real >=50ft touchdowns), and the card showed T&G=0. The
loops clearly cross the runway, so they must count as touch-and-gos. T&G is now
a geometric subset of circles, not a >=50ft-touchdown episode.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app import db, detectors
from app.domain import ScanParams

FIX = Path(__file__).parent / "fixtures"


@pytest.fixture
def conn():
    c = db.connect(":memory:")
    c.executescript(db.SCHEMA); db.seed_db(c); c.commit()
    return c


def _replay(conn, track, airport, runways, params):
    t0, t1 = track[0]["timestamp"], track[-1]["timestamp"]
    for t_now in range(t0 + 120, t1 + 1, 10):
        w = [x for x in track if t_now - 20 * 60 <= x["timestamp"] <= t_now]
        for e in detectors.detect_events(w, airport, runways, params):
            db.persist_events(conn, [e])
        for e in detectors.detect_events_over_period(w, airport, runways, params, t_now - 20 * 60, t_now):
            db.persist_events(conn, [e])
    conn.commit()


@pytest.mark.parametrize("fixture", ["n333rx_track.json", "n737jr_track.json"])
def test_tg_equals_over_runway_circles(conn, fixture):
    track = json.loads((FIX / fixture).read_text())
    airport = db.get_airport(conn, "KLMO")
    runways = db.runways_for_airport(conn, "KLMO")
    params = ScanParams(airport_icao="KLMO", user_lat=40.1672, user_lon=-105.0997, ring_nm=8.0)

    _replay(conn, track, airport, runways, params)

    circles = conn.execute("SELECT COUNT(*) FROM operations WHERE type='circle'").fetchone()[0]
    tgs = conn.execute("SELECT COUNT(*) FROM operations WHERE type='touch_and_go'").fetchone()[0]

    # These aircraft fly the runway pattern, so every lap crosses the runway:
    # T&G should track circles closely (allow a small geometric edge margin).
    assert circles >= 5, f"expected real pattern laps, got {circles}"
    assert abs(tgs - circles) <= max(2, circles // 5), f"circles={circles} tgs={tgs}"


def test_side_orbit_circle_is_not_a_touch_and_go():
    """A closed loop that orbits away from the runway is a circle but NOT a
    touch-and-go."""
    import math
    from app.db import Airport

    airport = Airport(icao="KLMO", iata="LMO", name="x", city="x", country="US",
                      lat=40.1646, lon=-105.163, elevation_ft=5055, is_towered=False)
    runways = [{"runway_id": "29", "lat_threshold": 40.1591, "lon_threshold": -105.1487,
                "heading_deg": 290.0, "length_ft": 4800}]
    params = ScanParams(airport_icao="KLMO", user_lat=40.167, user_lon=-105.10, ring_nm=8.0)

    # A ~1nm-radius orbit centred ~3nm NORTH of the field — never over the runway.
    cx_lat, cx_lon = 40.21, -105.163
    pts = []
    t = 1780000000
    for k in range(60):
        ang = k / 60 * 2 * math.pi
        lat = cx_lat + 0.015 * math.sin(ang)
        lon = cx_lon + 0.020 * math.cos(ang)
        pts.append({"timestamp": t, "lat": lat, "lon": lon,
                    "geo_altitude_ft": airport.elevation_ft + 1200,
                    "baro_altitude_ft": airport.elevation_ft + 1200,
                    "icao24": "side01", "callsign": "SIDE", "on_ground": False})
        t += 12
    circles = [e for e in detectors.detect_circles(pts, airport, params)]
    tgs = detectors.touch_and_gos_from_circles(circles)
    assert len(circles) >= 1, "should detect the orbit as a circle"
    assert tgs == [], "an off-field orbit must not be a touch-and-go"
