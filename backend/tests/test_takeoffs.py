from __future__ import annotations

from app.db import Airport
from app.detectors import (
    detect_takeoffs_over_period,
    detect_touch_and_gos_over_period,
    event_counts,
)

RWY = [{"runway_id": "30R", "lat_threshold": 39.8962, "lon_threshold": -105.1013,
        "heading_deg": 300, "length_ft": 9000}]


def _ap():
    return Airport(icao="KBJC", iata="BJC", name="RMMA", city="Broomfield, CO",
                   country="US", lat=39.9088, lon=-105.1172, elevation_ft=5673, is_towered=True)


def _s(ts, agl, vr=0, on_ground=False):
    # Sits on the 30R threshold so _nearest_runway distance ~= 0.
    return {"icao24": "dep01", "callsign": "N9", "timestamp": ts,
            "lat": 39.8962, "lon": -105.1013,
            "geo_altitude_ft": _ap().elevation_ft + agl,
            "velocity_kt": 60, "vertical_rate_fpm": vr, "on_ground": on_ground,
            "emitter_category": "A1"}


def _departure_track():
    # Starts on the ground and climbs out — no prior high-altitude approach.
    return [_s(1000, 0, vr=0, on_ground=True), _s(1030, 15, vr=600),
            _s(1060, 120, vr=800), _s(1120, 400, vr=700), _s(1200, 800, vr=500)]


def _touch_and_go_track():
    # Approaches from pattern altitude (>=500 AGL), touches, climbs out.
    return [_s(900, 900, vr=-400), _s(960, 500, vr=-500), _s(1000, 20, vr=0),
            _s(1060, 160, vr=700), _s(1120, 500, vr=600), _s(1200, 900, vr=400)]


def test_departure_is_takeoff():
    events = detect_takeoffs_over_period(_departure_track(), _ap(), RWY, 1000, 1200)
    assert [e["type"] for e in events] == ["takeoff"]
    assert events[0]["emitter_category"] == "A1"


def test_touch_and_go_is_not_a_takeoff():
    takeoffs = detect_takeoffs_over_period(_touch_and_go_track(), _ap(), RWY, 900, 1200)
    assert takeoffs == []
    tgs = detect_touch_and_gos_over_period(_touch_and_go_track(), _ap(), RWY, 900, 1200)
    assert "touch_and_go" in [e["type"] for e in tgs]


def test_departure_is_not_a_touch_and_go():
    tgs = detect_touch_and_gos_over_period(_departure_track(), _ap(), RWY, 1000, 1200)
    assert tgs == []  # no approach from altitude -> not a T&G


def test_event_counts_includes_takeoffs():
    counts = event_counts([{"type": "takeoff"}, {"type": "landing"}, {"type": "takeoff"}])
    assert counts["takeoffs"] == 2
    assert counts["landings"] == 1
