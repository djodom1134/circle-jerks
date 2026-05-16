from __future__ import annotations

import base64
import hashlib
import hmac
import json
import math
import secrets
import time
from contextlib import asynccontextmanager
from typing import Annotated

import httpx
from fastapi import Cookie, Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from . import db
from .db import db_session
from .detectors import pass_geometry_key
from .domain import ScanParams, monitor_hash
from .llm import MessagePreferences
from .services import build_description, build_scan_response, build_summary_description
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


class SummaryComplaintRequest(BaseModel):
    airport_icao: str
    user_lat: float
    user_lon: float
    window: str = "1h"
    icao24s: list[str] = Field(min_length=1, max_length=40)
    sliders: dict[str, int] = Field(default_factory=dict)
    message_preferences: MessagePreferencesRequest = Field(default_factory=MessagePreferencesRequest)
    report_counts: dict[str, int] = Field(default_factory=dict)


class ActivityAircraft(BaseModel):
    icao24: str = Field(min_length=1, max_length=16)
    callsign: str | None = Field(default=None, max_length=32)


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


class AdminLoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=80)
    password: str = Field(min_length=1, max_length=400)


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
    previous_report_count: int = 0,
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
