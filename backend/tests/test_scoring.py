from app.scoring import event_histogram
from app.windows import WindowRange


def _win(seconds):
    return WindowRange(code="1h", label="1 hour", start_ts=0, end_ts=seconds, seconds=seconds)


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
