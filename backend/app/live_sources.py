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


class LiveSourceStale(LiveSourceUnavailable):
    def __init__(self, source: str, age_seconds: float):
        super().__init__(f"{source} payload is {age_seconds:.0f}s stale")
        self.source = source
        self.age_seconds = age_seconds


def readsb_payload_aircraft(payload: dict) -> list[dict]:
    """Pull aircraft rows from any readsb-shaped payload (ac, aircraft, states)."""
    return payload.get("ac") or payload.get("aircraft") or payload.get("states") or []


def readsb_payload_now(payload: dict, fallback: float | None = None) -> float:
    raw = payload.get("now") or payload.get("ctime")
    if raw is None:
        return float(fallback if fallback is not None else time.time())
    value = float(raw)
    if value > 10_000_000_000:
        value = value / 1000.0
    return value


class ReadsbPointClient:
    def __init__(
        self,
        settings: Settings,
        source: str,
        base_url: str,
        path_style: str,
        extra_headers: dict[str, str] | None = None,
    ):
        self.settings = settings
        self.source = source
        self.base_url = base_url.rstrip("/")
        self.path_style = path_style
        self.extra_headers = dict(extra_headers or {})
        self.client = httpx.AsyncClient(timeout=settings.live_source_timeout_seconds)

    async def close(self) -> None:
        await self.client.aclose()

    def url_for_bbox(self, bbox: tuple[float, float, float, float]) -> str:
        lat, lon, radius_nm = bbox_center_radius_nm(bbox, self.settings.live_source_max_radius_nm)
        if self.path_style == "lat_lon_dist":
            return f"{self.base_url}/v2/lat/{lat:.5f}/lon/{lon:.5f}/dist/{radius_nm:.1f}"
        return f"{self.base_url}/v2/point/{lat:.5f}/{lon:.5f}/{radius_nm:.1f}"

    async def states_bbox(self, bbox: tuple[float, float, float, float]) -> list[dict]:
        headers = {
            "User-Agent": f"circlejerk-prototype/0.1 ({self.settings.public_base_url})",
            **self.extra_headers,
        }
        response = await self.client.get(self.url_for_bbox(bbox), headers=headers)
        if response.status_code == 429:
            retry = response.headers.get("Retry-After") or response.headers.get("X-Rate-Limit-Retry-After-Seconds")
            retry_after = int(float(retry)) if retry else self.settings.live_source_rate_limit_backoff_seconds
            raise LiveSourceRateLimited(self.source, retry_after)
        response.raise_for_status()
        payload = response.json()
        payload_now = readsb_payload_now(payload)
        rows = readsb_payload_aircraft(payload)
        states = []
        for row in rows:
            parsed = parse_readsb_aircraft(row, payload_now, self.source)
            if parsed:
                states.append(parsed)
        max_stale = max(0, self.settings.live_source_max_staleness_seconds)
        if max_stale and len(states) >= max(1, self.settings.live_source_min_aircraft_for_freshness):
            age = time.time() - payload_now
            if age > max_stale:
                raise LiveSourceStale(self.source, age)
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


def build_live_source_client(source: str, settings: Settings):
    if source == "adsb_lol":
        return ReadsbPointClient(settings, "adsb_lol", settings.adsb_lol_base_url, "lat_lon_dist")
    if source == "adsb_fi":
        return ReadsbPointClient(settings, "adsb_fi", settings.adsb_fi_base_url, "lat_lon_dist")
    if source == "airplanes_live":
        return ReadsbPointClient(settings, "airplanes_live", settings.airplanes_live_base_url, "point")
    if source == "self_hosted":
        base_url = settings.self_hosted_feeder_base_url
        if not base_url:
            return None
        return ReadsbPointClient(
            settings,
            "self_hosted",
            base_url,
            settings.self_hosted_feeder_path_style,
        )
    if source == "adsbx":
        if not settings.adsbx_rapidapi_key:
            return None
        return ReadsbPointClient(
            settings,
            "adsbx",
            f"https://{settings.adsbx_rapidapi_host}",
            "lat_lon_dist",
            extra_headers={
                "x-rapidapi-key": settings.adsbx_rapidapi_key,
                "x-rapidapi-host": settings.adsbx_rapidapi_host,
            },
        )
    if source == "opensky":
        return OpenSkyLiveClient(settings)
    return None


@dataclass
class SourceHealth:
    success_count: int = 0
    error_count: int = 0
    last_success_ts: float | None = None
    last_error_ts: float | None = None
    last_error: str | None = None
    last_states_count: int | None = None
    last_latency_ms: int | None = None

    def to_dict(self) -> dict:
        return {
            "success_count": self.success_count,
            "error_count": self.error_count,
            "last_success_ts": int(self.last_success_ts) if self.last_success_ts else None,
            "last_error_ts": int(self.last_error_ts) if self.last_error_ts else None,
            "last_error": self.last_error,
            "last_states_count": self.last_states_count,
            "last_latency_ms": self.last_latency_ms,
        }


class LiveStateClient:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.backoff_until: dict[str, float] = {}
        self.clients: dict[str, Any] = {}
        known_sources = {"adsb_lol", "adsb_fi", "airplanes_live", "opensky", "self_hosted", "adsbx"}
        for source in known_sources:
            instance = build_live_source_client(source, settings)
            if instance is not None:
                self.clients[source] = instance
        self.health: dict[str, SourceHealth] = {source: SourceHealth() for source in self.clients}
        self.last_source: str | None = None

    async def close(self) -> None:
        for client in self.clients.values():
            await client.close()

    def priority(self) -> list[str]:
        configured = self.settings.live_source_priority_list()
        usable = [source for source in configured if source in self.clients]
        if usable:
            return usable
        return [source for source in ("adsb_lol", "adsb_fi", "airplanes_live", "opensky") if source in self.clients]

    def is_backing_off(self, source: str) -> bool:
        return self.backoff_until.get(source, 0.0) > time.time()

    def mark_backoff(self, source: str, seconds: int | float) -> None:
        self.backoff_until[source] = time.time() + max(float(seconds), 1.0)

    def health_snapshot(self) -> dict[str, dict]:
        now = time.time()
        return {
            source: {
                **stats.to_dict(),
                "available": source in self.clients,
                "backoff_remaining_seconds": max(0, int(self.backoff_until.get(source, 0.0) - now)),
            }
            for source, stats in self.health.items()
        }

    def _record_success(self, source: str, states: list[dict], latency_ms: int) -> None:
        stats = self.health[source]
        stats.success_count += 1
        stats.last_success_ts = time.time()
        stats.last_states_count = len(states)
        stats.last_latency_ms = latency_ms
        stats.last_error = None

    def _record_error(self, source: str, message: str) -> None:
        stats = self.health[source]
        stats.error_count += 1
        stats.last_error_ts = time.time()
        stats.last_error = message[:200]

    async def states_bbox(self, bbox: tuple[float, float, float, float]) -> LiveFetchResult:
        failures: list[str] = []
        rate_limits: list[LiveSourceRateLimited] = []
        for source in self.priority():
            if self.is_backing_off(source):
                failures.append(f"{source}:backoff")
                continue
            client = self.clients[source]
            started = time.monotonic()
            try:
                states = await client.states_bbox(bbox)
                latency_ms = int((time.monotonic() - started) * 1000)
                self.last_source = source
                self._record_success(source, states, latency_ms)
                return LiveFetchResult(source=source, states=states)
            except LiveSourceStale as exc:
                self.mark_backoff(source, self.settings.live_source_backoff_seconds)
                failures.append(f"{source}:stale_{int(exc.age_seconds)}s")
                self._record_error(source, str(exc))
                logger.warning("live source %s returned stale data (%.0fs)", source, exc.age_seconds)
            except LiveSourceRateLimited as exc:
                self.mark_backoff(source, exc.retry_after_seconds)
                rate_limits.append(exc)
                failures.append(f"{source}:rate_limited")
                self._record_error(source, f"rate limited {exc.retry_after_seconds}s")
            except OpenSkyRateLimited as exc:
                self.mark_backoff(source, exc.retry_after_seconds)
                rate_limits.append(LiveSourceRateLimited(source, exc.retry_after_seconds))
                failures.append(f"{source}:rate_limited")
                self._record_error(source, f"rate limited {exc.retry_after_seconds}s")
            except (httpx.HTTPError, ValueError) as exc:
                self.mark_backoff(source, self.settings.live_source_backoff_seconds)
                failures.append(f"{source}:{type(exc).__name__}")
                self._record_error(source, f"{type(exc).__name__}: {exc}")
                logger.warning("live source %s failed: %s", source, exc)

        if rate_limits and len(rate_limits) == len(self.priority()):
            raise LiveSourceRateLimited(
                "all_live_sources",
                max(exc.retry_after_seconds for exc in rate_limits),
            )
        raise LiveSourceUnavailable(", ".join(failures) or "no live sources configured")
