from __future__ import annotations

import json
import sqlite3

import pytest
from fastapi.testclient import TestClient

from app import fees
from app.main import app, settings_dep
from app.settings import Settings

from .dbsupport import KLMO_SEED, PROD_TEST_SCHEMA

DAY = 86400
# 2026-07-13 12:00:00 America/Denver (UTC-6) == 18:00:00 UTC.
NOW = 1783965600


@pytest.fixture
def client(tmp_path):
    prod_path = str(tmp_path / "prod.sqlite3")
    ledger_path = str(tmp_path / "ledger.sqlite3")

    setup = sqlite3.connect(prod_path)
    setup.row_factory = sqlite3.Row
    setup.executescript(PROD_TEST_SCHEMA)
    setup.execute(
        "INSERT INTO airports (icao, iata, name, city, country, lat, lon, elevation_ft, is_towered, timezone) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        KLMO_SEED,
    )
    setup.execute(
        "INSERT INTO aircraft_registry (n_number, icao_hex, registrant_name, "
        "  registrant_street, registrant_city, registrant_state, registrant_zip) "
        "VALUES ('N111AA','AAA111','SOMEBODY PRIVATE','123 Main St','Boulder','CO','80301')"
    )
    setup.commit()
    setup.close()

    def _settings():
        return Settings(production_database_path=prod_path, ledger_database_path=ledger_path)

    app.dependency_overrides[settings_dep] = _settings
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def _insert_op(tmp_path, oid, type_, ts, icao24="aaa111", callsign="N111AA", icao="KLMO"):
    conn = sqlite3.connect(str(tmp_path / "prod.sqlite3"))
    conn.execute(
        "INSERT INTO operations (id, icao, icao24, callsign, type, timestamp) VALUES (?, ?, ?, ?, ?, ?)",
        (oid, icao, icao24, callsign, type_, ts),
    )
    conn.commit()
    conn.close()


def test_unknown_airport_is_404(client):
    assert client.get("/airports/ZZZZ/aircraft-fees").status_code == 404


def test_takeoff_is_never_counted(client, tmp_path):
    _insert_op(tmp_path, "t1", "takeoff", NOW - 60)
    _insert_op(tmp_path, "l1", "landing", NOW - 120)
    body = client.get(f"/airports/KLMO/aircraft-fees?_now={NOW}").json()

    assert body["today"]["runway_uses"] == 1  # the landing only, never the takeoff
    aircraft = body["aircraft"]["aaa111"]
    assert aircraft["today"] == 1
    assert aircraft["total"] == 1
    assert aircraft["month"] == 1
    assert aircraft["year"] == 1
    # The takeoff must never leak into the ticker's tick-rate window either.
    assert body["rate_window"]["runway_uses"] == 1


def test_rate_window_excludes_events_older_than_20_minutes(client, tmp_path):
    _insert_op(tmp_path, "old", "landing", NOW - 21 * 60, icao24="a1")
    _insert_op(tmp_path, "recent", "landing", NOW - 5 * 60, icao24="a2")
    body = client.get(f"/airports/KLMO/aircraft-fees?_now={NOW}").json()

    assert body["rate_window"]["seconds"] == 1200
    assert body["rate_window"]["runway_uses"] == 1
    assert body["rate_window"]["uses_per_second"] == pytest.approx(1 / 1200)


def test_rate_window_is_zero_with_no_recent_traffic(client, tmp_path):
    # Old activity exists (so `total`/`today` are nonzero) but nothing in the
    # last 20 minutes -- the debt-clock's rate must be exactly zero, not some
    # long-run average that papers over a currently-quiet pattern.
    _insert_op(tmp_path, "old", "landing", NOW - 3600, icao24="a1")
    body = client.get(f"/airports/KLMO/aircraft-fees?_now={NOW}").json()
    assert body["rate_window"]["runway_uses"] == 0
    assert body["rate_window"]["uses_per_second"] == 0.0


def test_rolling_24h_excludes_25_hours_ago_and_includes_23_hours_ago(client, tmp_path):
    _insert_op(tmp_path, "old", "landing", NOW - 25 * 3600, icao24="a1", callsign="N001AA")
    _insert_op(tmp_path, "recent", "landing", NOW - 23 * 3600, icao24="a2", callsign="N002AA")
    body = client.get(f"/airports/KLMO/aircraft-fees?_now={NOW}").json()

    assert body["today"]["runway_uses"] == 1
    assert body["today"]["window"] == "rolling_24h"
    assert body["today"]["since_ts"] == NOW - 24 * 3600
    assert body["today"]["until_ts"] == NOW

    # The 25-hour-old aircraft still appears (it has a `total`), but carries
    # no rolling-window count -- excluded from the ticker, not deleted from
    # the dataset.
    assert body["aircraft"]["a1"]["today"] == 0
    assert body["aircraft"]["a1"]["total"] == 1
    assert body["aircraft"]["a2"]["today"] == 1
    assert body["aircraft"]["a2"]["total"] == 1


def test_rolling_window_is_not_a_local_calendar_day(client, tmp_path):
    # NOW is 2026-07-13 12:00 America/Denver. 2026-07-12 22:00 America/Denver
    # (ts 1783915200) is YESTERDAY's local calendar date but only 14 hours
    # before NOW -- well inside the trailing-24h window. If `today` were
    # bucketed by db.local_day_key (a local CALENDAR day) instead of a
    # rolling window, this event would be excluded; it must not be.
    _insert_op(tmp_path, "yesterday_but_recent", "landing", 1783915200, icao24="a3")
    body = client.get(f"/airports/KLMO/aircraft-fees?_now={NOW}").json()
    assert body["aircraft"]["a3"]["today"] == 1


def test_local_calendar_month_and_year_bucketing(client, tmp_path):
    # NOW = 2026-07-13 12:00 America/Denver. An operation on 2026-07-01 local
    # (start of the current month) counts toward `month` and `year`; one on
    # 2026-06-30 local (previous month, same year... actually previous month)
    # counts toward neither; one on 2025-12-31 local (previous year) counts
    # toward neither.
    july_1_local_noon_utc = 1782928800  # 2026-07-01 12:00 America/Denver
    june_30_local_utc = 1782860400      # 2026-06-30 17:00 America/Denver (June 30 local)

    _insert_op(tmp_path, "jul1", "landing", july_1_local_noon_utc, icao24="m1")
    _insert_op(tmp_path, "jun30", "landing", june_30_local_utc, icao24="m2")

    body = client.get(f"/airports/KLMO/aircraft-fees?_now={NOW}").json()
    assert body["aircraft"]["m1"]["month"] == 1
    assert body["aircraft"]["m1"]["year"] == 1
    assert body["aircraft"]["m2"]["month"] == 0  # June, not July -- excluded from month-to-date
    assert body["aircraft"]["m2"]["year"] == 1   # still 2026 -- included in year-to-date


def test_counting_since_is_min_timestamp_across_all_operation_types(client, tmp_path):
    _insert_op(tmp_path, "earliest_takeoff", "takeoff", NOW - 30 * DAY)
    _insert_op(tmp_path, "later_landing", "landing", NOW - 5 * DAY)
    body = client.get(f"/airports/KLMO/aircraft-fees?_now={NOW}").json()
    # counting_since is MIN(timestamp) over ALL operations -- including the
    # takeoff, which is not itself billable but still marks when we started
    # observing this airport at all.
    assert body["counting_since"] == NOW - 30 * DAY


def test_payload_never_carries_registrant_or_owner_fields(client, tmp_path):
    _insert_op(tmp_path, "l1", "landing", NOW - 60, icao24="aaa111", callsign="N111AA")
    body = client.get(f"/airports/KLMO/aircraft-fees?_now={NOW}").json()
    raw = json.dumps(body).lower()

    for forbidden in ("registrant", "registrant_name", "owner", "street", "address", "somebody private", "main st"):
        assert forbidden not in raw, f"leaked {forbidden!r} into the aircraft-fees payload"

    assert body["aircraft"]["aaa111"]["tail"] == "N111AA"


def test_aircraft_never_detected_using_the_runway_is_absent_not_zero(client, tmp_path):
    # A takeoff-only aircraft (never a runway use) must not appear in
    # `aircraft` at all -- "we've never seen this aircraft use the runway" is
    # a different fact than "this aircraft owes $0", and the map/tooltip
    # logic depends on that distinction (see lostlanding's LiveMap: absence of
    # an entry means no price tag is drawn).
    _insert_op(tmp_path, "t1", "takeoff", NOW - 60, icao24="ghost")
    body = client.get(f"/airports/KLMO/aircraft-fees?_now={NOW}").json()
    assert "ghost" not in body["aircraft"]
