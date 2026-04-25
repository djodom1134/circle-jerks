from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Annotated

import httpx
from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from . import db
from .db import db_session
from .domain import ScanParams
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


def settings_dep() -> Settings:
    return app.state.settings


def store_dep() -> Store:
    return app.state.store


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


@app.get("/config")
async def config(settings: Annotated[Settings, Depends(settings_dep)]):
    return {
        "default_airport_icao": settings.default_airport_icao,
        "presets": PRESETS,
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
