from __future__ import annotations

import logging
import re

import httpx

from .settings import Settings

logger = logging.getLogger(__name__)

N_NUMBER_RE = re.compile(r"^N\d", re.IGNORECASE)
AIRLINE_IDENT_RE = re.compile(r"^[A-Z]{2,4}\d+[A-Z]?$")


def _position_to_sample(position: dict) -> dict:
    """Normalize an AeroAPI /flights/{id}/track position into our track sample shape.

    AeroAPI returns altitude in 100s of feet; heading in degrees; timestamp as
    ISO 8601. We never get a Mode-S hex here — that's resolved separately from
    the parent flight payload.
    """
    from datetime import datetime

    ts_str = position.get("timestamp")
    try:
        timestamp = int(datetime.fromisoformat(ts_str.replace("Z", "+00:00")).timestamp()) if ts_str else None
    except (ValueError, AttributeError):
        timestamp = None
    altitude_raw = position.get("altitude")
    altitude_ft = altitude_raw * 100 if isinstance(altitude_raw, (int, float)) else None
    return {
        "timestamp": timestamp,
        "lat": position.get("latitude"),
        "lon": position.get("longitude"),
        "altitude_ft": altitude_ft,
        "baro_altitude_ft": altitude_ft,
        "geo_altitude_ft": None,
        "heading_deg": position.get("heading"),
        "vertical_rate_fpm": None,
        "callsign": None,
        "in_window": True,
        "source": "flightaware_aeroapi",
    }


class FlightAwareClient:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.client = httpx.AsyncClient(timeout=settings.flightaware_timeout_seconds)
        self.auth_failed = False
        self.rate_limited = False

    async def close(self) -> None:
        await self.client.aclose()

    def _headers(self) -> dict[str, str]:
        return {
            "x-apikey": self.settings.flightaware_api_key or "",
            "Accept": "application/json",
        }

    @staticmethod
    def looks_like_airline_ident(callsign: str | None) -> bool:
        if not callsign:
            return False
        token = callsign.strip().upper()
        if not token:
            return False
        if N_NUMBER_RE.match(token):
            return False
        return bool(AIRLINE_IDENT_RE.match(token))

    @staticmethod
    def pick_flight(flights: list[dict], first_seen: int, last_seen: int) -> dict | None:
        """Choose the flight whose airborne window overlaps the observed sighting."""
        if not flights:
            return None

        def parse_ts(value: str | None) -> int | None:
            if not value:
                return None
            try:
                import datetime
                return int(datetime.datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp())
            except (ValueError, TypeError):
                return None

        candidates: list[tuple[int, dict]] = []
        for flight in flights:
            origin = flight.get("origin") or {}
            if not origin.get("code_icao") and not origin.get("city"):
                continue
            off_ts = parse_ts(flight.get("actual_off")) or parse_ts(flight.get("estimated_off")) or parse_ts(flight.get("scheduled_off"))
            on_ts = parse_ts(flight.get("actual_on")) or parse_ts(flight.get("estimated_on")) or parse_ts(flight.get("scheduled_on"))
            if off_ts is None and on_ts is None:
                continue
            window_start = off_ts or (on_ts - 3600 if on_ts else 0)
            window_end = on_ts or (off_ts + 6 * 3600 if off_ts else 0)
            # Score by overlap with [first_seen, last_seen]
            if window_end >= first_seen - 3600 and window_start <= last_seen + 3600:
                score = max(window_start, first_seen)
                candidates.append((score, flight))
            else:
                candidates.append((-abs(window_start - first_seen), flight))
        if not candidates:
            return flights[0] if (flights[0].get("origin") or {}).get("code_icao") else None
        candidates.sort(key=lambda row: row[0], reverse=True)
        return candidates[0][1]

    async def airport_flights(
        self,
        airport_icao: str,
        start_ts: int,
        end_ts: int,
        kind: str = "departures",
        max_pages: int = 2,
    ) -> list[dict]:
        """List flights at an airport in [start_ts, end_ts]. kind is 'departures' or 'arrivals'."""
        if not self.settings.flightaware_api_key or self.auth_failed or self.rate_limited:
            return []
        if kind not in {"departures", "arrivals"}:
            raise ValueError(f"kind must be departures or arrivals, got {kind!r}")
        from datetime import datetime, timezone

        start_iso = datetime.fromtimestamp(start_ts, tz=timezone.utc).isoformat().replace("+00:00", "Z")
        end_iso = datetime.fromtimestamp(end_ts, tz=timezone.utc).isoformat().replace("+00:00", "Z")
        try:
            response = await self.client.get(
                f"{self.settings.flightaware_base_url}/airports/{airport_icao}/flights/{kind}",
                params={"start": start_iso, "end": end_iso, "max_pages": max_pages},
                headers=self._headers(),
            )
        except httpx.HTTPError as exc:
            logger.warning("flightaware airport %s/%s request failed: %s", airport_icao, kind, exc)
            return []
        if response.status_code == 401:
            self.auth_failed = True
            return []
        if response.status_code == 429:
            self.rate_limited = True
            return []
        if response.status_code >= 400:
            logger.warning(
                "flightaware airport %s/%s HTTP %d", airport_icao, kind, response.status_code
            )
            return []
        try:
            data = response.json()
        except ValueError:
            return []
        return data.get(kind) or data.get("flights") or []

    async def flight_track(self, fa_flight_id: str) -> list[dict]:
        """Position history for a specific AeroAPI flight id. Returns
        normalized samples (timestamp/lat/lon/altitude_ft/heading_deg/...)
        with no icao24 — the caller resolves identity from the flight payload.
        """
        if not self.settings.flightaware_api_key or self.auth_failed or self.rate_limited:
            return []
        try:
            response = await self.client.get(
                f"{self.settings.flightaware_base_url}/flights/{fa_flight_id}/track",
                headers=self._headers(),
            )
        except httpx.HTTPError as exc:
            logger.warning("flightaware track request failed for %s: %s", fa_flight_id, exc)
            return []
        if response.status_code == 401:
            self.auth_failed = True
            return []
        if response.status_code == 429:
            self.rate_limited = True
            return []
        if response.status_code >= 400:
            return []
        try:
            data = response.json()
        except ValueError:
            return []
        return [_position_to_sample(pos) for pos in data.get("positions", [])]

    async def origin_for_callsign(self, callsign: str, first_seen: int, last_seen: int) -> dict | None:
        if not self.settings.flightaware_api_key or self.auth_failed or self.rate_limited:
            return None
        ident = callsign.strip().upper()
        if not self.looks_like_airline_ident(ident):
            return None
        try:
            response = await self.client.get(
                f"{self.settings.flightaware_base_url}/flights/{ident}",
                params={"max_pages": 1},
                headers=self._headers(),
            )
        except httpx.HTTPError as exc:
            logger.warning("flightaware request failed for %s: %s", ident, exc)
            return None
        if response.status_code == 401:
            self.auth_failed = True
            return None
        if response.status_code == 429:
            self.rate_limited = True
            return None
        if response.status_code >= 400:
            return None
        try:
            payload = response.json()
        except ValueError:
            return None
        flight = self.pick_flight(payload.get("flights") or [], first_seen, last_seen)
        if not flight:
            return None
        origin = flight.get("origin") or {}
        icao = (origin.get("code_icao") or "").strip().upper() or None
        city = (origin.get("city") or "").strip() or None
        if not icao and not city:
            return None
        label = None
        if city and icao:
            label = f"{city} ({icao})"
        elif icao:
            label = icao
        elif city:
            label = f"near {city}"
        return {
            "origin_city": city,
            "origin_airport_icao": icao,
            "origin_label": label or "unknown",
            "origin_source": "flightaware_aeroapi",
            "origin_confidence": "high" if icao else "medium",
            "origin_callsign": ident,
        }
