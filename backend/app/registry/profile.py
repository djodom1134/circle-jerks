"""Aircraft profile resolver.

Merges three sources into a single profile dict for the AircraftProfileCard:
1. FAA aircraft registry rows (aircraft_registry table)
2. Observed ADS-B identity history (aircraft_observed_identity)
3. Caller-provided ADS-B observation (callsign at time of complaint)

Resolution order (per spec):
1. If nNumber is provided → registry lookup by n_number.
2. Else if icaoHex is provided → observed_identity mapping → registry by n_number.
3. Else if callsign looks like an N-number → registry by extracted n_number.
4. Else partial ADS-B-only profile.

Every profile returns the owner-not-pilot disclaimer at the top level.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Any

from .. import db
from . import codes, normalize, owner_type as owner_type_mod

OWNER_DISCLAIMER = (
    "Registered owner is not necessarily the pilot. Aircraft may be rented, "
    "leased, operated by a flight school, flown by a student, flown by an "
    "instructor, or otherwise operated by someone other than the registered owner."
)

AIRMEN_DISCLAIMER = (
    "Airmen records are name-based and do not prove who was flying this "
    "aircraft during this event."
)

PROFILE_VERSION = "1"


def get_aircraft_profile(
    conn: sqlite3.Connection,
    *,
    n_number: str | None = None,
    icao_hex: str | None = None,
    callsign: str | None = None,
) -> dict[str, Any]:
    """Resolve identifiers, fetch registry + observed rows, build a profile."""
    normalized_n = normalize.normalize_n_number(n_number) if n_number else None
    normalized_hex = normalize.normalize_icao_hex(icao_hex) if icao_hex else None
    normalized_call = (callsign or "").strip().upper() or None

    identity_notes: list[str] = []
    identity_confidence = 0.0

    registry_row: dict | None = None

    # Resolution order
    if normalized_n:
        registry_row = db.get_registry_by_n_number(conn, normalized_n)
        if registry_row:
            identity_confidence = 1.0
            identity_notes.append("Matched FAA registry by N-number.")
        else:
            identity_notes.append("Provided N-number had no match in the FAA registry.")

    if registry_row is None and normalized_hex:
        observed = db.get_observed_identity(conn, normalized_hex)
        if observed and observed.get("normalized_n_number"):
            obs_n = normalize.normalize_n_number(observed["normalized_n_number"])
            if obs_n:
                registry_row = db.get_registry_by_n_number(conn, obs_n)
                if registry_row:
                    normalized_n = obs_n
                    identity_confidence = float(observed.get("confidence") or 0.8)
                    identity_notes.append(
                        "Linked ICAO hex to a previously observed N-number broadcast."
                    )
        if registry_row is None:
            row_by_hex = db.get_registry_by_icao_hex(conn, normalized_hex)
            if row_by_hex:
                registry_row = row_by_hex
                normalized_n = row_by_hex.get("n_number") or normalized_n
                identity_confidence = max(identity_confidence, 0.9)
                identity_notes.append(
                    "Matched FAA registry by Mode-S hex code."
                )

    if registry_row is None and normalized_call:
        extracted = normalize.extract_n_number_from_callsign(normalized_call)
        if extracted:
            registry_row = db.get_registry_by_n_number(conn, extracted)
            if registry_row:
                normalized_n = extracted
                identity_confidence = max(identity_confidence, 0.85)
                identity_notes.append(
                    "Extracted likely N-number from observed callsign."
                )
            else:
                identity_notes.append(
                    "Callsign looked like an N-number but registry had no match."
                )

    if registry_row is None:
        identity_notes.append("No FAA registry record matched; showing ADS-B-only profile.")

    return _build_profile(
        registry_row=registry_row,
        n_number=normalized_n,
        icao_hex=normalized_hex or (registry_row.get("icao_hex") if registry_row else None),
        callsign=normalized_call,
        identity_confidence=identity_confidence,
        identity_notes=identity_notes,
    )


def _build_profile(
    *,
    registry_row: dict | None,
    n_number: str | None,
    icao_hex: str | None,
    callsign: str | None,
    identity_confidence: float,
    identity_notes: list[str],
) -> dict[str, Any]:
    now = datetime.now(timezone.utc).isoformat()

    aircraft: dict[str, Any] = {
        "manufacturer": None,
        "model": None,
        "yearManufactured": None,
        "engineType": None,
        "typeAircraft": None,
        "category": "Unknown",
    }
    registration: dict[str, Any] = {
        "status": None,
        "statusCode": None,
        "expirationDate": None,
        "certificateIssueDate": None,
        "isExpired": False,
        "isDeregistered": False,
    }
    registrant: dict[str, Any] = {
        "name": None,
        "city": None,
        "state": None,
        "country": None,
        "ownerType": "unknown",
        "ownerTypeConfidence": 0.0,
        "ownerTypeReason": None,
    }
    sources: list[dict[str, Any]] = []

    if registry_row:
        aircraft.update({
            "manufacturer": registry_row.get("manufacturer"),
            "model": registry_row.get("model"),
            "yearManufactured": registry_row.get("year_manufactured"),
            "engineType": registry_row.get("engine_type_label"),
            "typeAircraft": registry_row.get("type_aircraft_label"),
            "category": registry_row.get("category_label") or "Unknown",
        })
        status_label = registry_row.get("registration_status_label") or "Unknown"
        status_code = (registry_row.get("registration_status_code") or "").upper()
        registration.update({
            "status": status_label,
            "statusCode": status_code or None,
            "expirationDate": registry_row.get("registration_expiration_date"),
            "certificateIssueDate": registry_row.get("certificate_issue_date"),
            "isExpired": _is_expired(
                registry_row.get("registration_expiration_date"),
                status_code,
            ),
            "isDeregistered": status_code in {"M", "E", "9", "11", "12", "13", "14", "15", "16", "17", "18", "19", "21"},
        })
        registrant.update({
            "name": registry_row.get("registrant_name"),
            "city": registry_row.get("registrant_city"),
            "state": registry_row.get("registrant_state"),
            "country": registry_row.get("registrant_country"),
            "ownerType": registry_row.get("owner_type") or "unknown",
            "ownerTypeConfidence": float(registry_row.get("owner_type_confidence") or 0.0),
            "ownerTypeReason": registry_row.get("owner_type_reason"),
        })
        sources.append({
            "name": "FAA Aircraft Registry",
            "updatedAt": registry_row.get("source_updated_at") or registry_row.get("imported_at"),
            "retrievedAt": now,
        })

    sources.append({"name": "ADS-B Observation", "retrievedAt": now})

    airmen = _build_airmen_section(registrant)

    return {
        "identity": {
            "nNumber": n_number,
            "icaoHex": icao_hex,
            "callsign": callsign,
            "identityConfidence": round(identity_confidence, 3),
            "identityNotes": identity_notes,
        },
        "aircraft": aircraft,
        "registration": registration,
        "registrant": registrant,
        "airmen": airmen,
        "disclaimers": [OWNER_DISCLAIMER],
        "sources": sources,
        "profileVersion": PROFILE_VERSION,
    }


def _build_airmen_section(registrant: dict[str, Any]) -> dict[str, Any]:
    """Produce the airmen helper section.

    Always defensive: even when registrant looks like an individual, we DO
    NOT assert the registrant is the pilot. We only offer a name-based
    search helper, captioned with the airmen disclaimer.
    """
    owner = registrant.get("ownerType")
    if owner == "individual" and registrant.get("name"):
        from urllib.parse import urlencode

        # FAA Airmen Inquiry only accepts last/first name + restrictive forms.
        # Best we can do is link to the search page with a name hint; users
        # must click through manually. We intentionally do NOT prefill or scrape.
        query = urlencode({"name": registrant["name"]})
        return {
            "lookupAvailable": True,
            "lookupUrl": f"https://amsrvs.registry.faa.gov/airmeninquiry/?{query}",
            "lookupLabel": "Search FAA Airmen Inquiry for this name",
            "reason": (
                "Registrant looks like an individual; you may search the FAA "
                "Airmen Inquiry by name, but this does not prove they were "
                "the pilot of any observed flight."
            ),
            "disclaimer": AIRMEN_DISCLAIMER,
        }
    return {
        "lookupAvailable": False,
        "lookupUrl": None,
        "lookupLabel": None,
        "reason": (
            "Registrant appears to be an organization, not an individual"
            if registrant.get("name")
            else "No registrant on record"
        ),
        "disclaimer": AIRMEN_DISCLAIMER,
    }


def _is_expired(expiration_date: str | None, status_code: str) -> bool:
    if status_code in {"D", "6"}:
        return True
    if not expiration_date:
        return False
    try:
        exp = datetime.fromisoformat(expiration_date)
    except ValueError:
        return False
    return exp.date() < datetime.now(timezone.utc).date()
