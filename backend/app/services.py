from __future__ import annotations

from collections import Counter
import asyncio
import logging
import time
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import httpx

from . import adsbdb, archive, db, deviation, flow, weather
from .db import Airport
from .detectors import closest_over_user_rows, detect_events, detect_events_over_period, detect_landings_over_period, event_counts, pass_geometry_key
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
    runway_change_note,
)
from .flightaware import FlightAwareClient
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
# Only do the expensive origin lookup (FA + OpenSky chain, up to 5 sequential
# HTTP calls) for the top-N offenders the UI actually displays. Lower-ranked
# offenders still get cheap local enrichment (report_count, altitude-over-user)
# so per-aircraft stats stay accurate.
ORIGIN_ENRICH_LIMIT = 12
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
    # Kick off an on-demand FlightAware backfill the first time we see an
    # airport (or any time the recency-gate has expired). New locations
    # otherwise have *no* history until live polling has accumulated for
    # hours, because the scheduled gap-filler runs only every 15 min and
    # processes airports in alphabetical order.
    asyncio.create_task(_maybe_trigger_on_demand_fa_backfill(store, settings, airport.icao))
    return monitor["hash"]


# Don't spam FlightAware: at most one on-demand backfill per airport per day.
# AeroAPI is per-call billed and we have a finite daily quota; cheap repeat
# attempts would burn the budget for free.
_FA_ON_DEMAND_GATE_SECONDS = 24 * 3600
# Bbox half-width (degrees) used to count archive samples near an airport when
# deciding whether it's "cold". ~0.2° ≈ 12nm — comfortably wraps the typical
# pattern + approach corridors we care about.
_ARCHIVE_COUNT_BBOX_DEG = 0.2
# Coarse client-facing ETA for an on-demand FA backfill. Worst case is
# ~30 per-flight calls plus 2 airport-flights calls at ~0.5–2s each, so 60s
# is a safe ceiling that won't surprise users with a longer-than-promised wait.
_FA_BACKFILL_ESTIMATED_SECONDS = 60
# Keep the status record around after completion long enough that the next
# poll cycle (≤5s) sees the "done" state and the frontend banner disappears
# cleanly, but not so long that stale completions linger across sessions.
_FA_BACKFILL_STATUS_TTL_SECONDS = 600


def _count_airport_archive_samples(
    settings: Settings,
    airport_icao: str,
    horizon_seconds: int = archive.DEFAULT_ARCHIVE_HORIZON_SECONDS,
) -> int:
    """Return the rough number of archived track samples near this airport.
    Synchronous (SQLite); call via asyncio.to_thread from async code."""
    now = int(time.time())
    with db.db_session(settings.database_path) as conn:
        airport = db.get_airport(conn, airport_icao)
        if not airport:
            return 0
        row = conn.execute(
            "SELECT COUNT(*) AS ct FROM track_archive "
            "WHERE timestamp > ? AND lat BETWEEN ? AND ? AND lon BETWEEN ? AND ?",
            (
                now - horizon_seconds,
                airport.lat - _ARCHIVE_COUNT_BBOX_DEG,
                airport.lat + _ARCHIVE_COUNT_BBOX_DEG,
                airport.lon - _ARCHIVE_COUNT_BBOX_DEG,
                airport.lon + _ARCHIVE_COUNT_BBOX_DEG,
            ),
        ).fetchone()
        return int(row["ct"]) if row else 0


async def _maybe_trigger_on_demand_fa_backfill(
    store: Store,
    settings: Settings,
    airport_icao: str,
) -> None:
    """Fire-and-forget: if we haven't backfilled this airport recently,
    enumerate the last 24h of activity now so the user sees history within
    a minute of selecting a new location.

    Sources (in priority order, falling through automatically):
      1. FlightAware AeroAPI — used when enabled AND the key is valid.
      2. FlightAware AeroAPI cold-start — fires once per 24h per airport,
         even if FA is otherwise disabled, when the archive is genuinely
         empty. Bounded by a global rolling-24h budget so a wave of new
         airports can't blow the spend ceiling. Skipped if the FA key
         has auth-failed.
      3. adsb.lol per-aircraft trace backfill — free, ODbL, used whenever
         FA is unavailable (disabled, no key, or auth-failed). No spend
         budget; concurrency is capped politely inside the module.
    """
    log = logging.getLogger(__name__)
    icao_upper = airport_icao.upper()
    gate_key = f"fa_on_demand_backfill:{icao_upper}"
    status_key = f"fa_backfill_status:{icao_upper}"

    try:
        # Per-airport cooldown applies to every source — we don't want
        # repeat visits to spam adsb.lol either.
        if await store.get_cache(gate_key):
            return

        # Skip if the archive already has plenty of recent samples here.
        sample_count = await asyncio.to_thread(
            _count_airport_archive_samples, settings, icao_upper
        )
        if sample_count >= settings.flightaware_cold_start_min_samples:
            return

        fa_rate_limited = bool(await store.get_cache("flightaware_rate_limited"))
        fa_auth_failed = bool(await store.get_cache("flightaware_auth_failed"))
        fa_usable = (
            settings.flightaware_enabled
            and bool(settings.flightaware_api_key)
            and not fa_rate_limited
            and not fa_auth_failed
        )

        # Source preference: FA when enabled+healthy, else adsb.lol (free).
        # The old "FA cold-start with budget" path is gone — when FA is off
        # for cost reasons, adsb.lol covers it without spending. If FA is on
        # but auth-failed (e.g., expired key), we silently fall through to
        # adsb.lol so users still get history.
        source: str | None = None
        if fa_usable:
            source = "flightaware"
        elif settings.adsblol_historical_enabled:
            source = "adsblol"
            if settings.flightaware_enabled and not fa_usable:
                # FA was meant to be on but isn't reachable — log it once per
                # cooldown so the operator notices.
                if fa_auth_failed:
                    log.info("FA auth_failed; routing %s cold-start to adsb.lol", icao_upper)
                elif fa_rate_limited:
                    log.info("FA rate_limited; routing %s cold-start to adsb.lol", icao_upper)

        if source is None:
            return

        await store.set_cache(gate_key, True, _FA_ON_DEMAND_GATE_SECONDS)

        # Publish a status record the frontend can poll to show an ETA banner.
        # FA worst case ≈ 60s; adsb.lol cold-start usually 30–45s with 5x
        # concurrency.  60s is a safe upper bound for either.
        started_at = int(time.time())
        mode = "adsblol_cold_start" if source == "adsblol" else "normal"
        await store.set_cache(
            status_key,
            {
                "running": True,
                "started_at": started_at,
                "estimated_total_seconds": _FA_BACKFILL_ESTIMATED_SECONDS,
                "mode": mode,
                "source": source,
            },
            _FA_BACKFILL_STATUS_TTL_SECONDS,
        )

        if source == "flightaware":
            result = await archive.gap_fill_via_flightaware_once(
                store, settings, only_airport=icao_upper
            )
            samples_written = int(result.get("written", 0)) if isinstance(result, dict) else 0
        else:
            from . import adsblol_historical

            result = await adsblol_historical.cold_start_backfill(
                store, settings, airport_icao=icao_upper
            )
            samples_written = int(result.get("samples_written", 0)) if isinstance(result, dict) else 0

        log.info(
            "on-demand backfill %s source=%s mode=%s: %s",
            icao_upper, source, mode, result,
        )
        await store.set_cache(
            status_key,
            {
                "running": False,
                "started_at": started_at,
                "completed_at": int(time.time()),
                "samples_written": samples_written,
                "mode": mode,
                "source": source,
            },
            _FA_BACKFILL_STATUS_TTL_SECONDS,
        )
    except Exception:  # noqa: BLE001 — never let backfill errors leak into scan
        log.exception("on-demand backfill failed for %s", icao_upper)
        try:
            await store.set_cache(
                status_key,
                {
                    "running": False,
                    "started_at": int(time.time()),
                    "completed_at": int(time.time()),
                    "error": True,
                },
                _FA_BACKFILL_STATUS_TTL_SECONDS,
            )
        except Exception:  # noqa: BLE001
            pass


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
    # Tracks are tiered: Redis holds `track_ttl_seconds` of hot data; SQLite
    # holds up to `archive.DEFAULT_ARCHIVE_HORIZON_SECONDS` of cold data. The
    # detector should look back over the union so wider scan windows ("today",
    # multi-hour) still detect events.
    cold_horizon = archive.DEFAULT_ARCHIVE_HORIZON_SECONDS
    if start_ts is not None:
        detector_start = max(start_ts, detector_end - cold_horizon)
    else:
        max_lookback = max(
            LIVE_DETECTOR_LOOKBACK_SECONDS,
            settings.opensky_historical_limit_seconds if settings.opensky_historical_enabled else 0,
        )
        detector_start = detector_end - max_lookback
    hot_icao24s = await store.list_aircraft()
    if _window_extends_into_cold_tier(detector_start - 20 * 60, settings):
        cold_icao24s = db.list_archive_aircraft(conn, detector_start - 20 * 60, detector_end)
        icao24s = sorted(set(hot_icao24s) | set(cold_icao24s))
    else:
        icao24s = hot_icao24s
    tracks = await bulk_get_tracks_unified(
        store, conn, settings, icao24s,
        detector_start - 20 * 60, detector_end,
    )
    # Fetch existing event IDs ONCE (not per-event) — event_exists re-scanned
    # the whole events set on every call, which was O(events²) per scan.
    existing_ids = await store.existing_event_ids(monitor["hash"])
    new_events: list[dict] = []
    tracks_by_icao24: dict[str, list[dict]] = {}
    for icao24, track in zip(icao24s, tracks):
        if not track_intersects_bbox(track, tuple(monitor["bbox"])):
            continue
        tracks_by_icao24[icao24] = track
        events = (
            detect_events_over_period(track, airport, runways, params, start_ts, end_ts)
            if start_ts is not None and end_ts is not None
            else detect_events(track, airport, runways, params)
        )
        if start_ts is None or end_ts is None:
            # The live detect_events() path can't emit landings — they need a
            # 5-min settle window. Run a windowed landing pass over the same
            # already-fetched tracks so the continuous worker detects landings
            # too; otherwise touch-and-gos accrue continuously but landings only
            # on the /scan polling path, biasing "% did not stop" high. The
            # detector's stable per-episode ids keep this idempotent across the
            # worker's repeated cycles.
            events = list(events) + detect_landings_over_period(
                track, airport, runways, detector_start, detector_end
            )
        for event in events:
            if event["id"] not in existing_ids:
                await store.add_event(monitor["hash"], event, settings.event_ttl_seconds)
                existing_ids.add(event["id"])
                new_events.append(event)
                written += 1
    # Durably log the ops so the KPIs page / deviation / rotation phases have
    # history beyond the ephemeral Redis (~4h) + archive (~24h) windows.
    if new_events:
        db.persist_events(conn, new_events)
        # Quantify how far each circling aircraft strays from the drawn VNAP
        # pattern (no-op when this airport has no pattern yet).
        deviation.store_deviations(conn, airport.icao, new_events, tracks_by_icao24)
        # Tag ops with headwind and update active-runway flow + cowboy log.
        try:
            wind = await weather.get_wind_summary(store, airport.icao, 1)
        except Exception:  # noqa: BLE001 — never let weather break detection
            wind = {}
        flow.process(conn, airport.icao, runways, new_events, wind, now)
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


def _scan_cache_key(settings: Settings, params: ScanParams) -> str:
    bucket = max(settings.scan_response_cache_bucket_deg, 0.0001)
    lat_b = round(params.user_lat / bucket) * bucket
    lon_b = round(params.user_lon / bucket) * bucket
    return (
        f"scan_response:{params.airport_icao.upper()}:"
        f"{lat_b:.4f}:{lon_b:.4f}:{params.window}:"
        f"{params.ring_nm}:{params.pass_radius_nm}:{params.pass_ceiling_ft}"
    )


# In-process singleflight: collapse concurrent identical scans within a worker
# into one computation. The frontend polls /scan every 5s but a cold scan can
# take many seconds; without this, polls stack up and saturate all workers,
# making every scan slow (the "stuck on loading" cascade). Keyed by scan cache
# key → the in-flight asyncio.Task computing it.
_scan_inflight: dict[str, "asyncio.Task[dict]"] = {}


async def build_scan_response(
    store: Store,
    settings: Settings,
    conn,
    params: ScanParams,
) -> dict:
    cache_key = _scan_cache_key(settings, params)
    if settings.scan_response_cache_seconds > 0:
        cached = await store.get_cache(cache_key)
        if isinstance(cached, dict):
            cached.setdefault("cache_meta", {})["cached"] = True
            return cached

        # Singleflight: if another request is already computing this exact key,
        # await its result instead of kicking off a duplicate computation.
        inflight = _scan_inflight.get(cache_key)
        if inflight is not None and not inflight.done():
            return await asyncio.shield(inflight)

        async def _compute_and_cache() -> dict:
            # Use a dedicated DB connection, NOT the caller's `conn`: the
            # originating request may be cancelled (client disconnect) and
            # close its connection while this shielded task is still running.
            try:
                with db.db_session(settings.database_path) as own_conn:
                    resp = await _compute_scan_response(store, settings, own_conn, params)
                await store.set_cache(cache_key, resp, settings.scan_response_cache_seconds)
                return resp
            finally:
                _scan_inflight.pop(cache_key, None)

        task = asyncio.ensure_future(_compute_and_cache())
        _scan_inflight[cache_key] = task
        return await asyncio.shield(task)

    response = await _compute_scan_response(store, settings, conn, params)
    return response


async def _compute_scan_response(
    store: Store,
    settings: Settings,
    conn,
    params: ScanParams,
) -> dict:
    # Per-stage timing so production logs show where slow scans spend their
    # time. With 3 uvicorn workers and CPU-bound detectors this matters a lot.
    import logging
    log = logging.getLogger(__name__)
    t0 = time.monotonic()

    def lap(stage: str, t_prev: float) -> float:
        now = time.monotonic()
        log.info(
            "scan stage=%s airport=%s window=%s ms=%d",
            stage, params.airport_icao, params.window, int((now - t_prev) * 1000),
        )
        return now

    p = await params_with_user_elevation(store, settings, params)
    t = lap("params", t0)
    window = resolve_window(p.window, settings.timezone)
    airport = db.get_airport(conn, p.airport_icao)
    if not airport:
        raise KeyError(f"unknown airport {p.airport_icao}")
    key = await register_monitor(store, settings, p, airport)
    monitor = monitor_for_params(p, airport)
    t = lap("monitor_registered", t)
    backfill = await backfill_historical_states(store, settings, monitor, window)
    t = lap("backfill", t)
    await run_detectors_for_monitor(store, settings, conn, monitor, window.start_ts, window.end_ts)
    t = lap("detectors", t)
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
    t = lap(f"enrich_offenders n={len(offenders)}", t)
    offender_tracks = await tracks_for_response(
        store, offenders, window, conn=conn, settings=settings,
    )
    recent_tracks = await recent_tracks_for_response(
        store, window, tuple(monitor["bbox"]), conn=conn, settings=settings,
    )
    tracks = merge_track_rows(offender_tracks, recent_tracks)
    t = lap(f"tracks_for_response tracks={len(tracks)}", t)
    _record_observed_identities(conn, tracks, window.end_ts)
    active_now = await active_aircraft_count(store, airport, p)
    log.info(
        "scan TOTAL airport=%s window=%s ms=%d offenders=%d tracks=%d events=%d",
        params.airport_icao, params.window,
        int((time.monotonic() - t0) * 1000),
        len(offenders), len(tracks), len(events),
    )
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


# --- Two-tier track reads (Redis hot + SQLite archive) ----------------------

def _window_extends_into_cold_tier(start_ts: int, settings: Settings) -> bool:
    """True if the requested window reaches back further than the Redis TTL."""
    return start_ts < int(time.time()) - settings.track_ttl_seconds


async def bulk_get_tracks_unified(
    store: Store,
    conn,
    settings: Settings,
    icao24s: list[str],
    start_ts: int,
    end_ts: int,
) -> list[list[dict]]:
    """Get track samples from Redis, falling through to the SQLite archive
    when the window extends beyond hot retention.

    Returns one list per icao24, in the same order as the input.
    """
    if not icao24s:
        return []
    hot_groups = await store.bulk_get_tracks(icao24s, start_ts, end_ts)
    if not _window_extends_into_cold_tier(start_ts, settings):
        return hot_groups
    archive_by_icao = db.bulk_read_track_archive(conn, icao24s, start_ts, end_ts)
    merged: list[list[dict]] = []
    for icao24, hot in zip(icao24s, hot_groups):
        cold = archive_by_icao.get(icao24.lower(), [])
        if not cold:
            merged.append(hot)
            continue
        merged.append(archive.merge_hot_and_cold_samples(hot, cold))
    return merged


async def get_track_unified(
    store: Store,
    conn,
    settings: Settings,
    icao24: str,
    start_ts: int,
    end_ts: int,
) -> list[dict]:
    hot = await store.get_track(icao24, start_ts, end_ts)
    if not _window_extends_into_cold_tier(start_ts, settings):
        return hot
    cold = db.read_track_archive(conn, icao24, start_ts, end_ts)
    if not cold:
        return hot
    return archive.merge_hot_and_cold_samples(hot, cold)


async def tracks_for_response(
    store: Store,
    offenders: list[dict],
    window: WindowRange,
    *,
    conn=None,
    settings: Settings | None = None,
) -> list[dict]:
    top_offenders = offenders[:30]
    icao24s = [o["icao24"] for o in top_offenders]
    start_ts = window.start_ts - 1800
    end_ts = window.end_ts + 1800
    if conn is not None and settings is not None:
        track_groups = await bulk_get_tracks_unified(
            store, conn, settings, icao24s, start_ts, end_ts,
        )
    else:
        track_groups = await store.bulk_get_tracks(icao24s, start_ts, end_ts)
    rows = []
    for offender, full_track in zip(top_offenders, track_groups):
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
                    "altitude_ft": sample.get("geo_altitude_ft") or sample.get("baro_altitude_ft"),
                    "vertical_rate_fpm": sample.get("vertical_rate_fpm"),
                    "in_window": window.start_ts <= sample["timestamp"] <= window.end_ts,
                }
                for sample in full_track
                if sample.get("lat") is not None and sample.get("lon") is not None
            ],
        })
    return rows


def _record_observed_identities(conn, tracks: list[dict], end_ts: int) -> None:
    """Persist (icao_hex, callsign, normalized_n_number) sightings from a scan.

    Best-effort: failures are swallowed so they can never block scan responses.
    """
    try:
        from .registry import normalize as registry_norm

        for track in tracks:
            icao_hex = registry_norm.normalize_icao_hex(track.get("icao24"))
            if not icao_hex:
                continue
            callsign = (track.get("callsign") or "").strip().upper() or None
            n_number = registry_norm.extract_n_number_from_callsign(callsign)
            confidence = 0.95 if n_number else (0.4 if callsign else 0.1)
            db.upsert_observed_identity(
                conn,
                icao_hex=icao_hex,
                callsign=callsign,
                normalized_n_number=n_number,
                timestamp=int(end_ts),
                confidence=confidence,
            )
    except Exception:  # noqa: BLE001 — never let observation tracking break scan
        pass


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
    *,
    conn=None,
    settings: Settings | None = None,
) -> list[dict]:
    # Recent/contextual tracks are the gray background paths of *all* aircraft
    # in the window. We deliberately read ONLY the hot tier (Redis) here, even
    # for wide windows: pulling every aircraft's full 24h history out of the
    # SQLite archive scanned hundreds of thousands of rows and hung the scan
    # for 40+ seconds. The offenders' full archived paths are still served by
    # tracks_for_response (bounded to ≤30 aircraft), so the worst offenders
    # keep their complete history; the background context is just last-4h.
    icao24s = await store.list_aircraft()
    track_groups = await store.bulk_get_tracks(icao24s, window.start_ts, window.end_ts)
    rows = []
    for icao24, full_track in zip(icao24s, track_groups):
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
                "altitude_ft": sample.get("geo_altitude_ft") or sample.get("baro_altitude_ft"),
                "vertical_rate_fpm": sample.get("vertical_rate_fpm"),
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
    """Scan-path enrichment: report_count + altitude-over-user + (cached) origin.

    Performance lessons paid for in production wedges:

    1. The async event loop is blocked by synchronous `conn.execute()` calls.
       8 "parallel" coroutines each doing `conn.execute()` serialize on the
       sqlite3 connection AND block the loop, so `asyncio.wait_for` timeouts
       never fire. So: batch every SQLite read into a SINGLE upfront query,
       BEFORE the gather.
    2. `resolve_origin` can fan out to ~5 sequential FA / OpenSky calls per
       offender. On the scan hot path we only consult the local cache + local
       DB — never the remote APIs. Cold lookups for new aircraft just return
       "unknown" until the worker / background tasks populate the origin
       cache. UX impact: rare and self-healing; latency impact: night and day.
    """
    icao24s = [o["icao24"] for o in offenders]

    # --- ONE upfront SQLite query for all report_counts (no per-row conn.execute) ---
    report_counts: dict[str, int] = {}
    if icao24s:
        placeholders = ",".join("?" * len(icao24s))
        rows = conn.execute(
            f"SELECT icao24, report_count FROM aircraft_report_counts WHERE icao24 IN ({placeholders})",
            icao24s,
        ).fetchall()
        report_counts = {r["icao24"]: int(r["report_count"]) for r in rows}

    # Deviation-from-pattern (avg over the window) + cowboy flag, batched upfront
    # (same perf rule as above: one query each, never per-offender).
    deviation_by_icao: dict[str, float] = {}
    cowboy_set: set[str] = set()
    if icao24s:
        placeholders = ",".join("?" * len(icao24s))
        dev_rows = conn.execute(
            f"SELECT icao24, ROUND(AVG(deviation_mean_nm), 2) AS dev FROM operations "
            f"WHERE icao = ? AND timestamp BETWEEN ? AND ? AND deviation_mean_nm IS NOT NULL "
            f"AND icao24 IN ({placeholders}) GROUP BY icao24",
            (airport.icao, window.start_ts, window.end_ts, *icao24s),
        ).fetchall()
        deviation_by_icao = {r["icao24"]: r["dev"] for r in dev_rows}
        cowboy_rows = conn.execute(
            f"SELECT DISTINCT cowboy_icao24 FROM runway_changes "
            f"WHERE icao = ? AND changed_at BETWEEN ? AND ? AND wind_favored_new = 0 "
            f"AND cowboy_icao24 IN ({placeholders})",
            (airport.icao, window.start_ts, window.end_ts, *icao24s),
        ).fetchall()
        cowboy_set = {r["cowboy_icao24"] for r in cowboy_rows}

    sem = asyncio.Semaphore(8)

    async def enrich_one(offender: dict, with_origin: bool) -> dict:
        async with sem:
            try:
                track = await store.get_track(
                    offender["icao24"], window.start_ts - 1800, window.end_ts + 1800
                )
            except Exception:  # noqa: BLE001
                track = []
            origin: dict = {}
            if with_origin:
                try:
                    origin = await _resolve_origin_local_only(
                        store, conn, offender["icao24"], track
                    )
                except Exception:  # noqa: BLE001
                    origin = {}
            return {
                **offender,
                "report_count": report_counts.get(offender["icao24"], 0),
                "deviation_mean_nm": deviation_by_icao.get(offender["icao24"]),
                "is_cowboy": offender["icao24"] in cowboy_set,
                **altitude_over_user_summary(track, airport, params, window),
                **origin,
            }

    return list(
        await asyncio.gather(
            *(
                enrich_one(o, with_origin=(i < ORIGIN_ENRICH_LIMIT))
                for i, o in enumerate(offenders)
            )
        )
    )


async def _resolve_origin_local_only(
    store: Store,
    conn,
    icao24: str,
    track: list[dict],
) -> dict:
    """Cache + local-DB origin lookup — no FlightAware, no OpenSky.

    Used by the scan hot path; the full chain (resolve_origin) is still
    available for background / out-of-band enrichment. The "unknown" return
    is acceptable because (a) repeat aircraft hit the cache and (b) the
    origin cache is populated by the worker / scheduled tasks over time.
    """
    samples = valid_position_samples(track)
    if not samples:
        return unknown_origin()
    first_seen = int(samples[0]["timestamp"])
    last_seen = int(samples[-1]["timestamp"])
    cache_key = f"origin:{icao24.lower()}:{first_seen // 3600}:{last_seen // 3600}"
    cached = await store.get_cache(cache_key)
    if isinstance(cached, dict):
        return cached
    local = origin_from_ground_track(conn, track)
    if local:
        await store.set_cache(cache_key, local, origin_cache_ttl(local))
        return local
    return unknown_origin()


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


# Acoustic reference: an aircraft at 900 ft AGL directly overhead is
# modelled at 65 dB at the listener (the midpoint of the 60-70 dB range
# given by the user). Sound pressure level decays 20·log10(distance ratio).
DB_REFERENCE_DISTANCE_FT = 900.0
DB_REFERENCE_LEVEL = 65.0
DB_AUDIBLE_THRESHOLD = 35.0
# Long-term residential ambient L_den floor. Constant for now; planned
# upgrade is to pull per-location values from an OSM road-noise raster
# (lukasmartinelli/osm-noise-pollution) or a paid noise-map.com dataset.
DB_AMBIENT_BASELINE = 40.0


def combine_db(*levels: float) -> float:
    """Energetic sum of independent sound levels.
    L_total = 10·log10(Σ 10^(L_i / 10))"""
    import math as _m
    energy = 0.0
    for lvl in levels:
        energy += _m.pow(10.0, lvl / 10.0)
    if energy <= 0:
        return 0.0
    return 10.0 * _m.log10(energy)


def climb_noise_bonus_db(vertical_rate_fpm: float | None) -> float:
    """Aircraft climbing under full power are MUCH louder than aircraft at
    the same altitude in cruise — engine + prop noise dominates the spectrum.
    Returns extra source dB added to the acoustic model:
        100 fpm  → +3 dB
        500 fpm  → +14 dB
        1000 fpm → +20 dB
        2000+    → capped at +25 dB
    Descending aircraft (idle power) get a small subtraction.
    """
    if vertical_rate_fpm is None:
        return 0.0
    import math as _m
    rate = float(vertical_rate_fpm)
    if rate < -300:
        return -3.0  # engines at idle
    if rate < 100:
        return 0.0
    # Logarithmic curve, biased high so climb dominates the model.
    return min(25.0, 10.0 * _m.log10(1.0 + rate / 100.0))


def _db_at_home_for_sample(
    sample: dict,
    user_lat: float,
    user_lon: float,
    user_elevation_ft: float,
) -> float | None:
    lat = sample.get("lat")
    lon = sample.get("lon")
    alt = sample.get("geo_altitude_ft") or sample.get("baro_altitude_ft")
    if lat is None or lon is None or alt is None:
        return None
    horizontal_nm = distance_nm(Point(float(lat), float(lon)), Point(user_lat, user_lon))
    horizontal_ft = horizontal_nm * 6076.12
    alt_above_user_ft = float(alt) - float(user_elevation_ft)
    if alt_above_user_ft <= 0:
        alt_above_user_ft = 100.0
    distance_ft = (horizontal_ft * horizontal_ft + alt_above_user_ft * alt_above_user_ft) ** 0.5
    source_db = DB_REFERENCE_LEVEL + climb_noise_bonus_db(sample.get("vertical_rate_fpm"))
    if distance_ft <= 0:
        return source_db + 20.0
    import math as _m
    return source_db - 20.0 * _m.log10(distance_ft / DB_REFERENCE_DISTANCE_FT)


def db_at_home_summary(
    track: list[dict],
    user_lat: float,
    user_lon: float,
    user_elevation_ft: float | None,
    window: WindowRange,
    seconds_per_sample: float = 10.0,
) -> dict:
    """Compute peak and audible-window-average dB at the listener (home) from
    aircraft positions during the window. `seconds_per_sample` reflects the
    worker's poll cadence so audible_seconds counts time, not raw samples."""
    if user_elevation_ft is None:
        return {"peak_db": None, "avg_db": None, "audible_seconds": 0}
    audible: list[float] = []
    peak: float | None = None
    for sample in track:
        ts = sample.get("timestamp")
        if ts is None or not (window.start_ts <= int(ts) <= window.end_ts):
            continue
        db = _db_at_home_for_sample(sample, user_lat, user_lon, float(user_elevation_ft))
        if db is None:
            continue
        if peak is None or db > peak:
            peak = db
        if db >= DB_AUDIBLE_THRESHOLD:
            audible.append(db)
    if peak is None:
        return {
            "peak_db": None,
            "avg_db": None,
            "combined_db": None,
            "ambient_db": DB_AMBIENT_BASELINE,
            "audible_seconds": 0,
        }
    avg_audible = sum(audible) / len(audible) if audible else None
    combined = combine_db(avg_audible, DB_AMBIENT_BASELINE) if avg_audible is not None else None
    return {
        "peak_db": round(peak, 1),
        "avg_db": round(avg_audible, 1) if avg_audible is not None else None,
        "combined_db": round(combined, 1) if combined is not None else None,
        "ambient_db": DB_AMBIENT_BASELINE,
        "audible_seconds": int(round(len(audible) * seconds_per_sample)),
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
    # Only the earliest samples are plausible "origin" candidates — once the
    # aircraft is airborne and en route, later samples aren't where it took
    # off from. Capping the loop here matters because each iteration calls
    # nearest_airport, which scans the airports table. With 16k US airports
    # in the table, iterating hundreds of samples per offender × 12 offenders
    # was burning all the CPU and starving every request behind it.
    for sample in valid_position_samples(track)[:8]:
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

    # Free callsign → origin via adsbdb.com (no key, no per-call cost).
    # Covers airline scheduled routes; GA aircraft fall through to OpenSky.
    callsign_for_lookup = next(
        (sample.get("callsign") for sample in reversed(samples) if sample.get("callsign")),
        None,
    )
    if callsign_for_lookup and adsbdb.looks_like_airline_callsign(callsign_for_lookup):
        adsbdb_origin = await adsbdb.origin_for_callsign(store, settings, callsign_for_lookup)
        if adsbdb_origin:
            await store.set_cache(cache_key, adsbdb_origin, origin_cache_ttl(adsbdb_origin))
            return adsbdb_origin

    # FlightAware AeroAPI — kept as opt-in fallback (per-call billing). Disabled
    # by default in production via FLIGHTAWARE_ENABLED=false; flip back on if
    # you want to pay for the extra coverage adsbdb doesn't have.
    if (
        settings.flightaware_enabled
        and settings.flightaware_api_key
        and not await store.get_cache("flightaware_auth_failed")
        and not await store.get_cache("flightaware_rate_limited")
    ):
        if callsign_for_lookup and FlightAwareClient.looks_like_airline_ident(callsign_for_lookup):
            fa = FlightAwareClient(settings)
            try:
                fa_origin = await fa.origin_for_callsign(callsign_for_lookup, first_seen, last_seen)
                if fa.auth_failed:
                    await store.set_cache("flightaware_auth_failed", True, 600)
                elif fa.rate_limited:
                    await store.set_cache("flightaware_rate_limited", True, 600)
                elif fa_origin:
                    await store.set_cache(cache_key, fa_origin, origin_cache_ttl(fa_origin))
                    return fa_origin
            finally:
                await fa.close()

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
    icao24s = await store.list_aircraft()
    raw_tracks = await store.bulk_get_tracks(icao24s, now - 150, now)
    count = 0
    for raw_track in raw_tracks:
        track = [
            sample for sample in raw_track
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
    db_summary: dict = {"peak_db": None, "avg_db": None, "audible_seconds": 0}
    if params is not None:
        user_elev = params.user_elevation_ft if params.user_elevation_ft is not None else airport.elevation_ft
        db_summary = db_at_home_summary(track, params.user_lat, params.user_lon, user_elev, window)
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
        avg_db_at_home=db_summary.get("avg_db"),
        peak_db_at_home=db_summary.get("peak_db"),
        audible_seconds_at_home=db_summary.get("audible_seconds"),
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
    system_prompt: str | None = None,
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
    against_wind = [
        dict(r) for r in conn.execute(
            "SELECT to_runway_id, cowboy_callsign, cowboy_icao24, changed_at FROM runway_changes "
            "WHERE icao = ? AND changed_at BETWEEN ? AND ? AND wind_favored_new = 0 ORDER BY changed_at DESC",
            (airport.icao, window.start_ts, window.end_ts),
        ).fetchall()
    ]
    rwy_note = runway_change_note(against_wind)
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
        "dbhome" if prefs.include_db_at_home else "nodbhome",
        origin_info.get("origin_label") or "unknown",
        origin_info.get("origin_source") or "unknown",
        str(max(0, previous_report_count)),
        str(len(against_wind)),
        (system_prompt or "default")[:120],
    ])
    cached = await store.get_description(cache_key)
    if cached:
        source = "cache"
        text = cached
    else:
        prompt = build_prompt(context, sliders) + "\n" + rwy_note + "\n"
        text = await generate_with_groq(settings.groq_api_key, settings.groq_model, prompt, system_prompt=system_prompt)
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
    system_prompt: str | None = None,
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
    peak_db_values = [context.peak_db_at_home for context in contexts if context.peak_db_at_home is not None]
    audible_pairs = [
        (context.avg_db_at_home, context.audible_seconds_at_home or 0)
        for context in contexts
        if context.avg_db_at_home is not None
    ]
    total_audible = sum(seconds for _, seconds in audible_pairs)
    avg_db_aggregate = (
        sum(db * max(1, seconds) for db, seconds in audible_pairs) / max(1, total_audible)
        if audible_pairs
        else None
    )
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
        avg_db_at_home=round(avg_db_aggregate, 1) if avg_db_aggregate is not None else None,
        peak_db_at_home=round(max(peak_db_values), 1) if peak_db_values else None,
        audible_seconds_at_home=total_audible if audible_pairs else 0,
        message_preferences=prefs,
    )
    against_wind = [
        dict(r) for r in conn.execute(
            "SELECT to_runway_id, cowboy_callsign, cowboy_icao24, changed_at FROM runway_changes "
            "WHERE icao = ? AND changed_at BETWEEN ? AND ? AND wind_favored_new = 0 ORDER BY changed_at DESC",
            (airport.icao, window.start_ts, window.end_ts),
        ).fetchall()
    ]
    rwy_note = runway_change_note(against_wind)
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
        "dbhome" if prefs.include_db_at_home else "nodbhome",
        str(aggregate.previous_report_total),
        str(len(against_wind)),
        (system_prompt or "default")[:120],
    ])
    cached = await store.get_description(cache_key)
    if cached:
        source = "cache"
        text = cached
    else:
        prompt = build_aggregate_prompt(aggregate, sliders) + "\n" + rwy_note + "\n"
        text = await generate_with_groq(settings.groq_api_key, settings.groq_model, prompt, system_prompt=system_prompt)
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
