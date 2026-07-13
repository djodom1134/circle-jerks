# The Lost Landing — Design Spec

**Date:** 2026-07-13
**Status:** Approved, pending implementation plan
**Branch:** `feat/lost-landing-ledger`

---

## 1. What this is

A second public site, built on the existing Circle Jerks data spine, that reframes
Vance Brand Airport (KLMO) activity around money and public cost.

Circle Jerks stays what it is: a tongue-in-cheek flight tracker. The Lost Landing is
the sober analysis that cites it. Two brands, one backend.

### The claim

> Longmont does not know how many aircraft use its runway. We counted. Every use is
> free — while the airport defers maintenance and out-of-town flight schools train
> here for nothing.

### The claim we are explicitly NOT making

> "The airport owes the city money."

That framing is illegal (see §3) and is the airport lobby's strongest counterattack.
Fee revenue stays on the airport, by federal law. The site says so, loudly, in its own
voice — which converts the constraint from a vulnerability into armor.

### Audience

Longmont residents and local press. Win condition: a Times-Call story and a packed
council comment period. This means the site optimizes for **one repeatable number** and
**screenshot-ability**, while remaining rigorous enough that a hostile aviation attorney
finds nothing to pull on.

---

## 2. The two flaws in the prototype that this spec fixes

The prototype (`prd/index.html`, `prd/airport-opportunity-concept.zip`) is a strong
visual and structural starting point. Two things in it are load-bearing errors.

### 2.1 Revenue diversion (fatal)

The project grid contains **"West Longmont Community Pavilion — funded by fair-use
revenue from a public asset."**

Vance Brand has accepted [over $6.2M in FAA Airport Improvement Program grants since
1988](https://en.wikipedia.org/wiki/Vance_Brand_Airport), making it a federally
obligated airport. [Grant Assurance 25](https://www.faa.gov/airports/aip/grant_assurances)
requires that all airport revenue — landing fees explicitly included — be spent on the
airport. Diverting it to a city park is
[revenue diversion](https://airportu.org/revenue-diversion) under 49 U.S.C. § 47107(b)
and § 47133. Penalties include grant clawback, civil penalties, and placement on the
FAA Airport Noncompliance List.

**Action:** delete the pavilion. The other five projects (taxiway preservation reserve,
electric aviation charging apron, community FBO, noise sensor network, unleaded fuel
transition) are all lawful airport capital costs and survive Grant Assurance 25 cleanly.

### 2.2 Operations double-counting (factual)

The FAA defines an *operation* as one takeoff **or** one landing. A touch-and-go is
**two** operations. The prototype's hero multiplies a fee by an operations count,
roughly doubling the billable base.

**Action:** the billable unit is defined in §5 and is derived from individual detected
events, never from an operations total.

---

## 3. Legal frame (this is site content, not just a constraint)

Two grant assurances govern everything the site may propose:

| Assurance | Requirement | Consequence for the site |
|---|---|---|
| **GA 25 — Airport Revenues** | Airport revenue must be used for airport operating and capital costs | Every funded project shown must be an on-airport cost. The site states this itself, in a section titled "What the law allows." |
| **GA 22 — Economic Nondiscrimination** | Fees must be reasonable and not unjustly discriminatory | The calculator applies **one fee to all aircraft**. The local/non-local split is journalism about *who benefits from the free ride* — never a proposal to charge non-locals more. |

**The GA 22 trap:** if the site reads as proposing a higher fee for out-of-town
aircraft, the airport gains a real and correct legal objection. Copy must make the
single-fee position explicit and unmissable.

---

## 4. Why "we counted" is the lede, not the fee

KLMO is **untowered**. No controller tallies movements. Published operations figures
(sources range from ~88,000 to ~126,000 per year) trace to an FAA Form 5010 airport
master record, which for a non-towered field is an **estimate submitted by the airport**,
not a count. The spread between published figures is itself evidence of this.

Circle Jerks has been detecting and persisting individual landings, takeoffs,
touch-and-gos, low approaches, and pattern circles as discrete timestamped rows — and
never pruning them.

This yields two strategic properties:

1. **The methodology is the story.** "The city does not know; we do" is difficult to
   refute, because refuting it requires producing a real count, which nobody has.
2. **Every number is a floor.** ADS-B does not capture non-equipped aircraft.
   Therefore our counts are lower bounds, not estimates. The site says "at least N"
   and can never be wrong in the direction that matters. Volunteering this ceiling is
   precisely what earns a hostile reader's trust in the rest.

---

## 5. The billable unit: "runway uses"

```
runway_uses = confirmed landings + touch-and-gos + low approaches
```

### Why not "touch-and-gos"

The existing touch-and-go detector (`backend/app/detectors.py:488`,
`touch_and_gos_from_circles`) is **geometric**: a pattern circle whose loop passed
within 0.25 nm of the runway segment. It does **not** verify a touchdown. This is a fine
approximation for a tracker. It is not survivable in a sentence that reads
"6,740 touch-and-gos × $10 = money owed" — that detector definition is the first thread
an aviation attorney pulls.

### Why "runway uses" works

Every constituent event is **true exactly as detected**. The claim becomes "this
aircraft used Longmont's runway N times," which is what the data actually supports,
whether or not rubber touched. There is no touchdown claim to refute. It is also the
larger, better number.

**Requirement:** one plain sentence on the page defines the unit, and the methodology
page publishes the exact detector logic for each constituent event type.

---

## 6. Architecture

Separate front end, shared data spine.

```
lostlanding.org  ──┐
                   ├──► Circle Jerks FastAPI ──► SQLite `operations`
circlejerks.live ──┘         + GET /airports/{icao}/ledger
```

- **New Vite/React/TS app** alongside the existing `frontend/`, deployed as a second
  static nginx container behind the existing Caddy, on its own domain.
- The prototype's Art Deco CSS ports over substantially as-is. The hand-rolled SVG bar
  chart is adequate; no new charting dependency is required.
- Rationale for separation: Circle Jerks' name and tone are an asset for press
  memorability and a liability for a money claim. A reporter screenshotting a revenue
  figure should not capture "circlejerks" in the URL. Shared backend guarantees the two
  sites' numbers can never drift apart — which would be the single most discrediting
  possible failure.

### Domain

**LostLanding.org.** The earlier lean toward LandingLedger.org optimized for
institutional legitimacy; the chosen audience is press and residents, and a reporter
writes a headline about a lost landing, not about a ledger.

---

## 7. Backend work

### 7.1 `daily_operation_rollup` table — required, not optional

`db.airport_operations_trends` (`backend/app/db.py:940`) currently pulls every matching
`operations` row into Python and buckets in a loop, with a correlated subquery against
the ~312k-row `aircraft_registry` per row. There is an existing performance comment at
`db.py:825-829` noting this already hangs the endpoint for busy airports.

A site engineered to attract press traffic will stress exactly this path.

**Schema:** `(icao, date_local, event_type, icao24, count)`, populated by the existing
worker, backfilled once over history. The ledger endpoint reads this table. It fixes a
known hotspot and serves the new feature — it pays for itself twice.

### 7.2 `homebase.py` — local vs non-local classifier

Does not exist today in any form. This is the largest single build.

**Signals:**
- **Overnight presence** — a `landing` with no subsequent `takeoff` until after dawn.
  Strongest single indicator of a based aircraft.
- **Arrival origin distribution** — `operations.origin_airport_icao` (patchy coverage;
  local-cache-only on the hot path, per `services.py:1247-1250`).
- **Operation recurrence** — frequency and span via `aircraft_observed_identity`
  (`first_seen_at`, `last_seen_at`, `observation_count`).
- **Registrant city/state** — weak tiebreaker only. The FAA registry records an owner's
  *mailing address*, not a based airport, and the codebase already ships an explicit
  disclaimer to this effect (`registry/profile.py:26`).

**Output — a receipt, not a verdict.** This is the core design decision of this
component. The classifier emits `locality`, `confidence`, and **structured evidence**
that the UI renders inline:

> **Non-local** — 0 overnight stays at KLMO in 180 days · 43 of 47 arrivals originated
> at KBDU · registrant address Boulder, CO

The hazard being designed out is *"you **guessed** I'm not local."* A press-facing site
that must run a correction about a named business loses more than the claim ever earned.
Evidence shown inline is reproducible and is not a guess.

**Below-threshold aircraft render as `unclassified`, visibly.** They are never silently
bucketed. The size of the unclassified bucket is displayed.

**Storage:** new `aircraft_home_base` table
`(icao24, based_icao, locality, confidence, evidence_json, computed_at)`, recomputed
nightly by the existing worker.

### 7.3 `dwell.py` — time on field

Dwell = next `takeoff.timestamp` − `landing.timestamp` per `icao24` at an airport. Both
event types are already detected and stored with timestamps. **Zero schema change.**

Coverage will be imperfect (aircraft that arrive or depart without a detected
counterpart event). Report coverage alongside the median, and exclude unpaired events
rather than imputing.

### 7.4 `GET /airports/{icao}/ledger`

One endpoint returning:
- **summary** — runway uses (total and by constituent type), unique aircraft, local /
  non-local / unclassified shares, median dwell + dwell coverage
- **daily** — 30-day series of runway uses
- **operators** — ranked operator ledger (see §8)
- **methodology** — `data_since`, detector definitions, ADS-B floor disclaimer,
  classifier confidence threshold, unclassified count

The methodology block is **served by the API, not hardcoded in the front end.** This
guarantees the site's published caveats can never drift from the code that produced the
numbers.

---

## 8. The operator ledger

Unit of accountability is the **operator**, not the individual aircraft.

`operations.operator` and `operations.flight_school` columns already exist, alongside a
full FAA registry join (`aircraft_registry.icao_hex = upper(operations.icao24)`), so
this is largely already supported.

**Rendered:**

```
TOP USERS OF THE PUBLIC ASSET
1. <Boulder flight school>      1,240 runway uses   $X
   └ 4 aircraft · 92% touch-and-go · non-local [evidence]
2. ...
▸ expand to see tail numbers
```

### Why operator and not tail number

An N-number resolves to an owner's **name and home address** in one FAA registry lookup.
A public leaderboard of "who owes Longmont the most," aimed at press, is a harassment
vector pointed at individual pilots — some of them student pilots in rented aircraft.

It is also, separately, the *weaker story*. A named individual generates sympathy for
the target. A named business generates outrage at it. "Out-of-town flight schools use
Longmont as a free practice field" is a front-page story; "this guy did 63
touch-and-gos" is a doxxing complaint that eats the coverage.

Tail numbers remain available behind an expand, in aggregate context. Owner names and
addresses are never surfaced.

**Tail number resolution:** use `resolve_display_tail(callsign, registration, icao24)`
(`backend/app/registry/normalize.py:73`). **Do not read `operations.registration`** — the
column exists but no detector ever populates it, so it is always NULL.

---

## 9. Front-end content

Port from the prototype, with these changes:

| Change | Rationale |
|---|---|
| **Delete West Longmont Community Pavilion** | §2.1. Revenue diversion. The single screenshot that discredits the site. |
| Keep the other five projects | All lawful on-airport capital costs. |
| **Hero becomes the counting story** | The fee is arguable; the count is not. §4. |
| **New section: "What the law allows"** | Explains GA 25 in the site's own voice, pre-empting the attack by owning it. |
| **New section: "How we counted"** | 5010 estimate vs. our count; the floor framing; detector definitions per event type. |
| **Explicit single-fee statement** | §3, the GA 22 trap. |
| Calculator retained, re-anchored | Fee slider stays. Its default is the **peer median** from §9.1, not an invented round number. |
| **New: peer fee benchmark table** | §9.1. Makes the fee the market's number rather than ours. |
| **Every fixture value deleted** | No demonstration data survives to launch. All numbers come from `/ledger`. |

**Calculator guardrails:** slider bounds must not permit a user to generate an absurd
figure that gets screenshotted out of context. All outputs are labeled as gross
illustrative revenue before collection costs, exemptions, and legal review.

### 9.1 Peer fee benchmark

The slider's default fee must not be an invented round number. "$10 — says who?" is the
first question a hostile reader asks, and an invented default has no answer.

Research what comparable Colorado GA airports actually charge today — Rocky Mountain
Metro (KBJC), Boulder Municipal (KBDU), Erie Municipal (KEIK), Front Range (KFTG),
Greeley-Weld (KGXY) — publish the table with citations, and **default the slider to the
peer median**. The fee stops being our assumption and becomes the market's. This also
satisfies GA 22's "reasonable fee" test on its face: a fee benchmarked to peers is
prima facie reasonable.

**Research risk, to be reported honestly either way:** many small GA airports do not
charge light-aircraft landing fees, because collection cost exceeds revenue. If the
benchmark comes back mostly zeroes, the "every airport around us charges something"
line is unavailable, and the site must say so and lean harder on the cost-recovery
argument (§10, v2) instead. Do not suppress an inconvenient benchmark result — the
credibility of the whole site rests on volunteering exactly this kind of finding.

---

## 10. Phasing

**v1 — the counting story.** Ships the count, the runway-use ledger, the operator
ranking with locality evidence, the calculator anchored to the peer benchmark (§9.1),
and both new methodology/law sections.

**v2 — the cost story.** Cost-recovery: what a landing actually costs Longmont, derived
from the city's published airport enterprise fund budget, deferred-maintenance list, and
AIP grant history, divided by our own counted runway uses. This converts the fee from an
assumption into a subsidy figure:

> Each landing at Vance Brand costs Longmont $X. Longmont charges $0.

This is the strongest form of the argument and the honest one. It is deferred only
because it depends on budget records not yet in hand, and the counting story stands
alone without it. Two news cycles instead of one.

**Known v1 weakness, accepted:** a hostile reader asks "so what *should* the fee be?"
and v1 answers only with an illustrative slider. v2 answers it properly.

---

## 11. Launch gate: data licensing

**This blocks everything and is sequenced first.**

The live-source priority list (`backend/app/settings.py`) currently defaults to
`adsbx,self_hosted,adsb_lol,adsb_fi,airplanes_live,opensky`.

| Source | Status | Exposure |
|---|---|---|
| **ADSBExchange** (via RapidAPI) | **First in priority** | Commercial/paid; restrictive redistribution terms |
| **adsb.lol** | In use | **ODbL 1.0** (`backend/app/adsblol_historical.py:17`) — attribution **and share-alike**, which a published derived database arguably triggers |
| **FlightAware AeroAPI** | Budget-gated | Paid; restrictive redistribution |
| adsb.fi, airplanes.live | In use | Open feeds; no terms documented in code |
| FAA Releasable Aircraft Database | In use | US Gov, public domain |
| self-hosted feeder | Supported, unset | No third-party terms |

There is no `LICENSE` file, no UI attribution, and no per-source terms document. The
only acknowledgement anywhere is `README.md:59`: *"Review each upstream provider's terms
before public launch."* This has never been done.

**Risk profile change:** a low-traffic tracker with a joke name is one thing. A site
*engineered to attract press attention*, naming businesses and attaching dollar figures
to their activity, is another. This is the most likely way the project dies, and it is
the least enjoyable to fix after the fact.

**Required resolution before launch:**
1. Audit each upstream provider's current terms of use.
2. Move the public-derived data path onto open/ODbL-compatible sources only
   (adsb.lol + self-hosted feeder); remove ADSBExchange and FlightAware from any path
   whose output is republished.
3. Carry ODbL attribution in the UI.
4. Decide whether to publish the derived aggregates under ODbL (share-alike compliance).
5. Add a `LICENSE` file and a data-sources page.

---

## 12. Build order

1. **Licensing audit (§11)** — gates everything. Before any code.
2. `daily_operation_rollup` + `GET /airports/{icao}/ledger` — unblocks the front end.
3. `homebase.py` classifier + evidence + `aircraft_home_base` table.
4. `dwell.py`.
5. Peer fee benchmark research (§9.1) — parallelizable with 2–4; gates the slider default.
6. Front end: port the Art Deco treatment, wire live data, build the law and
   methodology sections.
7. **Compute the real headline number from live data, then write copy around whatever
   it actually is.** No number is committed to copy before this step.

---

## 13. Acceptance criteria

- [ ] No fixture or demonstration value appears anywhere in the shipped site.
- [ ] No project shown on the site would constitute revenue diversion under GA 25.
- [ ] The site states, in its own copy, that fee revenue must remain on the airport.
- [ ] The site states, in its own copy, that the proposed fee applies equally to all
      aircraft (GA 22).
- [ ] Every published count is described as a floor, with the ADS-B equipage caveat
      stated on the page.
- [ ] The billable unit is "runway uses," defined in one sentence on the page, with
      per-event-type detector logic published on the methodology page.
- [ ] Every locality classification displays its supporting evidence inline.
- [ ] Aircraft below the confidence threshold display as `unclassified`, and the size of
      the unclassified bucket is shown.
- [ ] No owner name or address is surfaced anywhere on the site.
- [ ] The slider default is the peer median from a cited benchmark table, not an
      invented figure — or, if peers charge nothing, the site says so plainly.
- [ ] The methodology block is served by the API, not hardcoded in the front end.
- [ ] The ledger endpoint reads the rollup table, not raw `operations`.
- [ ] Upstream data licensing is audited, the public path uses only redistributable
      sources, and attribution is present.
