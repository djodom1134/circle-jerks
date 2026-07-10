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


def test_events_for_window_excludes_pass_over_user_from_the_cold_tier(conn):
    """`pass_over_user` is scoped to one user's location via pass_geometry_key,
    which `operations` has no column for. Serving those rows to a different
    visitor would attribute someone else's overflights to them."""
    now = int(__import__("time").time())
    db.persist_events(conn, [
        operation_event("evt-pass", now - 12 * 3600, "pass_over_user"),
        operation_event("evt-circle", now - 12 * 3600, "circle"),
    ])
    conn.commit()

    store = MemoryStore()
    window = resolve_window("today", "America/Denver")
    events = asyncio.run(
        services.events_for_window(store, conn, "mon", "KLMO", window)
    )

    ids = {e["id"] for e in events}
    assert "evt-circle" in ids
    assert "evt-pass" not in ids


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
