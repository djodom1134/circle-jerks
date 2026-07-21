"""The public, key-authenticated /v1 API.

Deliberately separate from the internal routes in main.py: those are shaped for
the frontend and change with it, while everything here is a contract partners
depend on. This module never imports main.py — it reads settings and store off
`request.app.state` so the dependency direction stays one-way.
"""

from __future__ import annotations

import base64
import binascii
import time
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response
from fastapi.exception_handlers import (
    http_exception_handler as default_http_exception_handler,
    request_validation_exception_handler as default_validation_exception_handler,
)
from fastapi.exceptions import RequestValidationError
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.openapi.utils import get_openapi
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.utils import is_body_allowed_for_status_code
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import api_keys, db
from .api_keys import ApiKeyContext
from .db import db_session
from .geo import bbox_for_radius
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


async def api_error_handler(request: Request, exc: ApiError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": {"code": exc.code, "message": exc.message}},
        headers=exc.headers,
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
    return _envelope(422, "invalid_request", message)


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
        headers=headers,
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


_SECURITY_SCHEMES = {
    "bearerAuth": {
        "type": "http",
        "scheme": "bearer",
        "description": "Authorization: Bearer <key>",
    },
    "apiKeyHeader": {
        "type": "apiKey",
        "in": "header",
        "name": "X-API-Key",
        "description": "The same key presented as a header instead.",
    },
}


_FORWARDED_PREFIX_HEADER = "X-Forwarded-Prefix"


def normalize_prefix(raw: str | None) -> str | None:
    """Normalize a forwarded prefix, or None when there isn't a usable one.

    Trailing slashes are dropped so the value concatenates cleanly with the
    "/v1/..." paths in the schema, and anything that is not rooted at "/" is
    discarded rather than trusted.

    A protocol-relative value like "//evil.example/api" is rooted at "/" but
    is an absolute URL to another host. Left through, it would land in the
    schema's `servers` entry and Swagger's "Try it out" would send the
    partner's API key off-site, so it is rejected explicitly.
    """
    if not raw:
        return None
    prefix = raw.strip().rstrip("/")
    if not prefix.startswith("/") or prefix.startswith("//"):
        return None
    return prefix


def external_prefix(request: Request) -> str | None:
    """The path prefix a proxy stripped, as that proxy declared it.

    Every proxy in front of this app strips its prefix before forwarding:
    Caddy uses `handle_path /api/*` (and `/live/*` on the ledger domain) and
    Vite rewrites `/api` away, so `request.url.path` is always the bare
    "/v1/..." and cannot tell us anything. The prefix therefore has to be
    declared out of band, via X-Forwarded-Prefix, which each of those proxies
    sets. Under direct uvicorn nobody sets it and this is None.
    """
    return normalize_prefix(request.headers.get(_FORWARDED_PREFIX_HEADER))


@router.get("/openapi.json", include_in_schema=False)
async def public_openapi(request: Request) -> JSONResponse:
    """A schema built from this router alone, so internal routes never leak."""
    prefix = external_prefix(request)
    schema = get_openapi(
        title="Circle Jerks Public API",
        version=API_VERSION,
        description="Read-only access to operations, tracks, aggregates, and the ledger.",
        routes=router.routes,
        servers=[{"url": prefix}] if prefix else None,
    )
    # Partners have to be told how to authenticate; both accepted mechanisms
    # are declared, and applied to every operation by default.
    schema.setdefault("components", {})["securitySchemes"] = _SECURITY_SCHEMES
    schema["security"] = [{"bearerAuth": []}, {"apiKeyHeader": []}]
    return JSONResponse(schema)


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
    end_ts = parse_time(until, "until") or now
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
    icao = require_airport(ctx, airport)
    start_ts, end_ts = resolve_range(since, until, now=int(time.time()))
    size = page_limit(limit)
    after = decode_cursor(cursor) if cursor else None

    with db_session(settings.database_path) as conn:
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
