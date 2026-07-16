"""Backfill submission_aircraft counts from historical submission text.

Past submissions (before per-aircraft counts were captured) left
submission_aircraft.{circles, touch_and_gos, low_approaches, passes_over_user}
all zero. The complaint text was generated from those counts at submission time
using known templates, so we can regex them back out.

Single-aircraft submissions: counts attach to the single aircraft.
Aggregate submissions: totals split evenly across the listed aircraft (lossy
but better than zeros).

Also seeds aircraft_cache.registration from callsign when callsign looks like
an N-number tail (the registration is the callsign for US civil registrations).

Idempotent: only updates rows where the target column is currently 0 / NULL.
"""
from __future__ import annotations

import argparse
import re
import sqlite3
import sys
from pathlib import Path

# Patterns match the deterministic templates in app/llm.py and similar
# Groq output. Capture the integer that precedes each kind.
CIRCLES_PATTERNS = [
    re.compile(r"(\d+)\s+(?:total\s+)?circles?\b", re.IGNORECASE),
    re.compile(r"circling\s+(\d+)\s+times?\b", re.IGNORECASE),
    re.compile(r"detected\s+circling\s+(\d+)", re.IGNORECASE),
]
TG_PATTERNS = [
    re.compile(r"(\d+)\s+touch[- ]and[- ]go", re.IGNORECASE),
]
LOW_PATTERNS = [
    re.compile(r"(\d+)\s+low\s+approach", re.IGNORECASE),
]
PASS_PATTERNS = [
    re.compile(r"(\d+)\s+(?:direct\s+)?(?:overflights?|passes?)\s+(?:directly\s+)?(?:over|of)\s+my\s+location", re.IGNORECASE),
    re.compile(r"passed?\s+directly\s+over\s+my\s+location\s+(\d+)\s+times?\b", re.IGNORECASE),
]


def first_match(patterns: list[re.Pattern], text: str) -> int | None:
    for pattern in patterns:
        match = pattern.search(text)
        if match:
            try:
                return int(match.group(1))
            except (ValueError, IndexError):
                continue
    return None


def extract_counts(text: str) -> dict[str, int | None]:
    return {
        "circles": first_match(CIRCLES_PATTERNS, text),
        "touch_and_gos": first_match(TG_PATTERNS, text),
        "low_approaches": first_match(LOW_PATTERNS, text),
        "passes_over_user": first_match(PASS_PATTERNS, text),
    }


def backfill(conn: sqlite3.Connection, *, dry_run: bool = False) -> dict:
    cursor = conn.execute(
        """
        SELECT id, text, target_count, mode
        FROM submission_events
        WHERE id IN (
          SELECT DISTINCT submission_id FROM submission_aircraft
          WHERE circles = 0 AND touch_and_gos = 0
          AND low_approaches = 0 AND passes_over_user = 0
        )
        ORDER BY id
        """
    )
    submissions = cursor.fetchall()
    stats = {
        "submissions_scanned": len(submissions),
        "rows_updated": 0,
        "circles_total": 0,
        "touch_and_gos_total": 0,
        "low_approaches_total": 0,
        "passes_total": 0,
        "registrations_seeded": 0,
    }

    for sub in submissions:
        counts = extract_counts(sub["text"] or "")
        if all(v is None for v in counts.values()):
            continue
        targets = conn.execute(
            "SELECT icao24, callsign FROM submission_aircraft WHERE submission_id = ?",
            (sub["id"],),
        ).fetchall()
        if not targets:
            continue
        target_count = max(1, len(targets))
        is_aggregate = target_count > 1

        # For aggregate submissions, divide evenly (lossy) across the listed
        # aircraft. For single-target submissions, the counts attach directly.
        per_aircraft = {}
        for key, value in counts.items():
            if value is None:
                per_aircraft[key] = 0
                continue
            if is_aggregate:
                per_aircraft[key] = value // target_count
            else:
                per_aircraft[key] = value

        for target in targets:
            if dry_run:
                stats["rows_updated"] += 1
            else:
                conn.execute(
                    """
                    UPDATE submission_aircraft
                    SET circles = ?,
                        touch_and_gos = ?,
                        low_approaches = ?,
                        passes_over_user = ?
                    WHERE submission_id = ? AND icao24 = ?
                    """,
                    (
                        per_aircraft["circles"],
                        per_aircraft["touch_and_gos"],
                        per_aircraft["low_approaches"],
                        per_aircraft["passes_over_user"],
                        sub["id"],
                        target["icao24"],
                    ),
                )
                stats["rows_updated"] += 1
            stats["circles_total"] += per_aircraft["circles"]
            stats["touch_and_gos_total"] += per_aircraft["touch_and_gos"]
            stats["low_approaches_total"] += per_aircraft["low_approaches"]
            stats["passes_total"] += per_aircraft["passes_over_user"]

    # Seed aircraft_cache.registration from callsign for US N-numbered tails.
    cache_seed = conn.execute(
        """
        SELECT DISTINCT r.icao24, r.callsign
        FROM aircraft_report_counts r
        LEFT JOIN aircraft_cache c ON c.icao24 = r.icao24
        WHERE r.callsign IS NOT NULL
          AND (c.registration IS NULL OR c.registration = '')
          AND r.callsign GLOB 'N[0-9]*'
        """
    ).fetchall()
    for row in cache_seed:
        if not dry_run:
            conn.execute(
                """
                INSERT INTO aircraft_cache (icao24, registration, last_updated)
                VALUES (?, ?, strftime('%s','now'))
                ON CONFLICT(icao24) DO UPDATE SET
                  registration = COALESCE(aircraft_cache.registration, excluded.registration),
                  last_updated = excluded.last_updated
                """,
                (row["icao24"], row["callsign"]),
            )
        stats["registrations_seeded"] += 1

    if not dry_run:
        conn.commit()
    return stats


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True, help="Path to circlejerk.sqlite3")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    db_path = Path(args.db)
    if not db_path.exists():
        print(f"db not found: {db_path}", file=sys.stderr)
        return 1

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        stats = backfill(conn, dry_run=args.dry_run)
    finally:
        conn.close()

    print(f"dry_run={args.dry_run}")
    for key, value in stats.items():
        print(f"  {key}: {value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
