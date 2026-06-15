from __future__ import annotations

from app import deviation
from app.geo import Point, distance_nm


def test_densify_pattern_interpolates_and_closes():
    geom = {"points": [{"lat": 0.0, "lon": 0.0}, {"lat": 0.0, "lon": 0.1},
                       {"lat": 0.1, "lon": 0.1}, {"lat": 0.1, "lon": 0.0}],
            "closed": True, "spline": "catmull-rom"}
    poly = deviation.densify_pattern(geom, steps=8)
    assert len(poly) > 4
    assert all(isinstance(p, Point) for p in poly)


def test_perpendicular_distance_zero_on_segment():
    line = [Point(0.0, 0.0), Point(0.0, 0.1)]  # runs east along the equator
    on = Point(0.0, 0.05)
    off = Point(0.02, 0.05)  # ~1.2 nm north of the line
    assert deviation.perpendicular_distance_nm(on, line) < 0.05
    assert deviation.perpendicular_distance_nm(off, line) > 0.5


def _samples(coords, dt=30):
    # coords: list of (lat, lon); evenly spaced dt seconds apart
    return [{"timestamp": 1000 + i * dt, "lat": lat, "lon": lon} for i, (lat, lon) in enumerate(coords)]


def test_compute_deviation_on_pattern_is_low():
    line = [Point(0.0, 0.0), Point(0.0, 0.2)]
    samples = _samples([(0.0, 0.02 * i) for i in range(6)])  # walking along the line
    metrics = deviation.compute_deviation(samples, line)
    assert metrics["deviation_mean_nm"] < 0.05
    assert metrics["pct_off_pattern"] == 0.0
    assert metrics["time_total_s"] == 5 * 30


def test_compute_deviation_off_pattern_is_high():
    line = [Point(0.0, 0.0), Point(0.0, 0.2)]
    samples = _samples([(0.05, 0.02 * i) for i in range(6)])  # ~3 nm north of the line
    metrics = deviation.compute_deviation(samples, line)
    assert metrics["deviation_mean_nm"] > 1.0
    assert metrics["pct_off_pattern"] == 1.0
    assert metrics["deviation_peak_nm"] >= metrics["deviation_mean_nm"]


def test_compute_deviation_needs_two_samples():
    line = [Point(0.0, 0.0), Point(0.0, 0.2)]
    assert deviation.compute_deviation(_samples([(0.0, 0.0)]), line) is None


def test_match_pattern_picks_nearest_same_sense():
    # Aircraft flies a small CCW square near (0,0).
    track = _samples([(0.0, 0.0), (0.0, 0.02), (0.02, 0.02), (0.02, 0.0), (0.0, 0.0)])

    near_ccw = {"id": 1, "geometry": {"points": [
        {"lat": 0.0, "lon": 0.0}, {"lat": 0.0, "lon": 0.02},
        {"lat": 0.02, "lon": 0.02}, {"lat": 0.02, "lon": 0.0}], "closed": True}}
    far_ccw = {"id": 2, "geometry": {"points": [
        {"lat": 1.0, "lon": 1.0}, {"lat": 1.0, "lon": 1.02},
        {"lat": 1.02, "lon": 1.02}, {"lat": 1.02, "lon": 1.0}], "closed": True}}

    assert deviation.match_pattern(track, [near_ccw, far_ccw])["id"] == 1
    assert deviation.match_pattern(track, []) is None


def test_update_operation_deviation(tmp_path):
    from app import db
    conn = db.connect(str(tmp_path / "t.sqlite3"))
    conn.executescript(db.SCHEMA)
    db.seed_db(conn)
    conn.commit()
    db.upsert_operation(conn, db.operation_from_event({
        "id": "dev1", "type": "circle", "icao24": "a", "callsign": "N1",
        "timestamp": 1000, "airport_icao": "KBJC",
    }))
    db.update_operation_deviation(conn, "dev1", 7, {
        "deviation_mean_nm": 0.42, "deviation_peak_nm": 0.9,
        "time_off_pattern_s": 60, "time_total_s": 240, "pct_off_pattern": 0.25,
    })
    conn.commit()
    row = db.read_operations(conn, "KBJC", 0, 10000)[0]
    assert row["matched_pattern_id"] == 7
    assert row["deviation_mean_nm"] == 0.42
    assert row["pct_off_pattern"] == 0.25
    assert row["time_total_s"] == 240


def test_store_deviations_end_to_end(tmp_path):
    import json
    from app import db
    from app.detectors import detect_events
    from app.domain import ScanParams
    # local fixtures (same as test_operations.py)
    from app.db import Airport

    def ap():
        return Airport(icao="KBJC", iata="BJC", name="x", city="x", country="US",
                       lat=39.9088, lon=-105.1172, elevation_ft=5673, is_towered=True)

    def sample(ts, lat, lon, alt_agl=900):
        return {"icao24": "abc123", "callsign": "N1", "timestamp": ts, "lat": lat, "lon": lon,
                "geo_altitude_ft": 5673 + alt_agl, "velocity_kt": 80, "vertical_rate_fpm": 0, "on_ground": False}

    pts = [(39.9238, -105.1172), (39.9194, -105.1013), (39.9088, -105.0950), (39.8982, -105.1013),
           (39.8938, -105.1172), (39.8982, -105.1331), (39.9088, -105.1394), (39.9194, -105.1331), (39.9238, -105.1172)]
    track = [sample(1000 + i * 30, lat, lon) for i, (lat, lon) in enumerate(pts)]

    conn = db.connect(str(tmp_path / "t.sqlite3"))
    conn.executescript(db.SCHEMA); db.seed_db(conn); conn.commit()

    # Save a pattern that IS the flown loop → deviation should be small.
    geom = json.dumps({"points": [{"lat": lat, "lon": lon} for lat, lon in pts], "closed": True, "spline": "catmull-rom"})
    saved = db.save_runway_pattern(conn, "KBJC", "12L", geom, name="loop")

    runways = db.runways_for_airport(conn, "KBJC")
    events = detect_events(track, ap(), runways, ScanParams(airport_icao="KBJC", user_lat=40.0, user_lon=-105.2))
    db.persist_events(conn, events)
    deviation.store_deviations(conn, "KBJC", events, {"abc123": track})
    conn.commit()

    rows = [r for r in db.read_operations(conn, "KBJC", 0, 10000) if r["type"] == "circle"]
    assert rows, "expected a persisted circle op"
    op = rows[0]
    assert op["matched_pattern_id"] == saved["id"]
    assert op["deviation_mean_nm"] is not None
    assert 0.0 <= op["pct_off_pattern"] <= 1.0
    assert op["deviation_mean_nm"] < 0.5  # track ≈ pattern


def test_services_imports_and_uses_store_deviations():
    import inspect
    from app import services
    src = inspect.getsource(services.run_detectors_for_monitor)
    assert "store_deviations" in src, "run_detectors_for_monitor must call deviation.store_deviations"
    # the module must import the deviation module
    assert hasattr(services, "deviation") or "from . import" in inspect.getsource(services)
