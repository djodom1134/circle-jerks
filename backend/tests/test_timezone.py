from __future__ import annotations

import sqlite3

import pytest

from app import db


def _cols(conn, table):
    return {row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def test_new_columns_and_tz_seed(tmp_path):
    path = str(tmp_path / "t.sqlite3")
    db.init_db(path)
    conn = db.connect(path)
    assert "timezone" in _cols(conn, "airports")
    assert "emitter_category" in _cols(conn, "operations")
    assert "emitter_category" in _cols(conn, "track_archive")
    tz = conn.execute("SELECT timezone FROM airports WHERE icao='KLMO'").fetchone()["timezone"]
    assert tz == "America/Denver"


def test_migrate_adds_columns_to_legacy_db(tmp_path):
    # Simulate a pre-existing DB missing the new columns, then migrate.
    path = str(tmp_path / "legacy.sqlite3")
    conn = db.connect(path)
    conn.executescript(
        "CREATE TABLE airports (icao TEXT PRIMARY KEY, name TEXT);"
        "CREATE TABLE operations (id TEXT PRIMARY KEY, type TEXT);"
        "CREATE TABLE track_archive (icao24 TEXT, timestamp INTEGER, PRIMARY KEY(icao24, timestamp));"
        "CREATE TABLE submission_aircraft (submission_id INTEGER, icao24 TEXT, PRIMARY KEY(submission_id, icao24));"
    )
    conn.commit()
    db._migrate(conn)
    conn.commit()
    assert "emitter_category" in _cols(conn, "operations")
    assert "emitter_category" in _cols(conn, "track_archive")
    assert "timezone" in _cols(conn, "airports")


# ─── Concurrent-migration guard (final-review FIX 7) ─────────────────────────
#
# uvicorn --workers 3 plus a separate worker container mean four or more
# processes call init_db -> _migrate on first boot. Each guards the ALTER
# with a PRAGMA table_info read taken before sqlite's write lock, not under
# it, so a loser can still hit "duplicate column name" on its own ALTER even
# though its own read said the column was missing. These tests exercise
# `_add_column_if_missing` directly against that race, since reproducing it
# through `_migrate` itself would require genuine concurrent connections.

def test_add_column_if_missing_swallows_a_duplicate_column_race(tmp_path):
    path = str(tmp_path / "race.sqlite3")
    conn = db.connect(path)
    # Simulate what a losing process sees: a sibling's ALTER already landed
    # between this process's own PRAGMA table_info read and its own ALTER.
    conn.executescript("CREATE TABLE api_keys (id TEXT PRIMARY KEY, owner_user_id TEXT);")
    conn.commit()

    db._add_column_if_missing(conn, "api_keys", "owner_user_id", "TEXT")  # must not raise

    assert "owner_user_id" in _cols(conn, "api_keys")


def test_add_column_if_missing_still_raises_on_a_real_operational_error(tmp_path):
    path = str(tmp_path / "race2.sqlite3")
    conn = db.connect(path)
    conn.executescript("CREATE TABLE api_keys (id TEXT PRIMARY KEY);")
    conn.commit()

    with pytest.raises(sqlite3.OperationalError):
        db._add_column_if_missing(conn, "no_such_table", "col", "TEXT")


from app.db import local_hour, local_day_key, local_month_key


def test_local_hour_denver_dst():
    # 2026-07-01 02:00:00 UTC = 2026-06-30 20:00 MDT (UTC-6 in summer).
    ts = 1782871200  # 2026-07-01T02:00:00Z
    assert local_hour(ts, "America/Denver") == 20
    assert local_day_key(ts, "America/Denver") == "2026-06-30"
    assert local_month_key(ts, "America/Denver") == "2026-06"


def test_local_hour_utc_fallback():
    ts = 1782871200
    assert local_hour(ts, None) == 2
    assert local_day_key(ts, None) == "2026-07-01"
