#!/usr/bin/env python3
"""Production watchdog for CircleJerks.

Runs every minute via cron. Watches two failure modes that have wedged the
site in production:

1. **Redis pressure** — Managed Valkey has `noeviction` and a hard 418MB cap.
   Once `used_memory >= maxmemory`, every write fails and scan/SQLite cascade.
   This script proactively trims the oldest `track:*` keys when usage crosses
   75%, before the OOM cliff. Track samples are reproducible from live ADS-B
   feeds within minutes, so trimming is safe.

2. **API responsiveness** — `/healthz` should answer in under a second. If it
   doesn't, the existing `check_production_health.sh` cron will restart the
   container. We log enough state here so a post-mortem can tell whether the
   restart was justified.

Run via cron inside the api container:
    docker compose exec api python /app/scripts/watchdog.py

…or from the host (preferred — survives api container restarts):
    docker compose exec -T api python -m scripts.watchdog
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
import urllib.request


HIGH_WATERMARK = 0.75  # Trim once we cross this fraction of maxmemory.
EMERGENCY_WATERMARK = 0.92  # Aggressive flush above this — site is about to wedge.
TARGET_AFTER_TRIM = 0.55  # Trim until used/max falls below this.
TRACK_KEY_PREFIX = "track:"
HEALTHZ_URL = os.environ.get("CIRCLEJERK_HEALTHZ_URL", "http://api:8000/healthz")


async def check_redis(log) -> None:
    # Import lazily — app imports take ~1s and most invocations are no-ops.
    from app.store import RedisStore

    redis_url = os.environ.get("CIRCLEJERK_REDIS_URL")
    if not redis_url:
        log("redis: no CIRCLEJERK_REDIS_URL configured, skipping")
        return

    store = RedisStore(redis_url)
    try:
        info = await store.redis.info("memory")
        used = int(info.get("used_memory") or 0)
        maxm = int(info.get("maxmemory") or 0)
        if maxm <= 0:
            log(f"redis: used={used} bytes, maxmemory=0 (unlimited), skipping")
            return
        pct = used / maxm
        log(f"redis: used={used / 1e6:.1f}MB maxmemory={maxm / 1e6:.1f}MB ({pct * 100:.1f}%)")
        if pct < HIGH_WATERMARK:
            return

        # Collect track:* keys with their idle time. Iterate in batches so a
        # single watchdog tick doesn't take forever on a large keyspace.
        candidates: list[tuple[int, str]] = []
        async for key in store.redis.scan_iter(f"{TRACK_KEY_PREFIX}*", count=500):
            try:
                idle = await store.redis.object("idletime", key)
            except Exception:  # noqa: BLE001
                idle = 0
            candidates.append((int(idle or 0), key))
        # Largest idle time first — these are the oldest, least-recently-touched
        # tracks and the cheapest to give up.
        candidates.sort(reverse=True)

        deleted = 0
        for _, key in candidates:
            try:
                await store.redis.delete(key)
                deleted += 1
            except Exception:  # noqa: BLE001
                continue
            if deleted % 50 == 0:
                info = await store.redis.info("memory")
                used = int(info.get("used_memory") or 0)
                pct_now = used / maxm
                if pct_now < TARGET_AFTER_TRIM:
                    break

        info = await store.redis.info("memory")
        used = int(info.get("used_memory") or 0)
        log(
            f"redis: trimmed {deleted} track keys, "
            f"now used={used / 1e6:.1f}MB ({used / maxm * 100:.1f}%)"
        )

        # If still over the emergency line, drop the scan_response cache too —
        # last resort before write rejection. Scan responses are reproducible.
        if used / maxm >= EMERGENCY_WATERMARK:
            cache_deleted = 0
            async for key in store.redis.scan_iter("cache:scan_response:*", count=500):
                await store.redis.delete(key)
                cache_deleted += 1
            log(f"redis: emergency-trimmed {cache_deleted} scan_response cache keys")
    finally:
        await store.close()


def check_api(log) -> None:
    t0 = time.monotonic()
    try:
        req = urllib.request.Request(HEALTHZ_URL, headers={"User-Agent": "circlejerk-watchdog"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            status = resp.status
            body_len = len(resp.read(1024))
        elapsed_ms = (time.monotonic() - t0) * 1000
        log(f"api: /healthz HTTP {status} in {elapsed_ms:.0f}ms (body {body_len}B)")
    except Exception as exc:  # noqa: BLE001
        elapsed_ms = (time.monotonic() - t0) * 1000
        log(f"api: /healthz FAILED after {elapsed_ms:.0f}ms: {type(exc).__name__}: {exc}")


def main() -> int:
    ts = time.strftime("%Y-%m-%dT%H:%M:%S")

    def log(line: str) -> None:
        print(f"[{ts}] {line}", flush=True)

    check_api(log)
    try:
        asyncio.run(check_redis(log))
    except Exception as exc:  # noqa: BLE001
        log(f"redis: watchdog crashed: {type(exc).__name__}: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
