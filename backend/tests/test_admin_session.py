from __future__ import annotations

from fastapi.testclient import TestClient

from app import db
from app.main import app, db_session
from app.settings import get_settings

PASSWORD = "test-admin-password"


def configure(tmp_path, monkeypatch, **extra):
    monkeypatch.setenv("CIRCLEJERK_DATABASE_PATH", str(tmp_path / "circlejerk.sqlite3"))
    monkeypatch.setenv("CIRCLEJERK_REDIS_URL", "memory://")
    monkeypatch.setenv("CIRCLEJERK_ENVIRONMENT", "test")
    monkeypatch.setenv("CIRCLEJERK_ADMIN_PASSWORD", PASSWORD)
    for key, value in extra.items():
        monkeypatch.setenv(key, value)
    get_settings.cache_clear()


def login(client: TestClient):
    resp = client.post("/admin/login", json={"username": "admin", "password": PASSWORD})
    assert resp.status_code == 200
    return resp


def test_password_login_creates_a_real_super_admin_row(tmp_path, monkeypatch):
    configure(tmp_path, monkeypatch)
    with TestClient(app) as client:
        login(client)
        session = client.get("/admin/session").json()
        assert session["role"] == "super_admin"
        assert session["status"] == "approved"
        assert session["id"] == "local-admin"


def test_session_cookie_is_lax_not_strict(tmp_path, monkeypatch):
    # Strict is unreliable across the Google -> callback -> /admin redirect
    # chain. Lax still refuses cross-site POST, and every state-changing admin
    # route is POST or PATCH.
    configure(tmp_path, monkeypatch)
    with TestClient(app) as client:
        resp = login(client)
        cookie = resp.headers["set-cookie"].lower()
        assert "samesite=lax" in cookie
        assert "httponly" in cookie


def test_a_suspended_user_loses_access_immediately_without_a_new_cookie(tmp_path, monkeypatch):
    configure(tmp_path, monkeypatch)
    with TestClient(app) as client:
        login(client)
        assert client.get("/admin/session").status_code == 200

        settings = get_settings()
        with db_session(settings.database_path) as conn:
            db.set_admin_user_access(
                conn,
                "local-admin",
                role="super_admin",
                status="suspended",
                granted_scopes=None,
                granted_airports=None,
                decided_by=None,
                now=1,
            )

        # Same cookie, still cryptographically valid, now refused: the reason
        # the dependency reads the row on every request.
        resp = client.get("/admin/session")
        assert resp.status_code == 403
        assert resp.json()["detail"]["status"] == "suspended"


def test_a_pending_user_is_told_they_are_pending(tmp_path, monkeypatch):
    configure(tmp_path, monkeypatch)
    with TestClient(app) as client:
        login(client)
        settings = get_settings()
        with db_session(settings.database_path) as conn:
            db.set_admin_user_access(
                conn, "local-admin", role="partner", status="pending",
                granted_scopes=None, granted_airports=None, decided_by=None, now=1,
            )
        resp = client.get("/admin/session")
        assert resp.status_code == 403
        assert resp.json()["detail"]["status"] == "pending"


def test_a_partner_is_refused_by_require_admin_routes(tmp_path, monkeypatch):
    configure(tmp_path, monkeypatch)
    with TestClient(app) as client:
        login(client)
        settings = get_settings()
        with db_session(settings.database_path) as conn:
            db.set_admin_user_access(
                conn, "local-admin", role="partner", status="approved",
                granted_scopes="ops:read", granted_airports="KLMO",
                decided_by=None, now=1,
            )
        assert client.get("/admin/dashboard").status_code == 403
        assert client.get("/admin/session").status_code == 200


def test_a_forged_cookie_is_rejected(tmp_path, monkeypatch):
    configure(tmp_path, monkeypatch)
    with TestClient(app) as client:
        client.cookies.set("circlejerk_admin", "bm90aGluZw.deadbeef")
        assert client.get("/admin/session").status_code == 401


def test_a_non_ascii_session_cookie_is_rejected_not_a_500(tmp_path, monkeypatch):
    # A hand-crafted or corrupted cookie can carry bytes outside ASCII.
    # decode_admin_token's str.encode("ascii") / compare_digest must fail
    # closed (401) rather than escape as an uncaught 500.
    configure(tmp_path, monkeypatch)
    with TestClient(app) as client:
        raw_cookie = "circlejerk_admin=caf\xe9.deadbeef".encode("latin-1")
        resp = client.get("/admin/session", headers=[(b"cookie", raw_cookie)])
        assert resp.status_code == 401


def test_a_cookie_naming_a_deleted_user_is_rejected(tmp_path, monkeypatch):
    configure(tmp_path, monkeypatch)
    with TestClient(app) as client:
        login(client)
        settings = get_settings()
        with db_session(settings.database_path) as conn:
            conn.execute("DELETE FROM admin_users WHERE id = 'local-admin'")
        assert client.get("/admin/session").status_code == 401
