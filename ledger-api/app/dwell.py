"""Time on field, derived from landing -> takeoff pairs already in `operations`.

Nothing persists a ground interval, and nothing needs to: both `landing` and
`takeoff` are detected and stored with timestamps, so dwell is a pairing problem,
not a schema problem. Zero migration.

We never impute. A landing with no matching takeoff in the window has no known
dwell and is excluded from the sample — but the exclusion is REPORTED, as
`coverage`, so a reader can see how much of the activity the median actually
speaks for.

Every function here only ever reads `operations` — `conn` must be the
production READ-ONLY connection (see app/db.py). Nothing in this module
writes anything, ever.
"""
from __future__ import annotations

import sqlite3
from statistics import median

# Above this, the aircraft is parked, not visiting. Used to keep the median
# meaningful for transient time-on-field; dwell_intervals() still returns the
# long stays, because a based-aircraft signal needs exactly those.
DEFAULT_MAX_VISIT_SECONDS = 6 * 3600


def dwell_intervals(
    conn: sqlite3.Connection,
    icao: str,
    start_ts: int,
    end_ts: int,
    takeoff_lookahead_seconds: int = DEFAULT_MAX_VISIT_SECONDS,
) -> list[dict]:
    """Every landing paired with that aircraft's next takeoff.

    Landings are bounded to [start_ts, end_ts] — a landing belongs to the window
    it falls in, full stop. Takeoffs are looked up in the wider
    [start_ts, end_ts + takeoff_lookahead_seconds]: a landing shortly before
    end_ts can have its takeoff shortly after end_ts, and bounding both sides to
    the same window would silently drop that takeoff even though the row exists,
    censoring the pair as unpaired. Because only short dwells can have their
    takeoff still land inside [start_ts, end_ts], that bound systematically
    censors LONG dwells at the boundary and would bias the published median
    short. Widening only the takeoff side fixes this without pulling
    next-window landings into this window's counts.

    A takeoff still beyond the lookahead horizon correctly falls out as a
    based/overnight aircraft or a genuine detection gap — see `dwell_summary`
    for how `coverage` reports (but does not distinguish the cause of) that
    residual censoring.

    Pairing rules, and why:
      - A takeoff before any landing is ignored: the aircraft was already parked
        when the window opened, so there is no arrival to measure from.
      - Two landings with no takeoff between them means we MISSED a departure.
        Pair the takeoff with the most recent landing and drop the earlier one,
        rather than inventing a multi-day dwell out of a detection gap.
      - A landing with no subsequent takeoff (within the lookahead) is dropped,
        not imputed.
    """
    rows = conn.execute(
        "SELECT icao24, type, timestamp AS ts FROM operations "
        "WHERE icao=? AND icao24 IS NOT NULL AND ("
        "    (type='landing' AND timestamp BETWEEN ? AND ?)"
        "    OR (type='takeoff' AND timestamp BETWEEN ? AND ?)"
        "  ) "
        "ORDER BY icao24 ASC, timestamp ASC, id ASC",
        (
            icao.upper(),
            int(start_ts), int(end_ts),
            int(start_ts), int(end_ts) + int(takeoff_lookahead_seconds),
        ),
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
    and the reader is entitled to know which they are looking at. What `coverage`
    does NOT distinguish: a landing can be uncounted either because no takeoff was
    found within `max_seconds` of end_ts (a pair longer than `max_seconds` is
    filtered from the median anyway, so that is exactly the right lookahead
    horizon) or because the paired stay was itself excluded for being over
    `max_seconds` (an overnight/based aircraft). Both look identical in
    `coverage` — a reader cannot tell "gap in detection" from "aircraft stayed
    the night" from the number alone.

    `max_seconds` is passed through to `dwell_intervals` as its takeoff lookahead
    horizon, so the two never disagree: a pair longer than `max_seconds` would be
    filtered out of the median here regardless, so there is no reason to look
    for its takeoff any farther than that.
    """
    visits = [
        i["seconds"]
        for i in dwell_intervals(
            conn, icao, start_ts, end_ts, takeoff_lookahead_seconds=max_seconds
        )
        if i["seconds"] <= max_seconds
    ]

    landings = conn.execute(
        "SELECT COUNT(*) AS n FROM operations "
        "WHERE icao=? AND type='landing' AND timestamp BETWEEN ? AND ? AND icao24 IS NOT NULL",
        (icao.upper(), int(start_ts), int(end_ts)),
    ).fetchone()["n"]

    return {
        "median_seconds": round(median(visits)) if visits else None,
        "sample_size": len(visits),
        "landings": landings,
        "coverage": round(len(visits) / landings, 4) if landings else 0.0,
    }
