from __future__ import annotations

from .geo import Point, destination_point, distance_nm

PATTERN_SPLINE = "catmull-rom"

# Standard traffic-circuit geometry (editable starting shape, not exact TPP).
DEFAULT_PATTERN_WIDTH_NM = 0.7     # downwind offset from the runway centerline
DEFAULT_FINAL_NM = 1.0             # final-approach length beyond the threshold
FEET_PER_NM = 6076.12

MAX_PATTERN_POINTS = 50
MAX_PATTERN_RADIUS_NM = 10.0


def generate_template_pattern(runway: dict, side: str = "left") -> dict:
    """Build a standard rectangular circuit for one runway end as a closed
    control-point spline. `side` is the traffic direction ('left' or 'right')."""
    if side not in ("left", "right"):
        raise ValueError("side must be 'left' or 'right'")
    heading = float(runway["heading_deg"])
    length_nm = max(0.3, float(runway["length_ft"]) / FEET_PER_NM)
    width = DEFAULT_PATTERN_WIDTH_NM
    sign = -1 if side == "left" else 1
    side_bearing = (heading + sign * 90) % 360
    threshold = Point(float(runway["lat_threshold"]), float(runway["lon_threshold"]))

    upwind = destination_point(threshold, heading, length_nm)
    crosswind = destination_point(upwind, side_bearing, width)
    downwind_end = destination_point(crosswind, (heading + 180) % 360, length_nm + DEFAULT_FINAL_NM)
    final_pt = destination_point(downwind_end, (side_bearing + 180) % 360, width)

    points = [
        {"lat": threshold.lat, "lon": threshold.lon},
        {"lat": upwind.lat, "lon": upwind.lon},
        {"lat": crosswind.lat, "lon": crosswind.lon},
        {"lat": downwind_end.lat, "lon": downwind_end.lon},
        {"lat": final_pt.lat, "lon": final_pt.lon},
    ]
    return {"points": points, "closed": True, "spline": PATTERN_SPLINE}


def validate_pattern_geometry(
    points: list[dict],
    airport,
    max_points: int = MAX_PATTERN_POINTS,
    max_nm: float = MAX_PATTERN_RADIUS_NM,
) -> str | None:
    """Return an error message if the geometry is invalid, else None.
    `airport` must expose `.lat` and `.lon`."""
    if not points:
        return "pattern must have at least one point"
    if len(points) > max_points:
        return f"pattern has too many points (max {max_points})"
    center = Point(airport.lat, airport.lon)
    for index, point in enumerate(points):
        if distance_nm(Point(point["lat"], point["lon"]), center) > max_nm:
            return f"point {index} is more than {max_nm} nm from the airport"
    return None
