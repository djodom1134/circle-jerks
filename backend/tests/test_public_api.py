from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from app import api_keys, db
from app.main import app
from app.settings import get_settings


def configure(tmp_path, monkeypatch):
    monkeypatch.setenv("CIRCLEJERK_DATABASE_PATH", str(tmp_path / "circlejerk.sqlite3"))
    monkeypatch.setenv("CIRCLEJERK_REDIS_URL", "memory://")
    monkeypatch.setenv("CIRCLEJERK_ENVIRONMENT", "test")
    get_settings.cache_clear()
    return str(tmp_path / "circlejerk.sqlite3")


def mint(db_path: str, *, scopes: list[str], airports: list[str] | None = None) -> str:
    """Insert a key directly and return the full presentable key string."""
    full, key_id, secret_hash = api_keys.generate_key("test")
    db.init_db(db_path)
    with db.db_session(db_path) as conn:
        db.create_api_key(
            conn,
            key_id=key_id,
            secret_hash=secret_hash,
            name="test-key",
            scopes=api_keys.serialize_scopes(scopes),
            airports=api_keys.serialize_airports(airports),
            created_at=int(time.time()),
            created_by="admin",
        )
    return full


def auth(key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}"}


def test_meta_requires_a_key(tmp_path, monkeypatch):
    configure(tmp_path, monkeypatch)
    with TestClient(app) as client:
        resp = client.get("/v1/meta")
        assert resp.status_code == 401
        assert resp.json()["error"]["code"] == "unauthorized"


def test_meta_echoes_the_keys_scopes_and_airports(tmp_path, monkeypatch):
    db_path = configure(tmp_path, monkeypatch)
    key = mint(db_path, scopes=["ops:read", "aggregates:read"], airports=["KLMO"])
    with TestClient(app) as client:
        resp = client.get("/v1/meta", headers=auth(key))
        assert resp.status_code == 200
        body = resp.json()
        assert body["name"] == "test-key"
        assert sorted(body["scopes"]) == ["aggregates:read", "ops:read"]
        assert body["airports"] == ["KLMO"]
        assert body["version"] == "v1"


def test_unrestricted_key_reports_null_airports(tmp_path, monkeypatch):
    db_path = configure(tmp_path, monkeypatch)
    key = mint(db_path, scopes=["ops:read"], airports=None)
    with TestClient(app) as client:
        assert client.get("/v1/meta", headers=auth(key)).json()["airports"] is None


def test_x_api_key_header_is_accepted(tmp_path, monkeypatch):
    db_path = configure(tmp_path, monkeypatch)
    key = mint(db_path, scopes=["ops:read"])
    with TestClient(app) as client:
        assert client.get("/v1/meta", headers={"X-API-Key": key}).status_code == 200


@pytest.mark.parametrize("presented", ["garbage", "cj_test_0123456789abcdef_wrongsecret"])
def test_unknown_and_wrong_keys_are_indistinguishable(tmp_path, monkeypatch, presented):
    db_path = configure(tmp_path, monkeypatch)
    mint(db_path, scopes=["ops:read"])
    with TestClient(app) as client:
        resp = client.get("/v1/meta", headers=auth(presented))
        assert resp.status_code == 401
        assert resp.json()["error"] == {
            "code": "unauthorized",
            "message": "invalid or missing API key",
        }


def test_revoked_key_is_rejected(tmp_path, monkeypatch):
    db_path = configure(tmp_path, monkeypatch)
    key = mint(db_path, scopes=["ops:read"])
    key_id = api_keys.parse_key(key)[0]
    with db.db_session(db_path) as conn:
        db.revoke_api_key(conn, key_id, int(time.time()))

    with TestClient(app) as client:
        assert client.get("/v1/meta", headers=auth(key)).status_code == 401


def test_successful_call_records_last_used_at(tmp_path, monkeypatch):
    db_path = configure(tmp_path, monkeypatch)
    key = mint(db_path, scopes=["ops:read"])
    key_id = api_keys.parse_key(key)[0]
    with TestClient(app) as client:
        client.get("/v1/meta", headers=auth(key))
    with db.db_session(db_path) as conn:
        assert db.get_api_key(conn, key_id)["last_used_at"] is not None


def test_rate_limit_headers_and_429(tmp_path, monkeypatch):
    db_path = configure(tmp_path, monkeypatch)
    key = mint(db_path, scopes=["ops:read"])
    monkeypatch.setattr("app.public_api.RATE_LIMIT_PER_MINUTE", 2)

    with TestClient(app) as client:
        first = client.get("/v1/meta", headers=auth(key))
        assert first.headers["X-RateLimit-Limit"] == "2"
        assert first.headers["X-RateLimit-Remaining"] == "1"

        client.get("/v1/meta", headers=auth(key))
        third = client.get("/v1/meta", headers=auth(key))
        assert third.status_code == 429
        assert third.json()["error"]["code"] == "rate_limited"
        assert third.headers["Retry-After"] == "60"


def test_docs_and_openapi_cover_only_the_v1_namespace(tmp_path, monkeypatch):
    configure(tmp_path, monkeypatch)
    with TestClient(app) as client:
        schema = client.get("/v1/openapi.json")
        assert schema.status_code == 200
        paths = schema.json()["paths"]
        assert "/v1/meta" in paths
        assert "/admin/dashboard" not in paths
        assert client.get("/v1/docs").status_code == 200


def test_internal_routes_keep_their_detail_error_shape(tmp_path, monkeypatch):
    configure(tmp_path, monkeypatch)
    with TestClient(app) as client:
        resp = client.get("/admin/dashboard")
        assert resp.status_code in (401, 503)
        assert "detail" in resp.json()
