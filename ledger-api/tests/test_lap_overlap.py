"""Overlapping-lap suppression, against the real shape (see app/laps.py).

Every test here runs on a real SQLite file with the production schema — the same
two-database topology the service actually runs against (dbsupport.build_dbs).
"""
from __future__ import annotations

from app import fees, laps, ledger

from .dbsupport import build_dbs, lap, op

BASE = 1_780_000_000
N26CW = "a27ceb"

# THE REAL SHAPE. These are the circle rows the main API's detector actually
# writes for ONE aircraft flying EIGHT pattern laps at KLMO, recovered by replaying
# the production sliding-scan cadence over a reconstructed N26CW track: 8 laps
# flown, 14 rows written. Each physical lap resolves to two anchors — a ~330 s loop
# (the airborne circuit) and a ~580 s loop (the same circuit measured from the
# previous detection) — so the rows overlap each other in time, which is physically
# impossible and is what identifies them as duplicates.
#
# (timestamp, time_total_s) — timestamp is the lap's END, so it spans
# [timestamp - time_total_s, timestamp].
REAL_LAPS: list[tuple[int, int]] = [
    (320, 320), (390, 330), (900, 580), (990, 330), (1280, 580), (1590, 330),
    (1880, 580), (2190, 330), (2480, 580), (2790, 330), (3080, 580), (3390, 330),
    (3680, 580), (4280, 580),
]
REAL_LAPS_FLOWN = 8


def _seed_real_shape(setup_conn, icao24=N26CW):
    for i, (ts, duration) in enumerate(REAL_LAPS):
        lap(setup_conn, f"{icao24}-{i}", BASE + ts, icao24=icao24, duration_s=duration)


def test_the_real_shape_collapses_to_the_laps_that_could_have_been_flown(tmp_path):
    setup_conn, ro_conn, _rw = build_dbs(tmp_path)
    _seed_real_shape(setup_conn)

    phantoms = laps.phantom_lap_op_ids(ro_conn, "KLMO")
    kept = len(REAL_LAPS) - len(phantoms)

    # 14 detected rows for 8 real laps -> 7 kept. Never MORE than were flown: an
    # inflated count is the one error this site cannot survive.
    assert kept == 7
    assert kept <= REAL_LAPS_FLOWN


def test_no_kept_lap_overlaps_another(tmp_path):
    # The invariant the whole filter exists to establish: what survives could
    # actually have been flown by one aircraft.
    setup_conn, ro_conn, _rw = build_dbs(tmp_path)
    _seed_real_shape(setup_conn)
    phantoms = laps.phantom_lap_op_ids(ro_conn, "KLMO")

    spans = sorted(
        (BASE + ts - duration, BASE + ts)
        for i, (ts, duration) in enumerate(REAL_LAPS)
        if f"tg-{N26CW}-{i}" not in phantoms
    )
    for (_, earlier_end), (later_start, _) in zip(spans, spans[1:]):
        assert later_start >= earlier_end


def test_genuine_laps_are_never_suppressed(tmp_path):
    # A real aircraft flying real, separated laps must lose nothing. If this fails,
    # the filter is eating real runway uses and the site is under-reporting.
    setup_conn, ro_conn, _rw = build_dbs(tmp_path)
    for i in range(8):
        lap(setup_conn, f"clean-{i}", BASE + i * 600, icao24="clean1", duration_s=330)

    assert laps.phantom_lap_op_ids(ro_conn, "KLMO") == set()


def test_laps_are_suppressed_per_aircraft_never_across_them(tmp_path):
    # Two aircraft in the pattern together fly overlapping laps ALL THE TIME —
    # that is what a traffic pattern IS. Only one aircraft's laps can conflict.
    setup_conn, ro_conn, _rw = build_dbs(tmp_path)
    for i in range(3):
        lap(setup_conn, f"a-{i}", BASE + i * 600, icao24="aa11", duration_s=330)
        lap(setup_conn, f"b-{i}", BASE + i * 600 + 60, icao24="bb22", duration_s=330)

    assert laps.phantom_lap_op_ids(ro_conn, "KLMO") == set()


def test_landings_and_low_approaches_are_never_suppressed(tmp_path):
    # These come from the runway-contact episode detector, not from circles, so they
    # are not subject to circle re-detection and must survive untouched.
    setup_conn, ro_conn, _rw = build_dbs(tmp_path)
    _seed_real_shape(setup_conn)
    op(setup_conn, "land-1", "landing", BASE + 500, icao24=N26CW)
    op(setup_conn, "low-1", "low_approach", BASE + 900, icao24=N26CW)

    phantoms = laps.phantom_lap_op_ids(ro_conn, "KLMO")
    assert "land-1" not in phantoms
    assert "low-1" not in phantoms


def test_a_lap_with_no_recoverable_duration_is_kept(tmp_path):
    # `time_total_s` is only written when the lap matched a pattern, so it can be
    # NULL. We cannot prove such a lap overlaps anything, so we keep it: the filter
    # removes provable duplicates, it does not guess.
    setup_conn, ro_conn, _rw = build_dbs(tmp_path)
    op(setup_conn, "tg-orphan", "touch_and_go", BASE + 100, icao24="cc33")

    assert laps.phantom_lap_op_ids(ro_conn, "KLMO") == set()


def test_a_lap_that_ended_inside_a_kept_lap_is_suppressed_even_without_a_duration(tmp_path):
    # An unknown duration is a zero-length lap at its own timestamp. It cannot
    # suppress anything, but it can still be suppressed BY a kept lap whose span
    # covers it — a lap that ENDED in the middle of another lap is impossible
    # however long it was.
    setup_conn, ro_conn, _rw = build_dbs(tmp_path)
    lap(setup_conn, "real", BASE + 600, icao24="dd44", duration_s=330)   # spans [270, 600]
    op(setup_conn, "tg-inside", "touch_and_go", BASE + 400, icao24="dd44")

    assert laps.phantom_lap_op_ids(ro_conn, "KLMO") == {"tg-inside"}


# --- the filter reaches every published count -------------------------------


def test_the_rollup_counts_only_the_laps_that_could_have_been_flown(tmp_path):
    setup_conn, ro_conn, rw_conn = build_dbs(tmp_path)
    _seed_real_shape(setup_conn)

    ledger.rebuild_rollup(ro_conn, rw_conn, "KLMO", BASE, BASE + 5000)
    rw_conn.commit()
    totals = ledger.rollup_totals(
        rw_conn, "KLMO", "2026-05-01", "2026-08-01",
    )
    assert totals["by_type"]["touch_and_go"] == 7
    assert totals["runway_uses"] == 7


def test_the_projection_counts_only_the_laps_that_could_have_been_flown(tmp_path):
    # annual_projection reads `operations` directly, never the rollup, so it has to
    # suppress duplicates for itself.
    setup_conn, ro_conn, _rw = build_dbs(tmp_path)
    _seed_real_shape(setup_conn)

    projection = ledger.annual_projection(ro_conn, "KLMO", now_ts=BASE + 5000)
    assert projection["runway_uses_to_date"] == 7


def test_the_fee_ticker_counts_only_the_laps_that_could_have_been_flown(tmp_path):
    # fees.py also reads `operations` directly (the live map's price tags and the
    # hero ticker). An inflated tag here is a dollar figure attached to a tail
    # number on a public page.
    setup_conn, ro_conn, _rw = build_dbs(tmp_path)
    _seed_real_shape(setup_conn)

    payload = fees.build_aircraft_fees(ro_conn, "KLMO", now_ts=BASE + 5000)
    assert payload["aircraft"][N26CW]["total"] == 7
    assert payload["today"]["runway_uses"] == 7


def test_the_billable_unit_is_unchanged(tmp_path):
    # Runway uses = landing + touch_and_go + low_approach. Never takeoff. The
    # suppression must not have quietly changed what is billable.
    setup_conn, ro_conn, rw_conn = build_dbs(tmp_path)
    _seed_real_shape(setup_conn)
    op(setup_conn, "land-1", "landing", BASE + 5, icao24=N26CW)
    op(setup_conn, "low-1", "low_approach", BASE + 10, icao24=N26CW)
    op(setup_conn, "off-1", "takeoff", BASE + 15, icao24=N26CW)

    ledger.rebuild_rollup(ro_conn, rw_conn, "KLMO", BASE, BASE + 5000)
    rw_conn.commit()
    totals = ledger.rollup_totals(rw_conn, "KLMO", "2026-05-01", "2026-08-01")

    assert totals["runway_uses"] == 7 + 1 + 1        # 7 laps + landing + low approach
    assert totals["by_type"]["takeoff"] == 1         # counted, but never billable
