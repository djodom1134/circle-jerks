from __future__ import annotations

import pytest

import app.store as store_module
from app.store import MemoryStore


@pytest.mark.asyncio
async def test_incr_counter_increments_and_isolates_keys():
    store = MemoryStore()
    assert await store.incr_counter("a", 60) == 1
    assert await store.incr_counter("a", 60) == 2
    assert await store.incr_counter("b", 60) == 1


@pytest.mark.asyncio
async def test_incr_counter_resets_after_the_window_expires(monkeypatch):
    store = MemoryStore()
    now = 1_000_000.0
    monkeypatch.setattr(store_module.time, "time", lambda: now)

    # Realistic 60s window: still counting mid-window, resets once it has
    # genuinely elapsed. This fails if the expiry check is broken (e.g.
    # never resets, or resets too early/late).
    assert await store.incr_counter("a", 60) == 1
    now += 59
    assert await store.incr_counter("a", 60) == 2
    now += 2  # 61s have elapsed since the window opened
    assert await store.incr_counter("a", 60) == 1

    # A non-positive ttl is clamped to a minimum one-second window rather
    # than a zero-length one. This fails if the max(ttl, 1) clamp is
    # removed: without it, the window would already be "expired" the
    # instant it opens, and the second call below would return 1 instead
    # of 2.
    now = 2_000_000.0
    assert await store.incr_counter("b", 0) == 1
    assert await store.incr_counter("b", 0) == 2
    now += 1.5  # past the clamped 1s window
    assert await store.incr_counter("b", 0) == 1
