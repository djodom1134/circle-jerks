from __future__ import annotations

import sqlite3

import pytest

from app import db


def _seed_operation(conn, *, id, icao, ts, type="landing", runway="29"):
    conn.execute(
        "INSERT INTO operations (id, icao, icao24, type, timestamp, runway_id) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (id, icao, "a26f5e", type, ts, runway),
    )
    conn.commit()


def _seed_track(conn, *, icao24, ts, lat=40.16, lon=-105.16):
    conn.execute(
        "INSERT INTO track_archive (icao24, timestamp, lat, lon, source) "
        "VALUES (?, ?, ?, ?, ?)",
        (icao24, ts, lat, lon, "adsb"),
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


# --- read_track_archive_page: parallel coverage of its UNION ALL merge ------
#
# Structurally different from read_operations_page's two-fetch Python merge:
# this builds a single SQL `UNION ALL` over cold+hot with one `ORDER BY
# (timestamp, icao24) LIMIT`. Same boundary/limit/None-path behaviour, same
# ATTACH/DETACH lifecycle, but the merge itself happens inside SQLite rather
# than in Python, so it gets its own set of tests rather than reusing the
# operations ones.


def test_track_no_history_path_is_operational_only(tmp_path):
    hot = str(tmp_path / "hot.sqlite3"); db.init_db(hot)
    with db.connect(hot) as conn:
        _seed_track(conn, icao24="h1", ts=1000)
        rows = db.read_track_archive_page(
            conn, start_ts=0, end_ts=2000, history_path=None,
            hot_cutoff_ts=None,
        )
    assert [r["icao24"] for r in rows] == ["h1"]


def test_track_cold_rows_older_than_the_cutoff_are_served(tmp_path):
    hot = str(tmp_path / "hot.sqlite3"); db.init_db(hot)
    cold = _cold_db(tmp_path)
    cutoff = 10_000
    with db.connect(cold) as cconn:
        _seed_track(cconn, icao24="c1", ts=5_000)   # older than cutoff
    with db.connect(hot) as conn:
        _seed_track(conn, icao24="h1", ts=15_000)   # newer than cutoff
        rows = db.read_track_archive_page(
            conn, start_ts=0, end_ts=20_000,
            history_path=cold, hot_cutoff_ts=cutoff,
        )
    # both the cold and the hot row, merged, ordered by (timestamp, icao24)
    assert [r["icao24"] for r in rows] == ["c1", "h1"]


def test_track_the_hot_cold_boundary_does_not_duplicate(tmp_path):
    # A row exactly at the cutoff belongs to hot (>= cutoff); the cold slice's
    # upper bound must be *exclusive* of the cutoff so that row is never also
    # served from cold. To make that exclusivity actually observable, seed
    # the same (icao24, ts=cutoff) identity into BOTH stores -- simulating
    # archival copying the boundary row into cold just before/around the
    # moment it's pruned from hot, which does happen transiently in
    # production. With the boundary correct, cold's query never reaches
    # ts == cutoff, so this row is only ever served once (from hot).
    hot = str(tmp_path / "hot.sqlite3"); db.init_db(hot)
    cold = _cold_db(tmp_path)
    cutoff = 10_000
    with db.connect(cold) as cconn:
        _seed_track(cconn, icao24="c1", ts=9_999)     # last true cold ts
        _seed_track(cconn, icao24="h1", ts=10_000)    # archival-lag copy of the boundary row
    with db.connect(hot) as conn:
        _seed_track(conn, icao24="h1", ts=10_000)     # first hot ts (the "real" copy)
        rows = db.read_track_archive_page(
            conn, start_ts=0, end_ts=20_000,
            history_path=cold, hot_cutoff_ts=cutoff,
        )
    ids = [r["icao24"] for r in rows]
    assert ids == ["c1", "h1"]
    assert len(ids) == len(set(ids))   # no duplication at the seam


def test_track_a_fully_recent_window_never_touches_cold(tmp_path):
    hot = str(tmp_path / "hot.sqlite3"); db.init_db(hot)
    cold = _cold_db(tmp_path)
    cutoff = 10_000
    with db.connect(cold) as cconn:
        _seed_track(cconn, icao24="c1", ts=5_000)
    with db.connect(hot) as conn:
        _seed_track(conn, icao24="h1", ts=15_000)
        # window starts after the cutoff: cold slice is empty by construction
        rows = db.read_track_archive_page(
            conn, start_ts=12_000, end_ts=20_000,
            history_path=cold, hot_cutoff_ts=cutoff,
        )
    assert [r["icao24"] for r in rows] == ["h1"]


def test_track_the_limit_applies_across_the_merged_page(tmp_path):
    hot = str(tmp_path / "hot.sqlite3"); db.init_db(hot)
    cold = _cold_db(tmp_path)
    cutoff = 10_000
    with db.connect(cold) as cconn:
        for i in range(5):
            _seed_track(cconn, icao24=f"c{i}", ts=1_000 + i)
    with db.connect(hot) as conn:
        for i in range(5):
            _seed_track(conn, icao24=f"h{i}", ts=15_000 + i)
        rows = db.read_track_archive_page(
            conn, start_ts=0, end_ts=20_000,
            history_path=cold, hot_cutoff_ts=cutoff, limit=3,
        )
    # ordered by (timestamp, icao24), the first 3 are the earliest cold rows
    assert [r["icao24"] for r in rows] == ["c0", "c1", "c2"]


# --- keyset cursor across the hot/cold seam ---------------------------------
#
# The two slices are time-disjoint, so a cursor derived from a row served by
# one slice must still correctly resume into whichever slice serves the next
# rows -- including a page 1 entirely in cold resuming into a page 2 that
# itself straddles into hot.


def test_operations_cursor_resumes_correctly_across_the_hot_cold_seam(tmp_path):
    hot = str(tmp_path / "hot.sqlite3"); db.init_db(hot)
    cold = _cold_db(tmp_path)
    cutoff = 10_000
    with db.connect(cold) as cconn:
        for i in range(5):
            _seed_operation(cconn, id=f"c{i}", icao="KLMO", ts=1_000 + i)
    with db.connect(hot) as conn:
        for i in range(5):
            _seed_operation(conn, id=f"h{i}", icao="KLMO", ts=15_000 + i)

        full = db.read_operations_page(
            conn, icao="KLMO", start_ts=0, end_ts=20_000,
            history_path=cold, hot_cutoff_ts=cutoff, limit=100,
        )
        full_ids = [r["id"] for r in full]
        assert full_ids == ["c0", "c1", "c2", "c3", "c4", "h0", "h1", "h2", "h3", "h4"]

        # page 1: small limit, entirely within the cold slice
        page1 = db.read_operations_page(
            conn, icao="KLMO", start_ts=0, end_ts=20_000,
            history_path=cold, hot_cutoff_ts=cutoff, limit=3,
        )
        assert [r["id"] for r in page1] == ["c0", "c1", "c2"]

        last = page1[-1]
        after = (last["timestamp"], last["id"])

        # page 2: cursor resumes past the last cold row of page 1, spanning
        # the seam into the hot slice
        page2 = db.read_operations_page(
            conn, icao="KLMO", start_ts=0, end_ts=20_000,
            history_path=cold, hot_cutoff_ts=cutoff, limit=100, after=after,
        )
    page2_ids = [r["id"] for r in page2]
    assert page2_ids == ["c3", "c4", "h0", "h1", "h2", "h3", "h4"]
    # no overlap, no gap: page1 + page2 reconstructs the full ordered set
    assert [r["id"] for r in page1] + page2_ids == full_ids


def test_track_cursor_resumes_correctly_across_the_hot_cold_seam(tmp_path):
    hot = str(tmp_path / "hot.sqlite3"); db.init_db(hot)
    cold = _cold_db(tmp_path)
    cutoff = 10_000
    with db.connect(cold) as cconn:
        for i in range(5):
            _seed_track(cconn, icao24=f"c{i}", ts=1_000 + i)
    with db.connect(hot) as conn:
        for i in range(5):
            _seed_track(conn, icao24=f"h{i}", ts=15_000 + i)

        full = db.read_track_archive_page(
            conn, start_ts=0, end_ts=20_000,
            history_path=cold, hot_cutoff_ts=cutoff, limit=100,
        )
        full_ids = [r["icao24"] for r in full]
        assert full_ids == ["c0", "c1", "c2", "c3", "c4", "h0", "h1", "h2", "h3", "h4"]

        # page 1: small limit, entirely within the cold slice
        page1 = db.read_track_archive_page(
            conn, start_ts=0, end_ts=20_000,
            history_path=cold, hot_cutoff_ts=cutoff, limit=3,
        )
        assert [r["icao24"] for r in page1] == ["c0", "c1", "c2"]

        last = page1[-1]
        after = (last["timestamp"], last["icao24"])

        # page 2: cursor resumes past the last cold row of page 1, spanning
        # the seam into the hot slice
        page2 = db.read_track_archive_page(
            conn, start_ts=0, end_ts=20_000,
            history_path=cold, hot_cutoff_ts=cutoff, limit=100, after=after,
        )
    page2_ids = [r["icao24"] for r in page2]
    assert page2_ids == ["c3", "c4", "h0", "h1", "h2", "h3", "h4"]
    # no overlap, no gap: page1 + page2 reconstructs the full ordered set
    assert [r["icao24"] for r in page1] + page2_ids == full_ids


# --- ATTACH/DETACH lifecycle on a mid-merge query error ---------------------
#
# Both merges do `ATTACH ? AS hist ... finally: DETACH hist`. If the query
# against `hist` fails, DETACH must still run -- otherwise the next query on
# this connection sees a leaked `hist` schema, and any later merge on the
# same connection fails to re-ATTACH it.
#
# `bad_cold` below is a real, valid SQLite database (so ATTACH itself
# succeeds) that simply lacks the expected table, so the query against
# `hist.<table>` raises "no such table" mid-merge -- without ever touching
# db.py's actual merge logic.


def _valid_db_missing_the_expected_table(path):
    """A real, ATTACH-able SQLite file with no `operations`/`track_archive`
    table, so a query against `hist.<table>` raises after ATTACH succeeds."""
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE decoy (x INTEGER)")
    conn.commit()
    conn.close()


def test_operations_attach_is_detached_on_a_mid_merge_query_error(tmp_path):
    hot = str(tmp_path / "hot.sqlite3"); db.init_db(hot)
    cutoff = 10_000
    bad_cold = str(tmp_path / "bad_cold.sqlite3")
    _valid_db_missing_the_expected_table(bad_cold)

    with db.connect(hot) as conn:
        _seed_operation(conn, id="h1", icao="KLMO", ts=15_000)

        with pytest.raises(sqlite3.OperationalError):
            db.read_operations_page(
                conn, icao="KLMO", start_ts=0, end_ts=20_000,
                history_path=bad_cold, hot_cutoff_ts=cutoff,
            )

        # `hist` must have been DETACHed in the `finally` clause: a trivial
        # follow-up query on the SAME connection must still succeed.
        assert conn.execute("SELECT 1").fetchone()[0] == 1

        # And prove it wasn't just tolerant of a stray query: a second,
        # *valid* merge on the same connection must be able to re-ATTACH
        # `hist` at all -- which fails outright ("database hist is already
        # in use") if the first ATTACH leaked.
        cold = _cold_db(tmp_path)
        with db.connect(cold) as cconn:
            _seed_operation(cconn, id="c1", icao="KLMO", ts=5_000)
        rows = db.read_operations_page(
            conn, icao="KLMO", start_ts=0, end_ts=20_000,
            history_path=cold, hot_cutoff_ts=cutoff,
        )
    assert [r["id"] for r in rows] == ["c1", "h1"]


def test_track_attach_is_detached_on_a_mid_merge_query_error(tmp_path):
    hot = str(tmp_path / "hot.sqlite3"); db.init_db(hot)
    cutoff = 10_000
    bad_cold = str(tmp_path / "bad_cold_track.sqlite3")
    _valid_db_missing_the_expected_table(bad_cold)

    with db.connect(hot) as conn:
        _seed_track(conn, icao24="h1", ts=15_000)

        with pytest.raises(sqlite3.OperationalError):
            db.read_track_archive_page(
                conn, start_ts=0, end_ts=20_000,
                history_path=bad_cold, hot_cutoff_ts=cutoff,
            )

        # `hist` must have been DETACHed in the `finally` clause: a trivial
        # follow-up query on the SAME connection must still succeed.
        assert conn.execute("SELECT 1").fetchone()[0] == 1

        # And prove it wasn't just tolerant of a stray query: a second,
        # *valid* merge on the same connection must be able to re-ATTACH
        # `hist` at all -- which fails outright ("database hist is already
        # in use") if the first ATTACH leaked.
        cold = _cold_db(tmp_path)
        with db.connect(cold) as cconn:
            _seed_track(cconn, icao24="c1", ts=5_000)
        rows = db.read_track_archive_page(
            conn, start_ts=0, end_ts=20_000,
            history_path=cold, hot_cutoff_ts=cutoff,
        )
    assert [r["icao24"] for r in rows] == ["c1", "h1"]
