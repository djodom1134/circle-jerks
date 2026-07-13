from __future__ import annotations

from app import db, ledger


def seeded_conn(path):
    conn = db.connect(str(path))
    conn.executescript(db.SCHEMA)
    db.seed_db(conn)
    conn.commit()
    return conn


def _op(conn, oid, type_, ts, icao24="a1"):
    db.upsert_operation(conn, db.operation_from_event({
        "id": oid, "type": type_, "icao24": icao24, "callsign": icao24.upper(),
        "timestamp": ts, "airport_icao": "KLMO",
    }))


# 2026-06-01 12:00:00 America/Denver == 1780336800 UTC
DAY1_NOON = 1780336800
DAY = 86400


def test_rollup_counts_runway_uses_and_excludes_takeoff(tmp_path):
    conn = seeded_conn(tmp_path / "t.sqlite3")
    _op(conn, "l1", "landing", DAY1_NOON)
    _op(conn, "g1", "touch_and_go", DAY1_NOON + 60)
    _op(conn, "a1", "low_approach", DAY1_NOON + 120)
    _op(conn, "t1", "takeoff", DAY1_NOON + 180)   # rolled up, but NOT a runway use
    _op(conn, "c1", "circle", DAY1_NOON + 240)    # not rolled up at all
    conn.commit()

    written = ledger.rebuild_rollup(conn, "KLMO", DAY1_NOON - DAY, DAY1_NOON + DAY)
    conn.commit()
    assert written == 4  # circle excluded

    totals = ledger.rollup_totals(conn, "KLMO", "2026-06-01", "2026-06-01")
    assert totals["runway_uses"] == 3            # takeoff excluded from the billable unit
    assert totals["by_type"]["landing"] == 1
    assert totals["by_type"]["touch_and_go"] == 1
    assert totals["by_type"]["low_approach"] == 1
    assert totals["unique_aircraft"] == 1


def test_rollup_buckets_by_local_day_not_utc_day(tmp_path):
    conn = seeded_conn(tmp_path / "t.sqlite3")
    # 2026-06-02 01:00 UTC is still 2026-06-01 19:00 in America/Denver.
    ts = 1780362000  # 2026-06-02T01:00:00Z
    assert db.local_day_key(ts, "America/Denver") == "2026-06-01"
    _op(conn, "l1", "landing", ts)
    conn.commit()

    ledger.rebuild_rollup(conn, "KLMO", ts - DAY, ts + DAY)
    conn.commit()

    assert ledger.rollup_totals(conn, "KLMO", "2026-06-01", "2026-06-01")["runway_uses"] == 1
    assert ledger.rollup_totals(conn, "KLMO", "2026-06-02", "2026-06-02")["runway_uses"] == 0


def test_rebuild_is_idempotent(tmp_path):
    conn = seeded_conn(tmp_path / "t.sqlite3")
    _op(conn, "l1", "landing", DAY1_NOON)
    conn.commit()
    ledger.rebuild_rollup(conn, "KLMO", DAY1_NOON - DAY, DAY1_NOON + DAY)
    ledger.rebuild_rollup(conn, "KLMO", DAY1_NOON - DAY, DAY1_NOON + DAY)
    conn.commit()
    assert ledger.rollup_totals(conn, "KLMO", "2026-06-01", "2026-06-01")["runway_uses"] == 1


def test_rebuild_drops_rows_for_deleted_operations(tmp_path):
    # The maintenance scripts in backend/scripts/ DELETE FROM operations. A rebuild
    # must not leave orphaned counts behind.
    conn = seeded_conn(tmp_path / "t.sqlite3")
    _op(conn, "l1", "landing", DAY1_NOON)
    conn.commit()
    ledger.rebuild_rollup(conn, "KLMO", DAY1_NOON - DAY, DAY1_NOON + DAY)
    conn.commit()

    conn.execute("DELETE FROM operations WHERE id='l1'")
    ledger.rebuild_rollup(conn, "KLMO", DAY1_NOON - DAY, DAY1_NOON + DAY)
    conn.commit()

    assert ledger.rollup_totals(conn, "KLMO", "2026-06-01", "2026-06-01")["runway_uses"] == 0


def test_daily_series_gap_fills_with_zeroes(tmp_path):
    conn = seeded_conn(tmp_path / "t.sqlite3")
    _op(conn, "l1", "landing", DAY1_NOON)
    _op(conn, "l2", "landing", DAY1_NOON + 2 * DAY)  # skip 2026-06-02
    conn.commit()
    ledger.rebuild_rollup(conn, "KLMO", DAY1_NOON - DAY, DAY1_NOON + 3 * DAY)
    conn.commit()

    series = ledger.rollup_daily_runway_uses(conn, "KLMO", "2026-06-01", "2026-06-03")
    assert [d["date"] for d in series] == ["2026-06-01", "2026-06-02", "2026-06-03"]
    assert [d["runway_uses"] for d in series] == [1, 0, 1]


def test_unique_aircraft_counts_distinct_icao24(tmp_path):
    conn = seeded_conn(tmp_path / "t.sqlite3")
    _op(conn, "l1", "landing", DAY1_NOON, icao24="aaa111")
    _op(conn, "l2", "landing", DAY1_NOON + 60, icao24="aaa111")
    _op(conn, "l3", "landing", DAY1_NOON + 120, icao24="bbb222")
    conn.commit()
    ledger.rebuild_rollup(conn, "KLMO", DAY1_NOON - DAY, DAY1_NOON + DAY)
    conn.commit()

    totals = ledger.rollup_totals(conn, "KLMO", "2026-06-01", "2026-06-01")
    assert totals["runway_uses"] == 3
    assert totals["unique_aircraft"] == 2
