"""Tests for two-tier event reads (Redis hot + SQLite `operations` cold).

Events used to live only in Redis under `event_ttl_seconds`. Because
`add_event` prunes the sorted set to `now - ttl` on every write, any scan
window wider than the TTL silently collapsed to the TTL: the `6h` and `today`
windows both returned exactly the last 4h of events. The tracks tier already
solved this (`bulk_get_tracks_unified`); these tests pin the same behaviour for
events, reading the durable `operations` rows that `persist_events` writes.
"""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

import pytest

from app import db, services
from app.store import MemoryStore
from app.windows import resolve_window


HOT_TTL = 120  # seconds — anything older than this has aged out of Redis


@pytest.fixture
def conn():
    with tempfile.TemporaryDirectory() as tmp:
        path = str(Path(tmp) / "events.sqlite3")
        db.init_db(path)
        connection = db.connect(path)
        yield connection
        connection.close()


def operation_event(event_id: str, ts: int, event_type: str = "circle", **extra) -> dict:
    return {
        "id": event_id,
        "type": event_type,
        "icao24": "abc123",
        "callsign": "N828FC",
        "timestamp": ts,
        "airport_icao": "KLMO",
        **extra,
    }


def test_read_operations_round_trips_into_event_shape(conn):
    """A persisted operation must come back as something the scan pipeline —
    offender_rows, event_histogram, event_counts — can consume unchanged."""
    now = 1_700_000_000
    db.persist_events(conn, [
        operation_event("evt-circle", now - 6 * 3600, "circle", turn_direction="left"),
        operation_event("evt-tg", now - 5 * 3600, "touch_and_go", runway_id="29"),
    ])
    conn.commit()

    rows = db.read_operations(conn, "KLMO", now - 24 * 3600, now)
    events = [services.event_from_operation(row) for row in rows]

    assert [e["id"] for e in events] == ["evt-circle", "evt-tg"]
    assert [e["type"] for e in events] == ["circle", "touch_and_go"]
    assert events[0]["icao24"] == "abc123"
    assert events[0]["callsign"] == "N828FC"
    assert events[0]["turn_direction"] == "left"
    assert events[1]["runway_id"] == "29"
    assert events[0]["airport_icao"] == "KLMO"


def test_events_for_window_serves_events_older_than_the_hot_ttl(conn):
    """The bug: a 24h window returned only the last `event_ttl_seconds`."""
    now = int(__import__("time").time())
    old_ts = now - 12 * 3600   # long past the 120s hot TTL
    fresh_ts = now - 30        # inside the hot TTL

    db.persist_events(conn, [operation_event("evt-old", old_ts, "circle")])
    conn.commit()

    store = MemoryStore()
    asyncio.run(store.add_event("mon", operation_event("evt-fresh", fresh_ts, "circle"), HOT_TTL))

    window = resolve_window("today", "America/Denver")
    events = asyncio.run(
        services.events_for_window(store, conn, "mon", "KLMO", window)
    )

    ids = {e["id"] for e in events}
    assert "evt-old" in ids, "event older than the hot TTL must come from the cold tier"
    assert "evt-fresh" in ids, "hot-tier events must still be served"


def test_events_for_window_dedupes_hot_and_cold_by_id(conn):
    """The same event lives in both tiers; it must be counted once, and the
    hot copy (which carries fields `operations` has no column for) must win."""
    now = int(__import__("time").time())
    ts = now - 12 * 3600

    db.persist_events(conn, [operation_event("evt-dup", ts, "circle")])
    conn.commit()

    store = MemoryStore()
    hot = operation_event("evt-dup", ts, "circle", pass_radius_nm=0.5)
    asyncio.run(store.add_event("mon", hot, 24 * 3600))

    window = resolve_window("today", "America/Denver")
    events = asyncio.run(
        services.events_for_window(store, conn, "mon", "KLMO", window)
    )

    assert [e["id"] for e in events] == ["evt-dup"]
    assert events[0].get("pass_radius_nm") == 0.5, "hot copy should win the merge"


def test_narrow_windows_survive_a_redis_flush(conn):
    """The cold tier is consulted for every window, not just wide ones. Gating
    it on the TTL meant an empty Redis served 0 events for `1h` while `6h`
    served a full set — the same durable rows, arbitrarily withheld."""
    now = int(__import__("time").time())
    db.persist_events(conn, [operation_event("evt-recent", now - 120, "circle")])
    conn.commit()

    store = MemoryStore()  # flushed: no hot events at all
    window = resolve_window("1h", "America/Denver")
    events = asyncio.run(
        services.events_for_window(store, conn, "mon", "KLMO", window)
    )

    assert [e["id"] for e in events] == ["evt-recent"]


def test_pass_over_user_is_served_from_the_cold_tier_for_its_own_geometry(conn):
    """`pass_over_user` is scoped to one visitor's coordinates. Persisting
    pass_geometry_key lets the cold tier serve a visitor their OWN overflight
    history past the 4h Redis TTL — the whole point of this app — without ever
    attributing a neighbour's overflights to them."""
    now = int(__import__("time").time())
    db.persist_events(conn, [
        operation_event("mine", now - 12 * 3600, "pass_over_user", pass_geometry_key="me"),
        operation_event("theirs", now - 12 * 3600, "pass_over_user", pass_geometry_key="neighbour"),
        operation_event("evt-circle", now - 12 * 3600, "circle"),
    ])
    conn.commit()

    store = MemoryStore()
    window = resolve_window("today", "America/Denver")
    events = asyncio.run(
        services.events_for_window(store, conn, "mon", "KLMO", window, pass_geometry_key="me")
    )

    ids = {e["id"] for e in events}
    assert "evt-circle" in ids
    assert "mine" in ids, "a visitor must get their own 24h pass history"
    assert "theirs" not in ids, "never serve another geometry's overflights"


def test_pass_over_user_cold_read_is_skipped_without_a_geometry_key(conn):
    """No key (or a legacy NULL row) must never leak passes to everyone."""
    now = int(__import__("time").time())
    db.persist_events(conn, [
        operation_event("legacy", now - 12 * 3600, "pass_over_user"),  # NULL key
    ])
    conn.commit()

    store = MemoryStore()
    window = resolve_window("today", "America/Denver")
    assert asyncio.run(services.events_for_window(store, conn, "mon", "KLMO", window)) == []
    # ...and a real key must not match the legacy NULL row either.
    events = asyncio.run(
        services.events_for_window(store, conn, "mon", "KLMO", window, pass_geometry_key="me")
    )
    assert events == []


def test_read_operations_filters_by_pass_geometry_key(conn):
    now = 1_700_000_000
    db.persist_events(conn, [
        operation_event("mine", now - 60, "pass_over_user", pass_geometry_key="me"),
        operation_event("theirs", now - 60, "pass_over_user", pass_geometry_key="you"),
    ])
    conn.commit()
    rows = db.read_operations(conn, "KLMO", now - 3600, now,
                              types=["pass_over_user"], pass_geometry_key="me")
    assert [r["id"] for r in rows] == ["mine"]


def test_complaint_builders_read_events_through_the_two_tier_helper():
    """The complaint letter must count the same events the map draws. Reading
    `store.get_events` directly capped both builders at `event_ttl_seconds`, so
    a 24h letter cited 4h of circles while the panel above it showed 24h."""
    import inspect

    for fn in (services.build_description, services.build_summary_description):
        src = inspect.getsource(fn)
        assert "events_for_window" in src, f"{fn.__name__} must use events_for_window"
        assert "store.get_events" not in src, (
            f"{fn.__name__} reads the hot tier directly and will truncate wide windows"
        )


def test_events_for_window_includes_events_detected_this_scan(conn):
    """Detected-this-scan events cover `pass_over_user` beyond the hot TTL,
    which the cold tier deliberately cannot serve."""
    now = int(__import__("time").time())
    store = MemoryStore()
    window = resolve_window("today", "America/Denver")

    detected = [
        operation_event("evt-pass", now - 12 * 3600, "pass_over_user", pass_geometry_key="k1"),
        operation_event("evt-stale", window.start_ts - 600, "circle"),  # outside window
    ]
    events = asyncio.run(
        services.events_for_window(store, conn, "mon", "KLMO", window, detected=detected)
    )

    ids = {e["id"] for e in events}
    assert "evt-pass" in ids
    assert events[0]["pass_geometry_key"] == "k1"
    assert "evt-stale" not in ids, "detected events outside the window must be dropped"


# --- scan cost controls -----------------------------------------------------


def test_detection_runs_off_the_event_loop():
    """A 24h window is ~11s of pure-CPU detection. Running it on the event loop
    starved the Redis client's socket reads, timing them out and 500-ing
    unrelated requests mid-detection."""
    import inspect

    src = inspect.getsource(services.run_detectors_for_monitor)
    assert "asyncio.to_thread" in src, "detector CPU must not run on the event loop"
    assert "_detect_over_tracks" in src

    # The offloaded function must stay pure: no awaits, no store/db handles.
    pure = inspect.getsource(services._detect_over_tracks)
    assert "await " not in pure
    assert "store." not in pure and "db." not in pure


# --- redundant re-write prevention ------------------------------------------


def test_existing_operation_ids_returns_ids_in_range(conn):
    now = 1_700_000_000
    db.persist_events(conn, [
        operation_event("in-a", now - 3600, "circle"),
        operation_event("in-b", now - 60, "touch_and_go"),
        operation_event("too-old", now - 40 * 3600, "circle"),
    ])
    conn.commit()

    ids = db.existing_operation_ids(conn, "KLMO", now - 24 * 3600, now)
    assert ids == {"in-a", "in-b"}


def test_new_and_hot_events_skips_ids_already_durably_stored():
    """`existing_ids` came only from Redis, which prunes to event_ttl. So on a
    24h scan every event older than 4h looked new *forever*: ~1423 of them were
    re-added to Redis (3 round-trips each, to a remote Valkey) and re-run
    through persist_events / store_deviations / flow.process on every scan.
    """
    now = 1_700_000_000
    hot_cutoff = now - 4 * 3600
    detected = [
        operation_event("old-known", now - 12 * 3600),   # durable, not in Redis
        operation_event("old-fresh", now - 12 * 3600),   # genuinely new, but cold
        operation_event("new-hot", now - 60),            # genuinely new, and hot
    ]
    existing = {"old-known"}

    new_events, hot_events = services.new_and_hot_events(detected, existing, hot_cutoff)

    assert [e["id"] for e in new_events] == ["old-fresh", "new-hot"]
    # Writing a cold event to Redis is a guaranteed no-op — add_event prunes
    # anything older than the TTL in the same call.
    assert [e["id"] for e in hot_events] == ["new-hot"]


def test_new_and_hot_events_dedupes_within_a_single_batch():
    now = 1_700_000_000
    detected = [operation_event("dup", now - 60), operation_event("dup", now - 60)]
    new_events, hot_events = services.new_and_hot_events(detected, set(), now - 3600)
    assert len(new_events) == 1 and len(hot_events) == 1


def test_only_today_gets_the_long_cache():
    """1h and 6h carry the live aircraft markers; a 90s cache froze them.
    The threshold must be strictly greater than the windows users watch live."""
    from app.settings import Settings

    s = Settings(database_path=":memory:")
    for code in ("5m", "30m", "1h", "6h"):
        assert services.scan_cache_seconds(s, code) == s.scan_response_cache_seconds, code
    assert services.scan_cache_seconds(s, "today") == s.wide_window_scan_cache_seconds
