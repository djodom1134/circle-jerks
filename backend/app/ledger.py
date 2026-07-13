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

import sqlite3
from collections import defaultdict
from datetime import date, timedelta

from . import db

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


def rebuild_rollup(conn: sqlite3.Connection, icao: str, start_ts: int, end_ts: int) -> int:
    """Rebuild daily_operation_rollup for every LOCAL day touched by [start_ts, end_ts].

    Idempotent: deletes the affected local days wholesale, then reinserts from
    `operations`. Deleting first is what makes a rebuild correct after the
    maintenance scripts in backend/scripts/ prune rows from `operations` — an
    incremental upsert would leave orphaned counts behind forever.

    Returns the number of rollup rows written.
    """
    icao = icao.upper()
    tz = db.airport_timezone(conn, icao)

    rows = conn.execute(
        "SELECT timestamp AS ts, type AS type, icao24 AS icao24 "
        "FROM operations "
        f"WHERE icao=? AND type IN ({','.join('?' * len(ROLLUP_TYPES))}) "
        "  AND timestamp BETWEEN ? AND ? AND icao24 IS NOT NULL",
        (icao, *ROLLUP_TYPES, int(start_ts), int(end_ts)),
    ).fetchall()

    counts: dict[tuple[str, str, str], int] = defaultdict(int)
    for row in rows:
        day = db.local_day_key(row["ts"], tz)
        counts[(day, row["type"], row["icao24"])] += 1

    # The days to rebuild are those spanned by the REQUESTED range, not merely the
    # days that happen to have rows — otherwise a day whose last operation was just
    # deleted would never get cleared.
    span_days = _local_days_between(start_ts, end_ts, tz)
    conn.executemany(
        "DELETE FROM daily_operation_rollup WHERE icao=? AND date_local=?",
        [(icao, day) for day in span_days],
    )
    conn.executemany(
        "INSERT INTO daily_operation_rollup (icao, date_local, event_type, icao24, count) "
        "VALUES (?, ?, ?, ?, ?)",
        [(icao, day, etype, icao24, n) for (day, etype, icao24), n in counts.items()],
    )
    return len(counts)


def _local_days_between(start_ts: int, end_ts: int, tz: str | None) -> list[str]:
    first = date.fromisoformat(db.local_day_key(int(start_ts), tz))
    last = date.fromisoformat(db.local_day_key(int(end_ts), tz))
    out, cursor = [], first
    while cursor <= last:
        out.append(cursor.isoformat())
        cursor += timedelta(days=1)
    return out


def rollup_totals(conn: sqlite3.Connection, icao: str, start_day: str, end_day: str) -> dict:
    """Totals over an inclusive local-day range. Runway uses EXCLUDE takeoffs."""
    rows = conn.execute(
        "SELECT event_type, icao24, SUM(count) AS n "
        "FROM daily_operation_rollup "
        "WHERE icao=? AND date_local BETWEEN ? AND ? "
        "GROUP BY event_type, icao24",
        (icao.upper(), start_day, end_day),
    ).fetchall()

    by_type: dict[str, int] = {etype: 0 for etype in ROLLUP_TYPES}
    aircraft: set[str] = set()
    runway_uses = 0
    for row in rows:
        by_type[row["event_type"]] = by_type.get(row["event_type"], 0) + row["n"]
        if is_runway_use(row["event_type"]):
            runway_uses += row["n"]
            aircraft.add(row["icao24"])

    return {
        "runway_uses": runway_uses,
        "by_type": by_type,
        "unique_aircraft": len(aircraft),
    }


def rollup_daily_runway_uses(
    conn: sqlite3.Connection, icao: str, start_day: str, end_day: str
) -> list[dict]:
    """Daily runway-use series over an inclusive local-day range, gap-filled with zeroes.

    Gap-filling matters: a missing bar and a zero bar mean different things, and a
    chart that silently omits quiet days overstates the typical day.
    """
    rows = conn.execute(
        "SELECT date_local, SUM(count) AS n "
        "FROM daily_operation_rollup "
        f"WHERE icao=? AND date_local BETWEEN ? AND ? "
        f"  AND event_type IN ({','.join('?' * len(RUNWAY_USE_TYPES))}) "
        "GROUP BY date_local",
        (icao.upper(), start_day, end_day, *RUNWAY_USE_TYPES),
    ).fetchall()
    found = {row["date_local"]: row["n"] for row in rows}

    out, cursor, last = [], date.fromisoformat(start_day), date.fromisoformat(end_day)
    while cursor <= last:
        key = cursor.isoformat()
        out.append({"date": key, "runway_uses": found.get(key, 0)})
        cursor += timedelta(days=1)
    return out
