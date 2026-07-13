from __future__ import annotations

from app import dwell

from .dbsupport import build_dbs, op as _op

BASE = 1780336800  # 2026-06-01 12:00 America/Denver
WINDOW = (BASE - 86400, BASE + 7 * 86400)


def test_pairs_landing_with_next_takeoff(tmp_path):
    setup, ro, rw = build_dbs(tmp_path)
    _op(setup, "l1", "landing", BASE)
    _op(setup, "t1", "takeoff", BASE + 900)   # 15 minutes on the ground

    intervals = dwell.dwell_intervals(ro, "KLMO", *WINDOW)
    assert len(intervals) == 1
    assert intervals[0]["seconds"] == 900
    assert intervals[0]["icao24"] == "a1"


def test_unpaired_landing_is_excluded_not_imputed(tmp_path):
    # An aircraft that landed and never departed within the window has no known
    # dwell. Guessing one would be inventing data.
    setup, ro, rw = build_dbs(tmp_path)
    _op(setup, "l1", "landing", BASE)
    assert dwell.dwell_intervals(ro, "KLMO", *WINDOW) == []


def test_takeoff_before_any_landing_is_ignored(tmp_path):
    # The aircraft was already on the field when the window opened. There is no
    # landing to pair it with.
    setup, ro, rw = build_dbs(tmp_path)
    _op(setup, "t1", "takeoff", BASE)
    _op(setup, "l1", "landing", BASE + 3600)
    assert dwell.dwell_intervals(ro, "KLMO", *WINDOW) == []


def test_second_landing_without_intervening_takeoff_supersedes_the_first(tmp_path):
    # Two landings then one takeoff means we missed a departure. Pair the takeoff
    # with the MOST RECENT landing; the earlier one is unpaired and dropped.
    setup, ro, rw = build_dbs(tmp_path)
    _op(setup, "l1", "landing", BASE)
    _op(setup, "l2", "landing", BASE + 3600)
    _op(setup, "t1", "takeoff", BASE + 3600 + 600)

    intervals = dwell.dwell_intervals(ro, "KLMO", *WINDOW)
    assert len(intervals) == 1
    assert intervals[0]["landing_ts"] == BASE + 3600
    assert intervals[0]["seconds"] == 600


def test_pairing_is_per_aircraft(tmp_path):
    setup, ro, rw = build_dbs(tmp_path)
    _op(setup, "l1", "landing", BASE, icao24="aaa111")
    _op(setup, "l2", "landing", BASE + 60, icao24="bbb222")
    _op(setup, "t2", "takeoff", BASE + 120, icao24="bbb222")   # bbb222: 60s
    _op(setup, "t1", "takeoff", BASE + 300, icao24="aaa111")   # aaa111: 300s

    by_ac = {i["icao24"]: i["seconds"] for i in dwell.dwell_intervals(ro, "KLMO", *WINDOW)}
    assert by_ac == {"aaa111": 300, "bbb222": 60}


def test_summary_excludes_overnight_stays_from_the_median(tmp_path):
    # A based aircraft parked for two days is not a "visit". Including it would
    # drag the median time-on-field into meaninglessness.
    setup, ro, rw = build_dbs(tmp_path)
    _op(setup, "l1", "landing", BASE, icao24="aaa111")
    _op(setup, "t1", "takeoff", BASE + 600, icao24="aaa111")            # 10 min
    _op(setup, "l2", "landing", BASE, icao24="bbb222")
    _op(setup, "t2", "takeoff", BASE + 1800, icao24="bbb222")           # 30 min
    _op(setup, "l3", "landing", BASE, icao24="ccc333")
    _op(setup, "t3", "takeoff", BASE + 2 * 86400, icao24="ccc333")      # 2 days

    summary = dwell.dwell_summary(ro, "KLMO", *WINDOW)
    assert summary["sample_size"] == 2          # the 2-day stay is out
    assert summary["median_seconds"] == 1200    # mean of 600 and 1800


def test_takeoff_just_after_window_end_still_pairs(tmp_path):
    # Reviewer-reported boundary bug: a landing lands just before end_ts, and its
    # takeoff — an utterly ordinary 600-second visit — falls just after end_ts.
    # The old code bounded BOTH sides of the pair to [start_ts, end_ts], so this
    # takeoff was silently excluded even though the row exists, censoring the
    # landing as unpaired. Long dwells straddling the boundary were disproportionately
    # dropped, biasing the published median toward short stays. The takeoff side
    # must now look ahead past end_ts (up to the lookahead/max_seconds horizon).
    setup, ro, rw = build_dbs(tmp_path)
    start_ts = BASE
    end_ts = BASE + 3600

    # Aircraft A: an ordinary flight entirely inside the window -> 1200s dwell.
    _op(setup, "l1", "landing", BASE + 100, icao24="aaa111")
    _op(setup, "t1", "takeoff", BASE + 100 + 1200, icao24="aaa111")

    # Aircraft B: lands 100s before end_ts, takes off 500s after end_ts.
    _op(setup, "l2", "landing", end_ts - 100, icao24="bbb222")
    _op(setup, "t2", "takeoff", end_ts + 500, icao24="bbb222")

    summary = dwell.dwell_summary(ro, "KLMO", start_ts, end_ts)
    assert summary == {
        "median_seconds": 900,   # mean of 1200 and 600
        "sample_size": 2,
        "landings": 2,
        "coverage": 1.0,
    }


def test_based_aircraft_beyond_lookahead_horizon_still_unpaired(tmp_path):
    # The takeoff-side widening must have a horizon. An aircraft that lands near
    # end_ts and doesn't depart for 3 days is based/overnight, not mid-visit, and
    # must NOT be scooped up by the lookahead meant to catch a boundary-straddling
    # ordinary visit.
    setup, ro, rw = build_dbs(tmp_path)
    start_ts = BASE
    end_ts = BASE + 3600

    _op(setup, "l1", "landing", end_ts - 100, icao24="ccc333")
    _op(setup, "t1", "takeoff", end_ts + 3 * 86400, icao24="ccc333")  # 3 days later

    assert dwell.dwell_intervals(ro, "KLMO", start_ts, end_ts) == []


def test_summary_reports_coverage_and_never_divides_by_zero(tmp_path):
    setup, ro, rw = build_dbs(tmp_path)
    _op(setup, "l1", "landing", BASE, icao24="aaa111")
    _op(setup, "t1", "takeoff", BASE + 600, icao24="aaa111")
    _op(setup, "l2", "landing", BASE, icao24="bbb222")   # never departs

    summary = dwell.dwell_summary(ro, "KLMO", *WINDOW)
    assert summary["landings"] == 2
    assert summary["sample_size"] == 1
    assert summary["coverage"] == 0.5

    empty_dir = tmp_path / "empty"
    empty_dir.mkdir()
    _, empty_ro, _ = build_dbs(empty_dir)
    blank = dwell.dwell_summary(empty_ro, "KLMO", *WINDOW)
    assert blank["median_seconds"] is None
    assert blank["coverage"] == 0.0
