"""Free callsign → origin/destination lookup via adsbdb.com.

Replaces the FlightAware AeroAPI per-call charge with a public, key-less
source for the airline-callsign origin path. Coverage is excellent for
scheduled airline flights; GA aircraft (N-numbers etc.) return no flight
route, in which case callers fall back to OpenSky's free flights_for_aircraft
endpoint or local ground-track detection.

Polite-use only — we cache successful lookups for 24h and negative results
for 10 min. One outbound request per cache miss; no auth header required.
"""

from __future__ import annotations

import logging
import re
from typing import Any

import httpx

from .settings import Settings
from .store import Store

LOGGER = logging.getLogger(__name__)

ADSBDB_BASE_URL = "https://api.adsbdb.com/v0"
REQUEST_TIMEOUT_SECONDS = 6.0
POSITIVE_CACHE_SECONDS = 24 * 3600
NEGATIVE_CACHE_SECONDS = 10 * 60

# A callsign worth looking up: 2-3 letter airline prefix + flight number.
# N-numbers (GA tail numbers) won't have flight-route data in adsbdb.
_AIRLINE_CALLSIGN_RE = re.compile(r"^[A-Z]{2,4}\d+[A-Z]?$")


def looks_like_airline_callsign(callsign: str | None) -> bool:
    if not callsign:
        return False
    token = callsign.strip().upper()
    if token.startswith("N") and len(token) >= 2 and token[1].isdigit():
        return False  # N-number, GA aircraft — adsbdb won't have it
    return bool(_AIRLINE_CALLSIGN_RE.match(token))


async def origin_for_callsign(
    store: Store,
    settings: Settings,
    callsign: str,
) -> dict | None:
    """Return an origin dict shaped like resolve_origin's helpers, or None.

    Output:
      {
        "origin_city": "Boston",
        "origin_airport_icao": "KBOS",
        "origin_label": "Boston (KBOS)",
        "origin_source": "adsbdb_flightroute",
        "origin_confidence": "high",
        "origin_callsign": "UAL237",
      }
    """
    ident = callsign.strip().upper() if callsign else ""
    if not looks_like_airline_callsign(ident):
        return None

    cache_key = f"adsbdb_origin:{ident}"
    cached = await store.get_cache(cache_key)
    if cached == "MISS":
        return None
    if isinstance(cached, dict):
        return cached

    payload = await _fetch_callsign(ident)
    if payload is None:
        # Network error — don't cache; retry next time.
        return None
    flight = (payload.get("response") or {}).get("flightroute") or {}
    origin = flight.get("origin") or {}
    icao = (origin.get("icao_code") or "").strip().upper() or None
    city = (origin.get("municipality") or "").strip() or None
    if not icao and not city:
        await store.set_cache(cache_key, "MISS", NEGATIVE_CACHE_SECONDS)
        return None
    label = (
        f"{city} ({icao})" if city and icao
        else icao or (f"near {city}" if city else "unknown")
    )
    result = {
        "origin_city": city,
        "origin_airport_icao": icao,
        "origin_label": label or "unknown",
        "origin_source": "adsbdb_flightroute",
        "origin_confidence": "high" if icao else "medium",
        "origin_callsign": ident,
    }
    await store.set_cache(cache_key, result, POSITIVE_CACHE_SECONDS)
    return result


async def _fetch_callsign(callsign: str) -> dict[str, Any] | None:
    url = f"{ADSBDB_BASE_URL}/callsign/{callsign}"
    try:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SECONDS) as client:
            response = await client.get(
                url,
                headers={"User-Agent": "circlejerk-prototype/0.1 (+https://circlejerks.live)"},
            )
    except httpx.HTTPError as exc:
        LOGGER.warning("adsbdb fetch failed for %s: %s", callsign, exc)
        return None
    if response.status_code == 404:
        # Treat 404 like "no flight route" — cache the miss.
        return {"response": {}}
    if response.status_code != 200:
        LOGGER.warning("adsbdb HTTP %d for %s", response.status_code, callsign)
        return None
    try:
        return response.json()
    except ValueError:
        return None
