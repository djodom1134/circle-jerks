"""Heuristic owner-type inference from FAA registrant names.

Important product rule:
This module classifies the *registrant entity type*, not the pilot. The output
feeds the AircraftProfileCard's owner-type badge alongside an always-visible
disclaimer that the registered owner is not necessarily the pilot.

Returns a (owner_type, confidence, reason) tuple. When in doubt, returns
("unknown", 0.0, "...") rather than guessing.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class OwnerTypeResult:
    owner_type: str
    confidence: float
    reason: str


# Order matters: more specific patterns come first so e.g. "ACME FLIGHT SCHOOL,
# LLC" classifies as flight_school rather than llc. Each entry is
# (owner_type, confidence, matcher_function, reason_template).
_FLIGHT_SCHOOL_PATTERNS = (
    r"\bFLIGHT\s+SCHOOL\b",
    r"\bAVIATION\s+ACADEMY\b",
    r"\bFLIGHT\s+TRAINING\b",
    r"\bPILOT\s+TRAINING\b",
    r"\bGROUND\s+SCHOOL\b",
    r"\bACADEMY\s+OF\s+AVIATION\b",
)

_SKYDIVING_PATTERNS = (
    r"\bSKYDIV\w*\b",
    r"\bSKY\s+DIV\w*\b",
    r"\bPARACHUT\w*\b",
    r"\bDROP\s*ZONE\b",
    r"\bFREE\s*FALL\b",
)

_COMMERCIAL_AIRLINE_PATTERNS = (
    r"\bAIRLINES?\b",
    r"\bAIRWAYS\b",
    r"\bAIR\s+LINES?\b",
)

_CLUB_PATTERNS = (
    r"\bAERO\s+CLUB\b",
    r"\bFLYING\s+CLUB\b",
    r"\bFLIGHT\s+CLUB\b",
    r"\bSOARING\s+CLUB\b",
)

_UNIVERSITY_PATTERNS = (
    r"\bUNIVERSITY\b",
    r"\bCOLLEGE\b",
    r"\bINSTITUTE\s+OF\s+TECHNOLOGY\b",
    r"\bSCHOOL\s+DISTRICT\b",
)

_GOVERNMENT_PATTERNS = (
    r"\bCITY\s+OF\b",
    r"\bCOUNTY\s+OF\b",
    r"\bSTATE\s+OF\b",
    r"\bTOWN\s+OF\b",
    r"\bUNITED\s+STATES\b",
    r"\bU\.?\s*S\.?\s+(?:ARMY|NAVY|AIR\s+FORCE|MARINE|COAST\s+GUARD)\b",
    r"\bDEPARTMENT\s+OF\b",
    r"\bFAA\b",
    r"\bPOLICE\b",
    r"\bSHERIFF\b",
    r"\bFIRE\s+DEPARTMENT\b",
    r"\bPUBLIC\s+SAFETY\b",
    r"\bNATIONAL\s+GUARD\b",
)

_TRUST_PATTERNS = (
    r"\bTRUST\b",
    r"\bTRUSTEE\b",
    r"\bREVOCABLE\s+TRUST\b",
    r"\bLIVING\s+TRUST\b",
)

_LLC_PATTERNS = (
    r"\bL\.?\s*L\.?\s*C\.?\b",
    r"\bL\.?\s*L\.?\s*P\.?\b",
    r"\bLIMITED\s+LIABILITY\b",
)

_CORPORATION_PATTERNS = (
    r"\bINC\.?\b",
    r"\bINCORPORATED\b",
    r"\bCORP\.?\b",
    r"\bCORPORATION\b",
    r"\bCO\.?\b",
    r"\bCOMPANY\b",
    r"\bLTD\.?\b",
    r"\bLIMITED\b",
    r"\bGROUP\b",
    r"\bHOLDINGS\b",
    r"\bENTERPRISES\b",
    r"\bPARTNERS(?:HIP)?\b",
)


def _any_match(name: str, patterns) -> str | None:
    for pat in patterns:
        m = re.search(pat, name)
        if m:
            return m.group(0)
    return None


def _looks_like_individual(name: str) -> bool:
    """Two-or-three personal-name-shaped tokens, no company markers."""
    tokens = [t for t in re.split(r"[\s,]+", name) if t]
    if len(tokens) < 2 or len(tokens) > 4:
        return False
    for token in tokens:
        # Strip trailing dots / commas before checking shape so "JOHN." → "JOHN".
        bare = re.sub(r"[^A-Z]", "", token)
        if not bare:
            return False
        if len(bare) == 1:
            # Single-letter middle initial is fine.
            continue
        if not bare.isalpha():
            return False
    return True


def infer_owner_type(name: str | None) -> OwnerTypeResult:
    """Classify a registrant entity name into an owner_type bucket.

    The output is intentionally conservative: when the name doesn't clearly
    match a known pattern, we return "unknown" with confidence 0 so the UI
    shows the raw name without a misleading badge.
    """
    if not name:
        return OwnerTypeResult("unknown", 0.0, "Registrant name is empty")

    upper = re.sub(r"\s+", " ", name.upper().strip())

    hit = _any_match(upper, _FLIGHT_SCHOOL_PATTERNS)
    if hit:
        return OwnerTypeResult(
            "flight_school",
            0.95,
            f"Registrant name contains \"{hit}\"",
        )

    hit = _any_match(upper, _SKYDIVING_PATTERNS)
    if hit:
        return OwnerTypeResult(
            "skydiving",
            0.9,
            f"Registrant name contains \"{hit}\"",
        )

    hit = _any_match(upper, _COMMERCIAL_AIRLINE_PATTERNS)
    if hit:
        return OwnerTypeResult(
            "commercial_airline",
            0.9,
            f"Registrant name contains \"{hit}\"",
        )

    hit = _any_match(upper, _CLUB_PATTERNS)
    if hit:
        return OwnerTypeResult(
            "club",
            0.9,
            f"Registrant name contains \"{hit}\"",
        )

    hit = _any_match(upper, _UNIVERSITY_PATTERNS)
    if hit:
        return OwnerTypeResult(
            "university",
            0.9,
            f"Registrant name contains \"{hit}\"",
        )

    hit = _any_match(upper, _GOVERNMENT_PATTERNS)
    if hit:
        return OwnerTypeResult(
            "government",
            0.95,
            f"Registrant name contains \"{hit}\"",
        )

    hit = _any_match(upper, _TRUST_PATTERNS)
    if hit:
        return OwnerTypeResult(
            "trust",
            0.9,
            f"Registrant name contains \"{hit}\"",
        )

    hit = _any_match(upper, _LLC_PATTERNS)
    if hit:
        return OwnerTypeResult(
            "llc",
            0.95,
            f"Registrant name contains \"{hit}\"",
        )

    hit = _any_match(upper, _CORPORATION_PATTERNS)
    if hit:
        return OwnerTypeResult(
            "corporation",
            0.85,
            f"Registrant name contains \"{hit}\"",
        )

    if _looks_like_individual(upper):
        return OwnerTypeResult(
            "individual",
            0.7,
            "Registrant name looks like a personal name with no company markers",
        )

    return OwnerTypeResult(
        "unknown",
        0.0,
        "Registrant name didn't match any known owner-type pattern",
    )
