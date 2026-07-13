"""Is this aircraft based at the airport, or does it just use it?

There is no such classification anywhere else in the codebase; this is net-new.

DESIGN: this module emits a RECEIPT, not a verdict. Every classification carries
structured evidence that the UI renders inline next to it:

    Non-local — 0 overnight stays at KLMO in 180 days
              - 43 of 47 arrivals originated at KBDU
              - registrant address Boulder, CO

The reason is not decorum. The site names businesses and attaches dollar figures
to their activity. An unexplained "non-local" badge on a named flight school is a
correction waiting to happen, and one correction about a named business costs more
than the claim ever earned. Evidence shown inline is reproducible and is not a
guess. Anything we cannot support renders as `unclassified` and is COUNTED as such,
never quietly bucketed into a side we prefer.

PRIVACY: an N-number resolves to an owner name AND a home street address in one
FAA registry lookup, and many private aircraft are owned by individuals (a
single-member LLC is simply how an individual holds a personal aircraft). The
registrant signal below reads ONLY `aircraft_registry.registrant_city` and
`registrant_state` — city and state, never a name, street, or zip — and that is
the only registry-derived text that ever reaches an evidence string.
"""
from __future__ import annotations

import json
import sqlite3
from collections import Counter

from . import dwell

LOCAL = "local"
NON_LOCAL = "non_local"
UNCLASSIFIED = "unclassified"

# Below this, we say we don't know. Deliberately not tuned to maximise the
# non-local count.
CONFIDENCE_THRESHOLD = 0.5

DEFAULT_LOOKBACK_DAYS = 180

# A landing-to-takeoff gap this long means the aircraft slept there. The single
# strongest evidence that an aircraft is based at a field.
OVERNIGHT_SECONDS = 8 * 3600

# Weights. Overnight presence dominates; the registry address is a whisper.
_W_OVERNIGHT_STRONG = 0.6    # >= 3 overnights
_W_OVERNIGHT_SOME = 0.3      # 1-2 overnights
_W_OVERNIGHT_NONE = -0.4     # zero overnights across the whole lookback
_W_ORIGIN_HOME = 0.3         # arrivals mostly originate here
_W_ORIGIN_FOREIGN = -0.4     # arrivals mostly originate somewhere else
_W_REGISTRANT_NUDGE = 0.15   # registry city matches the airport's city

_MIN_ORIGIN_SAMPLE = 3
_ORIGIN_DOMINANCE = 0.5


def classify(
    conn: sqlite3.Connection,
    icao: str,
    icao24: str,
    now_ts: int,
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
) -> dict:
    icao = icao.upper()
    start_ts = int(now_ts) - lookback_days * 86400
    evidence: list[dict] = []
    score = 0.0
    based_icao: str | None = None

    # --- Signal 1: overnight presence (strongest) ---
    overnights = sum(
        1
        for interval in dwell.dwell_intervals(conn, icao, start_ts, int(now_ts))
        if interval["icao24"] == icao24 and interval["seconds"] >= OVERNIGHT_SECONDS
    )
    if overnights >= 3:
        score += _W_OVERNIGHT_STRONG
    elif overnights >= 1:
        score += _W_OVERNIGHT_SOME
    else:
        score += _W_OVERNIGHT_NONE
    evidence.append({
        "code": "overnight_stays",
        "text": f"{overnights} overnight stays at {icao} in {lookback_days} days",
    })

    # --- Signal 2: where its arrivals come from ---
    origins = [
        row["origin_airport_icao"]
        for row in conn.execute(
            "SELECT origin_airport_icao FROM operations "
            "WHERE icao=? AND icao24=? AND timestamp>=? "
            "  AND origin_airport_icao IS NOT NULL AND origin_airport_icao != ''",
            (icao, icao24, start_ts),
        ).fetchall()
    ]
    if len(origins) >= _MIN_ORIGIN_SAMPLE:
        top, count = Counter(origins).most_common(1)[0]
        share = count / len(origins)
        if share >= _ORIGIN_DOMINANCE:
            if top.upper() == icao:
                score += _W_ORIGIN_HOME
            else:
                score += _W_ORIGIN_FOREIGN
                based_icao = top.upper()
            evidence.append({
                "code": "arrival_origin",
                "text": f"{count} of {len(origins)} arrivals originated at {top.upper()}",
            })
    else:
        evidence.append({
            "code": "arrival_origin",
            "text": f"Origin airport known for only {len(origins)} arrivals — too few to weigh",
        })

    # --- Signal 3: registrant address (a whisper, never a decision) ---
    # The FAA registry holds a MAILING address, not a based airport. Only city
    # and state are read here — never registrant_name, registrant_street, or
    # registrant_zip — so no owner name or street address can ever reach this
    # evidence string, private individual or not.
    reg = conn.execute(
        "SELECT registrant_city, registrant_state FROM aircraft_registry "
        "WHERE icao_hex=? LIMIT 1",
        (icao24.upper(),),
    ).fetchone()
    airport_city = conn.execute(
        "SELECT city FROM airports WHERE icao=?", (icao,)
    ).fetchone()
    if reg and reg["registrant_city"] and airport_city:
        city = reg["registrant_city"].strip()
        state = (reg["registrant_state"] or "").strip()
        # airports.city is "Longmont, CO"; compare on the town only.
        town = airport_city["city"].split(",")[0].strip().lower()
        if city.lower() == town:
            score += _W_REGISTRANT_NUDGE
        evidence.append({
            "code": "registrant_address",
            "text": f"Registrant address {city}, {state}".strip().rstrip(","),
        })

    confidence = min(1.0, abs(score))
    if score >= CONFIDENCE_THRESHOLD:
        locality = LOCAL
    elif score <= -CONFIDENCE_THRESHOLD:
        locality = NON_LOCAL
    else:
        locality = UNCLASSIFIED

    return {
        "icao24": icao24,
        "locality": locality,
        "confidence": round(confidence, 4),
        "based_icao": based_icao if locality == NON_LOCAL else None,
        "evidence": evidence,
    }


def recompute_airport(
    conn: sqlite3.Connection,
    icao: str,
    now_ts: int,
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
) -> int:
    """Recompute and persist locality for every aircraft seen at `icao` in the window."""
    icao = icao.upper()
    start_ts = int(now_ts) - lookback_days * 86400
    aircraft = [
        row["icao24"]
        for row in conn.execute(
            "SELECT DISTINCT icao24 FROM operations "
            "WHERE icao=? AND timestamp>=? AND icao24 IS NOT NULL",
            (icao, start_ts),
        ).fetchall()
    ]
    for icao24 in aircraft:
        result = classify(conn, icao, icao24, now_ts, lookback_days)
        conn.execute(
            "INSERT INTO aircraft_home_base "
            "  (icao, icao24, locality, confidence, based_icao, evidence_json, computed_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(icao, icao24) DO UPDATE SET "
            "  locality=excluded.locality, confidence=excluded.confidence, "
            "  based_icao=excluded.based_icao, evidence_json=excluded.evidence_json, "
            "  computed_at=excluded.computed_at",
            (
                icao, icao24, result["locality"], result["confidence"],
                result["based_icao"], json.dumps(result["evidence"]), int(now_ts),
            ),
        )
    return len(aircraft)


def locality_map(conn: sqlite3.Connection, icao: str) -> dict[str, dict]:
    """icao24 -> {locality, confidence, based_icao, evidence} for one airport."""
    rows = conn.execute(
        "SELECT icao24, locality, confidence, based_icao, evidence_json "
        "FROM aircraft_home_base WHERE icao=?",
        (icao.upper(),),
    ).fetchall()
    return {
        row["icao24"]: {
            "locality": row["locality"],
            "confidence": row["confidence"],
            "based_icao": row["based_icao"],
            "evidence": json.loads(row["evidence_json"]),
        }
        for row in rows
    }
