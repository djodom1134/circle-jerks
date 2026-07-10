"""Historical de-dup of the circle over-count.

Fixed going forward in app/detectors.py (circle id anchored on the runway-pass,
not the drifting lap-end). This script collapses the rows already stored under
the old smeared id. The tracks that produced them are largely gone from the
archive, so we can't re-derive — but each circle op carries deviation_peak_nm,
computed from the lap's own samples, which is effectively a per-lap fingerprint:
every re-detection of one physical lap shares it. Rows with the same
(icao, deviation_peak_nm) that fall within one lap window are the same lap.
"""
from __future__ import annotations

from app import db
from scripts.dedup_lap_circles import dedup_lap_circles


def _seed(conn, icao24, peak, ts):
    db.upsert_operation(conn, db.operation_from_event({
        "id": f"{icao24}-{ts}-{peak}", "type": "circle", "icao24": icao24,
        "callsign": icao24.upper(), "timestamp": ts, "airport_icao": "KLMO",
        "turn_direction": "left",
    }))
    conn.execute("UPDATE operations SET deviation_peak_nm=? WHERE id=?",
                 (peak, f"{icao24}-{ts}-{peak}"))


def _conn(tmp_path):
    c = db.connect(str(tmp_path / "d.sqlite3"))
    c.executescript(db.SCHEMA); db.seed_db(c); c.commit()
    return c


def test_collapses_same_lap_redetections(tmp_path):
    conn = _conn(tmp_path)
    # One physical lap: 4 rows, same peak, within ~200s (re-detections).
    for dt in (0, 90, 140, 190):
        _seed(conn, "aa11", 1.748, 1780000000 + dt)
    # A second, genuinely different lap ~400s later: different peak.
    _seed(conn, "aa11", 0.912, 1780000000 + 420)
    conn.commit()

    deleted, remaining = dedup_lap_circles(conn)
    assert deleted == 3
    assert remaining == 2  # one row per real lap
    # The earliest row of each lap survives.
    ids = {r[0] for r in conn.execute("SELECT id FROM operations WHERE type='circle'")}
    assert "aa11-1780000000-1.748" in ids
    assert "aa11-1780000420-0.912" in ids


def test_same_peak_far_apart_is_two_laps(tmp_path):
    """A peak value can recur hours later on a genuinely separate lap; the lap
    window guard must keep both."""
    conn = _conn(tmp_path)
    _seed(conn, "bb22", 1.5, 1780000000)
    _seed(conn, "bb22", 1.5, 1780000000 + 3 * 3600)   # 3h later — different lap
    conn.commit()
    deleted, remaining = dedup_lap_circles(conn)
    assert deleted == 0 and remaining == 2


def test_leaves_null_peak_and_other_types_untouched(tmp_path):
    conn = _conn(tmp_path)
    # NULL-peak circles can't be fingerprinted — must be left alone.
    db.upsert_operation(conn, db.operation_from_event({
        "id": "np1", "type": "circle", "icao24": "cc33", "callsign": "N",
        "timestamp": 1780000000, "airport_icao": "KLMO", "turn_direction": "left"}))
    db.upsert_operation(conn, db.operation_from_event({
        "id": "np2", "type": "circle", "icao24": "cc33", "callsign": "N",
        "timestamp": 1780000030, "airport_icao": "KLMO", "turn_direction": "left"}))
    # A touch-and-go with a coincidental duplicate — not a circle, never touched.
    for dt in (0, 20):
        db.upsert_operation(conn, db.operation_from_event({
            "id": f"tg{dt}", "type": "touch_and_go", "icao24": "cc33", "callsign": "N",
            "timestamp": 1780000000 + dt, "airport_icao": "KLMO", "runway_id": "29"}))
    conn.commit()
    deleted, remaining = dedup_lap_circles(conn)
    assert deleted == 0
    assert conn.execute("SELECT COUNT(*) FROM operations WHERE type='touch_and_go'").fetchone()[0] == 2
    assert conn.execute("SELECT COUNT(*) FROM operations WHERE type='circle'").fetchone()[0] == 2


def test_scoped_to_icao(tmp_path):
    conn = _conn(tmp_path)
    for dt in (0, 100):
        _seed(conn, "aa11", 1.0, 1780000000 + dt)
        _seed(conn, "dd44", 1.0, 1780000000 + dt)
    conn.commit()
    deleted, _ = dedup_lap_circles(conn, icao24="aa11")
    assert deleted == 1
    assert conn.execute("SELECT COUNT(*) FROM operations WHERE icao24='dd44'").fetchone()[0] == 2
