"""One-shot backfill of the ledger's derived tables (rollup + home-base) over
all history in the production `operations` table.

The nightly worker tick (`app.worker.ledger_tick`) only keeps the last few
local days of `daily_operation_rollup` warm — a detector pass on the main API
can only add events near the present, so rebuilding the full history on every
tick would be pointless work. Run this script ONCE after deploying this
service so the first `/airports/{icao}/ledger` response isn't reporting only
the last few days, and again after any maintenance script on the main API
prunes/edits rows in `operations`.

WHAT THIS DOES NOT DO, AND NEVER WILL: it does not backfill
`operations.origin_airport_icao`. That column is permanently NULL — the
writer that once populated it lived on the main circlejerks API's detector
loop and has been removed from that codebase entirely. There is no origin
backfill here, now or in the future, because there is no origin signal for
this service to backfill from. `homebase.recompute_airport` already treats a
NULL origin as "no information" rather than evidence of anything (see
homebase.py's module docstring), so rebuilding it from `operations` as-is is
always safe.

Both tables are DERIVED CACHES in THIS SERVICE'S OWN database: rebuilding them
is safe and idempotent, and the production database is only ever opened
READ-ONLY (see app/db.py) — this script cannot write to it even if it tried.

Usage (from ledger-api/):
    .venv/bin/python -m scripts.backfill_ledger              # all airports
    .venv/bin/python -m scripts.backfill_ledger KLMO KBJC     # only these airports
"""
from __future__ import annotations

import sys
import time

from app import db, homebase, ledger
from app.settings import get_settings


def main() -> None:
    settings = get_settings()
    now = int(time.time())
    wanted = {a.upper() for a in sys.argv[1:]}

    with db.production_readonly_session(settings.production_database_path) as ro_conn, \
            db.ledger_db_session(settings.ledger_database_path) as rw_conn:
        airports = [
            row["icao"]
            for row in ro_conn.execute(
                "SELECT DISTINCT icao FROM operations WHERE icao IS NOT NULL"
            ).fetchall()
            if not wanted or row["icao"] in wanted
        ]

        if not airports:
            print("no matching airports with operations rows found")
            return

        for icao in airports:
            earliest = ro_conn.execute(
                "SELECT MIN(timestamp) AS t FROM operations WHERE icao=?", (icao,)
            ).fetchone()["t"]
            if earliest is None:
                continue

            rollup_rows = ledger.rebuild_rollup(ro_conn, rw_conn, icao, earliest, now)
            # Never carry a write transaction across the two rebuilds' worth of
            # work for one airport into the next airport's — same reasoning as
            # worker.ledger_tick's per-airport commit.
            rw_conn.commit()
            homebase_rows = homebase.recompute_airport(ro_conn, rw_conn, icao, now_ts=now)
            rw_conn.commit()

            print(
                f"{icao}: {rollup_rows} rollup rows, {homebase_rows} aircraft classified, "
                f"history since {time.strftime('%Y-%m-%d', time.gmtime(earliest))}"
            )


if __name__ == "__main__":
    main()
