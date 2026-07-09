# VNAP Phase 4 — Live Sidebar Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Enrich the live "Worst Offenders" sidebar (main page `/`) with owner class, aircraft type, and a flight-school flag — resolved via the same community-override logic as the dashboard — and show the derived detail on hover.

**Architecture:** `enrich_offenders` (scan hot path) gains a batched registry lookup + community-override resolution (no per-row query, per the module's hard-won perf rules). The `Offender` type + `OffenderTable` render an owner-class chip and a hover card (type · owner class + source · flight-school). One column layout change on the sidebar.

**Tech Stack:** Python (FastAPI, sqlite3) + React/TS.

## Global Constraints

- Backend tests from `/Users/d/Code/FAA_circle_jerk/backend` via `.venv/bin/python -m pytest` (bare `python` NOT on PATH). Baseline **245**. Frontend: `npx tsc --noEmit && npm run build && npm test` (baseline 28).
- **Perf rule (enforced in `enrich_offenders`):** every SQLite read is ONE upfront batched query before the `asyncio.gather` — never a per-offender `conn.execute`. The new registry + override lookups MUST follow this.
- Resolution: `owner_class` = community override if present (`owner_source="community"`), else registry `owner_type` (`"inferred"`), else `"unknown"`. Reuse `db.current_owner_overrides` (Phase 2). `is_flight_school = owner_class == "flight_school"`.
- The registry lookup is a **dict keyed by icao24** (last-write-wins) — a duplicate `icao_hex` cannot inflate a count here (it's a lookup, not an aggregate), so a batched `WHERE icao_hex IN (...)` is safe.
- Additive/behavior-preserving on the existing offender fields; only NEW keys added.

## COLUMN LAYOUT DECISION (confirm before Task 2)

The current sidebar columns (`App.tsx:1156`) are: **Callsign · Origin · Score · Cir · TG · Dev · Pass · Avg over you** (8). This plan's default: **drop `Pass`, add `Owner`** (keeps 8 columns, minimal grid change), put the owner-class chip in the new `Owner` column, and show **Type · Owner (source) · Flight-school** in a hover card on the row. If the user prefers a different column swap, adjust `COLUMNS`/`grid-template-columns` in Task 2 accordingly — the backend (Task 1) is layout-independent.

## File Structure

- Modify: `backend/app/services.py` (`enrich_offenders`) — batched registry + override lookup.
- Modify: `frontend/src/lib/api.ts` (`Offender` interface) — new optional fields.
- Modify: `frontend/src/App.tsx` (`OffenderTable`) — Owner chip + hover card + column swap.
- Modify: `frontend/src/styles.css` — grid template + chip/hover styles.
- Create/Modify: `backend/tests/test_offender_enrichment.py` — enrichment unit test.

---

### Task 1: Enrich offenders with owner class + type (backend)

**Files:**
- Modify: `backend/app/services.py` (`enrich_offenders`, ~L995–1053)
- Modify: `frontend/src/lib/api.ts` (`Offender`)
- Test: `backend/tests/test_offender_enrichment.py` (create)

**Interfaces:**
- Consumes: `db.current_owner_overrides`.
- Produces: each enriched offender gains `owner_class: str`, `owner_source: "community"|"inferred"`, `aircraft_type: str|null`, `is_flight_school: bool`.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_offender_enrichment.py`. `enrich_offenders` is async and needs a Store/Settings/params; to keep the test hermetic and focused on the NEW batched lookup, test the pure resolution the same way the code will compute it — but exercise the real function via a minimal in-memory store. If wiring the full async function is heavy, instead extract the resolution into a small pure helper and test that. The plan uses the pure-helper approach for testability:

Add a pure helper to `services.py` (near `enrich_offenders`):

```python
def resolve_offender_owner(icao24: str, owner_by_icao: dict[str, str],
                           overrides: dict[str, str]) -> dict:
    override = overrides.get(icao24)
    inferred = owner_by_icao.get(icao24, "unknown")
    owner_class = override or inferred
    return {
        "owner_class": owner_class,
        "owner_source": "community" if override else "inferred",
        "is_flight_school": owner_class == "flight_school",
    }
```

Test it:

```python
from app.services import resolve_offender_owner


def test_resolve_offender_owner_override_wins():
    r = resolve_offender_owner("aa11", {"aa11": "individual"}, {"aa11": "flight_school"})
    assert r == {"owner_class": "flight_school", "owner_source": "community", "is_flight_school": True}


def test_resolve_offender_owner_inferred():
    r = resolve_offender_owner("bb22", {"bb22": "llc"}, {})
    assert r == {"owner_class": "llc", "owner_source": "inferred", "is_flight_school": False}


def test_resolve_offender_owner_unknown():
    r = resolve_offender_owner("cc33", {}, {})
    assert r == {"owner_class": "unknown", "owner_source": "inferred", "is_flight_school": False}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/d/Code/FAA_circle_jerk/backend && .venv/bin/python -m pytest tests/test_offender_enrichment.py -v`
Expected: FAIL (`resolve_offender_owner` undefined).

- [ ] **Step 3: Add the helper + batched lookups + wire into enrich_one**

Add `resolve_offender_owner` (above) to `services.py`. Then in `enrich_offenders`, after the existing deviation/cowboy batch (~L1026), add a batched registry lookup + overrides fetch (ONE query, before the gather):

```python
    # Owner class + aircraft type, batched (perf rule: one query, never per-offender).
    owner_by_icao: dict[str, str] = {}
    model_by_icao: dict[str, str] = {}
    if icao24s:
        up = [i.upper() for i in icao24s]
        ph = ",".join("?" * len(up))
        for r in conn.execute(
            f"SELECT lower(icao_hex) AS icao24, owner_type, model FROM aircraft_registry "
            f"WHERE icao_hex IN ({ph})",
            up,
        ).fetchall():
            if r["owner_type"]:
                owner_by_icao[r["icao24"]] = r["owner_type"]
            if r["model"]:
                model_by_icao[r["icao24"]] = r["model"]
    overrides = db.current_owner_overrides(conn)
```

Then in `enrich_one`'s returned dict (~L1046-1053), add the new keys:

```python
            return {
                **offender,
                "report_count": report_counts.get(offender["icao24"], 0),
                "deviation_mean_nm": deviation_by_icao.get(offender["icao24"]),
                "is_cowboy": offender["icao24"] in cowboy_set,
                **resolve_offender_owner(offender["icao24"], owner_by_icao, overrides),
                "aircraft_type": model_by_icao.get(offender["icao24"]),
                **altitude_over_user_summary(track, airport, params, window),
                **origin,
            }
```

Confirm `db` is imported in `services.py` (it is — `from . import db` / `db` is used elsewhere; verify and reuse).

- [ ] **Step 4: Add the fields to the `Offender` TS type**

In `frontend/src/lib/api.ts`, add to the `Offender` interface (after `runway_breakdown`):

```typescript
  /** Resolved owner class: community override if present, else registry-inferred. */
  owner_class?: string;
  owner_source?: string;
  aircraft_type?: string | null;
  is_flight_school?: boolean;
```

- [ ] **Step 5: Run test + typecheck**

Run: `cd /Users/d/Code/FAA_circle_jerk/backend && .venv/bin/python -m pytest tests/test_offender_enrichment.py -v`
Expected: PASS.
Run: `cd /Users/d/Code/FAA_circle_jerk/frontend && npx tsc --noEmit`
Expected: clean.

- [ ] **Step 6: Full backend suite + commit**

Run: `cd /Users/d/Code/FAA_circle_jerk/backend && .venv/bin/python -m pytest -q`
Expected: PASS (≥ 245 + 3).

```bash
git add backend/app/services.py frontend/src/lib/api.ts backend/tests/test_offender_enrichment.py
git commit -m "feat(scan): enrich offenders with owner class + aircraft type"
```

---

### Task 2: OffenderTable — owner chip + hover card + column swap (frontend)

**Files:**
- Modify: `frontend/src/App.tsx` (`OffenderTable`, L1145-1186)
- Modify: `frontend/src/styles.css` (`.table-row` grid + chip/hover)

**Interfaces:**
- Consumes: the new `Offender.owner_class`/`owner_source`/`aircraft_type`/`is_flight_school`.

**NOTE:** implement the CONFIRMED column layout (default: drop `Pass`, add `Owner`). Steps below assume that default.

- [ ] **Step 1: Add owner label map + hover-title helper near OffenderTable**

Before `OffenderTable` in `App.tsx`, add:

```tsx
const OFFENDER_OWNER_LABELS: Record<string, string> = {
  individual: "Individual", llc: "LLC", corporation: "Corp", government: "Gov",
  flight_school: "Flight school", university: "University", club: "Club",
  trust: "Trust", unknown: "Unknown",
};

function offenderHoverTitle(row: Offender): string {
  const parts = [
    `Type: ${row.aircraft_type ?? "unknown"}`,
    `Owner: ${OFFENDER_OWNER_LABELS[row.owner_class ?? "unknown"] ?? row.owner_class}${row.owner_source === "community" ? " (community)" : ""}`,
    `Flight school: ${row.is_flight_school ? "yes" : "no"}`,
  ];
  return parts.join(" · ");
}
```

- [ ] **Step 2: Update the header + rows (drop Pass, add Owner; add hover title)**

Change the header span row (`App.tsx:1156`) to:

```tsx
          <span>Callsign</span><span>Origin</span><span>Owner</span><span>Score</span><span>Cir</span><span>TG</span><span>Dev</span><span>Avg over you</span>
```

Add `title={offenderHoverTitle(row)}` to the `<button className="table-row ...">` (so hovering the row shows type · owner · flight-school), and replace the cell list so the columns match the header — insert the Owner chip after Origin and remove the `Pass` cell:

```tsx
          <button
            key={row.icao24}
            className={`table-row ${selected?.icao24 === row.icao24 ? "selected" : ""}`}
            onClick={() => onSelect(row)}
            title={offenderHoverTitle(row)}
          >
            <span>
              <strong>{row.callsign}{row.is_cowboy ? " 🤠" : ""}</strong>
              <small>{row.icao24}{reportCounts[row.icao24] ? ` | reported ${reportCounts[row.icao24]}x` : ""}</small>
            </span>
            <span>{originDisplay(row).label}<small>{originDisplay(row).detail}</small></span>
            <span>
              <span className={`owner-chip owner-${row.owner_class ?? "unknown"}`}>
                {OFFENDER_OWNER_LABELS[row.owner_class ?? "unknown"] ?? row.owner_class}
              </span>
              {row.is_flight_school ? <small>✈ school</small> : null}
            </span>
            <span>{row.score}</span>
            <span>{row.circles}</span>
            <span>
              {row.touch_and_gos}
              {row.runway_breakdown && Object.keys(row.runway_breakdown).length > 0 && (
                <small>{runwayBreakdownLabel(row.runway_breakdown)}</small>
              )}
            </span>
            <span>{row.deviation_mean_nm != null ? `${row.deviation_mean_nm} nm` : "—"}</span>
            <span>{numberOrDash(row.avg_altitude_over_user_ft_agl, " ft")}</span>
          </button>
```

(The header and each row now have exactly 8 spans.)

- [ ] **Step 3: Update the grid template + add chip styles**

In `frontend/src/styles.css`, set the offender `.table-row` grid to 8 explicit columns (replace the existing `grid-template-columns` at ~L669):

```css
  grid-template-columns: 1.35fr 1.0fr 0.7fr 0.5fr 0.38fr 0.42fr 0.5fr 0.6fr;
```

Add chip styles (near the other `.table-row` rules):

```css
.owner-chip { display: inline-block; font-size: 10px; font-weight: 700; padding: 1px 6px;
  border-radius: 999px; background: var(--line); color: var(--ink); white-space: nowrap; }
.owner-chip.owner-flight_school { background: #b3231f; color: #fff; }
.owner-chip.owner-llc, .owner-chip.owner-corporation { background: #1b3a6b; color: #fff; }
```

Also check the responsive override at ~L1481-1485 (`.table-row span:nth-last-child(-n + 5)`): confirm the mobile column-hiding still reads sensibly with the new 8-column set; adjust the `-n + N` count if a hidden/shown column boundary shifted. If unsure, leave it — it hides trailing columns on narrow screens, which still degrades gracefully.

- [ ] **Step 4: Typecheck + build + tests + manual**

Run: `cd /Users/d/Code/FAA_circle_jerk/frontend && npx tsc --noEmit && npm run build && npm test`
Expected: clean build; tests pass.
Manual (controller): open `/`, confirm the sidebar shows an Owner chip per row, hovering a row shows "Type · Owner · Flight school", and the flight-school rows show the ✈ marker.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/App.tsx frontend/src/styles.css
git commit -m "feat(ui): worst-offenders sidebar shows owner class + hover card"
```

---

## Self-Review

**Spec coverage (Phase 4):**
- Offender rows enriched with owner class + type + flight-school flag, community-override-aware, batched (no N+1) → Task 1. ✓
- Owner-class visible in the sidebar + hover card showing type · owner class · flight-school → Task 2. ✓
- Column rework ("replace some columns") → Task 2 (drop Pass, add Owner) — CONFIRM layout first. ✓

**Placeholder scan:** the test-strategy prose in Task 1 Step 1 resolves to the pure-helper approach with full code — not a placeholder. The one conditional ("adjust responsive `-n+N` if boundary shifted") points at a concrete check. ✓

**Type consistency:** `owner_class`/`owner_source`/`aircraft_type`/`is_flight_school` identical across `resolve_offender_owner`, `enrich_one`, and the TS `Offender`; `OFFENDER_OWNER_LABELS`/`owner-{class}` classes match the `OwnerType` buckets. ✓

**Deviation from design:** owner detail shown via the app's `title=` hover idiom (not a bespoke popover) + an at-a-glance chip — matches the design's "reuse the `title=` tooltip idiom (or a small popover)". Column swap (drop Pass) keeps the grid at 8 columns to avoid destabilizing the already-terse grid template.
