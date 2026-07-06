"""Cold-tier track archival.

Background task that moves track samples from Redis (hot, ~4h retention,
bounded by Valkey memory) to SQLite (cold, ~7d retention, bounded by disk).
Lets the "today" / 24h scan windows draw on a full day of history without
bloating Redis past its maxmemory cap.

Design:
- Redis remains the source of truth for "current activity" — fast reads,
  short retention via natural TTL.
- Every `archive_interval_seconds`, walk every `track:*` key in Redis and
  read samples in a moving "tail window" — old enough that the user is
  unlikely to be actively querying them at sub-second latency, young enough
  that they haven't been evicted by the Redis TTL yet.
- Bulk INSERT OR IGNORE into the SQLite `track_archive` table. Samples that
  are already archived are no-ops, so the job is safe to overrun or repeat.
- Periodically prune the archive of samples older than the retention horizon.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Iterable

from . import db
from .settings import Settings
from .store import Store, loads

LOGGER = logging.getLogger(__name__)

# Window of samples we copy to the archive on each pass. We start a little
# before the archive horizon (so a fresh archive run will catch samples that
# arrived just before the previous run) and end at the eviction horizon (no
# point archiving samples about to expire from Redis on the next pass).
ARCHIVE_TAIL_BUFFER_SECONDS = 600  # 10-minute overlap, safe under INSERT OR IGNORE
DEFAULT_ARCHIVE_INTERVAL_SECONDS = 5 * 60  # archive every 5 minutes
DEFAULT_ARCHIVE_HORIZON_SECONDS = 24 * 3600  # keep 24h in cold storage
DEFAULT_PRUNE_INTERVAL_SECONDS = 60 * 60  # prune once an hour


def horizon_seconds(settings: Settings) -> int:
    """Cold-archive retention horizon in seconds, from the configurable
    day count. Used for both the archive tail-window and the prune cutoff."""
    return int(settings.track_archive_horizon_days) * 86400


# Gap-filler defaults. Walks the past 24h in 1-minute steps for each active
# monitor's bbox and asks OpenSky for any snapshot whose minute-bucket isn't
# already represented in the archive.
# Slow the *scheduled* sweep to once an hour so we don't drain the FlightAware
# rate-limit budget. The on-demand trigger (services.register_monitor) is what
# actually backfills fresh locations within seconds of a user scanning them.
DEFAULT_GAP_FILL_INTERVAL_SECONDS = 60 * 60  # every 1 hour
GAP_FILL_STEP_SECONDS = 60
GAP_FILL_MAX_SNAPSHOTS_PER_RUN = 240  # cap each pass so we don't hammer OpenSky
GAP_FILL_REQUEST_PAUSE_SECONDS = 0.15

# FlightAware fallback budget. Each call costs AeroAPI credits; cap aggressively
# so an outage doesn't run up a bill. One flight ≈ 2 calls (airport list +
# /flights/{id}/track). 30 flights → ~60 calls → ~$0.30 on a standard plan.
FA_FALLBACK_MAX_FLIGHTS_PER_RUN = 30


async def archive_once(
    store: Store,
    settings: Settings,
    *,
    archive_horizon_seconds: int = DEFAULT_ARCHIVE_HORIZON_SECONDS,
) -> tuple[int, int]:
    """Perform one archival pass. Returns (samples_written, aircraft_touched)."""
    now = int(time.time())
    # Anything older than the Redis TTL is gone from the hot tier; anything
    # newer than the tail-buffer hasn't aged out of the hot tier yet and is
    # cheaper to leave there. The middle slice is what we archive.
    hot_horizon = now - settings.track_ttl_seconds
    tail_start = max(hot_horizon, now - archive_horizon_seconds)
    tail_end = now - ARCHIVE_TAIL_BUFFER_SECONDS

    if tail_end <= tail_start:
        return 0, 0

    icao24s = await store.list_aircraft()
    if not icao24s:
        return 0, 0

    total_written = 0
    touched = 0
    # Process in chunks so we don't hold the SQLite write lock forever.
    chunk_size = 50
    with db.db_session(settings.database_path) as conn:
        for offset in range(0, len(icao24s), chunk_size):
            chunk = icao24s[offset : offset + chunk_size]
            groups = await store.bulk_get_tracks(chunk, tail_start, tail_end)
            for icao24, samples in zip(chunk, groups):
                if not samples:
                    continue
                written = db.archive_track_samples(conn, icao24, samples)
                if written:
                    total_written += written
                    touched += 1
            conn.commit()

    return total_written, touched


async def prune_once(
    settings: Settings,
    *,
    horizon_seconds: int = DEFAULT_ARCHIVE_HORIZON_SECONDS,
) -> int:
    cutoff = int(time.time()) - horizon_seconds
    with db.db_session(settings.database_path) as conn:
        deleted = db.prune_track_archive(conn, cutoff)
        conn.commit()
    return deleted


async def gap_fill_once(
    store: Store,
    settings: Settings,
    *,
    horizon_seconds: int = DEFAULT_ARCHIVE_HORIZON_SECONDS,
    max_snapshots: int = GAP_FILL_MAX_SNAPSHOTS_PER_RUN,
) -> dict:
    """Detect minute-bucket gaps in the archive (per active monitor's bbox)
    over the last `horizon_seconds` and fetch them from OpenSky.

    Returns a stats dict for logging.
    """
    if not settings.opensky_historical_enabled:
        return {"skipped": "opensky_historical_disabled"}
    if not all(settings.opensky_credentials()):
        return {"skipped": "opensky_no_credentials"}

    # Respect the unavailability flag the live backfill sets when OpenSky
    # returns 401/403 on historical queries (account doesn't have the quota /
    # tier for `time=` lookups). No point hammering them.
    unavailable = await store.get_cache("opensky_historical_unavailable")
    if isinstance(unavailable, dict):
        return {"skipped": "opensky_historical_unavailable", "reason": unavailable.get("reason")}
    if await store.get_cache("opensky_auth_failed"):
        return {"skipped": "opensky_auth_failed"}

    # Lazy imports: opensky has its own dependencies and we don't want to
    # import them at module load time.
    import httpx

    from .opensky import OpenSkyClient, OpenSkyRateLimited

    now = int(time.time())
    window_start = now - horizon_seconds

    # Active monitors give us a list of bboxes to check. If no monitors are
    # registered (e.g. the system just started), skip — there's no user
    # demand to satisfy yet.
    monitors = await store.active_monitors()
    if not monitors:
        return {"skipped": "no_active_monitors"}

    # Group monitors by bbox so two airports with overlapping bboxes don't
    # cost us two OpenSky calls for the same snapshot.
    from .worker import monitor_poll_groups

    groups = monitor_poll_groups(monitors, settings.bbox_merge_distance_nm)

    total_fetched = 0
    total_written = 0
    total_rate_limited = 0
    snapshots_budget = max_snapshots
    client = OpenSkyClient(settings)
    try:
        with db.db_session(settings.database_path) as conn:
            # Buckets we already have data for. We treat a bucket as "covered"
            # if any sample exists in that minute — small false positives are
            # fine (a bucket may be partial), but avoid re-fetching what we
            # have already.
            covered_buckets = _covered_minute_buckets(conn, window_start, now)
        for group in groups:
            if snapshots_budget <= 0:
                break
            bbox = tuple(group["bbox"])
            ts = window_start
            while ts < now - ARCHIVE_TAIL_BUFFER_SECONDS and snapshots_budget > 0:
                bucket = ts // GAP_FILL_STEP_SECONDS
                if bucket in covered_buckets:
                    ts += GAP_FILL_STEP_SECONDS
                    continue
                try:
                    states = await client.states_bbox(bbox, at_ts=ts)
                except OpenSkyRateLimited as exc:
                    total_rate_limited += 1
                    LOGGER.warning(
                        "gap_fill: rate limited at ts=%d (retry %ss); deferring",
                        ts,
                        getattr(exc, "retry_after_seconds", 5),
                    )
                    return {
                        "fetched": total_fetched,
                        "written": total_written,
                        "rate_limited": total_rate_limited,
                        "budget_remaining": snapshots_budget,
                        "stopped": "rate_limited",
                    }
                except httpx.HTTPStatusError as exc:
                    status = exc.response.status_code
                    if status in {401, 403}:
                        # OpenSky historical access denied — set the same
                        # unavailability flag the live backfill respects so
                        # neither path keeps hammering until the cache expires.
                        await store.set_cache(
                            "opensky_historical_unavailable",
                            {
                                "status": status,
                                "reason": "OpenSky rejected historical state vector access.",
                            },
                            settings.opensky_historical_cache_seconds,
                        )
                        LOGGER.warning(
                            "gap_fill: OpenSky historical access denied (HTTP %d) — "
                            "caching unavailability and stopping",
                            status,
                        )
                        return {
                            "fetched": total_fetched,
                            "written": total_written,
                            "rate_limited": total_rate_limited,
                            "budget_remaining": snapshots_budget,
                            "stopped": f"http_{status}",
                        }
                    LOGGER.warning("gap_fill: OpenSky HTTP %d at ts=%d", status, ts)
                    ts += GAP_FILL_STEP_SECONDS
                    continue
                except Exception:  # noqa: BLE001
                    LOGGER.warning("gap_fill: OpenSky error at ts=%d", ts, exc_info=True)
                    ts += GAP_FILL_STEP_SECONDS
                    continue
                snapshots_budget -= 1
                total_fetched += 1
                if states:
                    by_icao: dict[str, list[dict]] = {}
                    for sample in states:
                        by_icao.setdefault(sample["icao24"], []).append(sample)
                    with db.db_session(settings.database_path) as conn:
                        for icao24, samples in by_icao.items():
                            total_written += db.archive_track_samples(conn, icao24, samples)
                        conn.commit()
                covered_buckets.add(bucket)
                ts += GAP_FILL_STEP_SECONDS
                await asyncio.sleep(GAP_FILL_REQUEST_PAUSE_SECONDS)
    finally:
        await client.close()

    return {
        "fetched": total_fetched,
        "written": total_written,
        "rate_limited": total_rate_limited,
        "budget_remaining": snapshots_budget,
    }


async def gap_fill_via_flightaware_once(
    store: Store,
    settings: Settings,
    *,
    horizon_seconds: int = DEFAULT_ARCHIVE_HORIZON_SECONDS,
    max_flights: int = FA_FALLBACK_MAX_FLIGHTS_PER_RUN,
    only_airport: str | None = None,
    force: bool = False,
) -> dict:
    """Backfill the archive by enumerating flights at each active monitor's
    airport via FlightAware AeroAPI, then pulling per-flight track data.

    Used when OpenSky historical access is unavailable. Each AeroAPI call
    costs credits, so we cap aggressively per run.

    `only_airport` (optional ICAO) restricts processing to a single airport —
    used by the on-demand trigger that fires the first time a user scans a
    fresh location, so we don't sit and wait 15 minutes for the next scheduled
    sweep to reach that airport.

    `force` bypasses the flightaware_enabled gate — used by the cold-start
    backfill path so a one-shot fill can fire on a brand-new airport even
    while FA is globally off for cost reasons.
    """
    if not settings.flightaware_api_key:
        return {"skipped": "flightaware_no_key"}
    if not settings.flightaware_enabled and not force:
        # Cost gate — opt-in via FLIGHTAWARE_ENABLED=true.
        return {"skipped": "flightaware_disabled"}

    from .flightaware import FlightAwareClient

    now = int(time.time())
    window_start = now - horizon_seconds
    window_end = now - ARCHIVE_TAIL_BUFFER_SECONDS
    if window_end <= window_start:
        return {"skipped": "buffer_inverts_window"}

    if only_airport:
        airport_icaos = [only_airport.strip().upper()]
    else:
        monitors = await store.active_monitors()
        if not monitors:
            return {"skipped": "no_active_monitors"}
        airport_icaos = sorted(
            {monitor.get("airport_icao") for monitor in monitors if monitor.get("airport_icao")}
        )
    if not airport_icaos:
        return {"skipped": "no_airports"}

    # Precompute the minute-buckets we already have so we can decide whether
    # this airport's window is worth a FlightAware spend.
    with db.db_session(settings.database_path) as conn:
        covered_buckets = _covered_minute_buckets(conn, window_start, now)

    client = FlightAwareClient(settings)
    flights_processed = 0
    flights_seen = 0
    samples_written = 0
    aircraft_touched: set[str] = set()
    fa_calls = 0
    error_reason: str | None = None
    try:
        for airport_icao in airport_icaos:
            if flights_processed >= max_flights:
                break
            try:
                flights: list[dict] = []
                for kind in ("departures", "arrivals"):
                    rows = await client.airport_flights(airport_icao, window_start, window_end, kind=kind)
                    fa_calls += 1
                    flights.extend(rows)
                    if client.auth_failed or client.rate_limited:
                        error_reason = "auth_failed" if client.auth_failed else "rate_limited"
                        break
                if error_reason:
                    break
            except Exception:  # noqa: BLE001
                LOGGER.exception("gap_fill_fa: airport_flights failed for %s", airport_icao)
                continue
            # Dedupe by fa_flight_id so we don't fetch a flight twice if it
            # shows up in both departures and arrivals.
            unique: dict[str, dict] = {}
            for flight in flights:
                fa_id = flight.get("fa_flight_id") or flight.get("ident")
                if fa_id and fa_id not in unique:
                    unique[fa_id] = flight
            flights_seen += len(unique)

            # Pre-resolve icao24 for every flight (one short DB read). We skip
            # flights we can't identify rather than archiving them under bogus
            # keys; the daily FAA registry import is the real fix for those.
            with db.db_session(settings.database_path) as conn:
                resolved: list[tuple[str, str, dict]] = []
                unresolved = 0
                for fa_id, flight in unique.items():
                    icao24 = _resolve_icao24_for_flight(conn, flight)
                    if icao24:
                        resolved.append((fa_id, icao24, flight))
                    else:
                        unresolved += 1
            if unresolved:
                LOGGER.info(
                    "gap_fill_fa: %d flights unresolved at %s (no Mode-S hex; need FAA registry or live observation)",
                    unresolved, airport_icao,
                )

            for fa_id, icao24, flight in resolved:
                if flights_processed >= max_flights:
                    break
                try:
                    samples = await client.flight_track(fa_id)
                    fa_calls += 1
                except Exception:  # noqa: BLE001
                    LOGGER.exception("gap_fill_fa: flight_track failed for %s", fa_id)
                    continue
                if client.auth_failed or client.rate_limited:
                    error_reason = "auth_failed" if client.auth_failed else "rate_limited"
                    break
                if not samples:
                    flights_processed += 1
                    continue
                # Clip to the gap window; AeroAPI may return positions outside
                # the requested span (full flight track).
                clipped = [
                    s for s in samples
                    if s.get("timestamp") and window_start <= s["timestamp"] <= now
                ]
                if not clipped:
                    flights_processed += 1
                    continue
                # Skip if this flight's track is entirely in already-covered
                # buckets — saves the writer some work.
                if all(
                    (s["timestamp"] // GAP_FILL_STEP_SECONDS) in covered_buckets
                    for s in clipped
                ):
                    flights_processed += 1
                    continue
                with db.db_session(settings.database_path) as conn:
                    samples_written += db.archive_track_samples(conn, icao24, clipped)
                    conn.commit()
                aircraft_touched.add(icao24)
                flights_processed += 1
                for s in clipped:
                    covered_buckets.add(s["timestamp"] // GAP_FILL_STEP_SECONDS)
                await asyncio.sleep(GAP_FILL_REQUEST_PAUSE_SECONDS)
            if error_reason:
                break
    finally:
        # Surface the FA client's local rate-limit / auth state to the global
        # cache so other call paths (on-demand backfill trigger, the cache the
        # client itself reads on next instantiation) skip until cooldown.
        if client.rate_limited:
            await store.set_cache("flightaware_rate_limited", True, 600)
        if client.auth_failed:
            await store.set_cache("flightaware_auth_failed", True, 600)
        await client.close()

    return {
        "source": "flightaware",
        "fa_calls": fa_calls,
        "flights_seen": flights_seen,
        "flights_processed": flights_processed,
        "samples_written": samples_written,
        "aircraft_touched": len(aircraft_touched),
        "error_reason": error_reason,
    }


def _resolve_icao24_for_flight(conn, flight: dict) -> str | None:
    """Find the Mode-S hex for an AeroAPI flight payload, using whichever
    local store knows it. AeroAPI's airport-flight listings give us the
    tail number / N-number but not the Mode-S code — so we look up sideways:
    1. Any hex field that happens to be present in the payload.
    2. The FAA aircraft registry (`aircraft_registry.icao_hex` keyed on n_number).
    3. The observed-identity table (live ADS-B has paired this N-number with
       a Mode-S hex within our retention window).
    """
    direct = _flight_icao24(flight)
    if direct:
        return direct

    raw_n = flight.get("registration") or flight.get("ident")
    if not isinstance(raw_n, str):
        return None
    # Lazy import — avoid pulling registry.normalize at module load.
    from .registry import normalize as registry_norm

    n_number = registry_norm.normalize_n_number(raw_n)
    if not n_number:
        return None

    # 1. FAA aircraft registry (most authoritative; populated by registry importer)
    registry_row = db.get_registry_by_n_number(conn, n_number)
    if registry_row and registry_row.get("icao_hex"):
        return registry_row["icao_hex"].lower()

    # 2. Observed-identity table (populated by live ADS-B polling)
    row = conn.execute(
        """
        SELECT icao_hex FROM aircraft_observed_identity
        WHERE normalized_n_number = ?
        ORDER BY confidence DESC, last_seen_at DESC
        LIMIT 1
        """,
        (n_number,),
    ).fetchone()
    if row and row["icao_hex"]:
        return row["icao_hex"].lower()

    return None


def _flight_icao24(flight: dict) -> str | None:
    """Best-effort Mode-S hex extraction from an AeroAPI flight payload.

    AeroAPI's schema has shifted across versions; check a few field paths.
    Returns lowercase 6-char hex if found, else None.
    """
    candidates = [
        flight.get("hex_id"),
        (flight.get("aircraft") or {}).get("mode_s_code") if isinstance(flight.get("aircraft"), dict) else None,
        (flight.get("aircraft") or {}).get("hex") if isinstance(flight.get("aircraft"), dict) else None,
        flight.get("icao_address"),
        flight.get("mode_s_code"),
    ]
    for value in candidates:
        if isinstance(value, str) and len(value) == 6:
            return value.lower()
    return None


def _covered_minute_buckets(conn, start_ts: int, end_ts: int) -> set[int]:
    """Return the set of minute-buckets that already have at least one sample."""
    rows = conn.execute(
        f"""
        SELECT DISTINCT timestamp / {GAP_FILL_STEP_SECONDS} AS bucket
        FROM track_archive
        WHERE timestamp BETWEEN ? AND ?
        """,
        (int(start_ts), int(end_ts)),
    ).fetchall()
    return {int(row["bucket"]) for row in rows}


async def archive_loop(
    store: Store,
    settings: Settings,
    *,
    archive_interval_seconds: int = DEFAULT_ARCHIVE_INTERVAL_SECONDS,
    prune_interval_seconds: int = DEFAULT_PRUNE_INTERVAL_SECONDS,
    archive_horizon_seconds: int = DEFAULT_ARCHIVE_HORIZON_SECONDS,
    gap_fill_interval_seconds: int = DEFAULT_GAP_FILL_INTERVAL_SECONDS,
) -> None:
    """Long-running task: archive on a fast cadence, prune + gap-fill on slow ones.

    - archive_once: cheap, every `archive_interval_seconds` (~5 min)
    - prune_once: cheap, every `prune_interval_seconds` (~1 hour)
    - gap_fill_once: pulls from OpenSky to backfill gaps, every
      `gap_fill_interval_seconds` (~15 min). Skips when there are no active
      monitors or OpenSky is unavailable.
    """
    last_prune = 0.0
    last_gap_fill = 0.0
    while True:
        try:
            written, touched = await archive_once(
                store, settings, archive_horizon_seconds=archive_horizon_seconds
            )
            if written:
                LOGGER.info(
                    "track archive: wrote %d samples across %d aircraft",
                    written,
                    touched,
                )

            if time.time() - last_prune >= prune_interval_seconds:
                deleted = await prune_once(settings, horizon_seconds=archive_horizon_seconds)
                last_prune = time.time()
                if deleted:
                    LOGGER.info("track archive: pruned %d samples older than horizon", deleted)

            if time.time() - last_gap_fill >= gap_fill_interval_seconds:
                stats = await gap_fill_once(
                    store, settings, horizon_seconds=archive_horizon_seconds
                )
                LOGGER.info("track archive: gap-fill (opensky) stats=%s", stats)
                # If OpenSky bailed (unavailable / rate-limited / 401-403),
                # try FlightAware as a fallback. AeroAPI is per-call billed
                # so it's gated by FA_FALLBACK_MAX_FLIGHTS_PER_RUN.
                if stats.get("skipped") in {
                    "opensky_historical_unavailable",
                    "opensky_auth_failed",
                } or stats.get("stopped"):
                    fa_stats = await gap_fill_via_flightaware_once(
                        store, settings, horizon_seconds=archive_horizon_seconds
                    )
                    LOGGER.info("track archive: gap-fill (flightaware) stats=%s", fa_stats)
                last_gap_fill = time.time()
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 — never break the loop
            LOGGER.exception("track archive: pass failed, continuing")
        await asyncio.sleep(archive_interval_seconds)


# --- Read-side merge --------------------------------------------------------


def merge_hot_and_cold_samples(
    hot_samples: list[dict],
    cold_samples: Iterable[dict],
) -> list[dict]:
    """Union Redis (hot) and SQLite (cold) samples, deduped by timestamp.

    Hot wins on collision — Redis has the freshest data and the
    most-complete fields (the archive may drop fields we don't model in
    SQLite columns).
    """
    by_ts: dict[int, dict] = {}
    for sample in cold_samples:
        ts = sample.get("timestamp")
        if ts is None:
            continue
        by_ts[int(ts)] = sample
    for sample in hot_samples:
        ts = sample.get("timestamp")
        if ts is None:
            continue
        by_ts[int(ts)] = sample  # hot wins
    return sorted(by_ts.values(), key=lambda s: s["timestamp"])
