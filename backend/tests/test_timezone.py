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
