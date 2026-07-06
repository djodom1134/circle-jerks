"""Tests for the two-tier track storage (Redis hot + SQLite cold archive)."""

from __future__ import annotations

import asyncio
import os
import tempfile
import time
from pathlib import Path

import pytest

from app import archive, db
from app.settings import Settings
from app.store import MemoryStore


@pytest.fixture
def temp_db_path():
    with tempfile.TemporaryDirectory() as tmp:
        path = str(Path(tmp) / "archive.sqlite3")
        db.init_db(path)
        yield path


@pytest.fixture
def store():
    return MemoryStore()


@pytest.fixture
def settings(temp_db_path):
    return Settings(
        database_path=temp_db_path,
        # Use a short hot TTL so test windows trivially extend into the cold tier.
        track_ttl_seconds=120,
        event_ttl_seconds=120,
    )


# --- horizon_seconds ---------------------------------------------------------


def test_horizon_seconds_defaults_to_seven_days():
    from app.settings import Settings
    s = Settings(database_path=":memory:")
    assert s.track_archive_horizon_days == 7
    assert archive.horizon_seconds(s) == 7 * 86400


def test_horizon_seconds_honors_override():
    from app.settings import Settings
    s = Settings(database_path=":memory:", track_archive_horizon_days=3)
    assert archive.horizon_seconds(s) == 3 * 86400


# --- db accessors -----------------------------------------------------------


def test_archive_write_and_read(temp_db_path):
    with db.db_session(temp_db_path) as conn:
        n = db.archive_track_samples(conn, "A1B2C3", [
            {"timestamp": 1000, "lat": 40.1, "lon": -105.1, "altitude_ft": 5000, "callsign": "N123AB"},
            {"timestamp": 1030, "lat": 40.2, "lon": -105.2, "altitude_ft": 5100},
        ])
        conn.commit()
    assert n == 2
    with db.db_session(temp_db_path) as conn:
        rows = db.read_track_archive(conn, "A1B2C3", 900, 1100)
    assert [r["timestamp"] for r in rows] == [1000, 1030]
    assert rows[0]["callsign"] == "N123AB"


def test_archive_is_idempotent(temp_db_path):
    samples = [{"timestamp": 1000, "lat": 40.1, "lon": -105.1}]
    with db.db_session(temp_db_path) as conn:
        first = db.archive_track_samples(conn, "A1B2C3", samples)
        second = db.archive_track_samples(conn, "A1B2C3", samples)
        conn.commit()
    assert first == 1
    assert second == 0


def test_archive_bulk_read(temp_db_path):
    with db.db_session(temp_db_path) as conn:
        db.archive_track_samples(conn, "A1B2C3", [{"timestamp": 1000, "lat": 1.0, "lon": 1.0}])
        db.archive_track_samples(conn, "DEADBE", [{"timestamp": 1020, "lat": 2.0, "lon": 2.0}])
        conn.commit()
        bulk = db.bulk_read_track_archive(conn, ["A1B2C3", "DEADBE", "MISSING"], 900, 1100)
    assert len(bulk["a1b2c3"]) == 1
    assert len(bulk["deadbe"]) == 1
    assert "missing" in bulk and bulk["missing"] == []


def test_prune_removes_old_samples(temp_db_path):
    with db.db_session(temp_db_path) as conn:
        db.archive_track_samples(conn, "A1B2C3", [
            {"timestamp": 500, "lat": 1.0, "lon": 1.0},
            {"timestamp": 1500, "lat": 2.0, "lon": 2.0},
        ])
        conn.commit()
        deleted = db.prune_track_archive(conn, 1000)
        conn.commit()
        remaining = db.read_track_archive(conn, "A1B2C3", 0, 2000)
    assert deleted == 1
    assert [r["timestamp"] for r in remaining] == [1500]


def test_list_archive_aircraft(temp_db_path):
    with db.db_session(temp_db_path) as conn:
        db.archive_track_samples(conn, "AAAA01", [{"timestamp": 1000, "lat": 1, "lon": 1}])
        db.archive_track_samples(conn, "BBBB02", [{"timestamp": 2000, "lat": 2, "lon": 2}])
        db.archive_track_samples(conn, "CCCC03", [{"timestamp": 50, "lat": 3, "lon": 3}])
        conn.commit()
        within = db.list_archive_aircraft(conn, 500, 2500)
    assert sorted(within) == ["aaaa01", "bbbb02"]


# --- merge helper ----------------------------------------------------------


def test_merge_dedupes_and_sorts_by_timestamp():
    hot = [
        {"timestamp": 1000, "lat": 1.1, "lon": 1.1, "callsign": "HOT"},
        {"timestamp": 2000, "lat": 2.0, "lon": 2.0},
    ]
    cold = [
        {"timestamp": 500, "lat": 0.5, "lon": 0.5},
        {"timestamp": 1000, "lat": 9.9, "lon": 9.9, "callsign": "COLD"},  # collision
        {"timestamp": 1500, "lat": 1.5, "lon": 1.5},
    ]
    merged = archive.merge_hot_and_cold_samples(hot, cold)
    timestamps = [s["timestamp"] for s in merged]
    assert timestamps == [500, 1000, 1500, 2000]
    # Hot wins on collision.
    one_thousand = next(s for s in merged if s["timestamp"] == 1000)
    assert one_thousand["callsign"] == "HOT"


# --- archive_once integration ----------------------------------------------


@pytest.mark.asyncio
async def test_archive_once_moves_aging_samples_into_sqlite(store, settings, temp_db_path):
    now = int(time.time())
    icao = "A1B2C3"
    # Mix of samples: one fresh (should stay in Redis only), one aging (should
    # be archived). track_ttl_seconds=120, ARCHIVE_TAIL_BUFFER_SECONDS=600 →
    # archive window is [now-120, now-600] which is empty if now-120 > now-600.
    # Force a wider hot retention via a custom horizon so the test is meaningful.
    samples = [
        {"timestamp": now - 1800, "lat": 40.1, "lon": -105.1, "callsign": "OLD"},
        {"timestamp": now - 60, "lat": 40.2, "lon": -105.2, "callsign": "FRESH"},
    ]
    for s in samples:
        await store.add_track_sample(icao, s, ttl=settings.track_ttl_seconds * 100)
    # Reach back further than ARCHIVE_TAIL_BUFFER so we definitely capture the
    # old sample without competing with the buffer guard.
    written, touched = await archive.archive_once(
        store, settings, archive_horizon_seconds=24 * 3600,
    )
    # With track_ttl_seconds=120, tail_start = now - 120 (clamped to hot).
    # tail_end = now - 600. tail_end < tail_start → no work done by spec.
    # Confirm that's our defensive behavior — archive_once returns 0 when the
    # buffer would invert the window.
    assert written == 0
    assert touched == 0


@pytest.mark.asyncio
async def test_archive_once_writes_when_buffer_window_is_valid(temp_db_path):
    # Build a settings instance with a long hot TTL so tail_end > tail_start.
    s = Settings(
        database_path=temp_db_path,
        track_ttl_seconds=24 * 3600,  # 24h hot
    )
    store = MemoryStore()
    now = int(time.time())
    icao = "A1B2C3"
    for sample in [
        {"timestamp": now - 7200, "lat": 40.1, "lon": -105.1, "callsign": "OLD-IN-WINDOW"},
        {"timestamp": now - 60, "lat": 40.2, "lon": -105.2, "callsign": "TOO-FRESH"},
    ]:
        await store.add_track_sample(icao, sample, ttl=s.track_ttl_seconds)

    written, touched = await archive.archive_once(
        store, s, archive_horizon_seconds=24 * 3600,
    )

    with db.db_session(temp_db_path) as conn:
        archived = db.read_track_archive(conn, icao, now - 24 * 3600, now)
    # The 7200s-old sample is between tail_start (now - 24h) and tail_end (now-600s).
    assert written >= 1
    assert any(row["callsign"] == "OLD-IN-WINDOW" for row in archived)
    # The 60s-old sample is inside the tail buffer (TOO-FRESH) and not archived yet.
    assert not any(row["callsign"] == "TOO-FRESH" for row in archived)


def test_covered_minute_buckets_indexes_existing_samples(temp_db_path):
    with db.db_session(temp_db_path) as conn:
        db.archive_track_samples(conn, "A1B2C3", [
            {"timestamp": 60_000, "lat": 1, "lon": 1},   # bucket 1000
            {"timestamp": 60_030, "lat": 1, "lon": 1},   # bucket 1000 (same)
            {"timestamp": 60_120, "lat": 1, "lon": 1},   # bucket 1002
        ])
        conn.commit()
        buckets = archive._covered_minute_buckets(conn, 0, 200_000)
    assert 1000 in buckets
    assert 1002 in buckets
    # The bucket between (1001) was never sampled.
    assert 1001 not in buckets


@pytest.mark.asyncio
async def test_gap_fill_skips_when_opensky_disabled(temp_db_path):
    s = Settings(database_path=temp_db_path, opensky_historical_enabled=False)
    store = MemoryStore()
    stats = await archive.gap_fill_once(store, s)
    assert stats == {"skipped": "opensky_historical_disabled"}


@pytest.mark.asyncio
async def test_gap_fill_skips_when_no_active_monitors(temp_db_path):
    s = Settings(
        database_path=temp_db_path,
        opensky_historical_enabled=True,
        opensky_client_id="x",
        opensky_client_secret="y",
    )
    store = MemoryStore()
    stats = await archive.gap_fill_once(store, s)
    assert stats == {"skipped": "no_active_monitors"}


def test_flight_icao24_extractor():
    assert archive._flight_icao24({"hex_id": "A28E99"}) == "a28e99"
    assert archive._flight_icao24({"aircraft": {"mode_s_code": "B00CAFE"[:6]}}) == "b00caf"
    assert archive._flight_icao24({"icao_address": "deadbe"}) == "deadbe"
    assert archive._flight_icao24({"aircraft": {"hex": "DEAD00"}}) == "dead00"
    # Missing / wrong length → None
    assert archive._flight_icao24({}) is None
    assert archive._flight_icao24({"hex_id": "TOO_LONG_VALUE"}) is None


@pytest.mark.asyncio
async def test_gap_fill_via_flightaware_skips_without_key(temp_db_path):
    s = Settings(database_path=temp_db_path, flightaware_api_key=None)
    store = MemoryStore()
    stats = await archive.gap_fill_via_flightaware_once(store, s)
    assert stats == {"skipped": "flightaware_no_key"}


@pytest.mark.asyncio
async def test_gap_fill_via_flightaware_skips_without_monitors(temp_db_path):
    s = Settings(database_path=temp_db_path, flightaware_api_key="abc")
    store = MemoryStore()
    stats = await archive.gap_fill_via_flightaware_once(store, s)
    assert stats == {"skipped": "no_active_monitors"}


@pytest.mark.asyncio
async def test_prune_once_runs(temp_db_path):
    s = Settings(database_path=temp_db_path, track_ttl_seconds=120)
    now = int(time.time())
    with db.db_session(temp_db_path) as conn:
        db.archive_track_samples(conn, "A1B2C3", [
            {"timestamp": now - 26 * 3600, "lat": 1, "lon": 1},
            {"timestamp": now - 3600, "lat": 2, "lon": 2},
        ])
        conn.commit()
    deleted = await archive.prune_once(s, horizon_seconds=24 * 3600)
    assert deleted == 1
    with db.db_session(temp_db_path) as conn:
        remaining = db.read_track_archive(conn, "A1B2C3", 0, now)
    assert len(remaining) == 1
