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
