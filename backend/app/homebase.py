"""Is this aircraft based at the airport, or does it just use it?

There is no such classification anywhere else in the codebase; this is net-new.

DESIGN: this module emits a RECEIPT, not a verdict. Every classification carries
structured evidence that the UI renders inline next to it:

    Non-local — 0 overnight stays observed at KLMO in the last 180 days, across
                18 visits where we observed both the arrival and the departure
              - Origin known for 14 of 47 observed arrivals; 13 of those 14
                came from KBDU
              - Registrant address Boulder, CO

The reason is not decorum. The site names businesses and attaches dollar figures
to their activity. An unexplained "non-local" badge on a named flight school is a
correction waiting to happen, and one correction about a named business costs more
than the claim ever earned. Evidence shown inline is reproducible and is not a
guess. Anything we cannot support renders as `unclassified` and is COUNTED as such,
never quietly bucketed into a side we prefer.

THE FLOOR CONSTRAINT — the rule that shapes every weight below.
Everything this module can see is a FLOOR, not a measurement:

  * `dwell.dwell_intervals` drops a landing that has no matching takeoff
    (dwell.py:54-55), and `dwell_summary` publishes `coverage` precisely because
    landing->takeoff pairing is known to be lossy (dwell.py:51-53).
  * ADS-B has gaps, and not every aircraft is equipped.
  * `operations.origin_airport_icao` is forward-fill only: it is written from a
    locally-observed ground track on arrivals from Task 4a forward, and can never
    be recovered for an operation already in the database. For the ledger's first
    180-day window, origin is NULL for essentially every row.

So the ONLY honest reading of a zero is "we did not observe one", never "there
was not one". THEREFORE:

  ABSENCE IS NEVER EVIDENCE. Not a single weight in this module is triggered by
  something we failed to see. Every negative weight is triggered by something we
  DID see. A NON_LOCAL verdict — the only classification here that can hurt a
  real, named party — additionally requires a POSITIVE, NAMED observation of
  non-locality (arrivals we watched come in from a specific other airport). With
  no origin data at all, `non_local` is unreachable, arithmetically and
  structurally. It becomes reachable only as real origin coverage accrues.

ERROR DIRECTION, STATED PLAINLY. This model is deliberately asymmetric. Where it
cannot tell "the aircraft is parked here" from "we missed its departure", it
resolves toward LOCAL and UNCLASSIFIED, because those are the errors that cost
nobody anything. A false `local` slightly understates the ledger. A false
`non_local` is a public accusation against a named business. We take the first
error every time, on purpose.

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
#
# NOTE ON THE NAME: the number this is compared against is `signal_strength`, not
# a probability (see MAX_SIGNAL_STRENGTH). The constant keeps its published name
# because callers and the plan pin it; the FIELD was renamed, because a field
# called `confidence` sitting next to a "non-local" badge invites a reader to
# take 0.65 as "65% likely to be a visitor", which it is not and cannot be.
CONFIDENCE_THRESHOLD = 0.5

DEFAULT_LOOKBACK_DAYS = 180

# Nothing here is a probability, so nothing here may be published as a certainty.
# Under the floor constraint every input is a lower bound, so the top of the
# scale must stay strictly below 1.0 no matter how the weights are retuned.
MAX_SIGNAL_STRENGTH = 0.95

# A landing-to-takeoff gap this long means the aircraft slept there. The single
# strongest evidence that an aircraft is based at a field.
OVERNIGHT_SECONDS = 8 * 3600

# Operation types that are an ARRIVAL at this airport, i.e. the types for which
# an origin airport is a meaningful, honest signal. A takeoff's origin is
# trivially this airport, so counting takeoffs would poison the "N of M arrivals
# came from X" evidence with garbage. This mirrors
# `services.ORIGIN_ELIGIBLE_OP_TYPES` — the set of types the origin WRITER will
# ever populate — and a drift guard in the tests fails if the two diverge.
ARRIVAL_TYPES = ("landing", "touch_and_go", "low_approach")

# An arrival that ends in the same operation: the aircraft touched the runway (or
# didn't) and was airborne again seconds later. We do not need to pair these with
# a takeoff to know the visit ended — the visit ending IS the observation. They
# therefore count as observed departures for the coverage gate below.
_SELF_CLOSING_ARRIVAL_TYPES = ("touch_and_go", "low_approach")

# ---------------------------------------------------------------------------
# GATES. Each one exists to stop a weight firing on something we did not observe.
# ---------------------------------------------------------------------------

# "It never sleeps here" is only a claim we will publish once we have actually
# WATCHED the aircraft leave, repeatedly. A closed visit is one where we observed
# both the arrival and the departure: a paired landing->takeoff interval, or a
# self-closing arrival (touch-and-go / low approach). Below this many closed
# visits, zero overnights carries NO weight — it is an absence, not an
# observation.
#
# 5 is a judgment call, and it is worth saying so rather than dressing it up:
# there is no principled derivation, because we have no model of the detector's
# miss rate. It is set where five independent same-day departures stop looking
# like a run of luck. Raise it if the takeoff detector proves lossier than
# `dwell_summary.coverage` currently suggests.
_MIN_CLOSED_VISITS = 5

# An aircraft whose LAST observed operation here is a full-stop landing is, as far
# as we can see, still on the field. That is true whether it is genuinely parked
# or whether we simply missed its departure — and NEITHER of those is evidence
# that it lives somewhere else. So this signal is clamped non-negative and, above
# the 8h mark, it VETOES a non-local verdict outright.
#
# _RESIDENT_STRONG_SECONDS is where "still on the field" becomes strong enough to
# publish LOCAL on its own. 14 days, because a weekend visitor whose Monday
# departure we missed must NOT be published as local, while an aircraft that has
# sat un-superseded on our ramp for a fortnight is either based here, in
# maintenance here, or for sale here — and in the one remaining case (it left and
# we lost it entirely) it is not using the runway, which is what the ledger is
# about. This is the model's chosen error direction; see the module docstring.
_RESIDENT_STRONG_SECONDS = 14 * 86400

# Origin is the ONLY signal that can argue for non-locality, so its gate is the
# one that protects a named party, and it is set accordingly.
#
# _MIN_ORIGIN_SAMPLE was 3. Three known origins is a single weekend: two flights
# out of three swung a -0.4 penalty and, with one more weak signal, published a
# named business as a visitor. It is now 8 — roughly two months of a weekly flyer
# — so that the SMALLEST sample able to contribute at all reads as a habit rather
# than a phase. This is a judgment call, not a derived threshold: we have no prior
# over GA origin mixes to compute a p-value against, and inventing one would be
# false precision. The direction of the call is the point — we would rather leave
# a genuine visitor `unclassified` for another month than name a local business as
# a visitor for a day.
_MIN_ORIGIN_SAMPLE = 8
_ORIGIN_DOMINANCE = 0.6

# The strong tier: 12+ known origins, 80%+ from one other field. Even this cannot
# convict on its own (see the weights) — it must be corroborated by observed
# same-day departures. Two independent positive observations, or no verdict.
_STRONG_ORIGIN_SAMPLE = 12
_STRONG_ORIGIN_DOMINANCE = 0.8

# An aircraft we have watched sleep here this many times is based here, full stop.
# Where its arrivals happen to come from then tells us about its TRIPS, not about
# its home — a KLMO-based aircraft that shuttles to Boulder daily has every single
# arrival originating at KBDU. Above this count, origin may no longer argue
# non-local.
_OVERNIGHT_HOME_VETO = 3

# ---------------------------------------------------------------------------
# WEIGHTS. Positive = evidence of locality. Negative = evidence of non-locality.
#
# Note what is NOT here: there is no weight for "we saw no overnight" (ungated),
# no weight for "we have no origin data", and no weight for "the registrant lives
# elsewhere". Those are absences. They score 0.0.
#
# Max reachable negative:  -0.30 + -0.45 = -0.75. A non-local badge can never
# carry more than 0.75, and cannot be reached by any single signal.
# Max reachable positive:  +0.60 + 0.60 + 0.30 + 0.15 = +1.65, capped to 0.95.
# ---------------------------------------------------------------------------

_W_OVERNIGHT_STRONG = 0.6     # >= 3 observed overnight stays here
_W_OVERNIGHT_SOME = 0.3       # 1-2 observed overnight stays here
_W_OVERNIGHT_NONE = -0.3      # 0 overnights across >= _MIN_CLOSED_VISITS visits
                              # whose departure we ACTUALLY WATCHED. Gated. This
                              # is not "we saw nothing" — it is "we watched it
                              # leave the same day, five or more times".

_W_RESIDENT_STRONG = 0.6      # on the field, unsuperseded, >= 14 days
_W_RESIDENT_SOME = 0.3        # on the field, unsuperseded, >= 8 hours

_W_ORIGIN_HOME = 0.3          # its arrivals mostly originate at THIS airport
_W_ORIGIN_FOREIGN = -0.3      # >= 8 known origins, >= 60% from one other field
_W_ORIGIN_FOREIGN_STRONG = -0.45   # >= 12 known origins, >= 80% from one other

_W_REGISTRANT_NUDGE = 0.15    # registry city matches the airport's own town


def _build_context(
    conn: sqlite3.Connection,
    icao: str,
    start_ts: int,
    now_ts: int,
    icao24: str | None = None,
) -> dict:
    """Every fact `classify` needs, bucketed by icao24, in a fixed number of queries.

    `recompute_airport` used to call `dwell.dwell_intervals` — which scans and
    pairs the WHOLE airport's operations — once per aircraft, then throw away
    every interval belonging to a different aircraft. That made it
    O(aircraft x operations): 12-15 s for one airport / 600 aircraft / 16,200 ops
    on in-memory SQLite. The same shape applied to the four per-aircraft lookups,
    none of which SQLite can serve from an index cheaply once `icao` alone already
    matches every row in the table.

    So gather everything ONCE per airport instead. Pass `icao24` to build the same
    structure for a single aircraft (the standalone `classify` path). Both paths
    run the SAME code below, so the optimisation cannot silently change a verdict
    — `test_recompute_airport_agrees_with_standalone_classify` pins that.
    """
    one = (icao24,) if icao24 is not None else ()
    ac_filter = " AND icao24=?" if icao24 is not None else ""
    arrival_ph = ",".join("?" * len(ARRIVAL_TYPES))

    dwell_by_ac: dict[str, list[dict]] = {}
    for interval in dwell.dwell_intervals(conn, icao, start_ts, now_ts):
        if icao24 is None or interval["icao24"] == icao24:
            dwell_by_ac.setdefault(interval["icao24"], []).append(interval)

    arrivals: dict[str, int] = {}
    self_closing: dict[str, int] = {}
    origins: dict[str, list[str]] = {}
    for row in conn.execute(
        f"SELECT icao24, type, origin_airport_icao AS origin FROM operations "
        f"WHERE icao=? AND timestamp BETWEEN ? AND ? AND icao24 IS NOT NULL"
        f"{ac_filter} AND type IN ({arrival_ph})",
        (icao, start_ts, now_ts, *one, *ARRIVAL_TYPES),
    ).fetchall():
        ac = row["icao24"]
        arrivals[ac] = arrivals.get(ac, 0) + 1
        if row["type"] in _SELF_CLOSING_ARRIVAL_TYPES:
            self_closing[ac] = self_closing.get(ac, 0) + 1
        if row["origin"]:
            origins.setdefault(ac, []).append(row["origin"])

    # The LATEST operation of any kind, per aircraft — a takeoff, touch-and-go,
    # low approach or circle after a landing all mean it was airborne again.
    last_op: dict[str, sqlite3.Row] = {}
    for row in conn.execute(
        f"SELECT icao24, type, timestamp AS ts FROM operations "
        f"WHERE icao=? AND timestamp BETWEEN ? AND ? AND icao24 IS NOT NULL{ac_filter} "
        f"ORDER BY icao24 ASC, timestamp DESC, id DESC",
        (icao, start_ts, now_ts, *one),
    ).fetchall():
        last_op.setdefault(row["icao24"], row)

    # PRIVACY BOUNDARY (unchanged): this is the only read of `aircraft_registry`,
    # and it selects exactly registrant_city and registrant_state. The table also
    # carries registrant_name / registrant_street / registrant_zip (db.py:150-155);
    # they are never bound into Python, so no evidence string can reference a value
    # that was never fetched. `icao_hex` is the aircraft identity we already have.
    if icao24 is not None:
        reg_sql = (
            "SELECT icao_hex, registrant_city, registrant_state FROM aircraft_registry "
            "WHERE icao_hex=? ORDER BY rowid"
        )
        reg_args: tuple = (icao24.upper(),)
    else:
        reg_sql = (
            "SELECT icao_hex, registrant_city, registrant_state FROM aircraft_registry "
            "WHERE icao_hex IN ("
            "  SELECT DISTINCT upper(icao24) FROM operations "
            "  WHERE icao=? AND timestamp BETWEEN ? AND ? AND icao24 IS NOT NULL"
            ") ORDER BY rowid"
        )
        reg_args = (icao, start_ts, now_ts)
    registry: dict[str, sqlite3.Row] = {}
    for row in conn.execute(reg_sql, reg_args).fetchall():
        registry.setdefault(row["icao_hex"], row)

    airport = conn.execute(
        "SELECT city FROM airports WHERE icao=?", (icao,)
    ).fetchone()

    return {
        "dwell": dwell_by_ac,
        "arrivals": arrivals,
        "self_closing": self_closing,
        "origins": origins,
        "last_op": last_op,
        "registry": registry,
        "airport_city": airport["city"] if airport else None,
    }


def classify(
    conn: sqlite3.Connection,
    icao: str,
    icao24: str,
    now_ts: int,
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
    *,
    context: dict | None = None,
) -> dict:
    """Classify one aircraft's locality at one airport, with its receipt.

    `context` is a pure optimisation for `recompute_airport` (see `_build_context`).
    Omitting it gathers the same facts for this call alone; the result is identical
    either way.
    """
    icao = icao.upper()
    now_ts = int(now_ts)
    start_ts = now_ts - lookback_days * 86400
    evidence: list[dict] = []
    score = 0.0

    # `based_icao` is set ONLY by a positive, named observation of non-locality:
    # arrivals we watched come in from a specific other airport. It is therefore
    # also the flag that gates the NON_LOCAL verdict — no named origin, no verdict.
    based_icao: str | None = None
    non_local_vetoes: list[str] = []

    if context is None:
        context = _build_context(conn, icao, start_ts, now_ts, icao24=icao24)
    intervals = context["dwell"].get(icao24, [])

    # ---- Observation coverage: what did we actually WATCH? -------------------
    # Every denominator published below is one of these. Each is a floor.
    arrivals = context["arrivals"].get(icao24, 0)
    self_closing = context["self_closing"].get(icao24, 0)

    # A visit we watched BEGIN and END: a paired landing->takeoff, or a
    # touch-and-go / low approach that ended itself.
    closed_visits = len(intervals) + self_closing
    overnights = sum(1 for i in intervals if i["seconds"] >= OVERNIGHT_SECONDS)

    # ---- Signal 1: overnight presence (the strongest evidence of a home) -----
    if overnights >= _OVERNIGHT_HOME_VETO:
        score += _W_OVERNIGHT_STRONG
        evidence.append({
            "code": "overnight_stays",
            "text": (
                f"{overnights} overnight stays observed at {icao} in the last "
                f"{lookback_days} days (landing to takeoff of 8h or more), from "
                f"{closed_visits} visits where we observed both the arrival and "
                f"the departure"
            ),
        })
    elif overnights >= 1:
        score += _W_OVERNIGHT_SOME
        evidence.append({
            "code": "overnight_stays",
            "text": (
                f"{overnights} overnight stays observed at {icao} in the last "
                f"{lookback_days} days (landing to takeoff of 8h or more), from "
                f"{closed_visits} visits where we observed both the arrival and "
                f"the departure"
            ),
        })
    elif closed_visits >= _MIN_CLOSED_VISITS:
        # NOT an absence. We watched this aircraft arrive and leave again at least
        # _MIN_CLOSED_VISITS times, and every one of those visits ended the same
        # day. That is a positive observation, and it is the only thing that earns
        # the negative weight.
        score += _W_OVERNIGHT_NONE
        evidence.append({
            "code": "overnight_stays",
            "text": (
                f"0 overnight stays observed at {icao} in the last {lookback_days} "
                f"days, across {closed_visits} visits where we observed both the "
                f"arrival and the departure (of {arrivals} observed arrivals)"
            ),
        })
    else:
        # We never watched it leave often enough for "it doesn't sleep here" to
        # mean anything. Scores 0.0. This is the P3 receipt: 59 landings, every
        # departure missed, and the evidence says exactly that.
        evidence.append({
            "code": "overnight_stays",
            "text": (
                f"No overnight stay observed at {icao} in the last {lookback_days} "
                f"days — but of {arrivals} observed arrivals we watched this "
                f"aircraft leave again only {closed_visits} times, too thin a "
                f"sample to weigh"
            ),
        })

    # ---- Signal 2: is it on the field RIGHT NOW? ----------------------------
    # An unpaired trailing landing produces ZERO dwell intervals, so under the old
    # model the aircraft physically parked on the ramp scored the model's single
    # strongest negative. It is now clamped non-negative, and it VETOES non-local:
    # we will not publish "visitor from KBDU" about an aircraft that is, as far as
    # we can see, sitting on our own ramp.
    last = context["last_op"].get(icao24)
    # Any operation after a landing — takeoff, touch-and-go, low approach, circle —
    # means the aircraft was airborne again, so "still on the field" requires the
    # LATEST operation to be the full-stop landing itself.
    if last and last["type"] == "landing":
        on_field_seconds = now_ts - last["ts"]
        if on_field_seconds >= _RESIDENT_STRONG_SECONDS:
            score += _W_RESIDENT_STRONG
            non_local_vetoes.append("on_field")
            evidence.append({
                "code": "on_field_now",
                "text": (
                    f"Last seen landing at {icao} {on_field_seconds // 86400} days "
                    f"ago, with no departure observed since — as far as we can "
                    f"observe, the aircraft is still on the field"
                ),
            })
        elif on_field_seconds >= OVERNIGHT_SECONDS:
            score += _W_RESIDENT_SOME
            non_local_vetoes.append("on_field")
            evidence.append({
                "code": "on_field_now",
                "text": (
                    f"Last seen landing at {icao} {on_field_seconds // 3600} hours "
                    f"ago, with no departure observed since — as far as we can "
                    f"observe, the aircraft is still on the field"
                ),
            })

    # ---- Signal 3: where its arrivals come from -----------------------------
    # The ONLY signal that may argue non-local, because it is the only POSITIVE
    # observation of non-locality available to us: we watched it fly in from a
    # named other airport, repeatedly.
    #
    # `origin_airport_icao` is forward-fill only and is written on arrivals alone
    # (services.ORIGIN_ELIGIBLE_OP_TYPES). A NULL origin is NO INFORMATION and is
    # excluded from the numerator AND named in the denominator, so the receipt
    # never implies we knew more than we did.
    origins = context["origins"].get(icao24, [])
    known = len(origins)
    if known >= _MIN_ORIGIN_SAMPLE:
        top, count = Counter(origins).most_common(1)[0]
        top = top.upper()
        share = count / known
        detail = (
            f"Origin known for {known} of {arrivals} observed arrivals; "
            f"{count} of those {known} came from {top}"
        )
        if top == icao and share >= _ORIGIN_DOMINANCE:
            score += _W_ORIGIN_HOME
            evidence.append({"code": "arrival_origin", "text": detail})
        elif top != icao and share >= _ORIGIN_DOMINANCE:
            if overnights >= _OVERNIGHT_HOME_VETO:
                # It demonstrably sleeps here. Where it flies in FROM describes its
                # trips, not its home. Score nothing; still show the observation.
                evidence.append({
                    "code": "arrival_origin",
                    "text": (
                        f"{detail} — not weighed against it: we have observed it "
                        f"stay overnight at {icao} {overnights} times"
                    ),
                })
            else:
                strong = (
                    known >= _STRONG_ORIGIN_SAMPLE
                    and share >= _STRONG_ORIGIN_DOMINANCE
                )
                score += _W_ORIGIN_FOREIGN_STRONG if strong else _W_ORIGIN_FOREIGN
                based_icao = top
                evidence.append({"code": "arrival_origin", "text": detail})
        else:
            evidence.append({
                "code": "arrival_origin",
                "text": (
                    f"Origin known for {known} of {arrivals} observed arrivals, "
                    f"with no single airport accounting for most of them — nothing "
                    f"to weigh"
                ),
            })
    elif known == 0:
        evidence.append({
            "code": "arrival_origin",
            "text": (
                f"No arrival origin is known for any of the {arrivals} arrivals we "
                f"observed — no information either way"
            ),
        })
    else:
        evidence.append({
            "code": "arrival_origin",
            "text": (
                f"Origin known for {known} of {arrivals} observed arrivals — fewer "
                f"than the {_MIN_ORIGIN_SAMPLE} we require before weighing it"
            ),
        })

    # ---- Signal 4: registrant address (a whisper, never a decision) ---------
    # The FAA registry holds a MAILING address, not a based airport. Only city
    # and state are read here — never registrant_name, registrant_street, or
    # registrant_zip — so no owner name or street address can ever reach this
    # evidence string, private individual or not.
    #
    # It can only ever move the score TOWARD local. A registrant in some other
    # town scores 0.0, never a penalty: plenty of people keep an aeroplane at the
    # field down the road from where they get their post.
    reg = context["registry"].get(icao24.upper())
    airport_city = context["airport_city"]
    if reg and reg["registrant_city"] and airport_city:
        city = reg["registrant_city"].strip()
        state = (reg["registrant_state"] or "").strip()
        # airports.city is "Longmont, CO"; compare on the town only.
        town = airport_city.split(",")[0].strip().lower()
        if city.lower() == town:
            score += _W_REGISTRANT_NUDGE
            # And it VETOES non-local. A "non-local" badge printed directly above
            # "Registrant address Longmont, CO" — the airport's own town — is a
            # receipt that contradicts its own verdict in the reader's eye, and the
            # reader is right.
            non_local_vetoes.append("registrant_local")
        evidence.append({
            "code": "registrant_address",
            "text": f"Registrant address {city}, {state}".strip().rstrip(","),
        })

    # ---- Verdict ------------------------------------------------------------
    signal_strength = min(MAX_SIGNAL_STRENGTH, abs(score))

    if score >= CONFIDENCE_THRESHOLD:
        locality = LOCAL
    elif score <= -CONFIDENCE_THRESHOLD:
        locality = NON_LOCAL
    else:
        locality = UNCLASSIFIED

    if locality == NON_LOCAL:
        # THE POSITIVE-EVIDENCE GATE. A non-local verdict may never rest on things
        # we failed to see. It requires a named airport we watched it arrive from.
        # Belt and braces: with no origin data the arithmetic alone cannot reach
        # -0.5 (the only other negative is -0.3), so this gate is a second,
        # independent guarantee rather than the only one.
        if based_icao is None:
            locality = UNCLASSIFIED
            evidence.append({
                "code": "verdict_withheld",
                "text": (
                    "Not published as non-local: we have not observed this aircraft "
                    "arrive from any particular other airport, and we will not call "
                    "an aircraft a visitor on the strength of what we did not see"
                ),
            })
        elif "on_field" in non_local_vetoes:
            locality = UNCLASSIFIED
            evidence.append({
                "code": "verdict_withheld",
                "text": (
                    f"Not published as non-local: as far as we can observe the "
                    f"aircraft is still on the field at {icao}"
                ),
            })
        elif "registrant_local" in non_local_vetoes:
            locality = UNCLASSIFIED
            evidence.append({
                "code": "verdict_withheld",
                "text": (
                    f"Not published as non-local: the aircraft's registered address "
                    f"is in the same town as {icao}"
                ),
            })

    return {
        "icao24": icao24,
        "locality": locality,
        "signal_strength": round(signal_strength, 4),
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
    now_ts = int(now_ts)
    start_ts = now_ts - lookback_days * 86400
    aircraft = [
        row["icao24"]
        for row in conn.execute(
            "SELECT DISTINCT icao24 FROM operations "
            "WHERE icao=? AND timestamp>=? AND icao24 IS NOT NULL",
            (icao, start_ts),
        ).fetchall()
    ]
    # Gather the airport's facts ONCE, not once per aircraft.
    context = _build_context(conn, icao, start_ts, now_ts)
    for icao24 in aircraft:
        result = classify(conn, icao, icao24, now_ts, lookback_days, context=context)
        conn.execute(
            "INSERT INTO aircraft_home_base "
            "  (icao, icao24, locality, signal_strength, based_icao, evidence_json, computed_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(icao, icao24) DO UPDATE SET "
            "  locality=excluded.locality, signal_strength=excluded.signal_strength, "
            "  based_icao=excluded.based_icao, evidence_json=excluded.evidence_json, "
            "  computed_at=excluded.computed_at",
            (
                icao, icao24, result["locality"], result["signal_strength"],
                result["based_icao"], json.dumps(result["evidence"]), now_ts,
            ),
        )
    return len(aircraft)


def locality_map(conn: sqlite3.Connection, icao: str) -> dict[str, dict]:
    """icao24 -> {locality, signal_strength, based_icao, evidence} for one airport."""
    rows = conn.execute(
        "SELECT icao24, locality, signal_strength, based_icao, evidence_json "
        "FROM aircraft_home_base WHERE icao=?",
        (icao.upper(),),
    ).fetchall()
    return {
        row["icao24"]: {
            "locality": row["locality"],
            "signal_strength": row["signal_strength"],
            "based_icao": row["based_icao"],
            "evidence": json.loads(row["evidence_json"]),
        }
        for row in rows
    }
