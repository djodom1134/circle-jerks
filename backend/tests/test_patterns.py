from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from app import db
from app.geo import Point, distance_nm, destination_point
from app.main import app
from app.settings import get_settings


def seeded_conn(path):
    conn = db.connect(str(path))
    conn.executescript(db.SCHEMA)
    db.seed_db(conn)
    conn.commit()
    return conn


def api_client(tmp_path, monkeypatch):
    monkeypatch.setenv("CIRCLEJERK_DATABASE_PATH", str(tmp_path / "circlejerk.sqlite3"))
    monkeypatch.setenv("CIRCLEJERK_REDIS_URL", "memory://")
    monkeypatch.setenv("CIRCLEJERK_ENVIRONMENT", "test")
    get_settings.cache_clear()
    return TestClient(app)


def test_destination_point_projects_by_bearing_and_distance():
    start = Point(40.0, -105.0)
    north = destination_point(start, 0.0, 60.0)      # 60 nm due north ≈ +1° lat
    assert north.lat > start.lat
    assert abs(distance_nm(start, north) - 60.0) < 0.5
    assert abs(north.lon - start.lon) < 0.05         # due north → longitude ~unchanged

    east = destination_point(start, 90.0, 30.0)
    assert east.lon > start.lon
    assert abs(distance_nm(start, east) - 30.0) < 0.5


def test_runway_patterns_table_and_get_runway(tmp_path):
    conn = seeded_conn(tmp_path / "t.sqlite3")
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(runway_patterns)").fetchall()}
    assert {"id", "icao", "runway_id", "geometry_json", "version",
            "is_current", "locked", "editor_visitor_id", "change_note", "created_at"} <= cols

    rwy = db.get_runway(conn, "KBJC", "12L")
    assert rwy is not None
    assert rwy["heading_deg"] == 120
    assert rwy["length_ft"] == 9000
    assert db.get_runway(conn, "KBJC", "ZZ") is None
