# The Lost Landing — Backend Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship a live, publishable `GET /airports/{icao}/ledger` API that reports runway uses, an operator-level usage ledger with locality evidence, dwell time, and a self-describing methodology block — on data the project is legally allowed to republish.

**Architecture:** Four new backend modules (`licensing`, `ledger`, `dwell`, `homebase`), two new SQLite tables (`daily_operation_rollup`, `aircraft_home_base`), one new endpoint, and one new nightly worker loop. Everything reads from the existing, never-pruned `operations` event table. The endpoint is gated in code: if the configured live sources are not redistributable, it returns 503 rather than publishing.

**Tech Stack:** Python 3.12, FastAPI, raw `sqlite3` (no ORM), Redis/Valkey, pytest (`asyncio_mode = "auto"`).

**Spec:** `docs/superpowers/specs/2026-07-13-lost-landing-design.md`

**Branch:** `feat/lost-landing-ledger`

## Global Constraints

- **Billable unit is "runway uses"** = `landing` + `touch_and_go` + `low_approach`. Never `takeoff`. Never a raw "operations" total.
- **Every published count is a floor**, not an estimate (ADS-B equipage gap). The API says so, in the methodology block, in every response.
- **Never read `operations.registration`** — the column exists but no detector populates it, so it is always NULL. Resolve tails via `resolve_display_tail(callsign, registration, icao24)` from `app/registry/normalize.py:73`.
- **Never surface an owner's name or address for a private individual.** Only organizational owner types are named (see Task 5 for the exact allowlist). `llc`, `trust`, `individual`, and `unknown` bucket to an unnamed group.
- **The locality classifier emits evidence, not verdicts.** Every classification carries structured, human-readable supporting evidence. Below-threshold aircraft are `unclassified` and are counted visibly.
- **Timestamps are unix seconds (int).** Local-day bucketing uses the airport's `timezone` column via `db.local_day_key(ts, tz)`.
- **No DB migration framework.** New columns go in `db._migrate()`; new tables go in `db.SCHEMA` (which is `CREATE TABLE IF NOT EXISTS` and re-run on every `init_db`).
- **Commit after every task.** Run tests from `backend/`: `pytest tests/<file> -v`.

---

## File Structure

| File | Responsibility |
|---|---|
| `backend/app/licensing.py` | **New.** Which live sources may be republished; the ledger's publish gate. |
| `backend/app/ledger.py` | **New.** Runway-use vocabulary, rollup read/write, operator aggregation, methodology block. |
| `backend/app/dwell.py` | **New.** Landing→takeoff pairing and dwell summary. Zero schema change. |
| `backend/app/homebase.py` | **New.** Local/non-local classifier with evidence. |
| `backend/app/db.py` | Modify: add two tables to `SCHEMA`. |
| `backend/app/main.py` | Modify: add `GET /airports/{icao}/ledger`. |
| `backend/app/worker.py` | Modify: add a nightly `_ledger_loop`. |
| `backend/scripts/backfill_ledger.py` | **New.** One-shot historical rollup + homebase backfill. |
| `docs/DATA_SOURCES.md` | **New.** Per-source terms audit and redistribution verdict. |
| `LICENSE` | **New.** Repo license (ODbL implications from adsb.lol). |

---

## Task 0: Data licensing audit and the publish gate

**This is the launch gate.** It is first because it can invalidate everything downstream, and because enforcing it in code is cheaper than remembering it.

The live-source priority currently defaults to `adsbx,self_hosted,adsb_lol,adsb_fi,airplanes_live,opensky` (`app/settings.py:48`). ADSBExchange is a paid commercial feed with restrictive redistribution terms and it is **first**. adsb.lol is **ODbL 1.0** (`app/adsblol_historical.py:17`) — attribution *and* share-alike, which a published derived database arguably triggers. `README.md:59` has said "review each upstream provider's terms before public launch" since day one.

The gate: the ledger endpoint physically cannot serve if any configured source is not on the redistributable allowlist.

**Files:**
- Create: `backend/app/licensing.py`
- Create: `backend/tests/test_licensing.py`
- Create: `docs/DATA_SOURCES.md`
- Create: `LICENSE`
- Modify: `backend/app/settings.py:48`

**Interfaces:**
- Consumes: `Settings.live_source_priority_list()` (`app/settings.py:156`)
- Produces:
  - `licensing.REDISTRIBUTABLE_SOURCES: frozenset[str]`
  - `licensing.non_redistributable_sources(settings) -> list[str]`
  - `licensing.ledger_publishable(settings) -> bool`
  - `licensing.ATTRIBUTION: dict` — rendered into the API methodology block by Task 6

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_licensing.py`:

```python
from __future__ import annotations

from app import licensing
from app.settings import Settings


def _settings(priority: str) -> Settings:
    return Settings(live_source_priority=priority)


def test_adsbx_is_not_redistributable():
    s = _settings("adsbx,adsb_lol")
    assert licensing.non_redistributable_sources(s) == ["adsbx"]
    assert licensing.ledger_publishable(s) is False


def test_open_sources_are_publishable():
    s = _settings("adsb_lol,self_hosted")
    assert licensing.non_redistributable_sources(s) == []
    assert licensing.ledger_publishable(s) is True


def test_unknown_source_is_treated_as_restricted():
    # Fail closed: a source we have not audited must never be assumed safe.
    s = _settings("some_new_feed")
    assert licensing.non_redistributable_sources(s) == ["some_new_feed"]
    assert licensing.ledger_publishable(s) is False


def test_attribution_names_odbl_for_adsb_lol():
    assert "ODbL" in licensing.ATTRIBUTION["adsb_lol"]["license"]


def test_default_settings_are_publishable():
    # The shipped default must not block the ledger.
    assert licensing.ledger_publishable(Settings()) is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && pytest tests/test_licensing.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.licensing'`

- [ ] **Step 3: Write the implementation**

Create `backend/app/licensing.py`:

```python
"""Which upstream live sources may appear in a PUBLISHED derived dataset.

The Lost Landing republishes aggregates derived from live ADS-B feeds. Some of
our configured sources forbid that (ADSBExchange is a paid commercial feed;
FlightAware AeroAPI is likewise restrictive). Others permit it with conditions
(adsb.lol is ODbL 1.0: attribution AND share-alike).

This module is the enforcement point. It fails CLOSED: any source not on the
audited allowlist is treated as non-redistributable, so adding a new feed can
never silently make the published ledger unlawful.

Audit findings live in docs/DATA_SOURCES.md. Widening the allowlist requires
updating that document in the same commit.
"""
from __future__ import annotations

# Audited and cleared for republication of derived aggregates.
REDISTRIBUTABLE_SOURCES: frozenset[str] = frozenset({
    "adsb_lol",     # ODbL 1.0 — attribution + share-alike. See ATTRIBUTION below.
    "self_hosted",  # Our own receiver. No third-party terms attach.
})

# Audited and REFUSED. Listed explicitly so the reason survives in code.
RESTRICTED_SOURCES: dict[str, str] = {
    "adsbx": "ADSBExchange (RapidAPI) — paid commercial feed; redistribution prohibited.",
    "flightaware": "FlightAware AeroAPI — paid; redistribution prohibited.",
    "opensky": "OpenSky — terms restrict redistribution; not cleared for a published dataset.",
    "adsb_fi": "adsb.fi — terms not established; failing closed pending audit.",
    "airplanes_live": "airplanes.live — terms not established; failing closed pending audit.",
}

ATTRIBUTION: dict[str, dict[str, str]] = {
    "adsb_lol": {
        "name": "adsb.lol",
        "url": "https://www.adsb.lol/",
        "license": "ODbL 1.0",
        "license_url": "https://opendatacommons.org/licenses/odbl/1-0/",
        # ODbL share-alike: a published derived database must itself be ODbL.
        "share_alike": "This derived dataset is published under ODbL 1.0.",
    },
    "self_hosted": {
        "name": "Self-hosted ADS-B receiver",
        "url": "",
        "license": "None (first-party data)",
        "license_url": "",
        "share_alike": "",
    },
}


def non_redistributable_sources(settings) -> list[str]:
    """Configured sources that may NOT appear in a published derived dataset.

    Order-preserving. Any source absent from REDISTRIBUTABLE_SOURCES counts —
    including ones we have never heard of. Fail closed.
    """
    return [
        source
        for source in settings.live_source_priority_list()
        if source not in REDISTRIBUTABLE_SOURCES
    ]


def ledger_publishable(settings) -> bool:
    """True when every configured live source is cleared for republication."""
    return not non_redistributable_sources(settings)


def restriction_reason(source: str) -> str:
    return RESTRICTED_SOURCES.get(source, f"{source} — not audited; failing closed.")
```

- [ ] **Step 4: Change the shipped default source priority**

In `backend/app/settings.py`, change line 48 from:

```python
    live_source_priority: str = "adsbx,self_hosted,adsb_lol,adsb_fi,airplanes_live,opensky"
```

to:

```python
    # Default is the REDISTRIBUTABLE set only (see app/licensing.py). The Lost
    # Landing republishes derived aggregates; a paid/restricted feed anywhere in
    # this chain makes that unlawful, and licensing.ledger_publishable() will
    # refuse to serve the ledger if one is present.
    live_source_priority: str = "adsb_lol,self_hosted"
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd backend && pytest tests/test_licensing.py -v`
Expected: 5 passed

- [ ] **Step 6: Verify the change did not break existing source-selection tests**

Run: `cd backend && pytest tests/ -v -k "source or live"`
Expected: PASS. If a test asserts the old default priority string, update that assertion to the new default — the new default is deliberate, not a regression.

- [ ] **Step 7: Write the audit document**

Create `docs/DATA_SOURCES.md`:

```markdown
# Data sources and redistribution terms

The Lost Landing publishes aggregates **derived from** live ADS-B data. That is a
republication, and it is governed by each upstream provider's terms. This document
records the audit. `backend/app/licensing.py` enforces it in code and fails closed.

**Last audited:** 2026-07-13

| Source | Cost | Redistribution of derived data | In default priority? |
|---|---|---|---|
| **adsb.lol** | Free | **Permitted — ODbL 1.0.** Requires attribution and **share-alike**: our published derived dataset must itself be ODbL. | ✅ Yes |
| **Self-hosted receiver** | Free | **Permitted.** First-party data, no third-party terms. | ✅ Yes |
| ADSBExchange (RapidAPI) | Paid | **Prohibited.** Commercial feed. | ❌ Removed |
| FlightAware AeroAPI | Paid | **Prohibited.** | ❌ Not in chain |
| OpenSky | Free (OAuth) | **Restricted.** Terms limit redistribution. Not cleared. | ❌ Removed |
| adsb.fi | Free | **Not established.** Failing closed pending audit. | ❌ Removed |
| airplanes.live | Free | **Not established.** Failing closed pending audit. | ❌ Removed |
| FAA Releasable Aircraft Database | Free | **Permitted.** US Government, public domain. | n/a (registry, not live) |

## Consequences

1. `CIRCLEJERK_LIVE_SOURCE_PRIORITY` defaults to `adsb_lol,self_hosted`.
2. If any restricted source is configured, `GET /airports/{icao}/ledger` returns **503**
   and names the offending source. The ledger cannot be served from data we may not
   republish.
3. adsb.lol's share-alike means the derived ledger dataset is published under **ODbL 1.0**.
   See `LICENSE`.
4. The public site must carry adsb.lol attribution. The API serves it in the
   `methodology.attribution` block so the UI cannot drift from it.

## To widen the allowlist

Audit the provider's current terms, record the finding in the table above, and add the
source to `REDISTRIBUTABLE_SOURCES` in `backend/app/licensing.py` **in the same commit**.
```

- [ ] **Step 8: Add the repo LICENSE**

Create `LICENSE` at the repo root. Because adsb.lol is ODbL 1.0 with share-alike, the
derived database is published under ODbL 1.0. Use the full ODbL 1.0 text from
https://opendatacommons.org/licenses/odbl/1-0/, preceded by:

```
The derived aviation-operations database published by this project (the
"Database") is licensed under the Open Database License (ODbL) v1.0.

It is derived in part from adsb.lol data, which is itself ODbL 1.0. ODbL's
share-alike provision requires that this derived database carry the same
license.

Source code in this repository is licensed separately; see the notice at the
end of this file.
```

- [ ] **Step 9: Commit**

```bash
git add backend/app/licensing.py backend/tests/test_licensing.py backend/app/settings.py docs/DATA_SOURCES.md LICENSE
git commit -m "feat(licensing): fail-closed publish gate for derived ADS-B data

Ledger republishes derived aggregates. ADSBExchange (paid, restricted) was
first in the source priority chain and adsb.lol is ODbL share-alike; neither
had ever been audited despite README saying to.

- licensing.py: allowlist + ledger_publishable(), fails closed on any
  unaudited source
- default priority narrowed to adsb_lol,self_hosted
- docs/DATA_SOURCES.md: the audit
- LICENSE: ODbL 1.0 (share-alike, inherited from adsb.lol)"
```

---

## Task 1: The runway-use vocabulary

One module owns the definition of a billable event, so it can never drift between the
rollup, the operator ledger, and the API response.

**Files:**
- Create: `backend/app/ledger.py`
- Create: `backend/tests/test_ledger_vocabulary.py`

**Interfaces:**
- Produces:
  - `ledger.RUNWAY_USE_TYPES: tuple[str, ...]` = `("landing", "touch_and_go", "low_approach")`
  - `ledger.ROLLUP_TYPES: tuple[str, ...]` = `("landing", "takeoff", "touch_and_go", "low_approach")`
  - `ledger.RUNWAY_USE_DEFINITIONS: dict[str, str]` — the per-type detector logic, published verbatim on the methodology page
  - `ledger.is_runway_use(event_type: str) -> bool`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_ledger_vocabulary.py`:

```python
from __future__ import annotations

from app import ledger


def test_takeoff_is_never_a_runway_use():
    # An FAA "operation" is a takeoff OR a landing. Counting both double-counts
    # a touch-and-go. The billable unit excludes takeoffs by construction.
    assert ledger.is_runway_use("takeoff") is False
    assert "takeoff" not in ledger.RUNWAY_USE_TYPES


def test_runway_uses_are_landing_tg_and_low_approach():
    assert set(ledger.RUNWAY_USE_TYPES) == {"landing", "touch_and_go", "low_approach"}


def test_circles_and_passes_are_not_runway_uses():
    assert ledger.is_runway_use("circle") is False
    assert ledger.is_runway_use("pass_over_user") is False


def test_every_runway_use_type_has_a_published_definition():
    # The site publishes exactly how each billable event is detected. A type
    # without a definition would be an unexplained number on a public page.
    for event_type in ledger.RUNWAY_USE_TYPES:
        assert event_type in ledger.RUNWAY_USE_DEFINITIONS
        assert len(ledger.RUNWAY_USE_DEFINITIONS[event_type]) > 40


def test_touch_and_go_definition_admits_it_is_geometric():
    # The detector does NOT verify a touchdown. The published definition must
    # say so; this is the single most attackable claim on the site.
    text = ledger.RUNWAY_USE_DEFINITIONS["touch_and_go"].lower()
    assert "does not confirm" in text or "not verified" in text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && pytest tests/test_ledger_vocabulary.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.ledger'`

- [ ] **Step 3: Write the implementation**

Create `backend/app/ledger.py`:

```python
"""The Lost Landing ledger: runway-use vocabulary, rollups, and operator aggregation.

The BILLABLE UNIT is a "runway use", not an FAA "operation". An FAA operation is
one takeoff OR one landing, so a touch-and-go is two operations — multiplying a
fee by an operations count double-counts every touch-and-go. A runway use is a
single arrival at the runway: a landing, a touch-and-go, or a low approach.

We deliberately do NOT denominate money in "touch-and-gos". The touch-and-go
detector is geometric (a pattern circle passing within 0.25 nm of the runway
segment) and does not verify a touchdown — see detectors.py:41-44. A claim of the
form "N touch-and-gos x $F" is refutable on that basis. "This aircraft used the
runway N times" is true exactly as detected, and is the larger number besides.
"""
from __future__ import annotations

# The billable unit. Every one of these is an arrival at the runway.
RUNWAY_USE_TYPES: tuple[str, ...] = ("landing", "touch_and_go", "low_approach")

# What the daily rollup stores. Includes takeoff, which is NOT billable but is
# needed to pair landings into dwell intervals (dwell.py) and to report the
# conventional FAA operations count alongside ours.
ROLLUP_TYPES: tuple[str, ...] = ("landing", "takeoff", "touch_and_go", "low_approach")

# Published verbatim on the site's methodology page. If a detector changes, this
# changes in the same commit. These strings are the site's factual claims about
# its own method; they are not decorative.
RUNWAY_USE_DEFINITIONS: dict[str, str] = {
    "landing": (
        "The aircraft descended from at least 500 ft above the field, came within "
        "1.5 nm of the runway at 200 ft AGL or below, and did not climb back out "
        "within 300 seconds."
    ),
    "touch_and_go": (
        "The aircraft flew a closed pattern circuit whose track passed within 0.25 nm "
        "of the runway. This is a geometric test: it does not confirm that the wheels "
        "touched the pavement. We count it as a use of the runway, not as a verified "
        "touchdown."
    ),
    "low_approach": (
        "The aircraft approached from at least 500 ft above the field, came within "
        "1.5 nm of the runway at 200 ft AGL or below at 90 knots or less, spent no "
        "more than 60 seconds on the ground, and climbed back out."
    ),
}

FLOOR_DISCLAIMER = (
    "Every count on this page is a floor, not an estimate. Aircraft without ADS-B Out "
    "are invisible to us, so real activity is higher than what we report — never lower."
)


def is_runway_use(event_type: str) -> bool:
    return event_type in RUNWAY_USE_TYPES
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && pytest tests/test_ledger_vocabulary.py -v`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add backend/app/ledger.py backend/tests/test_ledger_vocabulary.py
git commit -m "feat(ledger): runway-use vocabulary as the billable unit

An FAA operation is a takeoff OR a landing, so a touch-and-go is two. Billing
against an operations count double-counts. A runway use (landing + touch-and-go
+ low approach) is one arrival at the runway.

Publishes the detector definition for each type, including the admission that
touch-and-go detection is geometric and does not verify a touchdown."
```

---

## Task 2: `daily_operation_rollup` table

`db.airport_operations_trends` (`app/db.py:940`) pulls every matching row into Python and
runs a correlated subquery against the ~312k-row `aircraft_registry` **per row**. There is
already a performance comment about this at `db.py:825-829`. A public site built to attract
press traffic will hit this path hard. The rollup fixes the hotspot and is also what the
ledger reads — it pays for itself twice.

**Files:**
- Modify: `backend/app/db.py` (add table to `SCHEMA`, before the closing `"""` at line 351)
- Modify: `backend/app/ledger.py`
- Create: `backend/tests/test_ledger_rollup.py`

**Interfaces:**
- Consumes: `ledger.ROLLUP_TYPES`; `db.local_day_key(ts, tz)` (`db.py:619`); `db.airport_timezone(conn, icao)` (`db.py:600`)
- Produces:
  - `ledger.rebuild_rollup(conn, icao, start_ts, end_ts) -> int` — returns rows written
  - `ledger.rollup_daily_runway_uses(conn, icao, start_day, end_day) -> list[dict]` — `[{"date": "YYYY-MM-DD", "runway_uses": int}]`, gap-filled with zeroes
  - `ledger.rollup_totals(conn, icao, start_day, end_day) -> dict` — `{"runway_uses": int, "by_type": {...}, "unique_aircraft": int}`

- [ ] **Step 1: Add the table to the schema**

In `backend/app/db.py`, insert this immediately before the closing `"""` of the `SCHEMA`
string (currently line 351, after the `idx_community_notes_icao` index):

```sql
-- Pre-aggregated daily counts, keyed by AIRPORT-LOCAL calendar day. Exists because
-- airport_operations_trends recomputes in Python over every raw row with a per-row
-- registry subquery (see the perf note at the top of airport_stats); a public
-- dashboard cannot be served off that path. Rebuilt idempotently per day.
CREATE TABLE IF NOT EXISTS daily_operation_rollup (
  icao TEXT NOT NULL,
  date_local TEXT NOT NULL,        -- 'YYYY-MM-DD' in the airport's local timezone
  event_type TEXT NOT NULL,        -- landing | takeoff | touch_and_go | low_approach
  icao24 TEXT NOT NULL,
  count INTEGER NOT NULL,
  PRIMARY KEY (icao, date_local, event_type, icao24)
);
CREATE INDEX IF NOT EXISTS idx_rollup_icao_date ON daily_operation_rollup(icao, date_local);
```

- [ ] **Step 2: Write the failing test**

Create `backend/tests/test_ledger_rollup.py`:

```python
from __future__ import annotations

from app import db, ledger


def seeded_conn(path):
    conn = db.connect(str(path))
    conn.executescript(db.SCHEMA)
    db.seed_db(conn)
    conn.commit()
    return conn


def _op(conn, oid, type_, ts, icao24="a1"):
    db.upsert_operation(conn, db.operation_from_event({
        "id": oid, "type": type_, "icao24": icao24, "callsign": icao24.upper(),
        "timestamp": ts, "airport_icao": "KLMO",
    }))


# 2026-06-01 12:00:00 America/Denver == 1780336800 UTC
DAY1_NOON = 1780336800
DAY = 86400


def test_rollup_counts_runway_uses_and_excludes_takeoff(tmp_path):
    conn = seeded_conn(tmp_path / "t.sqlite3")
    _op(conn, "l1", "landing", DAY1_NOON)
    _op(conn, "g1", "touch_and_go", DAY1_NOON + 60)
    _op(conn, "a1", "low_approach", DAY1_NOON + 120)
    _op(conn, "t1", "takeoff", DAY1_NOON + 180)   # rolled up, but NOT a runway use
    _op(conn, "c1", "circle", DAY1_NOON + 240)    # not rolled up at all
    conn.commit()

    written = ledger.rebuild_rollup(conn, "KLMO", DAY1_NOON - DAY, DAY1_NOON + DAY)
    conn.commit()
    assert written == 4  # circle excluded

    totals = ledger.rollup_totals(conn, "KLMO", "2026-06-01", "2026-06-01")
    assert totals["runway_uses"] == 3            # takeoff excluded from the billable unit
    assert totals["by_type"]["landing"] == 1
    assert totals["by_type"]["touch_and_go"] == 1
    assert totals["by_type"]["low_approach"] == 1
    assert totals["unique_aircraft"] == 1


def test_rollup_buckets_by_local_day_not_utc_day(tmp_path):
    conn = seeded_conn(tmp_path / "t.sqlite3")
    # 2026-06-02 01:00 UTC is still 2026-06-01 19:00 in America/Denver.
    ts = 1780362000  # 2026-06-02T01:00:00Z
    assert db.local_day_key(ts, "America/Denver") == "2026-06-01"
    _op(conn, "l1", "landing", ts)
    conn.commit()

    ledger.rebuild_rollup(conn, "KLMO", ts - DAY, ts + DAY)
    conn.commit()

    assert ledger.rollup_totals(conn, "KLMO", "2026-06-01", "2026-06-01")["runway_uses"] == 1
    assert ledger.rollup_totals(conn, "KLMO", "2026-06-02", "2026-06-02")["runway_uses"] == 0


def test_rebuild_is_idempotent(tmp_path):
    conn = seeded_conn(tmp_path / "t.sqlite3")
    _op(conn, "l1", "landing", DAY1_NOON)
    conn.commit()
    ledger.rebuild_rollup(conn, "KLMO", DAY1_NOON - DAY, DAY1_NOON + DAY)
    ledger.rebuild_rollup(conn, "KLMO", DAY1_NOON - DAY, DAY1_NOON + DAY)
    conn.commit()
    assert ledger.rollup_totals(conn, "KLMO", "2026-06-01", "2026-06-01")["runway_uses"] == 1


def test_rebuild_drops_rows_for_deleted_operations(tmp_path):
    # The maintenance scripts in backend/scripts/ DELETE FROM operations. A rebuild
    # must not leave orphaned counts behind.
    conn = seeded_conn(tmp_path / "t.sqlite3")
    _op(conn, "l1", "landing", DAY1_NOON)
    conn.commit()
    ledger.rebuild_rollup(conn, "KLMO", DAY1_NOON - DAY, DAY1_NOON + DAY)
    conn.commit()

    conn.execute("DELETE FROM operations WHERE id='l1'")
    ledger.rebuild_rollup(conn, "KLMO", DAY1_NOON - DAY, DAY1_NOON + DAY)
    conn.commit()

    assert ledger.rollup_totals(conn, "KLMO", "2026-06-01", "2026-06-01")["runway_uses"] == 0


def test_daily_series_gap_fills_with_zeroes(tmp_path):
    conn = seeded_conn(tmp_path / "t.sqlite3")
    _op(conn, "l1", "landing", DAY1_NOON)
    _op(conn, "l2", "landing", DAY1_NOON + 2 * DAY)  # skip 2026-06-02
    conn.commit()
    ledger.rebuild_rollup(conn, "KLMO", DAY1_NOON - DAY, DAY1_NOON + 3 * DAY)
    conn.commit()

    series = ledger.rollup_daily_runway_uses(conn, "KLMO", "2026-06-01", "2026-06-03")
    assert [d["date"] for d in series] == ["2026-06-01", "2026-06-02", "2026-06-03"]
    assert [d["runway_uses"] for d in series] == [1, 0, 1]


def test_unique_aircraft_counts_distinct_icao24(tmp_path):
    conn = seeded_conn(tmp_path / "t.sqlite3")
    _op(conn, "l1", "landing", DAY1_NOON, icao24="aaa111")
    _op(conn, "l2", "landing", DAY1_NOON + 60, icao24="aaa111")
    _op(conn, "l3", "landing", DAY1_NOON + 120, icao24="bbb222")
    conn.commit()
    ledger.rebuild_rollup(conn, "KLMO", DAY1_NOON - DAY, DAY1_NOON + DAY)
    conn.commit()

    totals = ledger.rollup_totals(conn, "KLMO", "2026-06-01", "2026-06-01")
    assert totals["runway_uses"] == 3
    assert totals["unique_aircraft"] == 2
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd backend && pytest tests/test_ledger_rollup.py -v`
Expected: FAIL — `AttributeError: module 'app.ledger' has no attribute 'rebuild_rollup'`

- [ ] **Step 4: Write the implementation**

Append the functions below to `backend/app/ledger.py`.

**Imports go at the TOP of the module, not inline.** Tasks 2, 5, and 6 each add imports to
`ledger.py`; collect them all under the existing `from __future__ import annotations` rather
than scattering import statements through the file body. By the end of Task 6 the import
block reads:

```python
from __future__ import annotations

import sqlite3
from collections import defaultdict
from datetime import date, timedelta

from . import db, dwell, homebase, licensing
from .registry.normalize import resolve_display_tail
from .registry.owner_type import infer_owner_type
```

For this task, add only what Task 2 needs (`sqlite3`, `defaultdict`, `date`, `timedelta`,
`db`) and grow the block as later tasks require more.

```python
def rebuild_rollup(conn: sqlite3.Connection, icao: str, start_ts: int, end_ts: int) -> int:
    """Rebuild daily_operation_rollup for every LOCAL day touched by [start_ts, end_ts].

    Idempotent: deletes the affected local days wholesale, then reinserts from
    `operations`. Deleting first is what makes a rebuild correct after the
    maintenance scripts in backend/scripts/ prune rows from `operations` — an
    incremental upsert would leave orphaned counts behind forever.

    Returns the number of rollup rows written.
    """
    icao = icao.upper()
    tz = db.airport_timezone(conn, icao)

    rows = conn.execute(
        "SELECT timestamp AS ts, type AS type, icao24 AS icao24 "
        "FROM operations "
        f"WHERE icao=? AND type IN ({','.join('?' * len(ROLLUP_TYPES))}) "
        "  AND timestamp BETWEEN ? AND ? AND icao24 IS NOT NULL",
        (icao, *ROLLUP_TYPES, int(start_ts), int(end_ts)),
    ).fetchall()

    counts: dict[tuple[str, str, str], int] = defaultdict(int)
    for row in rows:
        day = db.local_day_key(row["ts"], tz)
        counts[(day, row["type"], row["icao24"])] += 1

    # The days to rebuild are those spanned by the REQUESTED range, not merely the
    # days that happen to have rows — otherwise a day whose last operation was just
    # deleted would never get cleared.
    span_days = _local_days_between(start_ts, end_ts, tz)
    conn.executemany(
        "DELETE FROM daily_operation_rollup WHERE icao=? AND date_local=?",
        [(icao, day) for day in span_days],
    )
    conn.executemany(
        "INSERT INTO daily_operation_rollup (icao, date_local, event_type, icao24, count) "
        "VALUES (?, ?, ?, ?, ?)",
        [(icao, day, etype, icao24, n) for (day, etype, icao24), n in counts.items()],
    )
    return len(counts)


def _local_days_between(start_ts: int, end_ts: int, tz: str | None) -> list[str]:
    first = date.fromisoformat(db.local_day_key(int(start_ts), tz))
    last = date.fromisoformat(db.local_day_key(int(end_ts), tz))
    out, cursor = [], first
    while cursor <= last:
        out.append(cursor.isoformat())
        cursor += timedelta(days=1)
    return out


def rollup_totals(conn: sqlite3.Connection, icao: str, start_day: str, end_day: str) -> dict:
    """Totals over an inclusive local-day range. Runway uses EXCLUDE takeoffs."""
    rows = conn.execute(
        "SELECT event_type, icao24, SUM(count) AS n "
        "FROM daily_operation_rollup "
        "WHERE icao=? AND date_local BETWEEN ? AND ? "
        "GROUP BY event_type, icao24",
        (icao.upper(), start_day, end_day),
    ).fetchall()

    by_type: dict[str, int] = {etype: 0 for etype in ROLLUP_TYPES}
    aircraft: set[str] = set()
    runway_uses = 0
    for row in rows:
        by_type[row["event_type"]] = by_type.get(row["event_type"], 0) + row["n"]
        if is_runway_use(row["event_type"]):
            runway_uses += row["n"]
            aircraft.add(row["icao24"])

    return {
        "runway_uses": runway_uses,
        "by_type": by_type,
        "unique_aircraft": len(aircraft),
    }


def rollup_daily_runway_uses(
    conn: sqlite3.Connection, icao: str, start_day: str, end_day: str
) -> list[dict]:
    """Daily runway-use series over an inclusive local-day range, gap-filled with zeroes.

    Gap-filling matters: a missing bar and a zero bar mean different things, and a
    chart that silently omits quiet days overstates the typical day.
    """
    rows = conn.execute(
        "SELECT date_local, SUM(count) AS n "
        "FROM daily_operation_rollup "
        f"WHERE icao=? AND date_local BETWEEN ? AND ? "
        f"  AND event_type IN ({','.join('?' * len(RUNWAY_USE_TYPES))}) "
        "GROUP BY date_local",
        (icao.upper(), start_day, end_day, *RUNWAY_USE_TYPES),
    ).fetchall()
    found = {row["date_local"]: row["n"] for row in rows}

    out, cursor, last = [], date.fromisoformat(start_day), date.fromisoformat(end_day)
    while cursor <= last:
        key = cursor.isoformat()
        out.append({"date": key, "runway_uses": found.get(key, 0)})
        cursor += timedelta(days=1)
    return out
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd backend && pytest tests/test_ledger_rollup.py -v`
Expected: 6 passed

- [ ] **Step 6: Verify the schema addition did not break existing tests**

Run: `cd backend && pytest tests/ -v`
Expected: all previously-passing tests still pass.

- [ ] **Step 7: Commit**

```bash
git add backend/app/db.py backend/app/ledger.py backend/tests/test_ledger_rollup.py
git commit -m "feat(ledger): daily_operation_rollup keyed by airport-local day

airport_operations_trends recomputes in Python over every raw row with a
per-row registry subquery (known hotspot, db.py:825-829). A public dashboard
cannot be served off that path.

Rebuild deletes the affected local days before reinserting, so a rebuild stays
correct after the maintenance scripts prune rows from operations. Daily series
gap-fills with zeroes: a missing bar and a zero bar are different claims."
```

---

## Task 3: `dwell.py` — time on field

Dwell = the interval between a landing and that aircraft's next takeoff. Both event types
are already detected and stored with timestamps, so this needs **zero schema change**.

Task 4's overnight signal reuses this pairing, which is why dwell comes first.

**Files:**
- Create: `backend/app/dwell.py`
- Create: `backend/tests/test_dwell.py`

**Interfaces:**
- Consumes: `operations` table (`landing` and `takeoff` rows)
- Produces:
  - `dwell.dwell_intervals(conn, icao, start_ts, end_ts) -> list[dict]` — `[{"icao24", "landing_ts", "takeoff_ts", "seconds"}]`, sorted by `landing_ts`
  - `dwell.dwell_summary(conn, icao, start_ts, end_ts, max_seconds=21600) -> dict` — `{"median_seconds", "sample_size", "landings", "coverage"}`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_dwell.py`:

```python
from __future__ import annotations

from app import db, dwell


def seeded_conn(path):
    conn = db.connect(str(path))
    conn.executescript(db.SCHEMA)
    db.seed_db(conn)
    conn.commit()
    return conn


def _op(conn, oid, type_, ts, icao24="a1"):
    db.upsert_operation(conn, db.operation_from_event({
        "id": oid, "type": type_, "icao24": icao24, "callsign": icao24.upper(),
        "timestamp": ts, "airport_icao": "KLMO",
    }))


BASE = 1780336800  # 2026-06-01 12:00 America/Denver
WINDOW = (BASE - 86400, BASE + 7 * 86400)


def test_pairs_landing_with_next_takeoff(tmp_path):
    conn = seeded_conn(tmp_path / "t.sqlite3")
    _op(conn, "l1", "landing", BASE)
    _op(conn, "t1", "takeoff", BASE + 900)   # 15 minutes on the ground
    conn.commit()

    intervals = dwell.dwell_intervals(conn, "KLMO", *WINDOW)
    assert len(intervals) == 1
    assert intervals[0]["seconds"] == 900
    assert intervals[0]["icao24"] == "a1"


def test_unpaired_landing_is_excluded_not_imputed(tmp_path):
    # An aircraft that landed and never departed within the window has no known
    # dwell. Guessing one would be inventing data.
    conn = seeded_conn(tmp_path / "t.sqlite3")
    _op(conn, "l1", "landing", BASE)
    conn.commit()
    assert dwell.dwell_intervals(conn, "KLMO", *WINDOW) == []


def test_takeoff_before_any_landing_is_ignored(tmp_path):
    # The aircraft was already on the field when the window opened. There is no
    # landing to pair it with.
    conn = seeded_conn(tmp_path / "t.sqlite3")
    _op(conn, "t1", "takeoff", BASE)
    _op(conn, "l1", "landing", BASE + 3600)
    conn.commit()
    assert dwell.dwell_intervals(conn, "KLMO", *WINDOW) == []


def test_second_landing_without_intervening_takeoff_supersedes_the_first(tmp_path):
    # Two landings then one takeoff means we missed a departure. Pair the takeoff
    # with the MOST RECENT landing; the earlier one is unpaired and dropped.
    conn = seeded_conn(tmp_path / "t.sqlite3")
    _op(conn, "l1", "landing", BASE)
    _op(conn, "l2", "landing", BASE + 3600)
    _op(conn, "t1", "takeoff", BASE + 3600 + 600)
    conn.commit()

    intervals = dwell.dwell_intervals(conn, "KLMO", *WINDOW)
    assert len(intervals) == 1
    assert intervals[0]["landing_ts"] == BASE + 3600
    assert intervals[0]["seconds"] == 600


def test_pairing_is_per_aircraft(tmp_path):
    conn = seeded_conn(tmp_path / "t.sqlite3")
    _op(conn, "l1", "landing", BASE, icao24="aaa111")
    _op(conn, "l2", "landing", BASE + 60, icao24="bbb222")
    _op(conn, "t2", "takeoff", BASE + 120, icao24="bbb222")   # bbb222: 60s
    _op(conn, "t1", "takeoff", BASE + 300, icao24="aaa111")   # aaa111: 300s
    conn.commit()

    by_ac = {i["icao24"]: i["seconds"] for i in dwell.dwell_intervals(conn, "KLMO", *WINDOW)}
    assert by_ac == {"aaa111": 300, "bbb222": 60}


def test_summary_excludes_overnight_stays_from_the_median(tmp_path):
    # A based aircraft parked for two days is not a "visit". Including it would
    # drag the median time-on-field into meaninglessness.
    conn = seeded_conn(tmp_path / "t.sqlite3")
    _op(conn, "l1", "landing", BASE, icao24="aaa111")
    _op(conn, "t1", "takeoff", BASE + 600, icao24="aaa111")            # 10 min
    _op(conn, "l2", "landing", BASE, icao24="bbb222")
    _op(conn, "t2", "takeoff", BASE + 1800, icao24="bbb222")           # 30 min
    _op(conn, "l3", "landing", BASE, icao24="ccc333")
    _op(conn, "t3", "takeoff", BASE + 2 * 86400, icao24="ccc333")      # 2 days
    conn.commit()

    summary = dwell.dwell_summary(conn, "KLMO", *WINDOW)
    assert summary["sample_size"] == 2          # the 2-day stay is out
    assert summary["median_seconds"] == 1200    # mean of 600 and 1800


def test_summary_reports_coverage_and_never_divides_by_zero(tmp_path):
    conn = seeded_conn(tmp_path / "t.sqlite3")
    _op(conn, "l1", "landing", BASE, icao24="aaa111")
    _op(conn, "t1", "takeoff", BASE + 600, icao24="aaa111")
    _op(conn, "l2", "landing", BASE, icao24="bbb222")   # never departs
    conn.commit()

    summary = dwell.dwell_summary(conn, "KLMO", *WINDOW)
    assert summary["landings"] == 2
    assert summary["sample_size"] == 1
    assert summary["coverage"] == 0.5

    empty = seeded_conn(tmp_path / "e.sqlite3")
    blank = dwell.dwell_summary(empty, "KLMO", *WINDOW)
    assert blank["median_seconds"] is None
    assert blank["coverage"] == 0.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && pytest tests/test_dwell.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.dwell'`

- [ ] **Step 3: Write the implementation**

Create `backend/app/dwell.py`:

```python
"""Time on field, derived from landing -> takeoff pairs already in `operations`.

Nothing persists a ground interval, and nothing needs to: both `landing` and
`takeoff` are detected and stored with timestamps, so dwell is a pairing problem,
not a schema problem. Zero migration.

We never impute. A landing with no matching takeoff in the window has no known
dwell and is excluded from the sample — but the exclusion is REPORTED, as
`coverage`, so a reader can see how much of the activity the median actually
speaks for.
"""
from __future__ import annotations

import sqlite3
from statistics import median

# Above this, the aircraft is parked, not visiting. Used to keep the median
# meaningful for transient time-on-field; dwell_intervals() still returns the
# long stays, because homebase.py needs exactly those to spot a based aircraft.
DEFAULT_MAX_VISIT_SECONDS = 6 * 3600


def dwell_intervals(
    conn: sqlite3.Connection, icao: str, start_ts: int, end_ts: int
) -> list[dict]:
    """Every landing paired with that aircraft's next takeoff, in [start_ts, end_ts].

    Pairing rules, and why:
      - A takeoff before any landing is ignored: the aircraft was already parked
        when the window opened, so there is no arrival to measure from.
      - Two landings with no takeoff between them means we MISSED a departure.
        Pair the takeoff with the most recent landing and drop the earlier one,
        rather than inventing a multi-day dwell out of a detection gap.
      - A landing with no subsequent takeoff is dropped, not imputed.
    """
    rows = conn.execute(
        "SELECT icao24, type, timestamp AS ts FROM operations "
        "WHERE icao=? AND type IN ('landing','takeoff') "
        "  AND timestamp BETWEEN ? AND ? AND icao24 IS NOT NULL "
        "ORDER BY icao24 ASC, timestamp ASC",
        (icao.upper(), int(start_ts), int(end_ts)),
    ).fetchall()

    intervals: list[dict] = []
    open_landing: dict[str, int] = {}
    for row in rows:
        aircraft = row["icao24"]
        if row["type"] == "landing":
            # Overwrites any still-open landing: a missed takeoff must not become
            # a fake dwell spanning to the next departure days later.
            open_landing[aircraft] = row["ts"]
            continue
        landed_at = open_landing.pop(aircraft, None)
        if landed_at is None:
            continue  # already on the field when the window opened
        intervals.append({
            "icao24": aircraft,
            "landing_ts": landed_at,
            "takeoff_ts": row["ts"],
            "seconds": row["ts"] - landed_at,
        })

    intervals.sort(key=lambda i: i["landing_ts"])
    return intervals


def dwell_summary(
    conn: sqlite3.Connection,
    icao: str,
    start_ts: int,
    end_ts: int,
    max_seconds: int = DEFAULT_MAX_VISIT_SECONDS,
) -> dict:
    """Median time on field for transient visits, plus how much of the activity
    that median actually covers.

    `coverage` is paired landings / all landings. Publishing it is the point: a
    median drawn from 20% of arrivals is a different claim from one drawn from 90%,
    and the reader is entitled to know which they are looking at.
    """
    visits = [i["seconds"] for i in dwell_intervals(conn, icao, start_ts, end_ts)
              if i["seconds"] <= max_seconds]

    landings = conn.execute(
        "SELECT COUNT(*) AS n FROM operations "
        "WHERE icao=? AND type='landing' AND timestamp BETWEEN ? AND ? AND icao24 IS NOT NULL",
        (icao.upper(), int(start_ts), int(end_ts)),
    ).fetchone()["n"]

    return {
        "median_seconds": int(median(visits)) if visits else None,
        "sample_size": len(visits),
        "landings": landings,
        "coverage": round(len(visits) / landings, 4) if landings else 0.0,
        "max_visit_seconds": max_seconds,
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && pytest tests/test_dwell.py -v`
Expected: 7 passed

- [ ] **Step 5: Commit**

```bash
git add backend/app/dwell.py backend/tests/test_dwell.py
git commit -m "feat(dwell): time on field from landing->takeoff pairs, no schema change

Both event types are already detected and stored with timestamps, so dwell is a
pairing problem, not a schema problem.

Never imputes an unpaired landing. Reports coverage (paired/total) alongside the
median, because a median drawn from 20% of arrivals is a different claim from one
drawn from 90%."
```

---

## Task 4: `homebase.py` — local vs non-local, with evidence

The backend has **no** local/non-local classification of any kind today. This is the
largest single build in the plan.

The hazard being designed out is *"you **guessed** I'm not local."* A press-facing site
that must run a correction about a named business loses more than the claim ever earned.
So the classifier does not emit a verdict — it emits a **receipt**:

> **Non-local** — 0 overnight stays at KLMO in 180 days · 43 of 47 arrivals originated at KBDU · registrant address Boulder, CO

**Files:**
- Modify: `backend/app/db.py` (add `aircraft_home_base` to `SCHEMA`)
- Create: `backend/app/homebase.py`
- Create: `backend/tests/test_homebase.py`

**Interfaces:**
- Consumes: `dwell.dwell_intervals(conn, icao, start_ts, end_ts)`; `operations.origin_airport_icao`; `aircraft_registry.registrant_city` / `registrant_state`
- Produces:
  - `homebase.LOCAL` = `"local"`, `homebase.NON_LOCAL` = `"non_local"`, `homebase.UNCLASSIFIED` = `"unclassified"`
  - `homebase.CONFIDENCE_THRESHOLD` = `0.5`
  - `homebase.classify(conn, icao, icao24, now_ts, lookback_days=180) -> dict` — `{"icao24", "locality", "confidence", "evidence": [{"code","text"}]}`
  - `homebase.recompute_airport(conn, icao, now_ts, lookback_days=180) -> int` — writes `aircraft_home_base`, returns rows written
  - `homebase.locality_map(conn, icao) -> dict[str, dict]` — `icao24 -> {"locality","confidence","evidence"}`

- [ ] **Step 1: Add the table to the schema**

In `backend/app/db.py`, insert immediately before the closing `"""` of `SCHEMA`
(after the `idx_rollup_icao_date` index added in Task 2):

```sql
-- Local vs non-local, recomputed nightly. `evidence_json` is a list of
-- {code, text} facts that the UI renders INLINE next to the classification —
-- the site never asserts a locality without showing why, because an unexplained
-- "non-local" next to a named business is a correction waiting to happen.
CREATE TABLE IF NOT EXISTS aircraft_home_base (
  icao TEXT NOT NULL,              -- the airport this judgement is ABOUT
  icao24 TEXT NOT NULL,
  locality TEXT NOT NULL,          -- local | non_local | unclassified
  confidence REAL NOT NULL,
  based_icao TEXT,                 -- best guess at where it IS based, when known
  evidence_json TEXT NOT NULL,
  computed_at INTEGER NOT NULL,
  PRIMARY KEY (icao, icao24)
);
CREATE INDEX IF NOT EXISTS idx_home_base_icao ON aircraft_home_base(icao, locality);
```

- [ ] **Step 2: Write the failing test**

Create `backend/tests/test_homebase.py`:

```python
from __future__ import annotations

import json

from app import db, homebase


def seeded_conn(path):
    conn = db.connect(str(path))
    conn.executescript(db.SCHEMA)
    db.seed_db(conn)
    conn.commit()
    return conn


def _op(conn, oid, type_, ts, icao24="a1", origin=None):
    db.upsert_operation(conn, db.operation_from_event({
        "id": oid, "type": type_, "icao24": icao24, "callsign": icao24.upper(),
        "timestamp": ts, "airport_icao": "KLMO",
    }))
    if origin:
        conn.execute("UPDATE operations SET origin_airport_icao=? WHERE id=?", (origin, oid))


NOW = 1780336800 + 90 * 86400   # ~90 days after our reference day
DAY = 86400


def _overnight(conn, day_offset, icao24):
    """A landing in the evening and a departure the next morning."""
    landed = NOW - day_offset * DAY - 4 * 3600      # evening
    departed = landed + 14 * 3600                    # next morning
    _op(conn, f"l{icao24}{day_offset}", "landing", landed, icao24=icao24)
    _op(conn, f"t{icao24}{day_offset}", "takeoff", departed, icao24=icao24)


def test_repeated_overnight_stays_classify_as_local(tmp_path):
    conn = seeded_conn(tmp_path / "t.sqlite3")
    for d in (10, 20, 30, 40):
        _overnight(conn, d, "aaa111")
    conn.commit()

    result = homebase.classify(conn, "KLMO", "aaa111", now_ts=NOW)
    assert result["locality"] == homebase.LOCAL
    assert result["confidence"] >= homebase.CONFIDENCE_THRESHOLD
    codes = {e["code"] for e in result["evidence"]}
    assert "overnight_stays" in codes


def test_no_overnights_plus_foreign_origin_classifies_as_non_local(tmp_path):
    conn = seeded_conn(tmp_path / "t.sqlite3")
    # 10 touch-and-go visits, every one arriving from KBDU, never staying the night.
    for i in range(10):
        _op(conn, f"g{i}", "touch_and_go", NOW - i * DAY, icao24="bbb222", origin="KBDU")
    conn.commit()

    result = homebase.classify(conn, "KLMO", "bbb222", now_ts=NOW)
    assert result["locality"] == homebase.NON_LOCAL
    assert result["based_icao"] == "KBDU"
    codes = {e["code"] for e in result["evidence"]}
    assert "overnight_stays" in codes
    assert "arrival_origin" in codes


def test_evidence_is_human_readable_and_states_the_numbers(tmp_path):
    conn = seeded_conn(tmp_path / "t.sqlite3")
    for i in range(10):
        _op(conn, f"g{i}", "touch_and_go", NOW - i * DAY, icao24="bbb222", origin="KBDU")
    conn.commit()

    result = homebase.classify(conn, "KLMO", "bbb222", now_ts=NOW)
    origin_text = next(e["text"] for e in result["evidence"] if e["code"] == "arrival_origin")
    assert "10" in origin_text and "KBDU" in origin_text
    overnight_text = next(e["text"] for e in result["evidence"] if e["code"] == "overnight_stays")
    assert "0" in overnight_text and "180" in overnight_text


def test_thin_evidence_is_unclassified_not_guessed(tmp_path):
    # One visit, no origin data, no overnight. We do not know. Say so.
    conn = seeded_conn(tmp_path / "t.sqlite3")
    _op(conn, "g1", "touch_and_go", NOW - DAY, icao24="ccc333")
    conn.commit()

    result = homebase.classify(conn, "KLMO", "ccc333", now_ts=NOW)
    assert result["locality"] == homebase.UNCLASSIFIED
    assert result["confidence"] < homebase.CONFIDENCE_THRESHOLD


def test_unclassified_still_carries_its_evidence(tmp_path):
    # Even a "we don't know" shows its working.
    conn = seeded_conn(tmp_path / "t.sqlite3")
    _op(conn, "g1", "touch_and_go", NOW - DAY, icao24="ccc333")
    conn.commit()
    result = homebase.classify(conn, "KLMO", "ccc333", now_ts=NOW)
    assert result["evidence"]


def test_registrant_address_alone_never_decides(tmp_path):
    # The FAA registry records a MAILING address, not a based airport (see the
    # disclaimer at registry/profile.py:26). It may nudge; it may not decide.
    conn = seeded_conn(tmp_path / "t.sqlite3")
    _op(conn, "g1", "touch_and_go", NOW - DAY, icao24="ddd444")
    conn.execute(
        "INSERT INTO aircraft_registry (n_number, icao_hex, registrant_city, registrant_state) "
        "VALUES ('N1', 'DDD444', 'Boulder', 'CO')"
    )
    conn.commit()

    result = homebase.classify(conn, "KLMO", "ddd444", now_ts=NOW)
    assert result["locality"] == homebase.UNCLASSIFIED


def test_recompute_airport_persists_and_is_idempotent(tmp_path):
    conn = seeded_conn(tmp_path / "t.sqlite3")
    for d in (10, 20, 30, 40):
        _overnight(conn, d, "aaa111")
    for i in range(10):
        _op(conn, f"g{i}", "touch_and_go", NOW - i * DAY, icao24="bbb222", origin="KBDU")
    conn.commit()

    assert homebase.recompute_airport(conn, "KLMO", now_ts=NOW) == 2
    conn.commit()
    assert homebase.recompute_airport(conn, "KLMO", now_ts=NOW) == 2  # upsert, not duplicate
    conn.commit()

    rows = conn.execute("SELECT * FROM aircraft_home_base WHERE icao='KLMO'").fetchall()
    assert len(rows) == 2
    by_ac = {r["icao24"]: r for r in rows}
    assert by_ac["aaa111"]["locality"] == homebase.LOCAL
    assert by_ac["bbb222"]["locality"] == homebase.NON_LOCAL
    assert json.loads(by_ac["bbb222"]["evidence_json"])


def test_locality_map_reads_back_what_recompute_wrote(tmp_path):
    conn = seeded_conn(tmp_path / "t.sqlite3")
    for i in range(10):
        _op(conn, f"g{i}", "touch_and_go", NOW - i * DAY, icao24="bbb222", origin="KBDU")
    conn.commit()
    homebase.recompute_airport(conn, "KLMO", now_ts=NOW)
    conn.commit()

    mapping = homebase.locality_map(conn, "KLMO")
    assert mapping["bbb222"]["locality"] == homebase.NON_LOCAL
    assert mapping["bbb222"]["evidence"]
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd backend && pytest tests/test_homebase.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.homebase'`

- [ ] **Step 4: Write the implementation**

Create `backend/app/homebase.py`:

```python
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
    # The FAA registry holds a MAILING address, not a based airport. See the
    # disclaimer already shipped at registry/profile.py:26.
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
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd backend && pytest tests/test_homebase.py -v`
Expected: 8 passed

- [ ] **Step 6: Commit**

```bash
git add backend/app/db.py backend/app/homebase.py backend/tests/test_homebase.py
git commit -m "feat(homebase): local vs non-local classifier that emits evidence, not verdicts

No local/non-local classification existed anywhere in the codebase. Signals:
overnight presence (strongest), arrival-origin distribution, registrant address
(a whisper — the FAA registry holds a mailing address, not a based airport).

Every classification carries structured evidence the UI renders inline. Below
threshold renders as 'unclassified' and is counted as such. The site names
businesses; an unexplained 'non-local' badge on a named flight school is a
correction waiting to happen."
```

---

## Task 5: The operator ledger

Aggregate runway uses by **operator**, not by tail number.

An N-number resolves to an owner's **name and home address** in one FAA registry lookup.
A public per-aircraft money leaderboard aimed at press is a harassment vector pointed at
individual pilots — some of them students in rented aircraft. It is also the *weaker*
story: a named individual generates sympathy for the target; a named business generates
outrage at it.

So: **organizations are named, individuals never are.**

**Files:**
- Modify: `backend/app/ledger.py`
- Create: `backend/tests/test_ledger_operators.py`

**Interfaces:**
- Consumes: `homebase.locality_map(conn, icao)`; `registry.owner_type.infer_owner_type(name) -> OwnerTypeResult(owner_type, confidence, reason)` (`app/registry/owner_type.py:136`); `registry.normalize.resolve_display_tail(callsign, registration, icao24)` (`app/registry/normalize.py:73`)
- Produces:
  - `ledger.NAMEABLE_OWNER_TYPES: frozenset[str]`
  - `ledger.PRIVATE_BUCKET: str` = `"Private / unaffiliated"`
  - `ledger.operator_ledger(conn, icao, start_day, end_day, limit=10) -> list[dict]`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_ledger_operators.py`:

```python
from __future__ import annotations

from app import db, homebase, ledger


def seeded_conn(path):
    conn = db.connect(str(path))
    conn.executescript(db.SCHEMA)
    db.seed_db(conn)
    conn.commit()
    return conn


def _op(conn, oid, type_, ts, icao24, callsign=None, origin=None):
    db.upsert_operation(conn, db.operation_from_event({
        "id": oid, "type": type_, "icao24": icao24,
        "callsign": callsign or icao24.upper(),
        "timestamp": ts, "airport_icao": "KLMO",
    }))
    if origin:
        conn.execute("UPDATE operations SET origin_airport_icao=? WHERE id=?", (origin, oid))


def _register(conn, n_number, icao_hex, registrant_name, city="Boulder", state="CO"):
    conn.execute(
        "INSERT INTO aircraft_registry (n_number, icao_hex, registrant_name, "
        "  registrant_city, registrant_state) VALUES (?, ?, ?, ?, ?)",
        (n_number, icao_hex.upper(), registrant_name, city, state),
    )


BASE = 1780336800
DAY = 86400


def test_flight_school_is_named(tmp_path):
    conn = seeded_conn(tmp_path / "t.sqlite3")
    _register(conn, "N111AA", "aaa111", "BOULDER FLIGHT SCHOOL LLC")
    for i in range(5):
        _op(conn, f"g{i}", "touch_and_go", BASE + i * 60, "aaa111", callsign="N111AA")
    conn.commit()
    ledger.rebuild_rollup(conn, "KLMO", BASE - DAY, BASE + DAY)
    conn.commit()

    rows = ledger.operator_ledger(conn, "KLMO", "2026-06-01", "2026-06-01")
    assert len(rows) == 1
    assert rows[0]["operator"] == "BOULDER FLIGHT SCHOOL LLC"
    assert rows[0]["owner_type"] == "flight_school"
    assert rows[0]["runway_uses"] == 5
    assert rows[0]["aircraft_count"] == 1


def test_private_individual_is_never_named(tmp_path):
    # The registry gives us a person's name and home address. We use neither.
    conn = seeded_conn(tmp_path / "t.sqlite3")
    _register(conn, "N222BB", "bbb222", "JOHN Q SMITH")
    _op(conn, "l1", "landing", BASE, "bbb222", callsign="N222BB")
    conn.commit()
    ledger.rebuild_rollup(conn, "KLMO", BASE - DAY, BASE + DAY)
    conn.commit()

    rows = ledger.operator_ledger(conn, "KLMO", "2026-06-01", "2026-06-01")
    assert rows[0]["operator"] == ledger.PRIVATE_BUCKET
    assert "SMITH" not in str(rows)


def test_single_member_llc_is_not_named(tmp_path):
    # "JOHN SMITH AVIATION LLC" is a legal entity that is also, effectively, a
    # person's name. LLC and trust are NOT nameable — they are the standard way an
    # individual holds a personal aircraft.
    conn = seeded_conn(tmp_path / "t.sqlite3")
    _register(conn, "N333CC", "ccc333", "SMITH AVIATION LLC")
    _op(conn, "l1", "landing", BASE, "ccc333", callsign="N333CC")
    conn.commit()
    ledger.rebuild_rollup(conn, "KLMO", BASE - DAY, BASE + DAY)
    conn.commit()

    rows = ledger.operator_ledger(conn, "KLMO", "2026-06-01", "2026-06-01")
    assert rows[0]["operator"] == ledger.PRIVATE_BUCKET


def test_no_registrant_address_ever_appears_in_output(tmp_path):
    conn = seeded_conn(tmp_path / "t.sqlite3")
    _register(conn, "N111AA", "aaa111", "BOULDER FLIGHT SCHOOL LLC", city="Boulder", state="CO")
    _op(conn, "g1", "touch_and_go", BASE, "aaa111", callsign="N111AA")
    conn.commit()
    ledger.rebuild_rollup(conn, "KLMO", BASE - DAY, BASE + DAY)
    conn.commit()

    rows = ledger.operator_ledger(conn, "KLMO", "2026-06-01", "2026-06-01")
    assert "registrant_street" not in rows[0]
    assert "registrant_city" not in rows[0]


def test_aircraft_are_grouped_under_one_operator(tmp_path):
    conn = seeded_conn(tmp_path / "t.sqlite3")
    _register(conn, "N111AA", "aaa111", "BOULDER FLIGHT SCHOOL LLC")
    _register(conn, "N111AB", "aaa112", "BOULDER FLIGHT SCHOOL LLC")
    _op(conn, "g1", "touch_and_go", BASE, "aaa111", callsign="N111AA")
    _op(conn, "g2", "touch_and_go", BASE + 60, "aaa112", callsign="N111AB")
    _op(conn, "g3", "touch_and_go", BASE + 120, "aaa112", callsign="N111AB")
    conn.commit()
    ledger.rebuild_rollup(conn, "KLMO", BASE - DAY, BASE + DAY)
    conn.commit()

    rows = ledger.operator_ledger(conn, "KLMO", "2026-06-01", "2026-06-01")
    assert len(rows) == 1
    assert rows[0]["runway_uses"] == 3
    assert rows[0]["aircraft_count"] == 2
    assert sorted(a["tail"] for a in rows[0]["aircraft"]) == ["N111AA", "N111AB"]


def test_ranked_by_runway_uses_descending(tmp_path):
    conn = seeded_conn(tmp_path / "t.sqlite3")
    _register(conn, "N111AA", "aaa111", "SMALL FLYING CLUB")
    _register(conn, "N222BB", "bbb222", "BIG FLIGHT SCHOOL LLC")
    _op(conn, "g1", "touch_and_go", BASE, "aaa111", callsign="N111AA")
    for i in range(4):
        _op(conn, f"g2{i}", "touch_and_go", BASE + i * 60, "bbb222", callsign="N222BB")
    conn.commit()
    ledger.rebuild_rollup(conn, "KLMO", BASE - DAY, BASE + DAY)
    conn.commit()

    rows = ledger.operator_ledger(conn, "KLMO", "2026-06-01", "2026-06-01")
    assert [r["runway_uses"] for r in rows] == [4, 1]


def test_operator_carries_locality_and_its_evidence(tmp_path):
    conn = seeded_conn(tmp_path / "t.sqlite3")
    _register(conn, "N111AA", "aaa111", "BOULDER FLIGHT SCHOOL LLC")
    now = BASE + 90 * DAY
    for i in range(10):
        _op(conn, f"g{i}", "touch_and_go", now - i * DAY, "aaa111",
            callsign="N111AA", origin="KBDU")
    conn.commit()
    ledger.rebuild_rollup(conn, "KLMO", now - 100 * DAY, now)
    homebase.recompute_airport(conn, "KLMO", now_ts=now)
    conn.commit()

    start = db.local_day_key(now - 100 * DAY, "America/Denver")
    end = db.local_day_key(now, "America/Denver")
    rows = ledger.operator_ledger(conn, "KLMO", start, end)
    assert rows[0]["locality"] == homebase.NON_LOCAL
    assert rows[0]["locality_evidence"]
    assert any("KBDU" in e["text"] for e in rows[0]["locality_evidence"])


def test_takeoffs_are_not_counted_as_runway_uses(tmp_path):
    conn = seeded_conn(tmp_path / "t.sqlite3")
    _register(conn, "N111AA", "aaa111", "BOULDER FLIGHT SCHOOL LLC")
    _op(conn, "l1", "landing", BASE, "aaa111", callsign="N111AA")
    _op(conn, "t1", "takeoff", BASE + 600, "aaa111", callsign="N111AA")
    conn.commit()
    ledger.rebuild_rollup(conn, "KLMO", BASE - DAY, BASE + DAY)
    conn.commit()

    rows = ledger.operator_ledger(conn, "KLMO", "2026-06-01", "2026-06-01")
    assert rows[0]["runway_uses"] == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && pytest tests/test_ledger_operators.py -v`
Expected: FAIL — `AttributeError: module 'app.ledger' has no attribute 'operator_ledger'`

- [ ] **Step 3: Write the implementation**

Append to `backend/app/ledger.py`. Add `homebase` to the existing `from . import db, ...`
line at the top of the module, and add the two `registry` imports there too — not here.

```python
# Owner types we will NAME on a public page. Organizations only.
#
# `llc` and `trust` are deliberately EXCLUDED even though they are legal entities:
# a single-member LLC ("SMITH AVIATION LLC") and a holding trust are the standard
# ways an individual owns a personal aircraft, and naming them names a person.
# `individual` and `unknown` are excluded for the obvious reason.
NAMEABLE_OWNER_TYPES: frozenset[str] = frozenset({
    "flight_school",
    "skydiving",
    "commercial_airline",
    "club",
    "university",
    "government",
    "corporation",
})

PRIVATE_BUCKET = "Private / unaffiliated"


def operator_ledger(
    conn: sqlite3.Connection,
    icao: str,
    start_day: str,
    end_day: str,
    limit: int = 10,
) -> list[dict]:
    """Runway uses aggregated by OPERATOR, ranked descending.

    Never by tail number. An N-number resolves to an owner's name and home address
    in one FAA registry lookup; a public per-aircraft money leaderboard aimed at
    press is a harassment vector pointed at individual pilots, some of them students
    in rented aircraft. It is also the weaker story — a named individual generates
    sympathy for the target, a named business generates outrage at it.

    Organizations are named. Individuals are bucketed and never named. No registrant
    address is emitted in any form.
    """
    icao = icao.upper()

    rows = conn.execute(
        "SELECT r.icao24 AS icao24, SUM(r.count) AS uses, "
        "       (SELECT o.callsign FROM operations o "
        "         WHERE o.icao24 = r.icao24 AND o.callsign IS NOT NULL "
        "         ORDER BY o.timestamp DESC LIMIT 1) AS callsign, "
        "       (SELECT reg.registrant_name FROM aircraft_registry reg "
        "         WHERE reg.icao_hex = upper(r.icao24) LIMIT 1) AS registrant_name "
        "FROM daily_operation_rollup r "
        f"WHERE r.icao=? AND r.date_local BETWEEN ? AND ? "
        f"  AND r.event_type IN ({','.join('?' * len(RUNWAY_USE_TYPES))}) "
        "GROUP BY r.icao24",
        (icao, start_day, end_day, *RUNWAY_USE_TYPES),
    ).fetchall()

    localities = homebase.locality_map(conn, icao)

    groups: dict[str, dict] = {}
    for row in rows:
        owner = infer_owner_type(row["registrant_name"])
        nameable = owner.owner_type in NAMEABLE_OWNER_TYPES and row["registrant_name"]
        key = row["registrant_name"] if nameable else PRIVATE_BUCKET

        group = groups.setdefault(key, {
            "operator": key,
            "owner_type": owner.owner_type if nameable else "private",
            "runway_uses": 0,
            "aircraft": [],
            "_localities": [],
        })
        group["runway_uses"] += row["uses"]
        group["aircraft"].append({
            "tail": resolve_display_tail(row["callsign"], None, row["icao24"]),
            "runway_uses": row["uses"],
        })
        entry = localities.get(row["icao24"])
        if entry:
            group["_localities"].append(entry)

    out = []
    for group in groups.values():
        locality, evidence = _dominant_locality(group.pop("_localities"))
        group["aircraft_count"] = len(group["aircraft"])
        group["aircraft"].sort(key=lambda a: a["runway_uses"], reverse=True)
        group["locality"] = locality
        group["locality_evidence"] = evidence
        out.append(group)

    out.sort(key=lambda g: g["runway_uses"], reverse=True)
    return out[:limit]


def _dominant_locality(entries: list[dict]) -> tuple[str, list[dict]]:
    """An operator's locality is its aircraft's, but only when they AGREE.

    A fleet split between local and non-local aircraft gets `unclassified`, not a
    majority vote — because the operator ledger names businesses, and a split fleet
    is precisely the case where a confident badge would be wrong.
    """
    if not entries:
        return homebase.UNCLASSIFIED, []

    decided = [e for e in entries if e["locality"] != homebase.UNCLASSIFIED]
    if not decided:
        return homebase.UNCLASSIFIED, entries[0]["evidence"]

    localities = {e["locality"] for e in decided}
    if len(localities) > 1:
        return homebase.UNCLASSIFIED, []

    best = max(decided, key=lambda e: e["confidence"])
    return best["locality"], best["evidence"]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && pytest tests/test_ledger_operators.py -v`
Expected: 8 passed

- [ ] **Step 5: Commit**

```bash
git add backend/app/ledger.py backend/tests/test_ledger_operators.py
git commit -m "feat(ledger): operator-level usage ledger; individuals are never named

An N-number resolves to an owner's name and home address in one registry lookup.
A public per-aircraft money leaderboard aimed at press is a harassment vector
pointed at individual pilots. It is also the weaker story: a named individual
generates sympathy for the target, a named business generates outrage at it.

Organizations are named. llc and trust are NOT nameable — a single-member LLC is
the standard way an individual holds a personal aircraft, and naming it names a
person. A split fleet gets 'unclassified', not a majority vote."
```

---

## Task 6: `GET /airports/{icao}/ledger`

The endpoint. Serves the summary, the 30-day series, the operator ledger, and the
methodology block — and refuses to serve at all if the configured data sources are not
redistributable.

**Files:**
- Modify: `backend/app/ledger.py` (add `methodology()` and `build_ledger()`)
- Modify: `backend/app/main.py` (add the route; extend CORS origins)
- Create: `backend/tests/test_ledger_endpoint.py`

**Interfaces:**
- Consumes: everything above; `licensing.ledger_publishable(settings)`; `db.get_airport(conn, icao)` (`db.py:1047`)
- Produces:
  - `ledger.methodology(conn, icao, settings) -> dict`
  - `ledger.build_ledger(conn, icao, settings, now_ts, days=30) -> dict`
  - `GET /airports/{icao}/ledger?days=30` → 200 | 404 (unknown airport) | 503 (not publishable)

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_ledger_endpoint.py`:

```python
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import db, ledger
from app.main import app, settings_dep
from app.settings import Settings


BASE = 1780336800   # 2026-06-01 12:00 America/Denver
DAY = 86400


@pytest.fixture
def client(tmp_path):
    path = str(tmp_path / "t.sqlite3")
    conn = db.connect(path)
    conn.executescript(db.SCHEMA)
    db.seed_db(conn)

    conn.execute(
        "INSERT INTO aircraft_registry (n_number, icao_hex, registrant_name, "
        "  registrant_city, registrant_state) VALUES ('N111AA','AAA111',"
        "  'BOULDER FLIGHT SCHOOL LLC','Boulder','CO')"
    )
    for i in range(6):
        db.upsert_operation(conn, db.operation_from_event({
            "id": f"g{i}", "type": "touch_and_go", "icao24": "aaa111",
            "callsign": "N111AA", "timestamp": BASE + i * 60, "airport_icao": "KLMO",
        }))
    db.upsert_operation(conn, db.operation_from_event({
        "id": "t1", "type": "takeoff", "icao24": "aaa111", "callsign": "N111AA",
        "timestamp": BASE + 600, "airport_icao": "KLMO",
    }))
    ledger.rebuild_rollup(conn, "KLMO", BASE - 40 * DAY, BASE + DAY)
    conn.commit()
    conn.close()

    def _settings():
        return Settings(database_path=path, live_source_priority="adsb_lol,self_hosted")

    app.dependency_overrides[settings_dep] = _settings
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def test_ledger_returns_runway_uses_excluding_takeoffs(client):
    body = client.get(f"/airports/KLMO/ledger?days=30&_now={BASE + DAY}").json()
    assert body["summary"]["runway_uses"] == 6      # 6 t&g; the takeoff does not count
    assert body["summary"]["unique_aircraft"] == 1


def test_ledger_daily_series_is_gap_filled_to_the_requested_length(client):
    body = client.get(f"/airports/KLMO/ledger?days=30&_now={BASE + DAY}").json()
    assert len(body["daily"]) == 30
    assert sum(d["runway_uses"] for d in body["daily"]) == 6


def test_ledger_names_the_flight_school(client):
    body = client.get(f"/airports/KLMO/ledger?days=30&_now={BASE + DAY}").json()
    assert body["operators"][0]["operator"] == "BOULDER FLIGHT SCHOOL LLC"
    assert body["operators"][0]["runway_uses"] == 6


def test_methodology_is_served_by_the_api_not_the_frontend(client):
    # The site's published caveats must never drift from the code that produced the
    # numbers, so they ship WITH the numbers.
    body = client.get(f"/airports/KLMO/ledger?days=30&_now={BASE + DAY}").json()
    method = body["methodology"]
    assert method["billable_unit"] == "runway_use"
    assert "floor" in method["floor_disclaimer"].lower()
    assert set(method["definitions"]) == set(ledger.RUNWAY_USE_TYPES)
    assert "does not confirm" in method["definitions"]["touch_and_go"].lower()
    assert method["attribution"]["adsb_lol"]["license"].startswith("ODbL")


def test_methodology_reports_unclassified_count(client):
    body = client.get(f"/airports/KLMO/ledger?days=30&_now={BASE + DAY}").json()
    assert "unclassified_aircraft" in body["summary"]


def test_unknown_airport_is_404(client):
    assert client.get("/airports/ZZZZ/ledger").status_code == 404


def test_ledger_refuses_to_serve_from_non_redistributable_sources(tmp_path):
    # The publish gate. If a restricted feed is configured, the ledger cannot be
    # served — the site would be republishing data it has no right to.
    path = str(tmp_path / "t.sqlite3")
    db.init_db(path)

    def _settings():
        return Settings(database_path=path, live_source_priority="adsbx,adsb_lol")

    app.dependency_overrides[settings_dep] = _settings
    with TestClient(app) as c:
        response = c.get("/airports/KLMO/ledger")
    app.dependency_overrides.clear()

    assert response.status_code == 503
    assert "adsbx" in response.json()["detail"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && pytest tests/test_ledger_endpoint.py -v`
Expected: FAIL — 404, the route does not exist

- [ ] **Step 3: Add `methodology()` and `build_ledger()` to `backend/app/ledger.py`**

Add `dwell` and `licensing` to the module's existing top-level `from . import ...` line, then
append:

```python
def methodology(conn: sqlite3.Connection, icao: str, settings) -> dict:
    """The site's factual claims about its own method, served WITH the numbers.

    This ships from the API rather than the front end on purpose: a caveat that
    lives in the UI can drift from the code that produced the figure it qualifies.
    Here, they cannot.
    """
    icao = icao.upper()
    data_since = conn.execute(
        "SELECT MIN(timestamp) AS t FROM operations WHERE icao=?", (icao,)
    ).fetchone()["t"]

    return {
        "billable_unit": "runway_use",
        "billable_unit_label": "runway uses",
        "billable_unit_description": (
            "A runway use is one arrival at the runway: a landing, a touch-and-go, or a "
            "low approach. It is not an FAA 'operation' — an operation is a takeoff OR a "
            "landing, so a touch-and-go counts as two, and multiplying a fee by an "
            "operations count would double-count every one of them."
        ),
        "definitions": dict(RUNWAY_USE_DEFINITIONS),
        "floor_disclaimer": FLOOR_DISCLAIMER,
        "data_since": data_since,
        "locality_confidence_threshold": homebase.CONFIDENCE_THRESHOLD,
        "locality_lookback_days": homebase.DEFAULT_LOOKBACK_DAYS,
        "attribution": {
            source: licensing.ATTRIBUTION[source]
            for source in settings.live_source_priority_list()
            if source in licensing.ATTRIBUTION
        },
    }


def build_ledger(
    conn: sqlite3.Connection, icao: str, settings, now_ts: int, days: int = 30
) -> dict:
    icao = icao.upper()
    tz = db.airport_timezone(conn, icao)
    start_ts = int(now_ts) - days * 86400
    start_day = db.local_day_key(start_ts + 86400, tz)  # inclusive window of `days` days
    end_day = db.local_day_key(int(now_ts), tz)

    totals = rollup_totals(conn, icao, start_day, end_day)
    localities = homebase.locality_map(conn, icao)

    seen = conn.execute(
        "SELECT DISTINCT icao24 FROM daily_operation_rollup "
        f"WHERE icao=? AND date_local BETWEEN ? AND ? "
        f"  AND event_type IN ({','.join('?' * len(RUNWAY_USE_TYPES))})",
        (icao, start_day, end_day, *RUNWAY_USE_TYPES),
    ).fetchall()
    counts = {homebase.LOCAL: 0, homebase.NON_LOCAL: 0, homebase.UNCLASSIFIED: 0}
    for row in seen:
        entry = localities.get(row["icao24"])
        counts[entry["locality"] if entry else homebase.UNCLASSIFIED] += 1

    return {
        "airport_icao": icao,
        "timezone": tz,
        "window": {"days": days, "start_day": start_day, "end_day": end_day},
        "summary": {
            "runway_uses": totals["runway_uses"],
            "by_type": totals["by_type"],
            "unique_aircraft": totals["unique_aircraft"],
            "local_aircraft": counts[homebase.LOCAL],
            "non_local_aircraft": counts[homebase.NON_LOCAL],
            "unclassified_aircraft": counts[homebase.UNCLASSIFIED],
            "dwell": dwell.dwell_summary(conn, icao, start_ts, int(now_ts)),
        },
        "daily": rollup_daily_runway_uses(conn, icao, start_day, end_day),
        "operators": operator_ledger(conn, icao, start_day, end_day),
        "methodology": methodology(conn, icao, settings),
    }
```

- [ ] **Step 4: Add the route to `backend/app/main.py`**

Add `ledger` and `licensing` to the app imports on line 31:

```python
from . import db, ledger, licensing, patterns, track_history, pattern_circuits, vnap
```

Then add the route immediately after `get_airport_operations_trends` (which ends at
`main.py:1463`):

```python
@app.get("/airports/{icao}/ledger")
async def get_airport_ledger(
    icao: str,
    settings: Annotated[Settings, Depends(settings_dep)],
    days: Annotated[int, Query(ge=1, le=365)] = 30,
    _now: int | None = None,
):
    # The publish gate. The ledger republishes aggregates DERIVED from live ADS-B
    # data; if any configured source forbids that, we do not get to serve it.
    # Failing loudly here beats a takedown after the press hit.
    blocked = licensing.non_redistributable_sources(settings)
    if blocked:
        raise HTTPException(
            status_code=503,
            detail=(
                "Ledger unavailable: live sources are not cleared for republication: "
                + "; ".join(licensing.restriction_reason(source) for source in blocked)
            ),
        )

    now = _now if _now is not None else int(time.time())
    with db_session(settings.database_path) as conn:
        if db.get_airport(conn, icao) is None:
            raise HTTPException(status_code=404, detail="airport not found")
        return ledger.build_ledger(conn, icao, settings, now_ts=now, days=days)
```

Finally, extend the CORS origins at `main.py:57` so the new front end can call the API in
development:

```python
    allow_origins=[
        "http://localhost:5173", "http://127.0.0.1:5173",   # circlejerks frontend
        "http://localhost:5174", "http://127.0.0.1:5174",   # lostlanding frontend
    ],
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd backend && pytest tests/test_ledger_endpoint.py -v`
Expected: 7 passed

If `TestClient` is unavailable, install it: `pip install httpx` (already a dependency) —
`fastapi.testclient.TestClient` requires it.

- [ ] **Step 6: Run the full suite**

Run: `cd backend && pytest tests/ -v`
Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add backend/app/ledger.py backend/app/main.py backend/tests/test_ledger_endpoint.py
git commit -m "feat(api): GET /airports/{icao}/ledger with a fail-closed publish gate

Serves runway uses, the 30-day series, the operator ledger, and a methodology
block — or 503s if the configured live sources are not cleared for republication.

The methodology block ships from the API, not the front end, so the site's
published caveats cannot drift from the code that produced the numbers they
qualify."
```

---

## Task 7: Nightly recompute + historical backfill

The rollup and the classifier are useless if nothing keeps them fresh, and the ledger is
empty until history is rolled up once.

**Files:**
- Modify: `backend/app/worker.py`
- Create: `backend/scripts/backfill_ledger.py`
- Create: `backend/tests/test_ledger_worker.py`

**Interfaces:**
- Consumes: `ledger.rebuild_rollup`; `homebase.recompute_airport`; `db.db_session`
- Produces:
  - `worker.ledger_tick(settings, now_ts) -> dict` — `{"rollup_rows": int, "homebase_rows": int}`
  - `worker._ledger_loop(store, settings)` — added to `run_forever()`'s task set

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_ledger_worker.py`:

```python
from __future__ import annotations

from app import db, ledger, worker
from app.settings import Settings

BASE = 1780336800
DAY = 86400


def test_ledger_tick_rolls_up_and_classifies(tmp_path):
    path = str(tmp_path / "t.sqlite3")
    db.init_db(path)
    conn = db.connect(path)
    for i in range(3):
        db.upsert_operation(conn, db.operation_from_event({
            "id": f"g{i}", "type": "touch_and_go", "icao24": "aaa111",
            "callsign": "N111AA", "timestamp": BASE + i * 60, "airport_icao": "KLMO",
        }))
    conn.commit()
    conn.close()

    settings = Settings(database_path=path)
    result = worker.ledger_tick(settings, now_ts=BASE + DAY)

    assert result["rollup_rows"] >= 1
    assert result["homebase_rows"] == 1

    conn = db.connect(path)
    assert ledger.rollup_totals(conn, "KLMO", "2026-06-01", "2026-06-01")["runway_uses"] == 3
    assert conn.execute(
        "SELECT COUNT(*) AS n FROM aircraft_home_base WHERE icao='KLMO'"
    ).fetchone()["n"] == 1
    conn.close()


def test_ledger_tick_is_idempotent(tmp_path):
    path = str(tmp_path / "t.sqlite3")
    db.init_db(path)
    conn = db.connect(path)
    db.upsert_operation(conn, db.operation_from_event({
        "id": "g1", "type": "touch_and_go", "icao24": "aaa111", "callsign": "N111AA",
        "timestamp": BASE, "airport_icao": "KLMO",
    }))
    conn.commit()
    conn.close()

    settings = Settings(database_path=path)
    worker.ledger_tick(settings, now_ts=BASE + DAY)
    worker.ledger_tick(settings, now_ts=BASE + DAY)

    conn = db.connect(path)
    assert ledger.rollup_totals(conn, "KLMO", "2026-06-01", "2026-06-01")["runway_uses"] == 1
    conn.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && pytest tests/test_ledger_worker.py -v`
Expected: FAIL — `AttributeError: module 'app.worker' has no attribute 'ledger_tick'`

- [ ] **Step 3: Add the tick and loop to `backend/app/worker.py`**

Add to the imports at the top (after `from . import db`):

```python
from . import db, homebase, ledger
```

Then add, after `_detect_tick` (which ends at `worker.py:165`):

```python
# The rollup only needs the recent past kept warm — a detector pass can only add
# events to the last few hours — but locality is judged over a long window.
LEDGER_ROLLUP_WINDOW_DAYS = 3
LEDGER_INTERVAL_SECONDS = 6 * 3600


def ledger_tick(settings, now_ts: int) -> dict:
    """Refresh the ledger's derived tables for every airport we have events for.

    Rollup: rebuild only the last few local days. A detector pass can only add
    events near the present, and a full rebuild over unbounded history on every
    tick would be pointless work.

    Locality: recomputed in full, because it is judged over a 180-day window and
    one new overnight stay can legitimately flip an aircraft from non-local to
    local. Getting that wrong on a named business is exactly the failure this
    project cannot afford.
    """
    rollup_rows = 0
    homebase_rows = 0
    start_ts = int(now_ts) - LEDGER_ROLLUP_WINDOW_DAYS * 86400

    with db_session(settings.database_path) as conn:
        airports = [
            row["icao"]
            for row in conn.execute(
                "SELECT DISTINCT icao FROM operations WHERE icao IS NOT NULL"
            ).fetchall()
        ]
        for icao in airports:
            rollup_rows += ledger.rebuild_rollup(conn, icao, start_ts, int(now_ts))
            homebase_rows += homebase.recompute_airport(conn, icao, now_ts=int(now_ts))
            conn.commit()

    return {"rollup_rows": rollup_rows, "homebase_rows": homebase_rows}


async def _ledger_loop(store, settings) -> None:
    while True:
        started = time.monotonic()
        try:
            result = await asyncio.to_thread(ledger_tick, settings, int(time.time()))
            await _record_heartbeat(store, settings, "ledger", ok=True, started=started)
            logger.info(
                "ledger tick rollup_rows=%s homebase_rows=%s",
                result["rollup_rows"], result["homebase_rows"],
            )
        except Exception as exc:
            await _record_heartbeat(
                store, settings, "ledger", ok=False, started=started, error=repr(exc)[:200]
            )
            logger.exception("ledger tick failed")
        await asyncio.sleep(LEDGER_INTERVAL_SECONDS)
```

`ledger_tick` runs in a thread because it is synchronous SQLite work that can take
seconds over a long history; running it inline on the event loop would stall live
position ingestion, which is exactly the failure `_ingest_tick`'s docstring warns about.

Now wire it into `run_forever()`. Change the task creation block (`worker.py:257-258`):

```python
    ingest_task = asyncio.create_task(_ingest_loop(store, settings, live_sources))
    detect_task = asyncio.create_task(_detect_loop(store, settings))
    ledger_task = asyncio.create_task(_ledger_loop(store, settings))
```

and the two lines that follow (`worker.py:260-263`):

```python
    try:
        await asyncio.gather(ingest_task, detect_task, ledger_task)
    finally:
        for task in (archive_task, ingest_task, detect_task, ledger_task):
            task.cancel()
        for task in (archive_task, ingest_task, detect_task, ledger_task):
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && pytest tests/test_ledger_worker.py -v`
Expected: 2 passed

- [ ] **Step 5: Write the historical backfill script**

Create `backend/scripts/backfill_ledger.py`:

```python
"""One-shot backfill of the ledger's derived tables over all history.

The nightly worker tick only keeps the last few days warm. Run this ONCE after
deploying the ledger, and again after any maintenance script that prunes rows from
`operations`.

    python -m scripts.backfill_ledger              # all airports
    python -m scripts.backfill_ledger KLMO         # one airport
"""
from __future__ import annotations

import sys
import time

from app import db, homebase, ledger
from app.db import db_session
from app.settings import get_settings


def main() -> None:
    settings = get_settings()
    db.init_db(settings.database_path)
    now = int(time.time())
    wanted = [a.upper() for a in sys.argv[1:]]

    with db_session(settings.database_path) as conn:
        airports = [
            row["icao"]
            for row in conn.execute(
                "SELECT DISTINCT icao FROM operations WHERE icao IS NOT NULL"
            ).fetchall()
            if not wanted or row["icao"] in wanted
        ]

        for icao in airports:
            earliest = conn.execute(
                "SELECT MIN(timestamp) AS t FROM operations WHERE icao=?", (icao,)
            ).fetchone()["t"]
            if earliest is None:
                continue

            rollup_rows = ledger.rebuild_rollup(conn, icao, earliest, now)
            conn.commit()
            homebase_rows = homebase.recompute_airport(conn, icao, now_ts=now)
            conn.commit()

            print(
                f"{icao}: {rollup_rows} rollup rows, {homebase_rows} aircraft classified, "
                f"history since {time.strftime('%Y-%m-%d', time.gmtime(earliest))}"
            )


if __name__ == "__main__":
    main()
```

- [ ] **Step 6: Run the backfill against the local seed database**

Run: `cd backend && python -m scripts.backfill_ledger KLMO`
Expected: it runs without error. The local seed database is empty, so it prints nothing —
that is correct. On production it will print real counts.

- [ ] **Step 7: Run the full suite**

Run: `cd backend && pytest tests/ -v`
Expected: all pass.

- [ ] **Step 8: Commit**

```bash
git add backend/app/worker.py backend/scripts/backfill_ledger.py backend/tests/test_ledger_worker.py
git commit -m "feat(worker): nightly ledger recompute + one-shot historical backfill

Rollup rebuilds only the recent local days (a detector pass can only add events
near the present). Locality is recomputed in full: it is judged over 180 days and
one new overnight stay can legitimately flip an aircraft, and getting that wrong
on a named business is the failure this project cannot afford.

Runs in a thread — synchronous SQLite work over a long history would otherwise
stall live position ingestion."
```

---

## Deployment note (not a task; do it when shipping)

`docker-compose.prod.yml` needs no new service — `_ledger_loop` runs inside the existing
`worker`. But after the first deploy:

1. SSH to the droplet, then run the backfill inside the worker container:
   `docker compose exec worker python -m scripts.backfill_ledger`
2. Confirm `CIRCLEJERK_LIVE_SOURCE_PRIORITY` in the production `.env` does **not** contain
   `adsbx` — if it does, the ledger will correctly 503, and the fix is to change the env
   var, not the gate.
3. Verify: `curl -s https://circlejerks.live/api/airports/KLMO/ledger | jq '.summary'`

---

## Self-review against the spec

| Spec section | Task |
|---|---|
| §2.1 Revenue diversion (pavilion) | Front-end plan — no backend surface. |
| §2.2 Operations double-counting | Task 1 (`RUNWAY_USE_TYPES` excludes `takeoff`) |
| §3 GA 25 / GA 22 legal frame | Front-end plan — site copy. |
| §4 "We counted" / floor framing | Task 1 (`FLOOR_DISCLAIMER`), Task 6 (served in `methodology`) |
| §5 Billable unit = runway uses | Tasks 1, 2, 5, 6 |
| §6 Architecture (shared backend) | Task 6 (endpoint + CORS for the second origin) |
| §7.1 `daily_operation_rollup` | Task 2 |
| §7.2 `homebase.py` + evidence | Task 4 |
| §7.3 `dwell.py` | Task 3 |
| §7.4 `/ledger` endpoint + methodology | Task 6 |
| §8 Operator ledger, no owner names | Task 5 |
| §9 / §9.1 Front-end content, peer benchmark | Front-end plan. |
| §10 Phasing (v1 counting story) | This plan is v1's backend in full. |
| §11 Licensing launch gate | Task 0 |
| §12 Build order | Tasks 0 → 7 |

**Deferred to the front-end plan** (spec §9, §9.1, §2.1, §3): the Art Deco port, the
calculator, the peer fee benchmark, the "What the law allows" section, the "How we
counted" section, and deleting the pavilion. None of these have a backend surface. That
plan gets written once this endpoint is real, so it can be written against an actual
response body rather than a guess at one.
