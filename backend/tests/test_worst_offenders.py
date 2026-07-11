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
