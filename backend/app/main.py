from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import logging
import math
import os
import secrets
import time
from contextlib import asynccontextmanager
from typing import Annotated

# Make sure app-level INFO logs reach the container stdout. Uvicorn's default
# config only configures its own loggers; without this our `logger.info` calls
# in services / archive / scan staging would silently drop on production.
logging.basicConfig(
    level=os.environ.get("CIRCLEJERK_LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)

import httpx
from fastapi import Cookie, Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from . import db, patterns, track_history, pattern_circuits, vnap
from .db import db_session
from .detectors import pass_geometry_key
from .domain import ScanParams, monitor_hash
from .geo import bbox_for_radius
from .llm import MessagePreferences
from .services import build_description, build_scan_response, build_summary_description, build_worst_offenders
from .settings import Settings, get_settings
from .store import Store, make_store
from .tone import PRESETS, sliders_from_request
from .windows import validate_window


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    db.init_db(settings.database_path)
    app.state.settings = settings
    app.state.store = make_store(settings.redis_url)
    yield
    await app.state.store.close()


app = FastAPI(title="Circle Jerks API", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class MessagePreferencesRequest(BaseModel):
    include_all_detail: bool = True
    include_elevation: bool = True
    include_circles: bool = True
    include_altitude_over_house: bool = True
    include_db_at_home: bool = True


class SummaryComplaintRequest(BaseModel):
    airport_icao: str
    user_lat: float
    user_lon: float
    window: str = "1h"
    icao24s: list[str] = Field(min_length=1, max_length=40)
    sliders: dict[str, int] = Field(default_factory=dict)
    message_preferences: MessagePreferencesRequest = Field(default_factory=MessagePreferencesRequest)
    report_counts: dict[str, int] = Field(default_factory=dict)
    system_prompt: str | None = Field(default=None, max_length=4000)


class ActivityAircraft(BaseModel):
    icao24: str = Field(min_length=1, max_length=16)
    callsign: str | None = Field(default=None, max_length=32)
    circles: int = Field(default=0, ge=0, le=10000)
    touch_and_gos: int = Field(default=0, ge=0, le=10000)
    low_approaches: int = Field(default=0, ge=0, le=10000)
    passes_over_user: int = Field(default=0, ge=0, le=10000)
    origin_airport_icao: str | None = Field(default=None, max_length=8)
    origin_label: str | None = Field(default=None, max_length=120)


class ActivityHeartbeatRequest(BaseModel):
    visitor_id: str = Field(min_length=8, max_length=80)
    airport_icao: str | None = Field(default=None, max_length=8)
    user_lat: float | None = None
    user_lon: float | None = None
    path: str | None = Field(default="/", max_length=200)


class ActivitySubmissionRequest(BaseModel):
    visitor_id: str | None = Field(default=None, min_length=8, max_length=80)
    airport_icao: str | None = Field(default=None, max_length=8)
    user_lat: float | None = None
    user_lon: float | None = None
    window: str | None = Field(default=None, max_length=20)
    mode: str | None = Field(default=None, max_length=20)
    text: str = Field(min_length=1, max_length=20000)
    targets: list[ActivityAircraft] = Field(default_factory=list, max_length=80)


class PatternPoint(BaseModel):
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)


class PatternSaveRequest(BaseModel):
    points: list[PatternPoint] = Field(min_length=1, max_length=50)
    closed: bool = True
    name: str | None = Field(default=None, max_length=80)
    change_note: str | None = Field(default=None, max_length=200)
    visitor_id: str | None = Field(default=None, min_length=8, max_length=80)


class PatternRevertRequest(BaseModel):
    version: int = Field(ge=1)
    visitor_id: str | None = Field(default=None, min_length=8, max_length=80)


class AdminLoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=80)
    password: str = Field(min_length=1, max_length=400)


VALID_OWNER_TYPES = frozenset({
    "individual", "llc", "corporation", "government",
    "flight_school", "university", "club", "trust",
    "skydiving", "commercial_airline", "unknown",
})


class OwnerClassRequest(BaseModel):
    owner_type: str
    visitor_id: str | None = Field(default=None, min_length=8, max_length=80)
    change_note: str | None = Field(default=None, max_length=280)


class CommunityNoteRequest(BaseModel):
    note: str = Field(min_length=1, max_length=280)
    is_flight_school: bool = False
    visitor_id: str | None = Field(default=None, min_length=8, max_length=80)


PATTERN_EDIT_WINDOW_S = 3600
PATTERN_EDIT_MAX_PER_WINDOW = 30

DEMO_USER_LAT = 40.1672
DEMO_USER_LON = -105.1019
DEMO_AIRCRAFT = [
    {
        "icao24": "a4c1d8",
        "callsign": "N4052F",
        "registration": "N4052F",
        "type_icao": "C172",
        "type_description": "Cessna 172 Skyhawk",
        "operator": "Demo flight school",
        "report_count": 17,
    },
    {
        "icao24": "a5a764",
        "callsign": "N4632F",
        "registration": "N4632F",
        "type_icao": "PA28",
        "type_description": "Piper PA-28 Cherokee",
        "operator": "Demo flight school",
        "report_count": 9,
    },
    {
        "icao24": "a9ce3a",
        "callsign": "N7306E",
        "registration": "N7306E",
        "type_icao": "C152",
        "type_description": "Cessna 152",
        "operator": "Demo flight school",
        "report_count": 5,
    },
]


def settings_dep() -> Settings:
    return app.state.settings


def store_dep() -> Store:
    return app.state.store


ADMIN_COOKIE_NAME = "circlejerk_admin"


def client_ip(request: Request) -> str | None:
    forwarded_for = request.headers.get("x-forwarded-for")
    if forwarded_for:
        return forwarded_for.split(",", 1)[0].strip()[:80] or None
    real_ip = request.headers.get("x-real-ip")
    if real_ip:
        return real_ip.strip()[:80] or None
    return request.client.host[:80] if request.client else None


def _pattern_response(row: dict) -> dict:
    return {
        "id": row["id"],
        "icao": row["icao"],
        "runway_id": row["runway_id"],
        "version": row["version"],
        "name": row["name"],
        "locked": bool(row["locked"]),
        "geometry": json.loads(row["geometry_json"]),
        "change_note": row["change_note"],
        "created_at": row["created_at"],
    }


def user_agent(request: Request) -> str | None:
    value = request.headers.get("user-agent")
    return value[:500] if value else None


def admin_auth_configured(settings: Settings) -> bool:
    return bool(settings.admin_password or settings.admin_password_hash)


def verify_pbkdf2_password(candidate: str, encoded: str) -> bool:
    try:
        algorithm, iterations, salt, expected = encoded.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        digest = hashlib.pbkdf2_hmac(
            "sha256",
            candidate.encode("utf-8"),
            salt.encode("utf-8"),
            int(iterations),
        ).hex()
        return secrets.compare_digest(digest, expected)
    except (TypeError, ValueError):
        return False


def verify_admin_password(settings: Settings, password: str) -> bool:
    if settings.admin_password_hash:
        return verify_pbkdf2_password(password, settings.admin_password_hash)
    if settings.admin_password:
        return secrets.compare_digest(password, settings.admin_password)
    return False


def _b64encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def sign_admin_token(settings: Settings) -> str:
    payload = {
        "sub": settings.admin_username,
        "exp": int(time.time()) + settings.admin_session_seconds,
        "nonce": secrets.token_urlsafe(12),
    }
    body = _b64encode(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8"))
    signature = hmac.new(settings.app_secret.encode("utf-8"), body.encode("ascii"), hashlib.sha256).hexdigest()
    return f"{body}.{signature}"


def decode_admin_token(settings: Settings, token: str) -> dict | None:
    try:
        body, signature = token.split(".", 1)
    except ValueError:
        return None
    expected = hmac.new(settings.app_secret.encode("utf-8"), body.encode("ascii"), hashlib.sha256).hexdigest()
    if not secrets.compare_digest(signature, expected):
        return None
    try:
        payload = json.loads(_b64decode(body))
    except (json.JSONDecodeError, ValueError):
        return None
    if payload.get("sub") != settings.admin_username:
        return None
    if int(payload.get("exp", 0)) < int(time.time()):
        return None
    return payload


def require_admin(
    settings: Annotated[Settings, Depends(settings_dep)],
    admin_session: Annotated[str | None, Cookie(alias=ADMIN_COOKIE_NAME)] = None,
) -> dict:
    if not admin_auth_configured(settings):
        raise HTTPException(status_code=503, detail="admin credentials are not configured")
    if not admin_session:
        raise HTTPException(status_code=401, detail="admin login required")
    payload = decode_admin_token(settings, admin_session)
    if not payload:
        raise HTTPException(status_code=401, detail="admin login required")
    return payload


@app.get("/healthz")
async def healthz(settings: Annotated[Settings, Depends(settings_dep)]):
    with db_session(settings.database_path) as conn:
        airport_count = conn.execute("SELECT COUNT(*) AS count FROM airports").fetchone()["count"]
        form_count = conn.execute("SELECT COUNT(*) AS count FROM complaint_forms").fetchone()["count"]
    return {
        "ok": True,
        "environment": settings.environment,
        "sqlite_seeded": airport_count > 0 and form_count >= 10,
        "airports": airport_count,
        "complaint_forms": form_count,
    }


def _demo_aircraft_for_airport(airport_icao: str) -> list[dict]:
    prefix = {"KLMO": "a", "KBJC": "b", "KBDU": "c"}.get(airport_icao.upper(), "d")
    return [
        {
            **aircraft,
            "icao24": f"{prefix}{aircraft['icao24'][1:]}".lower(),
        }
        for aircraft in DEMO_AIRCRAFT
    ]


def _demo_track_sample(airport: db.Airport, aircraft: dict, timestamp: int, lat: float, lon: float, agl: int, heading: float) -> dict:
    return {
        "icao24": aircraft["icao24"],
        "callsign": aircraft["callsign"],
        "timestamp": timestamp,
        "lat": round(lat, 6),
        "lon": round(lon, 6),
        "geo_altitude_ft": airport.elevation_ft + agl,
        "velocity_kt": 86,
        "vertical_rate_fpm": 0,
        "heading_deg": round(heading % 360, 1),
        "on_ground": False,
    }


def _demo_track_samples(airport: db.Airport, aircraft: dict, index: int, now: int, user_lat: float, user_lon: float) -> list[dict]:
    samples = []
    loop_count = 14
    lat_radius = 0.012 + index * 0.003
    lon_radius = 0.018 + index * 0.004
    phase = index * 0.85
    for step in range(loop_count):
        angle = (step / loop_count * math.tau) + phase
        samples.append(_demo_track_sample(
            airport,
            aircraft,
            now - 3300 + step * 70 + index * 11,
            airport.lat + math.sin(angle) * lat_radius,
            airport.lon + math.cos(angle) * lon_radius,
            850 + index * 130,
            math.degrees(angle) + 90,
        ))

    if index < 2:
        pass_points = [
            (now - 260 + index * 20, user_lat - 0.018, user_lon - 0.010, 970 + index * 120, 25),
            (now - 205 + index * 20, user_lat - 0.002, user_lon - 0.001, 900 + index * 110, 25),
            (now - 150 + index * 20, user_lat + 0.016, user_lon + 0.008, 940 + index * 120, 25),
        ]
        for timestamp, lat, lon, agl, heading in pass_points:
            samples.append(_demo_track_sample(airport, aircraft, timestamp, lat, lon, agl, heading))

    recent_points = [
        (now - 125, airport.lat + 0.006 + index * 0.002, airport.lon - 0.015, 780 + index * 120, 70),
        (now - 70, airport.lat + 0.011 + index * 0.002, airport.lon - 0.004, 800 + index * 120, 88),
        (now - 20, airport.lat + 0.009 + index * 0.002, airport.lon + 0.010, 820 + index * 120, 110),
    ]
    for timestamp, lat, lon, agl, heading in recent_points:
        samples.append(_demo_track_sample(airport, aircraft, timestamp, lat, lon, agl, heading))
    return sorted(samples, key=lambda sample: sample["timestamp"])


def _demo_events(airport_icao: str, params: ScanParams, aircraft_rows: list[dict], now: int) -> list[dict]:
    seed_bucket = now // 300
    pass_key = pass_geometry_key(params)
    first, second, third = aircraft_rows
    return [
        {
            "id": f"demo-{airport_icao}-{seed_bucket}-circle-1",
            "type": "circle",
            "icao24": first["icao24"],
            "callsign": first["callsign"],
            "timestamp": now - 245,
            "airport_icao": airport_icao,
            "avg_loop_radius_nm": 1.4,
            "path_nm": 4.6,
            "closure_nm": 0.3,
            "turn_degrees": 382,
            "turn_direction": "left",
            "min_altitude_ft_agl": 860,
        },
        {
            "id": f"demo-{airport_icao}-{seed_bucket}-pass-1",
            "type": "pass_over_user",
            "icao24": first["icao24"],
            "callsign": first["callsign"],
            "timestamp": now - 185,
            "airport_icao": airport_icao,
            "min_altitude_ft_agl": 900,
            "avg_altitude_ft_agl": 930,
            "closest_horizontal_nm": 0.04,
            "pass_times": [now - 185],
            "pass_geometry_key": pass_key,
            "pass_radius_nm": params.pass_radius_nm,
        },
        {
            "id": f"demo-{airport_icao}-{seed_bucket}-touch-and-go-1",
            "type": "touch_and_go",
            "icao24": first["icao24"],
            "callsign": first["callsign"],
            "timestamp": now - 105,
            "airport_icao": airport_icao,
            "runway_used": "active",
            "min_altitude_ft_agl": 35,
        },
        {
            "id": f"demo-{airport_icao}-{seed_bucket}-pass-2",
            "type": "pass_over_user",
            "icao24": second["icao24"],
            "callsign": second["callsign"],
            "timestamp": now - 255,
            "airport_icao": airport_icao,
            "min_altitude_ft_agl": 1010,
            "avg_altitude_ft_agl": 1060,
            "closest_horizontal_nm": 0.07,
            "pass_times": [now - 255],
            "pass_geometry_key": pass_key,
            "pass_radius_nm": params.pass_radius_nm,
        },
        {
            "id": f"demo-{airport_icao}-{seed_bucket}-circle-2",
            "type": "circle",
            "icao24": second["icao24"],
            "callsign": second["callsign"],
            "timestamp": now - 195,
            "airport_icao": airport_icao,
            "avg_loop_radius_nm": 1.8,
            "path_nm": 5.1,
            "closure_nm": 0.5,
            "turn_degrees": 405,
            "turn_direction": "right",
            "min_altitude_ft_agl": 990,
        },
        {
            "id": f"demo-{airport_icao}-{seed_bucket}-touch-and-go-2",
            "type": "touch_and_go",
            "icao24": second["icao24"],
            "callsign": second["callsign"],
            "timestamp": now - 75,
            "airport_icao": airport_icao,
            "runway_used": "active",
            "min_altitude_ft_agl": 42,
        },
        {
            "id": f"demo-{airport_icao}-{seed_bucket}-low-approach-1",
            "type": "low_approach",
            "icao24": third["icao24"],
            "callsign": third["callsign"],
            "timestamp": now - 220,
            "airport_icao": airport_icao,
            "runway_used": "active",
            "min_altitude_ft_agl": 90,
        },
        {
            "id": f"demo-{airport_icao}-{seed_bucket}-circle-3",
            "type": "circle",
            "icao24": third["icao24"],
            "callsign": third["callsign"],
            "timestamp": now - 140,
            "airport_icao": airport_icao,
            "avg_loop_radius_nm": 1.2,
            "path_nm": 3.8,
            "closure_nm": 0.4,
            "turn_degrees": 350,
            "turn_direction": "left",
            "min_altitude_ft_agl": 1120,
        },
    ]


@app.post("/dev/seed_demo")
async def seed_demo(
    store: Annotated[Store, Depends(store_dep)],
    settings: Annotated[Settings, Depends(settings_dep)],
    airport_icao: str | None = None,
    user_lat: float = DEMO_USER_LAT,
    user_lon: float = DEMO_USER_LON,
):
    if settings.environment == "production":
        raise HTTPException(status_code=404, detail="not found")

    airport_codes = [airport_icao.upper()] if airport_icao else [settings.default_airport_icao.upper(), "KLMO", "KBDU"]
    now = int(time.time())
    seeded = []
    with db_session(settings.database_path) as conn:
        for code in dict.fromkeys(airport_codes):
            airport = db.get_airport(conn, code)
            if not airport:
                continue
            params = ScanParams(
                airport_icao=airport.icao,
                user_lat=user_lat,
                user_lon=user_lon,
                ring_nm=8,
                pass_radius_nm=0.5,
                pass_ceiling_ft=5000,
                window="1h",
            ).normalized()
            key = monitor_hash(params)
            aircraft_rows = _demo_aircraft_for_airport(airport.icao)
            for index, aircraft in enumerate(aircraft_rows):
                conn.execute(
                    """
                    INSERT INTO aircraft_cache
                    (icao24, registration, type_icao, type_description, operator, last_updated)
                    VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(icao24) DO UPDATE SET
                      registration = excluded.registration,
                      type_icao = excluded.type_icao,
                      type_description = excluded.type_description,
                      operator = excluded.operator,
                      last_updated = excluded.last_updated
                    """,
                    (
                        aircraft["icao24"],
                        aircraft["registration"],
                        aircraft["type_icao"],
                        aircraft["type_description"],
                        aircraft["operator"],
                        now,
                    ),
                )
                conn.execute(
                    """
                    INSERT INTO aircraft_report_counts
                    (icao24, callsign, registration, report_count, first_reported_at, last_reported_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(icao24) DO UPDATE SET
                      callsign = excluded.callsign,
                      registration = excluded.registration,
                      report_count = excluded.report_count,
                      last_reported_at = excluded.last_reported_at
                    """,
                    (
                        aircraft["icao24"],
                        aircraft["callsign"],
                        aircraft["registration"],
                        aircraft["report_count"],
                        now - 7 * 24 * 3600,
                        now - 600 + index * 90,
                    ),
                )
                for sample in _demo_track_samples(airport, aircraft, index, now, user_lat, user_lon):
                    await store.add_track_sample(aircraft["icao24"], sample, settings.track_ttl_seconds)

            events = _demo_events(airport.icao, params, aircraft_rows, now)
            for event in events:
                await store.add_event(key, event, settings.event_ttl_seconds)
            seeded.append({
                "airport_icao": airport.icao,
                "monitor_hash": key,
                "aircraft": len(aircraft_rows),
                "events": len(events),
            })
    return {
        "ok": True,
        "message": "Seeded local demo aircraft, events, tracks, and repeat-report counts.",
        "user_location": {"lat": user_lat, "lon": user_lon},
        "seeded": seeded,
    }


@app.post("/activity/heartbeat")
async def activity_heartbeat(
    payload: ActivityHeartbeatRequest,
    request: Request,
    settings: Annotated[Settings, Depends(settings_dep)],
):
    now = int(time.time())
    with db_session(settings.database_path) as conn:
        db.record_visitor_activity(
            conn,
            visitor_id=payload.visitor_id,
            now=now,
            ip_address=client_ip(request),
            user_agent=user_agent(request),
            path=payload.path,
            airport_icao=payload.airport_icao,
            user_lat=payload.user_lat,
            user_lon=payload.user_lon,
        )
    return {"ok": True, "seen_at": now}


@app.get("/activity/online")
async def activity_online(settings: Annotated[Settings, Depends(settings_dep)]):
    now = int(time.time())
    with db_session(settings.database_path) as conn:
        count = db.online_visitor_count(
            conn, now=now, active_window_seconds=settings.active_user_window_seconds
        )
    return {"count": count}


@app.post("/activity/submissions")
async def activity_submission(
    payload: ActivitySubmissionRequest,
    request: Request,
    settings: Annotated[Settings, Depends(settings_dep)],
):
    now = int(time.time())
    text_hash = hashlib.sha256(payload.text.encode("utf-8")).hexdigest()
    with db_session(settings.database_path) as conn:
        submission_id = db.record_submission(
            conn,
            now=now,
            visitor_id=payload.visitor_id,
            ip_address=client_ip(request),
            user_agent=user_agent(request),
            airport_icao=payload.airport_icao,
            user_lat=payload.user_lat,
            user_lon=payload.user_lon,
            window_code=payload.window,
            mode=payload.mode,
            text=payload.text,
            text_hash=text_hash,
            targets=[target.model_dump() for target in payload.targets],
        )
    return {"ok": True, "submission_id": submission_id}


@app.post("/admin/login")
async def admin_login(
    payload: AdminLoginRequest,
    response: Response,
    settings: Annotated[Settings, Depends(settings_dep)],
):
    if not admin_auth_configured(settings):
        raise HTTPException(status_code=503, detail="admin credentials are not configured")
    if payload.username != settings.admin_username or not verify_admin_password(settings, payload.password):
        raise HTTPException(status_code=401, detail="invalid admin credentials")
    response.set_cookie(
        ADMIN_COOKIE_NAME,
        sign_admin_token(settings),
        max_age=settings.admin_session_seconds,
        httponly=True,
        secure=settings.environment == "production",
        samesite="strict",
        path="/",
    )
    return {"ok": True, "username": settings.admin_username}


@app.post("/admin/logout")
async def admin_logout(response: Response):
    response.delete_cookie(ADMIN_COOKIE_NAME, path="/")
    return {"ok": True}


@app.get("/admin/session")
async def admin_session(
    _: Annotated[dict, Depends(require_admin)],
    settings: Annotated[Settings, Depends(settings_dep)],
):
    return {"ok": True, "username": settings.admin_username}


@app.get("/admin/dashboard")
async def admin_dashboard(
    _: Annotated[dict, Depends(require_admin)],
    settings: Annotated[Settings, Depends(settings_dep)],
):
    with db_session(settings.database_path) as conn:
        return db.admin_dashboard(
            conn,
            now=int(time.time()),
            active_window_seconds=settings.active_user_window_seconds,
        )


@app.get("/admin/live_sources")
async def admin_live_sources(
    _: Annotated[dict, Depends(require_admin)],
    store: Annotated[Store, Depends(store_dep)],
    settings: Annotated[Settings, Depends(settings_dep)],
):
    snapshot = await store.get_cache("live_sources:health")
    return {
        "priority": settings.live_source_priority_list(),
        "configured_priority": settings.live_source_priority,
        "max_staleness_seconds": settings.live_source_max_staleness_seconds,
        "poll_interval_seconds": settings.live_poll_interval_seconds,
        "paid_sources_configured": {
            "adsbx": bool(settings.adsbx_rapidapi_key),
            "self_hosted": bool(settings.self_hosted_feeder_base_url),
            "opensky_auth": all(settings.opensky_credentials()),
        },
        "sources": snapshot if isinstance(snapshot, dict) else {},
    }


@app.get("/config")
async def config(settings: Annotated[Settings, Depends(settings_dep)]):
    return {
        "default_airport_icao": settings.default_airport_icao,
        "buy_me_coffee_url": settings.buy_me_coffee_url,
        "presets": PRESETS,
    }


@app.get("/live_status")
async def live_status(
    store: Annotated[Store, Depends(store_dep)],
    settings: Annotated[Settings, Depends(settings_dep)],
):
    snapshot = await store.get_cache("live_sources:health")
    sources_by_state: dict[str, dict] = {}
    if isinstance(snapshot, dict):
        sources_by_state = snapshot
    priority = settings.live_source_priority_list()
    primary = priority[0] if priority else None
    primary_health = sources_by_state.get(primary or "")
    healthy_paid = {
        "adsbx": bool(settings.adsbx_rapidapi_key)
        and isinstance(sources_by_state.get("adsbx"), dict)
        and sources_by_state["adsbx"].get("success_count", 0) > 0
        and not sources_by_state["adsbx"].get("backoff_remaining_seconds"),
    }
    serving_source = None
    serving_state = "unknown"
    if primary_health and primary_health.get("success_count", 0) > 0:
        serving_source = primary
        serving_state = "primary"
    else:
        for source in priority:
            row = sources_by_state.get(source)
            if isinstance(row, dict) and row.get("success_count", 0) > 0:
                serving_source = source
                serving_state = "fallback"
                break
    overall = "ok"
    if not sources_by_state:
        overall = "unknown"
    elif serving_state == "fallback":
        overall = "degraded"
    elif serving_state == "unknown":
        overall = "down"
    return {
        "overall": overall,
        "serving_source": serving_source,
        "serving_state": serving_state,
        "primary": primary,
        "paid_configured": {
            "adsbx": bool(settings.adsbx_rapidapi_key),
            "flightaware": bool(settings.flightaware_api_key),
        },
        "paid_healthy": healthy_paid,
        "updated_at": int(time.time()),
    }


@app.get("/sponsors")
async def sponsors(
    store: Annotated[Store, Depends(store_dep)],
    settings: Annotated[Settings, Depends(settings_dep)],
):
    cache_key = "sponsors:bmc"
    cached = await store.get_cache(cache_key)
    if isinstance(cached, dict):
        return cached
    payload = {
        "configured": bool(settings.bmc_api_token),
        "support_url": settings.buy_me_coffee_url,
        "supporters": [],
        "source": "buymeacoffee",
    }
    if not settings.bmc_api_token:
        await store.set_cache(cache_key, payload, settings.bmc_cache_seconds)
        return payload
    try:
        async with httpx.AsyncClient(timeout=settings.request_timeout_seconds) as client:
            response = await client.get(
                f"{settings.bmc_api_base_url}/supporters",
                headers={
                    "Authorization": f"Bearer {settings.bmc_api_token}",
                    "Accept": "application/json",
                },
                params={"per_page": 24},
            )
        if response.status_code == 200:
            data = response.json()
            rows = data.get("data") or []
            payload["supporters"] = [
                {
                    "name": (row.get("supporter_name") or row.get("payer_name") or "Anonymous").strip() or "Anonymous",
                    "coffees": int(row.get("support_coffees") or 0),
                    "message": (row.get("support_note") or row.get("message") or "").strip() or None,
                    "supported_at": row.get("support_created_on") or row.get("created_at"),
                }
                for row in rows
            ]
            payload["total"] = data.get("total") or len(payload["supporters"])
        else:
            payload["error"] = f"BMC API returned {response.status_code}"
    except httpx.HTTPError as exc:
        payload["error"] = f"BMC API unreachable: {type(exc).__name__}"
    await store.set_cache(cache_key, payload, settings.bmc_cache_seconds)
    return payload


LIVEATC_FEED_TYPES: list[tuple[str, str]] = [
    ("twr", "Tower"),
    ("app", "Approach / Departure"),
    ("atis", "ATIS"),
    ("gnd", "Ground"),
    ("ctaf", "CTAF / Unicom"),
]

# Hand-curated overrides: airports whose LiveATC feed IDs deviate from the
# standard <icao_lower>_<type> pattern. Each entry maps an ICAO to one or more
# {id, label} feeds.
LIVEATC_FEED_OVERRIDES: dict[str, list[dict]] = {
    "KLMO": [
        {"id": "klmo", "label": "Longmont CTAF (122.975)"},
    ],
    "KDEN": [
        {"id": "kden_twr_n", "label": "Tower (North)"},
        {"id": "kden_twr_s", "label": "Tower (South)"},
        {"id": "kden_atis", "label": "ATIS"},
        {"id": "kden_app_finals", "label": "Approach (Finals)"},
    ],
    "KAPA": [
        {"id": "kapa_twr_gnd", "label": "Tower / Ground"},
        {"id": "kapa_atis", "label": "ATIS"},
    ],
}


@app.get("/atc_feeds")
async def atc_feeds(airport_icao: str):
    icao = airport_icao.strip().upper()
    if not icao or len(icao) > 8:
        raise HTTPException(status_code=422, detail="airport_icao required")
    override = LIVEATC_FEED_OVERRIDES.get(icao)
    if override:
        feeds = [
            {
                "id": feed["id"],
                "label": feed["label"],
                "stream_url": f"https://d.liveatc.net/{feed['id']}",
            }
            for feed in override
        ]
    else:
        feeds = [
            {
                "id": f"{icao.lower()}_{suffix}",
                "label": label,
                "stream_url": f"https://d.liveatc.net/{icao.lower()}_{suffix}",
            }
            for suffix, label in LIVEATC_FEED_TYPES
        ]
    return {
        "airport_icao": icao,
        "feeds": feeds,
        "external_search_url": f"https://www.liveatc.net/search/?icao={icao}",
        "note": "Feeds are best-effort guesses based on LiveATC's standard naming. If none play, use 'Search on LiveATC' to find the right stream.",
    }


@app.get("/repeat_offenders")
async def repeat_offenders(
    settings: Annotated[Settings, Depends(settings_dep)],
    min_reports: int = 2,
    limit: int = 12,
):
    with db_session(settings.database_path) as conn:
        rows = db.top_repeat_offenders(
            conn,
            min_reports=max(settings.repeat_offender_min_reports, int(min_reports)),
            limit=min(settings.repeat_offender_limit, max(1, int(limit))),
        )
    return {
        "min_reports": max(settings.repeat_offender_min_reports, int(min_reports)),
        "count": len(rows),
        "aircraft": rows,
    }


@app.get("/geocode")
async def geocode(
    q: Annotated[str, Query(min_length=2, max_length=200)],
    settings: Annotated[Settings, Depends(settings_dep)],
):
    async with httpx.AsyncClient(timeout=settings.request_timeout_seconds) as client:
        response = await client.get(
            "https://nominatim.openstreetmap.org/search",
            params={"q": q, "format": "jsonv2", "limit": 5},
            headers={"User-Agent": f"circlejerk-prototype/0.1 ({settings.public_base_url})"},
        )
    if response.status_code >= 400:
        raise HTTPException(status_code=response.status_code, detail="geocoding failed")
    return {
        "features": [
            {
                "id": item.get("place_id"),
                "place_name": item.get("display_name"),
                "center": [float(item["lon"]), float(item["lat"])],
            }
            for item in response.json()
            if item.get("lon") and item.get("lat")
        ]
    }


@app.get("/reverse_geocode")
async def reverse_geocode(
    lat: Annotated[float, Query(ge=-90.0, le=90.0)],
    lon: Annotated[float, Query(ge=-180.0, le=180.0)],
    settings: Annotated[Settings, Depends(settings_dep)],
):
    async with httpx.AsyncClient(timeout=settings.request_timeout_seconds) as client:
        response = await client.get(
            "https://nominatim.openstreetmap.org/reverse",
            params={"lat": lat, "lon": lon, "format": "jsonv2", "zoom": 14},
            headers={"User-Agent": f"circlejerk-prototype/0.1 ({settings.public_base_url})"},
        )
    if response.status_code >= 400:
        raise HTTPException(status_code=response.status_code, detail="reverse geocoding failed")
    data = response.json()
    address = data.get("address") or {}
    parts = [
        address.get("neighbourhood") or address.get("suburb") or address.get("hamlet"),
        address.get("city") or address.get("town") or address.get("village") or address.get("county"),
        address.get("state"),
    ]
    short = ", ".join(part for part in parts if part)
    return {
        "display_name": data.get("display_name"),
        "short_name": short or data.get("display_name"),
    }


# Curated overrides for the Save Our Skies Alliance per-airport pages. Keys
# are ICAO codes; values are the canonical SOSA URL. Extend as we discover
# matches that the slug heuristic below can't construct correctly (typically
# because SOSA's airport name differs from our DB's name).
SOSA_OVERRIDES: dict[str, str] = {
    "KLMO": "https://www.saveourskiesalliance.org/vance-brand-municipal-airport--lmo.html",
}
SOSA_HOME = "https://www.saveourskiesalliance.org/"


def _sosa_slug(name: str) -> str:
    import re

    s = name.lower()
    s = re.sub(r"[^a-z0-9\s-]", "", s)
    s = re.sub(r"\s+", "-", s.strip())
    s = re.sub(r"-+", "-", s)
    return s


@app.get("/airports/{icao}/sosa_url")
async def airport_sosa_url(
    icao: str,
    store: Annotated[Store, Depends(store_dep)],
    settings: Annotated[Settings, Depends(settings_dep)],
):
    icao_up = icao.upper()
    cache_key = f"sosa_url:{icao_up}"
    cached = await store.get_cache(cache_key)
    if isinstance(cached, dict) and isinstance(cached.get("url"), str):
        return cached

    if icao_up in SOSA_OVERRIDES:
        result = {"url": SOSA_OVERRIDES[icao_up], "source": "curated"}
        # Curated overrides effectively never change — cache for a week.
        await store.set_cache(cache_key, result, 7 * 24 * 3600)
        return result

    with db_session(settings.database_path) as conn:
        airport = db.get_airport(conn, icao_up)
    if not airport:
        result = {"url": SOSA_HOME, "source": "fallback"}
        await store.set_cache(cache_key, result, 24 * 3600)
        return result

    slug = _sosa_slug(airport.name)
    code = (airport.iata or icao_up).lower()
    candidate = f"https://www.saveourskiesalliance.org/{slug}--{code}.html" if slug and code else None
    if candidate:
        try:
            async with httpx.AsyncClient(timeout=5.0, follow_redirects=True) as client:
                response = await client.head(candidate)
                # SOSA serves 200 for real pages and (typically) a soft 404
                # page wrapper for missing slugs — so trust only true 200.
                if response.status_code == 200:
                    result = {"url": candidate, "source": "verified-heuristic"}
                    await store.set_cache(cache_key, result, 24 * 3600)
                    return result
        except httpx.HTTPError:
            pass

    result = {"url": SOSA_HOME, "source": "fallback"}
    # Shorter TTL for negative results so we'll re-probe if SOSA adds the page.
    await store.set_cache(cache_key, result, 6 * 3600)
    return result


@app.get("/aircraft/profile")
async def aircraft_profile(
    settings: Annotated[Settings, Depends(settings_dep)],
    nNumber: str | None = None,
    icaoHex: str | None = None,
    callsign: str | None = None,
):
    """Resolve an aircraft profile from any combination of identifiers."""
    if not any([nNumber, icaoHex, callsign]):
        raise HTTPException(status_code=422, detail="Provide nNumber, icaoHex, or callsign")
    from .registry import profile as registry_profile  # local import keeps startup lean

    with db_session(settings.database_path) as conn:
        return registry_profile.get_aircraft_profile(
            conn,
            n_number=nNumber,
            icao_hex=icaoHex,
            callsign=callsign,
        )


@app.get("/aircraft/search")
async def aircraft_search(
    nNumber: Annotated[str, Query(min_length=2, max_length=12)],
    settings: Annotated[Settings, Depends(settings_dep)],
    limit: int = 10,
):
    from .registry import normalize as registry_norm

    normalized = registry_norm.normalize_n_number(nNumber) or nNumber.upper()
    with db_session(settings.database_path) as conn:
        return {"results": db.search_aircraft_registry(conn, normalized, min(50, max(1, limit)))}


@app.get("/aircraft/{n_number}/airmen-lookup-link")
async def aircraft_airmen_lookup(n_number: str):
    """Return the airmen helper section for a registrant name.

    Strictly a lookup helper: the response repeats the airmen disclaimer and
    never claims pilot identity. UI must surface the disclaimer prominently.
    """
    from urllib.parse import urlencode

    return {
        "lookupAvailable": False,
        "lookupUrl": "https://amsrvs.registry.faa.gov/airmeninquiry/",
        "lookupLabel": "Open FAA Airmen Inquiry",
        "instructions": (
            "Use the FAA Airmen Inquiry page to search by the registrant's "
            "name. Even a successful match does not identify the pilot of "
            "any specific flight."
        ),
        "disclaimer": (
            "Airmen records are name-based and do not prove who was flying "
            "this aircraft during this event."
        ),
        "queryHelper": urlencode({"hint": n_number}),
    }


@app.post("/admin/aircraft-registry/import")
async def admin_import_registry(
    _: Annotated[dict, Depends(require_admin)],
    settings: Annotated[Settings, Depends(settings_dep)],
    wait: bool = False,
):
    """Kick off (or run synchronously) an FAA aircraft registry import."""
    from .registry import importer as registry_importer

    if wait:
        result = await registry_importer.import_faa_registry(settings.database_path)
        return _import_result_to_dict(result)

    asyncio.create_task(registry_importer.import_faa_registry(settings.database_path))
    return {"status": "started", "wait": False}


@app.get("/admin/aircraft-registry/import/status")
async def admin_import_status(
    _: Annotated[dict, Depends(require_admin)],
    settings: Annotated[Settings, Depends(settings_dep)],
):
    with db_session(settings.database_path) as conn:
        latest = db.latest_registry_import(conn)
        row_count = db.aircraft_registry_row_count(conn)
    return {
        "latest_import": latest,
        "registry_row_count": row_count,
    }


def _import_result_to_dict(result) -> dict:
    return {
        "import_id": result.import_id,
        "status": result.status,
        "rows_imported": result.rows_imported,
        "rows_skipped": result.rows_skipped,
        "rows_invalid": result.rows_invalid,
        "duration_seconds": result.duration_seconds,
        "source_url": result.source_url,
        "source_date": result.source_date,
        "error": result.error,
    }


@app.get("/weather/wind")
async def weather_wind(
    airport_icao: Annotated[str, Query(min_length=3, max_length=8)],
    store: Annotated[Store, Depends(store_dep)],
    hours: Annotated[int, Query(ge=1, le=24)] = 1,
):
    """METAR-derived current + window-averaged wind for an airport.

    Source: NOAA AviationWeather (free, key-less). Cached per (airport, hours)
    for 5 minutes — well above the 20-60 min METAR update cadence.
    """
    from . import weather

    return await weather.get_wind_summary(store, airport_icao, hours)


@app.get("/airports/nearest")
async def nearest_airport(
    lat: float,
    lon: float,
    settings: Annotated[Settings, Depends(settings_dep)],
):
    with db_session(settings.database_path) as conn:
        result = db.nearest_airport(conn, lat, lon)
    if not result:
        raise HTTPException(status_code=404, detail="no airport data seeded")
    return result


@app.get("/airports/search")
async def airports_search(
    q: Annotated[str, Query(min_length=1, max_length=100)],
    settings: Annotated[Settings, Depends(settings_dep)],
):
    with db_session(settings.database_path) as conn:
        return {"airports": db.search_airports(conn, q)}


@app.get("/backfill_status")
async def backfill_status(
    airport_icao: Annotated[str, Query(min_length=3, max_length=8)],
    store: Annotated[Store, Depends(store_dep)],
):
    """On-demand FlightAware backfill status for a single airport.

    The frontend polls this while a freshly-selected airport is filling its
    archive so it can show an ETA banner and offer the user a "use the
    1h live window instead" shortcut.
    """
    icao = airport_icao.strip().upper()
    record = await store.get_cache(f"fa_backfill_status:{icao}")
    now = int(time.time())
    if not isinstance(record, dict):
        return {"running": False, "ever_started": False, "airport_icao": icao}
    started_at = int(record.get("started_at") or 0)
    estimated_total = int(record.get("estimated_total_seconds") or 60)
    if record.get("running"):
        elapsed = max(0, now - started_at)
        eta = max(0, estimated_total - elapsed)
        return {
            "running": True,
            "ever_started": True,
            "airport_icao": icao,
            "started_at": started_at,
            "elapsed_seconds": elapsed,
            "estimated_total_seconds": estimated_total,
            "eta_seconds": eta,
            "mode": record.get("mode"),
            "source": record.get("source"),
        }
    return {
        "running": False,
        "ever_started": True,
        "airport_icao": icao,
        "started_at": started_at,
        "completed_at": int(record.get("completed_at") or 0),
        "samples_written": int(record.get("samples_written") or 0),
        "mode": record.get("mode"),
        "source": record.get("source"),
        "error": bool(record.get("error", False)),
    }


@app.get("/scan")
async def scan(
    airport_icao: str,
    user_lat: float,
    user_lon: float,
    store: Annotated[Store, Depends(store_dep)],
    settings: Annotated[Settings, Depends(settings_dep)],
    ring_nm: float = 8.0,
    pass_radius_nm: float = 0.5,
    pass_ceiling_ft: int = 5000,
    window: str = "1h",
):
    try:
        validate_window(window)
        params = ScanParams(
            airport_icao=airport_icao,
            user_lat=user_lat,
            user_lon=user_lon,
            ring_nm=ring_nm,
            pass_radius_nm=pass_radius_nm,
            pass_ceiling_ft=pass_ceiling_ft,
            window=window,
        )
        with db_session(settings.database_path) as conn:
            return await build_scan_response(store, settings, conn, params)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/aircraft/{icao24}/detail")
async def aircraft_detail(
    icao24: str,
    airport_icao: str,
    user_lat: float,
    user_lon: float,
    store: Annotated[Store, Depends(store_dep)],
    settings: Annotated[Settings, Depends(settings_dep)],
    window: str = "1h",
    anger: int = 3,
    niceness: int = 6,
    respect: int = 7,
    detail: int = 6,
    local: int = 3,
    preset: str | None = None,
    include_all_detail: bool = True,
    include_elevation: bool = True,
    include_circles: bool = True,
    include_altitude_over_house: bool = True,
    include_db_at_home: bool = True,
    previous_report_count: int = 0,
    system_prompt: str | None = None,
):
    try:
        validate_window(window)
        sliders = sliders_from_request(preset, anger, niceness, respect, detail, local)
        params = ScanParams(
            airport_icao=airport_icao,
            user_lat=user_lat,
            user_lon=user_lon,
            window=window,
        )
        message_preferences = MessagePreferences(
            include_all_detail=include_all_detail,
            include_elevation=include_elevation,
            include_circles=include_circles,
            include_altitude_over_house=include_altitude_over_house,
            include_db_at_home=include_db_at_home,
        )
        with db_session(settings.database_path) as conn:
            return await build_description(
                store,
                settings,
                conn,
                params,
                icao24,
                sliders,
                message_preferences,
                max(0, previous_report_count),
                system_prompt=system_prompt,
            )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/complaint/summary")
async def complaint_summary(
    payload: SummaryComplaintRequest,
    store: Annotated[Store, Depends(store_dep)],
    settings: Annotated[Settings, Depends(settings_dep)],
):
    try:
        validate_window(payload.window)
        sliders = sliders_from_request(
            None,
            payload.sliders.get("anger", 3),
            payload.sliders.get("niceness", 6),
            payload.sliders.get("respect", 7),
            payload.sliders.get("detail", 6),
            payload.sliders.get("local", 3),
        )
        params = ScanParams(
            airport_icao=payload.airport_icao,
            user_lat=payload.user_lat,
            user_lon=payload.user_lon,
            window=payload.window,
        )
        prefs = MessagePreferences(
            include_all_detail=payload.message_preferences.include_all_detail,
            include_elevation=payload.message_preferences.include_elevation,
            include_circles=payload.message_preferences.include_circles,
            include_altitude_over_house=payload.message_preferences.include_altitude_over_house,
            include_db_at_home=payload.message_preferences.include_db_at_home,
        )
        with db_session(settings.database_path) as conn:
            return await build_summary_description(
                store,
                settings,
                conn,
                params,
                payload.icao24s,
                sliders,
                prefs,
                payload.report_counts,
                system_prompt=payload.system_prompt,
            )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/complaint_form")
async def complaint_form(
    airport_icao: str,
    settings: Annotated[Settings, Depends(settings_dep)],
):
    with db_session(settings.database_path) as conn:
        airport = db.get_airport(conn, airport_icao)
        form = db.complaint_form(conn, airport_icao)
    if not airport:
        raise HTTPException(status_code=404, detail="unknown airport")
    return {
        "airport": db.airport_to_dict(airport),
        "form": form or {
            "icao": airport.icao,
            "form_url": None,
            "phone": None,
            "email": None,
            "notes": "No complaint form is seeded for this airport yet.",
            "last_verified": None,
        },
    }


_STATS_WINDOWS = {"1d": (86400, 3600), "7d": (7 * 86400, 86400), "30d": (30 * 86400, 86400), "all": (None, 86400)}


@app.get("/airports/{icao}/stats")
async def get_airport_stats(
    icao: str,
    settings: Annotated[Settings, Depends(settings_dep)],
    window: Annotated[str, Query(pattern="^(1d|7d|30d|all)$")] = "7d",
):
    now = int(time.time())
    lookback, bucket = _STATS_WINDOWS[window]
    start_ts = 0 if lookback is None else now - lookback
    with db_session(settings.database_path) as conn:
        stats = db.airport_stats(conn, icao, start_ts, now, bucket_seconds=bucket)
    return {
        "airport_icao": icao.upper(),
        "window": {"code": window, "start_ts": start_ts, "end_ts": now, "bucket_seconds": bucket},
        **stats,
    }


@app.get("/airports/{icao}/worst_offenders")
async def get_worst_offenders(
    icao: str,
    settings: Annotated[Settings, Depends(settings_dep)],
    limit: Annotated[int, Query(ge=1, le=10)] = 5,
):
    now = int(time.time())
    try:
        with db_session(settings.database_path) as conn:
            return build_worst_offenders(conn, icao, now=now, limit=limit)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/airports/{icao}/operations-trends")
async def get_airport_operations_trends(
    icao: str,
    settings: Annotated[Settings, Depends(settings_dep)],
    _now: int | None = None,
):
    now = _now if _now is not None else int(time.time())
    with db_session(settings.database_path) as conn:
        if db.get_airport(conn, icao) is None:
            raise HTTPException(status_code=404, detail="airport not found")
        trends = db.airport_operations_trends(conn, icao, now_ts=now, months=12)
    return {"airport_icao": icao.upper(), **trends}


@app.get("/airports/{icao}/vnap-compliance")
async def get_airport_vnap_compliance(
    icao: str,
    settings: Annotated[Settings, Depends(settings_dep)],
    window: Annotated[str, Query(pattern="^(1d|7d|30d|all)$")] = "7d",
    _now: int | None = None,
):
    now = _now if _now is not None else int(time.time())
    lookback, _bucket = _STATS_WINDOWS[window]
    start_ts = 0 if lookback is None else now - lookback
    with db_session(settings.database_path) as conn:
        if db.get_airport(conn, icao) is None:
            raise HTTPException(status_code=404, detail="airport not found")
        compliance = vnap.compute_aircraft_compliance(conn, icao, start_ts, now)
    return {
        "airport_icao": icao.upper(),
        "window": {"code": window, "start_ts": start_ts, "end_ts": now},
        **compliance,
    }


# Radius (nm) of the area we pull historical tracks for — matches the scan ring.
_TRACK_HISTORY_RING_NM = 8.0


@app.get("/airports/{icao}/track-history")
async def get_airport_track_history(
    icao: str,
    settings: Annotated[Settings, Depends(settings_dep)],
    days: Annotated[int, Query(ge=track_history.MIN_DAYS, le=track_history.MAX_DAYS)] = 7,
    ceiling_ft: Annotated[int, Query(ge=0, le=60000)] = track_history.DEFAULT_CEILING_FT_AGL,
    _now: Annotated[int | None, Query()] = None,
):
    """Simplified per-flight polylines for all traffic in the airport ring over
    the last `days` days, below `ceiling_ft` AGL. Feeds the historical
    track-density map overlay."""
    now = int(_now) if _now is not None else int(time.time())
    start_ts = now - days * 86400
    with db_session(settings.database_path) as conn:
        airport = db.get_airport(conn, icao)
        if airport is None:
            raise HTTPException(status_code=404, detail="airport not found")
        min_lat, min_lon, max_lat, max_lon = bbox_for_radius(
            airport.lat, airport.lon, _TRACK_HISTORY_RING_NM
        )
        ceiling_msl = airport.elevation_ft + ceiling_ft
        rows = db.read_track_archive_bbox(
            conn, min_lat, max_lat, min_lon, max_lon, start_ts, now,
            ceiling_ft_msl=ceiling_msl,
        )
    tracks, total_tracks = track_history.build_tracks(rows)
    return {
        "airport": {
            "icao": airport.icao,
            "lat": airport.lat,
            "lon": airport.lon,
            "elevation_ft": airport.elevation_ft,
        },
        "days": days,
        "ceiling_ft": ceiling_ft,
        "window": {"start_ts": start_ts, "end_ts": now},
        "tracks": tracks,
        "total_tracks": total_tracks,
        "truncated": total_tracks > len(tracks),
    }


@app.get("/airports/{icao}/pattern-circuits")
async def get_airport_pattern_circuits(
    icao: str,
    settings: Annotated[Settings, Depends(settings_dep)],
    days: Annotated[int, Query(ge=pattern_circuits.MIN_DAYS, le=pattern_circuits.MAX_DAYS)] = 7,
    _now: Annotated[int | None, Query()] = None,
):
    """Pattern circuits (touch-and-go / low-approach / circle) over the last
    `days` days, each classified by runway (or 'area') and sliced to a short
    window of archived track around the operation. Feeds the Average overlay."""
    now = int(_now) if _now is not None else int(time.time())
    start_ts = now - days * 86400
    window = pattern_circuits.CIRCUIT_WINDOW_S
    with db_session(settings.database_path) as conn:
        airport = db.get_airport(conn, icao)
        if airport is None:
            raise HTTPException(status_code=404, detail="airport not found")
        ops = [dict(row) for row in db.read_operations(
            conn, airport.icao, start_ts, now, types=list(pattern_circuits.CIRCUIT_TYPES)
        )]
        icao24s = sorted({str(o["icao24"]).lower() for o in ops if o.get("icao24")})
        tracks_by_icao = db.bulk_read_track_archive(
            conn, icao24s, start_ts - window, now + window,
        ) if icao24s else {}
    circuits, counts_by_class, total, context_count = pattern_circuits.build_circuits(ops, tracks_by_icao)
    return {
        "airport": {
            "icao": airport.icao,
            "lat": airport.lat,
            "lon": airport.lon,
            "elevation_ft": airport.elevation_ft,
        },
        "days": days,
        "window": {"start_ts": start_ts, "end_ts": now},
        "circuits": circuits,
        "counts_by_class": counts_by_class,
        "context_count": context_count,
        "total_circuits": total,
        "truncated": total > len(circuits),
    }


@app.get("/airports/{icao}/flow")
async def get_airport_flow(
    icao: str,
    settings: Annotated[Settings, Depends(settings_dep)],
):
    with db_session(settings.database_path) as conn:
        active = db.current_flow(conn, icao)
        changes = db.recent_runway_changes(conn, icao, 20)
    return {"airport_icao": icao.upper(), "active": active, "recent_changes": changes}


@app.get("/airports/{icao}/runways")
async def get_airport_runways(
    icao: str,
    settings: Annotated[Settings, Depends(settings_dep)],
):
    with db_session(settings.database_path) as conn:
        runways = db.runways_for_airport(conn, icao)
    return {"airport_icao": icao.upper(), "runways": runways}


@app.get("/airports/{icao}/patterns")
async def get_airport_patterns(
    icao: str,
    settings: Annotated[Settings, Depends(settings_dep)],
):
    with db_session(settings.database_path) as conn:
        patterns_out = [_pattern_response(row) for row in db.current_patterns_for_airport(conn, icao)]
    return {"airport_icao": icao.upper(), "patterns": patterns_out}


@app.get("/runways/{icao}/{runway_id}/pattern")
async def get_runway_pattern(
    icao: str,
    runway_id: str,
    settings: Annotated[Settings, Depends(settings_dep)],
):
    with db_session(settings.database_path) as conn:
        row = db.get_current_pattern(conn, icao, runway_id)
    return {"pattern": _pattern_response(row) if row else None}


@app.get("/runways/{icao}/{runway_id}/pattern/template")
async def get_runway_pattern_template(
    icao: str,
    runway_id: str,
    settings: Annotated[Settings, Depends(settings_dep)],
    side: Annotated[str, Query(pattern="^(left|right)$")] = "left",
):
    with db_session(settings.database_path) as conn:
        runway = db.get_runway(conn, icao, runway_id)
    if runway is None:
        raise HTTPException(status_code=404, detail="runway not found")
    return {"geometry": patterns.generate_template_pattern(runway, side=side)}


def _enforce_pattern_edit_limit(conn, visitor_id: str | None, ip: str | None, now: int) -> None:
    """Guard all pattern writes (save and revert): reject unidentifiable editors
    (no visitor_id and no IP — otherwise the limit silently can't apply) and
    enforce the per-editor rate limit."""
    if not visitor_id and not ip:
        raise HTTPException(status_code=400, detail="cannot identify editor")
    recent = db.count_recent_pattern_edits(conn, visitor_id, ip, now - PATTERN_EDIT_WINDOW_S)
    if recent >= PATTERN_EDIT_MAX_PER_WINDOW:
        raise HTTPException(status_code=429, detail="too many pattern edits; slow down")


def _enforce_crowd_edit_limit(conn, visitor_id: str | None, ip: str | None, now: int) -> None:
    """Guard owner-class overrides and community notes: reject unidentifiable
    editors (no visitor_id and no IP) and enforce the per-editor rate limit."""
    if not visitor_id and not ip:
        raise HTTPException(status_code=400, detail="cannot identify editor")
    recent = db.count_recent_crowd_edits(conn, visitor_id, ip, now - PATTERN_EDIT_WINDOW_S)
    if recent >= PATTERN_EDIT_MAX_PER_WINDOW:
        raise HTTPException(status_code=429, detail="too many edits; slow down")


@app.put("/runways/{icao}/{runway_id}/pattern")
async def save_runway_pattern_endpoint(
    icao: str,
    runway_id: str,
    payload: PatternSaveRequest,
    request: Request,
    settings: Annotated[Settings, Depends(settings_dep)],
):
    now = int(time.time())
    ip = client_ip(request)
    with db_session(settings.database_path) as conn:
        airport = db.get_airport(conn, icao)
        if airport is None:
            raise HTTPException(status_code=404, detail="airport not found")
        if db.get_runway(conn, icao, runway_id) is None:
            raise HTTPException(status_code=404, detail="runway not found")
        current = db.get_current_pattern(conn, icao, runway_id)
        if current and current["locked"]:
            raise HTTPException(status_code=409, detail="pattern is locked")
        points = [{"lat": p.lat, "lon": p.lon} for p in payload.points]
        error = patterns.validate_pattern_geometry(points, airport)
        if error:
            raise HTTPException(status_code=400, detail=error)
        _enforce_pattern_edit_limit(conn, payload.visitor_id, ip, now)
        geometry = {"points": points, "closed": payload.closed, "spline": patterns.PATTERN_SPLINE}
        saved = db.save_runway_pattern(
            conn, icao, runway_id, json.dumps(geometry),
            name=payload.name, editor_visitor_id=payload.visitor_id,
            editor_ip=ip, change_note=payload.change_note,
        )
    return {"pattern": _pattern_response(saved)}


@app.get("/runways/{icao}/{runway_id}/pattern/history")
async def get_runway_pattern_history(
    icao: str,
    runway_id: str,
    settings: Annotated[Settings, Depends(settings_dep)],
):
    with db_session(settings.database_path) as conn:
        versions = db.list_pattern_versions(conn, icao, runway_id)
    return {"versions": [
        {**v, "is_current": bool(v["is_current"]), "locked": bool(v["locked"])}
        for v in versions
    ]}


@app.post("/runways/{icao}/{runway_id}/pattern/revert")
async def revert_runway_pattern_endpoint(
    icao: str,
    runway_id: str,
    payload: PatternRevertRequest,
    request: Request,
    settings: Annotated[Settings, Depends(settings_dep)],
):
    now = int(time.time())
    ip = client_ip(request)
    with db_session(settings.database_path) as conn:
        current = db.get_current_pattern(conn, icao, runway_id)
        if current and current["locked"]:
            raise HTTPException(status_code=409, detail="pattern is locked")
        _enforce_pattern_edit_limit(conn, payload.visitor_id, ip, now)
        saved = db.revert_pattern(
            conn, icao, runway_id, payload.version,
            editor_visitor_id=payload.visitor_id, editor_ip=ip,
        )
        if saved is None:
            raise HTTPException(status_code=404, detail="version not found")
    return {"pattern": _pattern_response(saved)}


@app.put("/aircraft/{icao24}/owner-class")
async def set_aircraft_owner_class(
    icao24: str,
    payload: OwnerClassRequest,
    request: Request,
    settings: Annotated[Settings, Depends(settings_dep)],
):
    if payload.owner_type not in VALID_OWNER_TYPES:
        raise HTTPException(status_code=422, detail="invalid owner_type")
    now = int(time.time())
    ip = client_ip(request)
    with db_session(settings.database_path) as conn:
        current = db.current_owner_override(conn, icao24)
        if current and current["locked"]:
            raise HTTPException(status_code=409, detail="owner class is locked")
        _enforce_crowd_edit_limit(conn, payload.visitor_id, ip, now)
        db.set_owner_override(conn, icao24, payload.owner_type,
                              editor_visitor_id=payload.visitor_id, editor_ip=ip,
                              change_note=payload.change_note)
        resolved = db.resolve_owner_class(conn, icao24)
    return resolved


@app.post("/aircraft/{icao24}/notes")
async def add_aircraft_note(
    icao24: str,
    payload: CommunityNoteRequest,
    request: Request,
    settings: Annotated[Settings, Depends(settings_dep)],
):
    now = int(time.time())
    ip = client_ip(request)
    with db_session(settings.database_path) as conn:
        _enforce_crowd_edit_limit(conn, payload.visitor_id, ip, now)
        note = db.add_community_note(conn, icao24, payload.note, payload.is_flight_school,
                                     editor_visitor_id=payload.visitor_id, editor_ip=ip)
    return {"id": note["id"], "note": note["note"],
            "is_flight_school": bool(note["is_flight_school"]), "created_at": note["created_at"]}


@app.get("/aircraft/{icao24}/notes")
async def get_aircraft_notes(
    icao24: str,
    settings: Annotated[Settings, Depends(settings_dep)],
):
    with db_session(settings.database_path) as conn:
        owner = db.resolve_owner_class(conn, icao24)
        notes = db.list_community_notes(conn, icao24)
    return {"owner": owner,
            "notes": [{"id": n["id"], "note": n["note"],
                       "is_flight_school": bool(n["is_flight_school"]),
                       "created_at": n["created_at"]} for n in notes]}
