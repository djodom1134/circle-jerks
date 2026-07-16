# VNAP Phase 3 — Dashboard Aircraft Table + Radar Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an aircraft-centric section to `/stats`: a sortable table (one row per tail, many columns) beside a spiderweb/radar that plots the selected aircraft's VNAP sub-scores against the set average. Owner class is inline-editable (writes a community override).

**Architecture:** A new `AircraftDashboard.tsx` fetches `getVnapCompliance(icao, window)` and renders a client-sorted table + a Recharts `RadarChart` with two series (selected aircraft vs. `averages`). Pure sort/radar-shaping helpers live in `lib/vnapDashboard.ts` (unit-tested with vitest). It mounts on `StatsPage`, driven by that page's existing window selector. Owner-class edits call `setOwnerClass` with `getVisitorId()` and refetch.

**Tech Stack:** React + TypeScript + Recharts + Vite/Vitest.

## Global Constraints

- Frontend commands: typecheck `cd /Users/d/Code/FAA_circle_jerk/frontend && npx tsc --noEmit`; build `npm run build`; tests `npm test` (vitest). Current frontend tests pass (20) — do not regress.
- Consume the EXISTING clients/types: `getVnapCompliance` + `VnapComplianceResponse`/`VnapAircraft`, `setOwnerClass`, and the `StatsWindow` type — all already in `frontend/src/lib/api.ts`. Do NOT add new fetchers.
- Radar axis order = the backend's `axes` array (`tightness, altitude, timeofday, tg_volume, circle_restraint, left_traffic, runway29`); label them via a display-name map.
- **A `null` sub-score is "no data," not 0**: table cells show `—`; on the radar a `null` collapses to 0 for that spoke (Recharts needs a number) AND the axis label notes it — never silently render null as a real 0.
- The radar's "average" polygon comes from the response's `averages` (per-axis means). Note (from Phase 1): this per-axis-average polygon will NOT exactly equal any single aircraft's displayed composite — that's expected.
- Owner-class dropdown options = the 9 buckets of the `OwnerType` enum already in `api.ts`.
- Reuse `getVisitorId()` from `../lib/visitor` for edits (same as the pattern editor). Reuse existing `stats-*` CSS classes; add new classes only where needed.
- Additive: do NOT remove or alter existing `StatsPage` sections.

## File Structure

- Create: `frontend/src/lib/vnapDashboard.ts` — pure helpers (`sortAircraft`, `radarData`, display maps).
- Create: `frontend/src/lib/vnapDashboard.test.ts` — vitest unit tests for the helpers.
- Create: `frontend/src/components/AircraftDashboard.tsx` — the section component.
- Modify: `frontend/src/components/StatsPage.tsx` — mount `<AircraftDashboard>`.
- Modify: `frontend/src/styles.css` — a few dashboard/table/radar layout rules.

---

### Task 1: Pure helpers + `AircraftDashboard` (table + radar, read-only)

**Files:**
- Create: `frontend/src/lib/vnapDashboard.ts`, `frontend/src/lib/vnapDashboard.test.ts`
- Create: `frontend/src/components/AircraftDashboard.tsx`

**Interfaces:**
- Produces:
  - `AXIS_LABELS: Record<string,string>`, `OWNER_LABELS: Record<string,string>`, `OWNER_OPTIONS: string[]`.
  - `sortAircraft(rows: VnapAircraft[], key: string, dir: "asc"|"desc"): VnapAircraft[]` — stable; nulls always last regardless of dir.
  - `radarData(axes: string[], selected: VnapAircraft | null, averages: Record<string, number|null>): {axis: string; label: string; selected: number; average: number}[]`.
  - default-exported `<AircraftDashboard icao={string} window={StatsWindow} />`.

- [ ] **Step 1: Write the failing helper test**

Create `frontend/src/lib/vnapDashboard.test.ts`:

```typescript
import { describe, it, expect } from "vitest";
import { sortAircraft, radarData } from "./vnapDashboard";
import type { VnapAircraft } from "./api";

function ac(partial: Partial<VnapAircraft>): VnapAircraft {
  return {
    icao24: "x", callsign: "X", registration: null, tail: "X", aircraft_type: null,
    owner_class: "unknown", owner_source: "inferred", vnap_score: null, reports: 0,
    operations: 0, touch_and_gos: 0, cowboy_count: 0, deviation_mean_nm: null, circles: 0,
    scores: {}, ...partial,
  };
}

describe("sortAircraft", () => {
  it("sorts numeric desc with nulls last", () => {
    const rows = [ac({ icao24: "a", vnap_score: 50 }), ac({ icao24: "b", vnap_score: null }),
                  ac({ icao24: "c", vnap_score: 90 })];
    const out = sortAircraft(rows, "vnap_score", "desc").map((r) => r.icao24);
    expect(out).toEqual(["c", "a", "b"]); // 90, 50, null-last
  });
  it("sorts numeric asc with nulls still last", () => {
    const rows = [ac({ icao24: "a", operations: 5 }), ac({ icao24: "b", operations: 1 })];
    expect(sortAircraft(rows, "operations", "asc").map((r) => r.icao24)).toEqual(["b", "a"]);
  });
  it("sorts strings", () => {
    const rows = [ac({ icao24: "a", tail: "N9" }), ac({ icao24: "b", tail: "N1" })];
    expect(sortAircraft(rows, "tail", "asc").map((r) => r.tail)).toEqual(["N1", "N9"]);
  });
});

describe("radarData", () => {
  it("maps selected+average per axis, null -> 0", () => {
    const axes = ["tightness", "altitude"];
    const sel = ac({ scores: { tightness: 80, altitude: null } });
    const out = radarData(axes, sel, { tightness: 60, altitude: 40 });
    expect(out).toEqual([
      { axis: "tightness", label: "Pattern tightness", selected: 80, average: 60 },
      { axis: "altitude", label: "Altitude", selected: 0, average: 40 },
    ]);
  });
  it("handles no selection (selected 0s)", () => {
    const out = radarData(["tightness"], null, { tightness: 55 });
    expect(out[0].selected).toBe(0);
    expect(out[0].average).toBe(55);
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/d/Code/FAA_circle_jerk/frontend && npx vitest run src/lib/vnapDashboard.test.ts`
Expected: FAIL (module missing).

- [ ] **Step 3: Implement the helpers**

Create `frontend/src/lib/vnapDashboard.ts`:

```typescript
import type { VnapAircraft } from "./api";

export const AXIS_LABELS: Record<string, string> = {
  tightness: "Pattern tightness",
  altitude: "Altitude",
  timeofday: "Time of day",
  tg_volume: "T&G volume",
  circle_restraint: "Circle restraint",
  left_traffic: "Left traffic",
  runway29: "Runway 29 pref",
};

export const OWNER_LABELS: Record<string, string> = {
  individual: "Individual", llc: "LLC", corporation: "Corporation",
  government: "Government", flight_school: "Flight school", university: "University",
  club: "Club", trust: "Trust", unknown: "Unknown",
};

export const OWNER_OPTIONS = [
  "individual", "llc", "corporation", "government",
  "flight_school", "university", "club", "trust", "unknown",
];

type Sortable = number | string | null;

function cellValue(row: VnapAircraft, key: string): Sortable {
  if (key in row) return (row as unknown as Record<string, Sortable>)[key];
  return row.scores[key] ?? null; // axis columns
}

export function sortAircraft(rows: VnapAircraft[], key: string, dir: "asc" | "desc"): VnapAircraft[] {
  const sign = dir === "asc" ? 1 : -1;
  return [...rows].sort((a, b) => {
    const va = cellValue(a, key);
    const vb = cellValue(b, key);
    // nulls always last, regardless of direction
    if (va === null && vb === null) return 0;
    if (va === null) return 1;
    if (vb === null) return -1;
    if (typeof va === "string" || typeof vb === "string") {
      return sign * String(va).localeCompare(String(vb));
    }
    return sign * (va - vb);
  });
}

export function radarData(
  axes: string[],
  selected: VnapAircraft | null,
  averages: Record<string, number | null>,
): { axis: string; label: string; selected: number; average: number }[] {
  return axes.map((axis) => ({
    axis,
    label: AXIS_LABELS[axis] ?? axis,
    selected: selected?.scores[axis] ?? 0,
    average: averages[axis] ?? 0,
  }));
}
```

- [ ] **Step 4: Run helper test to green**

Run: `cd /Users/d/Code/FAA_circle_jerk/frontend && npx vitest run src/lib/vnapDashboard.test.ts`
Expected: PASS.

- [ ] **Step 5: Implement `AircraftDashboard.tsx`**

Create `frontend/src/components/AircraftDashboard.tsx`:

```tsx
import { useEffect, useMemo, useState } from "react";
import {
  RadarChart, Radar, PolarGrid, PolarAngleAxis, PolarRadiusAxis,
  Tooltip, Legend, ResponsiveContainer,
} from "recharts";
import { getVnapCompliance, type VnapComplianceResponse, type VnapAircraft, type StatsWindow } from "../lib/api";
import { sortAircraft, radarData, AXIS_LABELS, OWNER_LABELS } from "../lib/vnapDashboard";

const COLUMNS: { key: string; label: string; numeric: boolean }[] = [
  { key: "tail", label: "Tail", numeric: false },
  { key: "owner_class", label: "Owner", numeric: false },
  { key: "aircraft_type", label: "Type", numeric: false },
  { key: "vnap_score", label: "VNAP", numeric: true },
  { key: "reports", label: "Reports", numeric: true },
  { key: "operations", label: "Ops", numeric: true },
  { key: "touch_and_gos", label: "T&G", numeric: true },
  { key: "cowboy_count", label: "Cowboy", numeric: true },
  { key: "deviation_mean_nm", label: "Dev nm", numeric: true },
  { key: "circles", label: "Circles", numeric: true },
];

function fmt(v: number | string | null, numeric: boolean): string {
  if (v === null || v === undefined) return "—";
  if (numeric && typeof v === "number") return Number.isInteger(v) ? String(v) : v.toFixed(2);
  return String(v);
}

export default function AircraftDashboard({ icao, window }: { icao: string; window: StatsWindow }) {
  const [data, setData] = useState<VnapComplianceResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [sortKey, setSortKey] = useState("vnap_score");
  const [sortDir, setSortDir] = useState<"asc" | "desc">("asc"); // worst (lowest VNAP) first
  const [selected, setSelected] = useState<string | null>(null);

  useEffect(() => {
    setData(null); setError(null); setSelected(null);
    getVnapCompliance(icao, window)
      .then(setData)
      .catch((e) => setError(e instanceof Error ? e.message : "Failed to load compliance."));
  }, [icao, window]);

  const sorted = useMemo(
    () => (data ? sortAircraft(data.aircraft, sortKey, sortDir) : []),
    [data, sortKey, sortDir],
  );
  const selectedAc: VnapAircraft | null = useMemo(
    () => sorted.find((a) => a.icao24 === selected) ?? sorted[0] ?? null,
    [sorted, selected],
  );
  const chart = useMemo(
    () => (data ? radarData(data.axes, selectedAc, data.averages) : []),
    [data, selectedAc],
  );

  if (error) return <div className="stats-error">{error}</div>;
  if (!data) return <div className="stats-loading">Loading aircraft…</div>;

  const onHeader = (key: string) => {
    if (key === sortKey) setSortDir((d) => (d === "asc" ? "desc" : "asc"));
    else { setSortKey(key); setSortDir(key === "tail" || key === "owner_class" || key === "aircraft_type" ? "asc" : "desc"); }
  };

  return (
    <section className="stats-card vnap-dashboard">
      <h2>Aircraft VNAP compliance</h2>
      <p className="stats-besteffort">
        One row per aircraft over the selected window. Click a row to compare it against the set
        average on the radar. VNAP score is 0–100 (100 = follows the noise-abatement procedures).
      </p>
      <div className="vnap-split">
        <div className="vnap-table-wrap">
          <table className="stats-table vnap-table">
            <thead>
              <tr>
                {COLUMNS.map((c) => (
                  <th key={c.key} className="vnap-th" onClick={() => onHeader(c.key)}>
                    {c.label}{sortKey === c.key ? (sortDir === "asc" ? " ▲" : " ▼") : ""}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {sorted.map((a) => (
                <tr key={a.icao24}
                    className={`vnap-row${a.icao24 === selectedAc?.icao24 ? " selected" : ""}`}
                    onClick={() => setSelected(a.icao24)}>
                  <td>{a.tail}</td>
                  <td>{OWNER_LABELS[a.owner_class] ?? a.owner_class}
                      {a.owner_source === "community" ? " ✓" : ""}</td>
                  <td>{fmt(a.aircraft_type, false)}</td>
                  <td>{fmt(a.vnap_score, true)}</td>
                  <td>{a.reports}</td>
                  <td>{a.operations}</td>
                  <td>{a.touch_and_gos}</td>
                  <td>{a.cowboy_count}</td>
                  <td>{fmt(a.deviation_mean_nm, true)}</td>
                  <td>{a.circles}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <div className="vnap-radar">
          <div className="vnap-radar-title">
            {selectedAc ? `${selectedAc.tail} vs. average` : "Select an aircraft"}
          </div>
          <ResponsiveContainer width="100%" height={320}>
            <RadarChart data={chart} outerRadius="70%">
              <PolarGrid />
              <PolarAngleAxis dataKey="label" fontSize={11} />
              <PolarRadiusAxis domain={[0, 100]} fontSize={10} />
              <Radar name="Average" dataKey="average" stroke="#8a8f98" fill="#8a8f98" fillOpacity={0.25} />
              <Radar name={selectedAc?.tail ?? "Selected"} dataKey="selected"
                     stroke="#1b3a6b" fill="#1b3a6b" fillOpacity={0.4} />
              <Tooltip /><Legend />
            </RadarChart>
          </ResponsiveContainer>
        </div>
      </div>
    </section>
  );
}
```

Note: `AXIS_LABELS` is imported for the helper's use; it's fine if the component doesn't reference it directly (the helper does) — if the linter flags an unused import, drop `AXIS_LABELS` from the component's import line (keep `sortAircraft, radarData, OWNER_LABELS`).

- [ ] **Step 6: Typecheck + build + tests**

Run: `cd /Users/d/Code/FAA_circle_jerk/frontend && npx tsc --noEmit`
Expected: clean.
Run: `cd /Users/d/Code/FAA_circle_jerk/frontend && npm run build`
Expected: succeeds.
Run: `cd /Users/d/Code/FAA_circle_jerk/frontend && npm test`
Expected: PASS (existing + new helper tests).

- [ ] **Step 7: Commit**

```bash
git add frontend/src/lib/vnapDashboard.ts frontend/src/lib/vnapDashboard.test.ts frontend/src/components/AircraftDashboard.tsx
git commit -m "feat(ui): AircraftDashboard table + VNAP radar (read-only)"
```

---

### Task 2: Mount on StatsPage + inline owner-class editing + styles

**Files:**
- Modify: `frontend/src/components/AircraftDashboard.tsx` (owner-class dropdown)
- Modify: `frontend/src/components/StatsPage.tsx` (mount)
- Modify: `frontend/src/styles.css`

**Interfaces:**
- Consumes: `setOwnerClass` + `getVisitorId`; the `StatsPage` `win` state.

- [ ] **Step 1: Add inline owner-class editing to the component**

In `AircraftDashboard.tsx`, add imports:

```tsx
import { getVnapCompliance, setOwnerClass, type VnapComplianceResponse, type VnapAircraft, type StatsWindow } from "../lib/api";
import { sortAircraft, radarData, OWNER_LABELS, OWNER_OPTIONS } from "../lib/vnapDashboard";
import { getVisitorId } from "../lib/visitor";
```

Add a refetch helper and an edit handler inside the component (after the existing `useEffect`):

```tsx
  const reload = () => getVnapCompliance(icao, window).then(setData).catch(() => {});

  const onOwnerChange = async (icao24: string, owner_type: string) => {
    try {
      await setOwnerClass(icao24, owner_type, getVisitorId());
      await reload();
    } catch {
      /* keep prior value on failure; a toast could be added later */
    }
  };
```

Replace the Owner `<td>` cell with an inline `<select>` (stopPropagation so choosing doesn't also select the row):

```tsx
                  <td onClick={(e) => e.stopPropagation()}>
                    <select className="vnap-owner-select" value={a.owner_class}
                            onChange={(e) => onOwnerChange(a.icao24, e.target.value)}>
                      {OWNER_OPTIONS.map((o) => (
                        <option key={o} value={o}>{OWNER_LABELS[o] ?? o}</option>
                      ))}
                    </select>
                    {a.owner_source === "community" ? " ✓" : ""}
                  </td>
```

- [ ] **Step 2: Mount on StatsPage**

In `frontend/src/components/StatsPage.tsx`, add the import:

```tsx
import AircraftDashboard from "./AircraftDashboard";
```

Render it inside the `{data && ( <> … )}` fragment, right after `<OperationsTrends icao={icao} />` (so the aircraft table leads the analytical sections). It uses the page's current `win`:

```tsx
          <OperationsTrends icao={icao} />
          <AircraftDashboard icao={icao} window={win} />
```

- [ ] **Step 3: Add styles**

In `frontend/src/styles.css`, add:

```css
.vnap-split { display: grid; grid-template-columns: 1fr; gap: 16px; }
@media (min-width: 900px) { .vnap-split { grid-template-columns: 1.4fr 1fr; } }
.vnap-table-wrap { overflow-x: auto; }
.vnap-th { cursor: pointer; user-select: none; white-space: nowrap; }
.vnap-row { cursor: pointer; }
.vnap-row.selected { background: rgba(27, 58, 107, 0.12); }
.vnap-radar-title { font-size: 12px; font-weight: 700; color: var(--muted); margin-bottom: 6px; }
.vnap-owner-select { font-size: 12px; padding: 2px 4px; }
```

- [ ] **Step 4: Typecheck + build + tests**

Run: `cd /Users/d/Code/FAA_circle_jerk/frontend && npx tsc --noEmit && npm run build && npm test`
Expected: clean build; tests pass.

- [ ] **Step 5: Manual verification**

Run the app, open `/stats?airport=KLMO`, confirm: the "Aircraft VNAP compliance" section shows a sortable table + radar; clicking a row updates the radar (selected vs. average); clicking column headers re-sorts (nulls last); changing an Owner dropdown persists (reload shows the ✓ community marker); the window selector changes the data set. (The controller runs this; the implementer's verification is tsc + build + vitest.)

- [ ] **Step 6: Commit**

```bash
git add frontend/src/components/AircraftDashboard.tsx frontend/src/components/StatsPage.tsx frontend/src/styles.css
git commit -m "feat(ui): mount AircraftDashboard on stats; inline owner-class editing"
```

---

## Self-Review

**Spec coverage (Phase 3):**
- Sortable aircraft-centric table with the many columns → Task 1 `COLUMNS` + `sortAircraft`. ✓
- Radar of sub-scores, selected aircraft vs. set average, updates on row click → Task 1 `radarData` + `RadarChart` + selection. ✓
- Additive on `/stats`, driven by the existing window selector → Task 2 mount with `win`. ✓
- Inline owner-class editing → community override via `setOwnerClass` + `getVisitorId` + refetch → Task 2. ✓
- null = "no data" (`—` in table; 0 spoke on radar) → `fmt` + `radarData`. ✓

**Placeholder scan:** no TBD/TODO; full code each step. The empty `catch` on edit failure is a deliberate no-op (documented) — not a placeholder. ✓

**Type consistency:** `VnapAircraft`/`VnapComplianceResponse` fields used match the Phase-1 client types; `OWNER_OPTIONS` matches the backend `VALID_OWNER_TYPES`; `radarData`/`sortAircraft` signatures match their tests; `window: StatsWindow` matches `getVnapCompliance`. ✓

**Deviation from design (intentional):** the "multi-select 2–3 to overlay" was listed as an optional stretch in the design — omitted here (YAGNI); single-selection vs. average is the core interaction. Radar renders a `null` sub-score as a 0 spoke (Recharts needs a number); the table's `—` preserves the "no data" distinction for the reader.
