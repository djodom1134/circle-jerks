from __future__ import annotations

from fastapi.testclient import TestClient

from app import db
from app.geo import bbox_for_radius
from app.main import app
from app.public_api import MAX_TRACK_SPAN_SECONDS, TRACK_RING_NM
from test_public_api import auth, configure, mint

NOW = 1_700_000_000
# KBJC is in the seeded airport table; these coordinates sit inside its ring.
KBJC_LAT, KBJC_LON = 39.9088, -105.1172


def seed_tracks(db_path: str, count: int, *, icao24: str = "a1b2c3") -> None:
    db.init_db(db_path)
    with db.db_session(db_path) as conn:
        for index in range(count):
            conn.execute(
                """
                INSERT OR REPLACE INTO track_archive
                (icao24, timestamp, lat, lon, altitude_ft, heading_deg,
                 vertical_rate_fpm, callsign, in_window, source)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, 'test')
                """,
                (icao24, NOW + index, KBJC_LAT, KBJC_LON, 6500.0, 110.0, 0.0, "N333RX"),
            )


def test_tracks_requires_the_tracks_scope(tmp_path, monkeypatch):
    db_path = configure(tmp_path, monkeypatch)
    key = mint(db_path, scopes=["ops:read"])
    with TestClient(app) as client:
        resp = client.get(
            "/v1/tracks",
            params={"icao24": "a1b2c3", "since": NOW, "until": NOW + 10},
            headers=auth(key),
        )
        assert resp.status_code == 403
        assert resp.json()["error"]["code"] == "forbidden_scope"


def test_tracks_requires_an_icao24_or_airport(tmp_path, monkeypatch):
    db_path = configure(tmp_path, monkeypatch)
    key = mint(db_path, scopes=["tracks:read"])
    with TestClient(app) as client:
        resp = client.get(
            "/v1/tracks", params={"since": NOW, "until": NOW + 10}, headers=auth(key)
        )
        assert resp.status_code == 400
        assert resp.json()["error"]["code"] == "invalid_request"


def test_tracks_requires_an_explicit_range(tmp_path, monkeypatch):
    db_path = configure(tmp_path, monkeypatch)
    key = mint(db_path, scopes=["tracks:read"])
    with TestClient(app) as client:
        resp = client.get("/v1/tracks", params={"icao24": "a1b2c3"}, headers=auth(key))
        assert resp.status_code == 400
        assert "since" in resp.json()["error"]["message"]


def test_tracks_rejects_a_span_over_the_cap(tmp_path, monkeypatch):
    db_path = configure(tmp_path, monkeypatch)
    key = mint(db_path, scopes=["tracks:read"])
    with TestClient(app) as client:
        resp = client.get(
            "/v1/tracks",
            params={
                "icao24": "a1b2c3",
                "since": NOW,
                "until": NOW + MAX_TRACK_SPAN_SECONDS + 1,
            },
            headers=auth(key),
        )
        assert resp.status_code == 400
        assert resp.json()["error"]["code"] == "invalid_request"


def test_tracks_returns_samples_for_an_aircraft(tmp_path, monkeypatch):
    db_path = configure(tmp_path, monkeypatch)
    seed_tracks(db_path, 1)
    key = mint(db_path, scopes=["tracks:read"])
    with TestClient(app) as client:
        resp = client.get(
            "/v1/tracks",
            params={"icao24": "a1b2c3", "since": NOW - 5, "until": NOW + 5},
            headers=auth(key),
        )
        assert resp.status_code == 200
        row = resp.json()["data"][0]
        assert set(row) == {
            "icao24", "timestamp", "lat", "lon", "altitude_ft",
            "baro_altitude_ft", "geo_altitude_ft", "heading_deg",
            "vertical_rate_fpm", "callsign", "emitter_category", "source",
        }
        assert row["icao24"] == "a1b2c3"


def test_tracks_paginate_without_gaps(tmp_path, monkeypatch):
    db_path = configure(tmp_path, monkeypatch)
    seed_tracks(db_path, 5)
    key = mint(db_path, scopes=["tracks:read"])
    seen: list[int] = []
    with TestClient(app) as client:
        params = {"icao24": "a1b2c3", "since": NOW - 5, "until": NOW + 50, "limit": 2}
        cursor = None
        for _ in range(5):
            page = client.get(
                "/v1/tracks",
                params={**params, **({"cursor": cursor} if cursor else {})},
                headers=auth(key),
            ).json()
            seen.extend(item["timestamp"] for item in page["data"])
            cursor = page["next_cursor"]
            if not cursor:
                break
    assert seen == [NOW + i for i in range(5)]


def test_tracks_by_airport_filters_to_the_ring_and_honours_restriction(tmp_path, monkeypatch):
    db_path = configure(tmp_path, monkeypatch)
    seed_tracks(db_path, 1)
    with db.db_session(db_path) as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO track_archive
            (icao24, timestamp, lat, lon, altitude_ft, in_window, source)
            VALUES ('ffffff', ?, 0.0, 0.0, 3000.0, 1, 'test')
            """,
            (NOW + 1,),
        )

    key = mint(db_path, scopes=["tracks:read"])
    with TestClient(app) as client:
        body = client.get(
            "/v1/tracks",
            params={"airport": "KBJC", "since": NOW - 5, "until": NOW + 5},
            headers=auth(key),
        ).json()
        assert {row["icao24"] for row in body["data"]} == {"a1b2c3"}

    restricted = mint(db_path, scopes=["tracks:read"], airports=["KLMO"])
    with TestClient(app) as client:
        resp = client.get(
            "/v1/tracks",
            params={"airport": "KBJC", "since": NOW - 5, "until": NOW + 5},
            headers=auth(restricted),
        )
        assert resp.status_code == 403
        assert resp.json()["error"]["code"] == "forbidden_airport"


def test_restricted_key_without_airport_is_rejected_even_with_icao24(tmp_path, monkeypatch):
    """Closes the bypass: an icao24-only request from a restricted key must not
    silently reach the database and return samples for an arbitrary aircraft."""
    db_path = configure(tmp_path, monkeypatch)
    seed_tracks(db_path, 1)
    restricted = mint(db_path, scopes=["tracks:read"], airports=["KLMO"])
    with TestClient(app) as client:
        resp = client.get(
            "/v1/tracks",
            params={"icao24": "a1b2c3", "since": NOW - 5, "until": NOW + 5},
            headers=auth(restricted),
        )
        assert resp.status_code == 403
        assert resp.json()["error"]["code"] == "forbidden_airport"


def test_restricted_key_with_airport_and_icao24_still_works(tmp_path, monkeypatch):
    """The combined airport+icao24 path must keep working: a restricted key can
    still follow a single aircraft within its own airport."""
    db_path = configure(tmp_path, monkeypatch)
    db.init_db(db_path)
    with db.db_session(db_path) as conn:
        klmo = db.get_airport(conn, "KLMO")
    assert klmo is not None
    min_lat, min_lon, max_lat, max_lon = bbox_for_radius(klmo.lat, klmo.lon, TRACK_RING_NM)
    assert min_lat <= klmo.lat <= max_lat
    assert min_lon <= klmo.lon <= max_lon

    with db.db_session(db_path) as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO track_archive
            (icao24, timestamp, lat, lon, altitude_ft, heading_deg,
             vertical_rate_fpm, callsign, in_window, source)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, 'test')
            """,
            ("d4e5f6", NOW, klmo.lat, klmo.lon, 5200.0, 200.0, 0.0, "N4KLMO"),
        )

    restricted = mint(db_path, scopes=["tracks:read"], airports=["KLMO"])
    with TestClient(app) as client:
        resp = client.get(
            "/v1/tracks",
            params={
                "airport": "KLMO",
                "icao24": "d4e5f6",
                "since": NOW - 5,
                "until": NOW + 5,
            },
            headers=auth(restricted),
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["data"], "expected the seeded KLMO sample to be returned"
        assert {row["icao24"] for row in body["data"]} == {"d4e5f6"}


def test_unrestricted_key_with_icao24_only_still_works(tmp_path, monkeypatch):
    """An unrestricted key's icao24-only behaviour must be unchanged by the fix."""
    db_path = configure(tmp_path, monkeypatch)
    seed_tracks(db_path, 1)
    key = mint(db_path, scopes=["tracks:read"], airports=None)
    with TestClient(app) as client:
        resp = client.get(
            "/v1/tracks",
            params={"icao24": "a1b2c3", "since": NOW - 5, "until": NOW + 5},
            headers=auth(key),
        )
        assert resp.status_code == 200
        assert resp.json()["data"][0]["icao24"] == "a1b2c3"


# test_tracks_by_airport_filters_to_the_ring_and_honours_restriction (above)
# already covers a restricted key being rejected with 403 forbidden_airport
# when it requests an airport it is not scoped to (KBJC, with a key restricted
# to KLMO) — not duplicated here.


def test_tracks_for_an_unknown_airport_is_404(tmp_path, monkeypatch):
    db_path = configure(tmp_path, monkeypatch)
    key = mint(db_path, scopes=["tracks:read"])
    with TestClient(app) as client:
        resp = client.get(
            "/v1/tracks",
            params={"airport": "ZZZZ", "since": NOW, "until": NOW + 10},
            headers=auth(key),
        )
        assert resp.status_code == 404
        assert resp.json()["error"]["code"] == "not_found"
