from __future__ import annotations

import pytest

from app import db, ledger

from .dbsupport import build_dbs, op as _op

# 2026-06-01 12:00:00 America/Denver == 1780336800 UTC
DAY1_NOON = 1780336800
DAY = 86400


def test_rollup_counts_runway_uses_and_excludes_takeoff(tmp_path):
    setup, ro, rw = build_dbs(tmp_path)
    _op(setup, "l1", "landing", DAY1_NOON)
    _op(setup, "g1", "touch_and_go", DAY1_NOON + 60)
    _op(setup, "a1", "low_approach", DAY1_NOON + 120)
    _op(setup, "t1", "takeoff", DAY1_NOON + 180)   # rolled up, but NOT a runway use
    _op(setup, "c1", "circle", DAY1_NOON + 240)    # not rolled up at all

    written = ledger.rebuild_rollup(ro, rw, "KLMO", DAY1_NOON - DAY, DAY1_NOON + DAY)
    rw.commit()
    assert written == 4  # circle excluded

    totals = ledger.rollup_totals(rw, "KLMO", "2026-06-01", "2026-06-01")
    assert totals["runway_uses"] == 3            # takeoff excluded from the billable unit
    assert totals["by_type"]["landing"] == 1
    assert totals["by_type"]["touch_and_go"] == 1
    assert totals["by_type"]["low_approach"] == 1
    assert totals["unique_aircraft"] == 1


def test_rollup_buckets_by_local_day_not_utc_day(tmp_path):
    setup, ro, rw = build_dbs(tmp_path)
    # 2026-06-02 01:00 UTC is still 2026-06-01 19:00 in America/Denver.
    ts = 1780362000  # 2026-06-02T01:00:00Z
    assert db.local_day_key(ts, "America/Denver") == "2026-06-01"
    _op(setup, "l1", "landing", ts)

    ledger.rebuild_rollup(ro, rw, "KLMO", ts - DAY, ts + DAY)
    rw.commit()

    assert ledger.rollup_totals(rw, "KLMO", "2026-06-01", "2026-06-01")["runway_uses"] == 1
    assert ledger.rollup_totals(rw, "KLMO", "2026-06-02", "2026-06-02")["runway_uses"] == 0


def test_rebuild_is_idempotent(tmp_path):
    setup, ro, rw = build_dbs(tmp_path)
    _op(setup, "l1", "landing", DAY1_NOON)
    ledger.rebuild_rollup(ro, rw, "KLMO", DAY1_NOON - DAY, DAY1_NOON + DAY)
    ledger.rebuild_rollup(ro, rw, "KLMO", DAY1_NOON - DAY, DAY1_NOON + DAY)
    rw.commit()
    assert ledger.rollup_totals(rw, "KLMO", "2026-06-01", "2026-06-01")["runway_uses"] == 1


def test_rebuild_drops_rows_for_deleted_operations(tmp_path):
    # The maintenance scripts on the main API DELETE FROM operations. A rebuild
    # must not leave orphaned counts behind.
    setup, ro, rw = build_dbs(tmp_path)
    _op(setup, "l1", "landing", DAY1_NOON)
    ledger.rebuild_rollup(ro, rw, "KLMO", DAY1_NOON - DAY, DAY1_NOON + DAY)
    rw.commit()

    setup.execute("DELETE FROM operations WHERE id='l1'")
    setup.commit()
    ledger.rebuild_rollup(ro, rw, "KLMO", DAY1_NOON - DAY, DAY1_NOON + DAY)
    rw.commit()

    assert ledger.rollup_totals(rw, "KLMO", "2026-06-01", "2026-06-01")["runway_uses"] == 0


def test_daily_series_gap_fills_with_zeroes(tmp_path):
    setup, ro, rw = build_dbs(tmp_path)
    _op(setup, "l1", "landing", DAY1_NOON)
    _op(setup, "l2", "landing", DAY1_NOON + 2 * DAY)  # skip 2026-06-02
    ledger.rebuild_rollup(ro, rw, "KLMO", DAY1_NOON - DAY, DAY1_NOON + 3 * DAY)
    rw.commit()

    series = ledger.rollup_daily_runway_uses(rw, "KLMO", "2026-06-01", "2026-06-03")
    assert [d["date"] for d in series] == ["2026-06-01", "2026-06-02", "2026-06-03"]
    assert [d["runway_uses"] for d in series] == [1, 0, 1]


def test_rebuild_survives_a_second_non_midnight_aligned_overlapping_window(tmp_path):
    # Regression for the undercount bug: rebuild_rollup's DELETE clears whole
    # LOCAL days, but a prior version of the reinsert only re-read the raw
    # [start_ts, end_ts] slice. A second, non-midnight-aligned call that still
    # overlaps an already-correct day (e.g. a nightly "reprocess the last 24h"
    # job) must not silently zero out counts outside its slice but inside the
    # deleted day.
    setup, ro, rw = build_dbs(tmp_path)
    midnight_0601 = DAY1_NOON - 12 * 3600  # 2026-06-01 00:00:00 America/Denver
    _op(setup, "l1", "landing", midnight_0601 + 30 * 60)  # 00:30 local

    # First rebuild: exactly midnight-aligned for 2026-06-01. Correctly counts 1.
    ledger.rebuild_rollup(ro, rw, "KLMO", midnight_0601, midnight_0601 + DAY)
    rw.commit()
    assert ledger.rollup_totals(rw, "KLMO", "2026-06-01", "2026-06-01")["runway_uses"] == 1

    # Second rebuild: a plausible "reprocess the last 24 hours" window that
    # starts at noon rather than local midnight, but still overlaps 2026-06-01.
    ledger.rebuild_rollup(ro, rw, "KLMO", DAY1_NOON, DAY1_NOON + DAY)
    rw.commit()

    assert ledger.rollup_totals(rw, "KLMO", "2026-06-01", "2026-06-01")["runway_uses"] == 1


def test_local_days_between_raises_on_swapped_start_and_end(tmp_path):
    # A caller bug (swapped args) must surface loudly, not silently rebuild nothing.
    with pytest.raises(ValueError):
        ledger._local_days_between(DAY1_NOON, DAY1_NOON - DAY, "America/Denver")


def test_unique_aircraft_counts_distinct_icao24(tmp_path):
    setup, ro, rw = build_dbs(tmp_path)
    _op(setup, "l1", "landing", DAY1_NOON, icao24="aaa111")
    _op(setup, "l2", "landing", DAY1_NOON + 60, icao24="aaa111")
    _op(setup, "l3", "landing", DAY1_NOON + 120, icao24="bbb222")
    ledger.rebuild_rollup(ro, rw, "KLMO", DAY1_NOON - DAY, DAY1_NOON + DAY)
    rw.commit()

    totals = ledger.rollup_totals(rw, "KLMO", "2026-06-01", "2026-06-01")
    assert totals["runway_uses"] == 3
    assert totals["unique_aircraft"] == 2
