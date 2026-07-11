from __future__ import annotations

from app import db


def seeded_conn(path):
    conn = db.connect(str(path))
    conn.executescript(db.SCHEMA)
    db.seed_db(conn)
    conn.commit()
    return conn


def test_nearest_airport_excluding_skips_source(tmp_path):
    conn = seeded_conn(tmp_path / "t.sqlite3")
    klmo = db.get_airport(conn, "KLMO")
    out = db.nearest_airport_excluding(conn, klmo.lat, klmo.lon, "KLMO")
    assert out is not None
    assert out["icao"] != "KLMO"
    assert "distance_nm" in out


def test_report_meta_returns_counts_and_last_seen(tmp_path):
    conn = seeded_conn(tmp_path / "t.sqlite3")
    conn.execute(
        "INSERT INTO aircraft_report_counts (icao24, callsign, registration, report_count, first_reported_at, last_reported_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        ("a5a764", "N4632F", "N4632F", 5, 1780000000, 1780009999),
    )
    conn.commit()
    meta = db.report_meta(conn, ["a5a764", "ffffff"])
    assert meta["a5a764"]["report_count"] == 5
    assert meta["a5a764"]["last_reported_at"] == 1780009999
    assert "ffffff" not in meta


def test_report_meta_empty_list(tmp_path):
    conn = seeded_conn(tmp_path / "t.sqlite3")
    assert db.report_meta(conn, []) == {}
