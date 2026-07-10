from __future__ import annotations

from app import vnap
from app.vnap import ruleset_for


R = ruleset_for("KLMO")


def test_axes_order():
    assert vnap.AXES == ["tightness", "altitude", "timeofday", "tg_volume",
                         "circle_restraint", "left_traffic", "runway29"]
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
def test_off_pattern_fraction_is_time_weighted():
    """The tightness axis scored MEAN perpendicular distance, so N737JR — who
    sat outside the VNAP corridor 66-73% of every lap, peaking 1.75nm off —
    scored a mild 29 because his mean distance was only 0.29nm. Score the time
    spent outside the corridor instead: it is already computed and stored, it
    is in real units, and it neither saturates nor needs calibration.

    Time-weighted, not a mean of per-lap percentages: a long lap outside the
    corridor must not be cancelled by a short one inside it.
    """
    rows = [
        {"time_off_pattern_s": 200, "time_total_s": 300},   # 66.7%
        {"time_off_pattern_s": 220, "time_total_s": 300},   # 73.3%
    ]
    frac = vnap.off_pattern_fraction(rows)
    assert frac == round((200 + 220) / 600, 3)              # 0.7

    # Compliance 30 -> the published VIOLATION score is 70.
    assert vnap.off_pattern_score(frac) == 30.0
    assert vnap.off_pattern_score(0.0) == 100.0             # never leaves corridor
    assert vnap.off_pattern_score(1.0) == 0.0               # never inside it
    assert vnap.off_pattern_score(None) is None


def test_off_pattern_fraction_ignores_laps_without_timing():
    rows = [
        {"time_off_pattern_s": None, "time_total_s": None},
        {"time_off_pattern_s": 100, "time_total_s": 400},
    ]
    assert vnap.off_pattern_fraction(rows) == 0.25
    assert vnap.off_pattern_fraction([{"time_off_pattern_s": 5, "time_total_s": 0}]) is None
    assert vnap.off_pattern_fraction([]) is None


def test_tightness_axis_reports_time_off_pattern(tmp_path):
    """End to end: the published `tightness` axis must read ~68 for an aircraft
    outside the corridor ~68% of the time, not ~29 from its mean distance."""
    from app import db

    conn = db.connect(str(tmp_path / "t.sqlite3"))
    conn.executescript(db.SCHEMA); db.seed_db(conn)
    base = 1780000000
    for i in range(6):                      # >= tightness_min_circles
        db.upsert_operation(conn, db.operation_from_event({
            "id": f"c{i}", "type": "circle", "icao24": "zz99", "callsign": "N737JR",
            "timestamp": base + i, "airport_icao": "KLMO",
        }))
        conn.execute(
            "UPDATE operations SET deviation_mean_nm=?, time_off_pattern_s=?, time_total_s=?, "
            "pct_off_pattern=? WHERE id=?",
            (0.29, 204, 300, 0.68, f"c{i}"),
        )
    conn.commit()

    out = vnap.compute_aircraft_compliance(conn, "KLMO", base - 10, base + 100)
    ac = next(a for a in out["aircraft"] if a["icao24"] == "zz99")
    assert ac["scores"]["tightness"] == 68.0


def test_tightness_axis_needs_enough_laps(tmp_path):
    """Below tightness_min_circles the axis is skipped, not fabricated. A single
    wide transient loop must not publish a maximal violation."""
    from app import db

    conn = db.connect(str(tmp_path / "g.sqlite3"))
    conn.executescript(db.SCHEMA); db.seed_db(conn)
    base = 1780000000
    for i in range(R.tightness_min_circles - 1):        # one short of the gate
        db.upsert_operation(conn, db.operation_from_event({
            "id": f"g{i}", "type": "circle", "icao24": "yy88", "callsign": "N1",
            "timestamp": base + i, "airport_icao": "KLMO",
        }))
        conn.execute(
            "UPDATE operations SET time_off_pattern_s=?, time_total_s=? WHERE id=?",
            (300, 300, f"g{i}"),                       # 100% off pattern
        )
    conn.commit()
    out = vnap.compute_aircraft_compliance(conn, "KLMO", base - 10, base + 100)
    ac = next(a for a in out["aircraft"] if a["icao24"] == "yy88")
    assert ac["scores"]["tightness"] is None


def test_pattern_work_gate_matches_deinflated_circle_counts():
    """The VNAP score gate (min circles) was calibrated when circles were
    over-counted ~2.8x. After the one-row-per-lap fix, 10 circles means 10 real
    laps — so genuine offenders doing ~6 laps scored 0. The floor must sit at a
    handful of real laps, not ten."""
    assert R.score_min_circles <= 6, "gate must judge a ~6-lap offender"

    # An aircraft with a modest number of real laps AND runway pattern work is
    # judged (non-zero), not gated to 0.
    assert R.score_min_circles <= 6 and R.score_min_tg <= 1


def test_score_gate_uses_lap_count_robust_to_tg_over_circles():
    """A lap over the runway is both a circle and a T&G, so T&G should never
    exceed circles. Historical episode T&G rows break that, leaving e.g. 2
    circles + 4 T&G. The pattern-work gate must judge such an aircraft (4 laps
    over the runway is clearly pattern work), not zero it on circles<3."""
    from datetime import datetime
    from zoneinfo import ZoneInfo
    from app import db, vnap

    conn = db.connect(":memory:"); conn.executescript(db.SCHEMA); db.seed_db(conn); conn.commit()
    # 3 AM Denver -> a quiet-hours (time-of-day) violation, so a judged aircraft
    # scores > 0 and the test isolates the GATE from mere compliance.
    base = int(datetime(2026, 5, 28, 3, 0, tzinfo=ZoneInfo("America/Denver")).timestamp())
    for i in range(2):   # only 2 circles...
        db.upsert_operation(conn, db.operation_from_event({
            "id": f"c{i}", "type": "circle", "icao24": "zz", "callsign": "N738BJ",
            "timestamp": base + i, "airport_icao": "KLMO", "turn_direction": "left"}))
    for i in range(4):   # ...but 4 touch-and-gos over the runway
        db.upsert_operation(conn, db.operation_from_event({
            "id": f"t{i}", "type": "touch_and_go", "icao24": "zz", "callsign": "N738BJ",
            "timestamp": base + 10 + i, "airport_icao": "KLMO", "runway_id": "11"}))
    conn.commit()
    ac = next(a for a in vnap.compute_aircraft_compliance(conn, "KLMO", base - 5, base + 100)["aircraft"]
              if a["icao24"] == "zz")
    assert ac["vnap_score"] is not None and ac["vnap_score"] > 0.0, "pattern work must be judged, not gated"
