from __future__ import annotations

import httpx
import pytest
from fastapi.testclient import TestClient

from app.main import app
from test_public_api import auth, configure, mint


def stub_transport(handler):
    """Patch httpx.AsyncClient so no real ledger-api call is made."""
    original = httpx.AsyncClient.__init__

    def patched(self, *args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        original(self, *args, **kwargs)

    return patched


async def send_raw_asgi_request(path: str, headers: dict[str, str]) -> tuple[int, bytes]:
    """Deliver `path` to the app exactly as given: no client-side URL parsing,
    no dot-segment normalization.

    This is what distinguishes the path-traversal regression test from a
    normal TestClient/httpx request: any client built on a URL parser (httpx
    included) collapses "/a/../b" to "/b" before it ever leaves the process,
    which would make the test pass for the wrong reason. A raw socket or
    `curl --path-as-is` sends the unnormalized bytes, and uvicorn does not
    collapse them either — Starlette's router matches path *segments*
    directly against the route template, so an unnormalized ".." segment
    lands in the `{icao}` path parameter verbatim. Driving the ASGI app with
    a hand-built scope reproduces exactly that.
    """
    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode("utf-8"),
        "query_string": b"",
        "root_path": "",
        "headers": [
            (name.lower().encode("latin-1"), value.encode("latin-1"))
            for name, value in headers.items()
        ]
        + [(b"host", b"testserver")],
        "client": ("testclient", 50000),
        "server": ("testserver", 80),
    }
    messages: list[dict] = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        messages.append(message)

    # Drives the same startup/shutdown lifespan TestClient would, without
    # going through TestClient's own (URL-normalizing) request path.
    async with app.router.lifespan_context(app):
        await app(scope, receive, send)

    status = next(m["status"] for m in messages if m["type"] == "http.response.start")
    body = b"".join(m.get("body", b"") for m in messages if m["type"] == "http.response.body")
    return status, body


def test_ledger_requires_the_ledger_scope(tmp_path, monkeypatch):
    db_path = configure(tmp_path, monkeypatch)
    key = mint(db_path, scopes=["ops:read"])
    with TestClient(app) as client:
        resp = client.get("/v1/ledger/airports/KLMO/ledger", headers=auth(key))
        assert resp.status_code == 403
        assert resp.json()["error"]["code"] == "forbidden_scope"


def test_ledger_honours_the_airport_restriction(tmp_path, monkeypatch):
    db_path = configure(tmp_path, monkeypatch)
    key = mint(db_path, scopes=["ledger:read"], airports=["KBJC"])
    with TestClient(app) as client:
        resp = client.get("/v1/ledger/airports/KLMO/ledger", headers=auth(key))
        assert resp.status_code == 403
        assert resp.json()["error"]["code"] == "forbidden_airport"


def test_unknown_ledger_resource_is_404(tmp_path, monkeypatch):
    db_path = configure(tmp_path, monkeypatch)
    key = mint(db_path, scopes=["ledger:read"])
    with TestClient(app) as client:
        resp = client.get("/v1/ledger/airports/KLMO/nonsense", headers=auth(key))
        assert resp.status_code == 404
        assert resp.json()["error"]["code"] == "not_found"


def test_ledger_proxies_upstream_json_and_forwards_query_params(tmp_path, monkeypatch):
    db_path = configure(tmp_path, monkeypatch)
    key = mint(db_path, scopes=["ledger:read"])
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        return httpx.Response(200, json={"airport_icao": "KLMO", "total_fees": 1234})

    monkeypatch.setattr(httpx.AsyncClient, "__init__", stub_transport(handler))

    with TestClient(app) as client:
        resp = client.get(
            "/v1/ledger/airports/KLMO/ledger", params={"days": 7}, headers=auth(key)
        )
        assert resp.status_code == 200
        assert resp.json()["total_fees"] == 1234
    assert seen["url"].endswith("/airports/KLMO/ledger?days=7")


def test_ledger_forwards_upstream_404_as_a_v1_error(tmp_path, monkeypatch):
    # KLMO is seeded (unlike the ZZZZ this test used before airport
    # existence was validated locally); the point of this test is the
    # upstream-404 mapping, so the ICAO must pass the local existence check
    # and let the stub's 404 do the work.
    db_path = configure(tmp_path, monkeypatch)
    key = mint(db_path, scopes=["ledger:read"])

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"detail": "airport not found"})

    monkeypatch.setattr(httpx.AsyncClient, "__init__", stub_transport(handler))

    with TestClient(app) as client:
        resp = client.get("/v1/ledger/airports/KLMO/ledger", headers=auth(key))
        assert resp.status_code == 404
        assert resp.json()["error"]["code"] == "not_found"


def test_upstream_4xx_is_the_partners_bad_request_not_an_outage(tmp_path, monkeypatch):
    """ledger-api bounds `days` to [1, 365], so `?days=999` comes back 422.
    Mapping that to 503 told the partner the service was down because of their
    own parameter, and paged anyone alerting on /v1 5xx."""
    db_path = configure(tmp_path, monkeypatch)
    key = mint(db_path, scopes=["ledger:read"])

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(422, json={"detail": "days must be <= 365"})

    monkeypatch.setattr(httpx.AsyncClient, "__init__", stub_transport(handler))

    with TestClient(app) as client:
        resp = client.get(
            "/v1/ledger/airports/KLMO/ledger", params={"days": 999}, headers=auth(key)
        )
        assert resp.status_code == 400
        assert resp.json()["error"]["code"] == "invalid_request"
        assert "422" in resp.json()["error"]["message"]


@pytest.mark.parametrize("upstream_status", [500, 502, 503])
def test_upstream_5xx_is_still_an_outage(tmp_path, monkeypatch, upstream_status):
    """The other half of the 4xx split: a real upstream failure must keep
    answering 503 upstream_unavailable."""
    db_path = configure(tmp_path, monkeypatch)
    key = mint(db_path, scopes=["ledger:read"])

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(upstream_status, json={"detail": "boom"})

    monkeypatch.setattr(httpx.AsyncClient, "__init__", stub_transport(handler))

    with TestClient(app) as client:
        resp = client.get("/v1/ledger/airports/KLMO/ledger", headers=auth(key))
        assert resp.status_code == 503
        assert resp.json()["error"]["code"] == "upstream_unavailable"
        assert str(upstream_status) in resp.json()["error"]["message"]


def test_unreachable_ledger_service_returns_503(tmp_path, monkeypatch):
    db_path = configure(tmp_path, monkeypatch)
    key = mint(db_path, scopes=["ledger:read"])

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    monkeypatch.setattr(httpx.AsyncClient, "__init__", stub_transport(handler))

    with TestClient(app) as client:
        resp = client.get("/v1/ledger/airports/KLMO/ledger", headers=auth(key))
        assert resp.status_code == 503
        assert resp.json()["error"]["code"] == "upstream_unavailable"
        assert "ledger" in resp.json()["error"]["message"]


async def test_raw_unnormalized_traversal_icao_is_rejected(tmp_path, monkeypatch):
    """FINDING 1 regression test.

    An unrestricted key (no airport list) previously let `..` through
    `require_airport` unchecked, and `template.format(icao="..")` built
    ".../airports/../ledger" — which httpx's outbound URL normalization
    then collapsed to "/ledger" on the sidecar, stripping the intended
    prefix entirely. The fix must reject `..` before it ever reaches URL
    construction, and no upstream call may fire.
    """
    db_path = configure(tmp_path, monkeypatch)
    key = mint(db_path, scopes=["ledger:read"])  # unrestricted: airports=None

    called = {"hit": False}

    def handler(request: httpx.Request) -> httpx.Response:
        called["hit"] = True
        return httpx.Response(200, json={"ok": True})

    monkeypatch.setattr(httpx.AsyncClient, "__init__", stub_transport(handler))

    status, body = await send_raw_asgi_request(
        "/v1/ledger/airports/../ledger",
        {"Authorization": f"Bearer {key}"},
    )

    assert status == 400
    assert called["hit"] is False, "the traversal request must never reach the upstream call"


def test_non_json_upstream_200_is_upstream_unavailable(tmp_path, monkeypatch):
    """FINDING 2 regression test: a non-JSON 200 body must not escape as a
    generic 500 — it is just another upstream failure mode from the
    partner's point of view."""
    db_path = configure(tmp_path, monkeypatch)
    key = mint(db_path, scopes=["ledger:read"])

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"not json at all", headers={"content-type": "text/plain"})

    monkeypatch.setattr(httpx.AsyncClient, "__init__", stub_transport(handler))

    with TestClient(app) as client:
        resp = client.get("/v1/ledger/airports/KLMO/ledger", headers=auth(key))
        assert resp.status_code == 503
        assert resp.json()["error"]["code"] == "upstream_unavailable"


def test_underscore_prefixed_query_params_are_stripped(tmp_path, monkeypatch):
    """FINDING 3: `_now` (and any other `_`-prefixed param) is an internal
    escape hatch on the sidecar and must never reach it, while an ordinary
    param passes through untouched."""
    db_path = configure(tmp_path, monkeypatch)
    key = mint(db_path, scopes=["ledger:read"])
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        return httpx.Response(200, json={"ok": True})

    monkeypatch.setattr(httpx.AsyncClient, "__init__", stub_transport(handler))

    with TestClient(app) as client:
        resp = client.get(
            "/v1/ledger/airports/KLMO/ledger",
            params={"_now": "2020-01-01T00:00:00Z", "days": 7},
            headers=auth(key),
        )
        assert resp.status_code == 200
    assert "_now" not in seen["url"]
    assert "days=7" in seen["url"]


def test_restricted_key_nonexistent_airport_is_forbidden_not_not_found(tmp_path, monkeypatch):
    """The restriction check must run before the existence check: a key
    restricted to KBJC asking about an airport that doesn't exist at all
    (ZZZZ) must get 403 forbidden_airport, not 404 not_found — otherwise a
    403 vs. 404 split would let a restricted key fingerprint which airports
    exist outside its own allowlist."""
    db_path = configure(tmp_path, monkeypatch)
    key = mint(db_path, scopes=["ledger:read"], airports=["KBJC"])

    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("must not reach the upstream call")

    monkeypatch.setattr(httpx.AsyncClient, "__init__", stub_transport(handler))

    with TestClient(app) as client:
        resp = client.get("/v1/ledger/airports/ZZZZ/ledger", headers=auth(key))
        assert resp.status_code == 403
        assert resp.json()["error"]["code"] == "forbidden_airport"
