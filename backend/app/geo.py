from __future__ import annotations

import math
from dataclasses import dataclass

EARTH_RADIUS_NM = 3440.065
METER_TO_FT = 3.280839895
MPS_TO_KT = 1.9438444924
MPS_TO_FPM = 196.850394


@dataclass(frozen=True)
class Point:
    lat: float
    lon: float


def meters_to_feet(value: float | None) -> float | None:
    return None if value is None else value * METER_TO_FT


def mps_to_knots(value: float | None) -> float | None:
    return None if value is None else value * MPS_TO_KT


def mps_to_fpm(value: float | None) -> float | None:
    return None if value is None else value * MPS_TO_FPM


def distance_nm(a: Point, b: Point) -> float:
    lat1 = math.radians(a.lat)
    lat2 = math.radians(b.lat)
    d_lat = math.radians(b.lat - a.lat)
    d_lon = math.radians(b.lon - a.lon)
    h = (
        math.sin(d_lat / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin(d_lon / 2) ** 2
    )
    return 2 * EARTH_RADIUS_NM * math.asin(min(1, math.sqrt(h)))


def bearing_deg(a: Point, b: Point) -> float:
    lat1 = math.radians(a.lat)
    lat2 = math.radians(b.lat)
    d_lon = math.radians(b.lon - a.lon)
    y = math.sin(d_lon) * math.cos(lat2)
    x = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(d_lon)
    return (math.degrees(math.atan2(y, x)) + 360) % 360


def destination_point(origin: Point, bearing_deg: float, distance_nm: float) -> Point:
    """Return the point reached by travelling `distance_nm` from `origin` along
    a constant `bearing_deg` (great-circle). Inverse of bearing_deg/distance_nm."""
    angular = distance_nm / EARTH_RADIUS_NM
    bearing = math.radians(bearing_deg)
    lat1 = math.radians(origin.lat)
    lon1 = math.radians(origin.lon)
    lat2 = math.asin(
        math.sin(lat1) * math.cos(angular)
        + math.cos(lat1) * math.sin(angular) * math.cos(bearing)
    )
    lon2 = lon1 + math.atan2(
        math.sin(bearing) * math.sin(angular) * math.cos(lat1),
        math.cos(angular) - math.sin(lat1) * math.sin(lat2),
    )
    return Point(math.degrees(lat2), math.degrees(lon2))


def heading_delta_deg(previous: float, current: float) -> float:
    return (current - previous + 540) % 360 - 180


def bbox_for_radius(lat: float, lon: float, radius_nm: float) -> tuple[float, float, float, float]:
    lat_delta = radius_nm / 60.0
    lon_scale = max(0.2, math.cos(math.radians(lat)))
    lon_delta = radius_nm / (60.0 * lon_scale)
    return (lat - lat_delta, lon - lon_delta, lat + lat_delta, lon + lon_delta)


def bbox_union(*boxes: tuple[float, float, float, float]) -> tuple[float, float, float, float]:
    return (
        min(box[0] for box in boxes),
        min(box[1] for box in boxes),
        max(box[2] for box in boxes),
        max(box[3] for box in boxes),
    )


def sq_degrees(box: tuple[float, float, float, float]) -> float:
    return abs((box[2] - box[0]) * (box[3] - box[1]))


def _local_xy_nm(point: Point, origin: Point) -> tuple[float, float]:
    lat_scale = 60.0
    lon_scale = 60.0 * max(0.2, math.cos(math.radians(origin.lat)))
    return ((point.lon - origin.lon) * lon_scale, (point.lat - origin.lat) * lat_scale)


def closest_segment_approach_nm(a: Point, b: Point, target: Point) -> tuple[float, float]:
    ax, ay = _local_xy_nm(a, target)
    bx, by = _local_xy_nm(b, target)
    dx = bx - ax
    dy = by - ay
    length_sq = dx * dx + dy * dy
    if length_sq == 0:
        return (math.hypot(ax, ay), 0.0)
    t = max(0.0, min(1.0, -((ax * dx + ay * dy) / length_sq)))
    closest_x = ax + t * dx
    closest_y = ay + t * dy
    return (math.hypot(closest_x, closest_y), t)
