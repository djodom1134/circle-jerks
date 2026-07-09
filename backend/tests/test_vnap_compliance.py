from __future__ import annotations

from app import db, vnap


def seeded_conn(path):
    conn = db.connect(str(path))
    conn.executescript(db.SCHEMA)
    db.seed_db(conn)
    conn.commit()
    return conn


def _op(conn, oid, type_, ts, icao24="a1", dev=None, turn=None, runway=None,
        wind_from=None, wind_speed=None, min_agl=None, runway_heading=None):
    db.upsert_operation(conn, db.operation_from_event({
        "id": oid, "type": type_, "icao24": icao24, "callsign": icao24.upper(),
        "timestamp": ts, "airport_icao": "KLMO", "runway_id": runway,
        "runway_heading_deg": runway_heading,
        "turn_direction": turn, "min_altitude_ft_agl": min_agl,
    }))
    if dev is not None or wind_from is not None:
        conn.execute(
            "UPDATE operations SET deviation_mean_nm=?, wind_from_deg=?, wind_speed_kt=? WHERE id=?",
            (dev, wind_from, wind_speed, oid),
        )


def test_compliance_basic_counts_and_scores(tmp_path):
    conn = seeded_conn(tmp_path / "t.sqlite3")
    base = 1780000000
    # One aircraft: 2 landings + 1 takeoff + 1 T&G (=4 operations), 1 circle.
    _op(conn, "l1", "landing", base + 10, "aa11", turn="left", runway="29",
        wind_from=290, wind_speed=10)     # rwy 29, wind favors 29 -> compliant pref
    _op(conn, "l2", "landing", base + 20, "aa11", turn="left")
    _op(conn, "t1", "takeoff", base + 30, "aa11", turn="left")
    _op(conn, "g1", "touch_and_go", base + 40, "aa11", turn="right")
    _op(conn, "c1", "circle", base + 50, "aa11", dev=0.0)
    # Altitude axis is sourced from pass-over-user ops (real circle rows never
    # carry min_altitude_ft_agl -- see test_altitude_axis_sourced_from_passes_not_circles).
    _op(conn, "p1", "pass_over_user", base + 55, "aa11", min_agl=1000)
    conn.commit()

    out = vnap.compute_aircraft_compliance(conn, "KLMO", base, base + 100)

    assert out["axes"] == vnap.AXES
    ac = next(a for a in out["aircraft"] if a["icao24"] == "aa11")
    assert ac["operations"] == 4          # circle excluded from operations count
    assert ac["circles"] == 1
    assert ac["touch_and_gos"] == 1
    # Scores are VIOLATION scores now: 0 = fully compliant, higher = more infractions.
    assert ac["scores"]["tightness"] == 0.0        # dev 0.0 -> no tightness violation
    assert ac["scores"]["altitude"] == 0.0         # pass-over-user min agl 1000 -> compliant
    # left_traffic: 3 of 4 direction-known ops are left -> 25% not-left violation
    assert ac["scores"]["left_traffic"] == 25.0
    # runway29: 1 op where 29 favored, 1 used 29 -> no violation
    assert ac["scores"]["runway29"] == 0.0
    assert ac["vnap_score"] is not None
    assert "composite" in out["averages"]


def test_altitude_axis_sourced_from_passes_not_circles(tmp_path):
    conn = seeded_conn(tmp_path / "t.sqlite3")
    base = 1780000000
    # A circle with no min_altitude (as the real circle detector emits) -> no altitude signal.
    _op(conn, "c1", "circle", base + 10, "cc33", dev=0.2)
    # A pass over the user at 800 ft AGL -> 80% compliant -> 20 altitude violation.
    _op(conn, "p1", "pass_over_user", base + 20, "cc33", min_agl=800)
    conn.commit()
    out = vnap.compute_aircraft_compliance(conn, "KLMO", base, base + 100)
    ac = next(a for a in out["aircraft"] if a["icao24"] == "cc33")
    assert ac["scores"]["altitude"] == 20.0


def test_missing_axis_is_none_and_excluded_from_composite(tmp_path):
    conn = seeded_conn(tmp_path / "t.sqlite3")
    base = 1780000000
    # Aircraft with no circle -> tightness/altitude None; no wind -> runway29 None.
    _op(conn, "l1", "landing", base + 10, "bb22", turn="left")
    conn.commit()
    out = vnap.compute_aircraft_compliance(conn, "KLMO", base, base + 100)
    ac = next(a for a in out["aircraft"] if a["icao24"] == "bb22")
    assert ac["scores"]["tightness"] is None
    assert ac["scores"]["runway29"] is None
    # composite is the mean of only the non-None axes (left_traffic + timeofday here)
    present = [v for k, v in ac["scores"].items() if v is not None]
    assert ac["vnap_score"] == round(sum(present) / len(present), 1)


def test_compliance_uses_community_owner_override(tmp_path):
    conn = seeded_conn(tmp_path / "t.sqlite3")
    base = 1780000000
    _op(conn, "l1", "landing", base + 10, "dd44")
    conn.execute(
        "INSERT INTO aircraft_registry (n_number, icao_hex, owner_type) VALUES (?, ?, ?)",
        ("N9", "DD44", "individual"),
    )
    db.set_owner_override(conn, "dd44", "flight_school", editor_visitor_id="v-12345678")
    conn.commit()
    out = vnap.compute_aircraft_compliance(conn, "KLMO", base, base + 100)
    ac = next(a for a in out["aircraft"] if a["icao24"] == "dd44")
    assert ac["owner_class"] == "flight_school"   # override beats registry "individual"
    assert ac["owner_source"] == "community"


def test_compliance_icao24s_filter_matches_unscoped_score(tmp_path):
    conn = seeded_conn(tmp_path / "t.sqlite3")
    base = 1780000000
    _op(conn, "l1", "landing", base + 10, "aa11", turn="left")
    _op(conn, "l2", "landing", base + 20, "bb22", turn="right")
    conn.commit()
    full = vnap.compute_aircraft_compliance(conn, "KLMO", base, base + 100)
    scoped = vnap.compute_aircraft_compliance(conn, "KLMO", base, base + 100, icao24s=["aa11"])
    assert [a["icao24"] for a in scoped["aircraft"]] == ["aa11"]
    full_aa = next(a for a in full["aircraft"] if a["icao24"] == "aa11")
    scoped_aa = scoped["aircraft"][0]
    assert scoped_aa["vnap_score"] == full_aa["vnap_score"]
    assert scoped_aa["scores"] == full_aa["scores"]


def test_vnap_score_gated_until_pattern_work(tmp_path):
    conn = seeded_conn(tmp_path / "t.sqlite3")
    base = 1780000000
    # gate1 clearly violates (low pass = altitude infraction, off-pattern circles)
    # but only 3 circles + 0 T&G -> UNDER the gate -> score stays 0.
    _op(conn, "p0", "pass_over_user", base + 5, "gate1", min_agl=200)
    for i in range(3):
        _op(conn, f"c{i}", "circle", base + 10 + i, "gate1", dev=0.9)
    # work1 has the same violations but is doing real pattern work
    # (10 circles + 1 T&G) -> gate passes -> real, non-zero score.
    _op(conn, "tg", "touch_and_go", base + 5, "work1")
    _op(conn, "p1", "pass_over_user", base + 6, "work1", min_agl=200)
    for i in range(10):
        _op(conn, f"w{i}", "circle", base + 20 + i, "work1", dev=0.9)
    conn.commit()

    out = vnap.compute_aircraft_compliance(conn, "KLMO", base, base + 100)
    gated = next(a for a in out["aircraft"] if a["icao24"] == "gate1")
    worked = next(a for a in out["aircraft"] if a["icao24"] == "work1")

    assert gated["circles"] == 3 and gated["touch_and_gos"] == 0
    assert gated["vnap_score"] == 0.0                 # under the gate -> stays 0
    # but its per-axis breakdown is still computed (not zeroed)
    assert gated["scores"]["tightness"] and gated["scores"]["tightness"] > 0

    assert worked["circles"] == 10 and worked["touch_and_gos"] == 1
    assert worked["vnap_score"] is not None and worked["vnap_score"] > 0.0


def test_metrics_real_units(tmp_path):
    conn = seeded_conn(tmp_path / "t.sqlite3")
    base = 1780000000
    icao24 = "ee55"
    for i in range(2):
        _op(conn, f"tg{i}", "touch_and_go", base + 5 + i, icao24, turn="left")
    for i in range(20):
        _op(conn, f"c{i}", "circle", base + 10 + i, icao24, dev=0.9)
    _op(conn, "p1", "pass_over_user", base + 40, icao24, min_agl=500)
    # Takeoff on non-preferred runway "11" (heading 110) with a tailwind
    # (wind from 290 -> straight down runway 11's back -> headwind component < 0).
    _op(conn, "t1", "takeoff", base + 50, icao24, runway="11",
        runway_heading=110.0, wind_from=290, wind_speed=10)
    conn.commit()

    out = vnap.compute_aircraft_compliance(conn, "KLMO", base, base + 100)
    ac = next(a for a in out["aircraft"] if a["icao24"] == icao24)
    metrics = ac["metrics"]

    # All ops fall on the same local day -> active_days == 1, so per-day rates == raw counts.
    assert metrics["tg_volume"] == 2 / 1.0 - 10        # -8.0: well under the 10/day limit
    assert metrics["circle_restraint"] == 20 / 1.0     # 20.0 circles/day
    assert metrics["altitude"] == 100.0                # the one pass (500ft) is < 1000ft target
    assert metrics["left_traffic"] == 100.0            # only known-direction ops (the 2 T&Gs) were left
    assert metrics["preferred_runway"] == 0.0          # the one takeoff used rwy 11, not 29
    assert metrics["rwy_against"] == 100.0             # that takeoff had a tailwind


def test_metrics_per_day_uses_active_days_only(tmp_path):
    conn = seeded_conn(tmp_path / "t.sqlite3")
    base = 1780000000
    icao24 = "dd66"
    # 20 circles spread across 2 distinct local days.
    for i in range(10):
        _op(conn, f"a{i}", "circle", base + i, icao24, dev=0.5)
    for i in range(10):
        _op(conn, f"b{i}", "circle", base + 86400 + i, icao24, dev=0.5)
    conn.commit()
    # Wide 4-day window, but the aircraft is present on only 2 days.
    out = vnap.compute_aircraft_compliance(conn, "KLMO", base - 86400, base + 3 * 86400)
    ac = next(a for a in out["aircraft"] if a["icao24"] == icao24)
    # 20 circles / 2 ACTIVE days = 10.0 (not divided by the 4-day window).
    assert ac["metrics"]["circle_restraint"] == 10.0
