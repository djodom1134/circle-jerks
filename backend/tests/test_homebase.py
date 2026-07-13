from __future__ import annotations

import json

from app import db, homebase


def seeded_conn(path):
    conn = db.connect(str(path))
    conn.executescript(db.SCHEMA)
    db.seed_db(conn)
    conn.commit()
    return conn


def _op(conn, oid, type_, ts, icao24="a1", origin=None):
    db.upsert_operation(conn, db.operation_from_event({
        "id": oid, "type": type_, "icao24": icao24, "callsign": icao24.upper(),
        "timestamp": ts, "airport_icao": "KLMO",
    }))
    if origin:
        conn.execute("UPDATE operations SET origin_airport_icao=? WHERE id=?", (origin, oid))


NOW = 1780336800 + 90 * 86400   # ~90 days after our reference day
DAY = 86400


def _overnight(conn, day_offset, icao24):
    """A landing in the evening and a departure the next morning."""
    landed = NOW - day_offset * DAY - 4 * 3600      # evening
    departed = landed + 14 * 3600                    # next morning
    _op(conn, f"l{icao24}{day_offset}", "landing", landed, icao24=icao24)
    _op(conn, f"t{icao24}{day_offset}", "takeoff", departed, icao24=icao24)


def test_repeated_overnight_stays_classify_as_local(tmp_path):
    conn = seeded_conn(tmp_path / "t.sqlite3")
    for d in (10, 20, 30, 40):
        _overnight(conn, d, "aaa111")
    conn.commit()

    result = homebase.classify(conn, "KLMO", "aaa111", now_ts=NOW)
    assert result["locality"] == homebase.LOCAL
    assert result["signal_strength"] >= homebase.CONFIDENCE_THRESHOLD
    codes = {e["code"] for e in result["evidence"]}
    assert "overnight_stays" in codes


def test_no_overnights_plus_foreign_origin_classifies_as_non_local(tmp_path):
    conn = seeded_conn(tmp_path / "t.sqlite3")
    # 10 touch-and-go visits, every one arriving from KBDU, never staying the night.
    # A touch-and-go is SELF-CLOSING: we watched the visit begin and end in the
    # same operation, so "it never slept here" is an observation here, not an
    # absence -- and the KBDU origins are a positive, named observation of where
    # it comes from. Two positive observations. This is what a conviction costs.
    for i in range(10):
        _op(conn, f"g{i}", "touch_and_go", NOW - i * DAY, icao24="bbb222", origin="KBDU")
    conn.commit()

    result = homebase.classify(conn, "KLMO", "bbb222", now_ts=NOW)
    assert result["locality"] == homebase.NON_LOCAL
    assert result["based_icao"] == "KBDU"
    codes = {e["code"] for e in result["evidence"]}
    assert "overnight_stays" in codes
    assert "arrival_origin" in codes


def test_evidence_is_human_readable_and_states_the_numbers(tmp_path):
    conn = seeded_conn(tmp_path / "t.sqlite3")
    for i in range(10):
        _op(conn, f"g{i}", "touch_and_go", NOW - i * DAY, icao24="bbb222", origin="KBDU")
    conn.commit()

    result = homebase.classify(conn, "KLMO", "bbb222", now_ts=NOW)
    origin_text = next(e["text"] for e in result["evidence"] if e["code"] == "arrival_origin")
    assert "10" in origin_text and "KBDU" in origin_text
    overnight_text = next(e["text"] for e in result["evidence"] if e["code"] == "overnight_stays")
    assert "0" in overnight_text and "180" in overnight_text


def test_thin_evidence_is_unclassified_not_guessed(tmp_path):
    # One visit, no origin data, no overnight. We do not know. Say so.
    conn = seeded_conn(tmp_path / "t.sqlite3")
    _op(conn, "g1", "touch_and_go", NOW - DAY, icao24="ccc333")
    conn.commit()

    result = homebase.classify(conn, "KLMO", "ccc333", now_ts=NOW)
    assert result["locality"] == homebase.UNCLASSIFIED
    assert result["signal_strength"] < homebase.CONFIDENCE_THRESHOLD


def test_unclassified_still_carries_its_evidence(tmp_path):
    # Even a "we don't know" shows its working.
    conn = seeded_conn(tmp_path / "t.sqlite3")
    _op(conn, "g1", "touch_and_go", NOW - DAY, icao24="ccc333")
    conn.commit()
    result = homebase.classify(conn, "KLMO", "ccc333", now_ts=NOW)
    assert result["evidence"]


def test_registrant_address_alone_never_decides(tmp_path):
    # The FAA registry records a MAILING address, not a based airport (see the
    # owner-not-pilot disclaimer at registry/profile.py:26 for the same caution
    # applied to a different registry field). It may nudge; it may not decide.
    conn = seeded_conn(tmp_path / "t.sqlite3")
    _op(conn, "g1", "touch_and_go", NOW - DAY, icao24="ddd444")
    conn.execute(
        "INSERT INTO aircraft_registry (n_number, icao_hex, registrant_city, registrant_state) "
        "VALUES ('N1', 'DDD444', 'Boulder', 'CO')"
    )
    conn.commit()

    result = homebase.classify(conn, "KLMO", "ddd444", now_ts=NOW)
    assert result["locality"] == homebase.UNCLASSIFIED


def test_evidence_never_contains_registrant_name_or_street(tmp_path):
    # PRIVACY BOUNDARY: an N-number resolves to an owner name AND a home street
    # address in one FAA registry lookup, and many private aircraft are owned by
    # individuals (a single-member LLC is just how an individual holds a personal
    # aircraft). Evidence may use registrant_city/registrant_state ONLY. Prove
    # that a populated name/street column can never leak into a public evidence
    # string, no matter what classify() decides.
    conn = seeded_conn(tmp_path / "t.sqlite3")
    _op(conn, "g1", "touch_and_go", NOW - DAY, icao24="eee555")
    conn.execute(
        "INSERT INTO aircraft_registry "
        "(n_number, icao_hex, registrant_name, registrant_street, registrant_city, "
        " registrant_state, registrant_zip) "
        "VALUES ('N2', 'EEE555', 'Jane Q. Smith', '123 Main St', 'Longmont', 'CO', '80501')"
    )
    conn.commit()

    result = homebase.classify(conn, "KLMO", "eee555", now_ts=NOW)
    blob = json.dumps(result["evidence"])
    for leak in ("Jane", "Smith", "Main St", "123", "80501"):
        assert leak not in blob


def test_recompute_airport_persists_and_is_idempotent(tmp_path):
    conn = seeded_conn(tmp_path / "t.sqlite3")
    for d in (10, 20, 30, 40):
        _overnight(conn, d, "aaa111")
    for i in range(10):
        _op(conn, f"g{i}", "touch_and_go", NOW - i * DAY, icao24="bbb222", origin="KBDU")
    conn.commit()

    assert homebase.recompute_airport(conn, "KLMO", now_ts=NOW) == 2
    conn.commit()
    assert homebase.recompute_airport(conn, "KLMO", now_ts=NOW) == 2  # upsert, not duplicate
    conn.commit()

    rows = conn.execute("SELECT * FROM aircraft_home_base WHERE icao='KLMO'").fetchall()
    assert len(rows) == 2
    by_ac = {r["icao24"]: r for r in rows}
    assert by_ac["aaa111"]["locality"] == homebase.LOCAL
    assert by_ac["bbb222"]["locality"] == homebase.NON_LOCAL
    assert json.loads(by_ac["bbb222"]["evidence_json"])


def test_locality_map_reads_back_what_recompute_wrote(tmp_path):
    conn = seeded_conn(tmp_path / "t.sqlite3")
    for i in range(10):
        _op(conn, f"g{i}", "touch_and_go", NOW - i * DAY, icao24="bbb222", origin="KBDU")
    conn.commit()
    homebase.recompute_airport(conn, "KLMO", now_ts=NOW)
    conn.commit()

    mapping = homebase.locality_map(conn, "KLMO")
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
    conn = seeded_conn(tmp_path / "t.sqlite3")
    # Four arrivals from KBDU over a fortnight, then it lands and never leaves.
    for i, d in enumerate((174, 172, 168, 160)):
        _op(conn, f"p{i}", "landing", NOW - d * DAY, icao24="fff666", origin="KBDU")
    conn.commit()

    result = homebase.classify(conn, "KLMO", "fff666", now_ts=NOW)
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
    conn = seeded_conn(tmp_path / "t.sqlite3")
    _op(conn, "w1", "landing", NOW - 3 * DAY, icao24="www000", origin="KBDU")
    conn.commit()

    result = homebase.classify(conn, "KLMO", "www000", now_ts=NOW)
    assert result["locality"] == homebase.UNCLASSIFIED


def test_three_arrivals_are_too_thin_to_convict(tmp_path):
    """Reviewer probe P2: three observed arrivals used to reach non_local @ 0.8."""
    conn = seeded_conn(tmp_path / "t.sqlite3")
    for i in range(3):
        _op(conn, f"q{i}", "touch_and_go", NOW - (i + 1) * DAY, icao24="ggg777", origin="KBDU")
    conn.commit()

    result = homebase.classify(conn, "KLMO", "ggg777", now_ts=NOW)
    assert result["locality"] == homebase.UNCLASSIFIED


def test_missed_departures_are_not_evidence_of_absence(tmp_path):
    """Reviewer probe P3: 59 landings, the takeoff detector missed every departure.

    Zero landing->takeoff pairs exist, so "0 overnight stays" is not an
    observation -- it is a hole in our data. `dwell.py:51-53` names missed
    departures as an EXPECTED condition. It must score nothing.
    """
    conn = seeded_conn(tmp_path / "t.sqlite3")
    for i in range(59):
        _op(conn, f"r{i}", "landing", NOW - (i + 2) * DAY, icao24="hhh888", origin="KBDU")
    conn.commit()

    result = homebase.classify(conn, "KLMO", "hhh888", now_ts=NOW)
    assert result["locality"] != homebase.NON_LOCAL
    overnight_text = next(
        e["text"] for e in result["evidence"] if e["code"] == "overnight_stays"
    )
    # The receipt must say WHY it weighed nothing, in the reader's language: we
    # saw 59 arrivals and watched it leave again 0 times.
    assert "59 observed arrivals" in overnight_text
    assert "only 0 times" in overnight_text


def test_all_null_origins_can_never_produce_a_non_local(tmp_path):
    """CRITICAL 2: `operations.origin_airport_icao` is forward-fill only.

    Historical origin is unrecoverable, so for the ledger's 180-day window at
    launch, origin is NULL for essentially every row. With no origin data the
    classifier must degrade to local/unclassified and NEVER manufacture a
    non-local verdict out of the absence.
    """
    conn = seeded_conn(tmp_path / "t.sqlite3")

    # A resident: sleeps here constantly.
    for d in range(10, 90, 7):
        _overnight(conn, d, "res001")
    # The archetypal "should be non-local if only we knew": 30 touch-and-goes,
    # never stays, but we have no idea where it comes from.
    for i in range(30):
        _op(conn, f"n{i}", "touch_and_go", NOW - (i + 1) * DAY, icao24="vis002")
    # Parked on the ramp.
    _op(conn, "park", "landing", NOW - 100 * DAY, icao24="prk003")
    # A one-off.
    _op(conn, "one", "landing", NOW - 40 * DAY, icao24="one004")
    _op(conn, "onet", "takeoff", NOW - 40 * DAY + 3600, icao24="one004")
    # Missed departures.
    for i in range(20):
        _op(conn, f"m{i}", "landing", NOW - (i + 2) * DAY, icao24="mis005")
    conn.commit()

    assert conn.execute(
        "SELECT COUNT(*) AS n FROM operations WHERE origin_airport_icao IS NOT NULL"
    ).fetchone()["n"] == 0

    homebase.recompute_airport(conn, "KLMO", now_ts=NOW)
    conn.commit()

    mapping = homebase.locality_map(conn, "KLMO")
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
    conn = seeded_conn(tmp_path / "t.sqlite3")
    for i in range(30):
        _op(conn, f"n{i}", "touch_and_go", NOW - (i + 1) * DAY, icao24="vis002")
    conn.commit()
    assert homebase.classify(conn, "KLMO", "vis002", now_ts=NOW)["locality"] == (
        homebase.UNCLASSIFIED
    )

    # Origin coverage accrues forward: the origin writer starts filling in arrivals.
    conn.execute(
        "UPDATE operations SET origin_airport_icao='KBDU' "
        "WHERE icao24='vis002' AND id IN (SELECT id FROM operations "
        "  WHERE icao24='vis002' ORDER BY timestamp DESC LIMIT 14)"
    )
    conn.commit()

    result = homebase.classify(conn, "KLMO", "vis002", now_ts=NOW)
    assert result["locality"] == homebase.NON_LOCAL
    assert result["based_icao"] == "KBDU"


def test_non_local_always_names_the_airport_it_came_from(tmp_path):
    """A non-local verdict may never rest on two absences.

    Whatever the arithmetic, `based_icao` -- a POSITIVE, named observation of
    where we watched it fly in from -- is a precondition for the verdict.
    """
    conn = seeded_conn(tmp_path / "t.sqlite3")
    for i in range(40):
        _op(conn, f"n{i}", "touch_and_go", NOW - (i + 1) * DAY, icao24="vis002")
    for i in range(20):
        _op(conn, f"k{i}", "touch_and_go", NOW - (i + 60) * DAY, icao24="vis002", origin="KBDU")
    conn.commit()

    for row in homebase.locality_map(conn, "KLMO").values():
        if row["locality"] == homebase.NON_LOCAL:
            assert row["based_icao"]

    result = homebase.classify(conn, "KLMO", "vis002", now_ts=NOW)
    if result["locality"] == homebase.NON_LOCAL:
        assert result["based_icao"] == "KBDU"


def test_repeated_overnight_stays_outrank_a_foreign_arrival_origin(tmp_path):
    """A KLMO-based aircraft that shuttles to Boulder daily has EVERY arrival
    originating at KBDU. Where it flies in from describes its trips, not its home.
    """
    conn = seeded_conn(tmp_path / "t.sqlite3")
    for d in range(10, 80, 5):
        _overnight(conn, d, "shu111")
    for i in range(20):
        _op(conn, f"s{i}", "landing", NOW - (i + 90) * DAY, icao24="shu111", origin="KBDU")
        _op(conn, f"st{i}", "takeoff", NOW - (i + 90) * DAY + 3600, icao24="shu111")
    conn.commit()

    result = homebase.classify(conn, "KLMO", "shu111", now_ts=NOW)
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
    conn = seeded_conn(tmp_path / "t.sqlite3")
    for i in range(14):
        _op(conn, f"s{i}", "touch_and_go", NOW - (i + 1) * DAY, icao24="iii999", origin="KBDU")
    conn.execute(
        "INSERT INTO aircraft_registry (n_number, icao_hex, registrant_city, registrant_state) "
        "VALUES ('N9', 'III999', 'Longmont', 'CO')"
    )
    conn.commit()

    result = homebase.classify(conn, "KLMO", "iii999", now_ts=NOW)
    assert result["locality"] == homebase.UNCLASSIFIED
    assert result["based_icao"] is None
    withheld = [e for e in result["evidence"] if e["code"] == "verdict_withheld"]
    assert withheld, "the veto must appear in the receipt, not happen silently"


def test_the_registrant_nudge_can_never_manufacture_a_non_local(tmp_path):
    """A registrant in some other town scores nothing -- never a penalty. Plenty of
    people keep an aeroplane at the field down the road from where they get post.
    """
    conn = seeded_conn(tmp_path / "t.sqlite3")
    for d in (10, 20, 30, 40):
        _overnight(conn, d, "reg777")
    conn.execute(
        "INSERT INTO aircraft_registry (n_number, icao_hex, registrant_city, registrant_state) "
        "VALUES ('N7', 'REG777', 'Anchorage', 'AK')"
    )
    conn.commit()

    result = homebase.classify(conn, "KLMO", "reg777", now_ts=NOW)
    assert result["locality"] == homebase.LOCAL


# ---------------------------------------------------------------------------
# I3 / I4 -- the number is not a probability; a takeoff is not an arrival.
# ---------------------------------------------------------------------------


def test_signal_strength_is_never_certainty(tmp_path):
    """Under the floor constraint no classification here can be certain. Stack
    every positive signal the model has and it must still land below 1.0.
    """
    conn = seeded_conn(tmp_path / "t.sqlite3")
    for d in range(20, 90, 5):
        _overnight(conn, d, "max222")
    for i in range(14):
        _op(conn, f"h{i}", "touch_and_go", NOW - (i + 100) * DAY, icao24="max222", origin="KLMO")
    _op(conn, "sit", "landing", NOW - 15 * DAY, icao24="max222", origin="KLMO")  # still here
    conn.execute(
        "INSERT INTO aircraft_registry (n_number, icao_hex, registrant_city, registrant_state) "
        "VALUES ('N5', 'MAX222', 'Longmont', 'CO')"
    )
    conn.commit()

    result = homebase.classify(conn, "KLMO", "max222", now_ts=NOW)
    assert result["locality"] == homebase.LOCAL
    assert result["signal_strength"] < 1.0
    assert result["signal_strength"] == homebase.MAX_SIGNAL_STRENGTH


def test_the_worst_non_local_badge_we_can_print_is_still_not_certain(tmp_path):
    conn = seeded_conn(tmp_path / "t.sqlite3")
    for i in range(40):
        _op(conn, f"z{i}", "touch_and_go", NOW - (i + 1) * DAY, icao24="wor333", origin="KBDU")
    conn.commit()

    result = homebase.classify(conn, "KLMO", "wor333", now_ts=NOW)
    assert result["locality"] == homebase.NON_LOCAL
    assert result["signal_strength"] <= 0.75


def test_takeoffs_are_never_counted_as_arrivals(tmp_path):
    """The origins query had no `type` filter while its evidence called the rows
    "arrivals". Task 4a means takeoffs never GET an origin -- but the query must be
    true by construction, not by a coincidence upstream.
    """
    conn = seeded_conn(tmp_path / "t.sqlite3")
    for i in range(10):
        _op(conn, f"g{i}", "touch_and_go", NOW - (i + 1) * DAY, icao24="bbb222", origin="KBDU")
    # Poison the well: 30 TAKEOFFS carrying an origin of KLMO. If takeoffs counted,
    # KLMO would dominate 30/40 and the aircraft would be scored as LOCAL.
    for i in range(30):
        _op(conn, f"tk{i}", "takeoff", NOW - (i + 40) * DAY, icao24="bbb222", origin="KLMO")
    conn.commit()

    result = homebase.classify(conn, "KLMO", "bbb222", now_ts=NOW)
    assert result["locality"] == homebase.NON_LOCAL
    assert result["based_icao"] == "KBDU"
    origin_text = next(e["text"] for e in result["evidence"] if e["code"] == "arrival_origin")
    # The denominator is the 10 arrivals, not the 40 operations.
    assert "10 of 10 observed arrivals" in origin_text
    assert "KLMO" not in origin_text


def test_arrival_types_match_the_types_the_origin_writer_populates(tmp_path):
    """Drift guard. `homebase.ARRIVAL_TYPES` is the denominator of every published
    "N of M arrivals" claim; `services.ORIGIN_ELIGIBLE_OP_TYPES` is the set of
    types that can ever HAVE an origin. If they diverge, the receipt starts lying.
    """
    from app import services

    assert set(homebase.ARRIVAL_TYPES) == set(services.ORIGIN_ELIGIBLE_OP_TYPES)


# ---------------------------------------------------------------------------
# I1 -- evidence describes what we OBSERVED, never what happened.
# ---------------------------------------------------------------------------


def test_evidence_states_observations_and_true_denominators(tmp_path):
    """"0 overnight stays at KLMO in 180 days" asserts the aircraft did not stay.
    What we know is that we observed no landing->takeoff pair of >= 8h. Likewise
    "4 of 4 arrivals originated at KBDU" implies it had 4 arrivals; it had 4
    arrivals WHOSE ORIGIN WE HAPPENED TO KNOW, possibly out of 200.
    """
    conn = seeded_conn(tmp_path / "t.sqlite3")
    # 20 arrivals. We know the origin of only 9 of them.
    for i in range(20):
        origin = "KBDU" if i < 9 else None
        _op(conn, f"e{i}", "touch_and_go", NOW - (i + 1) * DAY, icao24="obs444", origin=origin)
    conn.commit()

    result = homebase.classify(conn, "KLMO", "obs444", now_ts=NOW)
    texts = {e["code"]: e["text"] for e in result["evidence"]}

    # Every count is a floor, and every claim is about our observations.
    assert "observed" in texts["overnight_stays"]
    assert "arrival and the departure" in texts["overnight_stays"]
    # The TRUE denominator: 9 known origins out of 20 observed arrivals.
    assert "Origin known for 9 of 20 observed arrivals" in texts["arrival_origin"]
    assert "9 of those 9 came from KBDU" in texts["arrival_origin"]
    # Not a single evidence string may assert a fact we did not observe.
    for text in texts.values():
        assert "did not" not in text


def test_recompute_airport_agrees_with_standalone_classify(tmp_path):
    """`recompute_airport` pairs the airport's dwells ONCE and shares the index.
    That is a pure optimisation: it must not change a single verdict.
    """
    conn = seeded_conn(tmp_path / "t.sqlite3")
    for d in (10, 20, 30, 40):
        _overnight(conn, d, "aaa111")
    for i in range(12):
        _op(conn, f"g{i}", "touch_and_go", NOW - i * DAY, icao24="bbb222", origin="KBDU")
    _op(conn, "park", "landing", NOW - 100 * DAY, icao24="ccc333", origin="KBDU")
    conn.commit()

    homebase.recompute_airport(conn, "KLMO", now_ts=NOW)
    conn.commit()
    mapping = homebase.locality_map(conn, "KLMO")

    for icao24, stored in mapping.items():
        direct = homebase.classify(conn, "KLMO", icao24, now_ts=NOW)
        assert stored["locality"] == direct["locality"]
        assert stored["signal_strength"] == direct["signal_strength"]
        assert stored["based_icao"] == direct["based_icao"]
        assert stored["evidence"] == direct["evidence"]
