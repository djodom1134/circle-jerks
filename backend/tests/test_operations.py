from __future__ import annotations

import pytest

from app import db
from app.db import Airport
from app.detectors import detect_events
from app.domain import ScanParams


def seeded_conn(path):
    conn = db.connect(str(path))
    conn.executescript(db.SCHEMA)
    db.seed_db(conn)
    conn.commit()
    return conn


def airport_kbjc() -> Airport:
    return Airport(
        icao="KBJC",
        iata="BJC",
        name="Rocky Mountain Metropolitan Airport",
        city="Broomfield, CO",
        country="US",
        lat=39.9088,
        lon=-105.1172,
        elevation_ft=5673,
        is_towered=True,
    )


def sample(ts: int, lat: float, lon: float, alt_agl: int = 900, icao24: str = "abc123") -> dict:
    return {
        "icao24": icao24,
        "callsign": "N123AB",
        "timestamp": ts,
        "lat": lat,
        "lon": lon,
        "geo_altitude_ft": airport_kbjc().elevation_ft + alt_agl,
        "velocity_kt": 80,
        "vertical_rate_fpm": 0,
        "on_ground": False,
    }


def closed_loop_track() -> list[dict]:
    points = [
        (39.9238, -105.1172),
        (39.9194, -105.1013),
        (39.9088, -105.0950),
        (39.8982, -105.1013),
        (39.8938, -105.1172),
        (39.8982, -105.1331),
        (39.9088, -105.1394),
        (39.9194, -105.1331),
        (39.9238, -105.1172),
    ]
    return [sample(1000 + i * 30, lat, lon) for i, (lat, lon) in enumerate(points)]


def test_operations_table_exists(tmp_path):
    conn = seeded_conn(tmp_path / "t.sqlite3")
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='operations'"
    ).fetchone()
    assert row is not None
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(operations)").fetchall()}
    assert {"id", "icao", "icao24", "type", "timestamp", "deviation_mean_nm",
            "wind_from_deg", "origin_airport_icao", "flight_school"} <= cols


def test_operation_from_event_maps_circle_fields():
    event = {
        "id": "deadbeefcafe00000001",
        "type": "circle",
        "icao24": "a4c1d8",
        "callsign": "N4052F",
        "timestamp": 1718464800,
        "airport_icao": "KBJC",
        "turn_direction": "left",
    }
    op = db.operation_from_event(event)
    assert op["id"] == "deadbeefcafe00000001"
    assert op["icao"] == "KBJC"
    assert op["icao24"] == "a4c1d8"
    assert op["callsign"] == "N4052F"
    assert op["type"] == "circle"
    assert op["timestamp"] == 1718464800
    assert op["turn_direction"] == "left"
    # runway/altitude absent on this circle event → None
    assert op["runway_id"] is None
    assert op["min_altitude_ft_agl"] is None


def test_operation_from_event_maps_touch_and_go_fields():
    event = {
        "id": "deadbeefcafe00000002",
        "type": "touch_and_go",
        "icao24": "a4c1d8",
        "callsign": "N4052F",
        "timestamp": 1718464900,
        "airport_icao": "KLMO",
        "runway_id": "29",
        "runway_heading_deg": 290,
        "min_altitude_ft_agl": 35,
    }
    op = db.operation_from_event(event)
    assert op["type"] == "touch_and_go"
    assert op["runway_id"] == "29"
    assert op["runway_heading_deg"] == 290
    assert op["min_altitude_ft_agl"] == 35
    assert op["turn_direction"] is None


def test_upsert_operation_is_idempotent(tmp_path):
    conn = seeded_conn(tmp_path / "t.sqlite3")
    op = db.operation_from_event({
        "id": "dup00000000000000001",
        "type": "circle",
        "icao24": "a4c1d8",
        "callsign": "N4052F",
        "timestamp": 1718464800,
        "airport_icao": "KBJC",
        "turn_direction": "left",
    })
    db.upsert_operation(conn, op)
    db.upsert_operation(conn, op)  # second insert of same id must not duplicate
    conn.commit()
    rows = conn.execute("SELECT * FROM operations WHERE id = ?", (op["id"],)).fetchall()
    assert len(rows) == 1
    assert rows[0]["icao"] == "KBJC"
    assert rows[0]["type"] == "circle"
    assert rows[0]["turn_direction"] == "left"
    assert rows[0]["created_at"] is not None
    # enrichment columns default to NULL until later phases populate them
    assert rows[0]["deviation_mean_nm"] is None
