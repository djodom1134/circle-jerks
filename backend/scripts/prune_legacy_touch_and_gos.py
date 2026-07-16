"""One-off historical recalc: drop legacy episode-based touch-and-go rows.

Background (changed going forward in app/detectors.py): a touch-and-go used to
be a >=50 ft runway-contact episode. It is now a circle whose loop crosses the
runway (touch_and_gos_from_circles), so a lap over the runway is both a circle
and a T&G. The two are counted from separate operation rows, and the old
episode-based T&G rows (written under the previous definition) coexist with the
new circle-derived ones for the same laps under different ids — so T&G stacked
to ~2x circles (e.g. N738BJ: 12 circles, 24 T&G).

The two are cleanly distinguishable by turn_direction, exactly like
prune_crossing_circles:
  - circle-derived T&G ALWAYS inherit the circle's turn_direction ('left'/'right')
  - episode-based T&G NEVER set it (stored NULL)

So this deletes exactly the legacy rows:
    DELETE FROM operations WHERE type='touch_and_go' AND turn_direction IS NULL

Only type='touch_and_go' rows are touched; circles, low approaches, landings,
and every circle-derived T&G (turn_direction set) are left untouched. Laps whose
tracks are still retained are re-derived as circle-T&G by the live scan; laps
older than the archive lose their T&G (their tracks are gone).

Usage:
    python -m scripts.prune_legacy_touch_and_gos --db data/circlejerk.sqlite3
    python -m scripts.prune_legacy_touch_and_gos --db data/circlejerk.sqlite3 --apply
    python -m scripts.prune_legacy_touch_and_gos --db data/circlejerk.sqlite3 --icao24 a9e8e5 --apply
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

from app import db


def prune_legacy_touch_and_gos(
    conn: sqlite3.Connection,
    icao24: str | None = None,
) -> tuple[int, int]:
    """Delete episode-based touch-and-go rows (turn_direction IS NULL). Keeps
    circle-derived T&G (turn_direction set) and all non-T&G rows.

    Does NOT commit or rollback — the caller controls the transaction so a dry
    run can inspect the effect and then roll it back.

    Returns (deleted, remaining) T&G-row counts, scoped to `icao24` if given.
    """
    icao_l = icao24.lower() if icao24 else None
    scope = " AND icao24=?" if icao_l else ""
    params: list = [icao_l] if icao_l else []

    def _count() -> int:
        return conn.execute(
            f"SELECT COUNT(*) FROM operations WHERE type='touch_and_go'{scope}", params
        ).fetchone()[0]

    cur = conn.execute(
        f"DELETE FROM operations WHERE type='touch_and_go' AND turn_direction IS NULL{scope}",
        params,
    )
    deleted = cur.rowcount
    return deleted, _count()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True)
    parser.add_argument("--icao24", default=None)
    parser.add_argument("--apply", action="store_true", help="commit (default: dry run)")
    args = parser.parse_args()

    if not Path(args.db).exists():
        print(f"database not found: {args.db}", file=sys.stderr)
        return 2

    conn = db.connect(args.db)
    try:
        before = conn.execute("SELECT COUNT(*) FROM operations WHERE type='touch_and_go'").fetchone()[0]
        deleted, remaining = prune_legacy_touch_and_gos(conn, icao24=args.icao24)
        if args.apply:
            conn.commit()
            print(f"APPLIED: touch_and_go rows {before} -> {remaining} (deleted {deleted} legacy)")
        else:
            conn.rollback()
            print(f"DRY RUN: would delete {deleted} of {before} touch_and_go rows "
                  f"(-> {remaining} circle-derived). Re-run with --apply.")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
