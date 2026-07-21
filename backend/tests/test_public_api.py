from __future__ import annotations

import json
import time
from datetime import datetime, timezone

import pytest
from fastapi import HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.testclient import TestClient

from app import api_keys, db, public_api
from app.api_keys import ApiKeyContext
from app.main import app
from app.public_api import (
    ApiError,
    decode_cursor,
    encode_cursor,
    normalize_prefix,
    page_limit,
    paged,
    parse_time,
    require_airport,
    require_scope,
)
from app.settings import get_settings


def configure(tmp_path, monkeypatch):
    monkeypatch.setenv("CIRCLEJERK_DATABASE_PATH", str(tmp_path / "circlejerk.sqlite3"))
    monkeypatch.setenv("CIRCLEJERK_REDIS_URL", "memory://")
    monkeypatch.setenv("CIRCLEJERK_ENVIRONMENT", "test")
    get_settings.cache_clear()
    return str(tmp_path / "circlejerk.sqlite3")


def mint(db_path: str, *, scopes: list[str], airports: list[str] | None = None) -> str:
    """Insert a key directly and return the full presentable key string."""
    full, key_id, secret_hash = api_keys.generate_key("test")
    db.init_db(db_path)
    with db.db_session(db_path) as conn:
        db.create_api_key(
            conn,
            key_id=key_id,
            secret_hash=secret_hash,
            name="test-key",
            scopes=api_keys.serialize_scopes(scopes),
            airports=api_keys.serialize_airports(airports),
            created_at=int(time.time()),
            created_by="admin",
        )
    return full


def auth(key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}"}


def test_meta_requires_a_key(tmp_path, monkeypatch):
    configure(tmp_path, monkeypatch)
    with TestClient(app) as client:
        resp = client.get("/v1/meta")
        assert resp.status_code == 401
        assert resp.json()["error"]["code"] == "unauthorized"


def test_meta_echoes_the_keys_scopes_and_airports(tmp_path, monkeypatch):
    db_path = configure(tmp_path, monkeypatch)
    key = mint(db_path, scopes=["ops:read", "aggregates:read"], airports=["KLMO"])
    with TestClient(app) as client:
        resp = client.get("/v1/meta", headers=auth(key))
        assert resp.status_code == 200
        body = resp.json()
        assert body["name"] == "test-key"
        assert sorted(body["scopes"]) == ["aggregates:read", "ops:read"]
        assert body["airports"] == ["KLMO"]
        assert body["version"] == "v1"


def test_unrestricted_key_reports_null_airports(tmp_path, monkeypatch):
    db_path = configure(tmp_path, monkeypatch)
    key = mint(db_path, scopes=["ops:read"], airports=None)
    with TestClient(app) as client:
        assert client.get("/v1/meta", headers=auth(key)).json()["airports"] is None


def test_x_api_key_header_is_accepted(tmp_path, monkeypatch):
    db_path = configure(tmp_path, monkeypatch)
    key = mint(db_path, scopes=["ops:read"])
    with TestClient(app) as client:
        assert client.get("/v1/meta", headers={"X-API-Key": key}).status_code == 200


@pytest.mark.parametrize("presented", ["garbage", "cj_test_0123456789abcdef_wrongsecret"])
def test_unknown_and_wrong_keys_are_indistinguishable(tmp_path, monkeypatch, presented):
    db_path = configure(tmp_path, monkeypatch)
    mint(db_path, scopes=["ops:read"])
    with TestClient(app) as client:
        resp = client.get("/v1/meta", headers=auth(presented))
        assert resp.status_code == 401
        assert resp.json()["error"] == {
            "code": "unauthorized",
            "message": "invalid or missing API key",
        }


def test_revoked_key_is_rejected(tmp_path, monkeypatch):
    db_path = configure(tmp_path, monkeypatch)
    key = mint(db_path, scopes=["ops:read"])
    key_id = api_keys.parse_key(key)[0]
    with db.db_session(db_path) as conn:
        db.revoke_api_key(conn, key_id, int(time.time()))

    with TestClient(app) as client:
        assert client.get("/v1/meta", headers=auth(key)).status_code == 401


def test_successful_call_records_last_used_at(tmp_path, monkeypatch):
    db_path = configure(tmp_path, monkeypatch)
    key = mint(db_path, scopes=["ops:read"])
    key_id = api_keys.parse_key(key)[0]
    with TestClient(app) as client:
        client.get("/v1/meta", headers=auth(key))
    with db.db_session(db_path) as conn:
        assert db.get_api_key(conn, key_id)["last_used_at"] is not None


def test_rate_limit_headers_and_429(tmp_path, monkeypatch):
    db_path = configure(tmp_path, monkeypatch)
    key = mint(db_path, scopes=["ops:read"])
    monkeypatch.setattr("app.public_api.RATE_LIMIT_PER_MINUTE", 2)

    with TestClient(app) as client:
        first = client.get("/v1/meta", headers=auth(key))
        assert first.headers["X-RateLimit-Limit"] == "2"
        assert first.headers["X-RateLimit-Remaining"] == "1"

        client.get("/v1/meta", headers=auth(key))
        third = client.get("/v1/meta", headers=auth(key))
        assert third.status_code == 429
        assert third.json()["error"]["code"] == "rate_limited"
        assert third.headers["Retry-After"] == "60"


def test_docs_and_openapi_cover_only_the_v1_namespace(tmp_path, monkeypatch):
    configure(tmp_path, monkeypatch)
    with TestClient(app) as client:
        schema = client.get("/v1/openapi.json")
        assert schema.status_code == 200
        paths = schema.json()["paths"]
        assert "/v1/meta" in paths
        assert "/admin/dashboard" not in paths
        assert client.get("/v1/docs").status_code == 200


def test_internal_routes_keep_their_detail_error_shape(tmp_path, monkeypatch):
    configure(tmp_path, monkeypatch)
    with TestClient(app) as client:
        resp = client.get("/admin/dashboard")
        assert resp.status_code in (401, 503)
        assert "detail" in resp.json()


# ─── The error envelope is total over /v1 ────────────────────────────────────

def test_unknown_v1_path_uses_the_error_envelope(tmp_path, monkeypatch):
    configure(tmp_path, monkeypatch)
    with TestClient(app) as client:
        resp = client.get("/v1/does-not-exist")
        assert resp.status_code == 404
        assert resp.json() == {
            "error": {"code": "not_found", "message": "Not Found"}
        }


def test_unknown_internal_path_keeps_detail(tmp_path, monkeypatch):
    configure(tmp_path, monkeypatch)
    with TestClient(app) as client:
        resp = client.get("/definitely-not-a-route")
        assert resp.status_code == 404
        assert resp.json() == {"detail": "Not Found"}


def test_method_not_allowed_on_v1_is_enveloped(tmp_path, monkeypatch):
    configure(tmp_path, monkeypatch)
    with TestClient(app) as client:
        resp = client.post("/v1/meta")
        assert resp.status_code == 405
        assert resp.json()["error"]["code"] == "method_not_allowed"


def _request(path: str) -> Request:
    return Request({
        "type": "http",
        "method": "GET",
        "scheme": "http",
        "server": ("testserver", 80),
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "headers": [],
    })


async def test_validation_errors_are_enveloped_on_v1():
    exc = RequestValidationError([
        {"loc": ("query", "since"), "msg": "Input should be a valid integer",
         "type": "int_parsing"},
    ])
    resp = await public_api.validation_error_handler(_request("/v1/ops"), exc)
    assert resp.status_code == 422
    body = json.loads(resp.body)
    assert body["error"]["code"] == "invalid_request"
    assert "since" in body["error"]["message"]


async def test_validation_errors_keep_detail_off_v1():
    exc = RequestValidationError([
        {"loc": ("query", "since"), "msg": "Input should be a valid integer",
         "type": "int_parsing"},
    ])
    resp = await public_api.validation_error_handler(_request("/scan"), exc)
    assert resp.status_code == 422
    assert "detail" in json.loads(resp.body)


@pytest.mark.parametrize("path, public", [
    ("/v1", True),
    ("/v1/meta", True),
    ("/v1/", True),
    # A future internal namespace must not inherit the partner envelope.
    ("/v1beta/anything", False),
    ("/v1x", False),
    ("/scan", False),
])
def test_only_the_v1_namespace_is_public(path, public):
    assert public_api.is_public_path(_request(path)) is public


@pytest.mark.parametrize("status", [204, 304])
async def test_bodyless_statuses_get_no_envelope(status):
    """A body under 204/304 is malformed however pretty the JSON is."""
    resp = await public_api.http_exception_handler(
        _request("/v1/meta"), HTTPException(status_code=status),
    )
    assert resp.status_code == status
    assert resp.body == b""


async def test_container_details_do_not_leak_a_python_repr():
    resp = await public_api.http_exception_handler(
        _request("/v1/meta"), HTTPException(status_code=403, detail={"field": "x"}),
    )
    message = json.loads(resp.body)["error"]["message"]
    assert message == "forbidden"
    assert "{" not in message


async def test_string_details_still_reach_the_message():
    resp = await public_api.http_exception_handler(
        _request("/v1/meta"), HTTPException(status_code=404, detail="no such flight"),
    )
    assert json.loads(resp.body)["error"] == {
        "code": "not_found", "message": "no such flight",
    }


# ─── parse_time ──────────────────────────────────────────────────────────────

def test_compact_iso_date_is_not_read_as_a_unix_timestamp():
    parsed = parse_time("20260720", "since")
    # The bug returned 20260720 itself, i.e. 1970-08-23.
    assert parsed != 20260720
    assert datetime.fromtimestamp(parsed, timezone.utc).year == 2026
    assert parsed == int(datetime(2026, 7, 20, tzinfo=timezone.utc).timestamp())


def test_unix_timestamps_still_parse():
    assert parse_time("1700000000", "since") == 1700000000
    assert parse_time("0", "since") == 0
    assert parse_time("-100", "since") == -100


def test_underscore_separated_integers_are_rejected():
    with pytest.raises(ApiError) as exc:
        parse_time("1_000", "since")
    assert exc.value.code == "invalid_request"
    assert exc.value.status_code == 400


@pytest.mark.parametrize("value", [" 1700000000", "١٧٠٠", "20260720120000"])
def test_only_plain_short_digit_strings_are_timestamps(value):
    with pytest.raises(ApiError):
        parse_time(value, "since")


def test_iso_datetime_with_and_without_a_timezone():
    naive = parse_time("2026-07-20T12:00:00", "since")
    aware = parse_time("2026-07-20T12:00:00Z", "since")
    offset = parse_time("2026-07-20T06:00:00-06:00", "since")
    # A naive value is read as UTC, so all three are the same instant.
    assert naive == aware == offset
    assert aware == int(datetime(2026, 7, 20, 12, tzinfo=timezone.utc).timestamp())


def test_blank_and_none_are_absent():
    assert parse_time(None, "since") is None
    assert parse_time("", "since") is None


def test_garbage_time_is_an_api_error():
    with pytest.raises(ApiError) as exc:
        parse_time("not-a-time", "since")
    assert exc.value.code == "invalid_request"


# ─── Authorization gates ─────────────────────────────────────────────────────

def ctx(*, scopes: list[str], airports: list[str] | None = None) -> ApiKeyContext:
    return ApiKeyContext(
        key_id="k_test",
        name="test-key",
        scopes=frozenset(scopes),
        airports=frozenset(airports) if airports is not None else None,
    )


async def test_require_scope_passes_for_a_held_scope():
    held = ctx(scopes=["ops:read"])
    assert await require_scope("ops:read")(held) is held


async def test_require_scope_rejects_a_scope_not_held():
    with pytest.raises(ApiError) as exc:
        await require_scope("tracks:read")(ctx(scopes=["ops:read"]))
    assert exc.value.status_code == 403
    assert exc.value.code == "forbidden_scope"
    assert "tracks:read" in exc.value.message


def test_require_airport_allows_a_listed_icao():
    assert require_airport(ctx(scopes=[], airports=["KLMO"]), "KLMO") == "KLMO"


def test_require_airport_allows_anything_when_unrestricted():
    assert require_airport(ctx(scopes=[], airports=None), "KDEN") == "KDEN"


def test_require_airport_normalizes_case():
    assert require_airport(ctx(scopes=[], airports=["KLMO"]), "klmo") == "KLMO"


def test_require_airport_rejects_an_unlisted_icao():
    with pytest.raises(ApiError) as exc:
        require_airport(ctx(scopes=[], airports=["KLMO"]), "KDEN")
    assert exc.value.status_code == 403
    assert exc.value.code == "forbidden_airport"
    assert "KDEN" in exc.value.message


# ─── Pagination helpers ──────────────────────────────────────────────────────

def test_cursors_round_trip():
    assert decode_cursor(encode_cursor(1700000000, "op-42")) == (1700000000, "op-42")


def test_cursor_survives_colons_in_the_row_id():
    assert decode_cursor(encode_cursor(5, "a:b:c")) == (5, "a:b:c")


def test_cursors_carry_no_padding():
    assert "=" not in encode_cursor(1700000000, "op-42")


@pytest.mark.parametrize("garbage", ["!!!!", "Zm9v", "", "____"])
def test_decode_cursor_rejects_garbage(garbage):
    with pytest.raises(ApiError) as exc:
        decode_cursor(garbage)
    assert exc.value.code == "invalid_request"
    assert exc.value.status_code == 400


def test_page_limit_clamps_and_defaults():
    assert page_limit(None) == public_api.DEFAULT_PAGE_SIZE
    assert page_limit(public_api.MAX_PAGE_SIZE + 1) == public_api.MAX_PAGE_SIZE
    assert page_limit(0) == 1
    assert page_limit(-5) == 1
    assert page_limit(250) == 250


def test_paged_emits_next_cursor_only_on_a_full_page():
    rows = [{"id": "a"}, {"id": "b"}]
    full = paged(rows, 2, lambda row: row["id"])
    assert full == {"data": rows, "next_cursor": "b"}

    partial = paged(rows, 5, lambda row: row["id"])
    assert partial == {"data": rows, "next_cursor": None}

    assert paged([], 5, lambda row: row["id"]) == {"data": [], "next_cursor": None}


# ─── OpenAPI servers and security schemes ────────────────────────────────────

@pytest.mark.parametrize("raw, expected", [
    ("/api", "/api"),
    ("/live", "/live"),
    ("/api/", "/api"),
    ("/deep/nest/", "/deep/nest"),
    ("  /api  ", "/api"),
    (None, None),
    ("", None),
    ("/", None),
    ("api", None),
    ("https://evil.example/api", None),
    # Protocol-relative: rooted at "/" but an absolute URL to another host.
    # Left through, Swagger's "Try it out" would send the key off-site.
    ("//evil.example/api", None),
    ("//evil.example", None),
])
def test_normalize_prefix(raw, expected):
    assert normalize_prefix(raw) == expected


# The proxies strip their prefix before FastAPI sees the request, so these go
# through the real app with the header Caddy and Vite actually set.
@pytest.mark.parametrize("prefix", ["/api", "/live"])
def test_openapi_servers_follow_the_forwarded_prefix(tmp_path, monkeypatch, prefix):
    configure(tmp_path, monkeypatch)
    with TestClient(app) as client:
        schema = client.get(
            "/v1/openapi.json", headers={"X-Forwarded-Prefix": prefix},
        ).json()
    assert schema["servers"] == [{"url": prefix}]


def test_openapi_omits_servers_without_a_forwarded_prefix(tmp_path, monkeypatch):
    """Direct uvicorn access: no proxy, no header, no servers block at all."""
    configure(tmp_path, monkeypatch)
    with TestClient(app) as client:
        schema = client.get("/v1/openapi.json").json()
    assert "servers" not in schema


def test_openapi_declares_both_auth_mechanisms(tmp_path, monkeypatch):
    configure(tmp_path, monkeypatch)
    with TestClient(app) as client:
        schema = client.get("/v1/openapi.json").json()
    schemes = schema["components"]["securitySchemes"]
    assert schemes["bearerAuth"] == {
        "type": "http", "scheme": "bearer",
        "description": "Authorization: Bearer <key>",
    }
    assert schemes["apiKeyHeader"]["type"] == "apiKey"
    assert schemes["apiKeyHeader"]["in"] == "header"
    assert schemes["apiKeyHeader"]["name"] == "X-API-Key"
    assert {"bearerAuth": []} in schema["security"]
    assert {"apiKeyHeader": []} in schema["security"]
