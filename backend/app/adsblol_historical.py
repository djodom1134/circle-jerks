"""Historical track backfill via adsb.lol's per-aircraft trace endpoint.

Replaces FlightAware AeroAPI's airport-flights pattern with a free,
key-less alternative. The trade-off is the discovery model:
  - FA: enumerate flights touching an airport, fetch each.
  - adsb.lol: enumerate aircraft currently visible in a wide ring around
    the airport, fetch each one's last ~25h trace, filter to airport
    bbox + window.

This biases coverage toward aircraft still active in the broader region;
an aircraft that flew over 6h ago and has since gone idle won't be
captured. Live polling (the main loop) catches new traffic forward — so
the first visit to a fresh airport may miss some historical traffic, but
every subsequent visit benefits from the accumulating track_archive.

Endpoint: https://adsb.lol/data/traces/{last2}/trace_full_{hex}.json
License:  ODbL 1.0 (https://www.adsb.lol/docs/open-data/historical/)
"""

from __future__ import annotations

import asyncio
import gzip
import json
import logging
import time
from typing import Any

import httpx

from . import db
from .geo import bbox_for_radius
from .settings import Settings
from .store import Store

LOGGER = logging.getLogger(__name__)

ADSBLOL_LIVE_BASE = "https://api.adsb.lol/v2"
ADSBLOL_TRACE_BASE = "https://adsb.lol/data/traces"
USER_AGENT = "circlejerk-prototype/0.1 (+https://circlejerks.live)"

# How wide to sweep when discovering candidate aircraft. ~100nm covers
# climb-out / descent corridors for a major airport without grabbing
# half the country.
DEFAULT_DISCOVERY_RADIUS_NM = 100.0
# How tightly to filter trace points around the airport. Matches the
# 0.2°-bbox heuristic used by the cold-start gate elsewhere.
DEFAULT_FILTER_RADIUS_NM = 12.0
# Match FlightAware's 24h horizon so the cold-start fills the same
# retention window the rest of the app expects.
DEFAULT_HORIZON_SECONDS = 24 * 3600
# Be polite — adsb.lol is community-funded.
DEFAULT_MAX_CONCURRENT_TRACES = 5
DEFAULT_TRACE_TIMEOUT_SECONDS = 12.0
DEFAULT_DISCOVERY_TIMEOUT_SECONDS = 8.0


async def cold_start_backfill(
    store: Store,
    settings: Settings,
    *,
    airport_icao: str,
    horizon_seconds: int = DEFAULT_HORIZON_SECONDS,
    discovery_radius_nm: float = DEFAULT_DISCOVERY_RADIUS_NM,
    filter_radius_nm: float = DEFAULT_FILTER_RADIUS_NM,
    max_concurrent: int = DEFAULT_MAX_CONCURRENT_TRACES,
) -> dict:
    """Backfill the track_archive for a cold airport.

    Returns a result dict in the same shape as
    archive.gap_fill_via_flightaware_once so callers can log + treat
    uniformly.
    """
    airport_icao = airport_icao.strip().upper()
    with db.db_session(settings.database_path) as conn:
        airport = db.get_airport(conn, airport_icao)
    if not airport:
        return {
            "source": "adsblol",
            "skipped": "unknown_airport",
            "airport_icao": airport_icao,
        }

    now = int(time.time())
    window_start = now - horizon_seconds
    window_end = now
    bbox = bbox_for_radius(airport.lat, airport.lon, filter_radius_nm)

    hexes = await _discover_hexes(airport.lat, airport.lon, discovery_radius_nm)
    if not hexes:
        return {
            "source": "adsblol",
            "airport_icao": airport_icao,
            "aircraft_discovered": 0,
            "aircraft_fetched": 0,
            "samples_written": 0,
            "horizon_hours": horizon_seconds // 3600,
        }

    # Remember discovered hexes for ~24h so subsequent cold-start triggers
    # for this airport can fetch traces for aircraft we've previously
    # spotted, even after they leave the live ring. Best-effort — the
    # cold-start gate already prevents repeat runs within 24h, so the
    # main beneficiary is recovery after a Redis flush.
    await _remember_hexes(store, airport_icao, hexes)

    semaphore = asyncio.Semaphore(max(1, max_concurrent))

    async def _one(hex24: str) -> int:
        async with semaphore:
            try:
                return await _fetch_and_archive_trace(
                    settings,
                    hex24,
                    window_start=window_start,
                    window_end=window_end,
                    bbox=bbox,
                )
            except Exception:  # noqa: BLE001 — keep one bad hex from killing the run
                LOGGER.debug("adsblol trace failed for %s", hex24, exc_info=True)
                return 0

    results = await asyncio.gather(*(_one(h) for h in hexes))
    samples_written = sum(results)
    aircraft_fetched = sum(1 for n in results if n > 0)

    return {
        "source": "adsblol",
        "airport_icao": airport_icao,
        "aircraft_discovered": len(hexes),
        "aircraft_fetched": aircraft_fetched,
        "samples_written": samples_written,
        "horizon_hours": horizon_seconds // 3600,
    }


async def _discover_hexes(lat: float, lon: float, radius_nm: float) -> list[str]:
    url = f"{ADSBLOL_LIVE_BASE}/lat/{lat:.5f}/lon/{lon:.5f}/dist/{radius_nm:.0f}"
    try:
        async with httpx.AsyncClient(timeout=DEFAULT_DISCOVERY_TIMEOUT_SECONDS) as client:
            response = await client.get(url, headers={"User-Agent": USER_AGENT})
    except httpx.HTTPError as exc:
        LOGGER.warning("adsblol discovery failed: %s", exc)
        return []
    if response.status_code != 200:
        LOGGER.warning("adsblol discovery HTTP %d for (%s, %s)", response.status_code, lat, lon)
        return []
    try:
        payload = response.json()
    except ValueError:
        return []
    rows = payload.get("ac") or []
    hexes: list[str] = []
    seen: set[str] = set()
    for row in rows:
        raw = row.get("hex") if isinstance(row, dict) else None
        if not raw:
            continue
        h = str(raw).lower().lstrip("~").strip()
        if not h or h in seen:
            continue
        seen.add(h)
        hexes.append(h)
    return hexes


async def _fetch_and_archive_trace(
    settings: Settings,
    hex24: str,
    *,
    window_start: int,
    window_end: int,
    bbox: tuple[float, float, float, float],
) -> int:
    samples = await _fetch_trace_samples(
        hex24,
        window_start=window_start,
        window_end=window_end,
        bbox=bbox,
    )
    if not samples:
        return 0
    written = await asyncio.to_thread(
        _archive_samples_blocking,
        settings.database_path,
        hex24,
        samples,
    )
    return written


def _archive_samples_blocking(database_path: str, hex24: str, samples: list[dict]) -> int:
    with db.db_session(database_path) as conn:
        n = db.archive_track_samples(conn, hex24, samples)
        conn.commit()
    return n


async def _fetch_trace_samples(
    hex24: str,
    *,
    window_start: int,
    window_end: int,
    bbox: tuple[float, float, float, float],
) -> list[dict]:
    """Fetch trace_full for hex24 and return matching archive samples."""
    h = hex24.lower().lstrip("~").strip()
    if len(h) < 2:
        return []
    url = f"{ADSBLOL_TRACE_BASE}/{h[-2:]}/trace_full_{h}.json"
    try:
        async with httpx.AsyncClient(timeout=DEFAULT_TRACE_TIMEOUT_SECONDS) as client:
            response = await client.get(url, headers={"User-Agent": USER_AGENT})
    except httpx.HTTPError as exc:
        LOGGER.debug("adsblol trace fetch error for %s: %s", h, exc)
        return []
    if response.status_code == 404:
        return []
    if response.status_code != 200:
        LOGGER.debug("adsblol trace HTTP %d for %s", response.status_code, h)
        return []
    body = response.content
    # The trace files are served as raw gzip bytes (no content-encoding
    # header), so httpx doesn't auto-decompress. Detect the magic and
    # decompress manually.
    if body[:2] == b"\x1f\x8b":
        try:
            body = gzip.decompress(body)
        except OSError:
            return []
    try:
        payload = json.loads(body)
    except (ValueError, TypeError):
        return []
    return trace_to_samples(
        payload,
        window_start=window_start,
        window_end=window_end,
        bbox=bbox,
    )


def trace_to_samples(
    payload: dict,
    *,
    window_start: int,
    window_end: int,
    bbox: tuple[float, float, float, float],
) -> list[dict]:
    """Convert an adsb.lol trace_full payload into archive sample dicts.

    Exposed at module scope so unit tests can exercise the parser
    without hitting the network. Trace point shape (readsb):
        [time_offset_seconds, lat, lon, alt, gs, track_deg, baro_rate,
         details_dict | None, type_str, geo_alt, ...]
    """
    timestamp_base = payload.get("timestamp")
    trace = payload.get("trace") or []
    if not trace or timestamp_base is None:
        return []
    try:
        base = float(timestamp_base)
    except (TypeError, ValueError):
        return []
    south, west, north, east = bbox

    samples: list[dict] = []
    last_callsign: str | None = (payload.get("r") or "").strip() or None
    for point in trace:
        if not isinstance(point, list) or len(point) < 3:
            continue
        try:
            offset = float(point[0])
            lat = float(point[1])
            lon = float(point[2])
        except (TypeError, ValueError):
            continue
        ts = int(base + offset)
        if ts < window_start or ts > window_end:
            continue
        if not (south <= lat <= north and west <= lon <= east):
            continue
        baro_alt = _coerce_alt(point[3] if len(point) > 3 else None)
        track = _coerce_float(point[5] if len(point) > 5 else None)
        baro_rate = _coerce_float(point[6] if len(point) > 6 else None)
        details = point[7] if len(point) > 7 else None
        if isinstance(details, dict):
            cs = details.get("flight") or details.get("callsign")
            if cs:
                stripped = str(cs).strip()
                if stripped:
                    last_callsign = stripped
        geo_alt = _coerce_alt(point[9] if len(point) > 9 else None)
        samples.append({
            "timestamp": ts,
            "lat": lat,
            "lon": lon,
            "altitude_ft": baro_alt if baro_alt is not None else geo_alt,
            "baro_altitude_ft": baro_alt,
            "geo_altitude_ft": geo_alt,
            "heading_deg": track,
            "vertical_rate_fpm": baro_rate,
            "callsign": last_callsign,
            "in_window": True,
            "source": "adsblol_historical",
        })
    return samples


def _coerce_float(v: Any) -> float | None:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _coerce_alt(v: Any) -> float | None:
    if v == "ground":
        return 0.0
    return _coerce_float(v)


# ----- hex memory ---------------------------------------------------------

_HEX_MEMORY_TTL_SECONDS = 24 * 3600


async def _remember_hexes(store: Store, airport_icao: str, hexes: list[str]) -> None:
    if not hexes:
        return
    key = f"airport_hex_memory:{airport_icao.upper()}"
    try:
        existing = await store.get_cache(key)
    except Exception:  # noqa: BLE001
        existing = None
    seen: set[str] = set()
    if isinstance(existing, list):
        for h in existing:
            if isinstance(h, str):
                seen.add(h)
    seen.update(h.lower() for h in hexes)
    try:
        await store.set_cache(key, sorted(seen), _HEX_MEMORY_TTL_SECONDS)
    except Exception:  # noqa: BLE001 — memory is a nice-to-have, never block backfill on it
        pass
