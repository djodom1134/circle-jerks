"""Suppress physically-impossible OVERLAPPING pattern laps at read time.

THE PROBLEM. The main API's circle detector re-runs over a SLIDING scan window and
writes each detected lap to `operations`, deduped by a stable event id. That id is
anchored on the lap's closest approach to the field (detectors.py) — but a pattern
lap crosses the runway TWICE, once on the departure roll and once on the arrival,
and those two passes are further apart in time than the anchor's bucket. Which of
them wins depends on where the sliding window happened to cut the track, so ONE
physical lap can resolve to TWO anchors, two ids, and two rows. Measured against a
reconstructed KLMO track: 8 real laps flown, 14 circle rows written.

The tell is that the duplicates OVERLAP IN TIME. A circle row carries
`time_total_s` (the lap's duration), so a lap spans `[timestamp - time_total_s,
timestamp]`. Two real laps by one aircraft CANNOT overlap — you cannot fly two
seven-minute circuits starting one minute apart. Any lap that begins before the
previous one ended is therefore a re-detection of that same lap, not a new one.

So: sort an aircraft's laps by start, keep one, and drop every lap that starts
before the kept one ends. What survives is a set of laps that could actually have
been flown.

THIS IS NOT DEAD CODE AND IT IS NOT A ONE-OFF MIGRATION. It is tempting to read
this as a patch for legacy rows that a detector fix has since made unnecessary.
It is not. The duplicate rows come from lap IDENTITY, not from any gate the
detector could tighten, and the detector still emits them today — verified by
replaying the production scan cadence over a reconstructed KLMO track both before
and after the airborne-floor fix (14 rows for 8 laps, either way). Delete this
filter and the published counts double.

DIRECTION OF ERROR. The greedy keep-first rule can also drop a REAL lap whose
re-detection happened to be kept in its place (8 real laps -> 7 kept, in the
reconstruction above). That is the right way to be wrong: every number this site
publishes is a FLOOR — real activity is higher than we report, never lower. An
inflated count is the one error the site cannot survive.

A touch-and-go is derived 1:1 from a circle and shares its `(icao24, timestamp)`
(see `touch_and_gos_from_circles` in the main API), which is what lets us recover
each touch-and-go's time span from its circle. Only `touch_and_go` rows are
suppressed here: `landing` and `low_approach` come from the runway-contact episode
detector, not from circles, and are not subject to this duplication.
"""
from __future__ import annotations

import sqlite3

# The event type that is derived from a circle, and therefore the only runway use
# that can be duplicated by circle re-detection.
LAP_DERIVED_TYPE = "touch_and_go"


def phantom_lap_op_ids(ro_conn: sqlite3.Connection, icao: str) -> set[str]:
    """`operations.id` of every touch-and-go that overlaps an earlier kept lap.

    Computed over the airport's FULL history, never a window slice. The greedy
    scan's result depends on where it starts, so slicing first would let the same
    lap be kept in one window and dropped in another — the totals on two pages of
    the same site would then disagree. Callers filter the returned ids by whatever
    window they report on; the decision itself is made once, globally.

    `ro_conn` is the production READ-ONLY connection. This function only reads.
    """
    rows = ro_conn.execute(
        # `time_total_s` lives on the CIRCLE row, so recover each touch-and-go's
        # duration by joining back to the circle it was derived from. MAX() rather
        # than a bare join so a hypothetical pair of circles sharing one
        # (icao24, timestamp) can never fan this query out into duplicate rows.
        "SELECT tg.id AS id, tg.icao24 AS icao24, tg.timestamp AS ts, "
        "       (SELECT MAX(c.time_total_s) FROM operations c "
        "         WHERE c.icao = tg.icao AND c.type = 'circle' "
        "           AND c.icao24 = tg.icao24 AND c.timestamp = tg.timestamp) AS lap_seconds "
        "FROM operations tg "
        "WHERE tg.icao = ? AND tg.type = ? AND tg.icao24 IS NOT NULL",
        (icao.upper(), LAP_DERIVED_TYPE),
    ).fetchall()

    # (start, end, icao24, id). A lap with no recoverable duration — its circle row
    # was pruned, or `time_total_s` was never written because the airport had no
    # pattern to match the lap against — becomes a ZERO-LENGTH lap at its own
    # timestamp. It can then still be suppressed BY a kept lap whose span covers it
    # (a lap that ended inside another lap is impossible however long it was), but
    # it can never itself suppress anything, because we do not know how far back it
    # reached and will not invent it. Unprovable duplicates are KEPT.
    laps: list[tuple[int, int, str, str]] = []
    for row in rows:
        end = int(row["ts"])
        duration = row["lap_seconds"]
        start = end - int(duration) if duration is not None and int(duration) > 0 else end
        laps.append((start, end, row["icao24"], row["id"]))

    # Sort by START (then end, then id, so the result never depends on row order).
    laps.sort()

    suppressed: set[str] = set()
    kept_end: dict[str, int] = {}
    for start, end, icao24, op_id in laps:
        previous_end = kept_end.get(icao24)
        if previous_end is not None and start < previous_end:
            suppressed.add(op_id)  # begins before the kept lap ended -> impossible
            continue
        kept_end[icao24] = end
    return suppressed
