from datetime import datetime
from zoneinfo import ZoneInfo

from app.scoring import event_histogram
from app.windows import WindowRange


def _win(seconds):
    return WindowRange(code="1h", label="1 hour", start_ts=0, end_ts=seconds, seconds=seconds)


def _ts(y, mo, d, h, tz="America/Denver"):
    return int(datetime(y, mo, d, h, tzinfo=ZoneInfo(tz)).timestamp())


def test_event_histogram_counts_landing_events():
    # A 'landing' event must not blow up the histogram (regression: the bucket
    # factory omitted 'landing', so a landing raised KeyError and 404'd /scan).
    events = [
        {"type": "landing", "timestamp": 100},
        {"type": "touch_and_go", "timestamp": 100},
        {"type": "circle", "timestamp": 100},
    ]
    hist = event_histogram(events, _win(3600), "America/Denver")
    assert len(hist) == 1
    bucket = hist[0]
    assert bucket["landing"] == 1
    assert bucket["touch_and_go"] == 1
    assert bucket["circle"] == 1


def test_event_histogram_tolerates_unknown_type():
    # An unfamiliar op type should never crash the whole scan.
    hist = event_histogram([{"type": "future_op", "timestamp": 100}], _win(3600), "America/Denver")
    assert len(hist) == 1


def test_event_histogram_buckets_are_chronological_across_the_am_pm_boundary():
    """Buckets were sorted by their rendered label, so "10 AM" sorted before
    "9 AM". Sort by the bucket's instant instead."""
    events = [
        {"type": "circle", "timestamp": _ts(2026, 7, 9, 9)},
        {"type": "circle", "timestamp": _ts(2026, 7, 9, 10)},
        {"type": "circle", "timestamp": _ts(2026, 7, 9, 11)},
    ]
    window = WindowRange(
        code="6h", label="6h",
        start_ts=_ts(2026, 7, 9, 8), end_ts=_ts(2026, 7, 9, 12), seconds=4 * 3600,
    )
    hist = event_histogram(events, window, "America/Denver")
    assert [row["bucket"] for row in hist] == ["9 AM", "10 AM", "11 AM"]


def test_event_histogram_separates_same_hour_on_different_days():
    """A rolling 24h window covers each clock hour twice. Yesterday 3 PM and
    today 3 PM must not collapse into one bucket."""
    events = [
        {"type": "circle", "timestamp": _ts(2026, 7, 8, 15)},
        {"type": "circle", "timestamp": _ts(2026, 7, 9, 15)},
    ]
    window = WindowRange(
        code="today", label="in the last 24 hours",
        start_ts=_ts(2026, 7, 8, 15), end_ts=_ts(2026, 7, 9, 15), seconds=24 * 3600,
    )
    hist = event_histogram(events, window, "America/Denver")

    assert len(hist) == 2, "same clock hour on two days must be two buckets"
    assert [row["circle"] for row in hist] == [1, 1]
    assert hist[0]["bucket"] != hist[1]["bucket"]
    # Chronological, and the day is disambiguated in the label.
    assert "07-08" in hist[0]["bucket"] and "07-09" in hist[1]["bucket"]


def test_runway_breakdown_counts_only_touch_and_gos():
    """The offenders row shows a T&G count with a per-runway breakdown beneath
    it; the breakdown must sum to the T&G count, not silently fold in low
    approaches (which made 4 T&G render as '5x11 1x29' = 6)."""
    from app.scoring import offender_rows

    events = [
        {"type": "touch_and_go", "icao24": "aa", "callsign": "N", "timestamp": 100, "runway_id": "11"},
        {"type": "touch_and_go", "icao24": "aa", "callsign": "N", "timestamp": 200, "runway_id": "11"},
        {"type": "touch_and_go", "icao24": "aa", "callsign": "N", "timestamp": 300, "runway_id": "29"},
        {"type": "low_approach", "icao24": "aa", "callsign": "N", "timestamp": 400, "runway_id": "11"},
        {"type": "low_approach", "icao24": "aa", "callsign": "N", "timestamp": 500, "runway_id": "11"},
    ]
    row = offender_rows(events, _win(3600), "America/Denver")[0]
    assert row["touch_and_gos"] == 3
    assert row["runway_breakdown"] == {"11": 2, "29": 1}
    assert sum(row["runway_breakdown"].values()) == row["touch_and_gos"]
