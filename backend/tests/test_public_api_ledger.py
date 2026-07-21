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
    db_path = configure(tmp_path, monkeypatch)
    key = mint(db_path, scopes=["ledger:read"])

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"detail": "airport not found"})

    monkeypatch.setattr(httpx.AsyncClient, "__init__", stub_transport(handler))

    with TestClient(app) as client:
        resp = client.get("/v1/ledger/airports/ZZZZ/ledger", headers=auth(key))
        assert resp.status_code == 404
        assert resp.json()["error"]["code"] == "not_found"


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
