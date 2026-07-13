"""Is this aircraft based at the airport, or does it just use it?

There is no such classification anywhere else in the codebase; this is net-new.

DESIGN: this module emits a RECEIPT, not a verdict. Every classification carries
structured evidence that the UI renders inline next to it:

    Non-local — 0 overnight stays observed at KLMO in the last 180 days, across
                18 separate days on which we watched a visit here both begin and
                end (18 touch-and-go/low-approach circuits ended themselves and
                are not departures we watched), of 47 observed arrivals
              - Origin known on 11 separate days (14 of 47 observed arrivals);
                10 of those 11 days came from KBDU (13 of those 14 arrivals)
              - Registrant address Boulder, CO — the FAA registry records a
                mailing address, not a based airport, so this is not weighed
                against it

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

from . import db, dwell

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
# close themselves. They are NOT, however, departures we watched, and a circuit
# can never produce an overnight — so they are reported as their own population
# and they can never, on their own, license the "no overnights" penalty (see
# `_MAX_UNPAIRED_LANDINGS`).
_SELF_CLOSING_ARRIVAL_TYPES = ("touch_and_go", "low_approach")

# ---------------------------------------------------------------------------
# GATES. Each one exists to stop a weight firing on something we did not observe.
#
# EVERY GATE COUNTS DISTINCT LOCAL DAYS, NOT ROWS. The origin writer memoizes per
# aircraft per detector pass and resolves the origin from the earliest ground
# sample of the track (services.py:1659, 1725), so every arrival in ONE session
# carries the SAME origin from ONE resolution. Counting rows, a single 40-minute
# pattern session (12 circuits) reads as 12 closed visits and 12 known origins —
# it inflates both gates 10-40x and published `non_local` off one afternoon. A day
# is the coarsest bucket we can defend as "another look at the same question", and
# it is the bucket the rest of the ledger already speaks in. Local days, via
# db.local_day_key + db.airport_timezone — never UTC: a 19:00 MDT circuit is
# tomorrow in UTC, and bucketing it as a second day would re-introduce the very
# double-count this gate exists to prevent.
# ---------------------------------------------------------------------------

# "It never sleeps here" is only a claim we will publish once we have actually
# WATCHED it arrive and leave again, on this many SEPARATE DAYS. A closed visit is
# one we watched both begin and end: a paired landing->takeoff interval, or a
# self-closing circuit.
#
# 5 is a judgment call, and it is worth saying so rather than dressing it up:
# there is no principled derivation, because we have no model of the detector's
# miss rate. It is set where five days each ending in a same-day departure stop
# looking like a run of luck. Raise it if the takeoff detector proves lossier than
# `dwell_summary.coverage` currently suggests.
_MIN_CLOSED_VISIT_DAYS = 5

# THE GATE THAT ROUND 1 GOT WRONG, AND WHY IT MATTERS MOST.
#
# Round 1 gated the "no overnights observed" penalty on the COUNT OF CLOSED
# VISITS. That quantity is destroyed by the very data loss it was meant to gate
# against. `dwell.dwell_intervals` pairs a takeoff with the MOST RECENT landing
# and DROPS the earlier one (dwell.py:76-79) — so for an aircraft that sleeps
# here and whose dawn departure we miss:
#
#     lands Mon 19:00 (sleeps here) · dawn takeoff MISSED · lands Tue 10:00 ·
#     departs Tue 13:00 (seen)   =>   dwell_intervals() -> [3.0h]
#
# The 19h OVERNIGHT is dropped; the 3h turnaround is KEPT. The aircraft whose
# overnights we CANNOT see is therefore the aircraft that accrues "closed visits"
# FASTEST. A based day-tripper flying two legs a day for two months reached
# `non_local @ 0.75` on a receipt claiming we had watched it leave 59 times — we
# had never watched it leave once.
#
# So the gate must ALSO look at what it did NOT close: a full-stop landing with no
# departure we ever saw. Each one is a night the aircraft may well have spent on
# our ramp. ONE is the expected steady state — the aircraft is sitting out there
# right now — and must not suppress the signal. TWO OR MORE is a hole in the data,
# and "0 overnights" through a hole in the data is an absence, not an observation.
_MAX_UNPAIRED_LANDINGS = 1

# ROUND 3 / CRITICAL — `unpaired_landings` catches a missed DEPARTURE (the
# landing row exists; nothing ever paired it). It is completely blind to a
# missed ARRIVAL: if the detector never emits the `landing` at all, the op is in
# NEITHER `landings` NOR `intervals`, so `unpaired_landings` reads 0 —
# arithmetically identical to a perfect observation record. And `landing`
# (detectors.py:735-768) is structurally the most fragile op this module counts:
# it requires a >=500 ft AGL sample in the 300s before touchdown
# (detectors.py:664-672, 764) on top of the <=200 ft AGL sample every arrival
# needs, while `touch_and_go` (detectors.py:488-514) needs only runway-overlap
# geometry "at any altitude". Marginal low-altitude ADS-B coverage — named as an
# explicit risk at detectors.py:801-805 — therefore drops an aircraft's
# `landing` rows while leaving its circuits untouched.
#
# `detect_takeoffs_over_period` requires NO prior approach from altitude
# (detectors.py:778-800): a takeoff is only ever emitted for an aircraft that
# ORIGINATED at the field. So a takeoff we cannot pair with a landing we watched
# IS an arrival we missed — the exact mirror of an unpaired landing — and an
# overnight hides behind a missed arrival exactly as it hides behind a missed
# departure. Same gate, same threshold, same reasoning.
_MAX_UNPAIRED_TAKEOFFS = 1

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
# It was 3 known origins. Three is a single weekend: two flights out of three swung
# a -0.4 penalty and, with one more weak signal, published a named business as a
# visitor. It is now 8 DAYS on which we resolved an origin — roughly two months of
# a weekly flyer — so that the SMALLEST sample able to contribute at all reads as a
# habit rather than a phase.
#
# DAYS, not rows, and this is the correction that makes the comment true: because
# the writer memoizes per pass, 12 rows can be ONE resolution. "Eight independent
# looks" was simply not what `known >= 8` counted. It is what `known_days >= 8`
# counts. This remains a judgment call, not a derived threshold: we have no prior
# over GA origin mixes to compute a p-value against, and inventing one would be
# false precision. The direction of the call is the point — we would rather leave
# a genuine visitor `unclassified` for another month than name a local business as
# a visitor for a day.
_MIN_ORIGIN_DAYS = 8
_ORIGIN_DOMINANCE = 0.6

# The strong tier: origins resolved on 12+ separate days, 80%+ of those days from
# one other field. Even this cannot convict on its own (see the weights) — it must
# be corroborated by observed same-day departures. Two independent positive
# observations, or no verdict.
_STRONG_ORIGIN_DAYS = 12
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
_W_OVERNIGHT_NONE = -0.3      # 0 overnights, on >= _MIN_CLOSED_VISIT_DAYS separate
                              # days whose visits we watched END, and with no more
                              # than _MAX_UNPAIRED_LANDINGS full-stop landings we
                              # never watched leave. Doubly gated. This is not "we
                              # saw nothing" — it is "we watched it arrive and go
                              # again the same day, on five or more separate days,
                              # and there is no pile of landings we lost track of".

_W_RESIDENT_STRONG = 0.6      # on the field, unsuperseded, >= 14 days
_W_RESIDENT_SOME = 0.3        # on the field, unsuperseded, >= 8 hours

_W_ORIGIN_HOME = 0.3          # its arrivals mostly originate at THIS airport
_W_ORIGIN_FOREIGN = -0.3      # >= 8 known origins, >= 60% from one other field
_W_ORIGIN_FOREIGN_STRONG = -0.45   # >= 12 known origins, >= 80% from one other

_W_REGISTRANT_NUDGE = 0.15    # registry city matches the airport's own town

# A verdict that a VETO suppressed must not be resurrectable by a consumer that
# thresholds on the number instead of reading the word. The score that produced the
# suppressed verdict is unchanged by the veto, so a vetoed non-local published
# `unclassified` alongside `signal_strength=0.75` — and anything filtering on
# `signal_strength >= CONFIDENCE_THRESHOLD` would hand the reader back exactly the
# accusation we just withheld. The strength we publish is the strength of the claim
# we publish, and the claim is "we do not know". So it is clamped strictly below the
# threshold, and every UNCLASSIFIED row now satisfies
# `signal_strength < CONFIDENCE_THRESHOLD` by construction.
_MAX_UNCLASSIFIED_SIGNAL_STRENGTH = 0.45


def _closure_phrase(circuits: int, paired: int, landings: int) -> str:
    """The two observation populations, reported SEPARATELY, never merged.

    A circuit ends itself: we watched the visit begin and end, but we did NOT watch
    a departure, and a touch-and-go can never produce an overnight. A full-stop
    landing is only closed when we actually SAW the takeoff. The old receipt merged
    the two into "N visits where we observed both the arrival and the departure" —
    which, for an aircraft whose every full-stop departure we missed, asserted an
    observation we did not have. That sentence is the load-bearing one under the
    badge, and the one a correction would quote back at us.
    """
    parts: list[str] = []
    if landings:
        parts.append(
            f"we watched it depart after {paired} of the {landings} full-stop "
            f"landing{'' if landings == 1 else 's'} we observed"
        )
    else:
        # IMPORTANT 2 (round 3): `landings == 0` is itself load-bearing — it is
        # exactly the shape in which a missed ARRIVAL hides an overnight (see
        # `_MAX_UNPAIRED_TAKEOFFS`). Under this module's own standard, the
        # denominator is what we actually observed, and a reader who never sees
        # this zero cannot tell "it never sleeps here" from "we have never once
        # watched this aircraft touch the ground here". Never let it go unsaid.
        parts.append("we observed 0 full-stop landings")
    if circuits:
        parts.append(
            f"{circuits} touch-and-go/low-approach circuit"
            f"{'' if circuits == 1 else 's'} ended themselves and are not "
            f"departures we watched"
        )
    return "; ".join(parts) if parts else "we watched no arrival here end"


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

    # The airport's OWN timezone. Every gate below buckets by local day, and a UTC
    # day would split a Colorado evening across two buckets — inflating exactly the
    # counts these gates exist to hold down.
    tz = db.airport_timezone(conn, icao)
    day_of: dict[int, str] = {}

    def local_day(ts: int) -> str:
        # Memoised: recompute_airport walks ~16k operations, and the ZoneInfo
        # conversion is the only non-trivial work in this loop.
        key = int(ts)
        cached = day_of.get(key)
        if cached is None:
            cached = day_of[key] = db.local_day_key(key, tz)
        return cached

    dwell_by_ac: dict[str, list[dict]] = {}
    for interval in dwell.dwell_intervals(conn, icao, start_ts, now_ts):
        if icao24 is None or interval["icao24"] == icao24:
            dwell_by_ac.setdefault(interval["icao24"], []).append(interval)

    arrivals: dict[str, int] = {}
    self_closing: dict[str, int] = {}
    circuit_days: dict[str, set[str]] = {}
    origins: dict[str, list[str]] = {}
    origin_days: dict[str, set[tuple[str, str]]] = {}
    for row in conn.execute(
        f"SELECT icao24, type, timestamp AS ts, origin_airport_icao AS origin "
        f"FROM operations "
        f"WHERE icao=? AND timestamp BETWEEN ? AND ? AND icao24 IS NOT NULL"
        f"{ac_filter} AND type IN ({arrival_ph})",
        (icao, start_ts, now_ts, *one, *ARRIVAL_TYPES),
    ).fetchall():
        ac = row["icao24"]
        arrivals[ac] = arrivals.get(ac, 0) + 1
        if row["type"] in _SELF_CLOSING_ARRIVAL_TYPES:
            self_closing[ac] = self_closing.get(ac, 0) + 1
            circuit_days.setdefault(ac, set()).add(local_day(row["ts"]))
        if row["origin"]:
            # NORMALISE BEFORE COUNTING. The dominance Counter used to run on raw
            # strings and .upper() only the winner, so `KBDU` and `kbdu` split the
            # count and could suppress the signal entirely (or, worse, hand the
            # plurality to a different field).
            origin = row["origin"].strip().upper()
            if origin:
                origins.setdefault(ac, []).append(origin)
                # One (day, origin) pair per day, however many rows that day
                # produced: one detector pass writes the same memoised origin to
                # every arrival in the session, so the rows are one observation.
                origin_days.setdefault(ac, set()).add((local_day(row["ts"]), origin))

    # The LATEST operation of any kind, per aircraft — a takeoff, touch-and-go,
    # low approach or circle after a landing all mean it was airborne again.
    #
    # The SAME scan also counts `takeoff` rows per aircraft: a takeoff is only
    # ever emitted when the aircraft originated at the field (no prior approach
    # from altitude — detectors.py:778-800), so it is a positive, named
    # observation of ground presence here. Counting it here reuses this query's
    # existing full-airport scan rather than adding another one.
    last_op: dict[str, sqlite3.Row] = {}
    takeoffs: dict[str, int] = {}
    for row in conn.execute(
        f"SELECT icao24, type, timestamp AS ts FROM operations "
        f"WHERE icao=? AND timestamp BETWEEN ? AND ? AND icao24 IS NOT NULL{ac_filter} "
        f"ORDER BY icao24 ASC, timestamp DESC, id DESC",
        (icao, start_ts, now_ts, *one),
    ).fetchall():
        ac = row["icao24"]
        last_op.setdefault(ac, row)
        if row["type"] == "takeoff":
            takeoffs[ac] = takeoffs.get(ac, 0) + 1

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
        "circuit_days": circuit_days,
        "origins": origins,
        "origin_days": origin_days,
        "last_op": last_op,
        "takeoffs": takeoffs,
        "registry": registry,
        "airport_city": airport["city"] if airport else None,
        "tz": tz,
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
    # Every denominator published below is one of these. Each is a floor, and each
    # is reported as its own population — merging them is what let round 1 print
    # "we observed both the arrival and the departure" about an aircraft whose
    # departures we never once observed.
    arrivals = context["arrivals"].get(icao24, 0)
    self_closing = context["self_closing"].get(icao24, 0)

    # Full-stop landings, and how many of them we actually watched leave again.
    landings = arrivals - self_closing
    paired_landings = len(intervals)
    # THE QUANTITY ROUND 1 WAS MISSING: full-stop landings with no departure we
    # ever saw. Every one of these is a night the aircraft may have spent on our
    # ramp — invisible to `dwell_intervals`, which drops the unpaired landing.
    unpaired_landings = landings - paired_landings

    # THE QUANTITY ROUND 2 WAS MISSING: a takeoff never paired with a landing we
    # watched. Every `takeoff` op is a positive observation of ground presence at
    # THIS airport (no prior approach from altitude), so one we cannot match to a
    # landing we watched is an arrival we missed — and an overnight hides behind
    # a missed arrival exactly as it hides behind a missed departure.
    takeoffs = context["takeoffs"].get(icao24, 0)
    unpaired_takeoffs = takeoffs - paired_landings

    # A visit we watched BEGIN and END, counted in DISTINCT LOCAL DAYS: a paired
    # landing->takeoff, or a circuit that ended itself.
    tz = context["tz"]
    closed_days = set(context["circuit_days"].get(icao24, ()))
    closed_days.update(db.local_day_key(i["landing_ts"], tz) for i in intervals)
    closed_visit_days = len(closed_days)
    overnights = sum(1 for i in intervals if i["seconds"] >= OVERNIGHT_SECONDS)
    closure = _closure_phrase(self_closing, paired_landings, landings)

    # ---- Signal 1: overnight presence (the strongest evidence of a home) -----
    if overnights >= _OVERNIGHT_HOME_VETO:
        score += _W_OVERNIGHT_STRONG
        evidence.append({
            "code": "overnight_stays",
            "text": (
                f"{overnights} overnight stays observed at {icao} in the last "
                f"{lookback_days} days (landing to takeoff of 8h or more) — "
                f"{closure}"
            ),
        })
    elif overnights >= 1:
        score += _W_OVERNIGHT_SOME
        evidence.append({
            "code": "overnight_stays",
            "text": (
                f"{overnights} overnight stay{'' if overnights == 1 else 's'} "
                f"observed at {icao} in the last {lookback_days} days (landing to "
                f"takeoff of 8h or more) — {closure}"
            ),
        })
    elif closed_visit_days >= _MIN_CLOSED_VISIT_DAYS and (
        unpaired_landings <= _MAX_UNPAIRED_LANDINGS
    ) and (
        unpaired_takeoffs <= _MAX_UNPAIRED_TAKEOFFS
    ):
        # NOT an absence, on ALL THREE counts. We watched this aircraft arrive and
        # leave again on at least _MIN_CLOSED_VISIT_DAYS separate days, every one of
        # those visits ended the same day, there is no pile of full-stop landings we
        # lost track of behind which an overnight could be hiding (a missed
        # DEPARTURE), AND there is no pile of takeoffs we could not match to a
        # landing we watched (a missed ARRIVAL — the exact mirror). That triple of
        # observations is the only thing that earns the negative weight.
        score += _W_OVERNIGHT_NONE
        evidence.append({
            "code": "overnight_stays",
            "text": (
                f"0 overnight stays observed at {icao} in the last {lookback_days} "
                f"days, across {closed_visit_days} separate days on which we watched "
                f"a visit here both begin and end ({closure}), of {arrivals} observed "
                f"arrivals"
            ),
        })
    else:
        # We did not watch it leave often enough, or cleanly enough, for "it doesn't
        # sleep here" to mean anything. Scores 0.0, and the receipt names WHICH of
        # the three gates stopped it — because "we could not tell" is a different
        # sentence from "we did not look".
        if unpaired_landings > _MAX_UNPAIRED_LANDINGS:
            why = (
                f"{unpaired_landings} of those landings were never followed by a "
                f"departure we saw, and an aircraft that slept here is exactly what "
                f"that looks like"
            )
        elif unpaired_takeoffs > _MAX_UNPAIRED_TAKEOFFS:
            # ROUND 3 / CRITICAL: the mirror case. These takeoffs are positive,
            # named observations that the aircraft was on the ground at `icao` —
            # but we never watched the landing that put it there, so an overnight
            # could be hiding behind every one of them.
            why = (
                f"{unpaired_takeoffs} takeoff{'' if unpaired_takeoffs == 1 else 's'} "
                f"from {icao} we could not match to an arrival we watched — and "
                f"behind an arrival we missed, an overnight can hide"
            )
        else:
            why = (
                f"we watched a visit here both begin and end on only "
                f"{closed_visit_days} separate day"
                f"{'' if closed_visit_days == 1 else 's'}, too thin a sample to weigh"
            )
        evidence.append({
            "code": "overnight_stays",
            "text": (
                f"No overnight stay observed at {icao} in the last {lookback_days} "
                f"days — of {arrivals} observed arrivals, {closure}. Weighed as "
                f"nothing: {why}"
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
    #
    # Counted in DISTINCT LOCAL DAYS, not rows: the writer memoises per aircraft per
    # detector pass, so one session's twelve arrivals carry one origin from ONE
    # resolution. Twelve rows are not twelve looks.
    origins = context["origins"].get(icao24, [])      # already upper-cased
    day_pairs = context["origin_days"].get(icao24, set())
    known = len(origins)
    known_days = len({day for day, _ in day_pairs})
    if known_days >= _MIN_ORIGIN_DAYS:
        top, top_days = Counter(origin for _, origin in day_pairs).most_common(1)[0]
        count = sum(1 for origin in origins if origin == top)
        share = top_days / known_days
        # MINOR (round 3): the gate runs on DAYS, not rows -- lead with the
        # population the reader actually needs to track. Row counts are still
        # here, but parenthetical, so a lay reader is not asked to hold two
        # populations in one clause.
        detail = (
            f"Origin known on {known_days} separate days ({known} of {arrivals} "
            f"observed arrivals); {top_days} of those {known_days} days came from "
            f"{top} ({count} of those {known} arrivals)"
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
                    known_days >= _STRONG_ORIGIN_DAYS
                    and share >= _STRONG_ORIGIN_DOMINANCE
                )
                score += _W_ORIGIN_FOREIGN_STRONG if strong else _W_ORIGIN_FOREIGN
                based_icao = top
                evidence.append({"code": "arrival_origin", "text": detail})
        else:
            evidence.append({
                "code": "arrival_origin",
                "text": (
                    f"Origin known for {known} of {arrivals} observed arrivals, on "
                    f"{known_days} separate days, with no single airport accounting "
                    f"for most of those days — nothing to weigh"
                ),
            })
    elif known_days == 0:
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
                f"Origin known for {known} of {arrivals} observed arrivals, but on "
                f"only {known_days} separate day{'' if known_days == 1 else 's'} — "
                f"fewer than the {_MIN_ORIGIN_DAYS} days we require before weighing "
                f"it (one afternoon in the pattern is one observation, not twelve)"
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
        where = f"Registrant address {city}, {state}".strip().rstrip(",")
        # airports.city is "Longmont, CO"; compare on the town only.
        town = airport_city.split(",")[0].strip().lower()
        if city.lower() == town:
            score += _W_REGISTRANT_NUDGE
            # And it VETOES non-local. A "non-local" badge printed directly above
            # "Registrant address Longmont, CO" — the airport's own town — is a
            # receipt that contradicts its own verdict in the reader's eye, and the
            # reader is right.
            non_local_vetoes.append("registrant_local")
            text = (
                f"{where} — the same town as {icao}. The FAA registry records a "
                f"mailing address, not a based airport, so this is a nudge toward "
                f"local, never a decision"
            )
        else:
            # A registrant in another town scores 0.0 — it is NOT evidence of
            # non-locality (people keep aeroplanes at the field down the road from
            # where they get their post). But printed bare, directly beneath a
            # "non-local" badge, "Registrant address Boulder, CO" READS as
            # corroboration of a verdict it took no part in. Say so, in the same
            # idiom the origin branch already uses.
            text = (
                f"{where} — the FAA registry records a mailing address, not a based "
                f"airport, so this is not weighed against it"
            )
        evidence.append({"code": "registrant_address", "text": text})

    # ---- Verdict ------------------------------------------------------------
    signal_strength = min(MAX_SIGNAL_STRENGTH, abs(score))

    # THE PRECONDITIONS FOR AN ACCUSATION, EVALUATED BEFORE THE ARITHMETIC.
    #
    # These used to sit INSIDE `if locality == NON_LOCAL`, which made the `on_field`
    # veto arithmetically DEAD CODE: on-field adds at least +0.30, the deepest
    # negative reachable is -0.75, and -0.75 + 0.30 = -0.45 never crosses -0.50. The
    # protection an aircraft parked on our own ramp actually enjoyed was that
    # numeric coincidence — not the veto that claims to provide it. A future
    # reweighting could have taken the coincidence away while the veto sat there
    # looking like it still guarded the door.
    #
    # So the preconditions are now STRUCTURAL and are tested FIRST. `non_local` is
    # not a score that clears a bar; it is a claim that requires (a) a named airport
    # we watched it arrive from, and (b) no observation that contradicts it. The
    # arithmetic can only choose between the claims that are already permitted.
    non_local_permitted = based_icao is not None and not non_local_vetoes

    if score >= CONFIDENCE_THRESHOLD:
        locality = LOCAL
    elif score <= -CONFIDENCE_THRESHOLD and non_local_permitted:
        locality = NON_LOCAL
    elif score <= -CONFIDENCE_THRESHOLD:
        # The arithmetic reached non-local and a precondition refused it. Say which,
        # in the receipt: a veto that acts silently is indistinguishable from a bug.
        locality = UNCLASSIFIED
        if based_icao is None:
            # THE POSITIVE-EVIDENCE GATE. A non-local verdict may never rest on
            # things we failed to see. Belt and braces: with no origin data the
            # arithmetic alone cannot reach -0.5 either (the only other negative is
            # -0.3), so this is a second, independent guarantee, not the only one.
            evidence.append({
                "code": "verdict_withheld",
                "text": (
                    "Not published as non-local: we have not observed this aircraft "
                    "arrive from any particular other airport, and we will not call "
                    "an aircraft a visitor on the strength of what we did not see"
                ),
            })
        elif "on_field" in non_local_vetoes:
            evidence.append({
                "code": "verdict_withheld",
                "text": (
                    f"Not published as non-local: as far as we can observe the "
                    f"aircraft is still on the field at {icao}"
                ),
            })
        elif "registrant_local" in non_local_vetoes:
            evidence.append({
                "code": "verdict_withheld",
                "text": (
                    f"Not published as non-local: the aircraft's registered address "
                    f"is in the same town as {icao}"
                ),
            })
    else:
        locality = UNCLASSIFIED

    if locality == UNCLASSIFIED:
        # A veto suppresses the VERDICT but not the SCORE that produced it, so a
        # vetoed non-local published `unclassified` next to `signal_strength=0.75` —
        # and any consumer thresholding on the number instead of reading the word
        # would resurrect exactly the accusation the veto withheld. Clamp it below
        # CONFIDENCE_THRESHOLD: the strength we publish is the strength of the claim
        # we publish, and the claim is "we do not know".
        signal_strength = min(signal_strength, _MAX_UNCLASSIFIED_SIGNAL_STRENGTH)

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
    # BETWEEN start_ts AND now_ts — the SAME window `_build_context` gathers facts
    # for. Selecting on `timestamp >= start_ts` alone admitted aircraft whose only
    # operations are in the FUTURE (a clock-skewed sample, a bad backfill): the
    # context would then be empty for them and they would publish `unclassified @
    # 0.0` — a row asserting we looked and found nothing, about an aircraft we never
    # had a single in-window observation of.
    aircraft = [
        row["icao24"]
        for row in conn.execute(
            "SELECT DISTINCT icao24 FROM operations "
            "WHERE icao=? AND timestamp BETWEEN ? AND ? AND icao24 IS NOT NULL",
            (icao, start_ts, now_ts),
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
