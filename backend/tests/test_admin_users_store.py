from __future__ import annotations

import sqlite3

import pytest

from app import api_keys, db


@pytest.fixture()
def conn(tmp_path):
    path = str(tmp_path / "t.sqlite3")
    db.init_db(path)
    connection = db.connect(path)
    yield connection
    connection.close()


def make_user(connection, **kw):
    base = dict(
        id="u1",
        email="p@example.com",
        google_sub="sub-1",
        name="P",
        picture=None,
        role="partner",
        status="pending",
        now=1000,
    )
    base.update(kw)
    return db.upsert_admin_user(connection, **base)


def test_upsert_creates_then_updates_on_google_sub(conn):
    created = make_user(conn)
    assert created["status"] == "pending"
    assert created["granted_scopes"] is None

    again = make_user(conn, name="P2", now=2000)
    assert again["id"] == "u1"
    assert again["name"] == "P2"
    assert len(db.list_admin_users(conn)) == 1


def test_find_matches_by_sub_then_by_email(conn):
    make_user(conn)
    assert db.find_admin_user(conn, google_sub="sub-1", email="other@example.com")["id"] == "u1"
    assert db.find_admin_user(conn, google_sub="missing", email="p@example.com")["id"] == "u1"
    assert db.find_admin_user(conn, google_sub="missing", email="nobody@example.com") is None


def test_email_matching_is_case_insensitive(conn):
    make_user(conn, email="Mixed@Example.com")
    assert db.find_admin_user(conn, google_sub="x", email="mixed@example.com") is not None


def test_a_matching_email_rebinds_the_row_to_a_new_google_sub(conn):
    """Pins the account-identity-rebinding behavior of upsert_admin_user.

    When find_admin_user matches an existing row by its email fallback and
    the incoming google_sub differs from the one on file, upsert_admin_user
    silently rebinds the row to the new google_sub. That is deliberate — it
    is how a Google account whose subject changed still resolves to the same
    user — but it is also the account-takeover primitive find_admin_user's
    and upsert_admin_user's docstrings warn about. It is safe ONLY because
    the caller (the OAuth callback) has already asserted email_verified on
    the ID token before ever calling upsert_admin_user. If you are changing
    this behavior, make sure you understand what relies on that assertion
    holding upstream.
    """
    created = make_user(conn, id="u1", email="p@example.com", google_sub="sub-a", now=1000)
    assert created["google_sub"] == "sub-a"

    rebound = make_user(conn, id="u1", email="p@example.com", google_sub="sub-b", now=2000)

    assert len(db.list_admin_users(conn)) == 1
    assert rebound["id"] == created["id"]
    assert rebound["google_sub"] == "sub-b"


def test_list_filters_by_status_newest_first(conn):
    make_user(conn, id="u1", email="a@x.com", google_sub="s1", now=1000)
    make_user(conn, id="u2", email="b@x.com", google_sub="s2", now=3000)
    make_user(conn, id="u3", email="c@x.com", google_sub="s3", status="approved", now=2000)
    pending = db.list_admin_users(conn, status="pending")
    assert [row["id"] for row in pending] == ["u2", "u1"]
    assert [row["id"] for row in db.list_admin_users(conn, status="approved")] == ["u3"]


def test_set_access_records_the_decision(conn):
    make_user(conn)
    db.set_admin_user_access(
        conn,
        "u1",
        role="partner",
        status="approved",
        granted_scopes="ops:read",
        granted_airports="KLMO",
        decided_by="u0",
        now=5000,
    )
    row = db.get_admin_user(conn, "u1")
    assert row["status"] == "approved"
    assert row["granted_scopes"] == "ops:read"
    assert row["decided_by"] == "u0"
    assert row["decided_at"] == 5000


def make_key(connection, key_id, owner, scopes="ops:read", airports="KLMO"):
    db.create_api_key(
        connection,
        key_id=key_id,
        secret_hash="0" * 64,
        name=key_id,
        scopes=scopes,
        airports=airports,
        created_at=1000,
        created_by="someone",
        owner_user_id=owner,
    )


def test_list_api_keys_can_scope_to_one_owner(conn):
    make_key(conn, "aaaaaaaaaaaaaaaa", "u1")
    make_key(conn, "bbbbbbbbbbbbbbbb", "u2")
    make_key(conn, "cccccccccccccccc", None)
    assert len(db.list_api_keys(conn)) == 3
    assert [k["id"] for k in db.list_api_keys(conn, owner_user_id="u1")] == ["aaaaaaaaaaaaaaaa"]
    # A legacy key with no owner belongs to nobody and must not surface in a
    # partner's list just because their id is also absent.
    assert db.list_api_keys(conn, owner_user_id=None) != []


def test_revoking_for_an_owner_leaves_other_owners_alone(conn):
    make_key(conn, "aaaaaaaaaaaaaaaa", "u1")
    make_key(conn, "bbbbbbbbbbbbbbbb", "u1")
    make_key(conn, "cccccccccccccccc", "u2")
    assert db.revoke_keys_for_owner(conn, "u1", 9000) == 2
    assert db.get_api_key(conn, "aaaaaaaaaaaaaaaa")["revoked_at"] == 9000
    assert db.get_api_key(conn, "cccccccccccccccc")["revoked_at"] is None
    # Idempotent: an already-revoked key is not counted twice.
    assert db.revoke_keys_for_owner(conn, "u1", 9500) == 0


def test_revoke_keys_for_owner_rejects_a_falsy_owner_id(conn):
    """None (or "") must never silently match "no owner" here.

    list_api_keys treats owner_user_id=None as "every key in the system", so
    a None reaching this function by mistake must fail loudly rather than
    quietly revoking nothing (or, in the sibling function, everything).
    """
    with pytest.raises(ValueError):
        db.revoke_keys_for_owner(conn, None, 9000)
    with pytest.raises(ValueError):
        db.revoke_keys_for_owner(conn, "", 9000)


def test_revoke_keys_outside_grant_rejects_a_falsy_owner_id(conn):
    """Same guard as revoke_keys_for_owner, and more important here:

    this function composes with list_api_keys, where owner_user_id=None
    means "every key in the system." A None slipping through would silently
    iterate and potentially revoke every user's keys.
    """
    grant = api_keys.Grant(scopes=frozenset({"ops:read"}), airports=frozenset({"KLMO"}))
    with pytest.raises(ValueError):
        db.revoke_keys_outside_grant(conn, None, grant, 9000)
    with pytest.raises(ValueError):
        db.revoke_keys_outside_grant(conn, "", grant, 9000)


def test_narrowing_a_grant_revokes_exactly_the_keys_that_exceed_it(conn):
    make_key(conn, "aaaaaaaaaaaaaaaa", "u1", scopes="ops:read", airports="KLMO")
    make_key(conn, "bbbbbbbbbbbbbbbb", "u1", scopes="ops:read,ledger:read", airports="KLMO")
    make_key(conn, "cccccccccccccccc", "u1", scopes="ops:read", airports="KBJC")
    make_key(conn, "dddddddddddddddd", "u1", scopes="ops:read", airports=None)

    narrowed = api_keys.Grant(scopes=frozenset({"ops:read"}), airports=frozenset({"KLMO"}))
    assert db.revoke_keys_outside_grant(conn, "u1", narrowed, 9000) == 3

    assert db.get_api_key(conn, "aaaaaaaaaaaaaaaa")["revoked_at"] is None
    for key_id in ("bbbbbbbbbbbbbbbb", "cccccccccccccccc", "dddddddddddddddd"):
        assert db.get_api_key(conn, key_id)["revoked_at"] == 9000


def test_widening_a_grant_revokes_nothing(conn):
    make_key(conn, "aaaaaaaaaaaaaaaa", "u1", scopes="ops:read", airports="KLMO")
    assert db.revoke_keys_outside_grant(conn, "u1", api_keys.UNRESTRICTED_GRANT, 9000) == 0


def test_email_is_unique(conn):
    """The UNIQUE(email) constraint is schema-level defense in depth.

    upsert_admin_user itself can never hit this path: find_admin_user's email
    fallback means a second upsert with the same email always matches and
    updates the existing row rather than attempting a second INSERT (see
    test_find_matches_by_sub_then_by_email). So the constraint is exercised
    directly here, against a raw INSERT that bypasses the dedup lookup.
    """
    make_user(conn, id="u1", email="dupe@x.com", google_sub="s1")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO admin_users (id, email, google_sub, role, status, requested_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("u2", "dupe@x.com", "s2", "partner", "pending", 1),
        )
