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
