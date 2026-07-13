from __future__ import annotations

import json

from app import dwell, homebase

from .dbsupport import build_dbs, op as _op

NOW = 1780336800 + 90 * 86400   # ~90 days after our reference day
DAY = 86400


def _overnight(setup, day_offset, icao24):
    """A landing in the evening and a departure the next morning."""
    landed = NOW - day_offset * DAY - 4 * 3600      # evening
    departed = landed + 14 * 3600                    # next morning
    _op(setup, f"l{icao24}{day_offset}", "landing", landed, icao24=icao24)
    _op(setup, f"t{icao24}{day_offset}", "takeoff", departed, icao24=icao24)


def test_repeated_overnight_stays_classify_as_local(tmp_path):
    setup, ro, rw = build_dbs(tmp_path)
    for d in (10, 20, 30, 40):
        _overnight(setup, d, "aaa111")

    result = homebase.classify(ro, "KLMO", "aaa111", now_ts=NOW)
    assert result["locality"] == homebase.LOCAL
    assert result["signal_strength"] >= homebase.CONFIDENCE_THRESHOLD
    codes = {e["code"] for e in result["evidence"]}
    assert "overnight_stays" in codes


def test_no_overnights_plus_foreign_origin_classifies_as_non_local(tmp_path):
    setup, ro, rw = build_dbs(tmp_path)
    # 10 touch-and-go visits, every one arriving from KBDU, never staying the night.
    # A touch-and-go is SELF-CLOSING: we watched the visit begin and end in the
    # same operation, so "it never slept here" is an observation here, not an
    # absence -- and the KBDU origins are a positive, named observation of where
    # it comes from. Two positive observations. This is what a conviction costs.
    for i in range(10):
        _op(setup, f"g{i}", "touch_and_go", NOW - i * DAY, icao24="bbb222", origin="KBDU")

    result = homebase.classify(ro, "KLMO", "bbb222", now_ts=NOW)
    assert result["locality"] == homebase.NON_LOCAL
    assert result["based_icao"] == "KBDU"
    codes = {e["code"] for e in result["evidence"]}
    assert "overnight_stays" in codes
    assert "arrival_origin" in codes


def test_evidence_is_human_readable_and_states_the_numbers(tmp_path):
    setup, ro, rw = build_dbs(tmp_path)
    for i in range(10):
        _op(setup, f"g{i}", "touch_and_go", NOW - i * DAY, icao24="bbb222", origin="KBDU")

    result = homebase.classify(ro, "KLMO", "bbb222", now_ts=NOW)
    origin_text = next(e["text"] for e in result["evidence"] if e["code"] == "arrival_origin")
    assert "10" in origin_text and "KBDU" in origin_text
    overnight_text = next(e["text"] for e in result["evidence"] if e["code"] == "overnight_stays")
    assert "0" in overnight_text and "180" in overnight_text


def test_thin_evidence_is_unclassified_not_guessed(tmp_path):
    # One visit, no origin data, no overnight. We do not know. Say so.
    setup, ro, rw = build_dbs(tmp_path)
    _op(setup, "g1", "touch_and_go", NOW - DAY, icao24="ccc333")

    result = homebase.classify(ro, "KLMO", "ccc333", now_ts=NOW)
    assert result["locality"] == homebase.UNCLASSIFIED
    assert result["signal_strength"] < homebase.CONFIDENCE_THRESHOLD


def test_unclassified_still_carries_its_evidence(tmp_path):
    # Even a "we don't know" shows its working.
    setup, ro, rw = build_dbs(tmp_path)
    _op(setup, "g1", "touch_and_go", NOW - DAY, icao24="ccc333")
    result = homebase.classify(ro, "KLMO", "ccc333", now_ts=NOW)
    assert result["evidence"]


def test_registrant_address_alone_never_decides(tmp_path):
    # The FAA registry records a MAILING address, not a based airport. It may
    # nudge; it may not decide.
    setup, ro, rw = build_dbs(tmp_path)
    _op(setup, "g1", "touch_and_go", NOW - DAY, icao24="ddd444")
    setup.execute(
        "INSERT INTO aircraft_registry (n_number, icao_hex, registrant_city, registrant_state) "
        "VALUES ('N1', 'DDD444', 'Boulder', 'CO')"
    )
    setup.commit()

    result = homebase.classify(ro, "KLMO", "ddd444", now_ts=NOW)
    assert result["locality"] == homebase.UNCLASSIFIED


def test_evidence_never_contains_registrant_name_or_street(tmp_path):
    # PRIVACY BOUNDARY: an N-number resolves to an owner name AND a home street
    # address in one FAA registry lookup, and many private aircraft are owned by
    # individuals (a single-member LLC is just how an individual holds a personal
    # aircraft). Evidence may use registrant_city/registrant_state ONLY. Prove
    # that a populated name/street column can never leak into a public evidence
    # string, no matter what classify() decides.
    setup, ro, rw = build_dbs(tmp_path)
    _op(setup, "g1", "touch_and_go", NOW - DAY, icao24="eee555")
    setup.execute(
        "INSERT INTO aircraft_registry "
        "(n_number, icao_hex, registrant_name, registrant_street, registrant_city, "
        " registrant_state, registrant_zip) "
        "VALUES ('N2', 'EEE555', 'Jane Q. Smith', '123 Main St', 'Longmont', 'CO', '80501')"
    )
    setup.commit()

    result = homebase.classify(ro, "KLMO", "eee555", now_ts=NOW)
    blob = json.dumps(result["evidence"])
    for leak in ("Jane", "Smith", "Main St", "123", "80501"):
        assert leak not in blob


def test_recompute_airport_persists_and_is_idempotent(tmp_path):
    setup, ro, rw = build_dbs(tmp_path)
    for d in (10, 20, 30, 40):
        _overnight(setup, d, "aaa111")
    for i in range(10):
        _op(setup, f"g{i}", "touch_and_go", NOW - i * DAY, icao24="bbb222", origin="KBDU")

    assert homebase.recompute_airport(ro, rw, "KLMO", now_ts=NOW) == 2
    rw.commit()
    assert homebase.recompute_airport(ro, rw, "KLMO", now_ts=NOW) == 2  # upsert, not duplicate
    rw.commit()

    rows = rw.execute("SELECT * FROM aircraft_home_base WHERE icao='KLMO'").fetchall()
    assert len(rows) == 2
    by_ac = {r["icao24"]: r for r in rows}
    assert by_ac["aaa111"]["locality"] == homebase.LOCAL
    assert by_ac["bbb222"]["locality"] == homebase.NON_LOCAL
    assert json.loads(by_ac["bbb222"]["evidence_json"])


def test_locality_map_reads_back_what_recompute_wrote(tmp_path):
    setup, ro, rw = build_dbs(tmp_path)
    for i in range(10):
        _op(setup, f"g{i}", "touch_and_go", NOW - i * DAY, icao24="bbb222", origin="KBDU")
    homebase.recompute_airport(ro, rw, "KLMO", now_ts=NOW)
    rw.commit()

    mapping = homebase.locality_map(rw, "KLMO")
    assert mapping["bbb222"]["locality"] == homebase.NON_LOCAL
    assert mapping["bbb222"]["evidence"]


# ---------------------------------------------------------------------------
# Absence is not evidence.
#
# `dwell.dwell_intervals` drops a landing that has no subsequent takeoff
# (dwell.py:54-55), and `dwell_summary` publishes `coverage` precisely because
# landing->takeoff pairing is known to be lossy. So "we observed 0 overnight
# stays" can mean the aircraft never slept here, OR that we never observed it
# leave -- including the case where it never left because it is PARKED here.
# The classifier must never turn the second into a public accusation.
# ---------------------------------------------------------------------------


def test_aircraft_parked_at_the_field_is_never_non_local(tmp_path):
    """It flew in from KBDU and it is still sitting on the KLMO ramp.

    Zero landing->takeoff pairs exist, so zero overnight stays are OBSERVABLE.
    The aircraft whose locality is least in doubt -- it is physically on the
    field, right now, and has been for months -- must never be published as a
    visitor from somewhere else.
    """
    setup, ro, rw = build_dbs(tmp_path)
    # Four arrivals from KBDU over a fortnight, then it lands and never leaves.
    for i, d in enumerate((174, 172, 168, 160)):
        _op(setup, f"p{i}", "landing", NOW - d * DAY, icao24="fff666", origin="KBDU")

    result = homebase.classify(ro, "KLMO", "fff666", now_ts=NOW)
    assert result["locality"] != homebase.NON_LOCAL, (
        "an aircraft parked on the field for 160 days was published as a "
        f"visitor from {result['based_icao']}"
    )
    assert result["based_icao"] is None
    # Being on the field is, if anything, evidence it lives there.
    assert result["locality"] == homebase.LOCAL
    codes = {e["code"] for e in result["evidence"]}
    assert "on_field_now" in codes


def test_a_visitor_whose_departure_we_missed_is_not_promoted_to_local(tmp_path):
    """The other half of the parked case: don't over-correct into a false LOCAL.

    Landed Friday, we missed the Monday takeoff. Three days on the field is not
    a home. Non-local is still vetoed -- but the honest answer is "we don't know".
    """
    setup, ro, rw = build_dbs(tmp_path)
    _op(setup, "w1", "landing", NOW - 3 * DAY, icao24="www000", origin="KBDU")

    result = homebase.classify(ro, "KLMO", "www000", now_ts=NOW)
    assert result["locality"] == homebase.UNCLASSIFIED


def test_three_arrivals_are_too_thin_to_convict(tmp_path):
    """Reviewer probe P2: three observed arrivals used to reach non_local @ 0.8."""
    setup, ro, rw = build_dbs(tmp_path)
    for i in range(3):
        _op(setup, f"q{i}", "touch_and_go", NOW - (i + 1) * DAY, icao24="ggg777", origin="KBDU")

    result = homebase.classify(ro, "KLMO", "ggg777", now_ts=NOW)
    assert result["locality"] == homebase.UNCLASSIFIED


def test_missed_departures_are_not_evidence_of_absence(tmp_path):
    """Reviewer probe P3: 59 landings, the takeoff detector missed every departure.

    Zero landing->takeoff pairs exist, so "0 overnight stays" is not an
    observation -- it is a hole in our data. `dwell.py:51-53` names missed
    departures as an EXPECTED condition. It must score nothing.
    """
    setup, ro, rw = build_dbs(tmp_path)
    for i in range(59):
        _op(setup, f"r{i}", "landing", NOW - (i + 2) * DAY, icao24="hhh888", origin="KBDU")

    result = homebase.classify(ro, "KLMO", "hhh888", now_ts=NOW)
    assert result["locality"] != homebase.NON_LOCAL
    overnight_text = next(
        e["text"] for e in result["evidence"] if e["code"] == "overnight_stays"
    )
    # The receipt must say WHY it weighed nothing, in the reader's language: we
    # saw 59 arrivals and watched it leave again 0 times.
    assert "59 observed arrivals" in overnight_text
    assert "0 of the 59 full-stop landings" in overnight_text
    assert "never followed by a departure we saw" in overnight_text


def test_all_null_origins_can_never_produce_a_non_local(tmp_path):
    """CRITICAL 2: `operations.origin_airport_icao` is PERMANENTLY NULL — the
    writer that once populated it has been removed from the main circlejerks
    API entirely. With no origin data the classifier must degrade to
    local/unclassified and NEVER manufacture a non-local verdict out of the
    absence. This is the permanent contract this module holds itself to now,
    not a snapshot of a transitional launch state.
    """
    setup, ro, rw = build_dbs(tmp_path)

    # A resident: sleeps here constantly.
    for d in range(10, 90, 7):
        _overnight(setup, d, "res001")
    # The archetypal "should be non-local if only we knew": 30 touch-and-goes,
    # never stays, but we have no idea where it comes from.
    for i in range(30):
        _op(setup, f"n{i}", "touch_and_go", NOW - (i + 1) * DAY, icao24="vis002")
    # Parked on the ramp.
    _op(setup, "park", "landing", NOW - 100 * DAY, icao24="prk003")
    # A one-off.
    _op(setup, "one", "landing", NOW - 40 * DAY, icao24="one004")
    _op(setup, "onet", "takeoff", NOW - 40 * DAY + 3600, icao24="one004")
    # Missed departures.
    for i in range(20):
        _op(setup, f"m{i}", "landing", NOW - (i + 2) * DAY, icao24="mis005")

    assert ro.execute(
        "SELECT COUNT(*) AS n FROM operations WHERE origin_airport_icao IS NOT NULL"
    ).fetchone()["n"] == 0

    homebase.recompute_airport(ro, rw, "KLMO", now_ts=NOW)
    rw.commit()

    mapping = homebase.locality_map(rw, "KLMO")
    assert len(mapping) == 5
    offenders = {k: v for k, v in mapping.items() if v["locality"] == homebase.NON_LOCAL}
    assert not offenders, f"non_local reached with zero origin data: {offenders}"
    # And it degraded the way it promised to: local for the ones we watch sleep
    # here, honest ignorance for everyone else.
    assert mapping["res001"]["locality"] == homebase.LOCAL
    assert mapping["vis002"]["locality"] == homebase.UNCLASSIFIED
    assert mapping["prk003"]["locality"] == homebase.LOCAL


def test_non_local_becomes_reachable_organically_on_real_origin_evidence(tmp_path):
    """The companion to the test above: `non_local` is not dead code.

    The SAME aircraft that is unclassifiable with no origin data becomes non-local
    once we have actually WATCHED it arrive from a named airport enough times.
    """
    setup, ro, rw = build_dbs(tmp_path)
    for i in range(30):
        _op(setup, f"n{i}", "touch_and_go", NOW - (i + 1) * DAY, icao24="vis002")
    assert homebase.classify(ro, "KLMO", "vis002", now_ts=NOW)["locality"] == (
        homebase.UNCLASSIFIED
    )

    # Origin coverage accrues forward: some future signal starts filling in arrivals.
    setup.execute(
        "UPDATE operations SET origin_airport_icao='KBDU' "
        "WHERE icao24='vis002' AND id IN (SELECT id FROM operations "
        "  WHERE icao24='vis002' ORDER BY timestamp DESC LIMIT 14)"
    )
    setup.commit()

    result = homebase.classify(ro, "KLMO", "vis002", now_ts=NOW)
    assert result["locality"] == homebase.NON_LOCAL
    assert result["based_icao"] == "KBDU"


def test_non_local_always_names_the_airport_it_came_from(tmp_path):
    """A non-local verdict may never rest on two absences.

    Whatever the arithmetic, `based_icao` -- a POSITIVE, named observation of
    where we watched it fly in from -- is a precondition for the verdict.
    """
    setup, ro, rw = build_dbs(tmp_path)
    for i in range(40):
        _op(setup, f"n{i}", "touch_and_go", NOW - (i + 1) * DAY, icao24="vis002")
    for i in range(20):
        _op(setup, f"k{i}", "touch_and_go", NOW - (i + 60) * DAY, icao24="vis002", origin="KBDU")

    for row in homebase.locality_map(rw, "KLMO").values():
        if row["locality"] == homebase.NON_LOCAL:
            assert row["based_icao"]

    result = homebase.classify(ro, "KLMO", "vis002", now_ts=NOW)
    if result["locality"] == homebase.NON_LOCAL:
        assert result["based_icao"] == "KBDU"


def test_repeated_overnight_stays_outrank_a_foreign_arrival_origin(tmp_path):
    """A KLMO-based aircraft that shuttles to Boulder daily has EVERY arrival
    originating at KBDU. Where it flies in from describes its trips, not its home.
    """
    setup, ro, rw = build_dbs(tmp_path)
    for d in range(10, 80, 5):
        _overnight(setup, d, "shu111")
    for i in range(20):
        _op(setup, f"s{i}", "landing", NOW - (i + 90) * DAY, icao24="shu111", origin="KBDU")
        _op(setup, f"st{i}", "takeoff", NOW - (i + 90) * DAY + 3600, icao24="shu111")

    result = homebase.classify(ro, "KLMO", "shu111", now_ts=NOW)
    assert result["locality"] == homebase.LOCAL
    assert result["based_icao"] is None


# ---------------------------------------------------------------------------
# I2 -- the receipt may never contradict its own verdict.
# ---------------------------------------------------------------------------


def test_registrant_in_the_airports_own_town_vetoes_non_local(tmp_path):
    """A confident "non-local" badge printed directly above "Registrant address
    Longmont, CO" -- the airport's own town -- is a receipt that argues against
    itself, and the reader is right. A positive registrant match VETOES the
    verdict; it does not merely soften it from 0.8 to 0.65.
    """
    setup, ro, rw = build_dbs(tmp_path)
    for i in range(14):
        _op(setup, f"s{i}", "touch_and_go", NOW - (i + 1) * DAY, icao24="iii999", origin="KBDU")
    setup.execute(
        "INSERT INTO aircraft_registry (n_number, icao_hex, registrant_city, registrant_state) "
        "VALUES ('N9', 'III999', 'Longmont', 'CO')"
    )
    setup.commit()

    result = homebase.classify(ro, "KLMO", "iii999", now_ts=NOW)
    assert result["locality"] == homebase.UNCLASSIFIED
    assert result["based_icao"] is None
    withheld = [e for e in result["evidence"] if e["code"] == "verdict_withheld"]
    assert withheld, "the veto must appear in the receipt, not happen silently"


def test_the_registrant_nudge_can_never_manufacture_a_non_local(tmp_path):
    """A registrant in some other town scores nothing -- never a penalty. Plenty of
    people keep an aeroplane at the field down the road from where they get post.
    """
    setup, ro, rw = build_dbs(tmp_path)
    for d in (10, 20, 30, 40):
        _overnight(setup, d, "reg777")
    setup.execute(
        "INSERT INTO aircraft_registry (n_number, icao_hex, registrant_city, registrant_state) "
        "VALUES ('N7', 'REG777', 'Anchorage', 'AK')"
    )
    setup.commit()

    result = homebase.classify(ro, "KLMO", "reg777", now_ts=NOW)
    assert result["locality"] == homebase.LOCAL


# ---------------------------------------------------------------------------
# I3 / I4 -- the number is not a probability; a takeoff is not an arrival.
# ---------------------------------------------------------------------------


def test_signal_strength_is_never_certainty(tmp_path):
    """Under the floor constraint no classification here can be certain. Stack
    every positive signal the model has and it must still land below 1.0.
    """
    setup, ro, rw = build_dbs(tmp_path)
    for d in range(20, 90, 5):
        _overnight(setup, d, "max222")
    for i in range(14):
        _op(setup, f"h{i}", "touch_and_go", NOW - (i + 100) * DAY, icao24="max222", origin="KLMO")
    _op(setup, "sit", "landing", NOW - 15 * DAY, icao24="max222", origin="KLMO")  # still here
    setup.execute(
        "INSERT INTO aircraft_registry (n_number, icao_hex, registrant_city, registrant_state) "
        "VALUES ('N5', 'MAX222', 'Longmont', 'CO')"
    )
    setup.commit()

    result = homebase.classify(ro, "KLMO", "max222", now_ts=NOW)
    assert result["locality"] == homebase.LOCAL
    assert result["signal_strength"] < 1.0
    assert result["signal_strength"] == homebase.MAX_SIGNAL_STRENGTH


def test_the_worst_non_local_badge_we_can_print_is_still_not_certain(tmp_path):
    setup, ro, rw = build_dbs(tmp_path)
    for i in range(40):
        _op(setup, f"z{i}", "touch_and_go", NOW - (i + 1) * DAY, icao24="wor333", origin="KBDU")

    result = homebase.classify(ro, "KLMO", "wor333", now_ts=NOW)
    assert result["locality"] == homebase.NON_LOCAL
    assert result["signal_strength"] <= 0.75


def test_takeoffs_are_never_counted_as_arrivals(tmp_path):
    """The origins query had no `type` filter while its evidence called the rows
    "arrivals". A takeoff never GETS an origin -- but the query must be true by
    construction, not by a coincidence upstream. THAT subject is what this test
    still proves, and it is kept.

    ROUND 3 note: this fixture's ORIGINAL assertion was `locality == NON_LOCAL`,
    on a receipt claiming "0 overnight stays ... of 10 observed arrivals" about an
    aircraft we watched depart our own field 30 times and never once watched
    arrive. That is an instance of the round-3 CRITICAL, not a passing case -- 30
    takeoffs from KLMO with no landing we ever watched (`unpaired_takeoffs = 30`)
    is exactly the hole an overnight can hide behind, and the fix correctly
    withholds the "0 overnights" weight here. The aircraft now, correctly,
    classifies as UNCLASSIFIED: the KBDU origin alone (-0.30) does not clear the
    non-local threshold on its own.
    """
    setup, ro, rw = build_dbs(tmp_path)
    for i in range(10):
        _op(setup, f"g{i}", "touch_and_go", NOW - (i + 1) * DAY, icao24="bbb222", origin="KBDU")
    # Poison the well: 30 TAKEOFFS carrying an origin of KLMO. If takeoffs counted,
    # KLMO would dominate 30/40 and the aircraft would be scored as LOCAL.
    for i in range(30):
        _op(setup, f"tk{i}", "takeoff", NOW - (i + 40) * DAY, icao24="bbb222", origin="KLMO")

    result = homebase.classify(ro, "KLMO", "bbb222", now_ts=NOW)
    # Round 3: NOT non-local. These 30 takeoffs are unpaired to any landing we
    # watched, so the "0 overnights" weight is correctly withheld -- the KBDU
    # origin signal alone is not enough to convict.
    assert result["locality"] != homebase.NON_LOCAL
    assert result["locality"] == homebase.UNCLASSIFIED
    assert result["based_icao"] is None
    origin_text = next(e["text"] for e in result["evidence"] if e["code"] == "arrival_origin")
    # THE SUBJECT THIS TEST EXISTS TO PROVE, UNCHANGED: the denominator is the 10
    # arrivals, not the 40 operations, and the KLMO-origin takeoffs never appear.
    assert "10 of 10 observed arrivals" in origin_text
    assert "KLMO" not in origin_text
    # And the receipt says why the overnight signal was withheld.
    overnight_text = next(
        e["text"] for e in result["evidence"] if e["code"] == "overnight_stays"
    )
    assert "30 takeoffs from KLMO" in overnight_text
    assert "overnight can hide" in overnight_text


def test_arrival_types_match_ROLLUP_TYPES_minus_takeoff(tmp_path):
    """Drift guard, ported. `homebase.ARRIVAL_TYPES` is the denominator of
    every published "N of M arrivals" claim. It used to be pinned against
    `services.ORIGIN_ELIGIBLE_OP_TYPES` on the main circlejerks API — that
    module has been removed from this service's world entirely (this service
    never runs detectors or writes origins), so there is nothing left to pin
    it against on that side. What *is* still local to this service is
    `ledger.ROLLUP_TYPES`, which must agree that everything except `takeoff`
    is an arrival — pin against that instead, so the two ledger-side
    denominators cannot silently diverge from each other.
    """
    from app import ledger

    assert set(homebase.ARRIVAL_TYPES) == set(ledger.ROLLUP_TYPES) - {"takeoff"}


# ---------------------------------------------------------------------------
# I1 -- evidence describes what we OBSERVED, never what happened.
# ---------------------------------------------------------------------------


def test_evidence_states_observations_and_true_denominators(tmp_path):
    """"0 overnight stays at KLMO in 180 days" asserts the aircraft did not stay.
    What we know is that we observed no landing->takeoff pair of >= 8h. Likewise
    "4 of 4 arrivals originated at KBDU" implies it had 4 arrivals; it had 4
    arrivals WHOSE ORIGIN WE HAPPENED TO KNOW, possibly out of 200.
    """
    setup, ro, rw = build_dbs(tmp_path)
    # 20 arrivals. We know the origin of only 9 of them.
    for i in range(20):
        origin = "KBDU" if i < 9 else None
        _op(setup, f"e{i}", "touch_and_go", NOW - (i + 1) * DAY, icao24="obs444", origin=origin)

    result = homebase.classify(ro, "KLMO", "obs444", now_ts=NOW)
    texts = {e["code"]: e["text"] for e in result["evidence"]}

    # Every count is a floor, and every claim is about our observations.
    assert "observed" in texts["overnight_stays"]
    # ROUND 2 / I2: these 20 arrivals are all CIRCUITS. They closed themselves. We
    # never watched a departure, and the receipt may not say we did -- that sentence
    # is the load-bearing one under the badge, and the one a correction would quote
    # back at us.
    assert "we observed both the arrival and the departure" not in texts["overnight_stays"]
    assert "circuits ended themselves and are not departures we watched" in (
        texts["overnight_stays"]
    )
    assert "20 separate days" in texts["overnight_stays"]
    # The TRUE denominator: 9 known origins out of 20 observed arrivals, and -- the
    # count that actually gates the weight -- 9 separate DAYS, not 9 rows. MINOR
    # (round 3): the gate runs on days, so the sentence now leads with days.
    assert "Origin known on 9 separate days" in texts["arrival_origin"]
    assert "9 of 20 observed arrivals" in texts["arrival_origin"]
    assert "9 of those 9 days came from KBDU" in texts["arrival_origin"]
    # Not a single evidence string may assert a fact we did not observe.
    for text in texts.values():
        assert "did not" not in text


# ---------------------------------------------------------------------------
# ROUND 2 / CRITICAL 1 -- `closed_visits` was the wrong quantity.
#
# `dwell.dwell_intervals` pairs a takeoff with the MOST RECENT landing and DROPS
# the earlier one (dwell.py:76-79). So the aircraft whose overnights we cannot see
# is exactly the aircraft that accrues "closed visits" fastest: the destroyed pair
# is the overnight, the surviving pair is the same-day turnaround. The gate meant
# to protect against missed departures was being SATISFIED by them.
#
# And a touch-and-go can never produce an overnight, so counting circuits in the
# denominator of "0 overnights observed" is scoring an absence.
#
# The gate is now: enough closed visits AND at most one full-stop landing we never
# watched leave (one = it is still on the field).
# ---------------------------------------------------------------------------


def test_probe_a_based_trainer_with_missed_departures_is_never_non_local(tmp_path):
    """PROBE A -- a based trainer whose departures we NEVER observed.

    41 full-stop landings, not one paired with a takeoff we saw, plus 20 pattern
    circuits. Under the old gate the 20 self-closing circuits alone satisfied
    `closed_visits >= 5`, the 0 overnights scored -0.30, and the KBDU origins
    scored -0.45: `non_local @ 0.75, based KBDU` about an aircraft that LIVES here.
    """
    setup, ro, rw = build_dbs(tmp_path)
    # 41 landings, every departure missed by the detector.
    for i in range(41):
        _op(setup, f"pa_l{i}", "landing", NOW - (i + 2) * DAY, icao24="prb001", origin="KBDU")
    # 20 circuits, the most recent an hour ago -- so the last operation is NOT a
    # full-stop landing and the `on_field` veto (which is timing-dependent) does
    # not fire. Nothing saves this aircraft except the gate itself.
    for i in range(20):
        _op(setup, f"pa_g{i}", "touch_and_go", NOW - i * DAY - 3600,
            icao24="prb001", origin="KBDU")

    result = homebase.classify(ro, "KLMO", "prb001", now_ts=NOW)
    assert result["locality"] != homebase.NON_LOCAL, (
        "a based trainer whose 41 departures we never observed was published as a "
        f"visitor from {result['based_icao']} @ {result['signal_strength']}"
    )
    assert result["locality"] == homebase.UNCLASSIFIED
    assert result["based_icao"] is None
    # And the receipt says why: those 41 landings are a hole in our data.
    overnight_text = next(
        e["text"] for e in result["evidence"] if e["code"] == "overnight_stays"
    )
    assert "41" in overnight_text
    # It must NOT claim we watched a departure we never watched.
    assert "we observed both the arrival and the departure" not in overnight_text


def test_probe_d2_based_day_tripper_with_missed_dawn_departures_is_never_non_local(tmp_path):
    """PROBE D2 -- no touch-and-goes at all, and still convicted.

    A KLMO-BASED aircraft flying two legs a day to Boulder for 59 days. Its dawn
    departure is missed (ADS-B transponders that only come alive after the takeoff
    roll -- dwell.py:51-53 names missed departures as an EXPECTED condition); its
    midday departure is seen. Every night it sleeps on the KLMO ramp.

    `dwell_intervals` pairs the midday takeoff with the morning landing and DROPS
    the afternoon landing -- so the 59 real overnights are invisible and 59 SHORT
    turnarounds survive as "closed visits". Old verdict: `non_local @ 0.75,
    based_icao=KBDU`, on a receipt claiming we watched it leave 59 times. We never
    watched it leave once.
    """
    setup, ro, rw = build_dbs(tmp_path)
    for d in range(59):
        base = NOW - d * DAY
        # ...its dawn departure HAPPENED, and we did not see it...
        _op(setup, f"d2_a{d}", "landing", base - 9 * 3600, icao24="prb002", origin="KBDU")
        _op(setup, f"d2_t{d}", "takeoff", base - 6 * 3600, icao24="prb002")   # SEEN
        _op(setup, f"d2_b{d}", "landing", base - 4 * 3600, icao24="prb002", origin="KBDU")
        # ...and then it sleeps on the KLMO ramp. That overnight pair is destroyed
        # by the missed dawn takeoff: the NEXT landing overwrites the open one
        # (dwell.py:76-79), and only the 3h turnaround survives.

    intervals = [
        i for i in dwell.dwell_intervals(ro, "KLMO", NOW - 180 * DAY, NOW)
        if i["icao24"] == "prb002"
    ]
    assert len(intervals) == 59
    assert all(i["seconds"] == 3 * 3600 for i in intervals), (
        "the destroyed evidence: every surviving pair is the 3h turnaround, never "
        "the 19h overnight"
    )
    assert not any(i["seconds"] >= homebase.OVERNIGHT_SECONDS for i in intervals)
    # The on_field veto is TIMING-dependent and does not fire here: the last
    # operation is a landing only 4h old, short of the 8h mark. Nothing saves this
    # aircraft except the gate itself.

    result = homebase.classify(ro, "KLMO", "prb002", now_ts=NOW)
    assert result["locality"] != homebase.NON_LOCAL, (
        "an aircraft that sleeps on the KLMO ramp every night was published as a "
        f"visitor from {result['based_icao']} @ {result['signal_strength']}"
    )
    assert result["locality"] == homebase.UNCLASSIFIED
    assert result["based_icao"] is None
    overnight_text = next(
        e["text"] for e in result["evidence"] if e["code"] == "overnight_stays"
    )
    # 59 of its 118 landings were never followed by a departure we saw. An aircraft
    # that slept here is EXACTLY what that looks like.
    assert "59" in overnight_text
    assert "we observed both the arrival and the departure" not in overnight_text


def test_one_afternoon_of_circuits_is_one_observation_not_twelve(tmp_path):
    """IMPORTANT 3 -- the gates counted operations, not independent observations.

    A single 40-minute pattern session used to yield known=12, share=1.0,
    closed_visits=12 -- `non_local @ 0.75` from one afternoon, on a receipt
    claiming twelve independent observations. Both gates now count distinct
    LOCAL DAYS.
    """
    setup, ro, rw = build_dbs(tmp_path)
    for i in range(12):
        _op(setup, f"ses{i}", "touch_and_go", NOW - 2 * DAY + i * 200,
            icao24="ses888", origin="KBDU")

    result = homebase.classify(ro, "KLMO", "ses888", now_ts=NOW)
    assert result["locality"] == homebase.UNCLASSIFIED
    assert result["based_icao"] is None
    assert result["signal_strength"] < homebase.CONFIDENCE_THRESHOLD


def test_gate_still_fires_for_an_honest_transient(tmp_path):
    """The gate must not over-suppress. Ten circuits from KBDU on ten separate days
    is ten independent observations of an aircraft that arrives, does not stay, and
    comes from somewhere else. That is still `non_local`, and it must remain so.
    """
    setup, ro, rw = build_dbs(tmp_path)
    for i in range(10):
        _op(setup, f"ht{i}", "touch_and_go", NOW - (i + 1) * DAY, icao24="hon111", origin="KBDU")

    result = homebase.classify(ro, "KLMO", "hon111", now_ts=NOW)
    assert result["locality"] == homebase.NON_LOCAL
    assert result["based_icao"] == "KBDU"


def test_one_trailing_unpaired_landing_does_not_suppress_the_gate(tmp_path):
    """A single landing we never watched leave is the aircraft sitting on the ramp
    RIGHT NOW -- it is the expected steady state, not a data hole. It must not
    suppress the overnight gate on its own; two must.
    """
    setup, ro, rw = build_dbs(tmp_path)
    for i in range(10):
        _op(setup, f"tr{i}", "touch_and_go", NOW - (i + 20) * DAY, icao24="trl222", origin="KBDU")
    # One full-stop landing, four hours ago -- too recent for the on_field veto (8h).
    _op(setup, "tr_l", "landing", NOW - 4 * 3600, icao24="trl222", origin="KBDU")

    result = homebase.classify(ro, "KLMO", "trl222", now_ts=NOW)
    assert result["locality"] == homebase.NON_LOCAL


# ---------------------------------------------------------------------------
# ROUND 3 / CRITICAL -- `unpaired_landings` catches a missed DEPARTURE (the
# landing row exists, nothing ever paired with it) but is completely blind to a
# missed ARRIVAL: if the detector never emits the `landing` at all, the op is in
# NEITHER `landings` NOR `intervals`, so `unpaired_landings` reads 0 --
# arithmetically identical to a perfect observation record.
#
# `landing` (detectors.py:735-768) is structurally the most fragile op this
# module counts: on top of the <=200 ft AGL sample every arrival needs, it also
# requires a >=500 ft AGL sample in the 300s before touchdown
# (detectors.py:664-672, 764), while `touch_and_go` (detectors.py:488-514) needs
# only runway-overlap geometry "at any altitude". Marginal low-altitude ADS-B
# coverage -- an explicit risk named at detectors.py:801-805 -- drops an
# aircraft's `landing` rows while leaving its circuits untouched.
#
# `detect_takeoffs_over_period` requires NO prior approach from altitude
# (detectors.py:778-800): a takeoff is only ever emitted for an aircraft that
# ORIGINATED at the field. So a takeoff we cannot pair with a landing we watched
# IS an arrival we missed -- the exact mirror of an unpaired landing -- and an
# overnight hides behind a missed arrival exactly as it hides behind a missed
# departure.
# ---------------------------------------------------------------------------


def test_hangared_aircraft_with_missed_arrivals_is_never_non_local(tmp_path):
    """Reviewer's reproduction: 129 nights hangared at KLMO, flying to Boulder
    daily. The dawn departure is detected cleanly as a `takeoff` (no prior
    approach -- it started on the ground here). The nightly return is detected
    as two low pattern circuits, never as a full-stop `landing` -- exactly the
    shape the `landing` detector's altitude guards can drop.

    So `landings == 0` for this aircraft: we have never once watched it touch
    the ground here. `unpaired_landings` therefore reads 0 -- indistinguishable
    from a perfect record -- and before the fix this published `non_local @
    0.75, based_icao=KBDU` about an aircraft that lives here.
    """
    setup, ro, rw = build_dbs(tmp_path)
    for d in range(1, 130):
        base = NOW - d * DAY
        # Dawn departure: detected clean, on the ground here, no landing paired.
        _op(setup, f"hg_dep{d}", "takeoff", base - 10 * 3600, icao24="hng129")
        # The nightly return: two low circuits, never a full-stop landing.
        _op(setup, f"hg_c1_{d}", "touch_and_go", base - 4 * 3600, icao24="hng129", origin="KBDU")
        _op(setup, f"hg_c2_{d}", "touch_and_go", base - 3 * 3600, icao24="hng129", origin="KBDU")

    result = homebase.classify(ro, "KLMO", "hng129", now_ts=NOW)
    assert result["locality"] != homebase.NON_LOCAL, (
        "a hangared aircraft whose 129 nightly returns were never observed as a "
        f"full-stop landing was published as a visitor from {result['based_icao']} "
        f"@ {result['signal_strength']}"
    )
    assert result["locality"] == homebase.UNCLASSIFIED
    assert result["based_icao"] is None

    overnight_text = next(
        e["text"] for e in result["evidence"] if e["code"] == "overnight_stays"
    )
    # The new gate's reasoning, in the reader's language.
    assert "129 takeoffs from KLMO" in overnight_text
    assert "overnight can hide" in overnight_text
    # IMPORTANT 2: the receipt must state the number that exposes the hole -- we
    # have never once watched this aircraft touch the ground here.
    assert "0 full-stop landings" in overnight_text


def test_one_trailing_unpaired_takeoff_does_not_suppress_the_gate(tmp_path):
    """Mirror of the unpaired-landing case: a single takeoff we never matched to
    a landing we watched is the aircraft having departed from the ramp it was
    already sitting on when the window opened -- the expected steady state, not
    a data hole. It must not suppress the overnight gate on its own; two must.
    """
    setup, ro, rw = build_dbs(tmp_path)
    for i in range(10):
        _op(setup, f"ot{i}", "touch_and_go", NOW - (i + 20) * DAY, icao24="tko222", origin="KBDU")
    # One takeoff with no landing we ever watched to pair it with.
    _op(setup, "ot_t", "takeoff", NOW - (30) * DAY, icao24="tko222")

    result = homebase.classify(ro, "KLMO", "tko222", now_ts=NOW)
    assert result["locality"] == homebase.NON_LOCAL


def test_two_unpaired_takeoffs_do_suppress_the_gate(tmp_path):
    """The other half: TWO takeoffs we could never match to a landing we watched
    is a hole in the data, not the expected steady state, and must suppress the
    "0 overnights" weight exactly as two unpaired landings already do.
    """
    setup, ro, rw = build_dbs(tmp_path)
    for i in range(10):
        _op(setup, f"tu{i}", "touch_and_go", NOW - (i + 20) * DAY, icao24="tku333", origin="KBDU")
    _op(setup, "tu_t1", "takeoff", NOW - 30 * DAY, icao24="tku333")
    _op(setup, "tu_t2", "takeoff", NOW - 35 * DAY, icao24="tku333")

    result = homebase.classify(ro, "KLMO", "tku333", now_ts=NOW)
    assert result["locality"] != homebase.NON_LOCAL, (
        "two takeoffs we never matched to a watched landing should read as a "
        f"hole in the data, not evidence of non-locality -- got "
        f"{result['locality']} based on {result['based_icao']}"
    )


# ---------------------------------------------------------------------------
# ROUND 2 / IMPORTANTS 4-5 and MINORS 6-9.
# ---------------------------------------------------------------------------


def test_a_vetoed_verdict_never_publishes_a_signal_above_the_threshold(tmp_path):
    """IMPORTANT 5 -- a veto must not be defeatable by reading the number.

    The veto suppresses the VERDICT, but the score that produced it is unchanged --
    so a vetoed non-local published `unclassified` alongside `signal_strength=0.75`,
    above CONFIDENCE_THRESHOLD. Any consumer thresholding on the number instead of
    reading the word gets back exactly the accusation we just withheld.
    """
    setup, ro, rw = build_dbs(tmp_path)
    for i in range(14):
        _op(setup, f"cl{i}", "touch_and_go", NOW - (i + 1) * DAY, icao24="clm111", origin="KBDU")
    setup.execute(
        "INSERT INTO aircraft_registry (n_number, icao_hex, registrant_city, registrant_state) "
        "VALUES ('N8', 'CLM111', 'Longmont', 'CO')"
    )
    setup.commit()

    result = homebase.classify(ro, "KLMO", "clm111", now_ts=NOW)
    assert result["locality"] == homebase.UNCLASSIFIED
    assert result["signal_strength"] < homebase.CONFIDENCE_THRESHOLD, (
        f"a vetoed verdict published signal_strength={result['signal_strength']}, at "
        f"or above CONFIDENCE_THRESHOLD={homebase.CONFIDENCE_THRESHOLD} -- a consumer "
        f"thresholding on the number resurrects the verdict the veto suppressed"
    )

    # And the invariant, not just this one case: NO unclassified row, however it got
    # there, may ever publish a signal at or above the threshold.
    homebase.recompute_airport(ro, rw, "KLMO", now_ts=NOW)
    rw.commit()
    for icao24, row in homebase.locality_map(rw, "KLMO").items():
        if row["locality"] == homebase.UNCLASSIFIED:
            assert row["signal_strength"] < homebase.CONFIDENCE_THRESHOLD, icao24


def test_on_field_veto_is_a_live_backstop_not_dead_code(tmp_path, monkeypatch):
    """MINOR 8 -- the on_field veto was arithmetically unreachable.

    On-field adds at least +0.30 and the deepest reachable negative is -0.75, so
    -0.75 + 0.30 = -0.45 never crossed -0.50: the veto branch could not execute. The
    protection a parked aircraft actually enjoyed was that numeric coincidence, not
    the veto that claims to provide it -- and a future reweighting could take the
    coincidence away while the veto sat there looking like it still guarded the door.

    Neutralise the on-field WEIGHT (the coincidence) and the veto must still hold the
    line on its own.
    """
    setup, ro, rw = build_dbs(tmp_path)
    for i in range(20):
        _op(setup, f"bs{i}", "touch_and_go", NOW - (i + 3) * DAY, icao24="bck444", origin="KBDU")
    # Last operation: a full-stop landing 30h ago. As far as we can observe it is
    # sitting on the KLMO ramp right now.
    _op(setup, "bs_l", "landing", NOW - 30 * 3600, icao24="bck444", origin="KBDU")

    # Patching a WEIGHT, not the database: the SQLite below is real, and the point of
    # the test is that the verdict must not depend on this number's exact value.
    monkeypatch.setattr(homebase, "_W_RESIDENT_SOME", 0.0)
    result = homebase.classify(ro, "KLMO", "bck444", now_ts=NOW)

    assert result["locality"] != homebase.NON_LOCAL, (
        "with the on-field weight neutralised the veto did not hold: an aircraft on "
        "our own ramp was published as a visitor"
    )
    assert result["based_icao"] is None
    withheld = [e for e in result["evidence"] if e["code"] == "verdict_withheld"]
    assert withheld, "the veto must appear in the receipt, not happen silently"
    assert "still on the field" in withheld[0]["text"]


def test_origin_case_does_not_split_the_count(tmp_path):
    """MINOR 6 -- dominance was counted on raw strings and only the WINNER was
    upper-cased, so `KBDU` and `kbdu` split the Counter between them and suppressed
    the very signal that protects against a false verdict (or handed the plurality to
    a different field entirely). Normalise BEFORE counting.
    """
    setup, ro, rw = build_dbs(tmp_path)
    for i in range(14):
        origin = "KBDU" if i % 2 else "kbdu"      # same airport, two spellings
        _op(setup, f"cs{i}", "touch_and_go", NOW - (i + 1) * DAY, icao24="cse555", origin=origin)

    result = homebase.classify(ro, "KLMO", "cse555", now_ts=NOW)
    assert result["locality"] == homebase.NON_LOCAL
    assert result["based_icao"] == "KBDU"
    origin_text = next(e["text"] for e in result["evidence"] if e["code"] == "arrival_origin")
    assert "14 of those 14 days came from KBDU" in origin_text
    assert "kbdu" not in origin_text


def test_registrant_address_elsewhere_is_captioned_as_not_evidence(tmp_path):
    """MINOR 7 -- "Registrant address Boulder, CO", printed bare directly beneath a
    "non-local" badge, READS as corroboration of a verdict it took no part in. It
    scores 0.0 and it is a MAILING address, not a based airport. Say so.
    """
    setup, ro, rw = build_dbs(tmp_path)
    for i in range(14):
        _op(setup, f"cap{i}", "touch_and_go", NOW - (i + 1) * DAY, icao24="cap666", origin="KBDU")
    setup.execute(
        "INSERT INTO aircraft_registry (n_number, icao_hex, registrant_city, registrant_state) "
        "VALUES ('N6', 'CAP666', 'Boulder', 'CO')"
    )
    setup.commit()

    result = homebase.classify(ro, "KLMO", "cap666", now_ts=NOW)
    assert result["locality"] == homebase.NON_LOCAL      # scored by ORIGIN, not by this
    text = next(e["text"] for e in result["evidence"] if e["code"] == "registrant_address")
    assert "Boulder, CO" in text
    assert "mailing address" in text
    assert "not weighed against it" in text


def test_a_future_timestamped_operation_never_manufactures_a_verdict(tmp_path):
    """MINOR 9 -- `recompute_airport` selected aircraft on `timestamp >= start_ts`
    with no upper bound, while `_build_context` gathers facts BETWEEN start_ts AND
    now_ts. An aircraft whose only operations are in the future got an empty context
    and published `unclassified @ 0.0` -- a row asserting we looked and found nothing,
    about an aircraft we had not observed once inside the window.
    """
    setup, ro, rw = build_dbs(tmp_path)
    for d in (10, 20, 30, 40):
        _overnight(setup, d, "aaa111")
    # A clock-skewed sample / bad backfill: this aircraft exists ONLY in the future.
    _op(setup, "fut", "landing", NOW + 5 * DAY, icao24="fut999")

    assert homebase.recompute_airport(ro, rw, "KLMO", now_ts=NOW) == 1
    rw.commit()

    mapping = homebase.locality_map(rw, "KLMO")
    assert "fut999" not in mapping
    assert mapping["aaa111"]["locality"] == homebase.LOCAL


def test_recompute_airport_agrees_with_standalone_classify(tmp_path):
    """`recompute_airport` pairs the airport's dwells ONCE and shares the index.
    That is a pure optimisation: it must not change a single verdict.
    """
    setup, ro, rw = build_dbs(tmp_path)
    for d in (10, 20, 30, 40):
        _overnight(setup, d, "aaa111")
    for i in range(12):
        _op(setup, f"g{i}", "touch_and_go", NOW - i * DAY, icao24="bbb222", origin="KBDU")
    _op(setup, "park", "landing", NOW - 100 * DAY, icao24="ccc333", origin="KBDU")

    homebase.recompute_airport(ro, rw, "KLMO", now_ts=NOW)
    rw.commit()
    mapping = homebase.locality_map(rw, "KLMO")

    for icao24, stored in mapping.items():
        direct = homebase.classify(ro, "KLMO", icao24, now_ts=NOW)
        assert stored["locality"] == direct["locality"]
        assert stored["signal_strength"] == direct["signal_strength"]
        assert stored["based_icao"] == direct["based_icao"]
        assert stored["evidence"] == direct["evidence"]
