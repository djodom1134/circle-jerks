"""One-off historical recalc: prune line-crossing 'circle' rows so the
persisted circle counts match the closed-lap-only definition.

Background (fixed going forward in app/detectors.py): a "circle" used to be
the UNION of two detectors — a closed-lap detector (real ~340deg pattern lap)
and a home<->airport line-crossing counter. The crossing counter emitted
~2 events per orbit (a track orbiting the airport crosses the home<->airport
chord roughly twice per revolution), inflating circle totals ~2-5x. The
crossing detector has been removed; a circle is now a closed lap only.

The two detectors are distinguishable in the persisted `operations` rows by
`turn_direction`:
  - closed-lap events ALWAYS set turn_direction ('left'/'right')
  - line-crossing events NEVER set it (stored NULL)

So this script deletes exactly the historical crossing rows:
    DELETE FROM operations WHERE type='circle' AND turn_direction IS NULL

Only type='circle' rows are ever touched; every other operation type and every
closed-lap circle (turn_direction NOT NULL) is left untouched.

Usage:
    # Dry run (default) — prints counts, does not modify the database.
    python -m scripts.prune_crossing_circles --db data/circlejerk.sqlite3

    # Apply for real.
    python -m scripts.prune_crossing_circles --db data/circlejerk.sqlite3 --apply

    # Scope to one airport.
    python -m scripts.prune_crossing_circles --db data/circlejerk.sqlite3 --icao KBJC --apply
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

from app import db


def prune_crossing_circles(
    conn: sqlite3.Connection,
    icao: str | None = None,
) -> tuple[int, int]:
    """Delete 'circle' rows produced by the removed line-crossing detector,
    identified by turn_direction IS NULL. Closed-lap circles (turn_direction
    set) and all non-circle rows are left untouched.

    Does NOT commit or rollback — the caller controls the transaction so a
    dry run can inspect the effect and then roll it back.

    Returns (deleted, remaining) counts of circle rows, scoped to `icao` if given.
    """
    icao_up = icao.upper() if icao else None
    scope_sql = " AND icao=?" if icao_up else ""

    def _count_circles() -> int:
        params = [icao_up] if icao_up else []
        row = conn.execute(
            f"SELECT COUNT(*) FROM operations WHERE type='circle'{scope_sql}",
            params,
        ).fetchone()
        return row[0]

    total_before = _count_circles()

    params: list[object] = [icao_up] if icao_up else []
    conn.execute(
        f"DELETE FROM operations WHERE type='circle' AND turn_direction IS NULL{scope_sql}",
        params,
    )

    remaining = _count_circles()
    deleted = total_before - remaining
    return deleted, remaining


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True, help="Path to circlejerk.sqlite3")
    parser.add_argument("--icao", default=None, help="Scope to one airport ICAO (default: all airports)")
    parser.add_argument("--apply", action="store_true", help="Actually perform the delete (default: dry run)")
    args = parser.parse_args()

    db_path = Path(args.db)
    if not db_path.exists():
        print(f"db not found: {db_path}", file=sys.stderr)
        return 1

    scope = args.icao.upper() if args.icao else None
    scope_label = f"icao={scope}" if scope else "all airports"

    conn = db.connect(str(db_path))
    try:
        icao_clause = " AND icao=?" if scope else ""
        params = [scope] if scope else []
        total_before = conn.execute(
            f"SELECT COUNT(*) FROM operations WHERE type='circle'{icao_clause}", params
        ).fetchone()[0]
        print(f"circle rows ({scope_label}): {total_before}")

        deleted, remaining = prune_crossing_circles(conn, icao=args.icao)

        if args.apply:
            conn.commit()
            print(f"deleted (line-crossing artifacts): {deleted}")
            print(f"remaining (closed laps): {remaining}")
        else:
            conn.rollback()
            print(f"[DRY RUN] would delete (line-crossing artifacts): {deleted}")
            print(f"[DRY RUN] would remain (closed laps): {remaining}")
            print("Re-run with --apply to perform the delete.")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
