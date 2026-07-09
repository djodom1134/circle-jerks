from __future__ import annotations

from app import vnap
from app.vnap import ruleset_for


R = ruleset_for("KLMO")


def test_axes_order():
    assert vnap.AXES == ["tightness", "altitude", "timeofday", "tg_volume",
                         "circle_restraint", "left_traffic", "runway29"]


def test_tightness_score():
    assert vnap.tightness_score(0.0, R) == 100.0          # dead on pattern
    assert vnap.tightness_score(R.dev_floor_nm, R) == 0.0  # at the floor -> 0
    assert vnap.tightness_score(0.5, R) == 50.0            # halfway (floor 1.0)
    assert vnap.tightness_score(5.0, R) == 0.0             # clamped, not negative
    assert vnap.tightness_score(None, R) is None


def test_altitude_score():
    assert vnap.altitude_score(1000.0, R) == 100.0   # at/above target
    assert vnap.altitude_score(1500.0, R) == 100.0   # clamped
    assert vnap.altitude_score(0.0, R) == 0.0
    assert vnap.altitude_score(500.0, R) == 50.0
    assert vnap.altitude_score(None, R) is None


def test_timeofday_score():
    assert vnap.timeofday_score(8, 10) == 80.0
    assert vnap.timeofday_score(0, 0) is None


def test_left_traffic_score():
    assert vnap.left_traffic_score(3, 4) == 75.0
    assert vnap.left_traffic_score(0, 0) is None


def test_runway_pref_score():
    assert vnap.runway_pref_score(2, 5) == 40.0
    assert vnap.runway_pref_score(0, 0) is None


def test_session_limit_scores():
    # tg limit 10: a session of 12 -> 100*10/12; a compliant session -> 100.
    assert vnap.tg_volume_score([5, 10], R) == 100.0
    assert vnap.tg_volume_score([20], R) == 50.0
    assert vnap.tg_volume_score([], R) is None
    # circle limit 4
    assert vnap.circle_restraint_score([8], R) == 50.0


def test_composite_skips_missing_axes():
    scores = {"tightness": 100.0, "altitude": None, "timeofday": 50.0}
    # mean of the two present = 75.0
    assert vnap.composite_score(scores, R) == 75.0
    assert vnap.composite_score({"a": None}, R) is None


def test_group_sessions():
    # gap_min=60 -> 3600s. Two clusters separated by > 1h.
    ts = [0, 60, 120, 5000, 5060]
    assert vnap.group_sessions(ts, 60) == [[0, 60, 120], [5000, 5060]]
    assert vnap.group_sessions([], 60) == []
