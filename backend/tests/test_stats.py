from __future__ import annotations

from app import db


def seeded_conn(path):
    conn = db.connect(str(path))
    conn.executescript(db.SCHEMA)
    db.seed_db(conn)
    conn.commit()
    return conn


def _op(conn, oid, type_, ts, icao24="a1", headwind=None, dev=None, time_off=None, runway_id=None):
    db.upsert_operation(conn, db.operation_from_event({
        "id": oid, "type": type_, "icao24": icao24, "callsign": icao24.upper(),
        "timestamp": ts, "airport_icao": "KBJC", "runway_id": runway_id,
    }))
    if headwind is not None or dev is not None:
        conn.execute(
            "UPDATE operations SET headwind_kt=?, deviation_mean_nm=?, time_off_pattern_s=? WHERE id=?",
            (headwind, dev, time_off, oid),
        )


def test_airport_stats(tmp_path):
    conn = seeded_conn(tmp_path / "t.sqlite3")
    _op(conn, "o1", "circle", 1000, "a1", headwind=5.0, dev=0.4, time_off=60)
    _op(conn, "o2", "circle", 2000, "a2", headwind=-3.0, dev=1.2, time_off=120)
    _op(conn, "o3", "touch_and_go", 3000, "a1", runway_id="30R")
    _op(conn, "o4", "pass_over_user", 4000, "a3")
    # wind_favored_new=0 → a genuine against-wind change, i.e. a real cowboy.
    db.insert_runway_change(conn, "KBJC", "12L", "30R", 3000,
                            {"icao24": "a1", "callsign": "A1", "registration": "A1", "id": "o3"}, 300, 8.0, 0)
    db.aircraft_report_counts if False else None  # noqa
    conn.execute(
        "INSERT INTO aircraft_report_counts (icao24, callsign, registration, report_count, first_reported_at, last_reported_at) "
        "VALUES ('a1','A1','A1',7,1000,3000)"
    )
    conn.commit()

    stats = db.airport_stats(conn, "KBJC", 0, 10_000, bucket_seconds=86400, top=10)

    assert stats["counters"]["circles"] == 2
    assert stats["counters"]["touch_and_gos"] == 1
    assert stats["counters"]["passes"] == 1
    assert stats["counters"]["unique_aircraft"] == 3
    assert stats["counters"]["runway_changes"] == 1

    assert sum(b["count"] for b in stats["ops_over_time"]) == 4

    assert stats["wind"]["into_headwind_ops"] == 1   # o1 headwind +5
    assert stats["wind"]["downwind_ops"] == 1        # o2 headwind -3
    assert stats["wind"]["no_wind_data_ops"] == 2    # o3, o4 NULL

    assert stats["deviation"]["scored_ops"] == 2
    assert abs(stats["deviation"]["avg_mean_nm"] - 0.8) < 1e-6
    assert stats["deviation"]["max_nm"] == 1.2
    assert stats["deviation"]["total_time_off_s"] == 180
    assert stats["deviation"]["worst"][0]["icao24"] == "a2"

    assert stats["cowboys"][0]["icao24"] == "a1" and stats["cowboys"][0]["changes"] == 1
    assert stats["repeat_offenders"][0]["icao24"] == "a1"
    assert isinstance(stats["flight_schools"], list)


def test_airport_stats_flight_schools_match(tmp_path):
    # Registry stores icao_hex UPPERCASE; operations store icao24 lowercase. The
    # join must match across that case difference (and use the icao_hex index).
    conn = seeded_conn(tmp_path / "t.sqlite3")
    conn.execute(
        "INSERT INTO aircraft_registry (n_number, icao_hex, registrant_name) VALUES (?,?,?)",
        ("N111AB", "A4C1D8", "Test Flight School"),
    )
    db.upsert_operation(conn, db.operation_from_event({
        "id": "fs1", "type": "touch_and_go", "icao24": "a4c1d8", "callsign": "N111AB",
        "timestamp": 1000, "airport_icao": "KBJC",
    }))
    conn.commit()
    stats = db.airport_stats(conn, "KBJC", 0, 10_000)
    labels = {f["label"]: f["count"] for f in stats["flight_schools"]}
    assert labels.get("Test Flight School") == 1


def test_airport_stats_runway_usage_by_wind(tmp_path):
    # Classify each runway op by the wind's angle to the runway heading:
    # into-wind (<=45deg), crosswind (45-135), downwind/tailwind (>=135).
    conn = seeded_conn(tmp_path / "t.sqlite3")

    def rwop(oid, ts, wind_from):
        db.upsert_operation(conn, db.operation_from_event({
            "id": oid, "type": "touch_and_go", "icao24": "a", "callsign": "N1",
            "timestamp": ts, "airport_icao": "KBJC", "runway_id": "29", "runway_heading_deg": 290,
        }))
        if wind_from is not None:
            db.update_operation_wind(conn, oid, wind_from, 10.0, 0.0)

    rwop("r1", 1000, 290)   # wind from 290, runway 290 → into wind (upwind)
    rwop("r2", 1100, 110)   # wind from 110 (behind) → downwind / tailwind
    rwop("r3", 1200, 200)   # wind from 200 (~90deg) → crosswind
    rwop("r4", 1300, None)  # no wind data
    conn.commit()

    stats = db.airport_stats(conn, "KBJC", 0, 10_000)
    usage = {u["runway_id"]: u for u in stats["runway_usage"]}
    assert usage["29"]["total"] == 4
    assert usage["29"]["upwind"] == 1
    assert usage["29"]["downwind"] == 1
    assert usage["29"]["crosswind"] == 1
    assert usage["29"]["no_wind_data"] == 1


def test_cowboys_excludes_wind_favored_changes(tmp_path):
    # Only changes the wind did NOT favor count as cowboys. A wind-driven change
    # (wind_favored_new=1) is legitimate and must not appear in the leaderboard.
    conn = seeded_conn(tmp_path / "t.sqlite3")
    db.insert_runway_change(conn, "KBJC", "29", "11", 1000,
                            {"icao24": "favored", "callsign": "GOOD", "registration": "GOOD", "id": "c1"}, 60, 4.0, 1)
    db.insert_runway_change(conn, "KBJC", "11", "29", 2000,
                            {"icao24": "against", "callsign": "BADCOW", "registration": "BADCOW", "id": "c2"}, 110, 6.0, 0)
    conn.commit()
    stats = db.airport_stats(conn, "KBJC", 0, 10_000)
    ids = {c["icao24"] for c in stats["cowboys"]}
    assert "against" in ids        # against-wind change → cowboy
    assert "favored" not in ids    # wind-favored change → not a cowboy
    # the total runway-change counter still counts both
    assert stats["counters"]["runway_changes"] == 2


def test_deviation_worst_dedups_by_tail(tmp_path):
    # One aircraft flies many circles → one row per tail, averaged (not repeated).
    conn = seeded_conn(tmp_path / "t.sqlite3")
    for oid, dev in [("d1", 4.0), ("d2", 2.0)]:  # same tail "dup", avg 3.0
        db.upsert_operation(conn, db.operation_from_event({
            "id": oid, "type": "circle", "icao24": "dup", "callsign": "NDUP",
            "timestamp": 1000 + int(oid[1]), "airport_icao": "KBJC",
        }))
        conn.execute("UPDATE operations SET deviation_mean_nm=? WHERE id=?", (dev, oid))
    conn.commit()
    stats = db.airport_stats(conn, "KBJC", 0, 10_000)
    rows = [w for w in stats["deviation"]["worst"] if w["icao24"] == "dup"]
    assert len(rows) == 1                                   # one entry per tail
    assert abs(rows[0]["deviation_mean_nm"] - 3.0) < 1e-6   # averaged
    assert rows[0]["circles"] == 2


def test_stop_classification_includes_circles_and_excludes_passes(tmp_path):
    conn = seeded_conn(tmp_path / "s.sqlite3")
    day = 86400
    for i, t in enumerate(("circle", "circle", "touch_and_go", "low_approach", "landing")):
        _op(conn, f"a{i}", t, day + i)
    _op(conn, "p0", "pass_over_user", day + 9)  # excluded from ratio
    conn.commit()

    stats = db.airport_stats(conn, "KBJC", 0, 10 * day, bucket_seconds=day)
    sc = stats["stop_classification"]
    assert stats["counters"]["landings"] == 1
    assert sc["did_not_stop"] == 4          # 2 circles + 1 t&g + 1 low approach
    assert sc["landed"] == 1
    assert sc["total"] == 5
    assert sc["did_not_stop_pct"] == 80.0


def test_stop_classification_pct_null_when_empty(tmp_path):
    conn = seeded_conn(tmp_path / "s.sqlite3")
    stats = db.airport_stats(conn, "KBJC", 0, 86400)
    assert stats["stop_classification"]["total"] == 0
    assert stats["stop_classification"]["did_not_stop_pct"] is None


def test_stop_over_time_buckets_by_day(tmp_path):
    conn = seeded_conn(tmp_path / "s.sqlite3")
    day = 86400
    # Day 1: 2 non-stop, 0 landed. Day 2: 1 non-stop, 1 landed.
    _op(conn, "d1a", "circle", day + 10)
    _op(conn, "d1b", "touch_and_go", day + 20)
    _op(conn, "d2a", "touch_and_go", 2 * day + 10)
    _op(conn, "d2b", "landing", 2 * day + 20)
    conn.commit()

    stats = db.airport_stats(conn, "KBJC", 0, 10 * day)
    rows = {r["day"]: r for r in stats["stop_over_time"]}
    assert rows[day]["did_not_stop"] == 2 and rows[day]["landed"] == 0 and rows[day]["pct"] == 100.0
    assert rows[2 * day]["did_not_stop"] == 1 and rows[2 * day]["landed"] == 1 and rows[2 * day]["pct"] == 50.0


def test_stats_endpoint(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from app.main import app
    from app.settings import get_settings

    monkeypatch.setenv("CIRCLEJERK_DATABASE_PATH", str(tmp_path / "circlejerk.sqlite3"))
    monkeypatch.setenv("CIRCLEJERK_REDIS_URL", "memory://")
    monkeypatch.setenv("CIRCLEJERK_ENVIRONMENT", "test")
    get_settings.cache_clear()

    with TestClient(app) as client:
        resp = client.get("/airports/KBJC/stats", params={"window": "7d"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["airport_icao"] == "KBJC"
        assert data["window"]["code"] == "7d"
        assert data["window"]["end_ts"] > data["window"]["start_ts"]
        assert "counters" in data and "ops_over_time" in data and "deviation" in data

        # bad window code → 422
        assert client.get("/airports/KBJC/stats", params={"window": "nope"}).status_code == 422

        # all-time window starts at 0
        all_resp = client.get("/airports/KBJC/stats", params={"window": "all"}).json()
        assert all_resp["window"]["start_ts"] == 0
