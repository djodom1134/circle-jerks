from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone

from . import db
from .db import db_session
from .geo import bbox_union
from .live_sources import LiveSourceRateLimited, LiveSourceUnavailable, LiveStateClient, bbox_distance_nm
from .services import run_detectors_for_monitor
from .settings import get_settings
from .store import make_store

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("circlejerk.worker")


def monitor_poll_groups(monitors: list[dict], merge_distance_nm: float = 5.0) -> list[dict]:
    groups: list[dict] = []
    sorted_monitors = sorted(
        monitors,
        key=lambda monitor: abs(
            (monitor["bbox"][2] - monitor["bbox"][0])
            * (monitor["bbox"][3] - monitor["bbox"][1])
        ),
        reverse=True,
    )
    for monitor in sorted_monitors:
        bbox = tuple(monitor["bbox"])
        group = next(
            (
                candidate for candidate in groups
                if bbox_distance_nm(bbox, tuple(candidate["bbox"])) <= merge_distance_nm
            ),
            None,
        )
        if group is None:
            groups.append({
                "airport_icao": monitor["airport_icao"],
                "airport_icaos": [monitor["airport_icao"]],
                "bbox": bbox,
                "monitors": [monitor],
            })
            continue
        group["bbox"] = bbox_union(tuple(group["bbox"]), bbox)
        group["monitors"].append(monitor)
        group["airport_icaos"] = sorted({
            *group.get("airport_icaos", [group["airport_icao"]]),
            monitor["airport_icao"],
        })
        group["airport_icao"] = ",".join(group["airport_icaos"])
    return groups


def effective_poll_interval_seconds(settings, live_sources: LiveStateClient | None = None) -> int:
    return max(1, settings.live_poll_interval_seconds)


async def run_once() -> int:
    settings = get_settings()
    db.init_db(settings.database_path)
    store = make_store(settings.redis_url)
    live_sources = LiveStateClient(settings)
    processed = 0
    try:
        monitors = await store.active_monitors()
        if not monitors:
            return 0
        with db_session(settings.database_path) as conn:
            for group in monitor_poll_groups(monitors, settings.bbox_merge_distance_nm):
                result = await live_sources.states_bbox(tuple(group["bbox"]))
                states = result.states
                for sample in states[: settings.max_aircraft_per_scan]:
                    await store.add_track_sample(sample["icao24"], sample, settings.track_ttl_seconds)
                for monitor in group["monitors"]:
                    written = (await run_detectors_for_monitor(store, settings, conn, monitor)).written
                    # Never carry a write transaction into the next group's
                    # HTTP fetch: it holds SQLite's single write lock and 500s
                    # the api's concurrent writers.
                    conn.commit()
                    processed += written
                    logger.info(
                        "monitor=%s source=%s states=%s events=%s group_monitors=%s at=%s",
                        monitor["hash"],
                        result.source,
                        len(states),
                        written,
                        len(group["monitors"]),
                        datetime.now(timezone.utc).isoformat(),
                    )
        return processed
    finally:
        await live_sources.close()
        await store.close()


async def _ingest_tick(store, settings, live_sources) -> None:
    """One live-position ingestion pass: fetch states per monitor group and
    write track samples. No detection, no db_session — ingestion is
    Redis-only so it never contends with the detector's SQLite writes and
    stays on its own fast, predictable cadence."""
    monitors = await store.active_monitors()
    if not monitors:
        return
    for group in monitor_poll_groups(monitors, settings.bbox_merge_distance_nm):
        try:
            result = await live_sources.states_bbox(tuple(group["bbox"]))
        except LiveSourceRateLimited:
            # Rate limiting is a source-wide condition, not a per-group one:
            # let it propagate so the ingest loop backs off for
            # retry_after_seconds instead of treating it as "no live source"
            # for this group and silently continuing.
            raise
        except LiveSourceUnavailable as exc:
            logger.warning(
                "group_monitors=%s airports=%s no_live_source=%s",
                len(group["monitors"]),
                group.get("airport_icaos") or [group["airport_icao"]],
                exc,
            )
            continue
        states = result.states
        for sample in states[: settings.max_aircraft_per_scan]:
            await store.add_track_sample(sample["icao24"], sample, settings.track_ttl_seconds)
    await store.set_cache(
        "live_sources:health",
        live_sources.health_snapshot(),
        max(60, settings.live_poll_interval_seconds * 4),
    )


async def _detect_tick(store, settings) -> None:
    """One detection pass over all active monitors' 4h event-detection
    window. Runs on its own (slower) cadence, decoupled from ingestion, so a
    slow/heavy detector run never starves live position updates."""
    monitors = await store.active_monitors()
    if not monitors:
        return
    with db_session(settings.database_path) as conn:
        for group in monitor_poll_groups(monitors, settings.bbox_merge_distance_nm):
            for monitor in group["monitors"]:
                try:
                    written = (await run_detectors_for_monitor(store, settings, conn, monitor)).written
                    # Never carry a write transaction into the next monitor's
                    # detector run: commit promptly so the lock is only held
                    # for the writes themselves.
                    conn.commit()
                except Exception:
                    logger.exception("detector run failed monitor=%s", monitor.get("hash"))
                    try:
                        conn.rollback()
                    except Exception:
                        logger.exception(
                            "rollback failed after detector failure monitor=%s", monitor.get("hash")
                        )
                    continue
                logger.info(
                    "monitor=%s events=%s group_monitors=%s at=%s",
                    monitor["hash"],
                    written,
                    len(group["monitors"]),
                    datetime.now(timezone.utc).isoformat(),
                )


async def _record_heartbeat(store, settings, name: str, *, ok: bool, started: float, error: str | None = None) -> None:
    """Publish a loop liveness heartbeat to the store so an INDEPENDENT monitor
    (the /health/metrics endpoint, a separate process) can tell whether this
    loop is still ticking — a stale/absent heartbeat is itself the alarm — and
    how long its last tick took. Never lets a monitoring write break the loop."""
    payload = {
        "ts": int(time.time()),
        "duration_ms": int((time.monotonic() - started) * 1000),
        "ok": ok,
        "error": error,
    }
    # TTL comfortably exceeds a healthy cadence so a MISSING key means "the loop
    # died", distinct from a present-but-stale one that reports its own age.
    ttl = max(60, settings.detector_interval_seconds * 6, settings.live_poll_interval_seconds * 6)
    try:
        await store.set_cache(f"worker:heartbeat:{name}", payload, ttl)
    except Exception:
        logger.exception("failed to write %s heartbeat", name)


async def _ingest_loop(store, settings, live_sources) -> None:
    while True:
        started = time.monotonic()
        try:
            await _ingest_tick(store, settings, live_sources)
            await _record_heartbeat(store, settings, "ingest", ok=True, started=started)
            await asyncio.sleep(effective_poll_interval_seconds(settings, live_sources))
        except LiveSourceRateLimited as exc:
            await _record_heartbeat(store, settings, "ingest", ok=False, started=started, error=f"rate_limited:{exc.retry_after_seconds}s")
            logger.warning("live sources rate limited; backing off for %ss", exc.retry_after_seconds)
            await asyncio.sleep(exc.retry_after_seconds)
        except LiveSourceUnavailable as exc:
            await _record_heartbeat(store, settings, "ingest", ok=False, started=started, error=str(exc)[:200])
            logger.warning("live sources unavailable: %s", exc)
            await asyncio.sleep(max(10, settings.live_poll_interval_seconds))
        except Exception as exc:
            await _record_heartbeat(store, settings, "ingest", ok=False, started=started, error=repr(exc)[:200])
            logger.exception("ingest tick failed")
            await asyncio.sleep(max(10, settings.live_poll_interval_seconds))


async def _detect_loop(store, settings) -> None:
    while True:
        started = time.monotonic()
        try:
            await _detect_tick(store, settings)
            await _record_heartbeat(store, settings, "detect", ok=True, started=started)
            await asyncio.sleep(max(5, settings.detector_interval_seconds))
        except Exception as exc:
            await _record_heartbeat(store, settings, "detect", ok=False, started=started, error=repr(exc)[:200])
            logger.exception("detect tick failed")
            await asyncio.sleep(max(5, settings.detector_interval_seconds))


async def run_forever() -> None:
    settings = get_settings()
    db.init_db(settings.database_path)
    store = make_store(settings.redis_url)
    live_sources = LiveStateClient(settings)
    # Background archiver: copy aging Redis track samples to the SQLite cold
    # tier so the "today" / 24h scan window survives Redis TTL expiry.
    from . import archive as track_archive

    archive_task = asyncio.create_task(
        track_archive.archive_loop(
            store,
            settings,
            archive_horizon_seconds=track_archive.horizon_seconds(settings),
        )
    )
    # Ingestion and detection run as separate concurrent tasks: heavy 4h
    # event detection must never starve live position ingestion (positions
    # need to refresh every ~10s regardless of how long a detector pass
    # takes).
    ingest_task = asyncio.create_task(_ingest_loop(store, settings, live_sources))
    detect_task = asyncio.create_task(_detect_loop(store, settings))
    try:
        await asyncio.gather(ingest_task, detect_task)
    finally:
        for task in (archive_task, ingest_task, detect_task):
            task.cancel()
        for task in (archive_task, ingest_task, detect_task):
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        await live_sources.close()
        await store.close()


def main() -> None:
    asyncio.run(run_forever())


if __name__ == "__main__":
    main()
