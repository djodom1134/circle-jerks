"""Production observability metrics for circlejerks.live.

An INDEPENDENT view of system health, assembled purely from state the worker
publishes to the store — so it stays truthful even when a loop wedges:

- **Data-source uptime**: per live source, success rate, freshness of the last
  good fetch, latency, and backoff — from the `live_sources:health` snapshot
  the ingest loop writes each tick.
- **Independent loop monitor**: liveness of the ingest and detect loops from the
  heartbeats they publish. This endpoint reads those heartbeats from a separate
  process, so a stale/absent heartbeat is a real "the loop died" signal, not
  something the loop could paper over.
- **Live-update latency**: how long since ingestion last refreshed live
  positions (the age of the ingest heartbeat).
- **Site responsiveness**: `server_time` lets a caller compute round-trip
  latency, and the endpoint itself is a handful of cache reads (no DB, no
  detectors) so it stays fast under load.
"""
from __future__ import annotations

import time

from .settings import Settings
from .store import Store

INGEST_HEARTBEAT_KEY = "worker:heartbeat:ingest"
DETECT_HEARTBEAT_KEY = "worker:heartbeat:detect"
LIVE_HEALTH_KEY = "live_sources:health"

_DEAD_LOOP = {
    "alive": False,
    "last_tick_ts": None,
    "age_seconds": None,
    "last_duration_ms": None,
    "ok": None,
    "error": None,
}


def _coarse_error(err) -> str | None:
    """Reduce an arbitrary error string to a safe category. This endpoint is
    public, and raw exception text can embed internal URLs/hosts (e.g. the
    self-hosted feeder); expose only the category, never the free text."""
    if not err:
        return None
    text = str(err).lower()
    if text.startswith("rate_limited") or "rate limit" in text:
        return "rate_limited"
    if "unavailable" in text or "no live source" in text or "stale" in text:
        return "unavailable"
    return "error"


def _loop_status(heartbeat, interval_seconds: int, now: int) -> dict:
    if not isinstance(heartbeat, dict) or heartbeat.get("ts") is None:
        return dict(_DEAD_LOOP)
    try:
        ts = int(heartbeat["ts"])
    except (TypeError, ValueError):
        return dict(_DEAD_LOOP)
    age = max(0, now - ts)
    # Alive if we've seen a tick within a few cadences — one skipped tick (e.g.
    # a slow detector pass) must not flip the loop to "dead".
    stale_after = max(30, interval_seconds * 3)
    return {
        "alive": age <= stale_after,
        "last_tick_ts": ts,
        "age_seconds": age,
        "last_duration_ms": heartbeat.get("duration_ms"),
        "ok": heartbeat.get("ok"),
        "error": _coarse_error(heartbeat.get("error")),
    }


def _source_kpis(sources, settings: Settings, now: int) -> tuple[dict, int]:
    kpis: dict = {}
    healthy = 0
    fresh_within = max(60, settings.live_poll_interval_seconds * 6)
    items = sources.items() if isinstance(sources, dict) else []
    for name, stats in items:
        if not isinstance(stats, dict):
            continue
        success = int(stats.get("success_count") or 0)
        errors = int(stats.get("error_count") or 0)
        total = success + errors
        last_success_ts = stats.get("last_success_ts")
        last_success_age = int(now - int(last_success_ts)) if last_success_ts else None
        is_healthy = last_success_age is not None and last_success_age <= fresh_within
        if is_healthy:
            healthy += 1
        kpis[name] = {
            "available": bool(stats.get("available")),
            "healthy": is_healthy,
            "success_count": success,
            "error_count": errors,
            "success_rate": round(success / total, 4) if total else None,
            "last_success_age_seconds": last_success_age,
            "last_latency_ms": stats.get("last_latency_ms"),
            "last_states_count": stats.get("last_states_count"),
            "backoff_remaining_seconds": stats.get("backoff_remaining_seconds"),
            "last_error": _coarse_error(stats.get("last_error")),
        }
    return kpis, healthy


async def build_health_metrics(store: Store, settings: Settings) -> dict:
    """Assemble the monitoring payload. Cheap: three cache reads, no DB."""
    now = int(time.time())
    ingest_hb = await store.get_cache(INGEST_HEARTBEAT_KEY)
    detect_hb = await store.get_cache(DETECT_HEARTBEAT_KEY)
    sources = await store.get_cache(LIVE_HEALTH_KEY)

    ingest_status = _loop_status(ingest_hb, settings.live_poll_interval_seconds, now)
    detect_status = _loop_status(detect_hb, max(5, settings.detector_interval_seconds), now)
    source_kpis, healthy_sources = _source_kpis(sources, settings, now)

    # Overall health: positions are ingesting AND at least one live source is
    # serving fresh data. Detection lagging degrades event freshness but does
    # not by itself take the site "down", so it's reported but not gating.
    overall_healthy = ingest_status["alive"] and healthy_sources > 0

    return {
        "server_time": now,
        "healthy": overall_healthy,
        "loops": {
            "ingest": ingest_status,
            "detect": detect_status,
        },
        "sources": source_kpis,
        "sources_healthy_count": healthy_sources,
        "sources_total": len(source_kpis),
        # How long since live positions last refreshed — the user-visible
        # "live update latency".
        "live_update_latency_seconds": ingest_status["age_seconds"],
    }
