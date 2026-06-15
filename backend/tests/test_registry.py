"""Tests for the FAA aircraft registry enrichment layer."""

from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path

import pytest

from app import db
from app.registry import codes, importer, normalize, owner_type
from app.registry import profile as registry_profile


# --- normalize ---------------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("N123AB", "N123AB"),
        ("n123ab", "N123AB"),
        ("  N-123-AB  ", "N123AB"),
        ("123AB", "N123AB"),
        ("N12345", "N12345"),
        ("N1", "N1"),
        # Invalid forms
        ("", None),
        (None, None),
        ("UAL237", None),  # starts with letter, can't be N-number
        ("N0", None),  # leading zero after N
        ("N123IA", None),  # contains I (FAA forbids I)
        ("N12345AB", None),  # too long
    ],
)
def test_normalize_n_number(raw, expected):
    assert normalize.normalize_n_number(raw) == expected


def test_normalize_icao_hex():
    assert normalize.normalize_icao_hex("a1b2c3") == "A1B2C3"
    assert normalize.normalize_icao_hex("A1B2C3") == "A1B2C3"
    assert normalize.normalize_icao_hex("a1-b2-c3") == "A1B2C3"
    assert normalize.normalize_icao_hex("ZZZZZZ") is None  # not hex
    assert normalize.normalize_icao_hex("A1B2C") is None  # too short
    assert normalize.normalize_icao_hex(None) is None


def test_extract_n_number_from_callsign():
    assert normalize.extract_n_number_from_callsign("N123AB  ") == "N123AB"
    assert normalize.extract_n_number_from_callsign("n123ab") == "N123AB"
    # Airline callsigns must NOT be treated as N-numbers
    assert normalize.extract_n_number_from_callsign("UAL237") is None
    assert normalize.extract_n_number_from_callsign("AAL100") is None
    assert normalize.extract_n_number_from_callsign("") is None
    assert normalize.extract_n_number_from_callsign(None) is None


# --- codes -------------------------------------------------------------------


def test_type_aircraft_label():
    assert codes.type_aircraft_label("4") == "Fixed wing single-engine"
    assert codes.type_aircraft_label("5") == "Fixed wing multi-engine"
    assert codes.type_aircraft_label("6") == "Rotorcraft"
    assert codes.type_aircraft_label("Z") == "Unknown"
    assert codes.type_aircraft_label(None) == "Unknown"


def test_engine_type_label():
    assert codes.engine_type_label("1") == "Reciprocating"
    assert codes.engine_type_label("4") == "Turbo-jet"
    assert codes.engine_type_label("5") == "Turbo-fan"
    assert codes.engine_type_label("Q") == "Unknown"


def test_registration_status_label():
    assert codes.registration_status_label("V") == "Registered"
    assert codes.registration_status_label("D") == "Expired (suspended)"
    assert codes.registration_status_label("M") == "Registration cancelled"
    assert codes.registration_status_label("?") == "Unknown"


def test_derived_category():
    assert codes.derived_category("4", "1") == "single-engine piston"
    assert codes.derived_category("5", "1") == "multi-engine piston"
    assert codes.derived_category("4", "5") == "jet"
    assert codes.derived_category("6", "3") == "helicopter / rotorcraft"
    assert codes.derived_category("4", "10") == "electric single-engine"
    assert codes.derived_category(None, None) == "Unknown"


# --- owner_type --------------------------------------------------------------


def test_owner_type_llc():
    result = owner_type.infer_owner_type("EXAMPLE AVIATION LLC")
    assert result.owner_type == "llc"
    assert result.confidence >= 0.9
    assert "LLC" in result.reason


def test_owner_type_corporation():
    result = owner_type.infer_owner_type("CESSNA AIRCRAFT INC")
    assert result.owner_type == "corporation"


def test_owner_type_government():
    assert owner_type.infer_owner_type("CITY OF LONGMONT").owner_type == "government"
    assert owner_type.infer_owner_type("BOULDER COUNTY SHERIFF").owner_type == "government"


def test_owner_type_flight_school():
    result = owner_type.infer_owner_type("MILE HIGH FLIGHT SCHOOL LLC")
    # Flight school takes priority over LLC
    assert result.owner_type == "flight_school"


def test_owner_type_university():
    assert owner_type.infer_owner_type("METRO STATE UNIVERSITY").owner_type == "university"


def test_owner_type_club():
    assert owner_type.infer_owner_type("LONGMONT FLYING CLUB").owner_type == "club"


def test_owner_type_trust():
    assert owner_type.infer_owner_type("SMITH FAMILY TRUST").owner_type == "trust"


def test_owner_type_individual():
    result = owner_type.infer_owner_type("JOHN A SMITH")
    assert result.owner_type == "individual"


def test_owner_type_unknown_when_blank():
    result = owner_type.infer_owner_type("")
    assert result.owner_type == "unknown"
    assert result.confidence == 0.0


def test_owner_type_unknown_when_unclassifiable():
    # Single token that's not a personal name shape and has no markers.
    result = owner_type.infer_owner_type("ACME12345")
    assert result.owner_type == "unknown"


# --- profile -----------------------------------------------------------------


@pytest.fixture
def temp_db():
    with tempfile.TemporaryDirectory() as tmpdir:
        path = str(Path(tmpdir) / "test.sqlite3")
        db.init_db(path)
        yield path


def test_profile_partial_when_no_registry(temp_db):
    with db.db_session(temp_db) as conn:
        result = registry_profile.get_aircraft_profile(
            conn,
            n_number=None,
            icao_hex="A1B2C3",
            callsign="UAL237",
        )
    assert result["identity"]["icaoHex"] == "A1B2C3"
    assert result["aircraft"]["category"] == "Unknown"
    assert result["registrant"]["name"] is None
    assert result["registrant"]["ownerType"] == "unknown"
    assert any("ADS-B-only" in note for note in result["identity"]["identityNotes"])
    # Owner-not-pilot disclaimer must always be present.
    assert any("not necessarily the pilot" in d for d in result["disclaimers"])


def test_profile_matches_registry_by_n_number(temp_db):
    with db.db_session(temp_db) as conn:
        db.upsert_aircraft_registry(conn, {
            "n_number": "N123AB",
            "icao_hex": "A1B2C3",
            "manufacturer": "CESSNA",
            "model": "172S",
            "year_manufactured": 2006,
            "type_aircraft_code": "4",
            "type_aircraft_label": codes.type_aircraft_label("4"),
            "engine_type_code": "1",
            "engine_type_label": codes.engine_type_label("1"),
            "category_label": codes.derived_category("4", "1"),
            "registrant_name": "EXAMPLE AVIATION LLC",
            "registrant_city": "LONGMONT",
            "registrant_state": "CO",
            "registrant_country": "US",
            "registration_status_code": "V",
            "registration_status_label": codes.registration_status_label("V"),
            "owner_type": "llc",
            "owner_type_confidence": 0.95,
            "owner_type_reason": "test",
            "source": "TEST",
        })
        conn.commit()
        result = registry_profile.get_aircraft_profile(conn, n_number="N123AB")

    assert result["aircraft"]["manufacturer"] == "CESSNA"
    assert result["aircraft"]["model"] == "172S"
    assert result["aircraft"]["category"] == "single-engine piston"
    assert result["registrant"]["ownerType"] == "llc"
    # LLC registrant should NOT get an airmen lookup CTA.
    assert result["airmen"]["lookupAvailable"] is False
    assert "Registered owner is not necessarily the pilot" in result["disclaimers"][0]


def test_profile_individual_owner_gets_airmen_helper_with_disclaimer(temp_db):
    with db.db_session(temp_db) as conn:
        db.upsert_aircraft_registry(conn, {
            "n_number": "N999XY",
            "registrant_name": "JANE Q PILOT",
            "owner_type": "individual",
            "owner_type_confidence": 0.7,
            "owner_type_reason": "test",
            "source": "TEST",
        })
        conn.commit()
        result = registry_profile.get_aircraft_profile(conn, n_number="N999XY")

    assert result["registrant"]["ownerType"] == "individual"
    assert result["airmen"]["lookupAvailable"] is True
    # The airmen helper MUST say it doesn't identify the pilot.
    assert "do not prove" in result["airmen"]["disclaimer"]
    # And the global owner-not-pilot disclaimer is always present.
    assert any("not necessarily the pilot" in d for d in result["disclaimers"])


def test_profile_resolves_via_observed_identity_when_only_icao_hex_given(temp_db):
    with db.db_session(temp_db) as conn:
        db.upsert_aircraft_registry(conn, {
            "n_number": "N321ZZ",
            "icao_hex": "B0CAFE",
            "manufacturer": "PIPER",
            "model": "PA-28",
            "registrant_name": "FLYING CLUB OF FOO",
            "owner_type": "club",
            "source": "TEST",
        })
        db.upsert_observed_identity(
            conn,
            icao_hex="B0CAFE",
            callsign="N321ZZ",
            normalized_n_number="N321ZZ",
            timestamp=1700000000,
            confidence=0.95,
        )
        conn.commit()
        result = registry_profile.get_aircraft_profile(conn, icao_hex="B0CAFE")

    assert result["identity"]["nNumber"] == "N321ZZ"
    assert result["aircraft"]["manufacturer"] == "PIPER"
    assert result["registrant"]["ownerType"] == "club"


def test_profile_callsign_only_lookup(temp_db):
    with db.db_session(temp_db) as conn:
        db.upsert_aircraft_registry(conn, {
            "n_number": "N42AB",
            "manufacturer": "BEECHCRAFT",
            "model": "BARON 58",
            "type_aircraft_code": "5",
            "engine_type_code": "1",
            "registrant_name": "JOHN A SMITH",
            "owner_type": "individual",
            "source": "TEST",
        })
        conn.commit()
        # Caller has no ICAO hex but the callsign clearly looks like an N-number.
        result = registry_profile.get_aircraft_profile(conn, callsign="N42AB  ")

    assert result["identity"]["nNumber"] == "N42AB"
    assert result["aircraft"]["model"] == "BARON 58"


def test_profile_expired_registration_flagged(temp_db):
    with db.db_session(temp_db) as conn:
        db.upsert_aircraft_registry(conn, {
            "n_number": "N555EX",
            "registration_status_code": "D",
            "registration_status_label": codes.registration_status_label("D"),
            "registration_expiration_date": "2020-01-01",
            "registrant_name": "EXAMPLE LLC",
            "owner_type": "llc",
            "source": "TEST",
        })
        conn.commit()
        result = registry_profile.get_aircraft_profile(conn, n_number="N555EX")

    assert result["registration"]["isExpired"] is True


def test_profile_never_claims_pilot_identity(temp_db):
    """No path through the profile should affirmatively identify the pilot."""
    with db.db_session(temp_db) as conn:
        db.upsert_aircraft_registry(conn, {
            "n_number": "N111ZZ",
            "registrant_name": "JANE Q PILOT",
            "owner_type": "individual",
            "source": "TEST",
        })
        conn.commit()
        result = registry_profile.get_aircraft_profile(conn, n_number="N111ZZ")

    serialized = repr(result).lower()
    # The owner-not-pilot disclaimer must be present and the airmen helper
    # must caveat itself with the "does not prove" / "do not prove" phrasing.
    assert "registered owner is not necessarily the pilot" in serialized
    assert "do not prove who was flying" in serialized
    # The profile must NEVER use any affirmative "this is the pilot"-shaped phrase.
    forbidden_phrases = [
        "the pilot is",
        "pilot:",
        "pilot_name",
        "pilot identified",
        "operated by the pilot",
        "flown by the registered owner",
    ]
    for phrase in forbidden_phrases:
        assert phrase not in serialized, f"Forbidden affirmative phrase leaked: {phrase!r}"
    # Registrant field is named registrant/owner, not pilot.
    assert "ownertype" in serialized  # ownerType field exists
    assert '"pilot"' not in serialized.replace(" ", "")  # no field literally named "pilot"


# --- importer ----------------------------------------------------------------


SAMPLE_ACFTREF = """CODE,MFR,MODEL,TYPE-ACFT,TYPE-ENG,AC-CAT,BUILD-CERT-IND,NO-ENG,NO-SEATS,AC-WEIGHT,SPEED
1234567 ,CESSNA              ,172S                ,4,1,1, ,1,4,CLASS 1, 140
9999999 ,PIPER               ,PA-28               ,4,1,1, ,1,4,CLASS 1, 130
"""

SAMPLE_MASTER = (
    "N-NUMBER,SERIAL NUMBER,MFR MDL CODE,ENG MFR MDL,YEAR MFR,TYPE REGISTRANT,NAME,STREET,STREET2,CITY,STATE,ZIP CODE,REGION,COUNTY,COUNTRY,LAST ACTION DATE,CERT ISSUE DATE,CERTIFICATION,TYPE AIRCRAFT,TYPE ENGINE,STATUS CODE,MODE S CODE,FRACT OWNER,AIR WORTH DATE,OTHER NAMES(1),OTHER NAMES(2),OTHER NAMES(3),OTHER NAMES(4),OTHER NAMES(5),EXPIRATION DATE,UNIQUE ID,KIT MFR,KIT MODEL,MODE S CODE HEX\n"
    "123AB,SN001,1234567,ENG1,2006,1,EXAMPLE AVIATION LLC,123 MAIN ST,,LONGMONT,CO,80501,3,008,US,20240101,20240501,1N,4,1,V,52404043,,20240501,,,,,,20270430,12345,,,A1B2C3\n"
    "999XY,SN002,9999999,ENG2,2010,1,JANE Q PILOT,1 MAIN ST,,BOULDER,CO,80301,3,008,US,20240101,20240601,1N,4,1,V,52404044,,20240601,,,,,,20280630,12346,,,DEADBE\n"
)


def test_importer_parses_sample_zip(temp_db):
    import io
    import zipfile

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("MASTER.txt", SAMPLE_MASTER)
        zf.writestr("ACFTREF.txt", SAMPLE_ACFTREF)
    buf.seek(0)

    master_text, acftref_text, _ = importer._extract(buf.getvalue())
    ref = importer._parse_acftref(acftref_text)
    rows = [row for (row, kind) in importer._iter_master(master_text, ref) if kind == "ok"]

    assert len(rows) == 2
    by_n = {r["n_number"]: r for r in rows}
    assert by_n["N123AB"]["manufacturer"] == "CESSNA"
    assert by_n["N123AB"]["model"] == "172S"
    assert by_n["N123AB"]["icao_hex"] == "A1B2C3"
    assert by_n["N123AB"]["owner_type"] == "llc"
    assert by_n["N999XY"]["owner_type"] == "individual"
    assert by_n["N999XY"]["registration_expiration_date"] == "2028-06-30"
