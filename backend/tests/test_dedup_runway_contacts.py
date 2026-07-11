"""Historical de-dup of the runway-contact over-count (low approach / landing /
takeoff).

Fixed going forward in app/detectors.py: the id now anchors on the touchdown's
DEEPEST sample, not the merged low-run's FIRST bucket, which grew earlier as the
sliding window fed in more of the descent — drifting the id and smearing one
touchdown across several rows. This script collapses the rows already stored
under the old drifting id.

`_runway_low_episodes` merges CONSECUTIVE low buckets into one episode, so the
detector never emits two touchdowns for one aircraft inside one anchor bucket.
Runway-contact rows of one type for one aircraft that share an anchor bucket are
therefore always re-detections of a single physical touchdown — safe to merge.
"""
from __future__ import annotations

from app import db
from scripts.dedup_runway_contacts import dedup_runway_contacts


def _seed(conn, typ, icao24, ts, oid):
    db.upsert_operation(conn, db.operation_from_event({
        "id": oid, "type": typ, "icao24": icao24, "callsign": icao24.upper(),
        "timestamp": ts, "airport_icao": "KLMO",
    }))


def _conn(tmp_path):
    c = db.connect(str(tmp_path / "d.sqlite3"))
    c.executescript(db.SCHEMA)
    db.seed_db(c)
    c.commit()
    return c


def test_collapses_same_touchdown_redetections(tmp_path):
    conn = _conn(tmp_path)
    # One physical touchdown re-detected under three drifting ids — the deepest
    # sample is stable, so all three carry the same timestamp.
    for i in range(3):
        _seed(conn, "low_approach", "aa11", 1780000000, f"drift-{i}")
    # A genuinely separate touchdown a full lap later.
    _seed(conn, "low_approach", "aa11", 1780000000 + 400, "lap2")
    conn.commit()

    deleted, remaining = dedup_runway_contacts(conn)
    assert deleted == 2
    assert remaining == 2  # one row per real touchdown
    # The earliest-inserted row of the collapsed touchdown survives.
    ids = {r[0] for r in conn.execute(
        "SELECT id FROM operations WHERE type='low_approach'")}
    assert "lap2" in ids
    assert len(ids & {"drift-0", "drift-1", "drift-2"}) == 1


def test_landings_and_takeoffs_deduped_too(tmp_path):
    conn = _conn(tmp_path)
    for i in range(2):
        _seed(conn, "landing", "bb22", 1780000000, f"land-{i}")
    for i in range(2):
        _seed(conn, "takeoff", "bb22", 1780000000, f"toff-{i}")
    conn.commit()
    deleted, remaining = dedup_runway_contacts(conn)
    assert deleted == 2  # one duplicate each of landing + takeoff
    assert remaining == 2


def test_distinct_types_same_bucket_not_merged(tmp_path):
    """A low approach and a landing sharing an anchor bucket are different event
    kinds — never collapse across type."""
    conn = _conn(tmp_path)
    _seed(conn, "low_approach", "cc33", 1780000000, "lo")
    _seed(conn, "landing", "cc33", 1780000000, "la")
    conn.commit()
    deleted, remaining = dedup_runway_contacts(conn)
    assert deleted == 0 and remaining == 2


def test_circles_never_touched(tmp_path):
    conn = _conn(tmp_path)
    for i in range(3):
        db.upsert_operation(conn, db.operation_from_event({
            "id": f"circ-{i}", "type": "circle", "icao24": "dd44",
            "callsign": "DD44", "timestamp": 1780000000, "airport_icao": "KLMO",
            "turn_direction": "left",
        }))
    conn.commit()
    deleted, _ = dedup_runway_contacts(conn)
    assert deleted == 0
    assert conn.execute("SELECT COUNT(*) FROM operations WHERE type='circle'").fetchone()[0] == 3


def test_scoped_to_icao(tmp_path):
    conn = _conn(tmp_path)
    for i in range(2):
        _seed(conn, "low_approach", "aa11", 1780000000, f"a-{i}")
    for i in range(2):
        _seed(conn, "low_approach", "bb22", 1780000000, f"b-{i}")
    conn.commit()
    deleted, _ = dedup_runway_contacts(conn, icao24="aa11")
    assert deleted == 1
    assert conn.execute("SELECT COUNT(*) FROM operations WHERE type='low_approach'").fetchone()[0] == 3
