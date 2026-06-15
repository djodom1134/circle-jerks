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


def test_save_and_get_current_pattern_versions(tmp_path):
    conn = seeded_conn(tmp_path / "t.sqlite3")
    g1 = json.dumps({"points": [{"lat": 39.92, "lon": -105.13}], "closed": True, "spline": "catmull-rom"})
    g2 = json.dumps({"points": [{"lat": 39.93, "lon": -105.14}], "closed": True, "spline": "catmull-rom"})

    saved1 = db.save_runway_pattern(conn, "KBJC", "12L", g1, name="v1", editor_visitor_id="visitor-1", editor_ip="1.2.3.4")
    assert saved1["version"] == 1
    assert saved1["is_current"] == 1

    saved2 = db.save_runway_pattern(conn, "KBJC", "12L", g2, name="v2", editor_visitor_id="visitor-1", editor_ip="1.2.3.4")
    assert saved2["version"] == 2

    current = db.get_current_pattern(conn, "KBJC", "12L")
    assert current["version"] == 2
    assert json.loads(current["geometry_json"])["points"][0]["lat"] == 39.93

    # exactly one current row per runway end
    n_current = conn.execute(
        "SELECT COUNT(*) AS c FROM runway_patterns WHERE icao='KBJC' AND runway_id='12L' AND is_current=1"
    ).fetchone()["c"]
    assert n_current == 1

    # a different runway end is independent
    assert db.get_current_pattern(conn, "KBJC", "30R") is None

    db.save_runway_pattern(conn, "KBJC", "30R", g1)
    listed = db.current_patterns_for_airport(conn, "KBJC")
    assert {p["runway_id"] for p in listed} == {"12L", "30R"}


def test_pattern_history_revert_and_rate_count(tmp_path):
    conn = seeded_conn(tmp_path / "t.sqlite3")
    g1 = json.dumps({"points": [{"lat": 39.92, "lon": -105.13}], "closed": True, "spline": "catmull-rom"})
    g2 = json.dumps({"points": [{"lat": 39.93, "lon": -105.14}], "closed": True, "spline": "catmull-rom"})
    db.save_runway_pattern(conn, "KBJC", "12L", g1, name="v1", editor_visitor_id="visitor-1", editor_ip="1.1.1.1")
    db.save_runway_pattern(conn, "KBJC", "12L", g2, name="v2", editor_visitor_id="visitor-1", editor_ip="1.1.1.1")

    versions = db.list_pattern_versions(conn, "KBJC", "12L")
    assert [v["version"] for v in versions] == [2, 1]  # newest first

    reverted = db.revert_pattern(conn, "KBJC", "12L", 1, editor_visitor_id="visitor-2", editor_ip="2.2.2.2")
    assert reverted["version"] == 3
    assert json.loads(reverted["geometry_json"])["points"][0]["lat"] == 39.92  # v1 geometry
    assert "revert to v1" in reverted["change_note"]
    assert db.revert_pattern(conn, "KBJC", "12L", 99) is None  # missing version

    # rate count: 3 edits by visitor-1/1.1.1.1 so far (v1, v2, and revert used visitor-2)
    count_v1 = db.count_recent_pattern_edits(conn, "visitor-1", "1.1.1.1", 0)
    assert count_v1 == 2
    count_v2 = db.count_recent_pattern_edits(conn, "visitor-2", "2.2.2.2", 0)
    assert count_v2 == 1
    # window excludes old edits
    assert db.count_recent_pattern_edits(conn, "visitor-1", "1.1.1.1", 9_999_999_999) == 0


def test_pattern_read_endpoints(tmp_path, monkeypatch):
    with api_client(tmp_path, monkeypatch) as client:
        # template for a real runway
        resp = client.get("/runways/KBJC/12L/pattern/template", params={"side": "left"})
        assert resp.status_code == 200
        geom = resp.json()["geometry"]
        assert len(geom["points"]) == 5 and geom["closed"] is True

        # template for unknown runway → 404
        assert client.get("/runways/KBJC/ZZ/pattern/template").status_code == 404

        # no pattern yet
        resp = client.get("/runways/KBJC/12L/pattern")
        assert resp.status_code == 200 and resp.json()["pattern"] is None

        # airport patterns list is empty
        resp = client.get("/airports/KBJC/patterns")
        assert resp.status_code == 200 and resp.json()["patterns"] == []
