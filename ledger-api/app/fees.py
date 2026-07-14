"""Per-aircraft fee totals for the live map's price tags and the hero ticker.

Everything here is read directly off the production `operations` table via
the read-only connection (`ro_conn`) -- never `daily_operation_rollup`. Two
separate reasons, not one:

  * `total` must cover the aircraft's FULL history in `operations`.
    `daily_operation_rollup` is only rebuilt for a rolling few-day window on
    each worker tick (see worker.py's `LEDGER_ROLLUP_WINDOW_DAYS`); relying on
    it here would under-count anything the last backfill/tick didn't reach.

  * `today` is a ROLLING 24-HOUR window (`now - 86400` .. `now`), not a local
    calendar day, precisely so the hero ticker reacts the instant a new
    runway use is detected instead of waiting for the next worker tick.
    `daily_operation_rollup` is keyed by LOCAL CALENDAR day and structurally
    CANNOT express a rolling window at all -- there is no query against it
    that would give the right answer. Do not "optimize" this back onto the
    rollup; that would silently reintroduce up to a 6-hour lag on a live
    ticker, on top of being the wrong shape of window entirely.

`month` / `year` ARE local-calendar buckets (month-to-date / year-to-date in
the airport's own timezone, via `db.local_day_key`) -- those two are the only
fields for which "calendar" is the correct meaning.

`rate_window` is the hero ticker's tick RATE, not a count to display on its
own: the trailing 20 minutes of runway uses, for the frontend to extrapolate
a continuously-ticking dollar figure between polls. Same reasoning as
`today` -- read straight off `operations`, never the rollup, so the rate
reflects reality up to the second, not up to the last worker tick.

Never reads `aircraft_registry` or `operations.registration` -- this payload
carries tail numbers only, resolved via `resolve_display_tail`, never an
owner/registrant name or address. See ledger.py's `operator_ledger` docstring
for the fuller privacy rationale; the same boundary applies here.
"""
from __future__ import annotations

import sqlite3

from . import db, laps, ledger
from .registry.normalize import resolve_display_tail

# The ticker's "today" is a ROLLING window, not a local calendar day -- see
# the module docstring. Fixed at 24 hours; not configurable, since a fee
# ticker that means something different depending on deployment settings
# would be its own honesty problem.
ROLLING_WINDOW_SECONDS = 24 * 60 * 60

# The hero ticker's tick rate is the average observed over this trailing
# window -- long enough to smooth out single-aircraft noise, short enough to
# go quiet within 20 minutes of the pattern actually going quiet.
RATE_WINDOW_SECONDS = 20 * 60


def build_aircraft_fees(ro_conn: sqlite3.Connection, icao: str, now_ts: int) -> dict:
    """The Lost Landing's live-map fee payload for `icao`.

    `ro_conn` must be the production READ-ONLY connection (see app/db.py's
    module docstring). This function opens nothing and writes nothing; it is
    a pure read against `operations`.
    """
    icao = icao.upper()
    now_ts = int(now_ts)
    tz = db.airport_timezone(ro_conn, icao)

    counting_since = ro_conn.execute(
        "SELECT MIN(timestamp) AS t FROM operations WHERE icao=?", (icao,)
    ).fetchone()["t"]

    today_key = db.local_day_key(now_ts, tz)
    month_key = today_key[:7]  # "YYYY-MM"
    year_key = today_key[:4]   # "YYYY"

    placeholders = ",".join("?" * len(ledger.RUNWAY_USE_TYPES))

    # Every count below reads `operations` directly rather than the rollup (see the
    # module docstring), so each one must suppress lap re-detections for itself —
    # the rollup's own filtering in ledger.rebuild_rollup cannot help here. One
    # global decision, applied to all four windows, so the ticker, the 24h figure
    # and the lifetime total can never disagree about which laps were real. See
    # laps.py.
    #
    # Filtered in Python, never as a SQL `id NOT IN (...)`: the suppressed set grows
    # with history (already ~1k rows over a month of pattern work at KLMO) and would
    # run straight into SQLite's bound-parameter ceiling. The queries keep their
    # indexed range-scan shape and return ids instead of a bare COUNT(*).
    phantoms = laps.phantom_lap_op_ids(ro_conn, icao)

    # Rolling 24h count -- one indexed range scan on operations(icao,
    # timestamp) over a single day. Deliberately NOT a full-history scan; see
    # the module docstring for why this can never come from the rollup.
    since_ts = now_ts - ROLLING_WINDOW_SECONDS
    rolling_rows = ro_conn.execute(
        f"SELECT id, icao24 FROM operations "
        f"WHERE icao=? AND type IN ({placeholders}) AND icao24 IS NOT NULL "
        f"  AND timestamp BETWEEN ? AND ?",
        (icao, *ledger.RUNWAY_USE_TYPES, since_ts, now_ts),
    ).fetchall()
    rolling_by_aircraft: dict[str, int] = {}
    for row in rolling_rows:
        if row["id"] in phantoms:
            continue
        rolling_by_aircraft[row["icao24"]] = rolling_by_aircraft.get(row["icao24"], 0) + 1
    rolling_total = sum(rolling_by_aircraft.values())

    # Trailing 20-minute rate -- another indexed range scan on operations(icao,
    # timestamp), same shape as the rolling-24h query above, just a shorter
    # window and a scalar count rather than a per-aircraft breakdown.
    rate_since_ts = now_ts - RATE_WINDOW_SECONDS
    rate_rows = ro_conn.execute(
        f"SELECT id FROM operations "
        f"WHERE icao=? AND type IN ({placeholders}) AND icao24 IS NOT NULL "
        f"  AND timestamp BETWEEN ? AND ?",
        (icao, *ledger.RUNWAY_USE_TYPES, rate_since_ts, now_ts),
    ).fetchall()
    rate_runway_uses = sum(1 for row in rate_rows if row["id"] not in phantoms)

    # Full history for `total` plus local-calendar month/year-to-date. This
    # airport's ADS-B history is short (weeks, as of writing), so scanning
    # every runway-use row it has ever produced is cheap -- and it is the only
    # way `total` is guaranteed to cover ALL of `operations`, not just
    # whatever window the rollup happens to have rebuilt.
    rows = ro_conn.execute(
        f"SELECT id, icao24, callsign, timestamp AS ts FROM operations "
        f"WHERE icao=? AND type IN ({placeholders}) AND icao24 IS NOT NULL",
        (icao, *ledger.RUNWAY_USE_TYPES),
    ).fetchall()

    counts: dict[str, dict[str, int]] = {}
    latest_callsign: dict[str, tuple[int, str]] = {}
    for row in rows:
        if row["id"] in phantoms:
            continue
        icao24 = row["icao24"]
        entry = counts.setdefault(icao24, {"total": 0, "month": 0, "year": 0})
        entry["total"] += 1
        day = db.local_day_key(row["ts"], tz)
        if day[:7] == month_key:
            entry["month"] += 1
        if day[:4] == year_key:
            entry["year"] += 1
        if row["callsign"]:
            prev = latest_callsign.get(icao24)
            if prev is None or row["ts"] > prev[0]:
                latest_callsign[icao24] = (row["ts"], row["callsign"])

    aircraft: dict[str, dict] = {}
    for icao24, entry in counts.items():
        callsign = latest_callsign.get(icao24, (0, None))[1]
        aircraft[icao24] = {
            "tail": resolve_display_tail(callsign, None, icao24),
            "total": entry["total"],
            "today": rolling_by_aircraft.get(icao24, 0),
            "month": entry["month"],
            "year": entry["year"],
        }

    return {
        "airport_icao": icao,
        "timezone": tz,
        "counting_since": counting_since,
        "today": {
            # Rolling 24h window, NOT a local calendar day -- see module
            # docstring. `since_ts`/`until_ts` let a client render an honest
            # "last 24 hours" qualifier instead of assuming "today" means
            # midnight-to-now.
            "window": "rolling_24h",
            "since_ts": since_ts,
            "until_ts": now_ts,
            "runway_uses": rolling_total,
        },
        "rate_window": {
            "seconds": RATE_WINDOW_SECONDS,
            "runway_uses": rate_runway_uses,
            "uses_per_second": rate_runway_uses / RATE_WINDOW_SECONDS,
        },
        "aircraft": aircraft,
    }
