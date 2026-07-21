from __future__ import annotations

import pytest

from app.store import MemoryStore


@pytest.mark.asyncio
async def test_incr_counter_increments_and_isolates_keys():
    store = MemoryStore()
    assert await store.incr_counter("a", 60) == 1
    assert await store.incr_counter("a", 60) == 2
    assert await store.incr_counter("b", 60) == 1


@pytest.mark.asyncio
async def test_incr_counter_resets_after_the_window_expires():
    store = MemoryStore()
    assert await store.incr_counter("a", 0) == 1
    # ttl=0 means the window is already expired on the next read.
    assert await store.incr_counter("a", 0) == 1
