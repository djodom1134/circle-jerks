from __future__ import annotations

import re

from fastapi.testclient import TestClient

from app.main import app
from app.settings import get_settings

PASSWORD = "test-admin-password"
# What db.get_api_key actually returns and what must never be serialized out:
# a 64-char hex sha256 digest. The plaintext secret is base64url and is never
# stored, so searching responses for IT proves nothing.
_SECRET_HASH_RE = re.compile(r"\b[0-9a-f]{64}\b")


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
        assert body["record"]["created_by"] == "local-admin@circlejerks.live"

        # The load-bearing one. Create is the ONLY route holding a row from
        # db.get_api_key, which does return secret_hash; _api_key_record's
        # field whitelist is the sole thing keeping it out of the response.
        # A regression to {**row, ...} would leak the hash here and nowhere
        # else, so this is the assertion that has to exist.
        assert "secret_hash" not in body["record"]
        assert _SECRET_HASH_RE.search(created.text) is None, "a sha256 digest reached the create response"

        listed = client.get("/admin/api-keys").json()["keys"]
        assert len(listed) == 1
        assert listed[0]["prefix"] == f"cj_test_{body['record']['id']}"
        assert "secret_hash" not in listed[0]
        # Not `full_key not in ...`: the plaintext secret is never persisted
        # (only its sha256 is), so that assertion could not fail under any
        # implementation. Check for the stored digest instead.
        assert _SECRET_HASH_RE.search(client.get("/admin/api-keys").text) is None


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


def test_create_rejects_a_whitespace_only_name(tmp_path, monkeypatch):
    """min_length=1 alone lets "   " through, which then strips to "".

    A key with an empty name is indistinguishable from every other one in the
    admin list, so the strip has to happen before the length check.
    """
    configure_admin(tmp_path, monkeypatch)
    with TestClient(app) as client:
        login(client)
        resp = client.post("/admin/api-keys", json={
            "name": "   ", "scopes": ["ops:read"], "airports": None
        })
        assert resp.status_code == 422
        assert client.get("/admin/api-keys").json()["keys"] == []


def test_create_strips_surrounding_whitespace_from_the_name(tmp_path, monkeypatch):
    configure_admin(tmp_path, monkeypatch)
    with TestClient(app) as client:
        login(client)
        record = client.post("/admin/api-keys", json={
            "name": "  partner  ", "scopes": ["ops:read"], "airports": None
        }).json()["record"]
        assert record["name"] == "partner"
