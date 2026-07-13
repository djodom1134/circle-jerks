"""The data boundary is the single most important property of this service:
it must be STRUCTURALLY IMPOSSIBLE to write to the production database, not
merely a matter of application discipline. These tests prove the SQLite
engine itself refuses the write, and that this service's own database is a
completely separate file that behaves like an ordinary read-write database.
"""
from __future__ import annotations

import sqlite3

import pytest

from app import db


def _make_prod_file(tmp_path):
    path = tmp_path / "prod.sqlite3"
    conn = sqlite3.connect(str(path))
    conn.execute("CREATE TABLE operations (id TEXT PRIMARY KEY, icao TEXT)")
    conn.execute("INSERT INTO operations (id, icao) VALUES ('op1', 'KLMO')")
    conn.commit()
    conn.close()
    return path


def test_production_readonly_cannot_write(tmp_path):
    """The proof required by the plan: a write attempt through the read-only
    production connection must raise, at the SQLite engine level."""
    path = _make_prod_file(tmp_path)
    ro_conn = db.open_production_readonly(str(path))
    try:
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            ro_conn.execute("INSERT INTO operations (id, icao) VALUES ('op2', 'KBJC')")
    finally:
        ro_conn.close()


def test_production_readonly_cannot_update_existing_rows(tmp_path):
    path = _make_prod_file(tmp_path)
    ro_conn = db.open_production_readonly(str(path))
    try:
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            ro_conn.execute("UPDATE operations SET icao='KBJC' WHERE id='op1'")
    finally:
        ro_conn.close()


def test_production_readonly_cannot_delete(tmp_path):
    path = _make_prod_file(tmp_path)
    ro_conn = db.open_production_readonly(str(path))
    try:
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            ro_conn.execute("DELETE FROM operations WHERE id='op1'")
    finally:
        ro_conn.close()


def test_production_readonly_cannot_create_tables(tmp_path):
    """Not just DML — DDL against the production file is refused too. A bug
    that tried to "helpfully" migrate the production schema from this
    service cannot succeed."""
    path = _make_prod_file(tmp_path)
    ro_conn = db.open_production_readonly(str(path))
    try:
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            ro_conn.execute("CREATE TABLE daily_operation_rollup (icao TEXT)")
    finally:
        ro_conn.close()


def test_production_readonly_can_still_read(tmp_path):
    """The point is not to break reads — only writes."""
    path = _make_prod_file(tmp_path)
    ro_conn = db.open_production_readonly(str(path))
    try:
        row = ro_conn.execute("SELECT icao FROM operations WHERE id='op1'").fetchone()
        assert row["icao"] == "KLMO"
    finally:
        ro_conn.close()


def test_production_readonly_refuses_to_open_a_missing_file(tmp_path):
    """mode=ro must not silently create an empty database — that would hide
    a misconfigured path behind an empty-but-valid-looking response."""
    missing = tmp_path / "does-not-exist.sqlite3"
    with pytest.raises(sqlite3.OperationalError):
        db.open_production_readonly(str(missing))


def test_ledger_db_is_a_completely_separate_file(tmp_path):
    """The derived tables never land in the production file, under any
    circumstance — they live in a file this service opened itself, read-write,
    at a path that has nothing to do with the production path."""
    prod_path = _make_prod_file(tmp_path)
    ledger_path = tmp_path / "ledger.sqlite3"

    rw_conn = db.open_ledger_db(str(ledger_path))
    try:
        rw_conn.execute(
            "INSERT INTO daily_operation_rollup (icao, date_local, event_type, icao24, count) "
            "VALUES ('KLMO', '2026-01-01', 'landing', 'aaa111', 1)"
        )
        rw_conn.commit()
    finally:
        rw_conn.close()

    # The production file was never touched: it has no idea this table exists.
    check = sqlite3.connect(str(prod_path))
    tables = {
        row[0] for row in check.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    check.close()
    assert "daily_operation_rollup" not in tables
    assert "aircraft_home_base" not in tables

    # And the ledger file genuinely has the row.
    verify = sqlite3.connect(str(ledger_path))
    verify.row_factory = sqlite3.Row
    row = verify.execute("SELECT * FROM daily_operation_rollup WHERE icao='KLMO'").fetchone()
    verify.close()
    assert row["count"] == 1


def test_open_ledger_db_is_read_write(tmp_path):
    path = tmp_path / "ledger.sqlite3"
    conn = db.open_ledger_db(str(path))
    conn.execute(
        "INSERT INTO aircraft_home_base "
        "  (icao, icao24, locality, signal_strength, based_icao, evidence_json, computed_at) "
        "VALUES ('KLMO', 'aaa111', 'local', 0.6, NULL, '[]', 100)"
    )
    conn.commit()
    row = conn.execute(
        "SELECT locality FROM aircraft_home_base WHERE icao24='aaa111'"
    ).fetchone()
    assert row["locality"] == "local"
    conn.close()


def test_migrate_self_heals_a_home_base_table_from_before_the_rename(tmp_path):
    """`LEDGER_SCHEMA` is CREATE TABLE IF NOT EXISTS, so on a database that
    already carries the pre-rename `confidence` column, `executescript` alone
    is a silent no-op. `aircraft_home_base` is a pure derived cache rebuilt
    nightly, so a shape this service cannot use is dropped and recreated
    rather than left to break every future write.
    """
    path = tmp_path / "legacy.sqlite3"
    conn = sqlite3.connect(str(path))
    conn.executescript("""
        CREATE TABLE aircraft_home_base (
          icao TEXT NOT NULL, icao24 TEXT NOT NULL, locality TEXT NOT NULL,
          confidence REAL NOT NULL, based_icao TEXT, evidence_json TEXT NOT NULL,
          computed_at INTEGER NOT NULL, PRIMARY KEY (icao, icao24)
        );
    """)
    conn.execute(
        "INSERT INTO aircraft_home_base VALUES ('KLMO','old001','non_local',0.8,'KBDU','[]',1)"
    )
    conn.commit()
    conn.close()

    # Exactly what this service does on startup: open its own db.
    healed = db.open_ledger_db(str(path))
    cols = {row["name"] for row in healed.execute("PRAGMA table_info(aircraft_home_base)")}
    assert "signal_strength" in cols
    assert "confidence" not in cols
    # And the old row is gone -- it's a derived cache, safe to have dropped.
    assert healed.execute("SELECT COUNT(*) AS n FROM aircraft_home_base").fetchone()["n"] == 0
    healed.close()


def test_two_connections_are_never_confused_at_the_type_level(tmp_path):
    """Not a type-system guarantee (both are plain sqlite3.Connection) but a
    behavioral one: the read-only connection refuses a write EVEN WHEN the
    table structurally exists on that file -- so this is not merely "no such
    table", it is the connection itself refusing. A bug that passed the wrong
    connection to ledger.rebuild_rollup / homebase.recompute_airport would
    fail on the very first write, every time, not just under contention.
    """
    path = tmp_path / "prod.sqlite3"
    setup = sqlite3.connect(str(path))
    setup.execute(
        "CREATE TABLE daily_operation_rollup "
        "(icao TEXT, date_local TEXT, event_type TEXT, icao24 TEXT, count INTEGER)"
    )
    setup.commit()
    setup.close()

    ro_conn = db.open_production_readonly(str(path))
    try:
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            ro_conn.execute(
                "INSERT INTO daily_operation_rollup (icao, date_local, event_type, icao24, count) "
                "VALUES ('KLMO', '2026-01-01', 'landing', 'aaa111', 1)"
            )
    finally:
        ro_conn.close()
