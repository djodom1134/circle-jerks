from __future__ import annotations

from app import db


def seeded_conn(path):
    conn = db.connect(str(path))
    conn.executescript(db.SCHEMA)
    db.seed_db(conn)
    conn.commit()
    return conn


def test_nearest_airport_excluding_skips_source(tmp_path):
    conn = seeded_conn(tmp_path / "t.sqlite3")
    klmo = db.get_airport(conn, "KLMO")
    out = db.nearest_airport_excluding(conn, klmo.lat, klmo.lon, "KLMO")
    assert out is not None
    assert out["icao"] != "KLMO"
    assert "distance_nm" in out


def test_report_meta_returns_counts_and_last_seen(tmp_path):
    conn = seeded_conn(tmp_path / "t.sqlite3")
    conn.execute(
        "INSERT INTO aircraft_report_counts (icao24, callsign, registration, report_count, first_reported_at, last_reported_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        ("a5a764", "N4632F", "N4632F", 5, 1780000000, 1780009999),
    )
    conn.commit()
    meta = db.report_meta(conn, ["a5a764", "ffffff"])
    assert meta["a5a764"]["report_count"] == 5
    assert meta["a5a764"]["last_reported_at"] == 1780009999
    assert "ffffff" not in meta


def test_report_meta_empty_list(tmp_path):
    conn = seeded_conn(tmp_path / "t.sqlite3")
    assert db.report_meta(conn, []) == {}


from app import services


def _vac(icao24, tail, vnap, circles, scores):
    return {
        "icao24": icao24, "callsign": tail, "registration": tail, "tail": tail,
        "aircraft_type": "Cessna 172", "owner_class": "flight_school",
        "vnap_score": vnap, "circles": circles, "scores": scores,
    }


def test_rank_worst_offenders_orders_by_vnap_times_circles():
    aircraft = [
        _vac("a1", "N1", 40.0, 2, {"altitude": 40.0, "timeofday": 10.0, "tightness": None}),   # 80
        _vac("a2", "N2", 20.0, 10, {"altitude": 5.0, "timeofday": 20.0, "tightness": None}),   # 200
        _vac("a3", "N3", 0.0, 50, {"altitude": None, "timeofday": None, "tightness": None}),   # 0 -> dropped
    ]
    meta = {"a2": {"report_count": 7, "last_reported_at": 1780009999}}
    out = services.rank_worst_offenders(aircraft, meta, limit=5)
    assert [o["icao24"] for o in out] == ["a2", "a1"]  # a3 (product 0) dropped
    assert out[0]["total_circles"] == 10
    assert out[0]["report_count"] == 7
    assert out[0]["last_reported_at"] == 1780009999
    assert out[0]["worst_axis"] == "timeofday"          # 20 > 5
    assert out[1]["worst_axis"] == "altitude"           # 40 > 10
    assert out[1]["report_count"] == 0                  # no meta -> 0


def test_rank_worst_offenders_respects_limit():
    aircraft = [_vac(f"a{i}", f"N{i}", 50.0, i + 1, {"altitude": 50.0}) for i in range(8)]
    out = services.rank_worst_offenders(aircraft, {}, limit=5)
    assert len(out) == 5
