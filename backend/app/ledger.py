"""The Lost Landing ledger: runway-use vocabulary, rollups, and operator aggregation.

The BILLABLE UNIT is a "runway use", not an FAA "operation". An FAA operation is
one takeoff OR one landing, so a touch-and-go is two operations — multiplying a
fee by an operations count double-counts every touch-and-go. A runway use is a
single arrival at the runway: a landing, a touch-and-go, or a low approach.

We deliberately do NOT denominate money in "touch-and-gos". The touch-and-go
detector is geometric (a pattern circle passing within 0.25 nm of the runway
segment) and does not verify a touchdown — see detectors.py:41-44. A claim of the
form "N touch-and-gos x $F" is refutable on that basis. "This aircraft used the
runway N times" is true exactly as detected, and is the larger number besides.
"""
from __future__ import annotations

# The billable unit. Every one of these is an arrival at the runway.
RUNWAY_USE_TYPES: tuple[str, ...] = ("landing", "touch_and_go", "low_approach")

# What the daily rollup stores. Includes takeoff, which is NOT billable but is
# needed to pair landings into dwell intervals (dwell.py) and to report the
# conventional FAA operations count alongside ours.
ROLLUP_TYPES: tuple[str, ...] = ("landing", "takeoff", "touch_and_go", "low_approach")

# Published verbatim on the site's methodology page. If a detector changes, this
# changes in the same commit. These strings are the site's factual claims about
# its own method; they are not decorative.
RUNWAY_USE_DEFINITIONS: dict[str, str] = {
    "landing": (
        "The aircraft descended from at least 500 ft above the field, came within "
        "1.5 nm of the runway at 200 ft AGL or below, and did not climb back out "
        "within 300 seconds."
    ),
    "touch_and_go": (
        "The aircraft flew a closed pattern circuit whose track passed within 0.25 nm "
        "of the runway. This is a geometric test: it does not confirm that the wheels "
        "touched the pavement. We count it as a use of the runway, not as a verified "
        "touchdown."
    ),
    "low_approach": (
        "The aircraft approached from at least 500 ft above the field, came within "
        "1.5 nm of the runway at 200 ft AGL or below at 90 knots or less, spent no "
        "more than 60 seconds on the ground, and climbed back out."
    ),
}

FLOOR_DISCLAIMER = (
    "Every count on this page is a floor, not an estimate. Aircraft without ADS-B Out "
    "are invisible to us, so real activity is higher than what we report — never lower."
)


def is_runway_use(event_type: str) -> bool:
    return event_type in RUNWAY_USE_TYPES
