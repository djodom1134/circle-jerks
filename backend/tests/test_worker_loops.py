"""Ingestion and detection must run as decoupled ticks, not one serial pass.

Root cause (see worker.py `_ingest_tick`/`_detect_tick`/`_ingest_loop`/
`_detect_loop`): `run_forever` used to ingest live positions for a monitor
group and then run the heavy 4h `run_detectors_for_monitor` pass for every
monitor in that group, all within the SAME loop iteration. A slow/heavy
detector pass therefore starved live position ingestion — positions only
refreshed every ~29s instead of the configured ~10s poll interval, and the
worker pegged at ~95% CPU.

Fix: ingestion and detection are separate single-pass helpers
(`_ingest_tick`, `_detect_tick`), each driven by its own `while True` loop
(`_ingest_loop`, `_detect_loop`) on independent cadences, run as concurrent
asyncio tasks from `run_forever`. These tests pin `_ingest_tick` /
`_detect_tick` behavior in isolation — the detector logic itself is covered
by tests/test_operations.py and friends.
"""
from __future__ import annotations

import time

import pytest

from app import db
from app.live_sources import LiveFetchResult
from app.settings import Settings
from app.store import MemoryStore
from app.worker import _detect_tick, _ingest_tick


def _monitor(hash_: str = "mon1", airport_icao: str = "KBJC") -> dict:
    return {
        "hash": hash_,
        "airport_icao": airport_icao,
        "user_lat": 39.9088,
        "user_lon": -105.1172,
        "user_elevation_ft": None,
        "ring_nm": 5.0,
        "pass_radius_nm": 1.0,
        "pass_ceiling_ft": 5000,
        "bbox": (39.8, -105.3, 40.0, -105.0),
    }


class _ExplodingLiveSources:
    """Any call means _ingest_tick fetched live state despite having no
    active monitors to poll for — that must never happen."""

    async def states_bbox(self, bbox):
        raise AssertionError("must not fetch live states with no active monitors")

    def health_snapshot(self):
        raise AssertionError("must not write the health cache with no active monitors")


class _FakeLiveSources:
    def __init__(self, states: list[dict]):
        self._states = states
        self.calls = 0

    async def states_bbox(self, bbox):
        self.calls += 1
        return LiveFetchResult(source="fake", states=self._states)

    def health_snapshot(self) -> dict:
        return {}


# --- no-op behavior with no active monitors ---------------------------------


@pytest.mark.asyncio
async def test_ingest_tick_is_a_noop_without_monitors():
    store = MemoryStore()
    settings = Settings(database_path=":memory:")

    await _ingest_tick(store, settings, _ExplodingLiveSources())

    assert await store.list_aircraft() == []
    assert await store.get_cache("live_sources:health") is None


@pytest.mark.asyncio
async def test_detect_tick_is_a_noop_without_monitors():
    store = MemoryStore()
    settings = Settings(database_path=":memory:")

    # Must return cleanly without ever opening a db_session against a
    # nonexistent/uninitialized database.
    await _detect_tick(store, settings)


# --- _ingest_tick writes track samples, no detection ------------------------


@pytest.mark.asyncio
async def test_ingest_tick_writes_track_samples_and_health_cache():
    store = MemoryStore()
    settings = Settings(database_path=":memory:", track_ttl_seconds=3600, max_aircraft_per_scan=500)
    await store.register_monitor("mon1", _monitor(), ttl=60)

    now = int(time.time())
    states = [
        {"icao24": "abc123", "callsign": "N1", "lat": 39.9, "lon": -105.1, "altitude_ft": 5000, "timestamp": now},
        {"icao24": "def456", "callsign": "N2", "lat": 39.91, "lon": -105.12, "altitude_ft": 5200, "timestamp": now},
    ]
    fake_live = _FakeLiveSources(states)

    await _ingest_tick(store, settings, fake_live)

    assert fake_live.calls == 1, "must fetch live states once for the single monitor group"
    assert await store.list_aircraft() == ["abc123", "def456"]
    track = await store.get_track("abc123")
    assert len(track) == 1
    assert track[0]["timestamp"] == now

    # The live_sources:health cache write must still happen exactly as it did
    # in the old serial loop.
    assert await store.get_cache("live_sources:health") == {}


@pytest.mark.asyncio
async def test_ingest_tick_does_not_touch_sqlite(tmp_path, monkeypatch):
    """Ingestion is Redis-only: it must never open a db_session. Point
    database_path at a path with no parent directory so opening a connection
    would raise, proving _ingest_tick never tries."""
    store = MemoryStore()
    bogus_path = str(tmp_path / "does" / "not" / "exist" / "db.sqlite3")
    settings = Settings(database_path=bogus_path, track_ttl_seconds=3600)
    await store.register_monitor("mon1", _monitor(), ttl=60)

    now = int(time.time())
    fake_live = _FakeLiveSources([
        {"icao24": "abc123", "callsign": "N1", "lat": 39.9, "lon": -105.1, "altitude_ft": 5000, "timestamp": now},
    ])

    # Would raise sqlite3.OperationalError (unable to open database file) if
    # _ingest_tick opened a db_session against this path.
    await _ingest_tick(store, settings, fake_live)

    assert await store.list_aircraft() == ["abc123"]


# --- _detect_tick runs a detection pass -------------------------------------


@pytest.mark.asyncio
async def test_detect_tick_runs_a_detection_pass_without_error(tmp_path):
    db_path = str(tmp_path / "detect.sqlite3")
    db.init_db(db_path)  # seeds KBJC airport + runways
    store = MemoryStore()
    settings = Settings(database_path=db_path)
    await store.register_monitor("mon1", _monitor(), ttl=60)

    now = int(time.time())
    for i in range(5):
        await store.add_track_sample(
            "abc123",
            {
                "timestamp": now - (5 - i) * 10,
                "lat": 39.9088 + i * 0.0005,
                "lon": -105.1172,
                "altitude_ft": 5673,
                "callsign": "N1",
            },
            ttl=3600,
        )

    # Pins the wiring (monitor -> run_detectors_for_monitor -> commit), not
    # the detector logic itself. Must complete without raising.
    await _detect_tick(store, settings)


@pytest.mark.asyncio
async def test_detect_tick_survives_a_bad_monitor_airport(tmp_path):
    """One monitor whose airport doesn't exist must not stop the pass for
    the others in the same tick."""
    db_path = str(tmp_path / "detect_bad.sqlite3")
    db.init_db(db_path)
    store = MemoryStore()
    settings = Settings(database_path=db_path)
    await store.register_monitor("mon-bad", _monitor(hash_="mon-bad", airport_icao="ZZZZ"), ttl=60)
    await store.register_monitor("mon-good", _monitor(hash_="mon-good", airport_icao="KBJC"), ttl=60)

    # Must complete without raising even though "ZZZZ" has no airport row.
    await _detect_tick(store, settings)


@pytest.mark.asyncio
async def test_detect_tick_isolates_a_raising_monitor(tmp_path, monkeypatch):
    """A monitor whose detector run raises outright (not just "no airport")
    must not stop the tick, and the failed monitor's partial writes must not
    be committed alongside the next monitor's."""
    db_path = str(tmp_path / "detect_raise.sqlite3")
    db.init_db(db_path)
    store = MemoryStore()
    settings = Settings(database_path=db_path)
    await store.register_monitor("mon-explode", _monitor(hash_="mon-explode", airport_icao="KBJC"), ttl=60)
    await store.register_monitor("mon-good", _monitor(hash_="mon-good", airport_icao="KLMO"), ttl=60)

    import app.worker as worker_module

    real = worker_module.run_detectors_for_monitor
    calls: list[str] = []

    async def _flaky(store_, settings_, conn, monitor, *a, **kw):
        calls.append(monitor["hash"])
        if monitor["hash"] == "mon-explode":
            # Dirty the open transaction, then blow up before its own commit.
            conn.execute(
                "INSERT INTO track_archive (icao24, timestamp) VALUES ('doomed', 1)"
            )
            raise RuntimeError("boom")
        return await real(store_, settings_, conn, monitor, *a, **kw)

    monkeypatch.setattr(worker_module, "run_detectors_for_monitor", _flaky)

    await _detect_tick(store, settings)

    assert set(calls) == {"mon-explode", "mon-good"}, "one monitor raising must not skip the other"

    with db.db_session(db_path) as conn:
        row = conn.execute("SELECT 1 FROM track_archive WHERE icao24 = 'doomed'").fetchone()
    assert row is None, "the raising monitor's dirty write must have been rolled back, not committed"
