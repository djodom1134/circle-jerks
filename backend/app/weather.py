"""METAR wind summary, free + key-less via NOAA AviationWeather.

We fetch the last N hours of METAR observations for an airport, return the
freshest one as `current`, and compute a vector-averaged direction + scalar-
averaged speed over the window. METARs update every 20-60 minutes per
airport, so we cache for 5 min — that's well above the upstream cadence and
keeps our request volume to NOAA tiny.

NOTE on direction conventions: METAR `wdir` is the direction the wind is
COMING FROM (meteorological convention). The frontend renders an arrow that
points TOWARDS where the wind is going, so the caller subtracts 180° to flip.
We expose both as numbers; the API name `wind_from_dir_degrees` makes it
unambiguous.
"""

from __future__ import annotations

import logging
import math
import time
from typing import Iterable

import httpx

from .store import Store

LOGGER = logging.getLogger(__name__)

METAR_URL = "https://aviationweather.gov/api/data/metar"
CACHE_TTL_SECONDS = 5 * 60  # METARs update every 20-60 min upstream
REQUEST_TIMEOUT_SECONDS = 8.0


async def get_wind_summary(
    store: Store,
    airport_icao: str,
    hours: int,
) -> dict:
    """Return current + window-averaged wind for an airport.

    Cached in Redis for `CACHE_TTL_SECONDS` per (airport, hours) pair.
    Returns a dict with `current`, `average`, and an `error` key if the
    upstream is unreachable. Always returns a JSON-serializable dict — never
    raises into the caller.
    """
    icao = airport_icao.strip().upper()
    hours = max(1, min(int(hours), 24))
    cache_key = f"metar_wind:{icao}:{hours}"
    cached = await store.get_cache(cache_key)
    if isinstance(cached, dict):
        return cached

    try:
        rows = await _fetch_metars(icao, hours)
    except Exception as exc:  # noqa: BLE001 — never let weather break the page
        LOGGER.warning("METAR fetch failed for %s: %s", icao, exc)
        return {
            "airport_icao": icao,
            "hours": hours,
            "current": None,
            "average": None,
            "error": f"upstream_failed:{type(exc).__name__}",
        }

    if not rows:
        result = {
            "airport_icao": icao,
            "hours": hours,
            "current": None,
            "average": None,
            "error": "no_metars_available",
        }
        # Short cache on negative results so we retry sooner than 5 min.
        await store.set_cache(cache_key, result, 60)
        return result

    rows.sort(key=lambda r: int(r.get("obsTime") or 0))
    current = _row_to_current(rows[-1])
    average = _vector_average(rows)
    result = {
        "airport_icao": icao,
        "hours": hours,
        "current": current,
        "average": average,
        "error": None,
    }
    await store.set_cache(cache_key, result, CACHE_TTL_SECONDS)
    return result


async def _fetch_metars(airport_icao: str, hours: int) -> list[dict]:
    params = {"ids": airport_icao, "format": "json", "hours": hours}
    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SECONDS) as client:
        response = await client.get(
            METAR_URL,
            params=params,
            headers={"User-Agent": "circlejerk-prototype/0.1 (+https://circlejerks.live)"},
        )
    response.raise_for_status()
    data = response.json()
    if not isinstance(data, list):
        return []
    # Filter to rows that have at least direction + speed; AUTO METARs with
    # variable winds report wdir=0 or wdir="VRB" and wspd may still be useful.
    out: list[dict] = []
    for row in data:
        if not isinstance(row, dict):
            continue
        wdir = row.get("wdir")
        wspd = row.get("wspd")
        # Numeric wdir + non-null wspd is the minimum we use.
        if wspd is None:
            continue
        if isinstance(wdir, str):
            # "VRB" = variable; we keep the row for the speed scalar avg but
            # exclude from the vector direction average by zeroing out.
            row = {**row, "wdir": None}
        out.append(row)
    return out


def _row_to_current(row: dict) -> dict:
    wdir = row.get("wdir")
    return {
        "observed_at_unix": int(row.get("obsTime") or 0) or None,
        "report_time": row.get("reportTime"),
        "wind_from_dir_degrees": int(wdir) if isinstance(wdir, (int, float)) else None,
        "wind_speed_kt": _as_float(row.get("wspd")),
        "wind_gust_kt": _as_float(row.get("wgst")),
        "raw_metar": row.get("rawOb"),
    }


def _vector_average(rows: Iterable[dict]) -> dict:
    """Vector-average wind direction, scalar-average wind speed.

    Why vector-average direction: averaging 350° and 10° as scalars gives 180°
    (south); the vector average gives 0° (north) — which is correct. We project
    each METAR onto u/v components, average those, then convert back.
    """
    u_sum = 0.0
    v_sum = 0.0
    dir_n = 0
    speeds: list[float] = []
    gust_max: float | None = None
    sample_count = 0
    for row in rows:
        wspd = _as_float(row.get("wspd"))
        if wspd is None:
            continue
        sample_count += 1
        speeds.append(wspd)
        wgst = _as_float(row.get("wgst"))
        if wgst is not None and (gust_max is None or wgst > gust_max):
            gust_max = wgst
        wdir = row.get("wdir")
        if wdir is None:
            continue  # variable; contributes to speed avg but not direction
        try:
            d_rad = math.radians(float(wdir))
        except (TypeError, ValueError):
            continue
        # u = -wspd * sin(dir) (positive east), v = -wspd * cos(dir) (positive north).
        # Negative because wdir is the direction the wind is coming FROM.
        u_sum += -wspd * math.sin(d_rad)
        v_sum += -wspd * math.cos(d_rad)
        dir_n += 1

    avg_speed = sum(speeds) / len(speeds) if speeds else None
    if dir_n == 0 or (u_sum == 0 and v_sum == 0):
        avg_dir: int | None = None
    else:
        # u_sum / v_sum are already accumulated as "going-to" components
        # (negated above). atan2(u, v) in meteorological convention (clockwise
        # from north) gives the going-to bearing directly.
        going_to_rad = math.atan2(u_sum / dir_n, v_sum / dir_n)
        going_to_deg = (math.degrees(going_to_rad) + 360.0) % 360.0
        # Wind reports use "coming-from" direction; flip 180°.
        avg_dir = int(round((going_to_deg + 180.0) % 360.0))

    return {
        "wind_from_dir_degrees": avg_dir,
        "wind_speed_kt": round(avg_speed, 1) if avg_speed is not None else None,
        "wind_gust_max_kt": round(gust_max, 1) if gust_max is not None else None,
        "sample_count": sample_count,
    }


def _as_float(value) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
