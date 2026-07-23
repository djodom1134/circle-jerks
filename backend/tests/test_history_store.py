from __future__ import annotations

import sqlite3

from app import history_store
from app.settings import Settings


def _settings(tmp_path, path=None, airports=("KLMO",)):
    return Settings(
        history_database_path=path,
        permanent_history_airports=list(airports),
        track_archive_horizon_days=7,
    )


def test_available_is_false_when_unconfigured(tmp_path):
    assert history_store.available(_settings(tmp_path, path=None)) is False


def test_available_is_false_when_the_file_is_missing(tmp_path):
    missing = str(tmp_path / "nope.sqlite3")
    assert history_store.available(_settings(tmp_path, path=missing)) is False


def test_available_is_false_for_an_empty_file(tmp_path):
    empty = tmp_path / "empty.sqlite3"
    empty.write_bytes(b"")
    assert history_store.available(_settings(tmp_path, path=str(empty))) is False


def test_available_is_true_for_a_populated_file(tmp_path):
    db = tmp_path / "cold.sqlite3"
    sqlite3.connect(str(db)).execute("CREATE TABLE t (x)").connection.commit()
    assert history_store.available(_settings(tmp_path, path=str(db))) is True


def test_airport_allowed_gates_on_the_allowlist(tmp_path):
    s = _settings(tmp_path, airports=("KLMO",))
    assert history_store.airport_allowed("KLMO", s) is True
    assert history_store.airport_allowed("klmo", s) is True   # case-insensitive
    assert history_store.airport_allowed("KBJC", s) is False


def test_airport_allowed_permits_an_icao24_only_query(tmp_path):
    # A query with no airport carries None; the cold store is airport-scoped by
    # its own contents, so those are safe to serve. This is NOT a grant bypass —
    # the /v1/tracks handler still forces `airport` for restricted keys before
    # this is ever reached.
    assert history_store.airport_allowed(None, _settings(tmp_path)) is True


def test_horizon_seconds_is_days_times_86400(tmp_path):
    assert history_store.horizon_seconds(_settings(tmp_path)) == 7 * 86400
