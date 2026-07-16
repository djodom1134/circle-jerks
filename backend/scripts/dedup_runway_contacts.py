"""One-off historical recalc: collapse the runway-contact over-count in stored
operations (low approach / landing / takeoff).

Background (fixed going forward in app/detectors.py): a runway-contact event id
used to bucket the merged low-run's FIRST low bucket (`ep["bucket"]`). That
first bucket grows earlier as the sliding detection window feeds in more of the
descent, so one physical touchdown smeared across several ids -> several rows
(measured 3.4% of low approaches and 13.6% of landings on live KLMO data). The
id is now anchored on the touchdown's DEEPEST sample, which is stable across
scans, so new touchdowns collapse to one row.

This script fixes the rows ALREADY stored under the old drifting id. Every
re-detection of one physical touchdown shares that touchdown's deepest sample,
so its rows carry the same `timestamp`; and `_runway_low_episodes` merges
CONSECUTIVE low buckets into one episode, so the detector never emits two
touchdowns for one aircraft inside one RUNWAY_CONTACT_ANCHOR_SECONDS bucket.
Runway-contact rows of one type for one aircraft that fall in the same anchor
bucket are therefore always redetections of a single touchdown; keep the
earliest, delete the rest.

Only runway-contact rows are ever touched:
  - type NOT IN (low_approach, landing, takeoff)  -> never touched (circles are
    handled separately by dedup_lap_circles.py)

Usage:
    # Dry run (default) — prints counts, does not modify the database.
    python -m scripts.dedup_runway_contacts --db data/circlejerk.sqlite3

    # Apply for real.
    python -m scripts.dedup_runway_contacts --db data/circlejerk.sqlite3 --apply

    # Scope to one aircraft.
    python -m scripts.dedup_runway_contacts --db data/circlejerk.sqlite3 --icao24 a9e5e4 --apply
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

from app import db
from app.detectors import RUNWAY_CONTACT_ANCHOR_SECONDS

RUNWAY_CONTACT_TYPES = ("low_approach", "landing", "takeoff")


def dedup_runway_contacts(
    conn: sqlite3.Connection,
    icao24: str | None = None,
) -> tuple[int, int]:
    """Collapse same-touchdown redetections, keyed by
    (type, icao24, timestamp // RUNWAY_CONTACT_ANCHOR_SECONDS) — the same anchor
    the live id now uses. Keeps the earliest row of each touchdown; deletes the
    rest.

    Does NOT commit or rollback — the caller controls the transaction so a dry
    run can inspect the effect and then roll it back.

    Returns (deleted, remaining) counts of runway-contact rows, scoped to
    `icao24` if given.
    """
    icao_l = icao24.lower() if icao24 else None
    type_ph = ",".join("?" * len(RUNWAY_CONTACT_TYPES))
    scope_sql = " AND icao24=?" if icao_l else ""
    scope_params: list[object] = [icao_l] if icao_l else []

    def _count() -> int:
        return conn.execute(
            f"SELECT COUNT(*) FROM operations WHERE type IN ({type_ph}){scope_sql}",
            [*RUNWAY_CONTACT_TYPES, *scope_params],
        ).fetchone()[0]

    # ORDER BY timestamp then id: the earliest touchdown row wins, deterministically.
    rows = conn.execute(
        f"SELECT id, type, icao24, timestamp FROM operations "
        f"WHERE type IN ({type_ph}){scope_sql} "
        f"ORDER BY type, icao24, timestamp ASC, id ASC",
        [*RUNWAY_CONTACT_TYPES, *scope_params],
    ).fetchall()

    seen: set[tuple[str, str, int]] = set()
    to_delete: list[str] = []
    for oid, typ, ic, ts in rows:
        key = (typ, ic, int(ts) // RUNWAY_CONTACT_ANCHOR_SECONDS)
        if key in seen:
            to_delete.append(oid)
        else:
            seen.add(key)

    if to_delete:
        conn.executemany("DELETE FROM operations WHERE id=?", [(oid,) for oid in to_delete])

    return len(to_delete), _count()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True, help="path to the SQLite database")
    parser.add_argument("--icao24", default=None, help="scope to one aircraft (hex)")
    parser.add_argument("--apply", action="store_true", help="commit (default: dry run)")
    args = parser.parse_args()

    if not Path(args.db).exists():
        print(f"database not found: {args.db}", file=sys.stderr)
        return 2

    conn = db.connect(args.db)
    try:
        type_ph = ",".join("?" * len(RUNWAY_CONTACT_TYPES))
        before = conn.execute(
            f"SELECT COUNT(*) FROM operations WHERE type IN ({type_ph})",
            RUNWAY_CONTACT_TYPES,
        ).fetchone()[0]
        deleted, remaining = dedup_runway_contacts(conn, icao24=args.icao24)
        if args.apply:
            conn.commit()
            print(f"APPLIED: runway-contact rows {before} -> {remaining} (deleted {deleted})")
        else:
            conn.rollback()
            print(f"DRY RUN: would delete {deleted} of {before} runway-contact rows "
                  f"(-> {remaining}). Re-run with --apply to commit.")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
