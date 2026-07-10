"""One-off historical recalc: collapse the circle over-count in stored operations.

Background (fixed going forward in app/detectors.py): a "circle" event id used
to bucket the lap-END timestamp (`lap_end // 120s`). The lap end drifts sample
to sample as the detection window slides, so one physical pattern lap smeared
across ~3 buckets -> ~3 ids -> ~3 rows (measured 2.8x on real KLMO pattern
work). The id is now anchored on the lap's closest approach to the field (the
runway pass), which is stable across scans, so new laps collapse to one row.

This script fixes the rows ALREADY stored under the old smeared id. The dense
tracks that produced them are gone from the archive, so we can't re-derive.
But each circle op carries `deviation_peak_nm`, computed from that lap's own
samples — an effectively unique per-lap fingerprint that every re-detection of
the same lap shares. Rows with the same (icao24, deviation_peak_nm) that fall
within one lap window (LAP_WINDOW_S) are the same physical lap; keep the
earliest, delete the rest.

Only fingerprintable circle rows are ever touched:
  - type != 'circle'                 -> never touched
  - deviation_peak_nm IS NULL        -> never touched (no fingerprint; e.g.
                                        airports with no drawn pattern)
Two rows sharing a peak but more than LAP_WINDOW_S apart are treated as two
genuinely separate laps and both kept, so a recurring peak value cannot merge
distinct laps hours apart.

Usage:
    # Dry run (default) — prints counts, does not modify the database.
    python -m scripts.dedup_lap_circles --db data/circlejerk.sqlite3

    # Apply for real.
    python -m scripts.dedup_lap_circles --db data/circlejerk.sqlite3 --apply

    # Scope to one aircraft.
    python -m scripts.dedup_lap_circles --db data/circlejerk.sqlite3 --icao24 a9e5e4 --apply
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

from app import db

# Two circle rows sharing a deviation peak within this many seconds are the same
# physical lap. Longer than a lap's own duration yet far shorter than the gap
# before that exact peak could plausibly recur on a new lap.
LAP_WINDOW_S = 300

# No aircraft flies a full pattern lap in under this long, so two circle rows for
# the same aircraft closer than this are ALWAYS redetections of one lap — safe to
# merge for any aircraft even when their recomputed deviation peaks differ (which
# the peak key alone would miss). Well below the tightest real lap spacing.
SAFE_REDETECT_S = 150


def dedup_lap_circles(
    conn: sqlite3.Connection,
    icao24: str | None = None,
) -> tuple[int, int]:
    """Collapse same-lap circle redetections identified by a shared
    (icao24, deviation_peak_nm) fingerprint within LAP_WINDOW_S. Keeps the
    earliest row of each lap; deletes the rest.

    Does NOT commit or rollback — the caller controls the transaction so a dry
    run can inspect the effect and then roll it back.

    Returns (deleted, remaining) counts of circle rows, scoped to `icao24` if
    given.
    """
    icao_l = icao24.lower() if icao24 else None
    scope_sql = " AND icao24=?" if icao_l else ""
    scope_params: list[object] = [icao_l] if icao_l else []

    def _count_circles() -> int:
        return conn.execute(
            f"SELECT COUNT(*) FROM operations WHERE type='circle'{scope_sql}",
            scope_params,
        ).fetchone()[0]

    rows = conn.execute(
        "SELECT id, icao24, deviation_peak_nm, timestamp FROM operations "
        f"WHERE type='circle' AND deviation_peak_nm IS NOT NULL{scope_sql} "
        "ORDER BY icao24, timestamp ASC",
        scope_params,
    ).fetchall()

    # Walk each aircraft's circles in time order. A row is a redetection of the
    # last KEPT lap (drop it) when it is either within SAFE_REDETECT_S of it
    # (too soon to be a separate lap for any aircraft) or shares that lap's
    # deviation peak within LAP_WINDOW_S. Comparing to the last KEPT row — not
    # the previous row — stops a lap's own spread of redetections from being
    # mistaken for the start of the next lap.
    to_delete: list[str] = []
    cur_icao: str | None = None
    kept_ts: int | None = None
    kept_peak: float | None = None
    for oid, ic, peak, ts in rows:
        if ic != cur_icao:
            cur_icao, kept_ts, kept_peak = ic, None, None
        same_lap = kept_ts is not None and (
            ts - kept_ts < SAFE_REDETECT_S
            or (round(peak, 3) == round(kept_peak, 3) and ts - kept_ts < LAP_WINDOW_S)
        )
        if same_lap:
            to_delete.append(oid)
        else:
            kept_ts, kept_peak = ts, peak

    if to_delete:
        conn.executemany("DELETE FROM operations WHERE id=?", [(oid,) for oid in to_delete])

    return len(to_delete), _count_circles()


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
        before = conn.execute("SELECT COUNT(*) FROM operations WHERE type='circle'").fetchone()[0]
        deleted, remaining = dedup_lap_circles(conn, icao24=args.icao24)
        if args.apply:
            conn.commit()
            print(f"APPLIED: circle rows {before} -> {remaining} (deleted {deleted})")
        else:
            conn.rollback()
            print(f"DRY RUN: would delete {deleted} of {before} circle rows "
                  f"(-> {remaining}). Re-run with --apply to commit.")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
