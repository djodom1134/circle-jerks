from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app
from app.settings import get_settings


def test_healthz_and_airport_search(tmp_path, monkeypatch):
    monkeypatch.setenv("CIRCLEJERK_DATABASE_PATH", str(tmp_path / "circlejerk.sqlite3"))
    monkeypatch.setenv("CIRCLEJERK_REDIS_URL", "memory://")
    monkeypatch.setenv("CIRCLEJERK_ENVIRONMENT", "test")
    get_settings.cache_clear()

    with TestClient(app) as client:
        health = client.get("/healthz")
        assert health.status_code == 200
        assert health.json()["sqlite_seeded"] is True

        search = client.get("/airports/search", params={"q": "KBJC"})
        assert search.status_code == 200
        assert search.json()["airports"][0]["icao"] == "KBJC"

        bad_window = client.get("/scan", params={
            "airport_icao": "KBJC",
            "user_lat": 40,
            "user_lon": -105,
            "window": "week",
        })
        assert bad_window.status_code == 422


def test_admin_dashboard_requires_login_and_tracks_submissions(tmp_path, monkeypatch):
    monkeypatch.setenv("CIRCLEJERK_DATABASE_PATH", str(tmp_path / "circlejerk.sqlite3"))
    monkeypatch.setenv("CIRCLEJERK_REDIS_URL", "memory://")
    monkeypatch.setenv("CIRCLEJERK_ENVIRONMENT", "test")
    monkeypatch.setenv("CIRCLEJERK_ADMIN_PASSWORD", "correct-password")
    get_settings.cache_clear()

    with TestClient(app) as client:
        blocked = client.get("/admin/dashboard")
        assert blocked.status_code == 401

        heartbeat = client.post("/activity/heartbeat", json={
            "visitor_id": "visitor-123456",
            "airport_icao": "KBJC",
            "user_lat": 40.1,
            "user_lon": -105.2,
            "path": "/",
        })
        assert heartbeat.status_code == 200

        submission = client.post("/activity/submissions", json={
            "visitor_id": "visitor-123456",
            "airport_icao": "KBJC",
            "user_lat": 40.1,
            "user_lon": -105.2,
            "window": "1h",
            "mode": "all",
            "text": "Complaint text copied by the user.",
            "targets": [
                {"icao24": "abc123", "callsign": "N123AB"},
                {"icao24": "def456", "callsign": "N456CD"},
            ],
        })
        assert submission.status_code == 200

        bad_login = client.post("/admin/login", json={"username": "admin", "password": "wrong"})
        assert bad_login.status_code == 401

        login = client.post("/admin/login", json={"username": "admin", "password": "correct-password"})
        assert login.status_code == 200

        dashboard = client.get("/admin/dashboard")
        assert dashboard.status_code == 200
        data = dashboard.json()
        assert data["summary"]["submissions"] == 1
        assert data["summary"]["aircraft_reports"] == 2
        assert data["summary"]["current_users"] == 1
        assert data["recent_submissions"][0]["text"] == "Complaint text copied by the user."
        assert len(data["recent_submissions"][0]["aircraft"]) == 2
        counts = {row["icao24"]: row["report_count"] for row in data["aircraft_reports"]}
        assert counts == {"abc123": 1, "def456": 1}


def test_track_history_endpoint(tmp_path, monkeypatch):
    monkeypatch.setenv("CIRCLEJERK_DATABASE_PATH", str(tmp_path / "circlejerk.sqlite3"))
    monkeypatch.setenv("CIRCLEJERK_REDIS_URL", "memory://")
    monkeypatch.setenv("CIRCLEJERK_ENVIRONMENT", "test")
    get_settings.cache_clear()
    from app import db
    settings = get_settings()
    db.init_db(settings.database_path)
    with db.db_session(settings.database_path) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO airports (icao, iata, name, city, country, lat, lon, elevation_ft, is_towered) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("KTST", "TST", "Test Field", "Testville", "US", 40.1, -105.1, 5000, 0),
        )
        # Anchor sample timestamps just before NOW so they fall inside the
        # [NOW - days*86400, NOW] window.
        now = 2000000000
        db.archive_track_samples(conn, "AAA111", [
            {"timestamp": now - 3600, "lat": 40.10, "lon": -105.10, "altitude_ft": 6000},
            {"timestamp": now - 3570, "lat": 40.11, "lon": -105.11, "altitude_ft": 6100},
            {"timestamp": now - 3540, "lat": 40.12, "lon": -105.12, "altitude_ft": 6200},
        ])
        # Airliner far above the AGL ceiling (elev 5000 + 5000 = 10000 MSL):
        db.archive_track_samples(conn, "BBB222", [
            {"timestamp": now - 3600, "lat": 40.10, "lon": -105.10, "altitude_ft": 35000},
            {"timestamp": now - 3570, "lat": 40.11, "lon": -105.11, "altitude_ft": 35000},
        ])
        conn.commit()

    with TestClient(app) as client:
        resp = client.get(
            "/airports/KTST/track-history?days=7&ceiling_ft=5000&_now=2000000000"
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["airport"]["icao"] == "KTST"
        assert body["days"] == 7
        assert body["ceiling_ft"] == 5000
        icaos = {t["icao24"] for t in body["tracks"]}
        assert icaos == {"aaa111"}          # airliner filtered by ceiling
        assert body["total_tracks"] == 1
        assert body["truncated"] is False

    with TestClient(app) as client:
        assert client.get("/airports/ZZZZ/track-history").status_code == 404
    get_settings.cache_clear()


def test_track_history_rejects_out_of_range_days(tmp_path, monkeypatch):
    monkeypatch.setenv("CIRCLEJERK_DATABASE_PATH", str(tmp_path / "circlejerk.sqlite3"))
    monkeypatch.setenv("CIRCLEJERK_REDIS_URL", "memory://")
    monkeypatch.setenv("CIRCLEJERK_ENVIRONMENT", "test")
    get_settings.cache_clear()
    with TestClient(app) as client:
        # days is bounded [1,7]; FastAPI rejects out-of-range query params with 422.
        assert client.get("/airports/KBJC/track-history?days=0").status_code == 422
        assert client.get("/airports/KBJC/track-history?days=8").status_code == 422
    get_settings.cache_clear()


def test_pattern_circuits_endpoint(tmp_path, monkeypatch):
    monkeypatch.setenv("CIRCLEJERK_DATABASE_PATH", str(tmp_path / "circlejerk.sqlite3"))
    monkeypatch.setenv("CIRCLEJERK_REDIS_URL", "memory://")
    monkeypatch.setenv("CIRCLEJERK_ENVIRONMENT", "test")
    get_settings.cache_clear()
    from app import db
    settings = get_settings()
    db.init_db(settings.database_path)
    now = 2000000000

    def _op(op_id, icao24, ts, op_type, runway):
        return {
            "id": op_id, "icao": "KTST", "icao24": icao24, "callsign": "N1",
            "registration": None, "type": op_type, "timestamp": ts,
            "runway_id": runway, "runway_heading_deg": None,
            "turn_direction": None, "min_altitude_ft_agl": None,
            "emitter_category": None,
        }

    with db.db_session(settings.database_path) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO airports (icao, iata, name, city, country, lat, lon, elevation_ft, is_towered) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("KTST", "TST", "Test Field", "Testville", "US", 40.1, -105.1, 5000, 0),
        )
        db.upsert_operation(conn, _op("op1", "AAA111", now - 3600, "touch_and_go", "29"))
        db.upsert_operation(conn, _op("op2", "BBB222", now - 3000, "circle", None))
        # CCC333: two consecutive runway-29 ops 300s apart -> one closed lap.
        db.upsert_operation(conn, _op("op3", "CCC333", now - 2000, "touch_and_go", "29"))
        db.upsert_operation(conn, _op("op4", "CCC333", now - 1700, "touch_and_go", "29"))
        # archived tracks (lowercased icao24 in the table via archive_track_samples)
        db.archive_track_samples(conn, "AAA111", [
            {"timestamp": now - 3600 + 10 * i, "lat": 40.10 + 0.001 * i, "lon": -105.10 + 0.001 * i}
            for i in range(-4, 5)
        ])
        db.archive_track_samples(conn, "BBB222", [
            {"timestamp": now - 3000 + 10 * i, "lat": 40.12 + 0.001 * i, "lon": -105.12 + 0.001 * i}
            for i in range(-4, 5)
        ])
        # square-cycling track spanning the CCC333 lap window so RDP keeps >=4 pts
        _corners = [(40.10, -105.10), (40.12, -105.10), (40.12, -105.12), (40.10, -105.12)]
        db.archive_track_samples(conn, "CCC333", [
            {"timestamp": now - 2100 + 5 * i, "lat": _corners[i % 4][0], "lon": _corners[i % 4][1]}
            for i in range(0, 100)
        ])
        conn.commit()

    with TestClient(app) as client:
        resp = client.get("/airports/KTST/pattern-circuits?days=7&_now=2000000000")
        assert resp.status_code == 200
        body = resp.json()
        assert body["airport"]["icao"] == "KTST"
        assert all("is_loop" in c for c in body["circuits"])
        # one lap (CCC333, rwy 29) + two context slices (AAA111 lone tg, BBB222 circle)
        assert body["total_circuits"] == 3
        assert body["truncated"] is False
        assert body["counts_by_class"] == {"29": 1}
        assert body["context_count"] == 2
        laps = [c for c in body["circuits"] if c["is_loop"]]
        assert len(laps) == 1 and laps[0]["class"] == "29"

    with TestClient(app) as client:
        assert client.get("/airports/ZZZZ/pattern-circuits").status_code == 404
        assert client.get("/airports/KTST/pattern-circuits?days=0").status_code == 422
        assert client.get("/airports/KTST/pattern-circuits?days=8").status_code == 422
    get_settings.cache_clear()


def test_operations_trends_endpoint(tmp_path, monkeypatch):
    monkeypatch.setenv("CIRCLEJERK_DATABASE_PATH", str(tmp_path / "circlejerk.sqlite3"))
    monkeypatch.setenv("CIRCLEJERK_REDIS_URL", "memory://")
    monkeypatch.setenv("CIRCLEJERK_ENVIRONMENT", "test")
    get_settings.cache_clear()
    from app import db
    settings = get_settings()
    db.init_db(settings.database_path)
    base = 1780000000
    with db.db_session(settings.database_path) as conn:
        # KLMO is already seeded by db.init_db; no airport insert needed.
        for oid, typ in (("l1", "landing"), ("g1", "touch_and_go")):
            db.upsert_operation(conn, db.operation_from_event({
                "id": oid, "type": typ, "icao24": "a1", "callsign": "A1",
                "timestamp": base, "airport_icao": "KLMO", "emitter_category": "A1",
            }))
        conn.commit()

    with TestClient(app) as client:
        resp = client.get(f"/airports/KLMO/operations-trends?_now={base + 100}")
        assert resp.status_code == 200
        body = resp.json()
        assert body["airport_icao"] == "KLMO"
        assert body["timezone"] == "America/Denver"
        assert len(body["time_of_day"]) == 24
        assert any(m["total"] == 2 for m in body["monthly"])

    with TestClient(app) as client:
        assert client.get("/airports/ZZZZ/operations-trends").status_code == 404
    get_settings.cache_clear()


def test_vnap_compliance_endpoint(tmp_path, monkeypatch):
    monkeypatch.setenv("CIRCLEJERK_DATABASE_PATH", str(tmp_path / "circlejerk.sqlite3"))
    monkeypatch.setenv("CIRCLEJERK_REDIS_URL", "memory://")
    monkeypatch.setenv("CIRCLEJERK_ENVIRONMENT", "test")
    get_settings.cache_clear()
    from app import db
    settings = get_settings()
    db.init_db(settings.database_path)
    base = 1780000000
    with db.db_session(settings.database_path) as conn:
        for oid, typ in (("l1", "landing"), ("g1", "touch_and_go"), ("c1", "circle")):
            db.upsert_operation(conn, db.operation_from_event({
                "id": oid, "type": typ, "icao24": "aa11", "callsign": "AA11",
                "timestamp": base + 10, "airport_icao": "KLMO",
            }))
        conn.commit()
    with TestClient(app) as client:
        resp = client.get("/airports/KLMO/vnap-compliance?window=all&_now=%d" % (base + 100))
        assert resp.status_code == 200
        body = resp.json()
        assert body["airport_icao"] == "KLMO"
        assert body["axes"][0] == "tightness"
        ac = next(a for a in body["aircraft"] if a["icao24"] == "aa11")
        # operations = landing + takeoff + touch_and_go (circle excluded), per
        # the already-tested _OPERATION_TYPES semantics in test_vnap_compliance.py.
        assert ac["operations"] == 2
        assert ac["circles"] == 1
        assert client.get("/airports/ZZZZ/vnap-compliance").status_code == 404
    get_settings.cache_clear()


def test_owner_class_override_and_notes(tmp_path, monkeypatch):
    monkeypatch.setenv("CIRCLEJERK_DATABASE_PATH", str(tmp_path / "circlejerk.sqlite3"))
    monkeypatch.setenv("CIRCLEJERK_REDIS_URL", "memory://")
    monkeypatch.setenv("CIRCLEJERK_ENVIRONMENT", "test")
    get_settings.cache_clear()
    from app import db
    settings = get_settings()
    db.init_db(settings.database_path)
    with TestClient(app) as client:
        # Bad bucket -> 422.
        bad = client.put("/aircraft/aa11/owner-class",
                         json={"owner_type": "spaceship", "visitor_id": "visitor-1234"})
        assert bad.status_code == 422
        # Valid override -> resolved community class.
        ok = client.put("/aircraft/aa11/owner-class",
                        json={"owner_type": "flight_school", "visitor_id": "visitor-1234",
                              "change_note": "local school"})
        assert ok.status_code == 200
        assert ok.json()["owner_class"] == "flight_school"
        assert ok.json()["owner_source"] == "community"
        # Note round-trips and GET returns it + the resolved owner.
        client.post("/aircraft/aa11/notes",
                    json={"note": "laps at dawn", "is_flight_school": True,
                          "visitor_id": "visitor-1234"})
        got = client.get("/aircraft/aa11/notes")
        assert got.status_code == 200
        assert got.json()["owner"]["owner_class"] == "flight_school"
        assert got.json()["notes"][0]["note"] == "laps at dawn"


def test_owner_class_locked_returns_409(tmp_path, monkeypatch):
    monkeypatch.setenv("CIRCLEJERK_DATABASE_PATH", str(tmp_path / "circlejerk.sqlite3"))
    monkeypatch.setenv("CIRCLEJERK_REDIS_URL", "memory://")
    monkeypatch.setenv("CIRCLEJERK_ENVIRONMENT", "test")
    get_settings.cache_clear()
    from app import db
    settings = get_settings()
    db.init_db(settings.database_path)
    with db.db_session(settings.database_path) as conn:
        db.set_owner_override(conn, "ff66", "llc", editor_visitor_id="v-12345678")
        conn.execute("UPDATE aircraft_owner_overrides SET locked=1 WHERE icao24='ff66'")
    with TestClient(app) as client:
        resp = client.put("/aircraft/ff66/owner-class",
                          json={"owner_type": "individual", "visitor_id": "visitor-9999"})
        assert resp.status_code == 409
    get_settings.cache_clear()
    get_settings.cache_clear()
