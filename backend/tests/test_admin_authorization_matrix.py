from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app import db
from app.main import ADMIN_COOKIE_NAME, app, db_session, sign_admin_token
from app.registry import importer as registry_importer
from app.settings import get_settings

PASSWORD = "pw"

# (method, path, json body or None)
SUPER_ADMIN_ONLY = [
    ("GET", "/admin/users", None),
    ("POST", "/admin/users/target-user/approve", {"role": "partner", "scopes": ["ops:read"], "airports": ["KLMO"]}),
    ("POST", "/admin/users/target-user/reject", None),
    ("POST", "/admin/users/target-user/suspend", None),
    ("PATCH", "/admin/users/target-user", {"role": "partner", "scopes": ["ops:read"], "airports": ["KLMO"]}),
]

# require_admin routes that return a clean 200 on an empty database.
ADMIN_ROUTES = [
    ("GET", "/admin/dashboard", None),
    ("GET", "/admin/live_sources", None),
    ("POST", "/admin/aircraft-registry/import", None),
    ("GET", "/admin/aircraft-registry/import/status", None),
]

# require_user routes that return a clean 200 on an empty database.
APPROVED_USER_ROUTES = [
    ("GET", "/admin/session", None),
    ("GET", "/admin/api-keys", None),
    ("POST", "/admin/api-keys", {"name": "matrix-test-key", "scopes": ["ops:read"], "airports": ["KLMO"]}),
]

# require_user routes that 404 (or otherwise can't reach 200) against a
# database with no seeded data for them. Authorization still applies before
# the business logic runs, so these are tested everywhere else in the matrix
# — just not against the ==200 assertion.
APPROVED_USER_ROUTES_NO_HAPPY_PATH = [
    ("POST", "/admin/api-keys/aaaaaaaaaaaaaaaa/revoke", None),
]

PUBLIC_ROUTES = [
    ("GET", "/admin/auth/methods", None),
]


async def _fake_import_faa_registry(database_path, *args, **kwargs):
    """Stand-in for the real FAA registry importer.

    POST /admin/aircraft-registry/import fires this off with
    asyncio.create_task and does not await it, so the real implementation
    would otherwise make a genuine network call to registry.faa.gov from
    every test in this file that reaches an authorized-admin code path. This
    keeps the matrix a pure authorization test, not an integration test of
    the importer.
    """
    return SimpleNamespace(
        import_id=1,
        status="completed",
        rows_imported=0,
        rows_skipped=0,
        rows_invalid=0,
        duration_seconds=0.0,
        source_url="stub://test",
        source_date=None,
        error=None,
    )


def configure(tmp_path, monkeypatch):
    monkeypatch.setenv("CIRCLEJERK_DATABASE_PATH", str(tmp_path / "c.sqlite3"))
    monkeypatch.setenv("CIRCLEJERK_REDIS_URL", "memory://")
    monkeypatch.setenv("CIRCLEJERK_ENVIRONMENT", "test")
    monkeypatch.setenv("CIRCLEJERK_ADMIN_PASSWORD", PASSWORD)
    monkeypatch.delenv("ADMIN_SUPERUSERS", raising=False)
    monkeypatch.setattr(registry_importer, "import_faa_registry", _fake_import_faa_registry)
    get_settings.cache_clear()


def call(client, method, path, body):
    return client.request(method, path, json=body)


def sign_in_as(client, role: str, status: str):
    """Log in break-glass, then rewrite that row to the role under test.

    The cookie names a row, so changing the row changes who the cookie is —
    which is the property this whole matrix is checking.
    """
    assert client.post("/admin/login", json={"username": "admin", "password": PASSWORD}).status_code == 200
    with db_session(get_settings().database_path) as conn:
        db.upsert_admin_user(
            conn, id="target-user", email="t@example.com", google_sub="sub-t",
            name="T", picture=None, role="partner", status="pending", now=1,
        )
        db.set_admin_user_access(
            conn, "local-admin", role=role, status=status,
            granted_scopes="ops:read" if role == "partner" else None,
            granted_airports="KLMO" if role == "partner" else None,
            decided_by=None, now=1,
        )


@pytest.mark.parametrize(
    "method,path,body",
    SUPER_ADMIN_ONLY + ADMIN_ROUTES + APPROVED_USER_ROUTES + APPROVED_USER_ROUTES_NO_HAPPY_PATH,
)
def test_anonymous_is_401_everywhere(tmp_path, monkeypatch, method, path, body):
    configure(tmp_path, monkeypatch)
    with TestClient(app) as client:
        assert call(client, method, path, body).status_code == 401


@pytest.mark.parametrize("method,path,body", PUBLIC_ROUTES)
def test_public_routes_need_no_session(tmp_path, monkeypatch, method, path, body):
    configure(tmp_path, monkeypatch)
    with TestClient(app) as client:
        assert call(client, method, path, body).status_code == 200


@pytest.mark.parametrize("status", ["pending", "rejected", "suspended"])
@pytest.mark.parametrize(
    "method,path,body",
    SUPER_ADMIN_ONLY + ADMIN_ROUTES + APPROVED_USER_ROUTES + APPROVED_USER_ROUTES_NO_HAPPY_PATH,
)
def test_non_approved_users_are_403_everywhere(tmp_path, monkeypatch, status, method, path, body):
    configure(tmp_path, monkeypatch)
    with TestClient(app) as client:
        sign_in_as(client, "super_admin", status)
        resp = call(client, method, path, body)
        assert resp.status_code == 403, f"{method} {path} allowed a {status} user"
        assert resp.json()["detail"]["status"] == status


@pytest.mark.parametrize("method,path,body", SUPER_ADMIN_ONLY + ADMIN_ROUTES)
def test_an_approved_partner_is_403_on_admin_routes(tmp_path, monkeypatch, method, path, body):
    configure(tmp_path, monkeypatch)
    with TestClient(app) as client:
        sign_in_as(client, "partner", "approved")
        assert call(client, method, path, body).status_code == 403, f"{method} {path} leaked to a partner"


@pytest.mark.parametrize("method,path,body", APPROVED_USER_ROUTES)
def test_an_approved_partner_reaches_their_own_routes(tmp_path, monkeypatch, method, path, body):
    configure(tmp_path, monkeypatch)
    with TestClient(app) as client:
        sign_in_as(client, "partner", "approved")
        assert call(client, method, path, body).status_code == 200


@pytest.mark.parametrize("method,path,body", APPROVED_USER_ROUTES_NO_HAPPY_PATH)
def test_an_approved_partner_reaches_their_own_routes_no_happy_path(tmp_path, monkeypatch, method, path, body):
    configure(tmp_path, monkeypatch)
    with TestClient(app) as client:
        sign_in_as(client, "partner", "approved")
        resp = call(client, method, path, body)
        assert resp.status_code not in (401, 403), f"{method} {path} blocked an authorized partner"


@pytest.mark.parametrize("method,path,body", SUPER_ADMIN_ONLY)
def test_a_plain_admin_is_403_on_super_admin_routes(tmp_path, monkeypatch, method, path, body):
    configure(tmp_path, monkeypatch)
    with TestClient(app) as client:
        sign_in_as(client, "admin", "approved")
        assert call(client, method, path, body).status_code == 403, f"{method} {path} leaked to an admin"


@pytest.mark.parametrize("method,path,body", ADMIN_ROUTES)
def test_a_plain_admin_reaches_admin_routes(tmp_path, monkeypatch, method, path, body):
    # require_admin accepts role in {admin, super_admin}. Nothing above tests
    # the plain "admin" half of that set actually reaching its own routes —
    # test_a_super_admin_reaches_everything only proves the super_admin half.
    configure(tmp_path, monkeypatch)
    with TestClient(app) as client:
        sign_in_as(client, "admin", "approved")
        assert call(client, method, path, body).status_code == 200, f"{method} {path} refused a plain admin"


@pytest.mark.parametrize(
    "method,path,body", SUPER_ADMIN_ONLY + ADMIN_ROUTES + APPROVED_USER_ROUTES
)
def test_a_super_admin_reaches_everything(tmp_path, monkeypatch, method, path, body):
    configure(tmp_path, monkeypatch)
    with TestClient(app) as client:
        sign_in_as(client, "super_admin", "approved")
        assert call(client, method, path, body).status_code == 200, f"{method} {path} refused a super-admin"


@pytest.mark.parametrize("method,path,body", APPROVED_USER_ROUTES_NO_HAPPY_PATH)
def test_a_super_admin_reaches_everything_no_happy_path(tmp_path, monkeypatch, method, path, body):
    configure(tmp_path, monkeypatch)
    with TestClient(app) as client:
        sign_in_as(client, "super_admin", "approved")
        resp = call(client, method, path, body)
        assert resp.status_code not in (401, 403), f"{method} {path} refused a super-admin"


def test_every_admin_route_is_covered_by_this_matrix(tmp_path, monkeypatch):
    """Fail when a new /admin route is added without a row in the table above.

    Without this, the matrix silently stops being a matrix the first time
    someone adds a route.
    """
    covered = {
        (method, path)
        for method, path, _ in (
            SUPER_ADMIN_ONLY
            + ADMIN_ROUTES
            + APPROVED_USER_ROUTES
            + APPROVED_USER_ROUTES_NO_HAPPY_PATH
            + PUBLIC_ROUTES
        )
    }
    # Routes exercised elsewhere, or with no meaningful role dimension.
    exempt = {
        # No role dimension: this *is* the unauthenticated entry point.
        # Covered by tests/test_api.py::test_admin_dashboard_requires_login_and_tracks_submissions
        # (wrong password -> 401, correct password -> 200) and by every
        # other admin test file's login() helper.
        ("POST", "/admin/login"),
        # No role dimension: takes no auth dependency at all, works logged
        # out or in. Covered by this file:
        # test_logout_needs_no_role_and_clears_the_cookie
        ("POST", "/admin/logout"),
        # No role dimension: unauthenticated by definition (it's the start of
        # sign-in). Covered by tests/test_admin_google_login.py.
        ("GET", "/admin/auth/google/start"),
        # Same — the callback leg of the same unauthenticated flow. Covered
        # by tests/test_admin_google_login.py.
        ("GET", "/admin/auth/google/callback"),
    }
    missing = []
    for route in app.routes:
        path = getattr(route, "path", "")
        if not path.startswith("/admin"):
            continue
        for method in getattr(route, "methods", set()) - {"HEAD", "OPTIONS"}:
            probe = path.replace("{user_id}", "target-user").replace("{key_id}", "aaaaaaaaaaaaaaaa")
            if (method, path) in exempt or (method, probe) in exempt:
                continue
            if (method, probe) not in covered and (method, path) not in covered:
                missing.append(f"{method} {path}")
    assert not missing, (
        "these /admin routes have no authorization-matrix coverage: " + ", ".join(sorted(missing))
    )


def test_logout_needs_no_role_and_clears_the_cookie(tmp_path, monkeypatch):
    """POST /admin/logout takes no auth dependency at all — there is no role
    dimension to put in the matrix. This is the test the coverage exemption
    above points to."""
    configure(tmp_path, monkeypatch)
    with TestClient(app) as client:
        # Works with no session at all.
        assert client.post("/admin/logout").status_code == 200
        # And actually ends a real one.
        assert client.post("/admin/login", json={"username": "admin", "password": PASSWORD}).status_code == 200
        assert client.get("/admin/session").status_code == 200
        assert client.post("/admin/logout").status_code == 200
        assert client.get("/admin/session").status_code == 401


def test_expired_session_cookie_is_401(tmp_path, monkeypatch):
    """decode_admin_token in main.py checks `exp`, but nothing in the suite
    covers it, so a regression dropping that check would pass everything
    else. Mint an already-expired token with the app's own signing helper —
    sign_admin_token — by pinning a negative session TTL, rather than
    sleeping past a real one.

    Logging in first (so the local-admin row genuinely exists and would
    otherwise be a valid super_admin) matters: without it, this test would
    pass for the wrong reason — a 401 from "no such user" rather than from
    the exp check actually under test.
    """
    configure(tmp_path, monkeypatch)
    with TestClient(app) as client:
        assert client.post("/admin/login", json={"username": "admin", "password": PASSWORD}).status_code == 200
        assert client.get("/admin/session").status_code == 200

        monkeypatch.setenv("CIRCLEJERK_ADMIN_SESSION_SECONDS", "-10")
        get_settings.cache_clear()
        token = sign_admin_token(get_settings(), "local-admin")
        client.cookies.set(ADMIN_COOKIE_NAME, token)
        resp = client.get("/admin/session")
        assert resp.status_code == 401


def test_admin_users_response_excludes_google_identity_and_decision_fields(tmp_path, monkeypatch):
    """_user_record in main.py is an explicit field whitelist, not {**row}.
    Nothing pins that, so a regression to {**row} would leak Google's
    subject identifier and who decided each user's access, and pass every
    other test in the suite. Assert directly on a real response body."""
    configure(tmp_path, monkeypatch)
    with TestClient(app) as client:
        sign_in_as(client, "super_admin", "approved")
        body = client.get("/admin/users").json()
        assert body["users"], "expected at least the seeded target-user row"
        for user in body["users"]:
            assert "google_sub" not in user
            assert "decided_by" not in user
