"""Time on field, derived from landing -> takeoff pairs already in `operations`.

Nothing persists a ground interval, and nothing needs to: both `landing` and
`takeoff` are detected and stored with timestamps, so dwell is a pairing problem,
not a schema problem. Zero migration.

We never impute. A landing with no matching takeoff in the window has no known
dwell and is excluded from the sample — but the exclusion is REPORTED, as
`coverage`, so a reader can see how much of the activity the median actually
speaks for.
"""
from __future__ import annotations

import sqlite3
from statistics import median

# Above this, the aircraft is parked, not visiting. Used to keep the median
# meaningful for transient time-on-field; dwell_intervals() still returns the
# long stays, because a based-aircraft signal needs exactly those.
DEFAULT_MAX_VISIT_SECONDS = 6 * 3600


def dwell_intervals(
    conn: sqlite3.Connection, icao: str, start_ts: int, end_ts: int
) -> list[dict]:
    """Every landing paired with that aircraft's next takeoff, in [start_ts, end_ts].

    Pairing rules, and why:
      - A takeoff before any landing is ignored: the aircraft was already parked
        when the window opened, so there is no arrival to measure from.
      - Two landings with no takeoff between them means we MISSED a departure.
        Pair the takeoff with the most recent landing and drop the earlier one,
        rather than inventing a multi-day dwell out of a detection gap.
      - A landing with no subsequent takeoff is dropped, not imputed.
    """
    rows = conn.execute(
        "SELECT icao24, type, timestamp AS ts FROM operations "
        "WHERE icao=? AND type IN ('landing','takeoff') "
        "  AND timestamp BETWEEN ? AND ? AND icao24 IS NOT NULL "
        "ORDER BY icao24 ASC, timestamp ASC",
        (icao.upper(), int(start_ts), int(end_ts)),
    ).fetchall()

    intervals: list[dict] = []
    open_landing: dict[str, int] = {}
    for row in rows:
        aircraft = row["icao24"]
        if row["type"] == "landing":
            # Overwrites any still-open landing: a missed takeoff must not become
            # a fake dwell spanning to the next departure days later.
            open_landing[aircraft] = row["ts"]
            continue
        landed_at = open_landing.pop(aircraft, None)
        if landed_at is None:
            continue  # already on the field when the window opened
        intervals.append({
            "icao24": aircraft,
            "landing_ts": landed_at,
            "takeoff_ts": row["ts"],
            "seconds": row["ts"] - landed_at,
        })

    intervals.sort(key=lambda i: i["landing_ts"])
    return intervals


def dwell_summary(
    conn: sqlite3.Connection,
    icao: str,
    start_ts: int,
    end_ts: int,
    max_seconds: int = DEFAULT_MAX_VISIT_SECONDS,
) -> dict:
    """Median time on field for transient visits, plus how much of the activity
    that median actually covers.

    `coverage` is paired landings / all landings. Publishing it is the point: a
    median drawn from 20% of arrivals is a different claim from one drawn from 90%,
    and the reader is entitled to know which they are looking at.
    """
    visits = [i["seconds"] for i in dwell_intervals(conn, icao, start_ts, end_ts)
              if i["seconds"] <= max_seconds]

    landings = conn.execute(
        "SELECT COUNT(*) AS n FROM operations "
        "WHERE icao=? AND type='landing' AND timestamp BETWEEN ? AND ? AND icao24 IS NOT NULL",
        (icao.upper(), int(start_ts), int(end_ts)),
    ).fetchone()["n"]

    return {
        "median_seconds": int(median(visits)) if visits else None,
        "sample_size": len(visits),
        "landings": landings,
        "coverage": round(len(visits) / landings, 4) if landings else 0.0,
    }
