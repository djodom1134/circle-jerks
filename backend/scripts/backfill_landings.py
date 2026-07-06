"""One-time backfill: re-run runway-op detection over the 24 h track archive.

Landings were never detected before this feature shipped, so historical `/stats`
windows read ~100% "do not stop" until detection has been running for a while.
This re-runs the touch-and-go + landing detectors over the cold `track_archive`
for an airport and upserts any operations it finds. Idempotent via the stable
event ids + `ON CONFLICT(id) DO NOTHING`.

Usage:
    python -m scripts.backfill_landings --db data/circlejerk.sqlite3 --icao KBJC
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
import time
from collections import defaultdict
from pathlib import Path

from app import db
from app.detectors import detect_landings_over_period, detect_touch_and_gos_over_period
from app.geo import bbox_for_radius

RING_NM = 8.0


def backfill_landings(conn: sqlite3.Connection, icao: str, now: int, lookback_s: int = 86400) -> dict:
    icao = icao.upper()
    airport = db.get_airport(conn, icao)
    if airport is None:
        raise ValueError(f"airport not found: {icao}")
    runways = db.runways_for_airport(conn, icao)
    start_ts = now - lookback_s
    min_lat, min_lon, max_lat, max_lon = bbox_for_radius(airport.lat, airport.lon, RING_NM)
    rows = db.read_track_archive_bbox(conn, min_lat, max_lat, min_lon, max_lon, start_ts, now)

    tracks: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        tracks[row["icao24"]].append(row)

    events: list[dict] = []
    for track in tracks.values():
        events.extend(detect_touch_and_gos_over_period(track, airport, runways, start_ts, now))
        events.extend(detect_landings_over_period(track, airport, runways, start_ts, now))

    db.persist_events(conn, events)
    conn.commit()

    counts: dict[str, int] = defaultdict(int)
    for event in events:
        counts[event["type"]] += 1
    return {
        "aircraft_scanned": len(tracks),
        "events_found": len(events),
        "landings": counts.get("landing", 0),
        "touch_and_gos": counts.get("touch_and_go", 0),
        "low_approaches": counts.get("low_approach", 0),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True, help="Path to circlejerk.sqlite3")
    parser.add_argument("--icao", required=True, help="Airport ICAO, e.g. KBJC")
    parser.add_argument("--now", type=int, default=None, help="Override 'now' epoch (default: current time)")
    args = parser.parse_args()

    db_path = Path(args.db)
    if not db_path.exists():
        print(f"db not found: {db_path}", file=sys.stderr)
        return 1

    conn = db.connect(str(db_path))
    try:
        stats = backfill_landings(conn, args.icao, now=args.now or int(time.time()))
    finally:
        conn.close()

    for key, value in stats.items():
        print(f"  {key}: {value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
