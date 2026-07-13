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
    assert result["confidence"] >= homebase.CONFIDENCE_THRESHOLD
    codes = {e["code"] for e in result["evidence"]}
    assert "overnight_stays" in codes


def test_no_overnights_plus_foreign_origin_classifies_as_non_local(tmp_path):
    conn = seeded_conn(tmp_path / "t.sqlite3")
    # 10 touch-and-go visits, every one arriving from KBDU, never staying the night.
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
    assert result["confidence"] < homebase.CONFIDENCE_THRESHOLD


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
