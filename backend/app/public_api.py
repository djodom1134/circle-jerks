"""The public, key-authenticated /v1 API.

Deliberately separate from the internal routes in main.py: those are shaped for
the frontend and change with it, while everything here is a contract partners
depend on. This module never imports main.py — it reads settings and store off
`request.app.state` so the dependency direction stays one-way.
"""

from __future__ import annotations

import base64
import binascii
import json
import time
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Annotated

import httpx
from fastapi import APIRouter, Depends, Request, Response
from fastapi.exception_handlers import (
    http_exception_handler as default_http_exception_handler,
    request_validation_exception_handler as default_validation_exception_handler,
)
from fastapi.exceptions import RequestValidationError
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.utils import is_body_allowed_for_status_code
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import api_keys, db, vnap
from .api_keys import ApiKeyContext
from .db import db_session
from .geo import bbox_for_radius
from .services import build_worst_offenders
from .settings import Settings
from .store import Store

API_VERSION = "v1"

DEFAULT_PAGE_SIZE = 500
MAX_PAGE_SIZE = 5000
MAX_TRACK_SPAN_SECONDS = 31 * 86400
RATE_LIMIT_PER_MINUTE = 120
RATE_LIMIT_WINDOW_SECONDS = 60
# Without this, a hot client turns every read into a SQLite write.
TOUCH_THROTTLE_SECONDS = 60

router = APIRouter(prefix=f"/{API_VERSION}", tags=["public"])


class ApiError(Exception):
    """Anything raised here renders as the /v1 error envelope."""

    def __init__(self, status_code: int, code: str, message: str,
                 headers: dict[str, str] | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.headers = headers or {}


_RATE_LIMIT_STATE = "public_api_rate_limit_headers"


def rate_limit_headers(request: Request) -> dict[str, str]:
    """The counter values `resolve_key` recorded for this request, if any.

    FastAPI merges the injected `Response` headers only when a route returns
    normally, so every error path would otherwise drop the rate-limit counter —
    exactly when a partner backing off needs it most. `resolve_key` stashes the
    values on `request.state`; the exception handlers read them back here.

    Empty before the key is resolved (a 401 for a missing or bad key has no
    counter to report, and must not invent one).
    """
    return getattr(request.state, _RATE_LIMIT_STATE, None) or {}


def _with_rate_limit(request: Request,
                     headers: dict[str, str] | None) -> dict[str, str] | None:
    """Merge in the rate-limit counter without overriding explicit headers."""
    merged = {**rate_limit_headers(request), **(headers or {})}
    return merged or None


async def api_error_handler(request: Request, exc: ApiError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": {"code": exc.code, "message": exc.message}},
        headers=_with_rate_limit(request, exc.headers),
    )


def is_public_path(request: Request) -> bool:
    """The envelope is total over /v1 and applies nowhere else.

    The trailing slash matters: a bare `startswith("/v1")` would also claim a
    future internal `/v1beta/...` path and hand it the partner envelope. `/v1`
    itself is still ours, so it is matched explicitly.
    """
    path = request.url.path
    return path == f"/{API_VERSION}" or path.startswith(f"/{API_VERSION}/")


# Codes partners can branch on. Anything unmapped degrades to a generic code
# rather than leaking a framework-shaped body.
_STATUS_CODES = {
    400: "invalid_request",
    401: "unauthorized",
    403: "forbidden",
    404: "not_found",
    405: "method_not_allowed",
    422: "invalid_request",
    429: "rate_limited",
}


def _envelope(status_code: int, code: str, message: str,
              headers: dict[str, str] | None = None) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"error": {"code": code, "message": message}},
        headers=headers,
    )


async def validation_error_handler(
    request: Request, exc: RequestValidationError,
) -> JSONResponse:
    """422s from query/path/body validation, enveloped for /v1 only."""
    if not is_public_path(request):
        return await default_validation_exception_handler(request, exc)
    parts = []
    for err in exc.errors():
        location = ".".join(str(item) for item in err.get("loc", ()) if item != "query")
        parts.append(f"{location}: {err.get('msg', 'invalid')}" if location
                     else str(err.get("msg", "invalid")))
    message = "; ".join(parts) or "request is not valid"
    # 400, not FastAPI's 422: the spec's error table lists `invalid_request` at
    # 400 only, and every explicit ApiError(400, "invalid_request", ...) in this
    # module already answers there. One code at two statuses would break any
    # partner branching on the pair.
    return _envelope(400, "invalid_request", message,
                     headers=_with_rate_limit(request, None))


def _detail_message(detail: object, code: str) -> str:
    """Reduce an HTTPException detail to the envelope's `message` string.

    FastAPI lets `detail` be any JSON value, so `str()` on a dict or list would
    render a Python repr ("{'field': 'x'}") into a partner-facing string.
    Anything that is not already a non-empty string degrades to the generic
    text for the code instead.
    """
    if isinstance(detail, str) and detail:
        return detail
    return code.replace("_", " ")


async def http_exception_handler(
    request: Request, exc: StarletteHTTPException,
) -> Response:
    """404s on unknown /v1 paths, 405s, and any raised HTTPException."""
    if not is_public_path(request):
        return await default_http_exception_handler(request, exc)
    headers = getattr(exc, "headers", None)
    # 204/304 and friends forbid a body; the default handler checks this and so
    # must we, or the envelope would corrupt the response.
    if not is_body_allowed_for_status_code(exc.status_code):
        return Response(status_code=exc.status_code, headers=headers)
    code = _STATUS_CODES.get(exc.status_code, "http_error")
    return _envelope(
        exc.status_code,
        code,
        _detail_message(exc.detail, code),
        headers=_with_rate_limit(request, headers),
    )


# ─── Local dependencies (no main.py import) ──────────────────────────────────

def settings_from_app(request: Request) -> Settings:
    return request.app.state.settings


def store_from_app(request: Request) -> Store:
    return request.app.state.store


# ─── Authentication ──────────────────────────────────────────────────────────

_UNAUTHORIZED = "invalid or missing API key"


def _presented_key(request: Request) -> str | None:
    header = request.headers.get("authorization")
    if header and header.lower().startswith("bearer "):
        return header[7:].strip()
    return request.headers.get("x-api-key")


async def resolve_key(
    request: Request,
    response: Response,
    settings: Annotated[Settings, Depends(settings_from_app)],
    store: Annotated[Store, Depends(store_from_app)],
) -> ApiKeyContext:
    raw = _presented_key(request)
    parsed = api_keys.parse_key(raw) if raw else None
    if not parsed:
        raise ApiError(401, "unauthorized", _UNAUTHORIZED)
    key_id, secret = parsed

    with db_session(settings.database_path) as conn:
        row = db.get_api_key(conn, key_id)

    # An unknown id and a wrong secret must be indistinguishable to the caller.
    if not row or row["revoked_at"] is not None:
        raise ApiError(401, "unauthorized", _UNAUTHORIZED)
    if not api_keys.verify_secret(secret, row["secret_hash"]):
        raise ApiError(401, "unauthorized", _UNAUTHORIZED)

    now = int(time.time())
    window = now // RATE_LIMIT_WINDOW_SECONDS
    used = await store.incr_counter(f"apikey:{key_id}:{window}", RATE_LIMIT_WINDOW_SECONDS)
    remaining = max(RATE_LIMIT_PER_MINUTE - used, 0)
    response.headers["X-RateLimit-Limit"] = str(RATE_LIMIT_PER_MINUTE)
    response.headers["X-RateLimit-Remaining"] = str(remaining)
    # The injected Response only reaches the wire when the route returns
    # normally. Stash the same values on the request so the error handlers can
    # put them on 400/403/404/503 too — see `rate_limit_headers`.
    setattr(request.state, _RATE_LIMIT_STATE, {
        "X-RateLimit-Limit": str(RATE_LIMIT_PER_MINUTE),
        "X-RateLimit-Remaining": str(remaining),
    })
    if used > RATE_LIMIT_PER_MINUTE:
        raise ApiError(
            429,
            "rate_limited",
            f"rate limit of {RATE_LIMIT_PER_MINUTE} requests per minute exceeded",
            headers={
                "Retry-After": str(RATE_LIMIT_WINDOW_SECONDS),
                "X-RateLimit-Limit": str(RATE_LIMIT_PER_MINUTE),
                "X-RateLimit-Remaining": "0",
            },
        )

    if await store.get_cache(f"apikey:touched:{key_id}") is None:
        with db_session(settings.database_path) as conn:
            db.touch_api_key(conn, key_id, now)
        await store.set_cache(f"apikey:touched:{key_id}", now, TOUCH_THROTTLE_SECONDS)

    return ApiKeyContext(
        key_id=key_id,
        name=row["name"],
        scopes=api_keys.parse_scopes(row["scopes"]),
        airports=api_keys.parse_airports(row["airports"]),
    )


def require_scope(scope: str):
    """Dependency factory: resolves the key, then enforces one scope."""

    async def dependency(
        ctx: Annotated[ApiKeyContext, Depends(resolve_key)],
    ) -> ApiKeyContext:
        if not ctx.has_scope(scope):
            raise ApiError(403, "forbidden_scope", f"key lacks scope {scope}")
        return ctx

    return dependency


def require_airport(ctx: ApiKeyContext, icao: str) -> str:
    """Enforce the key's airport restriction. Returns the normalized ICAO."""
    normalized = icao.upper()
    if not ctx.allows_airport(normalized):
        raise ApiError(403, "forbidden_airport", f"key is not scoped to {normalized}")
    return normalized


# ─── Request helpers ─────────────────────────────────────────────────────────

def page_limit(value: int | None) -> int:
    """Total over None so an omitted limit lands on the documented default."""
    if value is None:
        return DEFAULT_PAGE_SIZE
    return max(1, min(int(value), MAX_PAGE_SIZE))


# A Unix second count fits in 10 digits until the year 2286; 11 leaves room and
# still keeps compact ISO datetimes ("20260720120000", 14 digits) out of the
# integer branch.
_MAX_TIMESTAMP_DIGITS = 11


def _is_plain_integer(value: str) -> bool:
    """Only ASCII digits with an optional leading sign, and not too many.

    `int()` is far more permissive than the wire format: it accepts underscore
    separators ("1_000"), surrounding whitespace, and non-ASCII digits, all of
    which would silently become a timestamp nobody typed.
    """
    body = value[1:] if value.startswith("-") else value
    return (
        bool(body)
        and body.isascii()
        and body.isdigit()
        and len(body) <= _MAX_TIMESTAMP_DIGITS
    )


def parse_time(value: str | None, field: str) -> int | None:
    """Accept a Unix timestamp or an ISO 8601 datetime; return Unix seconds.

    ISO is tried first: ISO 8601 basic format ("20260720") is all digits, so
    reading integers first would silently turn a 2026 date into a 1970 one.
    """
    if value is None or value == "":
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        if _is_plain_integer(value):
            return int(value)
        raise ApiError(
            400, "invalid_request",
            f"{field} must be a Unix timestamp or an ISO 8601 datetime",
        ) from None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return int(parsed.timestamp())


def encode_cursor(timestamp: int, row_id: str) -> str:
    raw = f"{int(timestamp)}:{row_id}".encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def decode_cursor(raw: str) -> tuple[int, str]:
    try:
        padded = raw + "=" * (-len(raw) % 4)
        timestamp, row_id = base64.urlsafe_b64decode(padded).decode("utf-8").split(":", 1)
        return int(timestamp), row_id
    except (ValueError, binascii.Error, UnicodeDecodeError) as exc:
        raise ApiError(400, "invalid_request", "cursor is not valid") from exc


def paged(rows: list[dict], limit: int, cursor_of) -> dict:
    """Wrap a page of rows, emitting next_cursor only when the page was full."""
    next_cursor = cursor_of(rows[-1]) if len(rows) == limit and rows else None
    return {"data": rows, "next_cursor": next_cursor}


# ─── Routes ──────────────────────────────────────────────────────────────────

@router.get("/meta", summary="Describe the calling key")
async def meta(ctx: Annotated[ApiKeyContext, Depends(resolve_key)]) -> dict:
    """Echoes this key's own scopes and airport restriction, so a 403 can be
    diagnosed without contacting us."""
    return {
        "version": API_VERSION,
        "name": ctx.name,
        "scopes": sorted(ctx.scopes),
        "airports": sorted(ctx.airports) if ctx.airports is not None else None,
        "limits": {
            "requests_per_minute": RATE_LIMIT_PER_MINUTE,
            "max_page_size": MAX_PAGE_SIZE,
            "max_track_span_seconds": MAX_TRACK_SPAN_SECONDS,
        },
    }


_OPENAPI_DOCUMENT_PATH = Path(__file__).resolve().parent / "generated" / "openapi.json"


@lru_cache(maxsize=1)
def _openapi_document() -> dict:
    """The hand-written partner contract, not FastAPI's generated schema.

    The generated schema omits the error envelope and the auth and pagination
    rules, and it still advertised 422 after validation moved to 400. The
    document served here is generated from docs/api/openapi.yaml by
    scripts/build_openapi_json.py and committed at
    backend/app/generated/openapi.json; scripts/verify_openapi_doc.py fails
    the build if the committed copy drifts from the YAML.
    """
    return json.loads(_OPENAPI_DOCUMENT_PATH.read_text())


@router.get("/openapi.json", include_in_schema=False)
async def public_openapi() -> dict:
    return _openapi_document()


@router.get("/docs", include_in_schema=False)
async def public_docs() -> HTMLResponse:
    # Relative URL on purpose: Caddy and Vite both strip the /api prefix, so
    # the browser sees /api/v1/docs while FastAPI sees /v1/docs. Resolving
    # "openapi.json" against the page URL is correct in both.
    return get_swagger_ui_html(
        openapi_url="openapi.json",
        title="Circle Jerks Public API",
    )


# Default lookback when the caller supplies no range.
DEFAULT_LOOKBACK_SECONDS = 7 * 86400

# Frozen on purpose: new columns on `operations` must not silently appear in
# the public contract.
_OPERATION_FIELDS = (
    "id", "icao24", "callsign", "registration", "type", "timestamp",
    "runway_id", "turn_direction", "min_altitude_ft_agl", "emitter_category",
    "deviation_mean_nm", "deviation_peak_nm", "pct_off_pattern",
    "wind_from_deg", "wind_speed_kt", "origin_airport_icao", "origin_label",
    "operator", "flight_school",
)


def operation_row(row) -> dict:
    out = {field: row[field] for field in _OPERATION_FIELDS}
    out["airport_icao"] = row["icao"]
    return out


def resolve_range(
    since: str | None, until: str | None, *, now: int, max_span: int | None = None
) -> tuple[int, int]:
    # `is None`, not truthiness: `until=0` (and 1970-01-01T00:00:00Z) parses to
    # a legitimate 0 that `or now` would silently rewrite to the current time.
    end_ts = parse_time(until, "until")
    if end_ts is None:
        end_ts = now
    start_ts = parse_time(since, "since")
    if start_ts is None:
        start_ts = end_ts - DEFAULT_LOOKBACK_SECONDS
    if start_ts > end_ts:
        raise ApiError(400, "invalid_request", "since must be before until")
    if max_span is not None and end_ts - start_ts > max_span:
        raise ApiError(
            400, "invalid_request",
            f"requested range exceeds the {max_span} second maximum; page with the cursor instead",
        )
    return start_ts, end_ts


@router.get("/operations", summary="Classified operations for an airport")
async def list_operations(
    ctx: Annotated[ApiKeyContext, Depends(require_scope("ops:read"))],
    settings: Annotated[Settings, Depends(settings_from_app)],
    airport: str,
    since: str | None = None,
    until: str | None = None,
    type: str | None = None,
    icao24: str | None = None,
    runway: str | None = None,
    cursor: str | None = None,
    limit: int = DEFAULT_PAGE_SIZE,
) -> dict:
    with db_session(settings.database_path) as conn:
        # `_known_airport`, the same gate the aggregates and the ledger proxy
        # use: the key's restriction is enforced BEFORE existence, so an
        # out-of-scope airport is always a 403 and never leaks whether it
        # exists. Before this, an unknown ICAO here answered 200 with an empty
        # page, which a partner could not tell from "no data".
        icao = _known_airport(conn, ctx, airport)
        start_ts, end_ts = resolve_range(since, until, now=int(time.time()))
        size = page_limit(limit)
        after = decode_cursor(cursor) if cursor else None

        rows = db.read_operations_page(
            conn,
            icao=icao,
            start_ts=start_ts,
            end_ts=end_ts,
            types=[type] if type else None,
            icao24=icao24,
            runway_id=runway,
            after=after,
            limit=size,
        )

    return paged(
        [operation_row(row) for row in rows],
        size,
        lambda row: encode_cursor(row["timestamp"], row["id"]),
    )


# Matches the scan ring used by the historical track-density view.
TRACK_RING_NM = 8.0

_TRACK_FIELDS = (
    "icao24", "timestamp", "lat", "lon", "altitude_ft", "baro_altitude_ft",
    "geo_altitude_ft", "heading_deg", "vertical_rate_fpm", "callsign",
    "emitter_category", "source",
)


def track_row(row) -> dict:
    return {field: row[field] for field in _TRACK_FIELDS}


@router.get("/tracks", summary="Raw ADS-B position samples")
async def list_tracks(
    ctx: Annotated[ApiKeyContext, Depends(require_scope("tracks:read"))],
    settings: Annotated[Settings, Depends(settings_from_app)],
    since: str | None = None,
    until: str | None = None,
    icao24: str | None = None,
    airport: str | None = None,
    cursor: str | None = None,
    limit: int = DEFAULT_PAGE_SIZE,
) -> dict:
    if not icao24 and not airport:
        raise ApiError(400, "invalid_request", "one of icao24 or airport is required")
    # A key restricted to specific airports must not be able to reach raw
    # position samples for an arbitrary aircraft by omitting `airport` and
    # supplying only `icao24` — that would bypass the restriction entirely,
    # since icao24-only queries carry no airport to check `require_airport`
    # against. Force the airport parameter (and therefore the bbox filter
    # below) whenever the key is scoped. This must run before the "both since
    # and until are required" check: a restricted key with neither icao24 nor
    # airport already fails above with the generic 400, but a restricted key
    # that supplies icao24 alone (with or without a range) should be told
    # about the airport requirement rather than getting a range error that
    # would just repeat once "fixed".
    if ctx.airports is not None and not airport:
        raise ApiError(
            403,
            "forbidden_airport",
            "key is restricted to specific airports; /v1/tracks requires the "
            "airport parameter for restricted keys",
        )
    # Declared optional so a missing range raises the /v1 envelope rather than
    # FastAPI's 422 {"detail": ...}. Raw tracks are never served unbounded.
    if not since or not until:
        raise ApiError(400, "invalid_request", "both since and until are required")
    start_ts, end_ts = resolve_range(
        since, until, now=int(time.time()), max_span=MAX_TRACK_SPAN_SECONDS
    )
    size = page_limit(limit)
    after = decode_cursor(cursor) if cursor else None

    bbox = None
    with db_session(settings.database_path) as conn:
        if airport:
            icao = require_airport(ctx, airport)
            found = db.get_airport(conn, icao)
            if found is None:
                raise ApiError(404, "not_found", f"unknown airport {icao}")
            min_lat, min_lon, max_lat, max_lon = bbox_for_radius(
                found.lat, found.lon, TRACK_RING_NM
            )
            bbox = (min_lat, min_lon, max_lat, max_lon)

        rows = db.read_track_archive_page(
            conn,
            start_ts=start_ts,
            end_ts=end_ts,
            icao24=icao24,
            bbox=bbox,
            after=after,
            limit=size,
        )

    return paged(
        [track_row(row) for row in rows],
        size,
        lambda row: encode_cursor(row["timestamp"], row["icao24"]),
    )


# ─── Aggregates ──────────────────────────────────────────────────────────────
#
# These mirror main.py's internal aggregate routes rather than importing their
# helpers. main.py is shaped for the frontend and free to change with it; the
# /v1 contract must not shift underneath partners when it does. Keeping a
# second copy of `_STATS_WINDOWS` (as `_WINDOWS`) and of `_pattern_response`
# (as `_pattern_out`) is the deliberate cost of that decoupling — main.py
# imports this module, never the reverse, so importing from main.py here would
# also create a circular import.

# Mirrors main.py's _STATS_WINDOWS so the public windows match the site's.
_WINDOWS = {"1d": (86400, 3600), "7d": (7 * 86400, 86400),
            "30d": (30 * 86400, 86400), "all": (None, 86400)}


def _window(code: str, now: int) -> tuple[int, int, int]:
    """Return (start_ts, end_ts, bucket_seconds) for a window code."""
    if code not in _WINDOWS:
        raise ApiError(
            400, "invalid_request",
            f"window must be one of {', '.join(_WINDOWS)}",
        )
    lookback, bucket = _WINDOWS[code]
    return (0 if lookback is None else now - lookback), now, bucket


def _known_airport(conn, ctx: ApiKeyContext, icao: str) -> str:
    """Enforce the key's restriction, then confirm the airport exists."""
    normalized = require_airport(ctx, icao)
    if db.get_airport(conn, normalized) is None:
        raise ApiError(404, "not_found", f"unknown airport {normalized}")
    return normalized


def _pattern_out(row) -> dict:
    return {
        "id": row["id"],
        "airport_icao": row["icao"],
        "runway_id": row["runway_id"],
        "version": row["version"],
        "name": row["name"],
        "locked": bool(row["locked"]),
        "geometry": json.loads(row["geometry_json"]),
        "created_at": row["created_at"],
    }


@router.get("/airports/{icao}/stats", summary="Operation counts and buckets")
async def airport_stats(
    icao: str,
    ctx: Annotated[ApiKeyContext, Depends(require_scope("aggregates:read"))],
    settings: Annotated[Settings, Depends(settings_from_app)],
    window: str = "7d",
) -> dict:
    now = int(time.time())
    start_ts, end_ts, bucket = _window(window, now)
    with db_session(settings.database_path) as conn:
        normalized = _known_airport(conn, ctx, icao)
        stats = db.airport_stats(conn, normalized, start_ts, end_ts, bucket_seconds=bucket)
    return {
        "airport_icao": normalized,
        "window": {"code": window, "start_ts": start_ts, "end_ts": end_ts,
                   "bucket_seconds": bucket},
        **stats,
    }


def _suppress_foreign_fallback(conn, ctx: ApiKeyContext, requested: str,
                               offenders: dict) -> dict:
    """Strip a worst-offenders fallback the calling key is not allowed to see.

    `services.build_worst_offenders` is shared with the internal frontend,
    where substituting the nearest neighbouring airport when the requested one
    has no scored aircraft is the desired UX. On /v1 it is an authorization
    hole: `_known_airport` gated on the airport the partner ASKED for, but the
    rows returned would be another airport's — icao24, registration, owner
    class and all — under an `airport_icao` that still says the requested one.

    `allows_airport` is True for unrestricted keys, so they keep the fallback
    exactly as before; only a key scoped away from the substitute loses it, and
    then it gets the honest answer: no offenders here, no fallback.
    """
    if not offenders.get("is_fallback"):
        return offenders
    if ctx.allows_airport(str(offenders.get("resolved_icao") or "")):
        return offenders
    airport = db.get_airport(conn, requested)
    label = (airport.city or airport.name) if airport else None
    return {
        **offenders,
        "resolved_icao": requested,
        "resolved_label": label or requested,
        "is_fallback": False,
        "offenders": [],
    }


@router.get("/airports/{icao}/worst-offenders", summary="Most-reported aircraft")
async def airport_worst_offenders(
    icao: str,
    ctx: Annotated[ApiKeyContext, Depends(require_scope("aggregates:read"))],
    settings: Annotated[Settings, Depends(settings_from_app)],
    limit: int = 5,
) -> dict:
    now = int(time.time())
    with db_session(settings.database_path) as conn:
        normalized = _known_airport(conn, ctx, icao)
        offenders = build_worst_offenders(
            conn, normalized, now=now, limit=max(1, min(int(limit), 10))
        )
        offenders = _suppress_foreign_fallback(conn, ctx, normalized, offenders)
    # airport_icao last: build_worst_offenders returns a whole response body in
    # main.py, so it may already carry the key. Ours is the normalized one.
    return {**offenders, "airport_icao": normalized}


@router.get("/airports/{icao}/operations-trends", summary="Twelve-month trends")
async def airport_operations_trends(
    icao: str,
    ctx: Annotated[ApiKeyContext, Depends(require_scope("aggregates:read"))],
    settings: Annotated[Settings, Depends(settings_from_app)],
) -> dict:
    now = int(time.time())
    with db_session(settings.database_path) as conn:
        normalized = _known_airport(conn, ctx, icao)
        trends = db.airport_operations_trends(conn, normalized, now_ts=now, months=12)
    return {"airport_icao": normalized, **trends}


@router.get("/airports/{icao}/vnap-compliance", summary="Per-aircraft VNAP compliance")
async def airport_vnap_compliance(
    icao: str,
    ctx: Annotated[ApiKeyContext, Depends(require_scope("aggregates:read"))],
    settings: Annotated[Settings, Depends(settings_from_app)],
    window: str = "7d",
) -> dict:
    now = int(time.time())
    start_ts, end_ts, _bucket = _window(window, now)
    with db_session(settings.database_path) as conn:
        normalized = _known_airport(conn, ctx, icao)
        compliance = vnap.compute_aircraft_compliance(conn, normalized, start_ts, end_ts)
    return {
        "airport_icao": normalized,
        "window": {"code": window, "start_ts": start_ts, "end_ts": end_ts},
        **compliance,
    }


@router.get("/airports/{icao}/runways", summary="Runway geometry")
async def airport_runways(
    icao: str,
    ctx: Annotated[ApiKeyContext, Depends(require_scope("aggregates:read"))],
    settings: Annotated[Settings, Depends(settings_from_app)],
) -> dict:
    with db_session(settings.database_path) as conn:
        normalized = _known_airport(conn, ctx, icao)
        runways = db.runways_for_airport(conn, normalized)
    return {"airport_icao": normalized, "runways": runways}


@router.get("/airports/{icao}/patterns", summary="Current traffic patterns")
async def airport_patterns(
    icao: str,
    ctx: Annotated[ApiKeyContext, Depends(require_scope("aggregates:read"))],
    settings: Annotated[Settings, Depends(settings_from_app)],
) -> dict:
    with db_session(settings.database_path) as conn:
        normalized = _known_airport(conn, ctx, icao)
        rows = db.current_patterns_for_airport(conn, normalized)
    return {"airport_icao": normalized, "patterns": [_pattern_out(row) for row in rows]}


@router.get("/airports/{icao}/flow", summary="Active runway and recent changes")
async def airport_flow(
    icao: str,
    ctx: Annotated[ApiKeyContext, Depends(require_scope("aggregates:read"))],
    settings: Annotated[Settings, Depends(settings_from_app)],
) -> dict:
    with db_session(settings.database_path) as conn:
        normalized = _known_airport(conn, ctx, icao)
        active = db.current_flow(conn, normalized)
        changes = db.recent_runway_changes(conn, normalized, 20)
    return {"airport_icao": normalized, "active": active, "recent_changes": changes}


# ─── Ledger proxy ────────────────────────────────────────────────────────────
#
# The only /v1 route that crosses a service boundary. ledger-api opens the
# main circlejerk.sqlite3 READ-ONLY and writes its own derived tables to a
# separate ledger.sqlite3 — that split keeps two writers off one SQLite write
# lock. Reading ledger.sqlite3 directly from here would recreate exactly the
# contention that split was made to avoid, so this proxies over HTTP instead.

# Public suffix -> upstream path on ledger-api. An explicit allowlist, not a
# passthrough: this must never become an open proxy into the sidecar.
LEDGER_RESOURCES = {
    "ledger": "/airports/{icao}/ledger",
    "aircraft-fees": "/airports/{icao}/aircraft-fees",
}


@router.get("/ledger/airports/{icao}/{resource}", summary="KLMO fees ledger")
async def ledger_proxy(
    icao: str,
    resource: str,
    request: Request,
    ctx: Annotated[ApiKeyContext, Depends(require_scope("ledger:read"))],
    settings: Annotated[Settings, Depends(settings_from_app)],
) -> JSONResponse:
    template = LEDGER_RESOURCES.get(resource)
    if template is None:
        raise ApiError(
            404, "not_found",
            f"unknown ledger resource {resource}; expected one of {', '.join(LEDGER_RESOURCES)}",
        )
    # Defence in depth: `icao` is formatted straight into the upstream URL
    # below. `_known_airport` is expected to reject anything that is not a
    # real, allowed airport, but a bare alphanumeric check here means a
    # malformed value (notably one containing "." or "/", as in a path-
    # traversal attempt) can never reach URL construction even if that
    # helper's behaviour changes later.
    if not icao.isalnum():
        raise ApiError(400, "invalid_request", f"invalid airport code {icao!r}")
    # Validated the same way as this route's six aggregate siblings: the
    # key's airport restriction is enforced BEFORE the airport is confirmed
    # to exist, so a restricted key can't distinguish "not yours" from
    # "doesn't exist". The connection is opened just for this lookup and
    # closed before the outbound call below — it must not sit open across a
    # network round trip to the sidecar.
    with db_session(settings.database_path) as conn:
        normalized = _known_airport(conn, ctx, icao)
    url = settings.ledger_api_base_url.rstrip("/") + template.format(icao=normalized)

    # Drop internal-only escape hatches (_now) rather than forwarding them.
    params = {k: v for k, v in request.query_params.items() if not k.startswith("_")}

    try:
        async with httpx.AsyncClient(timeout=settings.request_timeout_seconds) as client:
            upstream = await client.get(url, params=params)
    except httpx.HTTPError as exc:
        raise ApiError(
            503, "upstream_unavailable",
            "the ledger service is not reachable right now",
        ) from exc

    if upstream.status_code == 404:
        raise ApiError(404, "not_found", f"no ledger data for {normalized}")
    # A 4xx from the sidecar is the caller's fault, not an outage: ledger-api
    # bounds `days` to [1, 365], so `?days=999` comes back 422. Reporting that
    # as 503 tells the partner the service is down when their parameter is
    # wrong, and pages whoever alerts on /v1 5xx. Only 5xx and connection
    # failures are outages.
    if 400 <= upstream.status_code < 500:
        raise ApiError(
            400, "invalid_request",
            f"the ledger service rejected this request ({upstream.status_code}); "
            "check the query parameters",
        )
    if upstream.status_code >= 500:
        raise ApiError(
            503, "upstream_unavailable",
            f"the ledger service returned {upstream.status_code}",
        )
    try:
        content = upstream.json()
    except json.JSONDecodeError:
        # A 200 with a body that isn't JSON is still an upstream failure mode
        # from the partner's point of view; it must land in the same /v1
        # error envelope as every other upstream problem, not escape as a
        # raw 500.
        raise ApiError(
            503, "upstream_unavailable",
            "the ledger service returned an invalid response",
        ) from None
    return JSONResponse(status_code=200, content=content)
