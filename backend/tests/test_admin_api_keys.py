from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app
from app.settings import get_settings

PASSWORD = "test-admin-password"


def configure_admin(tmp_path, monkeypatch):
    monkeypatch.setenv("CIRCLEJERK_DATABASE_PATH", str(tmp_path / "circlejerk.sqlite3"))
    monkeypatch.setenv("CIRCLEJERK_REDIS_URL", "memory://")
    monkeypatch.setenv("CIRCLEJERK_ENVIRONMENT", "test")
    monkeypatch.setenv("CIRCLEJERK_ADMIN_PASSWORD", PASSWORD)
    get_settings.cache_clear()


def login(client: TestClient) -> None:
    resp = client.post("/admin/login", json={"username": "admin", "password": PASSWORD})
    assert resp.status_code == 200


def test_admin_key_routes_reject_an_unauthenticated_caller(tmp_path, monkeypatch):
    configure_admin(tmp_path, monkeypatch)
    with TestClient(app) as client:
        assert client.get("/admin/api-keys").status_code == 401
        assert client.post("/admin/api-keys", json={
            "name": "x", "scopes": ["ops:read"], "airports": None
        }).status_code == 401
        assert client.post("/admin/api-keys/deadbeefdeadbeef/revoke").status_code == 401


def test_create_returns_the_secret_exactly_once(tmp_path, monkeypatch):
    configure_admin(tmp_path, monkeypatch)
    with TestClient(app) as client:
        login(client)
        created = client.post("/admin/api-keys", json={
            "name": "partner", "scopes": ["ops:read", "aggregates:read"], "airports": ["klmo"]
        })
        assert created.status_code == 200
        body = created.json()
        full_key = body["key"]
        assert full_key.startswith("cj_test_")
        assert body["record"]["name"] == "partner"
        assert body["record"]["airports"] == ["KLMO"]
        assert sorted(body["record"]["scopes"]) == ["aggregates:read", "ops:read"]

        listed = client.get("/admin/api-keys").json()["keys"]
        assert len(listed) == 1
        # The secret must never reappear in any later response.
        assert full_key not in client.get("/admin/api-keys").text
        assert listed[0]["prefix"] == f"cj_test_{body['record']['id']}"
        assert "secret_hash" not in listed[0]


def test_created_key_authenticates_against_v1(tmp_path, monkeypatch):
    configure_admin(tmp_path, monkeypatch)
    with TestClient(app) as client:
        login(client)
        full_key = client.post("/admin/api-keys", json={
            "name": "partner", "scopes": ["ops:read"], "airports": None
        }).json()["key"]

        meta = client.get("/v1/meta", headers={"Authorization": f"Bearer {full_key}"})
        assert meta.status_code == 200
        assert meta.json()["scopes"] == ["ops:read"]


def test_revoke_stops_the_key_working(tmp_path, monkeypatch):
    configure_admin(tmp_path, monkeypatch)
    with TestClient(app) as client:
        login(client)
        created = client.post("/admin/api-keys", json={
            "name": "partner", "scopes": ["ops:read"], "airports": None
        }).json()
        full_key, key_id = created["key"], created["record"]["id"]

        revoked = client.post(f"/admin/api-keys/{key_id}/revoke")
        assert revoked.status_code == 200
        assert revoked.json() == {"ok": True, "revoked": True}

        assert client.get(
            "/v1/meta", headers={"Authorization": f"Bearer {full_key}"}
        ).status_code == 401

        # Idempotent: a second revoke succeeds but reports no change.
        assert client.post(f"/admin/api-keys/{key_id}/revoke").json()["revoked"] is False


def test_create_rejects_an_unknown_scope(tmp_path, monkeypatch):
    configure_admin(tmp_path, monkeypatch)
    with TestClient(app) as client:
        login(client)
        resp = client.post("/admin/api-keys", json={
            "name": "partner", "scopes": ["ops:write"], "airports": None
        })
        assert resp.status_code == 400
        assert "ops:write" in resp.json()["detail"]
