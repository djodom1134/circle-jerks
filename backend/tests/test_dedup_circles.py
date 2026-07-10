from __future__ import annotations

from app import db
from scripts.dedup_circles import dedup_circles
from tests.test_operations import seeded_conn


def _mk_circle_op(id_, ts, icao24="a4c1d8", icao="KBJC"):
    return db.operation_from_event({
        "id": id_, "type": "circle", "icao24": icao24, "callsign": "N1",
        "timestamp": ts, "airport_icao": icao,
    })


def _mk_landing_op(id_, ts, icao24="a4c1d8", icao="KBJC"):
    return db.operation_from_event({
        "id": id_, "type": "landing", "icao24": icao24, "callsign": "N1",
        "timestamp": ts, "airport_icao": icao, "runway_id": "12L",
    })


def test_dedup_circles_collapses_per_bucket_keeps_earliest_leaves_others_untouched(tmp_path):
    conn = seeded_conn(tmp_path / "t.sqlite3")

    # 5 circle rows within one 120s window (bucket 1000 // 120 == 8), earliest = op1.
    db.upsert_operation(conn, _mk_circle_op("op1", 1000))
    db.upsert_operation(conn, _mk_circle_op("op2", 1010))
    db.upsert_operation(conn, _mk_circle_op("op3", 1020))
    db.upsert_operation(conn, _mk_circle_op("op4", 1030))
    db.upsert_operation(conn, _mk_circle_op("op5", 1040))
    # 1 circle 200s later -> distinct bucket (1240 // 120 == 10).
    db.upsert_operation(conn, _mk_circle_op("op6", 1240))
    # A landing row in the same window that must be UNTOUCHED by the dedup.
    db.upsert_operation(conn, _mk_landing_op("land1", 1005))
    conn.commit()

    total_circles_before = len(db.read_operations(conn, "KBJC", 0, 10_000, types=["circle"]))
    assert total_circles_before == 6

    deleted, remaining = dedup_circles(conn, bucket_seconds=120)
    conn.commit()

    assert deleted == 4
    assert remaining == 2

    circle_rows = db.read_operations(conn, "KBJC", 0, 10_000, types=["circle"])
    assert len(circle_rows) == 2
    circle_ids = {r["id"] for r in circle_rows}
    # Earliest row per bucket is kept.
    assert circle_ids == {"op1", "op6"}

    # Non-circle row is never touched.
    landing_rows = db.read_operations(conn, "KBJC", 0, 10_000, types=["landing"])
    assert len(landing_rows) == 1
    assert landing_rows[0]["id"] == "land1"


def test_dedup_circles_scopes_to_icao(tmp_path):
    conn = seeded_conn(tmp_path / "t.sqlite3")

    # Two duplicate-bucket circles at KBJC, two at KLMO.
    db.upsert_operation(conn, _mk_circle_op("bjc1", 1000, icao="KBJC"))
    db.upsert_operation(conn, _mk_circle_op("bjc2", 1010, icao="KBJC"))
    db.upsert_operation(conn, _mk_circle_op("lmo1", 1000, icao="KLMO"))
    db.upsert_operation(conn, _mk_circle_op("lmo2", 1010, icao="KLMO"))
    conn.commit()

    deleted, remaining = dedup_circles(conn, bucket_seconds=120, icao="KBJC")
    conn.commit()

    assert deleted == 1
    assert remaining == 1

    # KBJC collapsed to the earliest row.
    bjc_rows = db.read_operations(conn, "KBJC", 0, 10_000, types=["circle"])
    assert [r["id"] for r in bjc_rows] == ["bjc1"]

    # KLMO untouched (out of scope).
    lmo_rows = db.read_operations(conn, "KLMO", 0, 10_000, types=["circle"])
    assert len(lmo_rows) == 2


def test_dedup_circles_dry_run_via_rollback_does_not_modify_db(tmp_path):
    conn = seeded_conn(tmp_path / "t.sqlite3")
    db.upsert_operation(conn, _mk_circle_op("op1", 1000))
    db.upsert_operation(conn, _mk_circle_op("op2", 1010))
    conn.commit()

    deleted, remaining = dedup_circles(conn, bucket_seconds=120)
    assert deleted == 1
    assert remaining == 1
    conn.rollback()  # simulate the CLI's dry-run path

    rows = db.read_operations(conn, "KBJC", 0, 10_000, types=["circle"])
    assert len(rows) == 2  # nothing actually removed
