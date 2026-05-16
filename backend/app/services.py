from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import httpx

from . import db
from .db import Airport
from .detectors import closest_over_user_rows, detect_events, detect_events_over_period, event_counts, pass_geometry_key
from .domain import ScanParams, hour_label, local_time_label, location_hash, monitor_hash
from .geo import Point, bbox_for_radius, bbox_union, distance_nm, sq_degrees
from .llm import (
    AggregateComplaintContext,
    ComplaintContext,
    MessagePreferences,
    build_aggregate_prompt,
    build_prompt,
    deterministic_aggregate_description,
    deterministic_description,
    generate_with_groq,
)
from .opensky import OpenSkyClient, OpenSkyRateLimited
from .scoring import event_histogram, offender_rows, quiet_hour
from .settings import Settings
from .store import Store
from .tone import ToneSliders
from .windows import WindowRange, resolve_window

ORIGIN_GROUND_AIRPORT_MAX_NM = 3.0
ORIGIN_TRACK_START_AIRPORT_MAX_NM = 8.0
ORIGIN_FIRST_SEEN_AIRPORT_MAX_NM = 8.0
ORIGIN_GROUND_SPEED_MAX_KT = 45.0
ORIGIN_LOOKBACK_SECONDS = 12 * 3600
ORIGIN_LOOKAHEAD_SECONDS = 2 * 3600
ORIGIN_STRONG_CACHE_SECONDS = 24 * 3600
ORIGIN_WEAK_CACHE_SECONDS = 10 * 60
ORIGIN_CITY_CACHE_SECONDS = 7 * 24 * 3600
ELEVATION_CACHE_SECONDS = 7 * 24 * 3600
LIVE_DETECTOR_LOOKBACK_SECONDS = 45 * 60
BBOX_FILTER_PADDING_DEGREES = 0.03


def monitor_for_params(params: ScanParams, airport: Airport) -> dict:
    p = params.normalized()
    airport_box = bbox_for_radius(airport.lat, airport.lon, p.ring_nm)
    user_box = bbox_for_radius(p.user_lat, p.user_lon, p.pass_radius_nm)
    bbox = bbox_union(airport_box, user_box)
    return {
        "hash": monitor_hash(p),
        "airport_icao": p.airport_icao,
        "user_lat": p.user_lat,
        "user_lon": p.user_lon,
        "user_elevation_ft": p.user_elevation_ft,
        "ring_nm": p.ring_nm,
        "pass_radius_nm": p.pass_radius_nm,
        "pass_ceiling_ft": p.pass_ceiling_ft,
        "bbox": bbox,
        "bbox_sq_degrees": round(sq_degrees(bbox), 4),
        "registered_at": int(datetime.now(timezone.utc).timestamp()),
    }


async def register_monitor(store: Store, settings: Settings, params: ScanParams, airport: Airport) -> str:
    monitor = monitor_for_params(params, airport)
    await store.register_monitor(monitor["hash"], monitor, settings.monitor_ttl_seconds)
    return monitor["hash"]


async def fetch_user_elevation_ft(store: Store, settings: Settings, lat: float, lon: float) -> int | None:
    cache_key = f"elevation:{location_hash(lat, lon)}"
    cached = await store.get_cache(cache_key)
    if isinstance(cached, int):
        return cached
    if isinstance(cached, float):
        return int(round(cached))
    try:
        async with httpx.AsyncClient(timeout=min(settings.request_timeout_seconds, 5.0)) as client:
            response = await client.get(
                "https://epqs.nationalmap.gov/v1/json",
                params={
                    "x": lon,
                    "y": lat,
                    "units": "Feet",
                    "wkid": 4326,
                    "includeDate": "false",
                },
                headers={"User-Agent": f"circlejerk-prototype/0.1 ({settings.public_base_url})"},
            )
        if response.status_code >= 400:
            return None
        value = response.json().get("value")
        if value is None:
            return None
        elevation_ft = int(round(float(value)))
        await store.set_cache(cache_key, elevation_ft, ELEVATION_CACHE_SECONDS)
        return elevation_ft
    except (httpx.HTTPError, TypeError, ValueError):
        return None


async def params_with_user_elevation(store: Store, settings: Settings, params: ScanParams) -> ScanParams:
    p = params.normalized()
    if p.user_elevation_ft is not None:
        return p
    elevation_ft = await fetch_user_elevation_ft(store, settings, p.user_lat, p.user_lon)
    if elevation_ft is None:
        return p
    return p.model_copy(update={"user_elevation_ft": elevation_ft})


async def run_detectors_for_monitor(
    store: Store,
    settings: Settings,
    conn,
    monitor: dict,
    start_ts: int | None = None,
    end_ts: int | None = None,
) -> int:
    airport = db.get_airport(conn, monitor["airport_icao"])
    if not airport:
        return 0
    runways = db.runways_for_airport(conn, airport.icao)
    params = ScanParams(
        airport_icao=airport.icao,
        user_lat=monitor["user_lat"],
        user_lon=monitor["user_lon"],
        user_elevation_ft=monitor.get("user_elevation_ft"),
        ring_nm=monitor["ring_nm"],
        pass_radius_nm=monitor["pass_radius_nm"],
        pass_ceiling_ft=monitor["pass_ceiling_ft"],
    )
    written = 0
    now = int(datetime.now(timezone.utc).timestamp())
    detector_end = end_ts or now
    max_lookback = max(
        LIVE_DETECTOR_LOOKBACK_SECONDS,
        settings.opensky_historical_limit_seconds if settings.opensky_historical_enabled else 0,
    )
    detector_start = max(
        start_ts if start_ts is not None else detector_end - LIVE_DETECTOR_LOOKBACK_SECONDS,
        detector_end - max_lookback,
    )
    for icao24 in await store.list_aircraft():
        track = await store.get_track(icao24, detector_start - 20 * 60, detector_end)
        if not track_intersects_bbox(track, tuple(monitor["bbox"])):
            continue
        events = (
            detect_events_over_period(track, airport, runways, params, start_ts, end_ts)
            if start_ts is not None and end_ts is not None
            else detect_events(track, airport, runways, params)
        )
        for event in events:
            if not await store.event_exists(monitor["hash"], event["id"]):
                await store.add_event(monitor["hash"], event, settings.event_ttl_seconds)
                written += 1
    return written


def track_intersects_bbox(track: list[dict], bbox: tuple[float, float, float, float]) -> bool:
    lamin, lomin, lamax, lomax = bbox
    lamin -= BBOX_FILTER_PADDING_DEGREES
    lomin -= BBOX_FILTER_PADDING_DEGREES
    lamax += BBOX_FILTER_PADDING_DEGREES
    lomax += BBOX_FILTER_PADDING_DEGREES
    return any(
        sample.get("lat") is not None
        and sample.get("lon") is not None
        and lamin <= float(sample["lat"]) <= lamax
        and lomin <= float(sample["lon"]) <= lomax
        for sample in track
    )


def historical_snapshot_times(window: WindowRange, settings: Settings) -> list[int]:
    limit = max(0, settings.opensky_historical_limit_seconds)
    step = max(5, settings.opensky_historical_step_seconds)
    start = max(window.start_ts, window.end_ts - limit)
    first = ((start + step - 1) // step) * step
    last = (window.end_ts // step) * step
    return list(range(first, last + 1, step)) if first <= last else []


async def backfill_historical_states(
    store: Store,
    settings: Settings,
    monitor: dict,
    window: WindowRange,
) -> dict:
    coverage_start = max(window.start_ts, window.end_ts - max(0, settings.opensky_historical_limit_seconds))
    base = {
        "enabled": True,
        "available": True,
        "requested": 0,
        "fetched": 0,
        "skipped_cached": 0,
        "remaining": 0,
        "resolution_seconds": settings.opensky_historical_step_seconds,
        "limit_seconds": settings.opensky_historical_limit_seconds,
        "coverage_start_ts": coverage_start,
        "coverage_end_ts": window.end_ts,
        "coverage_complete": False,
        "complete_for_requested_window": False,
    }
    if not settings.opensky_historical_enabled:
        return {
            **base,
            "enabled": False,
            "available": False,
            "reason": "Historical coverage is built from live polling for this airport.",
        }
    if not all(settings.opensky_credentials()):
        return {
            **base,
            "enabled": True,
            "available": False,
            "reason": "OpenSky authentication is required for historical state vectors.",
        }
    historical_unavailable = await store.get_cache("opensky_historical_unavailable")
    if isinstance(historical_unavailable, dict):
        return {
            **base,
            "enabled": True,
            "available": False,
            "reason": historical_unavailable.get("reason") or "OpenSky historical state vectors are unavailable.",
        }
    if await store.get_cache("opensky_auth_failed"):
        return {
            **base,
            "enabled": True,
            "available": False,
            "reason": "OpenSky rejected the configured OAuth credentials. Use an OpenSky OAuth API client_id/client_secret, not an account password.",
        }
    if await store.get_cache(f"opensky_state_backoff:{monitor['hash']}"):
        return {**base, "backing_off": True}

    timestamps = historical_snapshot_times(window, settings)
    if not timestamps:
        return {
            **base,
            "coverage_complete": True,
            "complete_for_requested_window": coverage_start <= window.start_ts,
        }

    missing = []
    skipped_cached = 0
    for ts in timestamps:
        cache_key = f"opensky_state:{monitor['hash']}:{ts}"
        if await store.get_cache(cache_key):
            skipped_cached += 1
            continue
        missing.append((ts, cache_key))

    fill_gate_key = f"opensky_state_fill_recent:{monitor['hash']}"
    if missing and await store.get_cache(fill_gate_key):
        return {
            **base,
            "enabled": True,
            "requested": len(timestamps),
            "fetched": 0,
            "skipped_cached": skipped_cached,
            "remaining": len(missing),
            "throttled": True,
            "coverage_complete": False,
            "complete_for_requested_window": False,
        }

    missing.sort(key=lambda row: row[0], reverse=True)
    selected = missing[: max(0, settings.opensky_historical_snapshots_per_scan)]
    if not selected:
        return {
            **base,
            "enabled": True,
            "requested": len(timestamps),
            "fetched": 0,
            "skipped_cached": skipped_cached,
            "remaining": len(missing),
            "coverage_complete": len(missing) == 0,
            "complete_for_requested_window": len(missing) == 0 and coverage_start <= window.start_ts,
        }

    fetched = 0
    states_seen = 0
    retry_after_seconds = None
    historical_error_reason = None
    opensky = OpenSkyClient(settings)
    try:
        for ts, cache_key in selected:
            states = await opensky.states_bbox(tuple(monitor["bbox"]), at_ts=ts)
            if opensky.auth_failed:
                await store.set_cache("opensky_auth_failed", True, 600)
                return {
                    **base,
                    "enabled": True,
                    "available": False,
                    "reason": "OpenSky rejected the configured OAuth credentials. Use an OpenSky OAuth API client_id/client_secret, not an account password.",
                    "requested": len(timestamps),
                    "fetched": fetched,
                    "skipped_cached": skipped_cached,
                    "remaining": len(missing),
                }
            for sample in states[: settings.max_aircraft_per_scan]:
                await store.add_track_sample(sample["icao24"], sample, settings.track_ttl_seconds)
            await store.set_cache(
                cache_key,
                {"states": len(states), "fetched_at": int(datetime.now(timezone.utc).timestamp())},
                settings.opensky_historical_cache_seconds,
            )
            fetched += 1
            states_seen += len(states)
        if fetched:
            await store.set_cache(
                fill_gate_key,
                {"fetched": fetched, "fetched_at": int(datetime.now(timezone.utc).timestamp())},
                settings.opensky_historical_backfill_interval_seconds,
            )
    except OpenSkyRateLimited as exc:
        retry_after_seconds = exc.retry_after_seconds
        await store.set_cache(
            f"opensky_state_backoff:{monitor['hash']}",
            {"retry_after_seconds": exc.retry_after_seconds},
            min(exc.retry_after_seconds, settings.opensky_historical_cache_seconds),
        )
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code
        if status in {401, 403}:
            reason = "OpenSky rejected historical state vector access."
            await store.set_cache(
                "opensky_historical_unavailable",
                {"status": status, "reason": reason},
                settings.opensky_historical_cache_seconds,
            )
            return {
                **base,
                "enabled": True,
                "available": False,
                "reason": reason,
                "requested": len(timestamps),
                "fetched": fetched,
                "skipped_cached": skipped_cached,
                "remaining": len(missing),
            }
        await store.set_cache(
            f"opensky_state_backoff:{monitor['hash']}",
            {"status": status},
            settings.opensky_historical_backfill_interval_seconds,
        )
        historical_error_reason = "OpenSky historical state vectors failed."
    except httpx.HTTPError:
        await store.set_cache(
            f"opensky_state_backoff:{monitor['hash']}",
            {"reason": "http_error"},
            settings.opensky_historical_backfill_interval_seconds,
        )
        historical_error_reason = "OpenSky historical state vectors failed."
    finally:
        await opensky.close()

    result = {
        **base,
        "enabled": True,
        "requested": len(timestamps),
        "fetched": fetched,
        "states_seen": states_seen,
        "skipped_cached": skipped_cached,
        "remaining": max(0, len(missing) - fetched),
        "coverage_complete": max(0, len(missing) - fetched) == 0,
        "complete_for_requested_window": max(0, len(missing) - fetched) == 0 and coverage_start <= window.start_ts,
    }
    if retry_after_seconds is not None:
        result.update({
            "backing_off": True,
            "retry_after_seconds": retry_after_seconds,
            "reason": "OpenSky historical state vectors are rate limited.",
        })
    if historical_error_reason is not None:
        result.update({
            "available": False,
            "backing_off": True,
            "reason": historical_error_reason,
        })
    return result


async def build_scan_response(
    store: Store,
    settings: Settings,
    conn,
    params: ScanParams,
) -> dict:
    p = await params_with_user_elevation(store, settings, params)
    window = resolve_window(p.window, settings.timezone)
    airport = db.get_airport(conn, p.airport_icao)
    if not airport:
        raise KeyError(f"unknown airport {p.airport_icao}")
    key = await register_monitor(store, settings, p, airport)
    monitor = monitor_for_params(p, airport)
    backfill = await backfill_historical_states(store, settings, monitor, window)
    await run_detectors_for_monitor(store, settings, conn, monitor, window.start_ts, window.end_ts)
    events = events_for_current_scan(await store.get_events(key, window.start_ts, window.end_ts), p)
    counts = event_counts(events)
    offenders = await enrich_offenders(
        store,
        settings,
        conn,
        airport,
        p,
        offender_rows(events, window, settings.timezone),
        window,
    )
    offender_tracks = await tracks_for_response(store, offenders, window)
    recent_tracks = await recent_tracks_for_response(store, window, tuple(monitor["bbox"]))
    tracks = merge_track_rows(offender_tracks, recent_tracks)
    active_now = await active_aircraft_count(store, airport, p)
    return {
        "monitor_hash": key,
        "window": window.model_dump(),
        "airport": db.airport_to_dict(airport),
        "user_location": {
            "lat": p.user_lat,
            "lon": p.user_lon,
            "hash": location_hash(p.user_lat, p.user_lon),
            "elevation_ft": p.user_elevation_ft,
        },
        "counters": {
            **counts,
            "offenders_active_now": active_now,
            "unique_aircraft": len(offenders),
            "label": window.label,
        },
        "offenders": offenders,
        "events": events,
        "histogram": event_histogram(events, window, settings.timezone),
        "tracks": tracks,
        "historical_backfill": backfill,
    }


def events_for_current_scan(events: list[dict], params: ScanParams) -> list[dict]:
    current_pass_key = pass_geometry_key(params)
    return [
        event for event in events
        if event["type"] != "pass_over_user"
        or event.get("pass_geometry_key") == current_pass_key
    ]


async def tracks_for_response(store: Store, offenders: list[dict], window: WindowRange) -> list[dict]:
    rows = []
    for offender in offenders[:30]:
        full_track = await store.get_track(offender["icao24"], window.start_ts - 1800, window.end_ts + 1800)
        if not full_track:
            continue
        rows.append({
            "icao24": offender["icao24"],
            "callsign": offender["callsign"],
            "samples": [
                {
                    "timestamp": sample["timestamp"],
                    "lat": sample["lat"],
                    "lon": sample["lon"],
                    "heading_deg": sample.get("heading_deg"),
                    "in_window": window.start_ts <= sample["timestamp"] <= window.end_ts,
                }
                for sample in full_track
                if sample.get("lat") is not None and sample.get("lon") is not None
            ],
        })
    return rows


def merge_track_rows(primary: list[dict], secondary: list[dict], limit: int = 40) -> list[dict]:
    rows = []
    seen = set()
    for group in (primary, secondary):
        for row in group:
            icao24 = row["icao24"].lower()
            if icao24 in seen:
                continue
            rows.append(row)
            seen.add(icao24)
            if len(rows) >= limit:
                return rows
    return rows


async def recent_tracks_for_response(
    store: Store,
    window: WindowRange,
    bbox: tuple[float, float, float, float] | None = None,
    limit: int = 40,
) -> list[dict]:
    rows = []
    for icao24 in await store.list_aircraft():
        full_track = await store.get_track(icao24, window.start_ts, window.end_ts)
        if not full_track:
            continue
        if bbox is not None and not track_intersects_bbox(full_track, bbox):
            continue
        callsign = next((sample.get("callsign") for sample in reversed(full_track) if sample.get("callsign")), icao24.upper())
        samples = [
            {
                "timestamp": sample["timestamp"],
                "lat": sample["lat"],
                "lon": sample["lon"],
                "heading_deg": sample.get("heading_deg"),
                "in_window": True,
            }
            for sample in full_track
            if sample.get("lat") is not None and sample.get("lon") is not None
        ]
        if not samples:
            continue
        rows.append({
            "icao24": icao24,
            "callsign": callsign,
            "samples": samples,
        })
    return sorted(
        rows,
        key=lambda row: row["samples"][-1]["timestamp"] if row["samples"] else 0,
        reverse=True,
    )[:limit]


async def enrich_offenders(
    store: Store,
    settings: Settings,
    conn,
    airport: Airport,
    params: ScanParams,
    offenders: list[dict],
    window: WindowRange,
) -> list[dict]:
    enriched = []
    for offender in offenders:
        track = await store.get_track(offender["icao24"], window.start_ts - 1800, window.end_ts + 1800)
        report_row = conn.execute(
            "SELECT report_count FROM aircraft_report_counts WHERE icao24 = ?",
            (offender["icao24"],),
        ).fetchone()
        enriched.append({
            **offender,
            "report_count": int(report_row["report_count"]) if report_row else 0,
            **altitude_over_user_summary(track, airport, params, window),
            **await resolve_origin(store, settings, conn, offender["icao24"], track),
        })
    return enriched


def altitude_over_user_summary(
    track: list[dict],
    airport: Airport,
    params: ScanParams,
    window: WindowRange,
) -> dict:
    over_user = closest_over_user_rows(track, airport, params, window.start_ts, window.end_ts, ceiling_ft=None)
    if not over_user:
        return {
            "avg_altitude_over_user_ft_agl": None,
            "min_altitude_over_user_ft_agl": None,
            "samples_over_user": 0,
        }
    return {
        "avg_altitude_over_user_ft_agl": int(round(sum(row["agl"] for row in over_user) / len(over_user))),
        "min_altitude_over_user_ft_agl": int(round(min(row["agl"] for row in over_user))),
        "closest_horizontal_nm": round(min(row["distance_nm"] for row in over_user), 3),
        "samples_over_user": len(over_user),
    }


def unknown_origin(source: str = "unknown") -> dict:
    return {
        "origin_city": None,
        "origin_airport_icao": None,
        "origin_label": "unknown",
        "origin_source": source,
        "origin_confidence": "unknown",
    }


def origin_from_airport_row(airport: dict, source: str, confidence: str, distance_nm: float | None = None) -> dict:
    result = {
        "origin_city": airport["city"],
        "origin_airport_icao": airport["icao"],
        "origin_label": f"{airport['city']} ({airport['icao']})",
        "origin_source": source,
        "origin_confidence": confidence,
    }
    if distance_nm is not None:
        result["origin_distance_nm"] = round(distance_nm, 2)
    return result


def airport_label_for_icao(conn, icao: str | None, source: str = "opensky_flights_departure") -> dict | None:
    if not icao:
        return None
    airport_icao = icao.strip().upper()
    if not airport_icao:
        return None
    airport = db.get_airport(conn, airport_icao)
    if airport:
        return origin_from_airport_row(db.airport_to_dict(airport), source, "high")
    return {
        "origin_city": None,
        "origin_airport_icao": airport_icao,
        "origin_label": airport_icao,
        "origin_source": source,
        "origin_confidence": "medium",
    }


def valid_position_samples(track: list[dict]) -> list[dict]:
    return [
        sample for sample in sorted(track, key=lambda row: row.get("timestamp") or 0)
        if sample.get("lat") is not None and sample.get("lon") is not None
    ]


def origin_from_point(
    conn,
    lat: float,
    lon: float,
    source: str,
    confidence: str,
    max_distance_nm: float,
) -> dict | None:
    nearest = db.nearest_airport(conn, lat, lon)
    if not nearest or nearest["distance_nm"] > max_distance_nm:
        return None
    return origin_from_airport_row(nearest, source, confidence, nearest["distance_nm"])


def origin_from_ground_track(conn, track: list[dict]) -> dict | None:
    for sample in valid_position_samples(track):
        origin = origin_from_point(
            conn,
            sample["lat"],
            sample["lon"],
            "ground_track_near_airport",
            "high",
            ORIGIN_GROUND_AIRPORT_MAX_NM,
        )
        if not origin:
            continue
        velocity = sample.get("velocity_kt")
        if sample.get("on_ground") is True or (velocity is not None and velocity <= ORIGIN_GROUND_SPEED_MAX_KT):
            return origin
    return None


def origin_from_track_start(
    conn,
    track: list[dict],
    source: str = "first_seen_near_airport",
    max_distance_nm: float = ORIGIN_FIRST_SEEN_AIRPORT_MAX_NM,
) -> dict | None:
    samples = valid_position_samples(track)
    if not samples:
        return None
    return origin_from_point(
        conn,
        samples[0]["lat"],
        samples[0]["lon"],
        source,
        "medium",
        max_distance_nm,
    )


def origin_from_track(conn, track: list[dict]) -> dict:
    ground_origin = origin_from_ground_track(conn, track)
    if ground_origin:
        return ground_origin
    track_origin = origin_from_track_start(conn, track)
    if track_origin:
        return track_origin
    first = next(
        (
            sample for sample in valid_position_samples(track)
        ),
        None,
    )
    if not first:
        return unknown_origin()
    if first.get("origin_country"):
        return {
            "origin_city": first.get("origin_country") or "unknown",
            "origin_airport_icao": None,
            "origin_label": first.get("origin_country") or "unknown",
            "origin_source": "first_seen_country",
            "origin_confidence": "low",
        }
    return unknown_origin()


def origin_cache_ttl(origin: dict) -> int:
    return (
        ORIGIN_STRONG_CACHE_SECONDS
        if origin.get("origin_confidence") in {"high", "medium"}
        else ORIGIN_WEAK_CACHE_SECONDS
    )


def flight_matches_window(flight: dict, first_seen: int, last_seen: int) -> bool:
    flight_first = flight.get("firstSeen")
    flight_last = flight.get("lastSeen")
    if flight_first is None and flight_last is None:
        return False
    start = int(flight_first or flight_last)
    end = int(flight_last or flight_first)
    return start <= last_seen + ORIGIN_LOOKAHEAD_SECONDS and end >= first_seen - ORIGIN_LOOKAHEAD_SECONDS


def select_origin_flight(flights: list[dict], first_seen: int, last_seen: int) -> dict | None:
    candidates = [flight for flight in flights if flight_matches_window(flight, first_seen, last_seen)]
    if not candidates:
        candidates = flights
    if not candidates:
        return None
    return max(
        candidates,
        key=lambda flight: int(flight.get("firstSeen") or flight.get("lastSeen") or 0),
    )


def origin_from_flights(conn, flights: list[dict], first_seen: int, last_seen: int) -> dict | None:
    flight = select_origin_flight(flights, first_seen, last_seen)
    if not flight:
        return None
    return airport_label_for_icao(conn, flight.get("estDepartureAirport"), "opensky_flights_departure")


def opensky_track_path_samples(payload: dict | None) -> list[dict]:
    if not payload:
        return []
    samples = []
    for row in payload.get("path") or []:
        if len(row) < 6 or row[1] is None or row[2] is None:
            continue
        samples.append({
            "timestamp": int(row[0]),
            "lat": float(row[1]),
            "lon": float(row[2]),
            "baro_altitude_m": row[3],
            "heading_deg": row[4],
            "on_ground": bool(row[5]),
        })
    return samples


async def reverse_city_for_point(store: Store, settings: Settings, lat: float, lon: float) -> str | None:
    cache_key = f"reverse_city:{round(lat, 2)}:{round(lon, 2)}"
    cached = await store.get_cache(cache_key)
    if isinstance(cached, str):
        return cached
    try:
        async with httpx.AsyncClient(timeout=min(settings.request_timeout_seconds, 4.0)) as client:
            response = await client.get(
                "https://nominatim.openstreetmap.org/reverse",
                params={
                    "lat": lat,
                    "lon": lon,
                    "format": "jsonv2",
                    "zoom": 10,
                    "addressdetails": 1,
                },
                headers={"User-Agent": f"circlejerk-prototype/0.1 ({settings.public_base_url})"},
            )
        if response.status_code >= 400:
            return None
        address = response.json().get("address") or {}
        city = next(
            (
                address.get(key)
                for key in ("city", "town", "village", "municipality", "hamlet", "county", "state")
                if address.get(key)
            ),
            None,
        )
        if city:
            await store.set_cache(cache_key, city, ORIGIN_CITY_CACHE_SECONDS)
        return city
    except httpx.HTTPError:
        return None


async def origin_city_from_point(
    store: Store,
    settings: Settings,
    lat: float,
    lon: float,
    source: str,
) -> dict | None:
    city = await reverse_city_for_point(store, settings, lat, lon)
    if not city:
        return None
    return {
        "origin_city": city,
        "origin_airport_icao": None,
        "origin_label": f"near {city}",
        "origin_source": source,
        "origin_confidence": "low",
    }


async def resolve_origin(
    store: Store,
    settings: Settings,
    conn,
    icao24: str,
    track: list[dict],
) -> dict:
    samples = valid_position_samples(track)
    if not samples:
        return unknown_origin()
    first_seen = int(samples[0]["timestamp"])
    last_seen = int(samples[-1]["timestamp"])
    cache_key = f"origin:{icao24.lower()}:{first_seen // 3600}:{last_seen // 3600}"
    cached = await store.get_cache(cache_key)
    if isinstance(cached, dict):
        return cached

    local_origin = origin_from_ground_track(conn, track)
    if local_origin:
        await store.set_cache(cache_key, local_origin, origin_cache_ttl(local_origin))
        return local_origin

    if all(settings.opensky_credentials()) and not await store.get_cache("opensky_auth_failed"):
        opensky = OpenSkyClient(settings)
        try:
            flights = await opensky.flights_for_aircraft(
                icao24,
                max(0, first_seen - ORIGIN_LOOKBACK_SECONDS),
                last_seen + ORIGIN_LOOKAHEAD_SECONDS,
            )
            if opensky.auth_failed:
                await store.set_cache("opensky_auth_failed", True, 600)
            flight_origin = origin_from_flights(conn, flights, first_seen, last_seen)
            if flight_origin:
                await store.set_cache(cache_key, flight_origin, origin_cache_ttl(flight_origin))
                return flight_origin

            for at_ts in dict.fromkeys([0, first_seen]):
                payload = await opensky.track_for_aircraft(icao24, at_ts)
                if opensky.auth_failed:
                    await store.set_cache("opensky_auth_failed", True, 600)
                    break
                path_samples = opensky_track_path_samples(payload)
                if not path_samples:
                    continue
                path_origin = origin_from_ground_track(conn, path_samples) or origin_from_track_start(
                    conn,
                    path_samples,
                    "opensky_track_start_near_airport",
                    ORIGIN_TRACK_START_AIRPORT_MAX_NM,
                )
                if path_origin:
                    await store.set_cache(cache_key, path_origin, origin_cache_ttl(path_origin))
                    return path_origin
                first_path = valid_position_samples(path_samples)[0]
                city_origin = await origin_city_from_point(
                    store,
                    settings,
                    first_path["lat"],
                    first_path["lon"],
                    "opensky_track_start_city",
                )
                if city_origin:
                    await store.set_cache(cache_key, city_origin, origin_cache_ttl(city_origin))
                    return city_origin
        finally:
            await opensky.close()

    fallback = origin_from_track(conn, track)
    if fallback.get("origin_label") == "unknown":
        city_origin = await origin_city_from_point(
            store,
            settings,
            samples[0]["lat"],
            samples[0]["lon"],
            "first_seen_city",
        )
        if city_origin:
            fallback = city_origin
    await store.set_cache(cache_key, fallback, origin_cache_ttl(fallback))
    return fallback


def origin_summary(conn, track: list[dict]) -> dict:
    return origin_from_track(conn, track)


async def active_aircraft_count(store: Store, airport: Airport, params: ScanParams) -> int:
    now = int(datetime.now(timezone.utc).timestamp())
    airport_point = Point(airport.lat, airport.lon)
    count = 0
    for icao24 in await store.list_aircraft():
        track = [
            sample for sample in await store.get_track(icao24, now - 150, now)
            if sample.get("lat") is not None and sample.get("lon") is not None
        ]
        if not track:
            continue

        latest = max(track, key=lambda sample: sample["timestamp"])
        if latest.get("on_ground"):
            continue
        if distance_nm(Point(latest["lat"], latest["lon"]), airport_point) > params.ring_nm:
            continue

        speed = latest.get("velocity_kt")
        try:
            moving_by_speed = speed is not None and float(speed) >= 30
        except (TypeError, ValueError):
            moving_by_speed = False
        recent_path_nm = sum(
            distance_nm(Point(a["lat"], a["lon"]), Point(b["lat"], b["lon"]))
            for a, b in zip(track, track[1:])
            if 0 < b["timestamp"] - a["timestamp"] <= 90
        )
        if moving_by_speed or recent_path_nm >= 0.2:
            count += 1
    return count


def complaint_context(
    settings: Settings,
    airport: Airport,
    icao24: str,
    events: list[dict],
    track: list[dict],
    window: WindowRange,
    aircraft: dict | None,
    params: ScanParams | None = None,
    conn=None,
    message_preferences: MessagePreferences | None = None,
    previous_report_count: int = 0,
    origin_info: dict | None = None,
) -> ComplaintContext:
    counts = event_counts(events)
    callsign = next(
        (sample.get("callsign") for sample in reversed(track) if sample.get("callsign")),
        next((event.get("callsign") for event in reversed(events) if event.get("callsign")), icao24.upper()),
    )
    alt_bands = [
        band for event in events
        if event["type"] == "circle"
        for band in [event.get("alt_band_ft")]
        if band
    ]
    alt_min = min((band[0] for band in alt_bands if band[0] is not None), default=None)
    alt_max = max((band[1] for band in alt_bands if band[1] is not None), default=None)
    avg_radius = next((event.get("avg_loop_radius_nm") for event in events if event.get("avg_loop_radius_nm") is not None), None)
    user_summary = altitude_over_user_summary(track, airport, params, window) if params else {}
    avg_user = user_summary.get("avg_altitude_over_user_ft_agl")
    min_user = user_summary.get("min_altitude_over_user_ft_agl")
    if min_user is None:
        min_user = min(
        [event["min_altitude_ft_agl"] for event in events if event["type"] == "pass_over_user" and event.get("min_altitude_ft_agl") is not None],
        default=None,
        )
    hours = Counter(hour_label(event["timestamp"], settings.timezone) for event in events)
    peak_hours = ", ".join(hour for hour, _ in hours.most_common(2)) if hours else "none"
    runway = next((event.get("runway_used") for event in events if event.get("runway_used")), None)
    first_seen = min([event["timestamp"] for event in events] + [sample["timestamp"] for sample in track], default=None)
    last_seen = max([event["timestamp"] for event in events] + [sample["timestamp"] for sample in track], default=None)
    origin = ((origin_info or origin_summary(conn, track))["origin_label"] if conn else None) or "unknown"
    if origin == "unknown" and track and first_seen:
        origin = f"unknown; first seen at {local_time_label(first_seen, settings.timezone)}"
    return ComplaintContext(
        airport_name=airport.name,
        airport_icao=airport.icao,
        user_location_label=airport.city,
        time_window_label=window.label,
        callsign=callsign,
        icao24=icao24,
        aircraft_type=(aircraft or {}).get("type_description") or "unknown aircraft type",
        observed_from=local_time_label(first_seen, settings.timezone) if first_seen else "unknown",
        observed_to=local_time_label(last_seen, settings.timezone) if last_seen else "unknown",
        circles=counts["circles"],
        avg_radius=avg_radius,
        alt_min=alt_min,
        alt_max=alt_max,
        touch_and_gos=counts["touch_and_gos"],
        low_approaches=counts["low_approaches"],
        passes=counts["passes"],
        avg_altitude_user=avg_user,
        min_altitude_user=min_user,
        quiet_hours_events=sum(1 for event in events if quiet_hour(event["timestamp"], settings.timezone)),
        peak_hours=peak_hours,
        origin_airport=origin,
        runway_used=runway,
        airport_elevation_ft=airport.elevation_ft,
        previous_report_count=max(0, previous_report_count),
        message_preferences=message_preferences or MessagePreferences(),
    )


async def build_description(
    store: Store,
    settings: Settings,
    conn,
    params: ScanParams,
    icao24: str,
    sliders: ToneSliders,
    message_preferences: MessagePreferences | None = None,
    previous_report_count: int = 0,
) -> dict:
    p = await params_with_user_elevation(store, settings, params)
    window = resolve_window(p.window, settings.timezone)
    airport = db.get_airport(conn, p.airport_icao)
    if not airport:
        raise KeyError(f"unknown airport {p.airport_icao}")
    key = monitor_hash(p)
    await register_monitor(store, settings, p, airport)
    events = [
        event for event in events_for_current_scan(await store.get_events(key, window.start_ts, window.end_ts), p)
        if event["icao24"] == icao24.lower()
    ]
    track = await store.get_track(icao24.lower(), window.start_ts - 1800, window.end_ts + 1800)
    aircraft = db.aircraft_detail(conn, icao24)
    prefs = message_preferences or MessagePreferences()
    origin_info = await resolve_origin(store, settings, conn, icao24.lower(), track)
    context = complaint_context(
        settings,
        airport,
        icao24.lower(),
        events,
        track,
        window,
        aircraft,
        p,
        conn,
        prefs,
        previous_report_count,
        origin_info,
    )
    cache_key = ":".join([
        icao24.lower(),
        airport.icao,
        location_hash(p.user_lat, p.user_lon),
        window.code,
        sliders.stable_hash(),
        "detail" if prefs.include_all_detail else "brief",
        "elev" if prefs.include_elevation else "noelev",
        "circles" if prefs.include_circles else "nocircles",
        "housealt" if prefs.include_altitude_over_house else "nohousealt",
        origin_info.get("origin_label") or "unknown",
        origin_info.get("origin_source") or "unknown",
        str(max(0, previous_report_count)),
    ])
    cached = await store.get_description(cache_key)
    if cached:
        source = "cache"
        text = cached
    else:
        prompt = build_prompt(context, sliders)
        text = await generate_with_groq(settings.groq_api_key, settings.groq_model, prompt)
        source = "groq"
        if not text:
            text = deterministic_description(context)
            source = "fallback"
        await store.set_description(cache_key, text, settings.description_ttl_seconds)
    return {
        "icao24": icao24.lower(),
        "sliders": sliders.model_dump(),
        "source": source,
        "text": text,
        "metadata": {
            "airport": airport.icao,
            "window": window.model_dump(),
            "callsign": context.callsign,
            "event_counts": event_counts(events),
            "previous_report_count": context.previous_report_count,
            "message_preferences": {
                "include_all_detail": prefs.include_all_detail,
                "include_elevation": prefs.include_elevation,
                "include_circles": prefs.include_circles,
                "include_altitude_over_house": prefs.include_altitude_over_house,
            },
        },
    }


async def build_summary_description(
    store: Store,
    settings: Settings,
    conn,
    params: ScanParams,
    icao24s: list[str],
    sliders: ToneSliders,
    message_preferences: MessagePreferences | None = None,
    report_counts: dict[str, int] | None = None,
) -> dict:
    p = await params_with_user_elevation(store, settings, params)
    window = resolve_window(p.window, settings.timezone)
    airport = db.get_airport(conn, p.airport_icao)
    if not airport:
        raise KeyError(f"unknown airport {p.airport_icao}")
    key = monitor_hash(p)
    await register_monitor(store, settings, p, airport)
    prefs = message_preferences or MessagePreferences()
    counts_by_aircraft = {icao.lower(): max(0, int(count)) for icao, count in (report_counts or {}).items()}
    contexts: list[ComplaintContext] = []
    all_timestamps = []

    for raw_icao24 in icao24s:
        icao24 = raw_icao24.lower()
        events = [
            event for event in events_for_current_scan(await store.get_events(key, window.start_ts, window.end_ts), p)
            if event["icao24"] == icao24
        ]
        track = await store.get_track(icao24, window.start_ts - 1800, window.end_ts + 1800)
        if not events and not track:
            continue
        aircraft = db.aircraft_detail(conn, icao24)
        origin_info = await resolve_origin(store, settings, conn, icao24, track)
        contexts.append(complaint_context(
            settings,
            airport,
            icao24,
            events,
            track,
            window,
            aircraft,
            p,
            conn,
            prefs,
            counts_by_aircraft.get(icao24, 0),
            origin_info,
        ))
        all_timestamps.extend(event["timestamp"] for event in events)
        all_timestamps.extend(sample["timestamp"] for sample in track if window.start_ts <= sample["timestamp"] <= window.end_ts)

    if not contexts:
        raise KeyError("no matching aircraft activity in this window")

    altitude_values = [context.avg_altitude_user for context in contexts if context.avg_altitude_user is not None]
    min_altitude_values = [context.min_altitude_user for context in contexts if context.min_altitude_user is not None]
    aggregate = AggregateComplaintContext(
        airport_name=airport.name,
        airport_icao=airport.icao,
        user_location_label=airport.city,
        time_window_label=window.label,
        observed_from=local_time_label(min(all_timestamps), settings.timezone) if all_timestamps else "unknown",
        observed_to=local_time_label(max(all_timestamps), settings.timezone) if all_timestamps else "unknown",
        aircraft_count=len(contexts),
        callsigns=[context.callsign for context in contexts],
        total_circles=sum(context.circles for context in contexts),
        total_touch_and_gos=sum(context.touch_and_gos for context in contexts),
        total_low_approaches=sum(context.low_approaches for context in contexts),
        total_passes=sum(context.passes for context in contexts),
        avg_altitude_user=int(round(sum(altitude_values) / len(altitude_values))) if altitude_values else None,
        min_altitude_user=min(min_altitude_values) if min_altitude_values else None,
        airport_elevation_ft=airport.elevation_ft,
        previous_report_total=sum(context.previous_report_count for context in contexts),
        items=contexts,
        message_preferences=prefs,
    )
    cache_key = ":".join([
        "summary",
        airport.icao,
        location_hash(p.user_lat, p.user_lon),
        window.code,
        ",".join(sorted(context.icao24 for context in contexts)),
        ",".join(sorted(context.origin_airport for context in contexts)),
        sliders.stable_hash(),
        "detail" if prefs.include_all_detail else "brief",
        "elev" if prefs.include_elevation else "noelev",
        "circles" if prefs.include_circles else "nocircles",
        "housealt" if prefs.include_altitude_over_house else "nohousealt",
        str(aggregate.previous_report_total),
    ])
    cached = await store.get_description(cache_key)
    if cached:
        source = "cache"
        text = cached
    else:
        prompt = build_aggregate_prompt(aggregate, sliders)
        text = await generate_with_groq(settings.groq_api_key, settings.groq_model, prompt)
        source = "groq"
        if not text:
            text = deterministic_aggregate_description(aggregate)
            source = "fallback"
        await store.set_description(cache_key, text, settings.description_ttl_seconds)
    return {
        "icao24s": [context.icao24 for context in contexts],
        "sliders": sliders.model_dump(),
        "source": source,
        "text": text,
        "metadata": {
            "airport": airport.icao,
            "window": window.model_dump(),
            "aircraft_count": aggregate.aircraft_count,
            "callsigns": aggregate.callsigns,
            "event_counts": {
                "circles": aggregate.total_circles,
                "touch_and_gos": aggregate.total_touch_and_gos,
                "low_approaches": aggregate.total_low_approaches,
                "passes": aggregate.total_passes,
            },
            "previous_report_total": aggregate.previous_report_total,
            "message_preferences": {
                "include_all_detail": prefs.include_all_detail,
                "include_elevation": prefs.include_elevation,
                "include_circles": prefs.include_circles,
                "include_altitude_over_house": prefs.include_altitude_over_house,
            },
        },
    }
