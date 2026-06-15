"""FAA aircraft registry code → human label mappings.

Source: FAA's "Aircraft Registration Database Description" (current as of
ReleasableAircraft.zip). All unknown codes intentionally render as the literal
string "Unknown" so the UI can fall back to showing the raw code alongside.
"""

from __future__ import annotations

# MASTER.txt "TYPE AIRCRAFT" column (single digit, 1 byte).
# Includes types FAA has actually used in releasable data.
TYPE_AIRCRAFT_LABELS: dict[str, str] = {
    "1": "Glider",
    "2": "Balloon",
    "3": "Blimp / dirigible",
    "4": "Fixed wing single-engine",
    "5": "Fixed wing multi-engine",
    "6": "Rotorcraft",
    "7": "Weight-shift-control",
    "8": "Powered parachute",
    "9": "Gyroplane",
    "H": "Hybrid lift",
    "O": "Other",
}

# MASTER.txt "TYPE ENGINE" column.
ENGINE_TYPE_LABELS: dict[str, str] = {
    "0": "None",
    "1": "Reciprocating",
    "2": "Turbo-prop",
    "3": "Turbo-shaft",
    "4": "Turbo-jet",
    "5": "Turbo-fan",
    "6": "Ramjet",
    "7": "2 cycle",
    "8": "4 cycle",
    "9": "Unknown",
    "10": "Electric",
    "11": "Rotary",
}

# MASTER.txt "STATUS CODE" column. Curated to common values; the FAA dictionary
# is larger but rare codes (W, X, Z, etc.) are seldom seen in current data.
REGISTRATION_STATUS_LABELS: dict[str, str] = {
    "A": "Registered (triennial)",
    "V": "Registered",
    "D": "Expired (suspended)",
    "E": "Certificate revoked",
    "M": "Registration cancelled",
    "N": "Non-citizen corporation without authorization",
    "R": "Registration pending",
    "S": "Second notice for re-registration",
    "T": "Valid registration but undeliverable triennial",
    "U": "Unknown",
    "Z": "Permanent reserved",
    "1": "Triennial form mailed not yet received",
    "2": "Triennial form returned undeliverable",
    "3": "Pending re-registration acknowledgement",
    "4": "Second attempt to mail returned",
    "5": "Certificate of registration revoked",
    "6": "Registration expired",
    "7": "Sale reported",
    "8": "Second notice mailed (re-registration)",
    "9": "Certificate of registration deregistered",
    "10": "Reserved for future use",
    "11": "Registration cancelled at owner request",
    "12": "Registration cancelled (corporation dissolved)",
    "13": "Registration revoked by enforcement",
    "14": "Registration cancelled (registrant deceased)",
    "15": "Registration cancelled (registrant request, no replacement)",
    "16": "Registration cancelled (exported)",
    "17": "Sale reported, registration cancelled",
    "18": "Registration cancelled (scrapped/destroyed)",
    "19": "Registration cancelled, no replacement",
    "20": "Registration pending cancellation",
    "21": "Revoked",
}


def _lookup(table: dict[str, str], code: str | None) -> str:
    if code is None:
        return "Unknown"
    raw = code.strip().upper()
    if not raw:
        return "Unknown"
    return table.get(raw, "Unknown")


def type_aircraft_label(code: str | None) -> str:
    """Decode MASTER.TYPE AIRCRAFT → readable label."""
    return _lookup(TYPE_AIRCRAFT_LABELS, code)


def engine_type_label(code: str | None) -> str:
    """Decode MASTER.TYPE ENGINE → readable label."""
    return _lookup(ENGINE_TYPE_LABELS, code)


def registration_status_label(code: str | None) -> str:
    """Decode MASTER.STATUS CODE → readable label."""
    return _lookup(REGISTRATION_STATUS_LABELS, code)


# Derived display category combining type + engine. Used for the AircraftProfileCard
# header line. Returns one of: "single-engine piston", "multi-engine piston",
# "turboprop", "jet", "helicopter / rotorcraft", "glider", "balloon", "blimp",
# "weight-shift-control", "powered parachute", "gyroplane", "experimental",
# "Unknown".
def derived_category(
    type_aircraft: str | None,
    engine_type: str | None,
) -> str:
    t = (type_aircraft or "").strip().upper()
    e = (engine_type or "").strip().upper()

    if t == "1":
        return "glider"
    if t == "2":
        return "balloon"
    if t == "3":
        return "blimp"
    if t == "6":
        return "helicopter / rotorcraft"
    if t == "7":
        return "weight-shift-control"
    if t == "8":
        return "powered parachute"
    if t == "9":
        return "gyroplane"

    if t == "4":
        if e == "1":
            return "single-engine piston"
        if e == "2":
            return "turboprop"
        if e in {"4", "5"}:
            return "jet"
        if e == "10":
            return "electric single-engine"
        if e in {"7", "8"}:
            return "single-engine piston"
        return "single-engine"

    if t == "5":
        if e == "1":
            return "multi-engine piston"
        if e == "2":
            return "twin turboprop"
        if e in {"4", "5"}:
            return "jet"
        if e in {"7", "8"}:
            return "multi-engine piston"
        return "multi-engine"

    return "Unknown"
