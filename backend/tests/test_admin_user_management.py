from __future__ import annotations

import pytest
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
        # Use empty string instead of delenv: pydantic-settings reads from the
        # .env file on disk even when a variable is deleted from os.environ.
        # Explicitly setting to empty string takes precedence over .env.
        monkeypatch.setenv("ADMIN_SUPERUSERS", "")
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


def become(client, role, status="approved"):
    """Rewrite the local-admin row's role/status in place.

    The session cookie names a row (local-admin), not a role, so this is how
    the same authenticated session can be re-tested as a plain admin or a
    partner without a second login flow.
    """
    with db_session(get_settings().database_path) as conn:
        db.set_admin_user_access(
            conn, "local-admin", role=role, status=status,
            granted_scopes=None, granted_airports=None, decided_by=None, now=1,
        )


def assert_all_five_routes_are_forbidden(client):
    approve_payload = {"role": "partner", "scopes": [], "airports": None}
    assert client.get("/admin/users").status_code == 403
    assert client.post("/admin/users/u2/approve", json=approve_payload).status_code == 403
    assert client.post("/admin/users/u2/reject").status_code == 403
    assert client.post("/admin/users/u2/suspend").status_code == 403
    assert client.patch("/admin/users/u2", json=approve_payload).status_code == 403


def test_only_a_super_admin_may_use_the_five_user_management_routes(tmp_path, monkeypatch):
    # Every other test in this file drives these routes as local-admin, a
    # super_admin. That leaves the actual regression that matters here — a
    # route dropped from require_super_admin to require_admin, letting a
    # plain admin promote themselves to super_admin — invisible to the
    # suite. Re-test the same session after rewriting its role in place.
    configure(tmp_path, monkeypatch)
    with TestClient(app) as client:
        login(client)
        seed_partner()

        become(client, "admin")
        assert_all_five_routes_are_forbidden(client)

        become(client, "partner")
        assert_all_five_routes_are_forbidden(client)


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


def test_reapproving_an_approved_partner_with_a_narrowed_grant_revokes_keys(tmp_path, monkeypatch):
    # approve has no status precondition: calling it again on an
    # already-approved partner is a grant edit, and must cascade exactly
    # like PATCH does.
    configure(tmp_path, monkeypatch)
    with TestClient(app) as client:
        login(client)
        seed_partner(status="approved")
        client.post("/admin/users/u2/approve", json={
            "role": "partner", "scopes": ["ops:read", "ledger:read"], "airports": ["KLMO"],
        })
        make_key("u2", "aaaaaaaaaaaaaaaa", scopes="ops:read", airports="KLMO")
        make_key("u2", "bbbbbbbbbbbbbbbb", scopes="ledger:read", airports="KLMO")

        resp = client.post("/admin/users/u2/approve", json={
            "role": "partner", "scopes": ["ops:read"], "airports": ["KLMO"],
        })
        assert resp.status_code == 200
        assert resp.json()["revoked_keys"] == 1
        with db_session(get_settings().database_path) as conn:
            assert db.get_api_key(conn, "aaaaaaaaaaaaaaaa")["revoked_at"] is None
            assert db.get_api_key(conn, "bbbbbbbbbbbbbbbb")["revoked_at"] is not None


def test_reapproving_an_approved_partner_with_a_widened_grant_revokes_nothing(tmp_path, monkeypatch):
    configure(tmp_path, monkeypatch)
    with TestClient(app) as client:
        login(client)
        seed_partner(status="approved")
        client.post("/admin/users/u2/approve", json={
            "role": "partner", "scopes": ["ops:read"], "airports": ["KLMO"],
        })
        make_key("u2", "aaaaaaaaaaaaaaaa", scopes="ops:read", airports="KLMO")

        resp = client.post("/admin/users/u2/approve", json={
            "role": "partner", "scopes": ["ops:read", "ledger:read"], "airports": ["KLMO"],
        })
        assert resp.status_code == 200
        assert resp.json()["revoked_keys"] == 0
        with db_session(get_settings().database_path) as conn:
            assert db.get_api_key(conn, "aaaaaaaaaaaaaaaa")["revoked_at"] is None


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
        # Scopes widen; airports stay the same restricted grant. (Airports is
        # no longer nullable for a partner grant — see the airports-explicit
        # tests below — so this only exercises scope widening.)
        resp = client.patch("/admin/users/u2", json={
            "role": "partner", "scopes": ["ops:read", "ledger:read"], "airports": ["KLMO"],
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


def test_unknown_role_is_rejected(tmp_path, monkeypatch):
    configure(tmp_path, monkeypatch)
    with TestClient(app) as client:
        login(client)
        seed_partner()
        resp = client.post("/admin/users/u2/approve", json={
            "role": "root", "scopes": [], "airports": None,
        })
        assert resp.status_code == 400
        with db_session(get_settings().database_path) as conn:
            # The row must be untouched: a rejected write must not leak a
            # partial change into the database.
            assert db.get_admin_user(conn, "u2")["role"] == "partner"
            assert db.get_admin_user(conn, "u2")["status"] == "pending"


def test_unknown_status_filter_is_rejected(tmp_path, monkeypatch):
    configure(tmp_path, monkeypatch)
    with TestClient(app) as client:
        login(client)
        assert client.get("/admin/users?status=bogus").status_code == 400


def test_unknown_user_is_404(tmp_path, monkeypatch):
    configure(tmp_path, monkeypatch)
    with TestClient(app) as client:
        login(client)
        assert client.post("/admin/users/nope/suspend").status_code == 404


# ─── Partner grants must state `airports` explicitly (final-review FIX 1) ───
#
# api_keys.serialize_airports documents "None or an empty list both mean 'all
# airports'". UserAccessRequest.airports defaulted to None, so approving a
# partner without ever mentioning airports silently granted every airport at
# the site — verified live by the reviewer via
# POST /admin/users/p1/approve {"role":"partner","scopes":["ops:read"]} => 200
# with granted_airports = NULL. The admin UI's resolveAirportsSelection
# already refused to submit that implicit default, which meant the UI, not
# the server, was the only thing enforcing the feature's whole thesis. These
# tests pin the server-side fix.

def test_partner_approval_with_airports_omitted_is_rejected(tmp_path, monkeypatch):
    configure(tmp_path, monkeypatch)
    with TestClient(app) as client:
        login(client)
        seed_partner()
        resp = client.post("/admin/users/u2/approve", json={
            "role": "partner", "scopes": ["ops:read"],
        })
        assert resp.status_code == 400
        with db_session(get_settings().database_path) as conn:
            row = db.get_admin_user(conn, "u2")
            assert row["status"] == "pending"
            assert row["granted_airports"] is None


def test_partner_approval_with_an_explicit_empty_airports_list_is_rejected(tmp_path, monkeypatch):
    configure(tmp_path, monkeypatch)
    with TestClient(app) as client:
        login(client)
        seed_partner()
        resp = client.post("/admin/users/u2/approve", json={
            "role": "partner", "scopes": ["ops:read"], "airports": [],
        })
        assert resp.status_code == 400


def test_partner_approval_accepts_the_explicit_all_airports_sentinel(tmp_path, monkeypatch):
    configure(tmp_path, monkeypatch)
    with TestClient(app) as client:
        login(client)
        seed_partner()
        resp = client.post("/admin/users/u2/approve", json={
            "role": "partner", "scopes": ["ops:read"], "airports": ["*"],
        })
        assert resp.status_code == 200
        assert resp.json()["user"]["airports"] is None
        with db_session(get_settings().database_path) as conn:
            assert db.get_admin_user(conn, "u2")["granted_airports"] is None


def test_partner_approval_rejects_the_sentinel_mixed_with_real_airports(tmp_path, monkeypatch):
    configure(tmp_path, monkeypatch)
    with TestClient(app) as client:
        login(client)
        seed_partner()
        resp = client.post("/admin/users/u2/approve", json={
            "role": "partner", "scopes": ["ops:read"], "airports": ["*", "KLMO"],
        })
        assert resp.status_code == 400


def test_partner_approval_with_specific_airports_still_works(tmp_path, monkeypatch):
    configure(tmp_path, monkeypatch)
    with TestClient(app) as client:
        login(client)
        seed_partner()
        resp = client.post("/admin/users/u2/approve", json={
            "role": "partner", "scopes": ["ops:read"], "airports": ["KLMO"],
        })
        assert resp.status_code == 200
        assert resp.json()["user"]["airports"] == ["KLMO"]


def test_admin_role_approval_is_unaffected_by_the_airports_requirement(tmp_path, monkeypatch):
    # admin/super_admin ignore submitted scopes/airports entirely and store a
    # NULL grant regardless — the explicit-airports requirement is scoped to
    # role "partner" only, per _normalize_access.
    configure(tmp_path, monkeypatch)
    with TestClient(app) as client:
        login(client)
        seed_partner()
        resp = client.post("/admin/users/u2/approve", json={
            "role": "admin", "scopes": [],
        })
        assert resp.status_code == 200
        assert resp.json()["user"]["airports"] is None


def test_partner_patch_with_airports_omitted_is_rejected(tmp_path, monkeypatch):
    # Same rule on the PATCH (edit) path, not just approve.
    configure(tmp_path, monkeypatch)
    with TestClient(app) as client:
        login(client)
        seed_partner(status="approved")
        resp = client.patch("/admin/users/u2", json={
            "role": "partner", "scopes": ["ops:read"],
        })
        assert resp.status_code == 400


# ─── A blank-string list entry must not slip past the airports guard
# (confirmation-review Fix A) ───
#
# `if not payload.airports:` only rejects an omitted/null field or an explicit
# `[]` — but a one-element list of whitespace (`[""]`, `["  "]`, `["\t"]`,
# `["", ""]`) is truthy, so it sailed past that check. `_normalize_access`
# then handed the list straight to `api_keys.serialize_airports`, which
# strips every entry down to nothing and returns None: an unrestricted NULL
# grant produced by exactly what a client sends when a comma-separated text
# field is left empty. The reviewer proved this end to end over real HTTP:
#   approve {"role":"partner","scopes":["ops:read"],"airports":["  "]} => 200,
#   granted_airports = NULL, and a partner-minted key with airports=["KBJC"]
#   then worked against GET /v1/operations?airport=KBJC.
# `_normalize_access` now strips/uppercases/drops-blanks *before* the
# emptiness check, so every one of these inputs is indistinguishable from an
# omitted field and hits the same 400.

BLANK_AIRPORTS_PAYLOADS = [[""], ["  "], ["\t"], ["", ""]]


@pytest.mark.parametrize("airports", BLANK_AIRPORTS_PAYLOADS)
def test_partner_approval_rejects_blank_string_airports_entries(tmp_path, monkeypatch, airports):
    configure(tmp_path, monkeypatch)
    with TestClient(app) as client:
        login(client)
        seed_partner()
        resp = client.post("/admin/users/u2/approve", json={
            "role": "partner", "scopes": ["ops:read"], "airports": airports,
        })
        assert resp.status_code == 400
        with db_session(get_settings().database_path) as conn:
            row = db.get_admin_user(conn, "u2")
            assert row["status"] == "pending"
            assert row["granted_airports"] is None


@pytest.mark.parametrize("airports", BLANK_AIRPORTS_PAYLOADS)
def test_partner_patch_rejects_blank_string_airports_entries(tmp_path, monkeypatch, airports):
    configure(tmp_path, monkeypatch)
    with TestClient(app) as client:
        login(client)
        seed_partner(status="approved")
        with db_session(get_settings().database_path) as conn:
            db.set_admin_user_access(
                conn, "u2", role="partner", status="approved",
                granted_scopes="ops:read", granted_airports="KLMO",
                decided_by=None, now=1,
            )
        resp = client.patch("/admin/users/u2", json={
            "role": "partner", "scopes": ["ops:read"], "airports": airports,
        })
        assert resp.status_code == 400
        with db_session(get_settings().database_path) as conn:
            row = db.get_admin_user(conn, "u2")
            assert row["granted_airports"] == "KLMO"
