from __future__ import annotations

from fastapi.testclient import TestClient

from app import db
from app.main import app
from app.settings import get_settings
from test_public_api import auth, configure, mint

NOW = 1_700_000_000


def _seed(db_path, *, icao, id, ts, type, agl):
    # init_db seeds KBJC and KLMO into `airports`, so _known_airport resolves
    # them (same reliance as test_public_api_operations.py's seed_operations).
    db.init_db(db_path)
    with db.db_session(db_path) as conn:
        conn.execute(
            "INSERT INTO operations (id, icao, icao24, type, timestamp, min_altitude_ft_agl) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (id, icao, "a26f5e", type, ts, agl),
        )
        conn.commit()


def test_summary_requires_the_ops_scope(tmp_path, monkeypatch):
    db_path = configure(tmp_path, monkeypatch)
    key = mint(db_path, scopes=["aggregates:read"])
    with TestClient(app) as client:
        resp = client.get("/v1/operations/summary", params={"airport": "KBJC"}, headers=auth(key))
        assert resp.status_code == 403
        assert resp.json()["error"]["code"] == "forbidden_scope"


def test_summary_enforces_the_airport_restriction(tmp_path, monkeypatch):
    db_path = configure(tmp_path, monkeypatch)
    key = mint(db_path, scopes=["ops:read"], airports=["KLMO"])
    with TestClient(app) as client:
        resp = client.get("/v1/operations/summary", params={"airport": "KBJC"}, headers=auth(key))
        assert resp.status_code == 403
        assert resp.json()["error"]["code"] == "forbidden_airport"


def test_summary_404s_an_unknown_airport(tmp_path, monkeypatch):
    db_path = configure(tmp_path, monkeypatch)
    key = mint(db_path, scopes=["ops:read"])
    with TestClient(app) as client:
        resp = client.get("/v1/operations/summary", params={"airport": "ZZZZ"}, headers=auth(key))
        assert resp.status_code == 404
        assert resp.json()["error"]["code"] == "not_found"


def test_summary_totals_and_shape(tmp_path, monkeypatch):
    db_path = configure(tmp_path, monkeypatch)
    _seed(db_path, icao="KBJC", id="t", ts=NOW, type="takeoff", agl=None)
    _seed(db_path, icao="KBJC", id="tg", ts=NOW + 1, type="touch_and_go", agl=900)
    _seed(db_path, icao="KBJC", id="la", ts=NOW + 2, type="low_approach", agl=20)
    key = mint(db_path, scopes=["ops:read"])
    with TestClient(app) as client:
        resp = client.get(
            "/v1/operations/summary",
            params={"airport": "KBJC", "since": NOW - 10, "until": NOW + 10},
            headers=auth(key),
        )
        assert resp.status_code == 200
        body = resp.json()
        assert set(body) == {
            "airport_icao", "since_ts", "until_ts", "faa_operations",
            "arrivals", "departures", "by_event_type",
            "low_approach_touchdowns", "low_approach_touchdown_max_agl_ft",
        }
        assert body["airport_icao"] == "KBJC"
        assert body["faa_operations"] == 5           # 1 + 2 + 2
        assert body["arrivals"] + body["departures"] == body["faa_operations"]
        assert body["low_approach_touchdowns"] == 1
        assert body["low_approach_touchdown_max_agl_ft"] == 25
        assert body["by_event_type"] == {"takeoff": 1, "touch_and_go": 1, "low_approach": 1}
