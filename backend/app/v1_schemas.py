"""The public /v1 contract: one model per response, plus the builders that map
an internal row onto it.

Deliberately free of FastAPI routing, sqlite3, and all I/O. public_api.py owns
auth, scoping, and routing; this module owns the shape. Keeping them apart means
the entire published contract is readable in one sitting, and that a database
column added tomorrow cannot reach a partner — the model filters it structurally
rather than relying on a test somebody remembers to update.

Renames live HERE, at the serialization boundary. Internal dict keys, database
columns, and the frontend keep their existing names.
"""

from __future__ import annotations

from pydantic import BaseModel

from . import api_keys
from .deviation import CORRIDOR_NM

# Published in /v1/meta so a consumer can interpret fraction_off_pattern.
PATTERN_CORRIDOR_NM = CORRIDOR_NM
VNAP_SCORE_SCALE = "0..100"
AXIS_SCORE_SCALE = "0..100"


class Constants(BaseModel):
    pattern_corridor_nm: float
    vnap_score_scale: str
    axis_score_scale: str


class Limits(BaseModel):
    requests_per_minute: int
    max_page_size: int
    max_track_span_seconds: int


class MetaOut(BaseModel):
    version: str
    name: str
    scopes: list[str]
    airports: list[str] | None
    limits: Limits
    constants: Constants


def meta_out(ctx: api_keys.ApiKeyContext) -> MetaOut:
    from .public_api import API_VERSION, MAX_PAGE_SIZE, MAX_TRACK_SPAN_SECONDS, RATE_LIMIT_PER_MINUTE

    return MetaOut(
        version=API_VERSION,
        name=ctx.name,
        scopes=sorted(ctx.scopes),
        airports=sorted(ctx.airports) if ctx.airports is not None else None,
        limits=Limits(
            requests_per_minute=RATE_LIMIT_PER_MINUTE,
            max_page_size=MAX_PAGE_SIZE,
            max_track_span_seconds=MAX_TRACK_SPAN_SECONDS,
        ),
        constants=Constants(
            pattern_corridor_nm=PATTERN_CORRIDOR_NM,
            vnap_score_scale=VNAP_SCORE_SCALE,
            axis_score_scale=AXIS_SCORE_SCALE,
        ),
    )


class OperationOut(BaseModel):
    id: str
    airport_icao: str
    icao24: str
    callsign: str | None
    registration: str | None
    type: str
    timestamp_ts: int
    runway_id: str | None
    turn_direction: str | None
    min_altitude_ft_agl: int | None
    emitter_category: str | None
    deviation_mean_nm: float | None
    deviation_peak_nm: float | None
    # Was `pct_off_pattern`, which held a fraction. The name contradicted the
    # value, and /stats' `stopped_pct` held a real percent — same prefix, two
    # scales, one API.
    fraction_off_pattern: float | None
    time_off_pattern_s: int | None
    time_total_s: int | None
    wind_from_deg: int | None
    wind_speed_kt: float | None
    origin_airport_icao: str | None
    origin_label: str | None
    operator: str | None
    flight_school: str | None


class OperationPage(BaseModel):
    data: list[OperationOut]
    next_cursor: str | None


def operation_out(row) -> OperationOut:
    return OperationOut(
        id=row["id"],
        airport_icao=row["airport_icao"],
        icao24=row["icao24"],
        callsign=row["callsign"],
        registration=row["registration"],
        type=row["type"],
        timestamp_ts=row["timestamp"],
        runway_id=row["runway_id"],
        turn_direction=row["turn_direction"],
        min_altitude_ft_agl=row["min_altitude_ft_agl"],
        emitter_category=row["emitter_category"],
        deviation_mean_nm=row["deviation_mean_nm"],
        deviation_peak_nm=row["deviation_peak_nm"],
        fraction_off_pattern=row["pct_off_pattern"],
        time_off_pattern_s=row["time_off_pattern_s"],
        time_total_s=row["time_total_s"],
        wind_from_deg=row["wind_from_deg"],
        wind_speed_kt=row["wind_speed_kt"],
        origin_airport_icao=row["origin_airport_icao"],
        origin_label=row["origin_label"],
        operator=row["operator"],
        flight_school=row["flight_school"],
    )
