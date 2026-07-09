from __future__ import annotations

from app import db


def seeded_conn(path):
    conn = db.connect(str(path))
    conn.executescript(db.SCHEMA)
    db.seed_db(conn)
    conn.commit()
    return conn


def _op(conn, oid, type_, ts, icao24="a1", emitter=None):
    db.upsert_operation(conn, db.operation_from_event({
        "id": oid, "type": type_, "icao24": icao24, "callsign": icao24.upper(),
        "timestamp": ts, "airport_icao": "KLMO", "emitter_category": emitter,
    }))


def test_operations_trends_counts_and_pct(tmp_path):
    conn = seeded_conn(tmp_path / "t.sqlite3")
    # 2026-06 (June): 2 landings, 1 takeoff, 1 touch_and_go, plus a circle that must be ignored.
    base = 1780000000  # somewhere in June 2026
    _op(conn, "l1", "landing", base + 10, emitter="A1")
    _op(conn, "l2", "landing", base + 20, emitter="A2")
    _op(conn, "t1", "takeoff", base + 30, emitter="A1")
    _op(conn, "g1", "touch_and_go", base + 40, emitter="A1")
    _op(conn, "c1", "circle", base + 50, emitter="A1")  # excluded from operations
    conn.commit()

    trends = db.airport_operations_trends(conn, "KLMO", now_ts=base + 100, months=12)

    assert trends["timezone"] == "America/Denver"
    month = next(m for m in trends["monthly"] if m["total"] > 0)
    assert month["landings"] == 2
    assert month["takeoffs"] == 1
    assert month["tg"] == 1
    assert month["total"] == 4  # circle excluded
    assert month["pct_tg"] == 25.0
    assert month["by_emitter"]["A1"] == 3
    assert month["by_emitter"]["A2"] == 1
    assert len(trends["time_of_day"]) == 24
    assert sum(h["operations"] for h in trends["time_of_day"]) == 4


def test_recent_days_pct_light(tmp_path):
    conn = seeded_conn(tmp_path / "t.sqlite3")
    base = 1780000000
    _op(conn, "l1", "landing", base + 10, emitter="A1")
    _op(conn, "l2", "landing", base + 20, emitter="A6")
    conn.commit()
    trends = db.airport_operations_trends(conn, "KLMO", now_ts=base + 100, months=12)
    day = next(d for d in trends["recent_days"] if d["operations"] > 0)
    assert day["operations"] == 2
    assert day["pct_light"] == 50.0  # one of two ops is A1 (Light)
