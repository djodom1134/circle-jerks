from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app
from test_public_api import auth, configure, mint

PATHS = [
    "/v1/airports/KBJC/stats",
    "/v1/airports/KBJC/worst-offenders",
    "/v1/airports/KBJC/operations-trends",
    "/v1/airports/KBJC/vnap-compliance",
    "/v1/airports/KBJC/runways",
    "/v1/airports/KBJC/patterns",
    "/v1/airports/KBJC/flow",
]


@pytest.mark.parametrize("path", PATHS)
def test_aggregates_require_the_aggregates_scope(tmp_path, monkeypatch, path):
    db_path = configure(tmp_path, monkeypatch)
    key = mint(db_path, scopes=["ops:read"])
    with TestClient(app) as client:
        resp = client.get(path, headers=auth(key))
        assert resp.status_code == 403
        assert resp.json()["error"]["code"] == "forbidden_scope"


@pytest.mark.parametrize("path", PATHS)
def test_aggregates_honour_the_airport_restriction(tmp_path, monkeypatch, path):
    db_path = configure(tmp_path, monkeypatch)
    key = mint(db_path, scopes=["aggregates:read"], airports=["KLMO"])
    with TestClient(app) as client:
        resp = client.get(path, headers=auth(key))
        assert resp.status_code == 403
        assert resp.json()["error"]["code"] == "forbidden_airport"


@pytest.mark.parametrize("path", PATHS)
def test_aggregates_answer_for_a_seeded_airport(tmp_path, monkeypatch, path):
    db_path = configure(tmp_path, monkeypatch)
    key = mint(db_path, scopes=["aggregates:read"])
    with TestClient(app) as client:
        resp = client.get(path, headers=auth(key))
        assert resp.status_code == 200
        assert resp.json()["airport_icao"] == "KBJC"


@pytest.mark.parametrize(
    "path",
    [
        "/v1/airports/ZZZZ/stats",
        "/v1/airports/ZZZZ/worst-offenders",
        "/v1/airports/ZZZZ/operations-trends",
        "/v1/airports/ZZZZ/vnap-compliance",
        "/v1/airports/ZZZZ/runways",
        "/v1/airports/ZZZZ/patterns",
        "/v1/airports/ZZZZ/flow",
    ],
)
def test_unknown_airport_is_a_v1_shaped_404(tmp_path, monkeypatch, path):
    db_path = configure(tmp_path, monkeypatch)
    key = mint(db_path, scopes=["aggregates:read"])
    with TestClient(app) as client:
        resp = client.get(path, headers=auth(key))
        assert resp.status_code == 404
        assert resp.json()["error"]["code"] == "not_found"


def test_stats_window_parameter_is_validated(tmp_path, monkeypatch):
    db_path = configure(tmp_path, monkeypatch)
    key = mint(db_path, scopes=["aggregates:read"])
    with TestClient(app) as client:
        ok = client.get("/v1/airports/KBJC/stats", params={"window": "30d"}, headers=auth(key))
        assert ok.status_code == 200
        assert ok.json()["window"]["code"] == "30d"

        bad = client.get("/v1/airports/KBJC/stats", params={"window": "9y"}, headers=auth(key))
        assert bad.status_code == 400
        assert bad.json()["error"]["code"] == "invalid_request"
