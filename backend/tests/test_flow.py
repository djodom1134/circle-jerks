from __future__ import annotations

from app import db, flow


def seeded_conn(path):
    conn = db.connect(str(path))
    conn.executescript(db.SCHEMA)
    db.seed_db(conn)
    conn.commit()
    return conn


def test_flow_tables_and_helpers(tmp_path):
    conn = seeded_conn(tmp_path / "t.sqlite3")
    for table in ("runway_flow", "runway_changes"):
        assert conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone() is not None

    assert db.current_flow(conn, "KBJC") is None
    db.open_flow(conn, "KBJC", "30R", 1000, 300, 8.0)
    cur = db.current_flow(conn, "KBJC")
    assert cur["active_runway_id"] == "30R" and cur["ended_at"] is None

    # opening a new flow closes the previous open row
    db.close_open_flow(conn, "KBJC", 2000)
    db.open_flow(conn, "KBJC", "12L", 2000, 120, 9.0)
    assert db.current_flow(conn, "KBJC")["active_runway_id"] == "12L"
    closed = conn.execute(
        "SELECT ended_at FROM runway_flow WHERE icao='KBJC' AND active_runway_id='30R'"
    ).fetchone()
    assert closed["ended_at"] == 2000

    db.insert_runway_change(conn, "KBJC", "30R", "12L", 2000,
                            {"icao24": "a4c1d8", "callsign": "N4052F", "registration": "N4052F", "id": "op9"},
                            120, 9.0, wind_favored_new=0)
    changes = db.recent_runway_changes(conn, "KBJC", 10)
    assert len(changes) == 1
    assert changes[0]["to_runway_id"] == "12L"
    assert changes[0]["cowboy_callsign"] == "N4052F"
    assert changes[0]["trigger_op_id"] == "op9"
    assert changes[0]["wind_favored_new"] == 0
