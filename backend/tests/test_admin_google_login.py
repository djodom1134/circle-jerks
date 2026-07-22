from __future__ import annotations

import base64
import hashlib
import hmac
import json
from urllib.parse import parse_qs, urlparse

from fastapi.testclient import TestClient

from app import db, google_oauth
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


def sign_state_cookie(state: str, verifier: str, exp: int) -> str:
    """Mint a state cookie value by hand, mirroring admin_google_start.

    Lets tests hand the callback a payload with an arbitrary embedded `exp`
    without going through /admin/auth/google/start and waiting on the clock.
    """
    settings = get_settings()
    body = base64.urlsafe_b64encode(
        json.dumps(
            {"state": state, "verifier": verifier, "exp": exp}, separators=(",", ":")
        ).encode("utf-8")
    ).decode("ascii").rstrip("=")
    signature = hmac.new(
        settings.app_secret.encode("utf-8"), body.encode("ascii"), hashlib.sha256
    ).hexdigest()
    return f"{body}.{signature}"


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
        # This one message is intentionally specific per the design doc's
        # error table; it is not the leaked-internal-reason the 400 path
        # guards against below.
        assert resp.json() == {"detail": "a verified Google account is required"}


def test_a_token_for_another_client_is_refused(tmp_path, monkeypatch):
    configure(tmp_path, monkeypatch)
    stub_exchange(monkeypatch, token=id_token(aud="some-other-client"))
    with TestClient(app) as client:
        state = start(client)
        resp = client.get(
            f"/admin/auth/google/callback?code=abc&state={state}", follow_redirects=False
        )
        assert resp.status_code == 400
        # The regression this guards against: an unauthenticated caller
        # must not learn *which* claim check failed.
        assert resp.json() == {"detail": "sign-in could not be completed"}
        assert "issued for this client" not in resp.text
        assert "aud" not in resp.text.lower()


def test_an_expired_google_token_is_refused_generically(tmp_path, monkeypatch):
    configure(tmp_path, monkeypatch)
    stub_exchange(monkeypatch, token=id_token(exp=1))
    with TestClient(app) as client:
        state = start(client)
        resp = client.get(
            f"/admin/auth/google/callback?code=abc&state={state}", follow_redirects=False
        )
        assert resp.status_code == 400
        assert resp.json() == {"detail": "sign-in could not be completed"}
        assert "expired" not in resp.text


def test_a_non_ascii_state_query_param_is_rejected_not_a_500(tmp_path, monkeypatch):
    configure(tmp_path, monkeypatch)
    stub_exchange(monkeypatch)
    with TestClient(app) as client:
        # A real, valid state cookie is on the client, but the state query
        # param is non-ASCII, which secrets.compare_digest cannot even
        # compare. Must fail closed with 400, not crash with 500.
        start(client)
        resp = client.get(
            "/admin/auth/google/callback?code=abc&state=%C3%A9", follow_redirects=False
        )
        assert resp.status_code == 400


def test_an_expired_state_cookie_is_rejected(tmp_path, monkeypatch):
    configure(tmp_path, monkeypatch)
    stub_exchange(monkeypatch)
    with TestClient(app) as client:
        cookie_value = sign_state_cookie(
            state="state-1", verifier="verifier-1", exp=1
        )
        resp = client.get(
            "/admin/auth/google/callback?code=abc&state=state-1",
            headers={"cookie": f"{google_oauth.OAUTH_STATE_COOKIE}={cookie_value}"},
            follow_redirects=False,
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


def test_state_cookie_is_cleared_on_failure_paths_too(tmp_path, monkeypatch):
    """Not just the success path: a rejected callback must also delete the
    signed {state, verifier} cookie, or it sits live for the rest of its TTL
    even though the comment on the success path claims it is "consumed".

    HTTPException cannot carry a Set-Cookie header, so this exercises that
    the callback's 400 path actually clears the cookie (via a Response
    rather than a raise) while leaving the status and body untouched.
    """
    configure(tmp_path, monkeypatch)
    stub_exchange(monkeypatch)
    with TestClient(app) as client:
        start(client)
        assert google_oauth.OAUTH_STATE_COOKIE in client.cookies

        resp = client.get(
            "/admin/auth/google/callback?code=abc&state=wrong", follow_redirects=False
        )
        assert resp.status_code == 400
        assert resp.json() == {"detail": "sign-in could not be completed"}
        assert google_oauth.OAUTH_STATE_COOKIE not in client.cookies
