from __future__ import annotations

import json
import time
from abc import ABC, abstractmethod
from collections import defaultdict
from typing import Any

from redis.asyncio import Redis


def dumps(value: Any) -> str:
    return json.dumps(value, separators=(",", ":"), sort_keys=True)


def loads(value: str | bytes) -> Any:
    if isinstance(value, bytes):
        value = value.decode("utf-8")
    return json.loads(value)


class Store(ABC):
    @abstractmethod
    async def close(self) -> None:
        ...

    @abstractmethod
    async def add_track_sample(self, icao24: str, sample: dict, ttl: int) -> None:
        ...

    @abstractmethod
    async def get_track(self, icao24: str, start_ts: int | None = None, end_ts: int | None = None) -> list[dict]:
        ...

    async def bulk_get_tracks(
        self,
        icao24s: list[str],
        start_ts: int | None = None,
        end_ts: int | None = None,
    ) -> list[list[dict]]:
        """Default implementation calls get_track per icao24. Concrete stores
        (e.g. RedisStore) should override with a single batched round-trip."""
        results: list[list[dict]] = []
        for icao24 in icao24s:
            results.append(await self.get_track(icao24, start_ts, end_ts))
        return results

    @abstractmethod
    async def list_aircraft(self) -> list[str]:
        ...

    @abstractmethod
    async def add_event(self, monitor_hash: str, event: dict, ttl: int) -> None:
        ...

    @abstractmethod
    async def get_events(self, monitor_hash: str, start_ts: int, end_ts: int) -> list[dict]:
        ...

    @abstractmethod
    async def event_exists(self, monitor_hash: str, event_id: str) -> bool:
        ...

    async def existing_event_ids(self, monitor_hash: str) -> set[str]:
        """Default: derived from get_events. Subclasses may override for speed."""
        events = await self.get_events(monitor_hash, 0, 2_000_000_000)
        return {e["id"] for e in events if e.get("id")}

    @abstractmethod
    async def recent_event_exists(self, monitor_hash: str, icao24: str, event_type: str, since_ts: int) -> bool:
        ...

    @abstractmethod
    async def register_monitor(self, monitor_hash: str, monitor: dict, ttl: int) -> None:
        ...

    @abstractmethod
    async def active_monitors(self) -> list[dict]:
        ...

    @abstractmethod
    async def get_description(self, key: str) -> str | None:
        ...

    @abstractmethod
    async def set_description(self, key: str, text: str, ttl: int) -> None:
        ...

    @abstractmethod
    async def get_cache(self, key: str) -> Any | None:
        ...

    @abstractmethod
    async def set_cache(self, key: str, value: Any, ttl: int) -> None:
        ...

    @abstractmethod
    async def incr_counter(self, key: str, ttl: int) -> int:
        """Increment a fixed-window counter and return its new value.

        The TTL is applied on the first increment only, so the window expires
        `ttl` seconds after it opened rather than sliding forward on every hit.
        """


class RedisStore(Store):
    def __init__(self, url: str):
        self.redis = Redis.from_url(url, decode_responses=True)

    async def close(self) -> None:
        await self.redis.aclose()

    async def add_track_sample(self, icao24: str, sample: dict, ttl: int) -> None:
        key = f"track:{icao24.lower()}"
        await self.redis.zadd(key, {dumps(sample): int(sample["timestamp"])})
        await self.redis.zremrangebyscore(key, "-inf", int(time.time()) - ttl)
        await self.redis.expire(key, ttl)
        await self.redis.sadd("aircraft", icao24.lower())

    async def get_track(self, icao24: str, start_ts: int | None = None, end_ts: int | None = None) -> list[dict]:
        key = f"track:{icao24.lower()}"
        start = "-inf" if start_ts is None else start_ts
        end = "+inf" if end_ts is None else end_ts
        values = await self.redis.zrangebyscore(key, start, end)
        return [loads(value) for value in values]

    async def bulk_get_tracks(
        self,
        icao24s: list[str],
        start_ts: int | None = None,
        end_ts: int | None = None,
    ) -> list[list[dict]]:
        if not icao24s:
            return []
        start = "-inf" if start_ts is None else start_ts
        end = "+inf" if end_ts is None else end_ts
        async with self.redis.pipeline(transaction=False) as pipe:
            for icao24 in icao24s:
                pipe.zrangebyscore(f"track:{icao24.lower()}", start, end)
            raw_results = await pipe.execute()
        return [[loads(value) for value in (group or [])] for group in raw_results]

    async def list_aircraft(self) -> list[str]:
        # Derive from live track keys, NOT the "aircraft" set: that set is
        # appended to on every sample and never shrinks, so it accumulates
        # thousands of expired icao24 (4000+ vs ~900 live). Reading tracks for
        # all of them pipelined thousands of empty Redis ops per scan and was a
        # primary cause of multi-second scans. SCAN of track:* is accurate and
        # self-cleaning.
        icao24s: list[str] = []
        async for key in self.redis.scan_iter("track:*", count=1000):
            icao24s.append(key.split(":", 1)[1])
        return sorted(icao24s)

    async def add_event(self, monitor_hash: str, event: dict, ttl: int) -> None:
        key = f"events:{monitor_hash}"
        await self.redis.zadd(key, {dumps(event): int(event["timestamp"])})
        await self.redis.zremrangebyscore(key, "-inf", int(time.time()) - ttl)
        await self.redis.expire(key, ttl)

    async def get_events(self, monitor_hash: str, start_ts: int, end_ts: int) -> list[dict]:
        values = await self.redis.zrangebyscore(f"events:{monitor_hash}", start_ts, end_ts)
        return sorted((loads(value) for value in values), key=lambda event: event["timestamp"])

    async def event_exists(self, monitor_hash: str, event_id: str) -> bool:
        values = await self.redis.zrange(f"events:{monitor_hash}", 0, -1)
        return any(loads(value).get("id") == event_id for value in values)

    async def existing_event_ids(self, monitor_hash: str) -> set[str]:
        """All event IDs currently stored — fetched once so callers can do
        O(1) in-memory existence checks instead of re-scanning per event."""
        values = await self.redis.zrange(f"events:{monitor_hash}", 0, -1)
        ids: set[str] = set()
        for value in values:
            event_id = loads(value).get("id")
            if event_id:
                ids.add(event_id)
        return ids

    async def recent_event_exists(self, monitor_hash: str, icao24: str, event_type: str, since_ts: int) -> bool:
        events = await self.get_events(monitor_hash, since_ts, int(time.time()) + 60)
        return any(event["icao24"] == icao24.lower() and event["type"] == event_type for event in events)

    async def register_monitor(self, monitor_hash: str, monitor: dict, ttl: int) -> None:
        expires = int(time.time()) + ttl
        await self.redis.set(f"monitor:{monitor_hash}", dumps(monitor), ex=ttl)
        await self.redis.zadd("active_monitors", {monitor_hash: expires})
        await self.redis.zremrangebyscore("active_monitors", "-inf", int(time.time()))

    async def active_monitors(self) -> list[dict]:
        now = int(time.time())
        await self.redis.zremrangebyscore("active_monitors", "-inf", now)
        hashes = await self.redis.zrangebyscore("active_monitors", now, "+inf")
        monitors = []
        for monitor_hash in hashes:
            raw = await self.redis.get(f"monitor:{monitor_hash}")
            if raw:
                monitors.append(loads(raw))
        return monitors

    async def get_description(self, key: str) -> str | None:
        return await self.redis.get(f"desc:{key}")

    async def set_description(self, key: str, text: str, ttl: int) -> None:
        await self.redis.set(f"desc:{key}", text, ex=ttl)

    async def get_cache(self, key: str) -> Any | None:
        raw = await self.redis.get(f"cache:{key}")
        return loads(raw) if raw else None

    async def set_cache(self, key: str, value: Any, ttl: int) -> None:
        await self.redis.set(f"cache:{key}", dumps(value), ex=ttl)

    async def incr_counter(self, key: str, ttl: int) -> int:
        redis_key = f"count:{key}"
        value = await self.redis.incr(redis_key)
        if value == 1:
            await self.redis.expire(redis_key, max(ttl, 1))
        return int(value)


class MemoryStore(Store):
    def __init__(self) -> None:
        self.tracks: dict[str, list[dict]] = defaultdict(list)
        self.events: dict[str, list[dict]] = defaultdict(list)
        self.monitors: dict[str, tuple[dict, int]] = {}
        self.descriptions: dict[str, tuple[str, int]] = {}
        self.cache: dict[str, tuple[Any, int]] = {}
        self.counters: dict[str, tuple[int, int]] = {}

    async def close(self) -> None:
        return None

    async def add_track_sample(self, icao24: str, sample: dict, ttl: int) -> None:
        key = icao24.lower()
        cutoff = int(time.time()) - ttl
        self.tracks[key] = [row for row in self.tracks[key] if row["timestamp"] >= cutoff]
        if not any(row["timestamp"] == sample["timestamp"] for row in self.tracks[key]):
            self.tracks[key].append(sample)
            self.tracks[key].sort(key=lambda row: row["timestamp"])

    async def get_track(self, icao24: str, start_ts: int | None = None, end_ts: int | None = None) -> list[dict]:
        rows = self.tracks.get(icao24.lower(), [])
        if start_ts is None and end_ts is None:
            return list(rows)
        return [
            row for row in rows
            if (start_ts is None or row["timestamp"] >= start_ts)
            and (end_ts is None or row["timestamp"] <= end_ts)
        ]

    async def list_aircraft(self) -> list[str]:
        return sorted(self.tracks.keys())

    async def add_event(self, monitor_hash: str, event: dict, ttl: int) -> None:
        cutoff = int(time.time()) - ttl
        self.events[monitor_hash] = [row for row in self.events[monitor_hash] if row["timestamp"] >= cutoff]
        event_id = event.get("id")
        if not any(row.get("id") == event_id for row in self.events[monitor_hash]):
            self.events[monitor_hash].append(event)
            self.events[monitor_hash].sort(key=lambda row: row["timestamp"])

    async def get_events(self, monitor_hash: str, start_ts: int, end_ts: int) -> list[dict]:
        return [
            event for event in self.events.get(monitor_hash, [])
            if start_ts <= event["timestamp"] <= end_ts
        ]

    async def event_exists(self, monitor_hash: str, event_id: str) -> bool:
        return any(event.get("id") == event_id for event in self.events.get(monitor_hash, []))

    async def recent_event_exists(self, monitor_hash: str, icao24: str, event_type: str, since_ts: int) -> bool:
        return any(
            event["icao24"] == icao24.lower()
            and event["type"] == event_type
            and event["timestamp"] >= since_ts
            for event in self.events.get(monitor_hash, [])
        )

    async def register_monitor(self, monitor_hash: str, monitor: dict, ttl: int) -> None:
        self.monitors[monitor_hash] = (monitor, int(time.time()) + ttl)

    async def active_monitors(self) -> list[dict]:
        now = int(time.time())
        self.monitors = {
            key: value for key, value in self.monitors.items() if value[1] > now
        }
        return [monitor for monitor, _ in self.monitors.values()]

    async def get_description(self, key: str) -> str | None:
        row = self.descriptions.get(key)
        if not row:
            return None
        text, expires = row
        if expires <= int(time.time()):
            self.descriptions.pop(key, None)
            return None
        return text

    async def set_description(self, key: str, text: str, ttl: int) -> None:
        self.descriptions[key] = (text, int(time.time()) + ttl)

    async def get_cache(self, key: str) -> Any | None:
        row = self.cache.get(key)
        if not row:
            return None
        value, expires = row
        if expires <= int(time.time()):
            self.cache.pop(key, None)
            return None
        return value

    async def set_cache(self, key: str, value: Any, ttl: int) -> None:
        self.cache[key] = (value, int(time.time()) + ttl)

    async def incr_counter(self, key: str, ttl: int) -> int:
        now = int(time.time())
        row = self.counters.get(key)
        if not row or row[1] <= now:
            self.counters[key] = (1, now + ttl)
            return 1
        value, expires = row
        self.counters[key] = (value + 1, expires)
        return value + 1


def make_store(redis_url: str) -> Store:
    if not redis_url or redis_url == "memory://":
        return MemoryStore()
    return RedisStore(redis_url)
