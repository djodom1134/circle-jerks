"""Task 4a: the origin writer — populate operations.origin_airport_icao.

homebase.py's locality classifier reads this column to decide whether an
aircraft is home-based at an airport or a visitor (homebase.py:90-99). Before
this task nothing wrote it: the INSERT at db.py:568 omits it, and the only two
UPDATE operations statements were deviation (db.py:673) and wind (db.py:702).
The column was always NULL and the classifier's whole "where do arrivals come
from" signal was dead.

These tests pin:
  * db.update_operation_origin — mirrors update_operation_deviation/wind.
  * services._enrich_origins_for_new_events — the local-only (cache +
    ground-track) enrichment step, called from run_detectors_for_monitor on
    new_events only, that never hits the network and never raises.
  * The end-to-end wiring: a newly-detected arrival with ground evidence gets
    an origin; one without evidence stays NULL; takeoffs never get an origin
    written at all.
"""
from __future__ import annotations

import inspect
import time

import pytest

from app import db, services
from app.settings import Settings
from app.store import MemoryStore


def seeded_conn(path):
    conn = db.connect(str(path))
    conn.executescript(db.SCHEMA)
    db.seed_db(conn)
    conn.commit()
    return conn


def _op(id_, icao24="abc123", type_="landing", icao="KLMO", ts=1000):
    return db.operation_from_event({
        "id": id_, "type": type_, "icao24": icao24, "callsign": "N1",
        "timestamp": ts, "airport_icao": icao,
    })


# --- db.update_operation_origin ---------------------------------------------


def test_update_operation_origin_sets_column(tmp_path):
    conn = seeded_conn(tmp_path / "t.sqlite3")
    db.upsert_operation(conn, _op("op1"))
    conn.commit()

    db.update_operation_origin(conn, "op1", "KBDU")
    conn.commit()

    row = db.read_operations(conn, "KLMO", 0, 10000)[0]
    assert row["origin_airport_icao"] == "KBDU"


def test_update_operation_origin_none_leaves_column_null_not_placeholder(tmp_path):
    conn = seeded_conn(tmp_path / "t.sqlite3")
    db.upsert_operation(conn, _op("op1"))
    conn.commit()

    db.update_operation_origin(conn, "op1", None)
    conn.commit()

    row = db.read_operations(conn, "KLMO", 0, 10000)[0]
    assert row["origin_airport_icao"] is None
    # Never a placeholder: never the empty string, never the literal "None".
    assert row["origin_airport_icao"] != ""
    assert row["origin_airport_icao"] != "None"


def test_update_operation_origin_is_idempotent_and_overwrites(tmp_path):
    conn = seeded_conn(tmp_path / "t.sqlite3")
    db.upsert_operation(conn, _op("op1"))
    conn.commit()

    db.update_operation_origin(conn, "op1", "KBDU")
    db.update_operation_origin(conn, "op1", "KBDU")  # repeat same value: no-op
    conn.commit()
    row = db.read_operations(conn, "KLMO", 0, 10000)[0]
    assert row["origin_airport_icao"] == "KBDU"

    db.update_operation_origin(conn, "op1", "KBJC")  # new value: overwrites
    conn.commit()
    row = db.read_operations(conn, "KLMO", 0, 10000)[0]
    assert row["origin_airport_icao"] == "KBJC"


def test_update_operation_origin_updates_by_id_only(tmp_path):
    conn = seeded_conn(tmp_path / "t.sqlite3")
    db.upsert_operation(conn, _op("op1", icao24="abc123"))
    db.upsert_operation(conn, _op("op2", icao24="def456"))
    conn.commit()

    db.update_operation_origin(conn, "op1", "KBDU")
    conn.commit()

    rows = {r["id"]: r for r in db.read_operations(conn, "KLMO", 0, 10000)}
    assert rows["op1"]["origin_airport_icao"] == "KBDU"
    assert rows["op2"]["origin_airport_icao"] is None


# --- services._enrich_origins_for_new_events (unit-level) -------------------


@pytest.mark.asyncio
async def test_enrich_origins_writes_resolved_airport_for_arrival(tmp_path):
    conn = seeded_conn(tmp_path / "t.sqlite3")
    icao24 = "res0001"
    db.upsert_operation(conn, _op("op-landing", icao24=icao24, type_="landing"))
    conn.commit()
    store = MemoryStore()
    # On the ground at KBDU (Boulder Municipal) — real ground-track evidence.
    track = [{
        "icao24": icao24, "timestamp": 1000,
        "lat": 40.0394, "lon": -105.2258,
        "on_ground": True, "velocity_kt": 0.0,
    }]
    new_events = [{"id": "op-landing", "type": "landing", "icao24": icao24, "timestamp": 1000}]

    await services._enrich_origins_for_new_events(store, conn, new_events, {icao24: track})
    conn.commit()

    row = db.read_operations(conn, "KLMO", 0, 10000)[0]
    assert row["origin_airport_icao"] == "KBDU"


@pytest.mark.asyncio
async def test_enrich_origins_writes_null_when_unresolved(tmp_path):
    conn = seeded_conn(tmp_path / "t.sqlite3")
    icao24 = "unk0001"
    db.upsert_operation(conn, _op("op-landing", icao24=icao24, type_="landing"))
    conn.commit()
    store = MemoryStore()
    new_events = [{"id": "op-landing", "type": "landing", "icao24": icao24, "timestamp": 1000}]

    # No track at all -> nothing to resolve from -> must stay NULL, not a
    # placeholder string.
    await services._enrich_origins_for_new_events(store, conn, new_events, {icao24: []})
    conn.commit()

    row = db.read_operations(conn, "KLMO", 0, 10000)[0]
    assert row["origin_airport_icao"] is None
    assert row["origin_airport_icao"] != "unknown"


@pytest.mark.asyncio
async def test_enrich_origins_skips_takeoff_events(tmp_path):
    """A takeoff's origin is trivially this airport — writing it would poison
    the classifier's "N of M arrivals originated at X" evidence. Even with
    unambiguous ground evidence available, a takeoff row must not get an
    origin written."""
    conn = seeded_conn(tmp_path / "t.sqlite3")
    icao24 = "tko0001"
    db.upsert_operation(conn, _op("op-takeoff", icao24=icao24, type_="takeoff"))
    conn.commit()
    store = MemoryStore()
    track = [{
        "icao24": icao24, "timestamp": 1000,
        "lat": 40.0394, "lon": -105.2258,
        "on_ground": True, "velocity_kt": 0.0,
    }]
    new_events = [{"id": "op-takeoff", "type": "takeoff", "icao24": icao24, "timestamp": 1000}]

    await services._enrich_origins_for_new_events(store, conn, new_events, {icao24: track})
    conn.commit()

    row = db.read_operations(conn, "KLMO", 0, 10000)[0]
    assert row["origin_airport_icao"] is None


@pytest.mark.asyncio
async def test_enrich_origins_never_raises_on_resolver_failure(tmp_path, monkeypatch):
    """A detector loop that dies on an origin lookup would take down live
    detection for circlejerks.live. Must swallow the failure and continue."""
    conn = seeded_conn(tmp_path / "t.sqlite3")
    icao24 = "boom0001"
    db.upsert_operation(conn, _op("op-boom", icao24=icao24, type_="landing"))
    conn.commit()
    store = MemoryStore()

    async def _boom(*args, **kwargs):
        raise RuntimeError("simulated resolver failure")

    monkeypatch.setattr(services, "_resolve_origin_local_only", _boom)

    new_events = [{"id": "op-boom", "type": "landing", "icao24": icao24, "timestamp": 1000}]
    await services._enrich_origins_for_new_events(store, conn, new_events, {icao24: []})  # must not raise
    conn.commit()

    row = db.read_operations(conn, "KLMO", 0, 10000)[0]
    assert row["origin_airport_icao"] is None


def test_enrich_origins_uses_local_only_resolver_never_the_network_waterfall():
    src = inspect.getsource(services._enrich_origins_for_new_events)
    assert "_resolve_origin_local_only(" in src
    assert "resolve_origin(" not in src


def test_run_detectors_for_monitor_calls_origin_enrichment():
    src = inspect.getsource(services.run_detectors_for_monitor)
    assert "_enrich_origins_for_new_events" in src
    # The network waterfall must never appear in the shared detector loop.
    assert "resolve_origin(" not in src


# --- end-to-end through run_detectors_for_monitor ---------------------------


def _monitor():
    return {
        "hash": "mon-klmo",
        "airport_icao": "KLMO",
        "user_lat": 40.1646,
        "user_lon": -105.1630,
        "user_elevation_ft": None,
        "ring_nm": 5.0,
        "pass_radius_nm": 1.0,
        "pass_ceiling_ft": 5000,
        "bbox": (40.0, -105.3, 40.3, -105.0),
    }


def _approach_and_landing_samples(icao24: str, t0: int) -> list[dict]:
    # Mirrors test_operations.py's landing_then_silence_track, retargeted at
    # KLMO runway 11's threshold (db.RUNWAY_SEED: 40.1702, -105.1775, hdg 110).
    # Descend into the field, touch down, then the track ends (settles as a
    # landing, not a touch-and-go, once LANDING_SETTLE_SECONDS has passed).
    rows = [(0, 1500, -500), (30, 1000, -500), (60, 600, -500), (90, 30, 0)]
    return [{
        "icao24": icao24, "callsign": "N9LAND", "timestamp": t0 + dt,
        "lat": 40.1702, "lon": -105.1775, "geo_altitude_ft": 5055 + agl,
        "heading_deg": 110, "vertical_rate_fpm": vr, "velocity_kt": 70,
        "on_ground": False,
    } for dt, agl, vr in rows]


@pytest.mark.asyncio
async def test_run_detectors_writes_origin_for_new_landing_with_ground_evidence(tmp_path):
    db_path = str(tmp_path / "detect.sqlite3")
    db.init_db(db_path)  # seeds KLMO, KBDU + runways
    store = MemoryStore()
    settings = Settings(database_path=db_path)

    now = int(time.time())
    lt = now - 400  # > LANDING_SETTLE_SECONDS (300s) before "now" so it settles as a landing
    t0 = lt - 90
    icao24 = "orig0001"

    # Earliest samples: on the ground at KBDU, well before the KLMO approach.
    ground_samples = [{
        "icao24": icao24, "callsign": "N9LAND", "timestamp": ts,
        "lat": 40.0394, "lon": -105.2258, "geo_altitude_ft": 5288,
        "velocity_kt": 0.0, "vertical_rate_fpm": 0, "on_ground": True,
    } for ts in (t0 - 600, t0 - 570)]

    for s in ground_samples + _approach_and_landing_samples(icao24, t0):
        await store.add_track_sample(icao24, s, ttl=3600)

    conn = db.connect(db_path)
    run = await services.run_detectors_for_monitor(store, settings, conn, _monitor())
    assert run.written >= 1

    rows = [r for r in db.read_operations(conn, "KLMO", 0, now + 10) if r["type"] == "landing"]
    assert rows, "expected a persisted landing op"
    assert rows[0]["origin_airport_icao"] == "KBDU"


@pytest.mark.asyncio
async def test_run_detectors_leaves_origin_null_without_ground_evidence(tmp_path):
    db_path = str(tmp_path / "detect2.sqlite3")
    db.init_db(db_path)
    store = MemoryStore()
    settings = Settings(database_path=db_path)

    now = int(time.time())
    lt = now - 400
    t0 = lt - 90
    icao24 = "orig0002"

    # No prior ground samples anywhere — the aircraft is only ever observed
    # airborne on approach into KLMO.
    for s in _approach_and_landing_samples(icao24, t0):
        await store.add_track_sample(icao24, s, ttl=3600)

    conn = db.connect(db_path)
    run = await services.run_detectors_for_monitor(store, settings, conn, _monitor())
    assert run.written >= 1

    rows = [r for r in db.read_operations(conn, "KLMO", 0, now + 10) if r["type"] == "landing"]
    assert rows, "expected a persisted landing op"
    assert rows[0]["origin_airport_icao"] is None
