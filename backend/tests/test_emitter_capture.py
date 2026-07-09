from __future__ import annotations

from app.live_sources import parse_readsb_aircraft
from app.opensky import parse_state_vector, OPENSKY_CATEGORY_CODES


def test_readsb_captures_category():
    row = {"hex": "a1b2c3", "lat": 40.1, "lon": -105.1, "flight": "N123",
           "alt_baro": 5000, "gs": 80, "track": 120, "t": "C172", "category": "A1"}
    sample = parse_readsb_aircraft(row, payload_now=1_700_000_000, source="adsbfi")
    assert sample["emitter_category"] == "A1"


def test_readsb_missing_category_is_none():
    row = {"hex": "a1b2c3", "lat": 40.1, "lon": -105.1}
    sample = parse_readsb_aircraft(row, payload_now=1_700_000_000, source="adsbfi")
    assert sample["emitter_category"] is None


def test_opensky_maps_numeric_category():
    # 17-element rows previously returned None; index 17 = emitter category (2 = Light).
    row = ["abc123", "N1  ", "US", 1_700_000_000, 1_700_000_000,
           -105.1, 40.1, 1500.0, False, 40.0, 120.0, 0.0, None, 1600.0, None, False, 0, 2]
    sample = parse_state_vector(row, fallback_ts=1_700_000_000)
    assert sample["emitter_category"] == "A1"
    assert OPENSKY_CATEGORY_CODES[9] == "B1"  # glider


def test_opensky_short_row_has_no_category():
    row = ["abc123", "N1  ", "US", 1_700_000_000, 1_700_000_000,
           -105.1, 40.1, 1500.0, False, 40.0, 120.0, 0.0, None, 1600.0]  # len 14
    sample = parse_state_vector(row, fallback_ts=1_700_000_000)
    assert sample is not None
    assert sample["emitter_category"] is None
