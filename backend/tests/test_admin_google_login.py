from __future__ import annotations

import base64
import json
from urllib.parse import parse_qs, urlparse

import pytest

from fastapi.testclient import TestClient

from app import db
from app.main import app, db_session
from app.settings import get_settings

CLIENT_ID = "client-123.apps.googleusercontent.com"
REDIRECT = "https://circlejerks.live/api/admin/auth/google/callback"


def configure(tmp_path, monkeypatch, *, google=True, superusers=None):
    monkeypatch.setenv("CIRCLEJERK_DATABASE_PATH", str(tmp_path / "circlejerk.sqlite3"))
    monkeypatch.setenv("CIRCLEJERK_REDIS_URL", "memory://")
    monkeypatch.setenv("CIRCLEJERK_ENVIRONMENT", "test")
    monkeypatch.setenv("CIRCLEJERK_ADMIN_PASSWORD", "pw")
    if google:
        monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", CLIENT_ID)
        monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_SECRET", "secret")
        monkeypatch.setenv("GOOGLE_OAUTH_REDIRECT_URI", REDIRECT)
    else:
        monkeypatch.delenv("GOOGLE_OAUTH_CLIENT_ID", raising=False)
        monkeypatch.delenv("GOOGLE_OAUTH_CLIENT_SECRET", raising=False)
    if superusers:
        monkeypatch.setenv("ADMIN_SUPERUSERS", superusers)
    else:
        monkeypatch.delenv("ADMIN_SUPERUSERS", raising=False)
    get_settings.cache_clear()


def id_token(**overrides) -> str:
    claims = {
        "iss": "https://accounts.google.com",
        "aud": CLIENT_ID,
        "sub": "google-sub-1",
        "email": "p@example.com",
        "email_verified": True,
        "exp": 9999999999,
        "name": "Partner P",
        "picture": "https://x.test/p.png",
    }
    claims.update(overrides)

    def seg(data: dict) -> str:
        return base64.urlsafe_b64encode(json.dumps(data).encode()).decode().rstrip("=")

    return f"{seg({'alg': 'RS256'})}.{seg(claims)}.sig"


def stub_exchange(monkeypatch, token: str | None = None, fail: bool = False):
    """Replace the one network call. Everything else runs for real."""
    async def fake(settings, code, verifier):
        if fail:
            raise RuntimeError("token endpoint said no")
        return {"id_token": token or id_token()}

    monkeypatch.setattr("app.main.exchange_google_code", fake)


def start(client: TestClient) -> str:
    resp = client.get("/admin/auth/google/start", follow_redirects=False)
    assert resp.status_code == 307
    return parse_qs(urlparse(resp.headers["location"]).query)["state"][0]


def test_methods_reports_what_is_configured(tmp_path, monkeypatch):
    configure(tmp_path, monkeypatch, google=True)
    with TestClient(app) as client:
        assert client.get("/admin/auth/methods").json() == {"google": True, "password": True}

    configure(tmp_path, monkeypatch, google=False)
    with TestClient(app) as client:
        assert client.get("/admin/auth/methods").json()["google"] is False


def test_start_is_503_when_google_is_not_configured(tmp_path, monkeypatch):
    configure(tmp_path, monkeypatch, google=False)
    with TestClient(app) as client:
        assert client.get("/admin/auth/google/start", follow_redirects=False).status_code == 503


def test_a_new_user_lands_pending_with_no_grant(tmp_path, monkeypatch):
    configure(tmp_path, monkeypatch)
    stub_exchange(monkeypatch)
    with TestClient(app) as client:
        state = start(client)
        resp = client.get(
            f"/admin/auth/google/callback?code=abc&state={state}", follow_redirects=False
        )
        assert resp.status_code == 307
        assert resp.headers["location"] == "/admin"

        session = client.get("/admin/session")
        assert session.status_code == 403
        assert session.json()["detail"]["status"] == "pending"

        with db_session(get_settings().database_path) as conn:
            row = db.find_admin_user(conn, google_sub="google-sub-1", email=None)
        assert row["role"] == "partner"
        assert row["granted_scopes"] is None


def test_an_allowlisted_email_becomes_super_admin_even_if_the_row_says_otherwise(
    tmp_path, monkeypatch
):
    configure(tmp_path, monkeypatch, superusers="P@Example.com")
    stub_exchange(monkeypatch)
    with TestClient(app) as client:
        state = start(client)
        client.get(f"/admin/auth/google/callback?code=abc&state={state}", follow_redirects=False)
        body = client.get("/admin/session").json()
        assert body["role"] == "super_admin"
        assert body["status"] == "approved"

        # Demote in the database, then prove the next request re-pins it.
        with db_session(get_settings().database_path) as conn:
            row = db.find_admin_user(conn, google_sub="google-sub-1", email=None)
            db.set_admin_user_access(
                conn, row["id"], role="partner", status="suspended",
                granted_scopes=None, granted_airports=None, decided_by=None, now=1,
            )
        assert client.get("/admin/session").json()["role"] == "super_admin"


def test_state_mismatch_is_rejected(tmp_path, monkeypatch):
    configure(tmp_path, monkeypatch)
    stub_exchange(monkeypatch)
    with TestClient(app) as client:
        start(client)
        resp = client.get(
            "/admin/auth/google/callback?code=abc&state=wrong", follow_redirects=False
        )
        assert resp.status_code == 400


def test_callback_without_a_state_cookie_is_rejected(tmp_path, monkeypatch):
    configure(tmp_path, monkeypatch)
    stub_exchange(monkeypatch)
    with TestClient(app) as client:
        state = start(client)
        client.cookies.clear()
        resp = client.get(
            f"/admin/auth/google/callback?code=abc&state={state}", follow_redirects=False
        )
        assert resp.status_code == 400


def test_an_unverified_email_is_refused(tmp_path, monkeypatch):
    configure(tmp_path, monkeypatch)
    stub_exchange(monkeypatch, token=id_token(email_verified=False))
    with TestClient(app) as client:
        state = start(client)
        resp = client.get(
            f"/admin/auth/google/callback?code=abc&state={state}", follow_redirects=False
        )
        assert resp.status_code == 403


def test_a_token_for_another_client_is_refused(tmp_path, monkeypatch):
    configure(tmp_path, monkeypatch)
    stub_exchange(monkeypatch, token=id_token(aud="some-other-client"))
    with TestClient(app) as client:
        state = start(client)
        resp = client.get(
            f"/admin/auth/google/callback?code=abc&state={state}", follow_redirects=False
        )
        assert resp.status_code == 400


def test_replaying_a_consumed_state_is_refused(tmp_path, monkeypatch):
    configure(tmp_path, monkeypatch)
    stub_exchange(monkeypatch)
    with TestClient(app) as client:
        state = start(client)
        first = client.get(
            f"/admin/auth/google/callback?code=abc&state={state}", follow_redirects=False
        )
        assert first.status_code == 307
        # The state cookie is cleared on use, so the same code cannot be
        # replayed against a fresh session.
        replay = client.get(
            f"/admin/auth/google/callback?code=abc&state={state}", follow_redirects=False
        )
        assert replay.status_code == 400


def test_a_failed_token_exchange_is_a_502(tmp_path, monkeypatch):
    configure(tmp_path, monkeypatch)
    stub_exchange(monkeypatch, fail=True)
    with TestClient(app) as client:
        state = start(client)
        resp = client.get(
            f"/admin/auth/google/callback?code=abc&state={state}", follow_redirects=False
        )
        assert resp.status_code == 502
