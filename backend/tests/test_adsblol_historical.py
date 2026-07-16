"""Unit tests for the adsb.lol historical backfill parser.

We test the pure trace_to_samples function without hitting the network
or filesystem so the suite stays hermetic.
"""

from app.adsblol_historical import trace_to_samples


def _bbox():
    # (south, west, north, east) — small box around KSEA-like coords.
    return (47.35, -122.40, 47.55, -122.20)


def test_trace_to_samples_filters_by_bbox():
    payload = {
        "icao": "a7b3c2",
        "r": "N596AS",
        "t": "B738",
        "timestamp": 1_780_000_000.0,
        "trace": [
            # inside bbox
            [0.0, 47.45, -122.30, 5000.0, 250.0, 90.0, 1500.0, None, "adsb", 5100.0],
            # outside bbox (lat too high)
            [60.0, 48.50, -122.30, 5500.0, 252.0, 91.0, 0.0, None, "adsb", 5600.0],
            # outside bbox (lon too east)
            [120.0, 47.45, -100.00, 5500.0, 252.0, 91.0, 0.0, None, "adsb", 5600.0],
            # inside bbox again
            [180.0, 47.50, -122.35, 4000.0, 240.0, 95.0, -500.0, {"flight": "ASA237"}, "adsb", 4100.0],
        ],
    }
    samples = trace_to_samples(
        payload,
        window_start=1_780_000_000 - 3600,
        window_end=1_780_000_000 + 3600,
        bbox=_bbox(),
    )
    assert len(samples) == 2
    assert samples[0]["lat"] == 47.45
    assert samples[0]["lon"] == -122.30
    assert samples[0]["timestamp"] == 1_780_000_000
    assert samples[1]["timestamp"] == 1_780_000_180
    assert samples[1]["callsign"] == "ASA237"
    assert samples[0]["source"] == "adsblol_historical"


def test_trace_to_samples_filters_by_time_window():
    payload = {
        "timestamp": 1_780_000_000.0,
        "trace": [
            # before window
            [-100.0, 47.45, -122.30, 5000.0, 250.0, 90.0, 0.0, None, "adsb", 5100.0],
            # in window
            [50.0, 47.45, -122.30, 5000.0, 250.0, 90.0, 0.0, None, "adsb", 5100.0],
            # after window
            [10_000.0, 47.45, -122.30, 5000.0, 250.0, 90.0, 0.0, None, "adsb", 5100.0],
        ],
    }
    samples = trace_to_samples(
        payload,
        window_start=1_780_000_000,
        window_end=1_780_000_000 + 600,
        bbox=_bbox(),
    )
    assert len(samples) == 1
    assert samples[0]["timestamp"] == 1_780_000_050


def test_trace_to_samples_handles_ground_altitude():
    payload = {
        "timestamp": 1_780_000_000.0,
        "trace": [
            [0.0, 47.45, -122.30, "ground", 0.0, 0.0, 0.0, None, "adsb", "ground"],
        ],
    }
    samples = trace_to_samples(
        payload,
        window_start=1_780_000_000 - 60,
        window_end=1_780_000_000 + 60,
        bbox=_bbox(),
    )
    assert len(samples) == 1
    assert samples[0]["baro_altitude_ft"] == 0.0
    assert samples[0]["geo_altitude_ft"] == 0.0
    assert samples[0]["altitude_ft"] == 0.0


def test_trace_to_samples_callsign_carries_forward():
    """When the payload provides a registration ('r'), that's the initial
    fallback before any in-trace callsign appears. Once a trace point
    reveals an operational callsign, later points inherit it."""
    payload = {
        "r": "N596AS",
        "timestamp": 1_780_000_000.0,
        "trace": [
            [0.0, 47.45, -122.30, 5000.0, 250.0, 90.0, 0.0, None, "adsb", 5100.0],
            [30.0, 47.46, -122.31, 5100.0, 252.0, 92.0, 0.0, {"flight": "ASA237"}, "adsb", 5200.0],
            [60.0, 47.47, -122.32, 5200.0, 254.0, 94.0, 0.0, None, "adsb", 5300.0],
        ],
    }
    samples = trace_to_samples(
        payload,
        window_start=1_780_000_000 - 60,
        window_end=1_780_000_000 + 600,
        bbox=_bbox(),
    )
    assert [s["callsign"] for s in samples] == ["N596AS", "ASA237", "ASA237"]


def test_trace_to_samples_callsign_none_when_no_registration():
    """No 'r' and no in-trace flight → callsign stays None."""
    payload = {
        "timestamp": 1_780_000_000.0,
        "trace": [
            [0.0, 47.45, -122.30, 5000.0, 250.0, 90.0, 0.0, None, "adsb", 5100.0],
        ],
    }
    samples = trace_to_samples(
        payload,
        window_start=1_780_000_000 - 60,
        window_end=1_780_000_000 + 60,
        bbox=_bbox(),
    )
    assert samples[0]["callsign"] is None


def test_trace_to_samples_returns_empty_on_missing_fields():
    assert trace_to_samples({}, window_start=0, window_end=1, bbox=_bbox()) == []
    assert trace_to_samples({"trace": []}, window_start=0, window_end=1, bbox=_bbox()) == []
    assert trace_to_samples(
        {"timestamp": 1, "trace": [[0.0]]},  # too-short row
        window_start=0, window_end=10, bbox=_bbox(),
    ) == []
