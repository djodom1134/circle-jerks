from __future__ import annotations

import logging
import re

import httpx

from .settings import Settings

logger = logging.getLogger(__name__)

N_NUMBER_RE = re.compile(r"^N\d", re.IGNORECASE)
AIRLINE_IDENT_RE = re.compile(r"^[A-Z]{2,4}\d+[A-Z]?$")


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
