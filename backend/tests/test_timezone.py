from __future__ import annotations

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
