"""One-shot backfill of the ledger's derived tables (rollup + home-base) over
all history in `operations`.

The nightly worker tick (`worker.ledger_tick`) only keeps the last few local
days of `daily_operation_rollup` warm — a detector pass can only add events
near the present, so rebuilding the full history on every tick would be
pointless work. Run this script ONCE after deploying the ledger so the first
`/airports/{icao}/ledger` response isn't reporting only the last few days, and
again after any maintenance script that prunes/edits rows in `operations`.

WHAT THIS DOES NOT DO: it does not attempt to backfill
`operations.origin_airport_icao` for historical rows. Origin is FORWARD-FILL
ONLY — written from a locally-observed ground track at detection time — and is
structurally unrecoverable for anything already in the database: the cold
track archive is pruned at `settings.track_archive_horizon_days` (7 days),
adsbdb/FlightAware reject GA N-numbers, and OpenSky historical lookups are
disabled on this account tier. `homebase.recompute_airport` already treats a
NULL origin as "no information" rather than evidence of anything (see
homebase.py's module docstring on the floor constraint), so rebuilding it from
`operations` as-is is always safe and never invents an origin that was never
observed.

Both tables are DERIVED CACHES: rebuilding them is safe and idempotent, and
`operations` itself is never modified by this script.

Usage:
    python -m scripts.backfill_ledger              # all airports
    python -m scripts.backfill_ledger KLMO KBJC     # only these airports
"""
from __future__ import annotations

import sys
import time

from app import db, homebase, ledger
from app.db import db_session
from app.settings import get_settings


def main() -> None:
    settings = get_settings()
    db.init_db(settings.database_path)
    now = int(time.time())
    wanted = {a.upper() for a in sys.argv[1:]}

    with db_session(settings.database_path) as conn:
        airports = [
            row["icao"]
            for row in conn.execute(
                "SELECT DISTINCT icao FROM operations WHERE icao IS NOT NULL"
            ).fetchall()
            if not wanted or row["icao"] in wanted
        ]

        if not airports:
            print("no matching airports with operations rows found")
            return

        for icao in airports:
            earliest = conn.execute(
                "SELECT MIN(timestamp) AS t FROM operations WHERE icao=?", (icao,)
            ).fetchone()["t"]
            if earliest is None:
                continue

            rollup_rows = ledger.rebuild_rollup(conn, icao, earliest, now)
            # Never carry a write transaction across the two rebuilds' worth of
            # work for one airport into the next airport's — same reasoning as
            # worker.ledger_tick's per-airport commit.
            conn.commit()
            homebase_rows = homebase.recompute_airport(conn, icao, now_ts=now)
            conn.commit()

            print(
                f"{icao}: {rollup_rows} rollup rows, {homebase_rows} aircraft classified, "
                f"history since {time.strftime('%Y-%m-%d', time.gmtime(earliest))}"
            )


if __name__ == "__main__":
    main()
