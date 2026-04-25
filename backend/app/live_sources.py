from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any

import httpx

from .geo import Point, distance_nm
from .opensky import OpenSkyClient, OpenSkyRateLimited
from .settings import Settings

logger = logging.getLogger(__name__)


class LiveSourceUnavailable(Exception):
    pass


class LiveSourceRateLimited(LiveSourceUnavailable):
    def __init__(self, source: str, retry_after_seconds: int):
        super().__init__(f"{source} rate limited for {retry_after_seconds}s")
        self.source = source
        self.retry_after_seconds = retry_after_seconds


@dataclass(frozen=True)
class LiveFetchResult:
    source: str
    states: list[dict]


def bbox_center_radius_nm(
    bbox: tuple[float, float, float, float],
    max_radius_nm: float = 250.0,
) -> tuple[float, float, float]:
    lamin, lomin, lamax, lomax = bbox
    center = Point((lamin + lamax) / 2.0, (lomin + lomax) / 2.0)
    corners = (
        Point(lamin, lomin),
        Point(lamin, lomax),
        Point(lamax, lomin),
        Point(lamax, lomax),
    )
    radius = max(distance_nm(center, corner) for corner in corners)
    return center.lat, center.lon, min(max(radius, 1.0), max_radius_nm)


def bbox_distance_nm(a: tuple[float, float, float, float], b: tuple[float, float, float, float]) -> float:
    a_lat, a_lon, a_radius = bbox_center_radius_nm(a)
    b_lat, b_lon, b_radius = bbox_center_radius_nm(b)
    center_distance = distance_nm(Point(a_lat, a_lon), Point(b_lat, b_lon))
    return max(0.0, center_distance - a_radius - b_radius)


def readsb_number(value: Any) -> float | None:
    if value in (None, "", "null"):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def readsb_altitude_ft(value: Any) -> float | None:
    if value == "ground":
        return 0.0
    return readsb_number(value)


def readsb_timestamp(row: dict, payload_now: float) -> int:
    if payload_now > 10_000_000_000:
        payload_now = payload_now / 1000.0
    seen_pos = readsb_number(row.get("seen_pos"))
    seen = readsb_number(row.get("seen"))
    age = seen_pos if seen_pos is not None else seen
    return int(payload_now - max(age or 0.0, 0.0))


def parse_readsb_aircraft(row: dict, payload_now: float, source: str) -> dict | None:
    icao24 = row.get("hex")
    lat = readsb_number(row.get("lat"))
    lon = readsb_number(row.get("lon"))
    if not icao24 or lat is None or lon is None:
        return None

    callsign = (row.get("flight") or row.get("r") or "").strip() or None
    alt_baro = row.get("alt_baro")
    vertical_rate = readsb_number(row.get("baro_rate"))
    if vertical_rate is None:
        vertical_rate = readsb_number(row.get("geom_rate"))

    return {
        "icao24": str(icao24).lower().lstrip("~"),
        "callsign": callsign,
        "origin_country": row.get("country"),
        "timestamp": readsb_timestamp(row, payload_now),
        "lon": lon,
        "lat": lat,
        "baro_altitude_ft": readsb_altitude_ft(alt_baro),
        "on_ground": alt_baro == "ground" or row.get("ground") is True,
        "velocity_kt": readsb_number(row.get("gs")),
        "heading_deg": readsb_number(row.get("track")),
        "vertical_rate_fpm": vertical_rate,
        "geo_altitude_ft": readsb_altitude_ft(row.get("alt_geom")),
        "squawk": row.get("squawk"),
        "aircraft_type": row.get("t"),
        "registration": row.get("r"),
        "source": source,
    }


class ReadsbPointClient:
    def __init__(self, settings: Settings, source: str, base_url: str, path_style: str):
        self.settings = settings
        self.source = source
        self.base_url = base_url.rstrip("/")
        self.path_style = path_style
        self.client = httpx.AsyncClient(timeout=settings.live_source_timeout_seconds)

    async def close(self) -> None:
        await self.client.aclose()

    def url_for_bbox(self, bbox: tuple[float, float, float, float]) -> str:
        lat, lon, radius_nm = bbox_center_radius_nm(bbox, self.settings.live_source_max_radius_nm)
        if self.path_style == "lat_lon_dist":
            return f"{self.base_url}/v2/lat/{lat:.5f}/lon/{lon:.5f}/dist/{radius_nm:.1f}"
        return f"{self.base_url}/v2/point/{lat:.5f}/{lon:.5f}/{radius_nm:.1f}"

    async def states_bbox(self, bbox: tuple[float, float, float, float]) -> list[dict]:
        response = await self.client.get(
            self.url_for_bbox(bbox),
            headers={"User-Agent": f"circlejerk-prototype/0.1 ({self.settings.public_base_url})"},
        )
        if response.status_code == 429:
            retry = response.headers.get("Retry-After") or response.headers.get("X-Rate-Limit-Retry-After-Seconds")
            retry_after = int(float(retry)) if retry else self.settings.live_source_rate_limit_backoff_seconds
            raise LiveSourceRateLimited(self.source, retry_after)
        response.raise_for_status()
        payload = response.json()
        payload_now = float(payload.get("now") or time.time())
        states = []
        for row in payload.get("ac") or []:
            parsed = parse_readsb_aircraft(row, payload_now, self.source)
            if parsed:
                states.append(parsed)
        return states


class OpenSkyLiveClient:
    source = "opensky"

    def __init__(self, settings: Settings):
        self.client = OpenSkyClient(settings)

    @property
    def auth_failed(self) -> bool:
        return self.client.auth_failed

    async def close(self) -> None:
        await self.client.close()

    async def states_bbox(self, bbox: tuple[float, float, float, float]) -> list[dict]:
        return await self.client.states_bbox(bbox)


class LiveStateClient:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.backoff_until: dict[str, float] = {}
        self.clients = {
            "adsb_lol": ReadsbPointClient(settings, "adsb_lol", settings.adsb_lol_base_url, "lat_lon_dist"),
            "opensky": OpenSkyLiveClient(settings),
            "airplanes_live": ReadsbPointClient(
                settings,
                "airplanes_live",
                settings.airplanes_live_base_url,
                "point",
            ),
        }
        self.last_source: str | None = None

    async def close(self) -> None:
        for client in self.clients.values():
            await client.close()

    def priority(self) -> list[str]:
        return self.settings.live_source_priority_list() or ["adsb_lol", "opensky", "airplanes_live"]

    def is_backing_off(self, source: str) -> bool:
        return self.backoff_until.get(source, 0.0) > time.time()

    def mark_backoff(self, source: str, seconds: int | float) -> None:
        self.backoff_until[source] = time.time() + max(float(seconds), 1.0)

    async def states_bbox(self, bbox: tuple[float, float, float, float]) -> LiveFetchResult:
        failures: list[str] = []
        rate_limits: list[LiveSourceRateLimited] = []
        for source in self.priority():
            if self.is_backing_off(source):
                failures.append(f"{source}:backoff")
                continue
            client = self.clients[source]
            try:
                states = await client.states_bbox(bbox)
                self.last_source = source
                return LiveFetchResult(source=source, states=states)
            except LiveSourceRateLimited as exc:
                self.mark_backoff(source, exc.retry_after_seconds)
                rate_limits.append(exc)
                failures.append(f"{source}:rate_limited")
            except OpenSkyRateLimited as exc:
                self.mark_backoff(source, exc.retry_after_seconds)
                rate_limits.append(LiveSourceRateLimited(source, exc.retry_after_seconds))
                failures.append(f"{source}:rate_limited")
            except (httpx.HTTPError, ValueError) as exc:
                self.mark_backoff(source, self.settings.live_source_backoff_seconds)
                failures.append(f"{source}:{type(exc).__name__}")
                logger.warning("live source %s failed: %s", source, exc)

        if rate_limits and len(rate_limits) == len(self.priority()):
            raise LiveSourceRateLimited(
                "all_live_sources",
                max(exc.retry_after_seconds for exc in rate_limits),
            )
        raise LiveSourceUnavailable(", ".join(failures) or "no live sources configured")
