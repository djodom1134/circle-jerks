# How the Runway Ledger Works

### A plain-language guide to the math behind the Lost Landing ledger for Vance Brand Municipal Airport (KLMO), Longmont, Colorado

*Prepared for the City of Longmont and the public. Every figure below is produced by the same code that runs the public site; this document explains that arithmetic and shows its work.*

---

## The problem we set out to solve

Vance Brand is an **untowered** airport. There is no control tower keeping a log of who used the runway or how often. When a field like this reports its traffic, the number is almost always an **FAA Form 5010 estimate** — a planning figure, not a count of real events. The commonly cited figure for KLMO is on the order of **120,000 operations a year**, and that is exactly what it says it is: an estimate.

This ledger is different. Instead of estimating, it **counts** — one aircraft, one runway pass at a time — using the same public ADS-B signals that hobbyists and flight-tracking sites already receive. Every airplane with an ADS-B transmitter broadcasts its position, altitude, and speed several times a second. We watch those broadcasts near the airport and record each discrete event we can positively identify.

The single most important rule follows from this: **every number on the ledger is a floor, never an estimate.** Aircraft without ADS-B are invisible to us, and coverage has gaps. So the true activity is always *higher* than what we report — never lower. If we cannot prove something, we do not publish it.

---

## What we count: the "runway use"

The billable unit is a **runway use**, and this is the most important definition on the whole page. A runway use is **one arrival at the runway.** We recognize three kinds:

| Event | Plain-language definition (from the site's methodology) |
|---|---|
| **Landing** | The aircraft descended from at least 500 ft above the field, came within 1.5 nm of the runway at 200 ft or lower, and did not climb back out within 300 seconds. |
| **Touch-and-go** | The aircraft flew a closed pattern loop that climbed at least 300 ft above the field and passed within 0.25 nm of the runway. This is a *geometric* test — it does not confirm the wheels touched pavement, so we count it as a *use of the runway*, not a verified touchdown. |
| **Low approach** | The aircraft approached low and slow (200 ft or lower, 90 knots or less), spent no more than 60 seconds near the ground, and climbed back out. |

**Why not just use "operations"?** Because an FAA "operation" is one takeoff *or* one landing. A single touch-and-go is therefore *two* operations. If we multiplied a fee by an operations count, we would **double-charge every touch-and-go on the field.** Counting runway uses instead — arrivals only, never takeoffs — is what keeps the money math honest. (Takeoffs are still recorded, but only so we can measure how long aircraft stay; see "Who actually stopped," below.)

**We also refuse to double-count a single lap.** Because our detector scans in overlapping time windows, one physical pattern lap can occasionally get written down twice. The tell is that the two records **overlap in time**, and you cannot fly two seven-minute circuits that start a minute apart. So when laps overlap, we keep one and discard the rest. Left uncorrected, this would have inflated the published count by roughly 75% in one measured stretch — exactly the kind of error the ledger exists to prevent. When in doubt, we drop the lap, because an inflated count is the one mistake the ledger cannot survive.

---

## The landing-fee math

The City charges **nothing** for the runway today. The ledger asks a simple counterfactual: *what if each runway use paid something?* The arithmetic is deliberately plain, so anyone can check it.

**Step 1 — the observed daily rate.** Over the trailing **30-day window**, take the runway uses we counted and divide by 30:

> daily rate = runway uses in the window ÷ 30 days

**Step 2 — apply a fee.** The site lets the reader drag a slider to pick a fee per runway use. Three presets are offered — **$5 (Conservative)**, **$10 (Fair-use, the default)**, and **$15 (Impact)** — and the slider runs from $0 to $30:

> per-day revenue = daily rate × fee × billable share
> per-month = per-day × 30  ·  per-year = per-day × 365

The **billable share** is a second slider (10%–100%, default 100%) that lets you model exemptions — based-aircraft discounts, training waivers, and so on. It is a modeling assumption the reader controls, not a number the data reports.

**A worked example.** Suppose the sensors counted **3,000 runway uses** in a 30-day window (an illustrative figure — the live site shows the real one):

- daily rate = 3,000 ÷ 30 = **100 runway uses/day**
- at the $10 default, 100% billable: 100 × $10 = **$1,000/day** → **$30,000/month** → **$365,000/year**
- at $5: **$182,500/year**  ·  at $15: **$547,500/year**

Every dollar figure on the page is anchored to that real observed count and the fee you pick — never a hard-coded guess. Where zero runway uses are observed, the calculator honestly shows **$0**; the arithmetic is not broken, there was simply nothing to count.

**The one number that is *not* a floor.** The hero "projected annual" figure extrapolates our own measured rate across a full year:

> projected annual runway uses = (runway uses to date ÷ days we've been counting) × 365

Unlike everything else on the page, this is an **over-estimate risk**, not a floor. Our data so far sits in **Colorado's peak flying season** (mid-June into mid-July); winters are quieter, so a full year at this rate is very likely *higher* than reality. We say so plainly wherever the number appears. Notably, this count-based projection lands in the **same order of magnitude** as the FAA's independent 120,000-operations estimate — two completely different methods, by two different parties, arriving at a comparable scale.

---

## "Locals" vs. visitors: how an aircraft is classified

A common argument for a small airport is that it brings *visitors who spend money in town.* That is a testable claim, so the ledger sorts aircraft into **local**, **non-local**, or **unclassified** — and it does so under strict, deliberately cautious rules, because these labels sit next to named businesses.

Every classification ships with a **receipt** — the specific evidence behind it — and the whole model is built on one principle: **absence is never evidence.** We never penalize an aircraft for something we *failed* to see. The signals that can move a classification are all things we positively observed:

- **Overnight stays** — an aircraft we watched land and not leave for 8+ hours almost certainly sleeps here. This is the strongest sign of a *home* aircraft.
- **Still on the field** — if the last thing we saw was a landing with no departure since, we treat it as still parked here, and we will not call such an aircraft a visitor.
- **Where its arrivals come from** — the *only* signal that can argue an aircraft is a visitor, and it requires repeatedly watching it fly in from a specific named airport.
- **Registered city** — if the FAA registry lists the aircraft's mailing city as Longmont, that nudges *toward* local. A registrant in another town is **not** counted against it (people keep planes at the field down the road from where they get their mail).

The model is **intentionally asymmetric.** A false "local" label slightly understates the ledger and costs no one anything. A false "non-local" label is a public accusation against a named business — so we take the harmless error every time. In practice this means **most aircraft stay "unclassified,"** and we show that count openly rather than quietly sorting the unknowns into whichever bucket we'd prefer. We would rather leave a genuine visitor unlabeled for another month than wrongly brand a local business a visitor for a day.

---

## Who actually stopped: a runway use is not a visit

Arriving at a runway is not the same as *stopping in Longmont* — and the ledger measures the difference. A large share of runway use is aircraft that **never stopped at all**: touch-and-goes and low approaches that touched down (or didn't) and were airborne again in seconds.

For the landings that *did* stop, we measure **time on the ground** by pairing each landing with that aircraft's next takeoff. A stay of **20 minutes or more** is counted as an aircraft that "actually stopped and did something" — refueled, ate, dropped someone off. (That 20-minute line is our own judgment call, not an FAA standard, and we label it as such.) We only report this for landings where we could see *both* the arrival and the departure, and we publish that **coverage** fraction openly — the real number of true visits is higher than what we can prove, never lower.

---

## Naming operators — and protecting people

The ledger ranks activity by **operator** (the business), never by tail number. An N-number resolves to an owner's name and home address in a single FAA lookup, and a public per-aircraft money leaderboard would be a harassment vector aimed at individual pilots — some of them students in rented aircraft. So **only organizations are named** (flight schools, skydiving operators, clubs, corporations, government). Individually owned aircraft — including single-member LLCs and trusts, which are just how a person holds a personal plane — are pooled into a "Private / unaffiliated" bucket and never named. No registrant street address is ever published.

---

## Why this matters for the City

When Longmont accepts federal airport money, it signs a promise (49 U.S.C. § 47107 and FAA Grant Assurance 24) to keep a fee structure that makes the airport **"as self-sustaining as possible… taking into account the volume of traffic and economy of collection."** The historic reason untowered fields charge nothing is precisely *economy of collection* — with no tower and no staff, counting who used the runway once cost more than it could raise.

This ledger dissolves that excuse. It counts runway use with **no tower, no staff, and no budget beyond public ADS-B data.** Collection is now an arithmetic problem, not a staffing problem — and any revenue raised would, by the same federal rules (Grant Assurance 25), be **locked to the airport itself.**

Everything above is arithmetic anyone can re-run. The counts are floors. The fee is illustrative and set by the reader. The projection is labeled as a projection. That is the whole point: **show the work, publish the caveats, and let the numbers speak.**

---

*This is an explanatory document about a civic-transparency tool. Fee levels, exemptions, and collection are matters for the City and its aviation counsel; nothing here is a proposal, a bill, or a legal opinion.*
