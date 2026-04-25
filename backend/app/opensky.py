from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

import httpx

from .geo import meters_to_feet, mps_to_fpm, mps_to_knots
from .settings import Settings

TOKEN_URL = "https://auth.opensky-network.org/auth/realms/opensky-network/protocol/openid-connect/token"
API_BASE = "https://opensky-network.org/api"


@dataclass
class OpenSkyToken:
    access_token: str
    expires_at: int


class OpenSkyRateLimited(Exception):
    def __init__(self, retry_after_seconds: int = 60):
        super().__init__(f"OpenSky rate limited for {retry_after_seconds}s")
        self.retry_after_seconds = retry_after_seconds


class OpenSkyClient:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.token: OpenSkyToken | None = None
        self.auth_failed = False
        self.client = httpx.AsyncClient(timeout=settings.opensky_timeout_seconds)

    async def close(self) -> None:
        await self.client.aclose()

    async def _auth_header(self) -> dict[str, str]:
        client_id, client_secret = self.settings.opensky_credentials()
        if not client_id or not client_secret or self.auth_failed:
            return {}
        if self.token and self.token.expires_at - 30 > int(time.time()):
            return {"Authorization": f"Bearer {self.token.access_token}"}
        response = await self.client.post(
            TOKEN_URL,
            data={
                "grant_type": "client_credentials",
                "client_id": client_id,
                "client_secret": client_secret,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        if response.status_code in {400, 401, 403}:
            self.auth_failed = True
            return {}
        response.raise_for_status()
        payload = response.json()
        self.token = OpenSkyToken(
            access_token=payload["access_token"],
            expires_at=int(time.time()) + int(payload.get("expires_in", 1800)),
        )
        return {"Authorization": f"Bearer {self.token.access_token}"}

    async def states_bbox(self, bbox: tuple[float, float, float, float], at_ts: int | None = None) -> list[dict]:
        lamin, lomin, lamax, lomax = bbox
        headers = await self._auth_header()
        if all(self.settings.opensky_credentials()) and self.auth_failed:
            return []
        if at_ts is not None and not headers:
            return []
        params = {"lamin": lamin, "lomin": lomin, "lamax": lamax, "lomax": lomax}
        if at_ts is not None:
            params["time"] = at_ts
        response = await self.client.get(
            f"{API_BASE}/states/all",
            params=params,
            headers=headers,
        )
        if response.status_code == 429:
            retry = response.headers.get("X-Rate-Limit-Retry-After-Seconds") or response.headers.get("Retry-After") or "60"
            raise OpenSkyRateLimited(int(float(retry)))
        response.raise_for_status()
        payload = response.json()
        now = int(payload.get("time") or time.time())
        states = []
        for row in payload.get("states") or []:
            parsed = parse_state_vector(row, now)
            if parsed:
                states.append(parsed)
        return states

    async def flights_for_aircraft(self, icao24: str, begin_ts: int, end_ts: int) -> list[dict]:
        headers = await self._auth_header()
        if all(self.settings.opensky_credentials()) and self.auth_failed:
            return []
        response = await self.client.get(
            f"{API_BASE}/flights/aircraft",
            params={"icao24": icao24.lower(), "begin": begin_ts, "end": end_ts},
            headers=headers,
        )
        if response.status_code in {401, 403}:
            self.auth_failed = True
            return []
        if response.status_code in {404, 429}:
            return []
        response.raise_for_status()
        return response.json()

    async def track_for_aircraft(self, icao24: str, at_ts: int = 0) -> dict | None:
        headers = await self._auth_header()
        if all(self.settings.opensky_credentials()) and self.auth_failed:
            return None
        response = await self.client.get(
            f"{API_BASE}/tracks/all",
            params={"icao24": icao24.lower(), "time": at_ts},
            headers=headers,
        )
        if response.status_code in {401, 403}:
            self.auth_failed = True
            return None
        if response.status_code in {404, 429}:
            return None
        response.raise_for_status()
        return response.json()


def parse_state_vector(row: list[Any], fallback_ts: int) -> dict | None:
    if len(row) < 17:
        return None
    icao24 = row[0]
    lon = row[5]
    lat = row[6]
    if not icao24 or lat is None or lon is None:
        return None
    callsign = row[1].strip() if row[1] else None
    return {
        "icao24": str(icao24).lower(),
        "callsign": callsign,
        "origin_country": row[2],
        "timestamp": int(row[4] or row[3] or fallback_ts),
        "lon": float(lon),
        "lat": float(lat),
        "baro_altitude_ft": meters_to_feet(row[7]),
        "on_ground": bool(row[8]),
        "velocity_kt": mps_to_knots(row[9]),
        "heading_deg": row[10],
        "vertical_rate_fpm": mps_to_fpm(row[11]),
        "geo_altitude_ft": meters_to_feet(row[13]),
        "source": "opensky",
    }
