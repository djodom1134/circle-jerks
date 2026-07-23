from __future__ import annotations

import time

from app import db


def _seed_operation(conn, *, id, icao, ts, type="landing", runway="29"):
    conn.execute(
        "INSERT INTO operations (id, icao, icao24, type, timestamp, runway_id) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (id, icao, "a26f5e", type, ts, runway),
    )
    conn.commit()


def _cold_db(tmp_path):
    path = str(tmp_path / "cold.sqlite3")
    db.init_db(path)          # same schema as hot
    return path


def test_no_history_path_is_operational_only(tmp_path):
    hot = str(tmp_path / "hot.sqlite3"); db.init_db(hot)
    with db.connect(hot) as conn:
        _seed_operation(conn, id="h1", icao="KLMO", ts=1000)
        rows = db.read_operations_page(
            conn, icao="KLMO", start_ts=0, end_ts=2000, history_path=None,
            hot_cutoff_ts=None,
        )
    assert [r["id"] for r in rows] == ["h1"]


def test_cold_rows_older_than_the_cutoff_are_served(tmp_path):
    hot = str(tmp_path / "hot.sqlite3"); db.init_db(hot)
    cold = _cold_db(tmp_path)
    cutoff = 10_000
    with db.connect(cold) as cconn:
        _seed_operation(cconn, id="c1", icao="KLMO", ts=5_000)   # older than cutoff
    with db.connect(hot) as conn:
        _seed_operation(conn, id="h1", icao="KLMO", ts=15_000)   # newer than cutoff
        rows = db.read_operations_page(
            conn, icao="KLMO", start_ts=0, end_ts=20_000,
            history_path=cold, hot_cutoff_ts=cutoff,
        )
    # both the cold and the hot row, merged, ordered by (timestamp, id)
    assert [r["id"] for r in rows] == ["c1", "h1"]


def test_the_hot_cold_boundary_does_not_duplicate(tmp_path):
    # A row exactly at the cutoff belongs to hot (>= cutoff); the same id in cold
    # (< cutoff would be a different ts) must not both appear. Seed the boundary.
    hot = str(tmp_path / "hot.sqlite3"); db.init_db(hot)
    cold = _cold_db(tmp_path)
    cutoff = 10_000
    with db.connect(cold) as cconn:
        _seed_operation(cconn, id="c1", icao="KLMO", ts=9_999)   # last cold ts
    with db.connect(hot) as conn:
        _seed_operation(conn, id="h1", icao="KLMO", ts=10_000)   # first hot ts
        rows = db.read_operations_page(
            conn, icao="KLMO", start_ts=0, end_ts=20_000,
            history_path=cold, hot_cutoff_ts=cutoff,
        )
    ids = [r["id"] for r in rows]
    assert ids == ["c1", "h1"]
    assert len(ids) == len(set(ids))   # no duplication at the seam


def test_a_fully_recent_window_never_touches_cold(tmp_path):
    hot = str(tmp_path / "hot.sqlite3"); db.init_db(hot)
    cold = _cold_db(tmp_path)
    cutoff = 10_000
    with db.connect(cold) as cconn:
        _seed_operation(cconn, id="c1", icao="KLMO", ts=5_000)
    with db.connect(hot) as conn:
        _seed_operation(conn, id="h1", icao="KLMO", ts=15_000)
        # window starts after the cutoff: cold slice is empty by construction
        rows = db.read_operations_page(
            conn, icao="KLMO", start_ts=12_000, end_ts=20_000,
            history_path=cold, hot_cutoff_ts=cutoff,
        )
    assert [r["id"] for r in rows] == ["h1"]


def test_the_limit_applies_across_the_merged_page(tmp_path):
    hot = str(tmp_path / "hot.sqlite3"); db.init_db(hot)
    cold = _cold_db(tmp_path)
    cutoff = 10_000
    with db.connect(cold) as cconn:
        for i in range(5):
            _seed_operation(cconn, id=f"c{i}", icao="KLMO", ts=1_000 + i)
    with db.connect(hot) as conn:
        for i in range(5):
            _seed_operation(conn, id=f"h{i}", icao="KLMO", ts=15_000 + i)
        rows = db.read_operations_page(
            conn, icao="KLMO", start_ts=0, end_ts=20_000,
            history_path=cold, hot_cutoff_ts=cutoff, limit=3,
        )
    # ordered by (timestamp, id), the first 3 are the earliest cold rows
    assert [r["id"] for r in rows] == ["c0", "c1", "c2"]
