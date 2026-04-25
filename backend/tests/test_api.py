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
