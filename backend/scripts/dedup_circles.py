"""One-off historical dedup: collapse over-counted 'circle' operation rows.

Root cause (fixed going forward in app/detectors.py): circle events were
bucketed for dedup at 30s (line-crossing detector) and 90s (closed-lap
detector) granularity, but a physical pattern lap is ALWAYS
>= MIN_CIRCLE_DURATION_SECONDS (120s). That meant a single real lap could
emit several distinct circle rows (observed: events 23-70s apart, ~5x
over-count) because each fell into a different fine-grained bucket and got a
distinct event id.

The forward fix coarsens the bucket to CIRCLE_BUCKET_SECONDS (120s), so new
scans naturally collapse to one row per aircraft per lap. This script cleans
up the historical rows written before that fix: for each (icao24, 120s
bucket), it keeps the EARLIEST circle row and deletes the rest.

Usage:
    # Dry run (default) — prints counts, does not modify the database.
    python -m scripts.dedup_circles --db data/circlejerk.sqlite3

    # Apply for real.
    python -m scripts.dedup_circles --db data/circlejerk.sqlite3 --apply

    # Scope to one airport.
    python -m scripts.dedup_circles --db data/circlejerk.sqlite3 --icao KBJC --apply
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

from app import db


def dedup_circles(
    conn: sqlite3.Connection,
    bucket_seconds: int = 120,
    icao: str | None = None,
) -> tuple[int, int]:
    """Collapse existing 'circle' rows to one per (icao24, timestamp // bucket_seconds)
    bucket, keeping the EARLIEST row in each bucket and deleting the rest.

    Only rows with type='circle' are ever touched — all other operation types
    (landing, touch_and_go, pass, ...) are left untouched.

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

    delete_params: list[object] = []
    if icao_up:
        delete_params.append(icao_up)
    delete_params.append(bucket_seconds)
    if icao_up:
        delete_params.append(icao_up)

    conn.execute(
        f"""
        DELETE FROM operations
        WHERE type='circle'{scope_sql} AND id NOT IN (
          SELECT id FROM (
            SELECT id, ROW_NUMBER() OVER (
              PARTITION BY icao24, timestamp / ? ORDER BY timestamp ASC
            ) AS rn
            FROM operations WHERE type='circle'{scope_sql}
          ) WHERE rn = 1
        )
        """,
        delete_params,
    )

    remaining = _count_circles()
    deleted = total_before - remaining
    return deleted, remaining


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True, help="Path to circlejerk.sqlite3")
    parser.add_argument("--icao", default=None, help="Scope to one airport ICAO (default: all airports)")
    parser.add_argument("--bucket-seconds", type=int, default=120, help="Dedup bucket width in seconds (default: 120)")
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

        deleted, remaining = dedup_circles(conn, bucket_seconds=args.bucket_seconds, icao=args.icao)

        if args.apply:
            conn.commit()
            print(f"deleted: {deleted}")
            print(f"remaining: {remaining}")
        else:
            conn.rollback()
            print(f"[DRY RUN] would delete: {deleted}")
            print(f"[DRY RUN] would remain: {remaining}")
            print("Re-run with --apply to perform the delete.")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
