from __future__ import annotations

from app import db
from scripts.prune_crossing_circles import prune_crossing_circles
from tests.test_operations import seeded_conn


def _mk_circle_op(id_, ts, turn=None, icao24="a4c1d8", icao="KBJC"):
    # turn=None mimics the removed line-crossing detector (never set turn_direction);
    # turn='left'/'right' mimics the closed-lap detector (always sets it).
    return db.operation_from_event({
        "id": id_, "type": "circle", "icao24": icao24, "callsign": "N1",
        "timestamp": ts, "airport_icao": icao, "turn_direction": turn,
    })


def _mk_landing_op(id_, ts, icao24="a4c1d8", icao="KBJC"):
    return db.operation_from_event({
        "id": id_, "type": "landing", "icao24": icao24, "callsign": "N1",
        "timestamp": ts, "airport_icao": icao, "runway_id": "12L",
    })


def test_prune_deletes_crossing_circles_keeps_closed_laps_and_other_types(tmp_path):
    conn = seeded_conn(tmp_path / "t.sqlite3")

    # 3 line-crossing artifacts (turn_direction NULL) -> should be deleted.
    db.upsert_operation(conn, _mk_circle_op("x1", 1000))
    db.upsert_operation(conn, _mk_circle_op("x2", 1010))
    db.upsert_operation(conn, _mk_circle_op("x3", 1240))
    # 2 real closed laps (turn_direction set) -> must be kept.
    db.upsert_operation(conn, _mk_circle_op("lap1", 1500, turn="left"))
    db.upsert_operation(conn, _mk_circle_op("lap2", 1800, turn="right"))
    # A landing row must be UNTOUCHED.
    db.upsert_operation(conn, _mk_landing_op("land1", 1005))
    conn.commit()

    assert len(db.read_operations(conn, "KBJC", 0, 10_000, types=["circle"])) == 5

    deleted, remaining = prune_crossing_circles(conn)
    conn.commit()

    assert deleted == 3
    assert remaining == 2

    circle_ids = {r["id"] for r in db.read_operations(conn, "KBJC", 0, 10_000, types=["circle"])}
    assert circle_ids == {"lap1", "lap2"}  # only closed laps survive

    landing_rows = db.read_operations(conn, "KBJC", 0, 10_000, types=["landing"])
    assert len(landing_rows) == 1 and landing_rows[0]["id"] == "land1"


def test_prune_scopes_to_icao(tmp_path):
    conn = seeded_conn(tmp_path / "t.sqlite3")

    db.upsert_operation(conn, _mk_circle_op("bjc_x", 1000, icao="KBJC"))       # crossing @ KBJC
    db.upsert_operation(conn, _mk_circle_op("bjc_lap", 1010, turn="left", icao="KBJC"))
    db.upsert_operation(conn, _mk_circle_op("lmo_x", 1000, icao="KLMO"))       # crossing @ KLMO
    conn.commit()

    deleted, remaining = prune_crossing_circles(conn, icao="KBJC")
    conn.commit()

    assert deleted == 1     # only the KBJC crossing row
    assert remaining == 1   # the KBJC closed lap

    bjc_ids = {r["id"] for r in db.read_operations(conn, "KBJC", 0, 10_000, types=["circle"])}
    assert bjc_ids == {"bjc_lap"}

    # KLMO crossing untouched (out of scope).
    lmo_rows = db.read_operations(conn, "KLMO", 0, 10_000, types=["circle"])
    assert len(lmo_rows) == 1


def test_prune_dry_run_via_rollback_does_not_modify_db(tmp_path):
    conn = seeded_conn(tmp_path / "t.sqlite3")
    db.upsert_operation(conn, _mk_circle_op("x1", 1000))
    db.upsert_operation(conn, _mk_circle_op("lap1", 1010, turn="left"))
    conn.commit()

    deleted, remaining = prune_crossing_circles(conn)
    assert deleted == 1
    assert remaining == 1
    conn.rollback()  # simulate the CLI's dry-run path

    rows = db.read_operations(conn, "KBJC", 0, 10_000, types=["circle"])
    assert len(rows) == 2  # nothing actually removed
