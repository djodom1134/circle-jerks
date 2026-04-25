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

