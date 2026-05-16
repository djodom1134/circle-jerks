from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
import httpx

from app import db
from app.db import Airport
from app.detectors import closest_over_user_rows, detect_circles, detect_passes, event_counts, pass_geometry_key
from app.domain import ScanParams, local_time_label, location_hash, monitor_hash
from app.geo import Point, distance_nm, heading_delta_deg
from app.llm import ComplaintContext, MessagePreferences, deterministic_description
from app.flightaware import FlightAwareClient
from app.live_sources import (
    LiveSourceStale,
    LiveStateClient,
    SourceHealth,
    bbox_center_radius_nm,
    build_live_source_client,
    parse_readsb_aircraft,
    readsb_payload_aircraft,
    readsb_payload_now,
)
from app.scoring import offender_rows, score_events
from app.services import (
    active_aircraft_count,
    airport_label_for_icao,
    altitude_over_user_summary,
    backfill_historical_states,
    build_summary_description,
    events_for_current_scan,
    historical_snapshot_times,
    merge_track_rows,
    monitor_for_params,
    opensky_track_path_samples,
    origin_from_ground_track,
    origin_from_track,
    recent_tracks_for_response,
    tracks_for_response,
)
from app.settings import Settings
from app.store import MemoryStore
from app.tone import PRESETS, ToneSliders, band_for, sliders_from_request
from app.worker import effective_poll_interval_seconds, monitor_poll_groups
from app.windows import resolve_window


def airport() -> Airport:
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
        "geo_altitude_ft": airport().elevation_ft + alt_agl,
        "velocity_kt": 80,
        "vertical_rate_fpm": 0,
        "on_ground": False,
    }


def seeded_conn(path):
    conn = db.connect(str(path))
    conn.executescript(db.SCHEMA)
    db.seed_db(conn)
    conn.commit()
    return conn


def test_distance_and_heading_delta():
    assert 50 < distance_nm(Point(39.9088, -105.1172), Point(40.758, -104.997)) < 60
    assert heading_delta_deg(350, 10) == 20
    assert heading_delta_deg(10, 350) == -20


def test_today_window_uses_local_midnight():
    now = datetime(2026, 4, 24, 18, 30, tzinfo=ZoneInfo("UTC"))
    window = resolve_window("today", "America/Denver", now)
    assert window.code == "today"
    assert window.seconds == 12 * 3600 + 30 * 60


def test_local_time_labels_use_am_pm():
    ts = int(datetime(2026, 4, 24, 18, 30, tzinfo=ZoneInfo("UTC")).timestamp())
    label = local_time_label(ts, "America/Denver")
    assert "12:30 PM" in label
    assert "18:30" not in label


def test_tone_presets_and_bands():
    sliders = sliders_from_request("formal_complaint", 7, 7, 7, 7, 7)
    assert sliders == ToneSliders(**PRESETS["formal_complaint"])
    assert "serious anger" in band_for("anger", 10)
    assert len(sliders.stable_hash()) == 16


def test_monitor_hash_truncates_user_location():
    a = ScanParams(airport_icao="KBJC", user_lat=40.1234, user_lon=-105.1234)
    b = ScanParams(airport_icao="KBJC", user_lat=40.1244, user_lon=-105.1244)
    assert location_hash(a.user_lat, a.user_lon) == location_hash(b.user_lat, b.user_lon)
    assert monitor_hash(a) == monitor_hash(b)


def test_circle_detector_detects_closed_loop():
    ap = airport()
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
    track = [sample(1000 + index * 30, lat, lon) for index, (lat, lon) in enumerate(points)]
    events = detect_circles(track, ap, ScanParams(airport_icao="KBJC", user_lat=40.0, user_lon=-105.2))
    assert len(events) == 1
    assert events[0]["type"] == "circle"


def test_circle_detector_counts_offset_traffic_pattern_lap():
    ap = airport()
    points = [
        (ap.lat + 0.010, ap.lon - 0.020),
        (ap.lat + 0.010, ap.lon - 0.070),
        (ap.lat - 0.025, ap.lon - 0.070),
        (ap.lat - 0.035, ap.lon - 0.045),
        (ap.lat - 0.025, ap.lon - 0.020),
        (ap.lat + 0.010, ap.lon - 0.020),
    ]
    track = [sample(1000 + index * 60, lat, lon) for index, (lat, lon) in enumerate(points)]
    params = ScanParams(airport_icao="KBJC", user_lat=40.0, user_lon=-105.2, ring_nm=8)

    events = detect_circles(track, ap, params)

    assert len(events) == 1
    assert events[0]["detection_method"] == "course_turn_closed_lap"
    assert events[0]["path_nm"] > 8
    assert events[0]["closure_nm"] == 0


def test_circle_detector_ignores_straight_departure():
    ap = airport()
    track = [
        sample(1000 + index * 60, ap.lat + 0.004 * index, ap.lon - 0.004 * index)
        for index in range(8)
    ]

    events = detect_circles(track, ap, ScanParams(airport_icao="KBJC", user_lat=40.0, user_lon=-105.2))

    assert events == []


def test_pass_detector_groups_overflights():
    ap = airport()
    params = ScanParams(airport_icao="KBJC", user_lat=40.0, user_lon=-105.0, pass_radius_nm=0.5)
    track = [
        sample(1000, 39.99, -105.02, 1200),
        sample(1030, 40.0005, -105.0005, 800),
        sample(1060, 40.0010, -105.0010, 850),
        sample(1160, 40.02, -105.03, 900),
    ]
    events = detect_passes(track, ap, params)
    assert len(events) == 1
    assert events[0]["type"] == "pass_over_user"
    assert events[0]["min_altitude_ft_agl"] == 800


def test_pass_detector_interpolates_between_samples():
    ap = airport()
    params = ScanParams(airport_icao="KBJC", user_lat=40.0, user_lon=-105.0, pass_radius_nm=0.2)
    track = [
        sample(1000, 39.998, -105.004, 1200),
        sample(1005, 40.002, -104.996, 800),
    ]
    rows = closest_over_user_rows(track, ap, params, 1000, 1005, ceiling_ft=5000)
    events = detect_passes(track, ap, params)
    assert len(rows) == 1
    assert rows[0]["distance_nm"] < 0.02
    assert int(rows[0]["agl"]) == 1000
    assert len(events) == 1
    assert events[0]["min_altitude_ft_agl"] == 1000
    assert events[0]["pass_geometry_key"] == pass_geometry_key(params)


def test_pass_events_are_filtered_to_exact_home_circle():
    params = ScanParams(airport_icao="KBJC", user_lat=40.00001, user_lon=-105.00001, pass_radius_nm=0.5)
    stale = {
        "type": "pass_over_user",
        "icao24": "abc123",
        "timestamp": 1000,
        "pass_geometry_key": pass_geometry_key(
            ScanParams(airport_icao="KBJC", user_lat=40.006, user_lon=-105.006, pass_radius_nm=0.5)
        ),
    }
    current = {
        "type": "pass_over_user",
        "icao24": "abc123",
        "timestamp": 1030,
        "pass_geometry_key": pass_geometry_key(params),
    }
    circle = {"type": "circle", "icao24": "abc123", "timestamp": 1060}

    assert events_for_current_scan([stale, current, circle], params) == [current, circle]


def test_over_user_altitude_ignores_airport_lows():
    ap = airport()
    params = ScanParams(airport_icao="KBJC", user_lat=40.0, user_lon=-105.0, user_elevation_ft=ap.elevation_ft + 100, pass_radius_nm=0.5)
    window = resolve_window("1h", "America/Denver", datetime(2026, 4, 24, 12, 0, tzinfo=ZoneInfo("UTC")))
    track = [
        sample(window.start_ts + 60, ap.lat, ap.lon, 0),
        sample(window.start_ts + 120, 40.0005, -105.0005, 900),
        sample(window.start_ts + 150, 40.0010, -105.0010, 1100),
    ]
    summary = altitude_over_user_summary(track, ap, params, window)
    assert summary["avg_altitude_over_user_ft_agl"] == 900
    assert summary["min_altitude_over_user_ft_agl"] == 799
    assert summary["samples_over_user"] == 2


def test_origin_from_track_prefers_ground_sample_near_airport(tmp_path):
    conn = seeded_conn(tmp_path / "origin.sqlite3")
    try:
        track = [
            {
                "timestamp": 1000,
                "lat": 40.1646,
                "lon": -105.1630,
                "on_ground": True,
                "velocity_kt": 10,
            }
        ]
        origin = origin_from_track(conn, track)
        assert origin["origin_airport_icao"] == "KLMO"
        assert origin["origin_city"] == "Longmont, CO"
        assert origin["origin_source"] == "ground_track_near_airport"
    finally:
        conn.close()


def test_origin_from_track_uses_first_seen_airport_fallback(tmp_path):
    conn = seeded_conn(tmp_path / "origin.sqlite3")
    try:
        track = [
            {
                "timestamp": 1000,
                "lat": 40.0394,
                "lon": -105.2258,
                "on_ground": False,
                "velocity_kt": 110,
            }
        ]
        origin = origin_from_track(conn, track)
        assert origin["origin_airport_icao"] == "KBDU"
        assert origin["origin_source"] == "first_seen_near_airport"
    finally:
        conn.close()


def test_opensky_track_path_can_seed_origin(tmp_path):
    conn = seeded_conn(tmp_path / "origin.sqlite3")
    try:
        path = opensky_track_path_samples({
            "path": [[1000, 40.1646, -105.1630, 1500.0, 90.0, True]],
        })
        origin = origin_from_ground_track(conn, path)
        assert origin is not None
        assert origin["origin_airport_icao"] == "KLMO"
    finally:
        conn.close()


async def test_active_aircraft_count_only_counts_moving_aircraft_in_airport_ring():
    ap = airport()
    store = MemoryStore()
    now = int(datetime.now(ZoneInfo("UTC")).timestamp())
    params = ScanParams(airport_icao=ap.icao, user_lat=40.0, user_lon=-105.2, ring_nm=8)

    moving = sample(now - 20, ap.lat + 0.01, ap.lon - 0.01, 900, "moving")
    await store.add_track_sample("moving", moving, 300)

    outside = sample(now - 20, ap.lat + 0.3, ap.lon - 0.3, 900, "outside")
    await store.add_track_sample("outside", outside, 300)

    ground = sample(now - 20, ap.lat + 0.01, ap.lon - 0.01, 0, "ground")
    ground["on_ground"] = True
    await store.add_track_sample("ground", ground, 300)

    stationary = sample(now - 20, ap.lat + 0.01, ap.lon - 0.01, 900, "still")
    stationary["velocity_kt"] = 0
    await store.add_track_sample("still", stationary, 300)

    inferred_moving_a = sample(now - 80, ap.lat + 0.01, ap.lon - 0.01, 900, "nospeed")
    inferred_moving_b = sample(now - 20, ap.lat + 0.02, ap.lon - 0.02, 900, "nospeed")
    inferred_moving_a["velocity_kt"] = None
    inferred_moving_b["velocity_kt"] = None
    await store.add_track_sample("nospeed", inferred_moving_a, 300)
    await store.add_track_sample("nospeed", inferred_moving_b, 300)

    assert await active_aircraft_count(store, ap, params) == 2


def test_airport_label_for_unknown_opensky_departure(tmp_path):
    conn = seeded_conn(tmp_path / "origin.sqlite3")
    try:
        known = airport_label_for_icao(conn, "kbdu")
        unknown = airport_label_for_icao(conn, "kxyz")
        assert known["origin_label"] == "Boulder, CO (KBDU)"
        assert unknown["origin_label"] == "KXYZ"
        assert unknown["origin_confidence"] == "medium"
    finally:
        conn.close()


async def test_summary_description_combines_multiple_aircraft(tmp_path, monkeypatch):
    async def no_groq(*args):
        return None

    monkeypatch.setattr("app.services.generate_with_groq", no_groq)
    conn = seeded_conn(tmp_path / "summary.sqlite3")
    store = MemoryStore()
    settings = Settings(
        groq_api_key=None,
        database_path=str(tmp_path / "summary.sqlite3"),
        redis_url="memory://",
        timezone="America/Denver",
        opensky=None,
        opensky_client_id=None,
        opensky_client_secret=None,
    )
    params = ScanParams(airport_icao="KLMO", user_lat=40.167, user_lon=-105.17, user_elevation_ft=5055, window="1h")
    window = resolve_window("1h", "America/Denver")
    key = monitor_hash(params)
    try:
        for index, icao24 in enumerate(["abc123", "def456"]):
            event = {
                "id": f"{icao24}:circle:{window.start_ts + 60}",
                "type": "circle",
                "timestamp": window.start_ts + 60 + index,
                "icao24": icao24,
                "callsign": f"N{index + 1}23AB",
                "avg_loop_radius_nm": 1.2,
                "alt_band_ft": [800, 1400],
            }
            await store.add_event(key, event, 3600)
            await store.add_track_sample(icao24, {
                "icao24": icao24,
                "callsign": event["callsign"],
                "timestamp": window.start_ts + 60 + index,
                "lat": 40.1646,
                "lon": -105.1630,
                "geo_altitude_ft": 5900,
                "velocity_kt": 90,
                "on_ground": False,
            }, 3600)
        result = await build_summary_description(
            store,
            settings,
            conn,
            params,
            ["abc123", "def456"],
            ToneSliders(anger=3, niceness=6, respect=7, detail=6, local=3),
        )
        assert result["source"] == "fallback"
        assert result["metadata"]["aircraft_count"] == 2
        assert "2 aircraft" in result["text"]
    finally:
        await store.close()
        conn.close()


def test_offender_min_altitude_only_uses_pass_over_user_events():
    window = resolve_window("1h", "America/Denver", datetime(2026, 4, 24, 12, 0, tzinfo=ZoneInfo("UTC")))
    events = [
        {"type": "touch_and_go", "timestamp": window.start_ts + 60, "min_altitude_ft_agl": 0, "icao24": "abc123", "callsign": "N123AB"},
        {"type": "pass_over_user", "timestamp": window.start_ts + 120, "min_altitude_ft_agl": 900, "icao24": "abc123", "callsign": "N123AB"},
    ]
    rows = offender_rows(events, window, "America/Denver")
    assert rows[0]["min_altitude_ft_agl"] == 900


def test_worker_groups_open_sky_polls_by_airport():
    groups = monitor_poll_groups([
        {"airport_icao": "KBJC", "bbox": (39.0, -106.0, 40.0, -105.0), "hash": "one"},
        {"airport_icao": "KBJC", "bbox": (39.5, -105.5, 40.5, -104.5), "hash": "two"},
        {"airport_icao": "KBDU", "bbox": (41.0, -106.0, 42.0, -105.0), "hash": "three"},
    ])
    by_airport = {group["airport_icaos"][0]: group for group in groups if len(group["airport_icaos"]) == 1}
    assert len(groups) == 2
    assert by_airport["KBJC"]["bbox"] == (39.0, -106.0, 40.5, -104.5)
    assert len(by_airport["KBJC"]["monitors"]) == 2


def test_worker_uses_live_poll_interval():
    settings = Settings(
        live_poll_interval_seconds=15,
    )
    assert effective_poll_interval_seconds(settings) == 15


def test_worker_merges_nearby_airport_bboxes():
    groups = monitor_poll_groups([
        {"airport_icao": "KLMO", "bbox": (40.05, -105.25, 40.25, -105.05), "hash": "one"},
        {"airport_icao": "KBJC", "bbox": (39.80, -105.22, 40.02, -105.00), "hash": "two"},
    ], merge_distance_nm=5.0)
    assert len(groups) == 1
    assert groups[0]["airport_icaos"] == ["KBJC", "KLMO"]
    assert len(groups[0]["monitors"]) == 2


def test_readsb_aircraft_parser_normalizes_adsb_lol_shape():
    parsed = parse_readsb_aircraft(
        {
            "hex": "AC6315",
            "flight": "N898AJ  ",
            "r": "N898AJ",
            "t": "PC12",
            "alt_baro": 7625,
            "alt_geom": 7400,
            "gs": 140.6,
            "track": 326.31,
            "baro_rate": -512,
            "squawk": "1200",
            "lat": 40.102053,
            "lon": -105.041,
            "seen_pos": 1.7,
        },
        1745512345.0,
        "adsb_lol",
    )
    assert parsed is not None
    assert parsed["icao24"] == "ac6315"
    assert parsed["callsign"] == "N898AJ"
    assert parsed["geo_altitude_ft"] == 7400
    assert parsed["vertical_rate_fpm"] == -512
    assert parsed["timestamp"] == 1745512343
    assert parsed["source"] == "adsb_lol"


def test_readsb_aircraft_parser_accepts_millisecond_payload_time():
    parsed = parse_readsb_aircraft(
        {
            "hex": "A21ABF",
            "flight": "N2347X  ",
            "alt_baro": 7450,
            "lat": 40.118998,
            "lon": -105.051644,
            "seen_pos": 0.387,
        },
        1777071710501,
        "adsb_lol",
    )
    assert parsed is not None
    assert parsed["timestamp"] == 1777071710


def test_bbox_center_radius_covers_box():
    lat, lon, radius_nm = bbox_center_radius_nm((40.0, -105.2, 40.2, -105.0))
    assert round(lat, 2) == 40.10
    assert round(lon, 2) == -105.10
    assert radius_nm > 7


def _stub_live_state_client(settings: Settings, clients: dict) -> LiveStateClient:
    client = LiveStateClient.__new__(LiveStateClient)
    client.settings = settings
    client.backoff_until = {}
    client.last_source = None
    client.clients = clients
    client.health = {source: SourceHealth() for source in clients}
    return client


async def test_live_state_client_falls_back_to_next_provider():
    class FailingClient:
        async def states_bbox(self, bbox):
            raise httpx.ConnectError("offline")

    class WorkingClient:
        async def states_bbox(self, bbox):
            return [{"icao24": "abc123", "timestamp": 1000, "lat": 40.1, "lon": -105.1}]

    settings = Settings(live_source_priority="adsb_lol,airplanes_live")
    client = _stub_live_state_client(
        settings,
        {"adsb_lol": FailingClient(), "airplanes_live": WorkingClient()},
    )

    result = await client.states_bbox((40.0, -105.2, 40.2, -105.0))
    assert result.source == "airplanes_live"
    assert result.states[0]["icao24"] == "abc123"
    assert client.health["airplanes_live"].success_count == 1
    assert client.health["adsb_lol"].error_count == 1
    assert client.health["adsb_lol"].last_error is not None


async def test_live_state_client_falls_through_on_stale_payload():
    class StaleClient:
        source = "adsb_lol"

        async def states_bbox(self, bbox):
            raise LiveSourceStale("adsb_lol", 240)

    class FreshClient:
        async def states_bbox(self, bbox):
            return [{"icao24": "abc123", "timestamp": 1000, "lat": 40.1, "lon": -105.1}]

    settings = Settings(live_source_priority="adsb_lol,airplanes_live")
    client = _stub_live_state_client(
        settings,
        {"adsb_lol": StaleClient(), "airplanes_live": FreshClient()},
    )

    result = await client.states_bbox((40.0, -105.2, 40.2, -105.0))
    assert result.source == "airplanes_live"
    assert client.health["adsb_lol"].error_count == 1
    assert "stale" in (client.health["adsb_lol"].last_error or "")
    assert client.backoff_until["adsb_lol"] > 0


def test_priority_list_includes_paid_sources_only_when_configured():
    base = Settings(
        live_source_priority="adsbx,self_hosted,adsb_lol,adsb_fi,opensky",
        adsbx_rapidapi_key=None,
        self_hosted_feeder_base_url=None,
    )
    assert base.live_source_priority_list() == ["adsb_lol", "adsb_fi", "opensky"]

    with_paid = Settings(
        live_source_priority="adsbx,self_hosted,adsb_lol,adsb_fi,opensky",
        adsbx_rapidapi_key="test-key",
        self_hosted_feeder_base_url="http://feeder.local:8080",
    )
    assert with_paid.live_source_priority_list() == [
        "adsbx",
        "self_hosted",
        "adsb_lol",
        "adsb_fi",
        "opensky",
    ]


def test_priority_list_drops_unknown_and_dedupes():
    settings = Settings(live_source_priority="bogus,adsb_lol,adsb_lol,opensky")
    assert settings.live_source_priority_list() == ["adsb_lol", "opensky"]


def test_build_live_source_client_returns_none_when_paid_creds_missing():
    settings = Settings(adsbx_rapidapi_key=None, self_hosted_feeder_base_url=None)
    assert build_live_source_client("adsbx", settings) is None
    assert build_live_source_client("self_hosted", settings) is None
    assert build_live_source_client("adsb_fi", settings) is not None


def test_build_live_source_client_constructs_adsbx_with_rapidapi_headers():
    settings = Settings(adsbx_rapidapi_key="abc")
    client = build_live_source_client("adsbx", settings)
    assert client is not None
    assert client.source == "adsbx"
    assert client.extra_headers["x-rapidapi-key"] == "abc"
    assert client.extra_headers["x-rapidapi-host"].endswith("rapidapi.com")
    assert client.path_style == "lat_lon_dist"


def test_readsb_payload_aircraft_handles_alternate_keys():
    assert readsb_payload_aircraft({"ac": [{"hex": "a"}]}) == [{"hex": "a"}]
    assert readsb_payload_aircraft({"aircraft": [{"hex": "b"}]}) == [{"hex": "b"}]
    assert readsb_payload_aircraft({"states": [{"hex": "c"}]}) == [{"hex": "c"}]
    assert readsb_payload_aircraft({}) == []


def test_readsb_payload_now_handles_seconds_and_ms():
    assert readsb_payload_now({"now": 1700000000}) == 1700000000.0
    assert readsb_payload_now({"now": 1700000000123}) == 1700000000.123


def test_live_state_client_health_snapshot_marks_unavailable_sources():
    settings = Settings(live_source_priority="adsb_lol,adsb_fi")
    client = _stub_live_state_client(
        settings,
        {"adsb_lol": object(), "adsb_fi": object()},
    )
    snapshot = client.health_snapshot()
    assert set(snapshot) == {"adsb_lol", "adsb_fi"}
    assert snapshot["adsb_lol"]["available"] is True
    assert snapshot["adsb_lol"]["backoff_remaining_seconds"] == 0


def test_historical_snapshots_are_capped_to_opensky_one_hour_limit():
    now = datetime(2026, 4, 24, 18, 30, tzinfo=ZoneInfo("UTC"))
    window = resolve_window("6h", "America/Denver", now)
    settings = Settings(
        opensky_historical_limit_seconds=3600,
        opensky_historical_step_seconds=600,
    )
    timestamps = historical_snapshot_times(window, settings)
    assert timestamps[0] >= window.end_ts - 3600
    assert timestamps[-1] <= window.end_ts


async def test_historical_backfill_handles_forbidden_opensky(monkeypatch):
    class ForbiddenOpenSky:
        def __init__(self, settings):
            self.auth_failed = False

        async def states_bbox(self, bbox, at_ts=None):
            request = httpx.Request("GET", "https://opensky.example/states")
            response = httpx.Response(403, request=request)
            raise httpx.HTTPStatusError("forbidden", request=request, response=response)

        async def close(self):
            return None

    monkeypatch.setattr("app.services.OpenSkyClient", ForbiddenOpenSky)
    store = MemoryStore()
    settings = Settings(
        opensky_historical_enabled=True,
        opensky_client_id="client",
        opensky_client_secret="secret",
        opensky_historical_step_seconds=600,
    )
    params = ScanParams(airport_icao="KBJC", user_lat=40.0, user_lon=-105.0)
    window = resolve_window("1h", "America/Denver", datetime(2026, 4, 24, 18, 30, tzinfo=ZoneInfo("UTC")))

    result = await backfill_historical_states(store, settings, monitor_for_params(params, airport()), window)

    assert result["enabled"] is True
    assert result["available"] is False
    assert "rejected" in result["reason"]


async def test_historical_backfill_fetches_newest_missing_snapshots_first(monkeypatch):
    calls = []

    class RecordingOpenSky:
        def __init__(self, settings):
            self.auth_failed = False

        async def states_bbox(self, bbox, at_ts=None):
            calls.append(at_ts)
            return []

        async def close(self):
            return None

    monkeypatch.setattr("app.services.OpenSkyClient", RecordingOpenSky)
    store = MemoryStore()
    settings = Settings(
        opensky_historical_enabled=True,
        opensky_client_id="client",
        opensky_client_secret="secret",
        opensky_historical_step_seconds=600,
        opensky_historical_snapshots_per_scan=3,
    )
    params = ScanParams(airport_icao="KBJC", user_lat=40.0, user_lon=-105.0)
    window = resolve_window("1h", "America/Denver", datetime(2026, 4, 24, 18, 30, tzinfo=ZoneInfo("UTC")))

    result = await backfill_historical_states(store, settings, monitor_for_params(params, airport()), window)

    assert result["fetched"] == 3
    assert calls == [window.end_ts, window.end_ts - 600, window.end_ts - 1200]


async def test_track_response_merges_non_offender_recent_tracks():
    store = MemoryStore()
    window = resolve_window("1h", "America/Denver", datetime(2026, 4, 24, 18, 30, tzinfo=ZoneInfo("UTC")))
    ap = airport()
    offender = sample(window.end_ts - 40, ap.lat + 0.01, ap.lon - 0.01, 900, "abc123")
    non_offender = sample(window.end_ts - 30, ap.lat + 0.02, ap.lon - 0.02, 900, "def456")
    non_offender["callsign"] = "N456CD"
    await store.add_track_sample("abc123", offender, 3600)
    await store.add_track_sample("def456", non_offender, 3600)

    offender_tracks = await tracks_for_response(
        store,
        [{"icao24": "abc123", "callsign": "N123AB"}],
        window,
    )
    recent_tracks = await recent_tracks_for_response(
        store,
        window,
        (ap.lat - 0.1, ap.lon - 0.1, ap.lat + 0.1, ap.lon + 0.1),
    )

    rows = merge_track_rows(offender_tracks, recent_tracks)

    assert [row["icao24"] for row in rows] == ["abc123", "def456"]
    assert rows[1]["callsign"] == "N456CD"


def test_deterministic_description_respects_message_preferences():
    context = ComplaintContext(
        airport_name="Rocky Mountain Metropolitan Airport",
        airport_icao="KBJC",
        user_location_label="Broomfield, CO",
        time_window_label="in the last hour",
        callsign="N123AB",
        icao24="abc123",
        aircraft_type="unknown aircraft type",
        observed_from="12:00 PM",
        observed_to="12:30 PM",
        circles=4,
        avg_radius=1.2,
        alt_min=600,
        alt_max=1200,
        touch_and_gos=2,
        low_approaches=1,
        passes=3,
        avg_altitude_user=950,
        min_altitude_user=800,
        quiet_hours_events=0,
        peak_hours="12 PM",
        origin_airport="Boulder, CO (KBDU)",
        runway_used=None,
        airport_elevation_ft=5673,
        previous_report_count=2,
        message_preferences=MessagePreferences(
            include_all_detail=False,
            include_elevation=False,
            include_circles=True,
            include_altitude_over_house=False,
        ),
    )
    text = deterministic_description(context)
    assert "previously reported this same aircraft 2 complaints" in text
    assert "circling 4 times" in text
    assert "950 ft" not in text
    assert "5673" not in text


def test_score_suppresses_bonuses_for_short_window():
    short = resolve_window("30m", "America/Denver", datetime(2026, 4, 24, 12, 0, tzinfo=ZoneInfo("UTC")))
    long = resolve_window("1h", "America/Denver", datetime(2026, 4, 24, 12, 0, tzinfo=ZoneInfo("UTC")))
    events = [
        {"type": "pass_over_user", "timestamp": short.end_ts - 60, "min_altitude_ft_agl": 500, "icao24": "abc123"},
    ]
    assert event_counts(events)["passes"] == 1
    assert score_events(events, short, "America/Denver") == 3
    assert score_events(events, long, "America/Denver") > 3


def test_flightaware_ident_classifier_filters_n_numbers():
    assert FlightAwareClient.looks_like_airline_ident("UAL640") is True
    assert FlightAwareClient.looks_like_airline_ident("SWA3533") is True
    assert FlightAwareClient.looks_like_airline_ident("DAL1234") is True
    assert FlightAwareClient.looks_like_airline_ident("KOW106") is True
    assert FlightAwareClient.looks_like_airline_ident("N123VM") is False
    assert FlightAwareClient.looks_like_airline_ident("N4052F") is False
    assert FlightAwareClient.looks_like_airline_ident("") is False
    assert FlightAwareClient.looks_like_airline_ident(None) is False
    assert FlightAwareClient.looks_like_airline_ident("garbage!") is False


def test_flightaware_pick_flight_prefers_window_overlap():
    flights = [
        {  # earlier flight, doesn't overlap
            "origin": {"code_icao": "KSEA", "city": "Seattle"},
            "actual_off": "2026-05-16T10:00:00Z",
            "actual_on": "2026-05-16T12:00:00Z",
        },
        {  # current flight, overlaps the observation window
            "origin": {"code_icao": "KSFO", "city": "San Francisco"},
            "actual_off": "2026-05-16T16:00:00Z",
            "actual_on": "2026-05-16T18:00:00Z",
        },
    ]
    # Observation 17:00-17:30 UTC
    first_seen = int(datetime(2026, 5, 16, 17, 0, tzinfo=ZoneInfo("UTC")).timestamp())
    last_seen = int(datetime(2026, 5, 16, 17, 30, tzinfo=ZoneInfo("UTC")).timestamp())
    chosen = FlightAwareClient.pick_flight(flights, first_seen, last_seen)
    assert chosen is flights[1]


async def test_flightaware_origin_for_callsign_happy_path():
    settings = Settings(flightaware_api_key="fake-key")
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["headers"] = dict(request.headers)
        return httpx.Response(
            200,
            json={
                "flights": [
                    {
                        "ident": "UAL640",
                        "origin": {"code_icao": "CYVR", "code_iata": "YVR", "city": "Vancouver", "name": "YVR"},
                        "destination": {"code_icao": "KDEN", "city": "Denver"},
                        "actual_off": "2026-05-16T16:00:00Z",
                        "actual_on": None,
                    }
                ]
            },
        )

    fa = FlightAwareClient(settings)
    fa.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        first_seen = int(datetime(2026, 5, 16, 17, 0, tzinfo=ZoneInfo("UTC")).timestamp())
        last_seen = first_seen + 1800
        result = await fa.origin_for_callsign("UAL640", first_seen, last_seen)
    finally:
        await fa.close()

    assert result is not None
    assert result["origin_airport_icao"] == "CYVR"
    assert result["origin_city"] == "Vancouver"
    assert result["origin_label"] == "Vancouver (CYVR)"
    assert result["origin_source"] == "flightaware_aeroapi"
    assert result["origin_confidence"] == "high"
    assert captured["headers"]["x-apikey"] == "fake-key"
    assert "/flights/UAL640" in captured["url"]


async def test_flightaware_skips_n_number_callsign():
    settings = Settings(flightaware_api_key="fake-key")
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(200, json={"flights": []})

    fa = FlightAwareClient(settings)
    fa.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        result = await fa.origin_for_callsign("N4052F", 1000, 2000)
    finally:
        await fa.close()

    assert result is None
    assert calls["n"] == 0  # never hit the network


async def test_flightaware_marks_auth_failed_on_401():
    settings = Settings(flightaware_api_key="bad-key")

    def handler(request):
        return httpx.Response(401, json={"detail": "invalid key"})

    fa = FlightAwareClient(settings)
    fa.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        result = await fa.origin_for_callsign("UAL640", 1000, 2000)
    finally:
        await fa.close()

    assert result is None
    assert fa.auth_failed is True


def test_flightaware_disabled_when_key_missing():
    settings = Settings(flightaware_api_key=None)
    fa = FlightAwareClient(settings)
    assert fa.settings.flightaware_api_key is None
    # The origin path in resolve_origin should short-circuit; nothing to assert
    # beyond config, but at least confirm the classifier still functions.
    assert FlightAwareClient.looks_like_airline_ident("UAL640") is True

