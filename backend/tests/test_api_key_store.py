from __future__ import annotations

from app import db


def _open(tmp_path):
    path = str(tmp_path / "circlejerk.sqlite3")
    db.init_db(path)
    return db.connect(path)


def test_create_and_get_api_key(tmp_path):
    conn = _open(tmp_path)
    db.create_api_key(
        conn,
        key_id="0123456789abcdef",
        secret_hash="deadbeef",
        name="partner",
        scopes="ops:read,tracks:read",
        airports="KLMO",
        created_at=1_700_000_000,
        created_by="admin",
    )
    conn.commit()

    row = db.get_api_key(conn, "0123456789abcdef")
    assert row is not None
    assert row["name"] == "partner"
    assert row["scopes"] == "ops:read,tracks:read"
    assert row["airports"] == "KLMO"
    assert row["secret_hash"] == "deadbeef"
    assert row["revoked_at"] is None
    assert row["last_used_at"] is None
    assert db.get_api_key(conn, "ffffffffffffffff") is None


def test_list_api_keys_never_exposes_the_secret_hash(tmp_path):
    conn = _open(tmp_path)
    db.create_api_key(
        conn,
        key_id="0123456789abcdef",
        secret_hash="deadbeef",
        name="partner",
        scopes="ops:read",
        airports=None,
        created_at=1_700_000_000,
        created_by="admin",
    )
    conn.commit()

    rows = db.list_api_keys(conn)
    assert len(rows) == 1
    assert "secret_hash" not in rows[0]
    assert rows[0]["airports"] is None


def test_revoke_marks_the_row_and_is_idempotent(tmp_path):
    conn = _open(tmp_path)
    db.create_api_key(
        conn,
        key_id="0123456789abcdef",
        secret_hash="deadbeef",
        name="partner",
        scopes="ops:read",
        airports=None,
        created_at=1_700_000_000,
        created_by="admin",
    )
    conn.commit()

    assert db.revoke_api_key(conn, "0123456789abcdef", 1_700_000_100) is True
    conn.commit()
    assert db.get_api_key(conn, "0123456789abcdef")["revoked_at"] == 1_700_000_100

    # Already revoked -> no second write, and an unknown id is a no-op.
    assert db.revoke_api_key(conn, "0123456789abcdef", 1_700_000_200) is False
    assert db.revoke_api_key(conn, "ffffffffffffffff", 1_700_000_200) is False


def test_touch_updates_last_used_at(tmp_path):
    conn = _open(tmp_path)
    db.create_api_key(
        conn,
        key_id="0123456789abcdef",
        secret_hash="deadbeef",
        name="partner",
        scopes="ops:read",
        airports=None,
        created_at=1_700_000_000,
        created_by="admin",
    )
    conn.commit()

    db.touch_api_key(conn, "0123456789abcdef", 1_700_000_500)
    conn.commit()
    assert db.get_api_key(conn, "0123456789abcdef")["last_used_at"] == 1_700_000_500
