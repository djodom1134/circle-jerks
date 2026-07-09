from __future__ import annotations

from app import db, vnap


def seeded_conn(path):
    conn = db.connect(str(path))
    conn.executescript(db.SCHEMA)
    db.seed_db(conn)
    conn.commit()
    return conn


def _op(conn, oid, type_, ts, icao24="a1", dev=None, turn=None, runway=None,
        wind_from=None, wind_speed=None, min_agl=None):
    db.upsert_operation(conn, db.operation_from_event({
        "id": oid, "type": type_, "icao24": icao24, "callsign": icao24.upper(),
        "timestamp": ts, "airport_icao": "KLMO", "runway_id": runway,
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
    _op(conn, "c1", "circle", base + 50, "aa11", dev=0.0, min_agl=1000)
    conn.commit()

    out = vnap.compute_aircraft_compliance(conn, "KLMO", base, base + 100)

    assert out["axes"] == vnap.AXES
    ac = next(a for a in out["aircraft"] if a["icao24"] == "aa11")
    assert ac["operations"] == 4          # circle excluded from operations count
    assert ac["circles"] == 1
    assert ac["touch_and_gos"] == 1
    assert ac["scores"]["tightness"] == 100.0      # dev 0.0
    assert ac["scores"]["altitude"] == 100.0       # circle min agl 1000
    # left_traffic: 3 of 4 direction-known ops are left -> 75.0
    assert ac["scores"]["left_traffic"] == 75.0
    # runway29: 1 op where 29 favored, 1 used 29 -> 100.0
    assert ac["scores"]["runway29"] == 100.0
    assert ac["vnap_score"] is not None
    assert "composite" in out["averages"]


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
