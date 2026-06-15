from __future__ import annotations

from statistics import fmean

from .geo import Point, closest_segment_approach_nm

CORRIDOR_NM = 0.25


def densify_pattern(geometry: dict, steps: int = 12) -> list[Point]:
    """Catmull-Rom densify a pattern's control points into a polyline of Points.
    Control points are interpolated in (lon, lat) space, fine at pattern scale."""
    pts = [(float(p["lon"]), float(p["lat"])) for p in geometry.get("points", [])]
    if geometry.get("closed") and len(pts) > 2:
        pts = pts + [pts[0]]
    if len(pts) < 3:
        return [Point(lat, lon) for lon, lat in pts]
    out: list[tuple[float, float]] = [pts[0]]
    for i in range(len(pts) - 1):
        p0 = pts[i - 1] if i - 1 >= 0 else pts[i]
        p1 = pts[i]
        p2 = pts[i + 1]
        p3 = pts[i + 2] if i + 2 < len(pts) else pts[i + 1]
        for s in range(1, steps + 1):
            t = s / steps
            t2 = t * t
            t3 = t2 * t
            x = 0.5 * (2 * p1[0] + (-p0[0] + p2[0]) * t + (2 * p0[0] - 5 * p1[0] + 4 * p2[0] - p3[0]) * t2 + (-p0[0] + 3 * p1[0] - 3 * p2[0] + p3[0]) * t3)
            y = 0.5 * (2 * p1[1] + (-p0[1] + p2[1]) * t + (2 * p0[1] - 5 * p1[1] + 4 * p2[1] - p3[1]) * t2 + (-p0[1] + 3 * p1[1] - 3 * p2[1] + p3[1]) * t3)
            out.append((x, y))
    return [Point(lat, lon) for lon, lat in out]


def perpendicular_distance_nm(point: Point, polyline: list[Point]) -> float:
    """Minimum distance (nm) from `point` to any segment of `polyline`."""
    best = float("inf")
    for a, b in zip(polyline, polyline[1:]):
        d, _ = closest_segment_approach_nm(a, b, point)
        if d < best:
            best = d
    return best
