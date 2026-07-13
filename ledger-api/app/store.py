"""A minimal, in-process cache used only to publish the nightly loop's
liveness heartbeat (see worker.py). This service has no live positions, no
monitors, no events -- the main API's Redis-backed `Store` abstraction does
not apply here, so this is deliberately not a copy of it.
"""
from __future__ import annotations

import time
from typing import Any


class MemoryStore:
    def __init__(self) -> None:
        self.cache: dict[str, tuple[Any, int]] = {}

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
