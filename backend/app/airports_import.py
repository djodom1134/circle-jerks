"""Import US airports from the public-domain OurAirports dataset.

The app originally shipped with ~10 hand-seeded Colorado airports. A user
anywhere else (e.g. Texas) resolves "nearest airport" to a Colorado field
hundreds of miles away, so the monitored bbox has no local traffic and the map
shows nothing. This importer loads the full set of US public-use airports so
"nearest airport" actually lands somewhere useful.

Source: https://davidmegginson.github.io/ourairports-data/airports.csv
  (OurAirports, public domain)

Safe to re-run: uses INSERT OR IGNORE so the curated Colorado seed (which has
correct is_towered flags + complaint forms) is never clobbered. Writes in
small committed chunks with a sleep between them so it never holds the SQLite
write lock long enough to starve the API workers.
"""

from __future__ import annotations

import asyncio
import csv
import io
import logging
import time

import httpx

from . import db

LOGGER = logging.getLogger(__name__)

OURAIRPORTS_URL = "https://davidmegginson.github.io/ourairports-data/airports.csv"
DOWNLOAD_TIMEOUT_SECONDS = 120.0
# Only public-use airports a GA aircraft would actually fly a pattern at.
WANTED_TYPES = {"small_airport", "medium_airport", "large_airport"}
CHUNK_SIZE = 500
CHUNK_SLEEP_SECONDS = 0.1
_BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


async def import_us_airports(database_path: str, *, country: str = "US") -> dict:
    """Download + upsert US airports. Returns a stats dict."""
    t0 = time.monotonic()
    async with httpx.AsyncClient(timeout=DOWNLOAD_TIMEOUT_SECONDS, follow_redirects=True) as client:
        resp = await client.get(OURAIRPORTS_URL, headers={"User-Agent": _BROWSER_UA, "Accept": "*/*"})
        resp.raise_for_status()
        text = resp.text

    reader = csv.DictReader(io.StringIO(text))
    rows: list[tuple] = []
    considered = 0
    for rec in reader:
        if rec.get("iso_country") != country:
            continue
        if rec.get("type") not in WANTED_TYPES:
            continue
        # Prefer ICAO-style ident; fall back to gps_code / local_code.
        icao = (rec.get("ident") or rec.get("gps_code") or rec.get("local_code") or "").strip().upper()
        if not icao:
            continue
        try:
            lat = float(rec["latitude_deg"])
            lon = float(rec["longitude_deg"])
        except (ValueError, KeyError, TypeError):
            continue
        considered += 1
        elevation_raw = rec.get("elevation_ft") or ""
        try:
            elevation = int(float(elevation_raw)) if elevation_raw else 0
        except ValueError:
            elevation = 0
        name = (rec.get("name") or icao).strip()
        municipality = (rec.get("municipality") or "").strip()
        region = (rec.get("iso_region") or "").strip()
        # `city` column is NOT NULL — fall back to region, then country.
        city = municipality or region or country
        iata = (rec.get("iata_code") or "").strip().upper() or None
        rows.append((icao, iata, name, city, country, lat, lon, elevation, 0))

    inserted = 0
    chunk: list[tuple] = []

    def flush(batch: list[tuple]) -> int:
        if not batch:
            return 0
        with db.db_session(database_path) as conn:
            cur = conn.executemany(
                """
                INSERT OR IGNORE INTO airports
                (icao, iata, name, city, country, lat, lon, elevation_ft, is_towered)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                batch,
            )
            conn.commit()
            return cur.rowcount or 0

    for row in rows:
        chunk.append(row)
        if len(chunk) >= CHUNK_SIZE:
            inserted += flush(chunk)
            chunk = []
            await asyncio.sleep(CHUNK_SLEEP_SECONDS)  # yield + release write lock
    inserted += flush(chunk)

    with db.db_session(database_path) as conn:
        total = conn.execute("SELECT COUNT(*) FROM airports").fetchone()[0]

    duration = time.monotonic() - t0
    stats = {
        "considered": considered,
        "inserted": inserted,
        "total_airports": total,
        "duration_seconds": round(duration, 1),
    }
    LOGGER.info("US airports import: %s", stats)
    return stats
