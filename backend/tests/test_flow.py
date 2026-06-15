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


def _op(ts, rid, icao24="a", oid=None):
    return {"timestamp": ts, "runway_id": rid, "icao24": icao24, "callsign": "N1",
            "registration": "N1", "id": oid or f"op{ts}"}


def test_trailing_run_and_established_end():
    ops = [_op(0, "12L"), _op(60, "12L"), _op(120, "30R"), _op(180, "30R"), _op(240, "30R")]
    rid, run_len, span = flow.trailing_run(ops)
    assert rid == "30R" and run_len == 3 and span == 120

    # 3 consecutive 30R establishes 30R over a previous 12L
    assert flow.established_end(ops, "12L") == "30R"
    # only 2 consecutive, short span → not yet established, keep previous
    assert flow.established_end([_op(0, "12L"), _op(60, "30R"), _op(120, "30R")], "12L") == "12L"
    # 2 ops but spanning >= 10 min → established by time
    assert flow.established_end([_op(0, "12L"), _op(60, "30R"), _op(700, "30R")], "12L") == "30R"
    # empty → keep previous
    assert flow.established_end([], "12L") == "12L"


def test_cowboy_for_end_is_earliest_of_trailing_run():
    ops = [_op(0, "12L"), _op(120, "30R", oid="first30"), _op(180, "30R"), _op(240, "30R", oid="last30")]
    cowboy = flow.cowboy_for_end(ops, "30R")
    assert cowboy["id"] == "first30"


def test_headwind_component():
    # wind from 300 onto runway heading 300 → full headwind (+)
    assert flow.headwind_component(300, 300, 10.0) == 10.0
    # opposite runway 120 → tailwind (negative)
    assert flow.headwind_component(120, 300, 10.0) == -10.0
    assert flow.headwind_component(None, 300, 10.0) is None
    assert flow.headwind_component(300, None, 10.0) is None


def _wind(from_deg, speed):
    return {"current": {"wind_from_dir_degrees": from_deg, "wind_speed_kt": speed}, "average": None, "error": None}


def test_process_establishes_and_flips_with_cowboy(tmp_path):
    conn = seeded_conn(tmp_path / "t.sqlite3")
    runways = db.runways_for_airport(conn, "KBJC")  # 12L=120°, 30R=300°, ...

    def persist(ts, rid, icao24, oid):
        db.upsert_operation(conn, db.operation_from_event({
            "id": oid, "type": "touch_and_go", "icao24": icao24, "callsign": icao24.upper(),
            "timestamp": ts, "airport_icao": "KBJC", "runway_id": rid, "runway_heading_deg": 300,
        }))

    # Establish 30R with 3 ops.
    for i, ts in enumerate((100, 160, 220)):
        persist(ts, "30R", "a1", f"e{i}")
    flow.process(conn, "KBJC", runways, [], _wind(300, 10.0), now=240)
    conn.commit()
    assert db.current_flow(conn, "KBJC")["active_runway_id"] == "30R"
    assert db.recent_runway_changes(conn, "KBJC") == []  # first establishment is not a "change"

    # Now 3 ops on 12L flip the flow — the first one is the cowboy.
    persist(400, "12L", "cowboy1", "c0")
    persist(460, "12L", "a2", "c1")
    persist(520, "12L", "a3", "c2")
    flow.process(conn, "KBJC", runways, [], _wind(300, 10.0), now=540)
    conn.commit()
    assert db.current_flow(conn, "KBJC")["active_runway_id"] == "12L"
    changes = db.recent_runway_changes(conn, "KBJC")
    assert len(changes) == 1
    assert changes[0]["from_runway_id"] == "30R" and changes[0]["to_runway_id"] == "12L"
    assert changes[0]["cowboy_icao24"] == "cowboy1"
    # wind still from 300 → 12L (120°) is a tailwind → not wind-favored
    assert changes[0]["wind_favored_new"] == 0


def test_process_tags_op_headwind(tmp_path):
    conn = seeded_conn(tmp_path / "t.sqlite3")
    runways = db.runways_for_airport(conn, "KBJC")
    db.upsert_operation(conn, db.operation_from_event({
        "id": "w1", "type": "touch_and_go", "icao24": "a", "callsign": "N1",
        "timestamp": 100, "airport_icao": "KBJC", "runway_id": "30R",
    }))
    event = {"id": "w1", "type": "touch_and_go", "icao24": "a", "airport_icao": "KBJC", "runway_id": "30R"}
    flow.process(conn, "KBJC", runways, [event], _wind(300, 10.0), now=120)
    conn.commit()
    row = db.read_operations(conn, "KBJC", 0, 10000)[0]
    assert row["wind_from_deg"] == 300
    assert row["headwind_kt"] == 10.0  # 30R heading 300 into wind from 300
