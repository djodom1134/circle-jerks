"""Production monitoring endpoint: data-source uptime, independent loop
liveness (ingest + detect heartbeats), and live-update latency.

The point of `build_health_metrics` is to be an INDEPENDENT observer: it reads
only what the worker publishes to the store, so a wedged loop can't hide — an
absent or stale heartbeat reports as not-alive.
"""
from __future__ import annotations

import time

import pytest

from app import monitoring
from app.monitoring import (
    DETECT_HEARTBEAT_KEY,
    INGEST_HEARTBEAT_KEY,
    LIVE_HEALTH_KEY,
    build_health_metrics,
)
from app.settings import Settings
from app.store import MemoryStore


@pytest.mark.asyncio
async def test_metrics_report_unhealthy_when_worker_never_ran():
    store = MemoryStore()
    settings = Settings(database_path=":memory:")

    metrics = await build_health_metrics(store, settings)

    assert metrics["healthy"] is False
    assert metrics["loops"]["ingest"]["alive"] is False
    assert metrics["loops"]["detect"]["alive"] is False
    assert metrics["loops"]["ingest"]["last_tick_ts"] is None
    assert metrics["sources_total"] == 0
    assert metrics["sources_healthy_count"] == 0
    assert metrics["live_update_latency_seconds"] is None
    assert isinstance(metrics["server_time"], int)


@pytest.mark.asyncio
async def test_metrics_report_healthy_with_fresh_heartbeats_and_source():
    store = MemoryStore()
    settings = Settings(database_path=":memory:", live_poll_interval_seconds=10, detector_interval_seconds=30)
    now = int(time.time())

    await store.set_cache(INGEST_HEARTBEAT_KEY, {"ts": now - 2, "duration_ms": 120, "ok": True, "error": None}, 600)
    await store.set_cache(DETECT_HEARTBEAT_KEY, {"ts": now - 5, "duration_ms": 900, "ok": True, "error": None}, 600)
    await store.set_cache(LIVE_HEALTH_KEY, {
        "adsb_lol": {
            "available": True,
            "success_count": 40,
            "error_count": 0,
            "last_success_ts": now - 2,
            "last_latency_ms": 180,
            "last_states_count": 12,
            "backoff_remaining_seconds": 0,
            "last_error": None,
        },
    }, 600)

    metrics = await build_health_metrics(store, settings)

    assert metrics["healthy"] is True
    assert metrics["loops"]["ingest"]["alive"] is True
    assert metrics["loops"]["ingest"]["last_duration_ms"] == 120
    assert metrics["loops"]["detect"]["alive"] is True
    assert metrics["live_update_latency_seconds"] == metrics["loops"]["ingest"]["age_seconds"] <= 3
    assert metrics["sources_healthy_count"] == 1
    src = metrics["sources"]["adsb_lol"]
    assert src["healthy"] is True
    assert src["success_rate"] == 1.0
    assert src["last_success_age_seconds"] <= 3


@pytest.mark.asyncio
async def test_stale_ingest_heartbeat_reports_not_alive():
    store = MemoryStore()
    settings = Settings(database_path=":memory:", live_poll_interval_seconds=10)
    now = int(time.time())
    # Older than 3 poll intervals -> stale -> not alive.
    await store.set_cache(INGEST_HEARTBEAT_KEY, {"ts": now - 300, "duration_ms": 100, "ok": True, "error": None}, 600)

    metrics = await build_health_metrics(store, settings)

    assert metrics["loops"]["ingest"]["alive"] is False
    assert metrics["loops"]["ingest"]["age_seconds"] >= 300
    assert metrics["healthy"] is False


@pytest.mark.asyncio
async def test_source_with_stale_success_counts_as_unhealthy():
    store = MemoryStore()
    settings = Settings(database_path=":memory:", live_poll_interval_seconds=10)
    now = int(time.time())
    await store.set_cache(INGEST_HEARTBEAT_KEY, {"ts": now - 1, "duration_ms": 100, "ok": True, "error": None}, 600)
    await store.set_cache(LIVE_HEALTH_KEY, {
        "opensky": {
            "available": True,
            "success_count": 3,
            "error_count": 5,
            "last_success_ts": now - 5000,  # long stale
            "last_latency_ms": 900,
            "last_states_count": 0,
            "backoff_remaining_seconds": 120,
            "last_error": "rate limited",
        },
    }, 600)

    metrics = await build_health_metrics(store, settings)

    assert metrics["sources"]["opensky"]["healthy"] is False
    assert metrics["sources"]["opensky"]["success_rate"] == round(3 / 8, 4)
    assert metrics["sources_healthy_count"] == 0
    # Ingest is alive but no fresh source -> overall not healthy.
    assert metrics["healthy"] is False


@pytest.mark.asyncio
async def test_worker_loop_writes_a_heartbeat_the_monitor_can_read():
    """End to end: the worker's _record_heartbeat publishes what
    build_health_metrics consumes, using the same store keys."""
    import app.worker as worker

    store = MemoryStore()
    settings = Settings(database_path=":memory:", live_poll_interval_seconds=10)
    started = time.monotonic()
    await worker._record_heartbeat(store, settings, "ingest", ok=True, started=started)

    metrics = await build_health_metrics(store, settings)
    assert metrics["loops"]["ingest"]["alive"] is True
    assert metrics["loops"]["ingest"]["ok"] is True


@pytest.mark.asyncio
async def test_record_heartbeat_survives_store_failure():
    """The single most safety-critical property: a failing monitoring write must
    NEVER propagate out of _record_heartbeat and break the worker loop."""
    import app.worker as worker

    class _RaisingStore(MemoryStore):
        async def set_cache(self, *args, **kwargs):
            raise RuntimeError("redis unavailable")

    settings = Settings(database_path=":memory:", live_poll_interval_seconds=10)
    # Must not raise despite the store blowing up on every write.
    await worker._record_heartbeat(
        _RaisingStore(), settings, "ingest", ok=True, started=time.monotonic()
    )
    await worker._record_heartbeat(
        _RaisingStore(), settings, "detect", ok=False, started=time.monotonic(),
        error="boom", min_ttl_seconds=300,
    )


@pytest.mark.asyncio
async def test_public_output_coarsens_error_text_no_leak():
    """The public /health/metrics must expose only a coarse error category —
    never raw exception text that can embed internal URLs/hosts."""
    store = MemoryStore()
    settings = Settings(database_path=":memory:", live_poll_interval_seconds=10)
    now = int(time.time())
    await store.set_cache(
        INGEST_HEARTBEAT_KEY,
        {"ts": now - 1, "duration_ms": 5, "ok": False,
         "error": "ConnectError: http://10.1.2.3:8080/feeder timed out"},
        600,
    )
    await store.set_cache(LIVE_HEALTH_KEY, {
        "self_hosted": {
            "available": True, "success_count": 1, "error_count": 1,
            "last_success_ts": now - 1, "last_latency_ms": 10,
            "last_states_count": 1, "backoff_remaining_seconds": 0,
            "last_error": "HTTPError http://10.1.2.3:8080 refused",
        },
    }, 600)

    metrics = await build_health_metrics(store, settings)
    assert metrics["loops"]["ingest"]["error"] == "error"
    assert metrics["sources"]["self_hosted"]["last_error"] == "error"
    assert "10.1.2.3" not in str(metrics)  # no internal host leaked anywhere

    # A rate-limit keeps its useful, non-sensitive category.
    await store.set_cache(
        INGEST_HEARTBEAT_KEY,
        {"ts": now - 1, "duration_ms": 5, "ok": False, "error": "rate_limited:300s"},
        600,
    )
    metrics2 = await build_health_metrics(store, settings)
    assert metrics2["loops"]["ingest"]["error"] == "rate_limited"


@pytest.mark.asyncio
async def test_loop_status_tolerates_corrupt_timestamp():
    store = MemoryStore()
    settings = Settings(database_path=":memory:")
    await store.set_cache(INGEST_HEARTBEAT_KEY, {"ts": "not-a-number", "ok": True}, 600)
    metrics = await build_health_metrics(store, settings)  # must not 500
    assert metrics["loops"]["ingest"]["alive"] is False
    assert metrics["loops"]["ingest"]["last_tick_ts"] is None
