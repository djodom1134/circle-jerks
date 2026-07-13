"""The nightly ledger recompute: rebuilds `daily_operation_rollup` and
`aircraft_home_base` in this service's OWN database, from the production
circlejerks database opened READ-ONLY. Ported from Task 7's `_ledger_loop`
on the main API's worker.py, which has been removed from that codebase —
this service is now the only thing that ever recomputes these tables.

Runs as its OWN process (`python -m app.worker`), separate from the API
process (`uvicorn app.main:app`) — same split as the main circlejerks
api/worker processes, and for the same reason: a slow or wedged API request
must never delay the nightly rebuild, and a long rebuild must never make an
API request wait on it.
"""
from __future__ import annotations

import asyncio
import logging
import time

from . import db, homebase, ledger
from .settings import Settings, get_settings
from .store import MemoryStore

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("ledger_api.worker")

# The rollup only needs the recent past kept warm — new production operations
# rows can only land near the present — but locality is judged over a long
# window. Mirrors Task 7's original constants.
LEDGER_ROLLUP_WINDOW_DAYS = 3
LEDGER_INTERVAL_SECONDS = 6 * 3600


def ledger_tick(settings: Settings, now_ts: int) -> dict:
    """Refresh the ledger's derived tables for every airport we have
    production events for.

    Rollup: rebuild only the last few local days — a full rebuild over
    unbounded history on every tick would be pointless work. (The one-shot
    backfill over ALL history lives in scripts/backfill_ledger.py, not here.)

    Locality: recomputed in full, because it is judged over a 180-day window
    and one new overnight stay can legitimately flip an aircraft from
    non-local to local. Getting that wrong on a named business is exactly the
    failure this project cannot afford.

    Opens the production database READ-ONLY (`ro_conn`) and this service's own
    database read-write (`rw_conn`) as two distinct connections for the whole
    tick — never the same object, never confused. `operations` itself is
    never written here, by construction: `ro_conn` cannot write it even if
    this function had a bug that tried.
    """
    rollup_rows = 0
    homebase_rows = 0
    start_ts = int(now_ts) - LEDGER_ROLLUP_WINDOW_DAYS * 86400

    with db.production_readonly_session(settings.production_database_path) as ro_conn, \
            db.ledger_db_session(settings.ledger_database_path) as rw_conn:
        airports = [
            row["icao"]
            for row in ro_conn.execute(
                "SELECT DISTINCT icao FROM operations WHERE icao IS NOT NULL"
            ).fetchall()
        ]
        for icao in airports:
            rollup_rows += ledger.rebuild_rollup(ro_conn, rw_conn, icao, start_ts, int(now_ts))
            homebase_rows += homebase.recompute_airport(ro_conn, rw_conn, icao, now_ts=int(now_ts))
            # Never carry a write transaction into the next airport's rebuild:
            # commit promptly so rw_conn's write lock is only held for the
            # writes themselves. (ro_conn never opens one at all.)
            rw_conn.commit()

    return {"rollup_rows": rollup_rows, "homebase_rows": homebase_rows}


async def _ledger_loop(store, settings: Settings) -> None:
    """Nightly-cadence loop: rebuild the ledger's derived caches. Runs the
    synchronous SQLite work in a thread so a long rebuild never blocks this
    process's event loop.

    A raising tick is logged and reported via the heartbeat, never allowed to
    end the loop — a nightly rebuild dying would silently stop refreshing the
    ledger forever.
    """
    while True:
        started = time.monotonic()
        try:
            result = await asyncio.to_thread(ledger_tick, settings, int(time.time()))
            await _record_heartbeat(store, "ledger", ok=True, started=started)
            logger.info(
                "ledger tick rollup_rows=%s homebase_rows=%s",
                result["rollup_rows"], result["homebase_rows"],
            )
        except Exception as exc:
            await _record_heartbeat(store, "ledger", ok=False, started=started, error=repr(exc)[:200])
            logger.exception("ledger tick failed")
        await asyncio.sleep(LEDGER_INTERVAL_SECONDS)


async def _record_heartbeat(
    store, name: str, *, ok: bool, started: float, error: str | None = None,
) -> None:
    """Publish a loop liveness heartbeat so an independent monitor can tell
    whether this loop is still ticking. Never lets a monitoring write break
    the loop."""
    payload = {
        "ts": int(time.time()),
        "duration_ms": int((time.monotonic() - started) * 1000),
        "ok": ok,
        "error": error,
    }
    try:
        await store.set_cache(f"worker:heartbeat:{name}", payload, max(60, LEDGER_INTERVAL_SECONDS * 2))
    except Exception:
        logger.exception("failed to write %s heartbeat", name)


async def run_forever() -> None:
    settings = get_settings()
    store = MemoryStore()
    await _ledger_loop(store, settings)


def main() -> None:
    asyncio.run(run_forever())


if __name__ == "__main__":
    main()
