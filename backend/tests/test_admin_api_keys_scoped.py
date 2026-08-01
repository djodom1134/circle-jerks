from __future__ import annotations

from fastapi.testclient import TestClient

from app import db
from app.main import app, db_session
from app.settings import get_settings

PASSWORD = "pw"


def configure(tmp_path, monkeypatch):
    monkeypatch.setenv("CIRCLEJERK_DATABASE_PATH", str(tmp_path / "c.sqlite3"))
    monkeypatch.setenv("CIRCLEJERK_REDIS_URL", "memory://")
    monkeypatch.setenv("CIRCLEJERK_ENVIRONMENT", "test")
    monkeypatch.setenv("CIRCLEJERK_ADMIN_PASSWORD", PASSWORD)
    # Use empty string instead of delenv: pydantic-settings reads from the
    # .env file on disk even when a variable is deleted from os.environ.
    # Explicitly setting to empty string takes precedence over .env.
    monkeypatch.setenv("ADMIN_SUPERUSERS", "")
    get_settings.cache_clear()


def login(client):
    assert client.post("/admin/login", json={"username": "admin", "password": PASSWORD}).status_code == 200


def become_partner(scopes="ops:read", airports="KLMO"):
    """Demote the logged-in break-glass row so the same cookie is now a partner."""
    with db_session(get_settings().database_path) as conn:
        db.set_admin_user_access(
            conn, "local-admin", role="partner", status="approved",
            granted_scopes=scopes, granted_airports=airports, decided_by=None, now=1,
        )


def test_a_partner_may_mint_inside_their_grant(tmp_path, monkeypatch):
    configure(tmp_path, monkeypatch)
    with TestClient(app) as client:
        login(client)
        become_partner()
        resp = client.post("/admin/api-keys", json={
            "name": "mine", "scopes": ["ops:read"], "airports": ["KLMO"],
        })
        assert resp.status_code == 200
        record = resp.json()["record"]
        assert record["owner_user_id"] == "local-admin"
        assert record["owned"] is True


def test_a_partner_cannot_mint_a_scope_they_were_not_granted(tmp_path, monkeypatch):
    configure(tmp_path, monkeypatch)
    with TestClient(app) as client:
        login(client)
        become_partner()
        resp = client.post("/admin/api-keys", json={
            "name": "wider", "scopes": ["ops:read", "ledger:read"], "airports": ["KLMO"],
        })
        assert resp.status_code == 400
        assert "ledger:read" in resp.json()["detail"]


def test_a_partner_cannot_mint_an_all_airports_key(tmp_path, monkeypatch):
    configure(tmp_path, monkeypatch)
    with TestClient(app) as client:
        login(client)
        become_partner()
        resp = client.post("/admin/api-keys", json={
            "name": "everywhere", "scopes": ["ops:read"], "airports": None,
        })
        assert resp.status_code == 400


def test_a_partner_cannot_mint_for_another_airport(tmp_path, monkeypatch):
    configure(tmp_path, monkeypatch)
    with TestClient(app) as client:
        login(client)
        become_partner()
        resp = client.post("/admin/api-keys", json={
            "name": "elsewhere", "scopes": ["ops:read"], "airports": ["KBJC"],
        })
        assert resp.status_code == 400
        assert "KBJC" in resp.json()["detail"]


def test_a_partner_sees_only_their_own_keys(tmp_path, monkeypatch):
    configure(tmp_path, monkeypatch)
    with TestClient(app) as client:
        login(client)
        with db_session(get_settings().database_path) as conn:
            db.create_api_key(
                conn, key_id="bbbbbbbbbbbbbbbb", secret_hash="0" * 64, name="theirs",
                scopes="ops:read", airports="KLMO", created_at=1, created_by="x",
                owner_user_id="someone-else",
            )
            db.create_api_key(
                conn, key_id="cccccccccccccccc", secret_hash="0" * 64, name="legacy",
                scopes="ops:read", airports=None, created_at=1, created_by="x",
                owner_user_id=None,
            )
        become_partner()
        client.post("/admin/api-keys", json={
            "name": "mine", "scopes": ["ops:read"], "airports": ["KLMO"],
        })
        keys = client.get("/admin/api-keys").json()["keys"]
        names = [k["name"] for k in keys]
        assert names == ["mine"]
        assert keys[0]["owned"] is True


def test_a_super_admin_sees_every_key_including_legacy_unowned(tmp_path, monkeypatch):
    configure(tmp_path, monkeypatch)
    with TestClient(app) as client:
        login(client)
        with db_session(get_settings().database_path) as conn:
            db.create_api_key(
                conn, key_id="cccccccccccccccc", secret_hash="0" * 64, name="legacy",
                scopes="ops:read", airports=None, created_at=1, created_by="x",
                owner_user_id=None,
            )
        keys = client.get("/admin/api-keys").json()["keys"]
        legacy = next(k for k in keys if k["name"] == "legacy")
        assert legacy["owner_user_id"] is None
        assert legacy["owned"] is False


def test_a_partner_revoking_someone_elses_key_gets_404_not_403(tmp_path, monkeypatch):
    # 403 would confirm the id exists. 404 makes owned and non-existent keys
    # indistinguishable, so ids are not enumerable.
    configure(tmp_path, monkeypatch)
    with TestClient(app) as client:
        login(client)
        with db_session(get_settings().database_path) as conn:
            db.create_api_key(
                conn, key_id="bbbbbbbbbbbbbbbb", secret_hash="0" * 64, name="theirs",
                scopes="ops:read", airports="KLMO", created_at=1, created_by="x",
                owner_user_id="someone-else",
            )
        become_partner()
        assert client.post("/admin/api-keys/bbbbbbbbbbbbbbbb/revoke").status_code == 404
        assert client.post("/admin/api-keys/aaaaaaaaaaaaaaaa/revoke").status_code == 404
        with db_session(get_settings().database_path) as conn:
            assert db.get_api_key(conn, "bbbbbbbbbbbbbbbb")["revoked_at"] is None


def test_a_partner_can_revoke_their_own_key(tmp_path, monkeypatch):
    configure(tmp_path, monkeypatch)
    with TestClient(app) as client:
        login(client)
        become_partner()
        created = client.post("/admin/api-keys", json={
            "name": "mine", "scopes": ["ops:read"], "airports": ["KLMO"],
        }).json()
        key_id = created["record"]["id"]
        assert client.post(f"/admin/api-keys/{key_id}/revoke").json()["revoked"] is True


def test_a_super_admin_can_revoke_a_legacy_unowned_key(tmp_path, monkeypatch):
    configure(tmp_path, monkeypatch)
    with TestClient(app) as client:
        login(client)
        with db_session(get_settings().database_path) as conn:
            db.create_api_key(
                conn, key_id="cccccccccccccccc", secret_hash="0" * 64, name="legacy",
                scopes="ops:read", airports=None, created_at=1, created_by="x",
                owner_user_id=None,
            )
        assert client.post("/admin/api-keys/cccccccccccccccc/revoke").json()["revoked"] is True


def test_a_restricted_partners_minted_key_is_actually_restricted_at_v1(tmp_path, monkeypatch):
    """The highest-value missing test from the final review.

    Every other test in this file (and in test_api_keys.py) proves one half
    in isolation: that admin_create_api_key enforces the grant, or that
    ApiKeyContext.allows_airport rejects an out-of-grant airport. Nothing
    crossed the boundary — mint a key through the real HTTP endpoint, then
    present that exact key to /v1 and prove the restriction actually holds.

    If a refactor ever dropped `airports=api_keys.serialize_airports(payload.airports)`
    from admin_create_api_key, enforce_grant would still run and still pass
    (the request only ever asks for KLMO), and this partner's key would
    silently become unrestricted across every airport — invisible to every
    other test, since none of them mint a key here and then use it there.
    """
    configure(tmp_path, monkeypatch)
    with TestClient(app) as client:
        login(client)
        become_partner(scopes="ops:read", airports="KLMO")
        created = client.post("/admin/api-keys", json={
            "name": "scoped", "scopes": ["ops:read"], "airports": ["KLMO"],
        })
        assert created.status_code == 200
        full_key = created.json()["key"]

        inside = client.get(
            "/v1/operations",
            params={"airport": "KLMO"},
            headers={"Authorization": f"Bearer {full_key}"},
        )
        assert inside.status_code == 200

        outside = client.get(
            "/v1/operations",
            params={"airport": "KBJC"},
            headers={"Authorization": f"Bearer {full_key}"},
        )
        assert outside.status_code == 403
        assert outside.json()["error"]["code"] == "forbidden_airport"


def test_a_pending_user_cannot_touch_keys_at_all(tmp_path, monkeypatch):
    configure(tmp_path, monkeypatch)
    with TestClient(app) as client:
        login(client)
        with db_session(get_settings().database_path) as conn:
            db.set_admin_user_access(
                conn, "local-admin", role="partner", status="pending",
                granted_scopes="ops:read", granted_airports="KLMO",
                decided_by=None, now=1,
            )
        assert client.get("/admin/api-keys").status_code == 403
        assert client.post("/admin/api-keys", json={
            "name": "x", "scopes": ["ops:read"], "airports": ["KLMO"],
        }).status_code == 403
