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


def test_registrant_spelling_variants_merge_into_one_operator(tmp_path):
    # The real bug: the FAA registry spells the same registrant three
    # different ways ("G & M AIRCRAFT INC" / "G&M AIRCRAFT INC" /
    # "G AND M AIRCRAFT INC"). Before the fix these published as three
    # separate operators on a live, press-facing page and understated the
    # actual top operator by roughly half. All three must merge into ONE
    # operator with the SUMMED runway uses and the UNION of aircraft.
    setup, ro, rw = build_dbs(tmp_path)
    _register(setup, "N100AA", "aa0001", "G & M AIRCRAFT INC")
    _register(setup, "N100AB", "aa0002", "G & M AIRCRAFT INC")
    _register(setup, "N200BA", "bb0001", "G&M AIRCRAFT INC")
    _register(setup, "N200BB", "bb0002", "G&M AIRCRAFT INC")
    _register(setup, "N300CA", "cc0001", "G AND M AIRCRAFT INC")

    for i in range(5):
        _op(setup, f"a1{i}", "touch_and_go", BASE + i * 60, "aa0001", callsign="N100AA")
    for i in range(4):
        _op(setup, f"a2{i}", "touch_and_go", BASE + i * 60 + 1000, "aa0002", callsign="N100AB")
    for i in range(3):
        _op(setup, f"b1{i}", "touch_and_go", BASE + i * 60 + 2000, "bb0001", callsign="N200BA")
    for i in range(3):
        _op(setup, f"b2{i}", "touch_and_go", BASE + i * 60 + 3000, "bb0002", callsign="N200BB")
    _op(setup, "c1", "touch_and_go", BASE + 4000, "cc0001", callsign="N300CA")

    ledger.rebuild_rollup(ro, rw, "KLMO", BASE - DAY, BASE + DAY)
    rw.commit()

    rows = ledger.operator_ledger(ro, rw, "KLMO", "2026-06-01", "2026-06-01")
    assert len(rows) == 1
    row = rows[0]
    # Canonical display name is the variant with the most runway uses:
    # "G & M AIRCRAFT INC" (5 + 4 = 9) beats "G&M AIRCRAFT INC" (3 + 3 = 6)
    # beats "G AND M AIRCRAFT INC" (1).
    assert row["operator"] == "G & M AIRCRAFT INC"
    assert row["runway_uses"] == 5 + 4 + 3 + 3 + 1
    assert row["aircraft_count"] == 5
    assert sorted(a["tail"] for a in row["aircraft"]) == [
        "N100AA", "N100AB", "N200BA", "N200BB", "N300CA",
    ]


def test_case_and_whitespace_variants_merge(tmp_path):
    setup, ro, rw = build_dbs(tmp_path)
    _register(setup, "N400AA", "dd0001", "foo  aviation   inc")
    _register(setup, "N400AB", "dd0002", "FOO AVIATION INC")
    _op(setup, "d1", "landing", BASE, "dd0001", callsign="N400AA")
    _op(setup, "d2", "landing", BASE + 60, "dd0002", callsign="N400AB")
    ledger.rebuild_rollup(ro, rw, "KLMO", BASE - DAY, BASE + DAY)
    rw.commit()

    rows = ledger.operator_ledger(ro, rw, "KLMO", "2026-06-01", "2026-06-01")
    assert len(rows) == 1
    assert rows[0]["runway_uses"] == 2
    assert rows[0]["aircraft_count"] == 2


def test_inc_and_llc_are_different_legal_entities_and_do_not_merge(tmp_path):
    # "FOO AVIATION INC" and "FOO AVIATION LLC" may be different legal
    # entities -- normalization must never touch legal suffixes. (LLC is
    # also never nameable per NAMEABLE_OWNER_TYPES, so this simultaneously
    # exercises the privacy bucket boundary: the LLC's uses must land in
    # PRIVATE_BUCKET, not get merged into the INC's named row.)
    setup, ro, rw = build_dbs(tmp_path)
    _register(setup, "N500AA", "ee0001", "FOO AVIATION INC")
    _register(setup, "N500AB", "ee0002", "FOO AVIATION LLC")
    _op(setup, "e1", "landing", BASE, "ee0001", callsign="N500AA")
    for i in range(3):
        _op(setup, f"e2{i}", "landing", BASE + i * 60 + 500, "ee0002", callsign="N500AB")
    ledger.rebuild_rollup(ro, rw, "KLMO", BASE - DAY, BASE + DAY)
    rw.commit()

    rows = ledger.operator_ledger(ro, rw, "KLMO", "2026-06-01", "2026-06-01")
    assert len(rows) == 2
    named = [r for r in rows if r["operator"] == "FOO AVIATION INC"]
    assert len(named) == 1
    assert named[0]["runway_uses"] == 1
    private = [r for r in rows if r["operator"] == ledger.PRIVATE_BUCKET]
    assert len(private) == 1
    assert private[0]["runway_uses"] == 3


def test_different_corporate_suffixes_do_not_merge(tmp_path):
    # "INC" and "CORP" both classify as owner_type "corporation" (both are
    # nameable), but they are literally different suffixes. Normalization
    # must never strip or fold legal suffixes -- these must stay separate.
    setup, ro, rw = build_dbs(tmp_path)
    _register(setup, "N600AA", "ff0001", "FOO AVIATION INC")
    _register(setup, "N600AB", "ff0002", "FOO AVIATION CORP")
    _op(setup, "f1", "landing", BASE, "ff0001", callsign="N600AA")
    _op(setup, "f2", "landing", BASE + 60, "ff0002", callsign="N600AB")
    ledger.rebuild_rollup(ro, rw, "KLMO", BASE - DAY, BASE + DAY)
    rw.commit()

    rows = ledger.operator_ledger(ro, rw, "KLMO", "2026-06-01", "2026-06-01")
    assert len(rows) == 2
    assert sorted(r["operator"] for r in rows) == ["FOO AVIATION CORP", "FOO AVIATION INC"]


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


def test_a_mostly_unclassified_fleet_is_not_badged_from_a_handful():
    """The 'Private / unaffiliated' bucket held 354 aircraft, of which ~20 were
    classified local — and it published a confident LOCAL badge across all 354.

    A label has to describe the group it is attached to. If we could not classify
    most of the fleet, we do not get to label the fleet.
    """
    from app.ledger import _dominant_locality
    from app import homebase

    mostly_unknown = (
        [{"locality": homebase.LOCAL, "signal_strength": 0.9, "evidence": [{"code": "x", "text": "t"}]}] * 20
        + [{"locality": homebase.UNCLASSIFIED, "signal_strength": 0.1, "evidence": []}] * 334
    )
    locality, _ = _dominant_locality(mostly_unknown)
    assert locality == homebase.UNCLASSIFIED

    # But a real operator whose fleet we DID classify still gets its badge.
    small_known_fleet = [
        {"locality": homebase.LOCAL, "signal_strength": 0.9, "evidence": [{"code": "x", "text": "t"}]},
        {"locality": homebase.LOCAL, "signal_strength": 0.8, "evidence": [{"code": "y", "text": "u"}]},
        {"locality": homebase.UNCLASSIFIED, "signal_strength": 0.1, "evidence": []},
    ]
    locality, _ = _dominant_locality(small_known_fleet)
    assert locality == homebase.LOCAL
