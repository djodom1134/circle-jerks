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
