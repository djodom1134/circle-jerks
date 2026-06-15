"""Normalization helpers for the three identifiers that follow an aircraft
across systems: N-number, ICAO Mode-S hex, and callsign.

These return clean canonical forms; they never throw. Callers feed dirty input
(callsigns with trailing spaces, mixed-case hex from various ADS-B feeds,
N-numbers users type as "N-12345" or "n12345") and get back either the
canonical string or None.
"""

from __future__ import annotations

import re

# N-numbers: leading "N" + 1 to 5 alphanumeric characters, with a few rules:
# - first character after N must be a digit (cannot start with letter)
# - last 1 or 2 characters can be letters
# - cannot contain I or O (FAA reserves to avoid confusion with 1 / 0)
# - max 5 chars after the N (so "N12345" and "N99999" are the longest pure-digit
#   forms; "N1234A" / "N123AB" are valid letter-suffixed forms)
_N_NUMBER_BODY = re.compile(r"^[1-9][0-9]{0,3}[A-HJ-NP-Z]{0,2}$|^[1-9][0-9]{1,4}$")


def normalize_n_number(value: str | None) -> str | None:
    """Return canonical N-number form (e.g. "N123AB") or None.

    Strips whitespace, hyphens, dots. Adds a leading "N" if the input is a
    bare body (e.g. "123AB" → "N123AB"). Returns None when the input cannot
    be a real FAA N-number; callers should use this signal to decide whether
    to attempt a registry lookup at all.
    """
    if value is None:
        return None
    cleaned = re.sub(r"[\s\-.]+", "", value).upper()
    if not cleaned:
        return None
    if cleaned.startswith("N"):
        cleaned = cleaned[1:]
    if not cleaned:
        return None
    if not _N_NUMBER_BODY.match(cleaned):
        return None
    return "N" + cleaned


_HEX_BODY = re.compile(r"^[0-9A-F]{6}$")


def normalize_icao_hex(value: str | None) -> str | None:
    """Return canonical 6-char uppercase Mode-S hex, or None."""
    if value is None:
        return None
    cleaned = re.sub(r"[\s\-]+", "", value).upper()
    if not _HEX_BODY.match(cleaned):
        return None
    return cleaned


def extract_n_number_from_callsign(callsign: str | None) -> str | None:
    """Extract an N-number from an ADS-B callsign if one is plausibly encoded.

    ADS-B callsigns are 8-char ASCII, often padded with spaces. US GA aircraft
    typically broadcast their N-number as the callsign (e.g. "N123AB  "), but
    commercial aircraft do not (they broadcast flight numbers like "UAL237").
    Only return a value if the callsign matches the N-number grammar; do NOT
    treat every callsign as an N-number.
    """
    if not callsign:
        return None
    candidate = callsign.strip().upper()
    if not candidate.startswith("N"):
        return None
    return normalize_n_number(candidate)
