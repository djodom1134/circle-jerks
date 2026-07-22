from __future__ import annotations

from fastapi.testclient import TestClient

from app import db
from app.main import app, db_session
from app.settings import get_settings

PASSWORD = "pw"


def configure(tmp_path, monkeypatch, superusers=None):
    monkeypatch.setenv("CIRCLEJERK_DATABASE_PATH", str(tmp_path / "c.sqlite3"))
    monkeypatch.setenv("CIRCLEJERK_REDIS_URL", "memory://")
    monkeypatch.setenv("CIRCLEJERK_ENVIRONMENT", "test")
    monkeypatch.setenv("CIRCLEJERK_ADMIN_PASSWORD", PASSWORD)
    if superusers:
        monkeypatch.setenv("ADMIN_SUPERUSERS", superusers)
    else:
        monkeypatch.delenv("ADMIN_SUPERUSERS", raising=False)
    get_settings.cache_clear()


def login(client):
    assert client.post("/admin/login", json={"username": "admin", "password": PASSWORD}).status_code == 200


def seed_partner(user_id="u2", email="p@example.com", status="pending"):
    with db_session(get_settings().database_path) as conn:
        db.upsert_admin_user(
            conn, id=user_id, email=email, google_sub=f"sub-{user_id}",
            name="P", picture=None, role="partner", status=status, now=100,
        )


def test_only_a_super_admin_reaches_the_user_list(tmp_path, monkeypatch):
    configure(tmp_path, monkeypatch)
    with TestClient(app) as client:
        assert client.get("/admin/users").status_code == 401
        login(client)
        assert client.get("/admin/users").status_code == 200


def test_pending_users_are_listed(tmp_path, monkeypatch):
    configure(tmp_path, monkeypatch)
    with TestClient(app) as client:
        login(client)
        seed_partner()
        body = client.get("/admin/users?status=pending").json()
        assert [u["email"] for u in body["users"]] == ["p@example.com"]
        assert body["users"][0]["status"] == "pending"


def test_approving_records_role_and_grant(tmp_path, monkeypatch):
    configure(tmp_path, monkeypatch)
    with TestClient(app) as client:
        login(client)
        seed_partner()
        resp = client.post("/admin/users/u2/approve", json={
            "role": "partner", "scopes": ["ops:read"], "airports": ["klmo"],
        })
        assert resp.status_code == 200
        user = resp.json()["user"]
        assert user["status"] == "approved"
        assert user["scopes"] == ["ops:read"]
        assert user["airports"] == ["KLMO"]


def test_approving_as_admin_stores_an_unrestricted_grant(tmp_path, monkeypatch):
    # Storing a narrower grant on a role the server treats as unrestricted
    # would be a value nothing ever enforces.
    configure(tmp_path, monkeypatch)
    with TestClient(app) as client:
        login(client)
        seed_partner()
        resp = client.post("/admin/users/u2/approve", json={
            "role": "admin", "scopes": ["ops:read"], "airports": ["KLMO"],
        })
        assert resp.json()["user"]["airports"] is None
        assert set(resp.json()["user"]["scopes"]) == {
            "ops:read", "tracks:read", "aggregates:read", "ledger:read"
        }


def test_rejecting_is_terminal(tmp_path, monkeypatch):
    configure(tmp_path, monkeypatch)
    with TestClient(app) as client:
        login(client)
        seed_partner()
        assert client.post("/admin/users/u2/reject").status_code == 200
        assert client.get("/admin/users?status=pending").json()["users"] == []
        assert client.get("/admin/users?status=rejected").json()["users"][0]["id"] == "u2"


def make_key(owner, key_id, scopes="ops:read", airports="KLMO"):
    with db_session(get_settings().database_path) as conn:
        db.create_api_key(
            conn, key_id=key_id, secret_hash="0" * 64, name=key_id, scopes=scopes,
            airports=airports, created_at=1, created_by="x", owner_user_id=owner,
        )


def test_suspending_revokes_that_users_keys(tmp_path, monkeypatch):
    configure(tmp_path, monkeypatch)
    with TestClient(app) as client:
        login(client)
        seed_partner(status="approved")
        seed_partner(user_id="u3", email="other@example.com", status="approved")
        make_key("u2", "aaaaaaaaaaaaaaaa")
        make_key("u3", "bbbbbbbbbbbbbbbb")

        resp = client.post("/admin/users/u2/suspend")
        assert resp.status_code == 200
        assert resp.json()["revoked_keys"] == 1
        with db_session(get_settings().database_path) as conn:
            assert db.get_api_key(conn, "aaaaaaaaaaaaaaaa")["revoked_at"] is not None
            assert db.get_api_key(conn, "bbbbbbbbbbbbbbbb")["revoked_at"] is None


def test_narrowing_a_grant_revokes_the_keys_that_exceed_it(tmp_path, monkeypatch):
    configure(tmp_path, monkeypatch)
    with TestClient(app) as client:
        login(client)
        seed_partner(status="approved")
        make_key("u2", "aaaaaaaaaaaaaaaa", scopes="ops:read", airports="KLMO")
        make_key("u2", "bbbbbbbbbbbbbbbb", scopes="ledger:read", airports="KLMO")

        resp = client.patch("/admin/users/u2", json={
            "role": "partner", "scopes": ["ops:read"], "airports": ["KLMO"],
        })
        assert resp.json()["revoked_keys"] == 1
        with db_session(get_settings().database_path) as conn:
            assert db.get_api_key(conn, "aaaaaaaaaaaaaaaa")["revoked_at"] is None
            assert db.get_api_key(conn, "bbbbbbbbbbbbbbbb")["revoked_at"] is not None


def test_narrowing_a_grant_does_not_touch_another_owners_key(tmp_path, monkeypatch):
    # revoke_keys_outside_grant has no cross-owner isolation test elsewhere:
    # every other test of it uses a single owner. Give a second user a key
    # that would ALSO fall outside the first user's narrowed grant, narrow
    # only the first user, and assert the second user's key survives.
    configure(tmp_path, monkeypatch)
    with TestClient(app) as client:
        login(client)
        seed_partner(status="approved")
        seed_partner(user_id="u3", email="other@example.com", status="approved")
        make_key("u2", "aaaaaaaaaaaaaaaa", scopes="ledger:read", airports="KLMO")
        make_key("u3", "bbbbbbbbbbbbbbbb", scopes="ledger:read", airports="KLMO")

        resp = client.patch("/admin/users/u2", json={
            "role": "partner", "scopes": ["ops:read"], "airports": ["KLMO"],
        })
        assert resp.json()["revoked_keys"] == 1
        with db_session(get_settings().database_path) as conn:
            assert db.get_api_key(conn, "aaaaaaaaaaaaaaaa")["revoked_at"] is not None
            assert db.get_api_key(conn, "bbbbbbbbbbbbbbbb")["revoked_at"] is None


def test_widening_a_grant_revokes_nothing(tmp_path, monkeypatch):
    configure(tmp_path, monkeypatch)
    with TestClient(app) as client:
        login(client)
        seed_partner(status="approved")
        make_key("u2", "aaaaaaaaaaaaaaaa", scopes="ops:read", airports="KLMO")
        resp = client.patch("/admin/users/u2", json={
            "role": "partner", "scopes": ["ops:read", "ledger:read"], "airports": None,
        })
        assert resp.json()["revoked_keys"] == 0


def test_you_cannot_change_your_own_access(tmp_path, monkeypatch):
    configure(tmp_path, monkeypatch)
    with TestClient(app) as client:
        login(client)
        resp = client.post("/admin/users/local-admin/suspend")
        assert resp.status_code == 400
        assert "your own" in resp.json()["detail"]


def test_an_allowlisted_account_cannot_be_changed(tmp_path, monkeypatch):
    configure(tmp_path, monkeypatch, superusers="pinned@example.com")
    with TestClient(app) as client:
        login(client)
        seed_partner(user_id="u9", email="pinned@example.com", status="approved")
        resp = client.post("/admin/users/u9/suspend")
        assert resp.status_code == 400
        assert "ADMIN_SUPERUSERS" in resp.json()["detail"]


def test_one_super_admin_may_demote_another(tmp_path, monkeypatch):
    # There is no "last super-admin" guard, because it could never fire: the
    # actor is an approved super-admin who cannot target themselves, so one
    # always survives. This test pins that demotion is permitted, so nobody
    # reintroduces the dead guard and breaks it.
    configure(tmp_path, monkeypatch)
    with TestClient(app) as client:
        login(client)
        seed_partner(user_id="u5", email="s@example.com", status="approved")
        client.post("/admin/users/u5/approve", json={
            "role": "super_admin", "scopes": [], "airports": None,
        })
        resp = client.patch("/admin/users/u5", json={
            "role": "partner", "scopes": ["ops:read"], "airports": ["KLMO"],
        })
        assert resp.status_code == 200
        assert resp.json()["user"]["role"] == "partner"
        # local-admin, the actor, is untouched and still in control.
        assert client.get("/admin/session").json()["role"] == "super_admin"


def test_unknown_user_is_404(tmp_path, monkeypatch):
    configure(tmp_path, monkeypatch)
    with TestClient(app) as client:
        login(client)
        assert client.post("/admin/users/nope/suspend").status_code == 404
