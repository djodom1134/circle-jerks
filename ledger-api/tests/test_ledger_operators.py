from __future__ import annotations

from app import db, homebase, ledger

from .dbsupport import build_dbs, op as _op, register as _register

BASE = 1780336800
DAY = 86400


def test_flight_school_is_named(tmp_path):
    setup, ro, rw = build_dbs(tmp_path)
    _register(setup, "N111AA", "aaa111", "BOULDER FLIGHT SCHOOL LLC")
    for i in range(5):
        _op(setup, f"g{i}", "touch_and_go", BASE + i * 60, "aaa111", callsign="N111AA")
    ledger.rebuild_rollup(ro, rw, "KLMO", BASE - DAY, BASE + DAY)
    rw.commit()

    rows = ledger.operator_ledger(ro, rw, "KLMO", "2026-06-01", "2026-06-01")
    assert len(rows) == 1
    assert rows[0]["operator"] == "BOULDER FLIGHT SCHOOL LLC"
    assert rows[0]["owner_type"] == "flight_school"
    assert rows[0]["runway_uses"] == 5
    assert rows[0]["aircraft_count"] == 1


def test_private_individual_is_never_named(tmp_path):
    # The registry gives us a person's name and home address. We use neither.
    setup, ro, rw = build_dbs(tmp_path)
    _register(setup, "N222BB", "bbb222", "JOHN Q SMITH")
    _op(setup, "l1", "landing", BASE, "bbb222", callsign="N222BB")
    ledger.rebuild_rollup(ro, rw, "KLMO", BASE - DAY, BASE + DAY)
    rw.commit()

    rows = ledger.operator_ledger(ro, rw, "KLMO", "2026-06-01", "2026-06-01")
    assert rows[0]["operator"] == ledger.PRIVATE_BUCKET
    assert "SMITH" not in str(rows)


def test_single_member_llc_is_not_named(tmp_path):
    # "JOHN SMITH AVIATION LLC" is a legal entity that is also, effectively, a
    # person's name. LLC and trust are NOT nameable — they are the standard way an
    # individual holds a personal aircraft.
    setup, ro, rw = build_dbs(tmp_path)
    _register(setup, "N333CC", "ccc333", "SMITH AVIATION LLC")
    _op(setup, "l1", "landing", BASE, "ccc333", callsign="N333CC")
    ledger.rebuild_rollup(ro, rw, "KLMO", BASE - DAY, BASE + DAY)
    rw.commit()

    rows = ledger.operator_ledger(ro, rw, "KLMO", "2026-06-01", "2026-06-01")
    assert rows[0]["operator"] == ledger.PRIVATE_BUCKET


def test_no_registrant_address_ever_appears_in_output(tmp_path):
    setup, ro, rw = build_dbs(tmp_path)
    _register(setup, "N111AA", "aaa111", "BOULDER FLIGHT SCHOOL LLC", city="Boulder", state="CO")
    _op(setup, "g1", "touch_and_go", BASE, "aaa111", callsign="N111AA")
    ledger.rebuild_rollup(ro, rw, "KLMO", BASE - DAY, BASE + DAY)
    rw.commit()

    rows = ledger.operator_ledger(ro, rw, "KLMO", "2026-06-01", "2026-06-01")
    assert "registrant_street" not in rows[0]
    assert "registrant_city" not in rows[0]


def test_aircraft_are_grouped_under_one_operator(tmp_path):
    setup, ro, rw = build_dbs(tmp_path)
    _register(setup, "N111AA", "aaa111", "BOULDER FLIGHT SCHOOL LLC")
    _register(setup, "N111AB", "aaa112", "BOULDER FLIGHT SCHOOL LLC")
    _op(setup, "g1", "touch_and_go", BASE, "aaa111", callsign="N111AA")
    _op(setup, "g2", "touch_and_go", BASE + 60, "aaa112", callsign="N111AB")
    _op(setup, "g3", "touch_and_go", BASE + 120, "aaa112", callsign="N111AB")
    ledger.rebuild_rollup(ro, rw, "KLMO", BASE - DAY, BASE + DAY)
    rw.commit()

    rows = ledger.operator_ledger(ro, rw, "KLMO", "2026-06-01", "2026-06-01")
    assert len(rows) == 1
    assert rows[0]["runway_uses"] == 3
    assert rows[0]["aircraft_count"] == 2
    assert sorted(a["tail"] for a in rows[0]["aircraft"]) == ["N111AA", "N111AB"]


def test_ranked_by_runway_uses_descending(tmp_path):
    setup, ro, rw = build_dbs(tmp_path)
    _register(setup, "N111AA", "aaa111", "SMALL FLYING CLUB")
    _register(setup, "N222BB", "bbb222", "BIG FLIGHT SCHOOL LLC")
    _op(setup, "g1", "touch_and_go", BASE, "aaa111", callsign="N111AA")
    for i in range(4):
        _op(setup, f"g2{i}", "touch_and_go", BASE + i * 60, "bbb222", callsign="N222BB")
    ledger.rebuild_rollup(ro, rw, "KLMO", BASE - DAY, BASE + DAY)
    rw.commit()

    rows = ledger.operator_ledger(ro, rw, "KLMO", "2026-06-01", "2026-06-01")
    assert [r["runway_uses"] for r in rows] == [4, 1]


def test_operator_carries_locality_and_its_evidence(tmp_path):
    setup, ro, rw = build_dbs(tmp_path)
    _register(setup, "N111AA", "aaa111", "BOULDER FLIGHT SCHOOL LLC")
    now = BASE + 90 * DAY
    for i in range(10):
        _op(setup, f"g{i}", "touch_and_go", now - i * DAY, "aaa111",
            callsign="N111AA", origin="KBDU")
    ledger.rebuild_rollup(ro, rw, "KLMO", now - 100 * DAY, now)
    homebase.recompute_airport(ro, rw, "KLMO", now_ts=now)
    rw.commit()

    start = db.local_day_key(now - 100 * DAY, "America/Denver")
    end = db.local_day_key(now, "America/Denver")
    rows = ledger.operator_ledger(ro, rw, "KLMO", start, end)
    assert rows[0]["locality"] == homebase.NON_LOCAL
    assert rows[0]["locality_evidence"]
    assert any("KBDU" in e["text"] for e in rows[0]["locality_evidence"])


def test_takeoffs_are_not_counted_as_runway_uses(tmp_path):
    setup, ro, rw = build_dbs(tmp_path)
    _register(setup, "N111AA", "aaa111", "BOULDER FLIGHT SCHOOL LLC")
    _op(setup, "l1", "landing", BASE, "aaa111", callsign="N111AA")
    _op(setup, "t1", "takeoff", BASE + 600, "aaa111", callsign="N111AA")
    ledger.rebuild_rollup(ro, rw, "KLMO", BASE - DAY, BASE + DAY)
    rw.commit()

    rows = ledger.operator_ledger(ro, rw, "KLMO", "2026-06-01", "2026-06-01")
    assert rows[0]["runway_uses"] == 1


def test_unclassified_aircraft_are_counted_visibly_not_dropped(tmp_path):
    # homebase.recompute_airport is never called here, so aircraft_home_base has
    # no row at all for this aircraft yet. That must not make the operator
    # disappear from the ledger, and it must not default to "local" by omission —
    # it must show up explicitly as unclassified, never silently dropped and
    # never silently lumped in with either local or non_local.
    setup, ro, rw = build_dbs(tmp_path)
    _register(setup, "N111AA", "aaa111", "BOULDER FLIGHT SCHOOL LLC")
    for i in range(3):
        _op(setup, f"g{i}", "touch_and_go", BASE + i * 60, "aaa111", callsign="N111AA")
    ledger.rebuild_rollup(ro, rw, "KLMO", BASE - DAY, BASE + DAY)
    rw.commit()

    rows = ledger.operator_ledger(ro, rw, "KLMO", "2026-06-01", "2026-06-01")
    assert len(rows) == 1
    assert rows[0]["operator"] == "BOULDER FLIGHT SCHOOL LLC"
    assert rows[0]["runway_uses"] == 3
    assert rows[0]["locality"] == homebase.UNCLASSIFIED
    assert rows[0]["locality_evidence"] == []
