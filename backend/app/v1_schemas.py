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


# Which datum each live source's `altitude_ft` carries. live_sources.py maps
# ADS-B alt_baro/alt_geom into the explicit baro_/geo_ fields for these six —
# verified against CIRCLEJERK_LIVE_SOURCE_PRIORITY in .env.example
# (adsbx,self_hosted,adsb_lol,adsb_fi,airplanes_live,opensky) and the
# `known_sources` set in live_sources.py, so these are the exact strings that
# land in a row's `source` column, not approximations.
#
# altitude_ft itself is populated by other paths whose datum is not knowable
# from the source name alone:
#   - flightaware.py's AeroAPI positions carry `source="flightaware_aeroapi"`
#     (NOT the bare "flightaware" string — that spelling only ever appears in
#     an unrelated backfill-job status dict, never in a track sample's own
#     `source` column, so it would never actually match a real row).
#   - adsblol_historical.py's trace parser carries
#     `source="adsblol_historical"` and fills altitude_ft from whichever of
#     baro/geo is present per point, so even the source name can't say which
#     datum a given row used.
# Both are left out of the map on purpose: the `.get(..., "unknown")` default
# already reports them honestly rather than guessing.
_ALTITUDE_DATUM_BY_SOURCE = {
    "adsbx": "barometric",
    "self_hosted": "barometric",
    "adsb_lol": "barometric",
    "adsb_fi": "barometric",
    "airplanes_live": "barometric",
    "opensky": "barometric",
    "flightaware_aeroapi": "unknown",
}


class TrackSampleOut(BaseModel):
    icao24: str
    timestamp_ts: int
    lat: float | None
    lon: float | None
    altitude_ft: float | None
    altitude_datum: str
    baro_altitude_ft: float | None
    geo_altitude_ft: float | None
    heading_deg: float | None
    vertical_rate_fpm: float | None
    callsign: str | None
    emitter_category: str | None
    source: str | None


class TrackPage(BaseModel):
    data: list[TrackSampleOut]
    next_cursor: str | None


def track_sample_out(row) -> TrackSampleOut:
    altitude = row["altitude_ft"]
    datum = "unknown"
    if altitude is not None:
        datum = _ALTITUDE_DATUM_BY_SOURCE.get(row["source"], "unknown")
    return TrackSampleOut(
        icao24=row["icao24"],
        timestamp_ts=row["timestamp"],
        lat=row["lat"],
        lon=row["lon"],
        altitude_ft=altitude,
        altitude_datum=datum,
        baro_altitude_ft=row["baro_altitude_ft"],
        geo_altitude_ft=row["geo_altitude_ft"],
        heading_deg=row["heading_deg"],
        vertical_rate_fpm=row["vertical_rate_fpm"],
        callsign=row["callsign"],
        emitter_category=row["emitter_category"],
        source=row["source"],
    )


class StopBreakdown(BaseModel):
    total: int
    stopped: int
    did_not_stop: int
    # Was `stopped_pct`, holding 28.9 (a real percent) while /operations'
    # fraction_off_pattern held a fraction — same "pct"/"fraction" prefix
    # confusion this whole project exists to remove. Now a fraction.
    #
    # db.py's `_stop_bucket` leaves `stopped_pct` as None when `total` is 0
    # (no aircraft at all in the window — a real, reachable case: a freshly
    # seeded airport or a quiet window has zero unique_aircraft). Nullable
    # here for the same reason Task 3's lat/lon had to be: a non-optional
    # float would 500 the whole page on that airport/window combination.
    fraction_stopped: float | None


class StopClassification(BaseModel):
    all: StopBreakdown
    pattern: StopBreakdown


class OpsBucket(BaseModel):
    bucket: int
    count: int


class StatsWindow(BaseModel):
    code: str
    start_ts: int
    end_ts: int
    bucket_seconds: int


class Counters(BaseModel):
    circles: int
    touch_and_gos: int
    low_approaches: int
    landings: int
    passes: int
    unique_aircraft: int
    runway_changes: int


class RunwayUsage(BaseModel):
    runway_id: str
    total: int
    upwind: int
    crosswind: int
    downwind: int
    no_wind_data: int


class AirportStatsOut(BaseModel):
    airport_icao: str
    window: StatsWindow
    counters: Counters
    ops_over_time: list[OpsBucket]
    runway_usage: list[RunwayUsage]
    stop_classification: StopClassification


def _stop_breakdown(raw: dict) -> StopBreakdown:
    pct = raw["stopped_pct"]
    return StopBreakdown(
        total=raw["total"],
        stopped=raw["stopped"],
        did_not_stop=raw["did_not_stop"],
        # See the None guard note on StopBreakdown.fraction_stopped above:
        # `pct` is None exactly when `total` is 0, and None / 100.0 raises.
        fraction_stopped=round(pct / 100.0, 4) if pct is not None else None,
    )


def airport_stats_out(airport_icao: str, window: dict, stats: dict) -> AirportStatsOut:
    return AirportStatsOut(
        airport_icao=airport_icao,
        window=StatsWindow(**window),
        counters=Counters(**stats["counters"]),
        ops_over_time=[OpsBucket(**b) for b in stats["ops_over_time"]],
        runway_usage=[RunwayUsage(**u) for u in stats.get("runway_usage", [])],
        stop_classification=StopClassification(
            all=_stop_breakdown(stats["stop_classification"]["all"]),
            pattern=_stop_breakdown(stats["stop_classification"]["pattern"]),
        ),
    )


class RunwayOut(BaseModel):
    runway_id: str
    # lat_threshold, lon_threshold, heading_deg, length_ft are all `REAL/
    # INTEGER NOT NULL` on the `runways` table (backend/app/db.py), and the
    # sole insert path (RUNWAY_SEED) populates every one — no optional here,
    # unlike the brief's example. `icao` is deliberately absent: it dupes the
    # envelope's airport_icao.
    lat_threshold: float
    lon_threshold: float
    heading_deg: float
    length_ft: int


class RunwaysOut(BaseModel):
    airport_icao: str
    runways: list[RunwayOut]


def runways_out(airport_icao: str, rows) -> RunwaysOut:
    return RunwaysOut(
        airport_icao=airport_icao,
        runways=[
            RunwayOut(
                runway_id=r["runway_id"],
                lat_threshold=r["lat_threshold"],
                lon_threshold=r["lon_threshold"],
                heading_deg=r["heading_deg"],
                length_ft=r["length_ft"],
            )
            for r in rows
        ],
    )
