from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from app import db, patterns
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


def test_generate_template_pattern_within_bounds(tmp_path):
    conn = seeded_conn(tmp_path / "t.sqlite3")
    runway = db.get_runway(conn, "KBJC", "12L")
    geom = patterns.generate_template_pattern(runway, side="left")
    assert geom["closed"] is True
    assert geom["spline"] == "catmull-rom"
    assert len(geom["points"]) == 5
    center = Point(39.9088, -105.1172)
    assert all(distance_nm(Point(p["lat"], p["lon"]), center) < 10 for p in geom["points"])
    # first control point is the runway threshold
    assert abs(geom["points"][0]["lat"] - runway["lat_threshold"]) < 1e-9
    assert abs(geom["points"][0]["lon"] - runway["lon_threshold"]) < 1e-9


def test_generate_template_rejects_bad_side(tmp_path):
    conn = seeded_conn(tmp_path / "t.sqlite3")
    runway = db.get_runway(conn, "KBJC", "12L")
    with pytest.raises(ValueError):
        patterns.generate_template_pattern(runway, side="sideways")


def test_validate_pattern_geometry():
    class _AP:
        lat = 39.9088
        lon = -105.1172
    ap = _AP()
    assert patterns.validate_pattern_geometry([{"lat": 39.91, "lon": -105.12}], ap) is None
    assert patterns.validate_pattern_geometry([], ap) is not None
    too_far = [{"lat": 41.5, "lon": -105.12}]  # ~90 nm north
    assert patterns.validate_pattern_geometry(too_far, ap) is not None
    too_many = [{"lat": 39.91, "lon": -105.12}] * 51
    assert patterns.validate_pattern_geometry(too_many, ap) is not None
