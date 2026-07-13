from __future__ import annotations

from app import db, dwell


def seeded_conn(path):
    conn = db.connect(str(path))
    conn.executescript(db.SCHEMA)
    db.seed_db(conn)
    conn.commit()
    return conn


def _op(conn, oid, type_, ts, icao24="a1"):
    db.upsert_operation(conn, db.operation_from_event({
        "id": oid, "type": type_, "icao24": icao24, "callsign": icao24.upper(),
        "timestamp": ts, "airport_icao": "KLMO",
    }))


BASE = 1780336800  # 2026-06-01 12:00 America/Denver
WINDOW = (BASE - 86400, BASE + 7 * 86400)


def test_pairs_landing_with_next_takeoff(tmp_path):
    conn = seeded_conn(tmp_path / "t.sqlite3")
    _op(conn, "l1", "landing", BASE)
    _op(conn, "t1", "takeoff", BASE + 900)   # 15 minutes on the ground
    conn.commit()

    intervals = dwell.dwell_intervals(conn, "KLMO", *WINDOW)
    assert len(intervals) == 1
    assert intervals[0]["seconds"] == 900
    assert intervals[0]["icao24"] == "a1"


def test_unpaired_landing_is_excluded_not_imputed(tmp_path):
    # An aircraft that landed and never departed within the window has no known
    # dwell. Guessing one would be inventing data.
    conn = seeded_conn(tmp_path / "t.sqlite3")
    _op(conn, "l1", "landing", BASE)
    conn.commit()
    assert dwell.dwell_intervals(conn, "KLMO", *WINDOW) == []


def test_takeoff_before_any_landing_is_ignored(tmp_path):
    # The aircraft was already on the field when the window opened. There is no
    # landing to pair it with.
    conn = seeded_conn(tmp_path / "t.sqlite3")
    _op(conn, "t1", "takeoff", BASE)
    _op(conn, "l1", "landing", BASE + 3600)
    conn.commit()
    assert dwell.dwell_intervals(conn, "KLMO", *WINDOW) == []


def test_second_landing_without_intervening_takeoff_supersedes_the_first(tmp_path):
    # Two landings then one takeoff means we missed a departure. Pair the takeoff
    # with the MOST RECENT landing; the earlier one is unpaired and dropped.
    conn = seeded_conn(tmp_path / "t.sqlite3")
    _op(conn, "l1", "landing", BASE)
    _op(conn, "l2", "landing", BASE + 3600)
    _op(conn, "t1", "takeoff", BASE + 3600 + 600)
    conn.commit()

    intervals = dwell.dwell_intervals(conn, "KLMO", *WINDOW)
    assert len(intervals) == 1
    assert intervals[0]["landing_ts"] == BASE + 3600
    assert intervals[0]["seconds"] == 600


def test_pairing_is_per_aircraft(tmp_path):
    conn = seeded_conn(tmp_path / "t.sqlite3")
    _op(conn, "l1", "landing", BASE, icao24="aaa111")
    _op(conn, "l2", "landing", BASE + 60, icao24="bbb222")
    _op(conn, "t2", "takeoff", BASE + 120, icao24="bbb222")   # bbb222: 60s
    _op(conn, "t1", "takeoff", BASE + 300, icao24="aaa111")   # aaa111: 300s
    conn.commit()

    by_ac = {i["icao24"]: i["seconds"] for i in dwell.dwell_intervals(conn, "KLMO", *WINDOW)}
    assert by_ac == {"aaa111": 300, "bbb222": 60}


def test_summary_excludes_overnight_stays_from_the_median(tmp_path):
    # A based aircraft parked for two days is not a "visit". Including it would
    # drag the median time-on-field into meaninglessness.
    conn = seeded_conn(tmp_path / "t.sqlite3")
    _op(conn, "l1", "landing", BASE, icao24="aaa111")
    _op(conn, "t1", "takeoff", BASE + 600, icao24="aaa111")            # 10 min
    _op(conn, "l2", "landing", BASE, icao24="bbb222")
    _op(conn, "t2", "takeoff", BASE + 1800, icao24="bbb222")           # 30 min
    _op(conn, "l3", "landing", BASE, icao24="ccc333")
    _op(conn, "t3", "takeoff", BASE + 2 * 86400, icao24="ccc333")      # 2 days
    conn.commit()

    summary = dwell.dwell_summary(conn, "KLMO", *WINDOW)
    assert summary["sample_size"] == 2          # the 2-day stay is out
    assert summary["median_seconds"] == 1200    # mean of 600 and 1800


def test_summary_reports_coverage_and_never_divides_by_zero(tmp_path):
    conn = seeded_conn(tmp_path / "t.sqlite3")
    _op(conn, "l1", "landing", BASE, icao24="aaa111")
    _op(conn, "t1", "takeoff", BASE + 600, icao24="aaa111")
    _op(conn, "l2", "landing", BASE, icao24="bbb222")   # never departs
    conn.commit()

    summary = dwell.dwell_summary(conn, "KLMO", *WINDOW)
    assert summary["landings"] == 2
    assert summary["sample_size"] == 1
    assert summary["coverage"] == 0.5

    empty = seeded_conn(tmp_path / "e.sqlite3")
    blank = dwell.dwell_summary(empty, "KLMO", *WINDOW)
    assert blank["median_seconds"] is None
    assert blank["coverage"] == 0.0
