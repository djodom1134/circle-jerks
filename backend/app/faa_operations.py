"""The FAA-operation weighting: the single source of truth mapping our detector
event types to the canonical FAA sense of an *operation* — one takeoff or one
landing.

    takeoff        -> 1   (one departure)
    landing        -> 1   (one arrival)
    touch_and_go   -> 2   (one arrival + one departure)
    low_approach   -> 2 if it was a real touchdown (min AGL <= threshold) else 0
    circle         -> 0   (a pattern lap, not a runway movement)
    pass_over_user -> 0   (not an airport movement)

A Python path (per record, in v1_schemas.operation_out) and a SQL path (the
aggregate in db.faa_operations_summary) both derive from the constants here, so
the two cannot drift. test_faa_operations.py asserts they agree.
"""

from __future__ import annotations

# From the KLMO audit: a low approach whose lowest point was <= this AGL is a
# real touchdown (a touch-and-go worth 2 operations); a higher low pass / go-
# around completes neither a takeoff nor a landing and is worth 0.
LOW_APPROACH_TOUCHDOWN_MAX_AGL_FT = 25


def is_low_approach_touchdown(min_altitude_ft_agl: int | None) -> bool:
    return (
        min_altitude_ft_agl is not None
        and min_altitude_ft_agl <= LOW_APPROACH_TOUCHDOWN_MAX_AGL_FT
    )


def faa_arrivals(op_type: str, min_altitude_ft_agl: int | None) -> int:
    if op_type in ("landing", "touch_and_go"):
        return 1
    if op_type == "low_approach":
        return 1 if is_low_approach_touchdown(min_altitude_ft_agl) else 0
    return 0


def faa_departures(op_type: str, min_altitude_ft_agl: int | None) -> int:
    if op_type in ("takeoff", "touch_and_go"):
        return 1
    if op_type == "low_approach":
        return 1 if is_low_approach_touchdown(min_altitude_ft_agl) else 0
    return 0


def faa_operation_count(op_type: str, min_altitude_ft_agl: int | None) -> int:
    """FAA operations a single detected event represents (0, 1, or 2)."""
    return (
        faa_arrivals(op_type, min_altitude_ft_agl)
        + faa_departures(op_type, min_altitude_ft_agl)
    )


# --- SQL fragments, built from the same constant -----------------------------
#
# Each references the bare columns `type` and `min_altitude_ft_agl`, so it drops
# into a SELECT over either `operations` or `hist.operations` unchanged. The
# threshold is an int, so interpolation is injection-safe.

_TOUCHDOWN_SQL = (
    "min_altitude_ft_agl IS NOT NULL "
    f"AND min_altitude_ft_agl <= {LOW_APPROACH_TOUCHDOWN_MAX_AGL_FT}"
)

FAA_ARRIVALS_SUM_SQL = (
    "SUM(CASE "
    "WHEN type IN ('landing','touch_and_go') THEN 1 "
    f"WHEN type='low_approach' AND {_TOUCHDOWN_SQL} THEN 1 "
    "ELSE 0 END)"
)

FAA_DEPARTURES_SUM_SQL = (
    "SUM(CASE "
    "WHEN type IN ('takeoff','touch_and_go') THEN 1 "
    f"WHEN type='low_approach' AND {_TOUCHDOWN_SQL} THEN 1 "
    "ELSE 0 END)"
)

FAA_OPS_SUM_SQL = (
    "SUM(CASE "
    "WHEN type IN ('takeoff','landing') THEN 1 "
    "WHEN type='touch_and_go' THEN 2 "
    f"WHEN type='low_approach' AND {_TOUCHDOWN_SQL} THEN 2 "
    "ELSE 0 END)"
)

LOW_APPROACH_TOUCHDOWN_COUNT_SQL = (
    f"SUM(CASE WHEN type='low_approach' AND {_TOUCHDOWN_SQL} THEN 1 ELSE 0 END)"
)
