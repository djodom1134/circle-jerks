from __future__ import annotations

import asyncio
import logging
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
                    written = await run_detectors_for_monitor(store, settings, conn, monitor)
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


async def run_forever() -> None:
    settings = get_settings()
    db.init_db(settings.database_path)
    store = make_store(settings.redis_url)
    live_sources = LiveStateClient(settings)
    try:
        while True:
            try:
                monitors = await store.active_monitors()
                if monitors:
                    with db_session(settings.database_path) as conn:
                        for group in monitor_poll_groups(monitors, settings.bbox_merge_distance_nm):
                            result = await live_sources.states_bbox(tuple(group["bbox"]))
                            states = result.states
                            for sample in states[: settings.max_aircraft_per_scan]:
                                await store.add_track_sample(sample["icao24"], sample, settings.track_ttl_seconds)
                            for monitor in group["monitors"]:
                                written = await run_detectors_for_monitor(store, settings, conn, monitor)
                                logger.info(
                                    "monitor=%s source=%s states=%s events=%s group_monitors=%s",
                                    monitor["hash"],
                                    result.source,
                                    len(states),
                                    written,
                                    len(group["monitors"]),
                                )
                await asyncio.sleep(effective_poll_interval_seconds(settings, live_sources))
            except LiveSourceRateLimited as exc:
                logger.warning("live sources rate limited; backing off for %ss", exc.retry_after_seconds)
                await asyncio.sleep(exc.retry_after_seconds)
            except LiveSourceUnavailable as exc:
                logger.warning("live sources unavailable: %s", exc)
                await asyncio.sleep(max(10, settings.live_poll_interval_seconds))
            except Exception:
                logger.exception("worker tick failed")
                await asyncio.sleep(max(10, settings.live_poll_interval_seconds))
    finally:
        await live_sources.close()
        await store.close()


def main() -> None:
    asyncio.run(run_forever())


if __name__ == "__main__":
    main()
