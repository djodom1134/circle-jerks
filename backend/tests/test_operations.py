from __future__ import annotations

import pytest

from app import db
from app.db import Airport
from app.detectors import (
    detect_circles,
    detect_events,
    detect_landings_over_period,
    detect_touch_and_gos_over_period,
)
from app.domain import ScanParams


def seeded_conn(path):
    conn = db.connect(str(path))
    conn.executescript(db.SCHEMA)
    db.seed_db(conn)
    conn.commit()
    return conn


def airport_kbjc() -> Airport:
    return Airport(
        icao="KBJC",
        iata="BJC",
        name="Rocky Mountain Metropolitan Airport",
        city="Broomfield, CO",
        country="US",
        lat=39.9088,
        lon=-105.1172,
        elevation_ft=5673,
        is_towered=True,
    )


def sample(ts: int, lat: float, lon: float, alt_agl: int = 900, icao24: str = "abc123") -> dict:
    return {
        "icao24": icao24,
        "callsign": "N123AB",
        "timestamp": ts,
        "lat": lat,
        "lon": lon,
        "geo_altitude_ft": airport_kbjc().elevation_ft + alt_agl,
        "velocity_kt": 80,
        "vertical_rate_fpm": 0,
        "on_ground": False,
    }


def closed_loop_track() -> list[dict]:
    points = [
        (39.9238, -105.1172),
        (39.9194, -105.1013),
        (39.9088, -105.0950),
        (39.8982, -105.1013),
        (39.8938, -105.1172),
        (39.8982, -105.1331),
        (39.9088, -105.1394),
        (39.9194, -105.1331),
        (39.9238, -105.1172),
    ]
    return [sample(1000 + i * 30, lat, lon) for i, (lat, lon) in enumerate(points)]


KBJC_RUNWAYS = [
    {"runway_id": "12L", "lat_threshold": 39.9215, "lon_threshold": -105.1321, "heading_deg": 120, "length_ft": 9000},
    {"runway_id": "30R", "lat_threshold": 39.8962, "lon_threshold": -105.1013, "heading_deg": 300, "length_ft": 9000},
]


def _rwy_sample(ts, agl, icao24="land01", vr=0, on_ground=False):
    # All samples sit on the 12L threshold so _nearest_runway distance ~= 0.
    return {
        "icao24": icao24,
        "callsign": "N9LAND",
        "timestamp": ts,
        "lat": 39.9215,
        "lon": -105.1321,
        "geo_altitude_ft": 5673 + agl,
        "heading_deg": 120,
        "vertical_rate_fpm": vr,
        "velocity_kt": 70,
        "on_ground": on_ground,
    }


def landing_then_silence_track(t0=18000, icao24="land01"):
    # Descend into the field, touch down, then the track ends (went to the ramp).
    rows = [(0, 1500), (30, 1000), (60, 600), (90, 30)]
    return [_rwy_sample(t0 + dt, agl, icao24, vr=-500 if agl > 0 else 0) for dt, agl in rows]


def touch_and_go_track(t0=18000, icao24="tag01"):
    # Descend, touch ≤50 AGL, climb straight back out.
    rows = [(0, 1200, -500), (30, 600, -500), (60, 200, -400), (90, 20, 0),
            (120, 200, 600), (150, 600, 700), (180, 1000, 700)]
    return [_rwy_sample(t0 + dt, agl, icao24, vr=vr) for dt, agl, vr in rows]


def departure_track(t0=18000, icao24="dep01"):
    # Starts on the runway, climbs away — never approached from altitude.
    rows = [(0, 0, 0), (30, 50, 500), (60, 300, 800), (90, 800, 900), (120, 1500, 900)]
    return [_rwy_sample(t0 + dt, agl, icao24, vr=vr, on_ground=(agl == 0)) for dt, agl, vr in rows]


def long_ground_presence_track(t0=18000, icao24="long01"):
    # Land, then keep transmitting 0 AGL for ~8 min. Must yield exactly ONE landing.
    approach = [(0, 1500), (30, 1000), (60, 600), (90, 30)]
    rows = [_rwy_sample(t0 + dt, agl, icao24, vr=-500 if agl > 0 else 0) for dt, agl in approach]
    for dt in range(120, 540, 30):
        rows.append(_rwy_sample(t0 + dt, 0, icao24, on_ground=True))
    return rows


def taxiing_no_approach_track(t0=18000, icao24="taxi01"):
    # Appears already low near the runway (never descended in from altitude),
    # never climbs out. Must be rejected ONLY by the approach-from-altitude guard.
    rows = [(0, 40), (30, 25), (60, 15), (90, 10)]
    return [_rwy_sample(t0 + dt, agl, icao24, on_ground=True) for dt, agl in rows]


def test_operations_table_exists(tmp_path):
    conn = seeded_conn(tmp_path / "t.sqlite3")
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='operations'"
    ).fetchone()
    assert row is not None
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(operations)").fetchall()}
    assert {"id", "icao", "icao24", "type", "timestamp", "deviation_mean_nm",
            "wind_from_deg", "origin_airport_icao", "flight_school"} <= cols


def test_operation_from_event_maps_circle_fields():
    event = {
        "id": "deadbeefcafe00000001",
        "type": "circle",
        "icao24": "a4c1d8",
        "callsign": "N4052F",
        "timestamp": 1718464800,
        "airport_icao": "KBJC",
        "turn_direction": "left",
    }
    op = db.operation_from_event(event)
    assert op["id"] == "deadbeefcafe00000001"
    assert op["icao"] == "KBJC"
    assert op["icao24"] == "a4c1d8"
    assert op["callsign"] == "N4052F"
    assert op["type"] == "circle"
    assert op["timestamp"] == 1718464800
    assert op["turn_direction"] == "left"
    # runway/altitude absent on this circle event → None
    assert op["runway_id"] is None
    assert op["min_altitude_ft_agl"] is None


def test_operation_from_event_maps_touch_and_go_fields():
    event = {
        "id": "deadbeefcafe00000002",
        "type": "touch_and_go",
        "icao24": "a4c1d8",
        "callsign": "N4052F",
        "timestamp": 1718464900,
        "airport_icao": "KLMO",
        "runway_id": "29",
        "runway_heading_deg": 290,
        "min_altitude_ft_agl": 35,
    }
    op = db.operation_from_event(event)
    assert op["type"] == "touch_and_go"
    assert op["runway_id"] == "29"
    assert op["runway_heading_deg"] == 290
    assert op["min_altitude_ft_agl"] == 35
    assert op["turn_direction"] is None


def test_upsert_operation_is_idempotent(tmp_path):
    conn = seeded_conn(tmp_path / "t.sqlite3")
    op = db.operation_from_event({
        "id": "dup00000000000000001",
        "type": "circle",
        "icao24": "a4c1d8",
        "callsign": "N4052F",
        "timestamp": 1718464800,
        "airport_icao": "KBJC",
        "turn_direction": "left",
    })
    db.upsert_operation(conn, op)
    db.upsert_operation(conn, op)  # second insert of same id must not duplicate
    conn.commit()
    rows = conn.execute("SELECT * FROM operations WHERE id = ?", (op["id"],)).fetchall()
    assert len(rows) == 1
    assert rows[0]["icao"] == "KBJC"
    assert rows[0]["type"] == "circle"
    assert rows[0]["turn_direction"] == "left"
    assert rows[0]["created_at"] is not None
    # enrichment columns default to NULL until later phases populate them
    assert rows[0]["deviation_mean_nm"] is None


def _mk_op(id_, ts, type_="circle", icao="KBJC"):
    return db.operation_from_event({
        "id": id_, "type": type_, "icao24": "a4c1d8", "callsign": "N1",
        "timestamp": ts, "airport_icao": icao,
    })


def test_read_operations_filters_by_window_and_type(tmp_path):
    conn = seeded_conn(tmp_path / "t.sqlite3")
    db.upsert_operation(conn, _mk_op("op1", 1000, "circle"))
    db.upsert_operation(conn, _mk_op("op2", 2000, "touch_and_go"))
    db.upsert_operation(conn, _mk_op("op3", 3000, "circle"))
    db.upsert_operation(conn, _mk_op("op4", 9999, "circle", icao="KLMO"))  # other airport
    conn.commit()

    # window only
    rows = db.read_operations(conn, "KBJC", 1500, 3500)
    assert [r["id"] for r in rows] == ["op2", "op3"]

    # window + type filter
    rows = db.read_operations(conn, "KBJC", 0, 10000, types=["circle"])
    assert [r["id"] for r in rows] == ["op1", "op3"]

    # other airport isolated
    rows = db.read_operations(conn, "KLMO", 0, 10000)
    assert [r["id"] for r in rows] == ["op4"]


def test_persist_events_writes_and_dedupes(tmp_path):
    conn = seeded_conn(tmp_path / "t.sqlite3")
    events = [
        {"id": "e1", "type": "circle", "icao24": "a", "callsign": "N1",
         "timestamp": 1000, "airport_icao": "KBJC"},
        {"id": "e2", "type": "touch_and_go", "icao24": "a", "callsign": "N1",
         "timestamp": 1100, "airport_icao": "KBJC", "runway_id": "12L"},
        {"id": "e1", "type": "circle", "icao24": "a", "callsign": "N1",
         "timestamp": 1000, "airport_icao": "KBJC"},  # duplicate id
    ]
    processed = db.persist_events(conn, events)
    conn.commit()
    assert processed == 3  # all three considered
    rows = db.read_operations(conn, "KBJC", 0, 10000)
    assert len(rows) == 2  # but only two distinct rows persisted


def test_persist_events_skips_events_without_id_or_airport(tmp_path):
    conn = seeded_conn(tmp_path / "t.sqlite3")
    events = [
        {"type": "circle", "icao24": "a", "timestamp": 1000, "airport_icao": "KBJC"},  # no id
        {"id": "x", "type": "circle", "icao24": "a", "timestamp": 1000},               # no airport
    ]
    processed = db.persist_events(conn, events)
    conn.commit()
    assert processed == 0
    assert db.read_operations(conn, "KBJC", 0, 10000) == []


def test_persist_events_skips_events_without_type(tmp_path):
    conn = seeded_conn(tmp_path / "t.sqlite3")
    events = [
        {"id": "notype1", "icao24": "a", "timestamp": 1000, "airport_icao": "KBJC"},  # no type
    ]
    processed = db.persist_events(conn, events)
    conn.commit()
    assert processed == 0
    assert db.read_operations(conn, "KBJC", 0, 10000) == []


def test_detected_circle_is_persisted(tmp_path):
    conn = seeded_conn(tmp_path / "t.sqlite3")
    ap = airport_kbjc()
    runways = db.runways_for_airport(conn, "KBJC")
    params = ScanParams(airport_icao="KBJC", user_lat=40.0, user_lon=-105.2)

    events = detect_events(closed_loop_track(), ap, runways, params)
    assert any(e["type"] == "circle" for e in events)  # sanity: detector fired

    db.persist_events(conn, events)
    conn.commit()

    rows = db.read_operations(conn, "KBJC", 0, 10_000)
    assert any(r["type"] == "circle" for r in rows)
    assert all(r["icao"] == "KBJC" for r in rows)


def test_closed_lap_circle_event_has_lap_time_bounds():
    from app.detectors import detect_circles
    ap = airport_kbjc()
    events = detect_circles(closed_loop_track(), ap, ScanParams(airport_icao="KBJC", user_lat=40.0, user_lon=-105.2))
    lap = next(e for e in events if e.get("detection_method") == "course_turn_closed_lap")
    assert "start_timestamp" in lap and "end_timestamp" in lap
    assert lap["start_timestamp"] <= lap["end_timestamp"] <= lap["timestamp"] + 1
    assert lap["end_timestamp"] - lap["start_timestamp"] >= 120  # the lap spans real time


def test_landing_detected_when_no_climb_out():
    ap = airport_kbjc()
    track = landing_then_silence_track(t0=18000)
    # end_ts well past touchdown so the 5-min settle window is satisfied.
    events = detect_landings_over_period(track, ap, KBJC_RUNWAYS, 18000, 18900)
    landings = [e for e in events if e["type"] == "landing"]
    assert len(landings) == 1
    assert landings[0]["runway_id"] == "12L"
    assert landings[0]["min_altitude_ft_agl"] <= 50


def test_touch_and_go_is_not_a_landing():
    ap = airport_kbjc()
    track = touch_and_go_track(t0=18000)
    events = detect_landings_over_period(track, ap, KBJC_RUNWAYS, 18000, 18900)
    assert [e for e in events if e["type"] == "landing"] == []


def test_departure_is_not_a_landing():
    ap = airport_kbjc()
    track = departure_track(t0=18000)
    events = detect_landings_over_period(track, ap, KBJC_RUNWAYS, 18000, 18900)
    assert [e for e in events if e["type"] == "landing"] == []


def test_no_landing_before_settle_window():
    ap = airport_kbjc()
    track = landing_then_silence_track(t0=18000)
    # Only ~100s observed past the touchdown at 18090 -> not settled yet.
    events = detect_landings_over_period(track, ap, KBJC_RUNWAYS, 18000, 18190)
    assert [e for e in events if e["type"] == "landing"] == []


def test_long_ground_presence_yields_single_landing():
    ap = airport_kbjc()
    track = long_ground_presence_track(t0=18000)
    events = detect_landings_over_period(track, ap, KBJC_RUNWAYS, 18000, 18900)
    assert len([e for e in events if e["type"] == "landing"]) == 1


def test_touch_and_go_still_detected_after_refactor():
    ap = airport_kbjc()
    track = touch_and_go_track(t0=18000)
    events = detect_touch_and_gos_over_period(track, ap, KBJC_RUNWAYS, 18000, 18900)
    assert any(e["type"] == "touch_and_go" for e in events)
    assert all(e["type"] != "landing" for e in events)


def test_taxiing_without_approach_is_not_a_landing():
    ap = airport_kbjc()
    track = taxiing_no_approach_track(t0=18000)
    events = detect_landings_over_period(track, ap, KBJC_RUNWAYS, 18000, 18900)
    assert [e for e in events if e["type"] == "landing"] == []


def test_period_circle_detection_includes_closed_lap():
    # The historical/backfill variant must also emit closed-lap events (with lap
    # bounds) so deviation gets computed for backfilled scans, not just live ones.
    from app.detectors import detect_circles_over_period
    ap = airport_kbjc()
    events = detect_circles_over_period(
        closed_loop_track(), ap,
        ScanParams(airport_icao="KBJC", user_lat=40.0, user_lon=-105.2),
        1000, 1300,
    )
    assert any(
        e.get("detection_method") == "course_turn_closed_lap" and "start_timestamp" in e
        for e in events
    )
