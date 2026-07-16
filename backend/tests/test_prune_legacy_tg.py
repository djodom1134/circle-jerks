from app import db
from scripts.prune_legacy_touch_and_gos import prune_legacy_touch_and_gos


def _conn(tmp_path):
    c = db.connect(str(tmp_path / "p.sqlite3"))
    c.executescript(db.SCHEMA); db.seed_db(c); c.commit()
    return c


def _op(conn, oid, type_, turn, icao24="aa11"):
    db.upsert_operation(conn, db.operation_from_event({
        "id": oid, "type": type_, "icao24": icao24, "callsign": "N", "timestamp": 1780000000,
        "airport_icao": "KLMO", "turn_direction": turn, "runway_id": "11"}))


def test_deletes_only_null_turn_touch_and_gos(tmp_path):
    conn = _conn(tmp_path)
    _op(conn, "legacy1", "touch_and_go", None)     # episode-based
    _op(conn, "legacy2", "touch_and_go", None)
    _op(conn, "derived1", "touch_and_go", "left")  # circle-derived
    _op(conn, "circle1", "circle", "left")         # not a t&g
    _op(conn, "low1", "low_approach", None)        # not a t&g
    conn.commit()

    deleted, remaining = prune_legacy_touch_and_gos(conn)
    assert deleted == 2 and remaining == 1
    ids = {r[0] for r in conn.execute("SELECT id FROM operations")}
    assert ids == {"derived1", "circle1", "low1"}


def test_scoped_to_icao(tmp_path):
    conn = _conn(tmp_path)
    _op(conn, "a", "touch_and_go", None, icao24="aa11")
    _op(conn, "b", "touch_and_go", None, icao24="bb22")
    conn.commit()
    deleted, _ = prune_legacy_touch_and_gos(conn, icao24="aa11")
    assert deleted == 1
    assert {r[0] for r in conn.execute("SELECT id FROM operations")} == {"b"}
