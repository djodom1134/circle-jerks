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


from fastapi.testclient import TestClient
from app.main import app
from app.settings import get_settings


def _seed_klmo_offender(conn):
    # One clear offender at KLMO: enough T&G + circles to clear the VNAP gate,
    # low passes over homes and quiet-hours ops to push the violation score up.
    base = 1780000000
    def op(oid, type_, ts, min_agl=None, turn=None, runway=None):
        db.upsert_operation(conn, db.operation_from_event({
            "id": oid, "type": type_, "icao24": "a5a764", "callsign": "N4632F",
            "timestamp": ts, "airport_icao": "KLMO", "runway_id": runway,
            "turn_direction": turn, "min_altitude_ft_agl": min_agl,
        }))
    for i in range(6):
        op(f"g{i}", "touch_and_go", base + i * 10, turn="right")
    for i in range(6):
        op(f"c{i}", "circle", base + 100 + i * 10, turn="right")
    for i in range(4):
        op(f"p{i}", "pass_over_user", base + 200 + i * 10, min_agl=150)  # very low -> altitude violation
    conn.commit()


def test_build_worst_offenders_ranks_and_reports(tmp_path):
    conn = seeded_conn(tmp_path / "t.sqlite3")
    _seed_klmo_offender(conn)
    out = services.build_worst_offenders(conn, "KLMO", now=1780000000 + 100000, limit=5)
    assert out["resolved_icao"] == "KLMO"
    assert out["is_fallback"] is False
    assert out["offenders"], "expected at least one scored offender"
    top = out["offenders"][0]
    assert top["tail"] == "N4632F"
    assert top["total_circles"] == 6
    assert top["worst_axis"] is not None


def test_worst_offenders_endpoint_and_fallback(tmp_path, monkeypatch):
    monkeypatch.setenv("CIRCLEJERK_DATABASE_PATH", str(tmp_path / "circlejerk.sqlite3"))
    monkeypatch.setenv("CIRCLEJERK_REDIS_URL", "memory://")
    monkeypatch.setenv("CIRCLEJERK_ENVIRONMENT", "test")
    get_settings.cache_clear()
    with TestClient(app) as client:
        # Seed the offender into the app's DB via a direct connection.
        from app.db import connect as _connect
        conn = _connect(str(tmp_path / "circlejerk.sqlite3"))
        _seed_klmo_offender(conn)
        conn.close()

        ok = client.get("/airports/KLMO/worst_offenders", params={"limit": 5})
        assert ok.status_code == 200
        assert ok.json()["resolved_icao"] == "KLMO"
        assert len(ok.json()["offenders"]) >= 1

        # KBJC has no ops -> falls back to the nearest OTHER airport that does.
        fb = client.get("/airports/KBJC/worst_offenders", params={"limit": 5})
        assert fb.status_code == 200
        body = fb.json()
        assert body["source_icao"] == "KBJC"
        assert body["is_fallback"] is True
        assert body["resolved_icao"] != "KBJC"

        missing = client.get("/airports/ZZZZ/worst_offenders")
        assert missing.status_code == 404
