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


def test_tightness_uses_the_typical_op_not_the_mean():
    """The tightness axis summarised an aircraft's per-op deviations with an
    arithmetic mean. Those deviations are bimodal — a tight-pattern trainer sits
    around 0.1 nm but throws the occasional 3 nm go-around — so one excursion
    tripled the mean and made a tight flyer score the same as a loose one.

    Real KLMO data:
      N971KC: 11 ops in 0.07-0.20 nm + one 3.12  -> median 0.143, mean 0.389
      N1218S: 48 ops in 0.07-0.57 nm + 14 wide   -> median 0.351, mean 0.772
    A 2.5x difference in typical tightness collapsed to 0.389 vs 0.42.
    """
    tight = [0.07, 0.10, 0.10, 0.12, 0.13, 0.14, 0.14, 0.17, 0.18, 0.19, 0.20, 3.12]
    loose = [0.30, 0.33, 0.35, 0.35, 0.36, 0.40, 0.42, 2.20, 2.29, 2.56]

    tight_dev = vnap.typical_deviation_nm(tight)
    loose_dev = vnap.typical_deviation_nm(loose)

    # The single 3.12 outlier must not drag the tight flyer up to the loose one.
    assert tight_dev == 0.14
    assert loose_dev == 0.38

    tight_score = vnap.tightness_score(tight_dev, R)
    loose_score = vnap.tightness_score(loose_dev, R)
    # Separated by a wide, visible margin on a 0-100 axis.
    assert tight_score - loose_score >= 20, (tight_score, loose_score)


def test_typical_deviation_handles_empty_and_single():
    assert vnap.typical_deviation_nm([]) is None
    assert vnap.typical_deviation_nm([0.25]) == 0.25
