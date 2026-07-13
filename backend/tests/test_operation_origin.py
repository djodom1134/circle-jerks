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
    detection for circlejerks.live. Must swallow the failure and continue.

    The track below carries real ground evidence (on_ground=True) so the
    performance pre-filter (_track_has_ground_evidence) does not shortcut
    around the resolver — otherwise the monkeypatched failure would never
    actually be exercised, and this test would pass for the wrong reason."""
    conn = seeded_conn(tmp_path / "t.sqlite3")
    icao24 = "boom0001"
    db.upsert_operation(conn, _op("op-boom", icao24=icao24, type_="landing"))
    conn.commit()
    store = MemoryStore()

    async def _boom(*args, **kwargs):
        raise RuntimeError("simulated resolver failure")

    monkeypatch.setattr(services, "_resolve_origin_local_ground_track", _boom)

    track = [{
        "icao24": icao24, "timestamp": 1000,
        "lat": 40.0394, "lon": -105.2258,
        "on_ground": True, "velocity_kt": 0.0,
    }]
    new_events = [{"id": "op-boom", "type": "landing", "icao24": icao24, "timestamp": 1000}]
    await services._enrich_origins_for_new_events(store, conn, new_events, {icao24: track})  # must not raise
    conn.commit()

    row = db.read_operations(conn, "KLMO", 0, 10000)[0]
    assert row["origin_airport_icao"] is None


@pytest.mark.asyncio
async def test_enrich_origins_memoizes_resolver_failure_across_events(tmp_path, monkeypatch):
    """Task-4a review round 2, Finding 4: services.py's old single-phase loop
    skipped `resolved_by_icao24[icao24] = origin` in its except branch, so N
    events from one persistently-failing aircraft in the same pass retried
    the resolver N times (and logged N warnings) instead of once. Two events
    for the same icao24, both eligible, with a resolver that always raises:
    the resolver must be invoked exactly once, and both operations must land
    NULL (not raise, not partially resolve)."""
    conn = seeded_conn(tmp_path / "t.sqlite3")
    icao24 = "boom0002"
    db.upsert_operation(conn, _op("op-boom-a", icao24=icao24, type_="landing", ts=1000))
    db.upsert_operation(conn, _op("op-boom-b", icao24=icao24, type_="touch_and_go", ts=1010))
    conn.commit()
    store = MemoryStore()

    calls = []

    async def _boom(*args, **kwargs):
        calls.append(1)
        raise RuntimeError("simulated resolver failure")

    monkeypatch.setattr(services, "_resolve_origin_local_ground_track", _boom)

    track = [{
        "icao24": icao24, "timestamp": 1000,
        "lat": 40.0394, "lon": -105.2258,
        "on_ground": True, "velocity_kt": 0.0,
    }]
    new_events = [
        {"id": "op-boom-a", "type": "landing", "icao24": icao24, "timestamp": 1000},
        {"id": "op-boom-b", "type": "touch_and_go", "icao24": icao24, "timestamp": 1010},
    ]
    await services._enrich_origins_for_new_events(store, conn, new_events, {icao24: track})  # must not raise
    conn.commit()

    assert len(calls) == 1, "resolver must be memoized per icao24 per pass, even after a failure"
    rows = {r["id"]: r for r in db.read_operations(conn, "KLMO", 0, 10000)}
    assert rows["op-boom-a"]["origin_airport_icao"] is None
    assert rows["op-boom-b"]["origin_airport_icao"] is None


@pytest.mark.asyncio
async def test_enrich_origins_never_raises_on_non_dict_event(tmp_path):
    """Finding 3 (task-4a review round 1): the eligible-type filter and the
    op_id/icao24 extraction used to sit OUTSIDE the try/except, so a
    non-dict entry in new_events raised AttributeError straight out of this
    function (and out of run_detectors_for_monitor). worker.py's detect loop
    catches that and rolls back the WHOLE pass — discarding persist_events /
    deviation / flow writes too, not just the origin. Confirms the guard now
    actually covers the type filter + extraction, not just the resolver
    call."""
    conn = seeded_conn(tmp_path / "t.sqlite3")
    icao24 = "dict0001"
    db.upsert_operation(conn, _op("op-dict", icao24=icao24, type_="landing"))
    conn.commit()
    store = MemoryStore()
    track = [{
        "icao24": icao24, "timestamp": 1000,
        "lat": 40.0394, "lon": -105.2258,
        "on_ground": True, "velocity_kt": 0.0,
    }]
    new_events = [
        "not-a-dict",
        {"id": "op-dict", "type": "landing", "icao24": icao24, "timestamp": 1000},
    ]

    await services._enrich_origins_for_new_events(store, conn, new_events, {icao24: track})  # must not raise
    conn.commit()

    row = db.read_operations(conn, "KLMO", 0, 10000)[0]
    assert row["origin_airport_icao"] == "KBDU"


# --- provenance gate: a cached network-derived guess must never be persisted ---
#
# Finding 2 (task-4a review round 1): _resolve_origin_local_only's cache key
# (f"origin:{icao24}:{first_seen//3600}:{last_seen//3600}") is byte-identical
# to resolve_origin's. resolve_origin writes NETWORK-derived results into
# that same namespace (adsbdb, FlightAware, OpenSky flights/track-start,
# Nominatim reverse-geocoding). So even though this detector-loop path never
# makes a network call itself, it can read one of those results back out of
# the cache — and this is the first code that persists that value into a
# durable, press-facing column. These tests pre-seed the cache with the
# EXACT shape a network result takes and assert it is rejected.


@pytest.mark.asyncio
async def test_enrich_origins_ignores_cached_network_derived_origin_medium_confidence(tmp_path):
    """Task-4a review round 2, Finding 1: the round-1 gate rejected a cached
    network value by inspecting its origin_source/confidence — but
    _enrich_origins_for_new_events no longer even LOOKS at the shared
    `origin:` cache namespace on this path, so a poisoned entry there
    (e.g. OpenSky's 8nm track-start proximity match, cached by
    enrich_offenders's ORIGIN_ENRICH_LIMIT path or a background task) is
    never read at all — the persisted value is the LOCAL track's own ground
    truth (KBDU), proving the cache is bypassed by construction, not by a
    dict-shape gate."""
    conn = seeded_conn(tmp_path / "t.sqlite3")
    icao24 = "net0001"
    db.upsert_operation(conn, _op("op-net1", icao24=icao24, type_="landing"))
    conn.commit()
    store = MemoryStore()

    # Real ground evidence at KBDU.
    track = [{
        "icao24": icao24, "timestamp": 1000,
        "lat": 40.0394, "lon": -105.2258,
        "on_ground": True, "velocity_kt": 0.0,
    }]
    samples = services.valid_position_samples(track)
    first_seen = int(samples[0]["timestamp"])
    last_seen = int(samples[-1]["timestamp"])
    cache_key = f"origin:{icao24.lower()}:{first_seen // 3600}:{last_seen // 3600}"
    # Simulates resolve_origin having already run (e.g. enrich_offenders's
    # ORIGIN_ENRICH_LIMIT path, or a background task) and cached a NETWORK
    # result — for a DIFFERENT airport than the local ground truth — under
    # this exact key.
    await store.set_cache(cache_key, {
        "origin_city": "Denver",
        "origin_airport_icao": "KDEN",
        "origin_label": "Denver (KDEN)",
        "origin_source": "opensky_track_start_near_airport",
        "origin_confidence": "medium",
    }, 3600)

    new_events = [{"id": "op-net1", "type": "landing", "icao24": icao24, "timestamp": 1000}]
    await services._enrich_origins_for_new_events(store, conn, new_events, {icao24: track})
    conn.commit()

    row = db.read_operations(conn, "KLMO", 0, 10000)[0]
    assert row["origin_airport_icao"] == "KBDU"


@pytest.mark.asyncio
async def test_enrich_origins_ignores_cached_network_origin_even_at_high_confidence(tmp_path):
    """Confidence alone was never a sufficient gate: airport_label_for_icao's
    "opensky_flights_departure" source (a NETWORK result, from OpenSky flight
    history) is stamped confidence "high" whenever the departure ICAO happens
    to match a row in our airports table — indistinguishable, by field
    inspection alone, from a real local observation. Post round-2 fix this
    doesn't matter: the shared cache isn't consulted, so a poisoned entry
    (here, for a DIFFERENT airport than the local ground truth) is never
    read, at any confidence level."""
    conn = seeded_conn(tmp_path / "t.sqlite3")
    icao24 = "net0002"
    db.upsert_operation(conn, _op("op-net2", icao24=icao24, type_="landing"))
    conn.commit()
    store = MemoryStore()

    track = [{
        "icao24": icao24, "timestamp": 1000,
        "lat": 40.0394, "lon": -105.2258,
        "on_ground": True, "velocity_kt": 0.0,
    }]
    samples = services.valid_position_samples(track)
    first_seen = int(samples[0]["timestamp"])
    last_seen = int(samples[-1]["timestamp"])
    cache_key = f"origin:{icao24.lower()}:{first_seen // 3600}:{last_seen // 3600}"
    await store.set_cache(cache_key, {
        "origin_city": "Broomfield, CO",
        "origin_airport_icao": "KBJC",
        "origin_label": "Broomfield, CO (KBJC)",
        "origin_source": "opensky_flights_departure",
        "origin_confidence": "high",
    }, 3600)

    new_events = [{"id": "op-net2", "type": "landing", "icao24": icao24, "timestamp": 1000}]
    await services._enrich_origins_for_new_events(store, conn, new_events, {icao24: track})
    conn.commit()

    row = db.read_operations(conn, "KLMO", 0, 10000)[0]
    assert row["origin_airport_icao"] == "KBDU"


# --- Finding 1 (task-4a review round 2): the round-1 gate does not hold -----
#
# _persistable_origin_icao gates on origin_source == "ground_track_near_airport"
# AND origin_confidence == "high". But resolve_origin's OWN OpenSky branch
# (services.py, inside the `for at_ts in dict.fromkeys([0, first_seen])` loop)
# calls origin_from_ground_track on OPENSKY NETWORK path samples and caches
# the result under the EXACT SAME key this local-only path reads
# (f"origin:{icao24}:{first_seen//3600}:{last_seen//3600}") -- with the SAME
# origin_source and SAME confidence "high" that a genuine local ground
# observation would carry. There is no difference in the dict shape, so no
# gate on the dict's contents can ever distinguish them. The only fix is to
# never read that shared namespace on this path at all.


@pytest.mark.asyncio
async def test_enrich_origins_never_persists_cached_opensky_path_origin_over_local_truth(tmp_path):
    """Reproduces the reviewer's end-to-end finding: seed the SHARED `origin:`
    cache with a network-derived value (origin_from_ground_track applied to
    OpenSky /tracks/all path samples for a DIFFERENT airport, KBJC) under the
    exact key this aircraft/flight would use, while the LOCAL in-memory track
    has real ground evidence for KBDU. The persisted column must be KBDU --
    never KBJC (the laundered network guess), and never NULL (that would
    silently discard a real local observation just because the shared cache
    happened to be poisoned)."""
    conn = seeded_conn(tmp_path / "t.sqlite3")
    icao24 = "lnd0001"
    db.upsert_operation(conn, _op("op-l", icao24=icao24, type_="landing"))
    conn.commit()
    store = MemoryStore()

    # Local in-memory track: REAL ground evidence at KBDU.
    track = [{
        "icao24": icao24, "timestamp": 1000,
        "lat": 40.0394, "lon": -105.2258,
        "on_ground": True, "velocity_kt": 0.0,
    }]
    samples = services.valid_position_samples(track)
    first_seen = int(samples[0]["timestamp"])
    last_seen = int(samples[-1]["timestamp"])
    cache_key = f"origin:{icao24.lower()}:{first_seen // 3600}:{last_seen // 3600}"

    # Exactly what resolve_origin's OpenSky branch caches after fetching an
    # OpenSky track payload over the NETWORK: origin_from_ground_track(conn,
    # path_samples), where path_samples = opensky_track_path_samples(payload)
    # -- rows carry on_ground=bool(row[5]) and no velocity_kt at all.
    opensky_path_samples = [{
        "timestamp": 1000, "lat": 39.9088, "lon": -105.1172,  # KBJC -- a DIFFERENT airport
        "baro_altitude_m": None, "heading_deg": 0, "on_ground": True,
    }]
    network_derived = services.origin_from_ground_track(conn, opensky_path_samples)
    assert network_derived["origin_airport_icao"] == "KBJC"
    assert network_derived["origin_source"] == services.ORIGIN_GROUND_TRACK_SOURCE
    assert network_derived["origin_confidence"] == "high"
    await store.set_cache(cache_key, network_derived, services.origin_cache_ttl(network_derived))

    new_events = [{"id": "op-l", "type": "landing", "icao24": icao24, "timestamp": 1000}]
    await services._enrich_origins_for_new_events(store, conn, new_events, {icao24: track})
    conn.commit()

    row = db.read_operations(conn, "KLMO", 0, 10000)[0]
    assert row["origin_airport_icao"] == "KBDU"
    assert row["origin_airport_icao"] != "KBJC"


# --- source-inspection lints (weak — see the behavioral test below) ---------


def test_enrich_origins_uses_local_only_resolver_never_the_network_waterfall():
    """This is a lint, not a proof: it would still pass if
    _resolve_origin_local_ground_track itself grew a network call tomorrow.
    See test_run_detectors_never_hits_network_even_if_httpx_would_raise below
    for the behavioral guarantee that survives refactoring.

    Also pins task-4a review round 2, Finding 1: the persisting path must
    call _resolve_origin_local_ground_track (which resolves directly off the
    local track and never touches resolve_origin's shared `origin:` cache),
    NOT the old _resolve_origin_local_only (still used elsewhere, by
    enrich_offenders's display-only path, but whose cache key collides with
    resolve_origin's and is therefore unsafe to persist from)."""
    src = inspect.getsource(services._enrich_origins_for_new_events)
    assert "_resolve_origin_local_ground_track(" in src
    assert "_resolve_origin_local_only(" not in src
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


@pytest.mark.asyncio
async def test_run_detectors_never_hits_network_even_if_httpx_would_raise(tmp_path, monkeypatch):
    """Finding 4 (task-4a review round 1): a BEHAVIORAL guard for "the
    detector loop never calls resolve_origin's network waterfall", replacing
    the source-inspection lint above with a test that survives refactoring —
    it doesn't matter what the code looks like, only what it DOES.

    Every httpx.AsyncClient in this codebase is constructed as `httpx.AsyncClient(...)`
    at call time (adsbdb.py, flightaware.py, opensky.py, services.py's
    reverse_city_for_point/weather-adjacent helpers), so patching the shared
    httpx module's AsyncClient attribute intercepts ALL of them. If anything
    reachable from run_detectors_for_monitor constructed one, it would raise
    here, get swallowed by _enrich_origins_for_new_events's own try/except,
    and the origin below would be written as NULL instead of the correct
    resolved airport — so a wrong (None) result below proves a network call
    was attempted."""
    import httpx as httpx_module

    class _BoomAsyncClient:
        def __init__(self, *args, **kwargs):
            raise AssertionError("network call attempted from the detector loop")

    monkeypatch.setattr(httpx_module, "AsyncClient", _BoomAsyncClient)

    db_path = str(tmp_path / "detect3.sqlite3")
    db.init_db(db_path)
    store = MemoryStore()
    settings = Settings(database_path=db_path)

    now = int(time.time())
    lt = now - 400
    t0 = lt - 90
    icao24 = "netbm001"

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
