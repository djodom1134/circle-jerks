# Worst-offender → Stats page deep-link highlight — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Clicking a card in the main page's bottom "Worst offenders" leaderboard opens the Stats page and highlights that tail in the "Aircraft VNAP compliance" table (which also drives the "vs. average" radar).

**Architecture:** Routing is pathname-based (no SPA router), so the highlight target rides in the query string. A leaderboard card is a real `<a href="/stats?airport=…&aircraft=…&win=all">`. On the Stats page, `StatsPage` reads the window from the URL and `AircraftDashboard` reads the `aircraft` param, pre-selects that row once data loads, scrolls it into view, and pulses it. Branching logic lives in three pure, unit-tested helpers; the components stay thin.

**Tech Stack:** React + TypeScript (Vite), Recharts, vitest. Frontend dir: `frontend/`.

## Global Constraints

- Frontend only — no backend changes.
- `icao24` is stored/compared lower-case; airport ICAO is upper-case.
- Valid `StatsWindow` values (verbatim): `"1d" | "7d" | "30d" | "all"`; default `"7d"`.
- Deep-link window is `"all"` (the leaderboard is all-time).
- Airport fallback when none is known: `"KBJC"` (matches the header `/stats` link, `App.tsx:501`).
- Existing frontend tests are pure `lib/*.test.ts` (vitest). Only the pure helpers get unit tests; component/DOM changes are verified by `npx tsc --noEmit` + browser.
- Run all commands from `frontend/`.

---

### Task 1: `statsHighlightHref` + `windowFromUrl` helpers

**Files:**
- Create: `frontend/src/lib/statsLinks.ts`
- Test: `frontend/src/lib/statsLinks.test.ts`

**Interfaces:**
- Consumes: `StatsWindow` from `./api`.
- Produces:
  - `statsHighlightHref(airportIcao: string | undefined | null, icao24: string): string`
  - `windowFromUrl(search: string): StatsWindow`

- [ ] **Step 1: Write the failing test**

Create `frontend/src/lib/statsLinks.test.ts`:

```ts
import { describe, it, expect } from "vitest";
import { statsHighlightHref, windowFromUrl } from "./statsLinks";

describe("statsHighlightHref", () => {
  it("builds the deep-link with all-time window", () => {
    expect(statsHighlightHref("KLMO", "a23a01")).toBe(
      "/stats?airport=KLMO&aircraft=a23a01&win=all",
    );
  });
  it("upper-cases the airport and lower-cases the icao24", () => {
    expect(statsHighlightHref("klmo", "A23A01")).toBe(
      "/stats?airport=KLMO&aircraft=a23a01&win=all",
    );
  });
  it("falls back to KBJC when the airport is missing", () => {
    expect(statsHighlightHref(undefined, "a23a01")).toBe(
      "/stats?airport=KBJC&aircraft=a23a01&win=all",
    );
  });
});

describe("windowFromUrl", () => {
  it("returns the win param when valid", () => {
    expect(windowFromUrl("?win=all")).toBe("all");
    expect(windowFromUrl("?airport=KLMO&win=30d")).toBe("30d");
  });
  it("defaults to 7d for missing or invalid win", () => {
    expect(windowFromUrl("")).toBe("7d");
    expect(windowFromUrl("?win=bogus")).toBe("7d");
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npx vitest run src/lib/statsLinks.test.ts`
Expected: FAIL — cannot resolve `./statsLinks`.

- [ ] **Step 3: Write minimal implementation**

Create `frontend/src/lib/statsLinks.ts`:

```ts
import type { StatsWindow } from "./api";

const STATS_WINDOWS: readonly StatsWindow[] = ["1d", "7d", "30d", "all"];
const DEFAULT_STATS_WINDOW: StatsWindow = "7d";
const DEFAULT_AIRPORT = "KBJC";

export function statsHighlightHref(
  airportIcao: string | undefined | null,
  icao24: string,
): string {
  const params = new URLSearchParams({
    airport: (airportIcao || DEFAULT_AIRPORT).toUpperCase(),
    aircraft: icao24.toLowerCase(),
    win: "all",
  });
  return `/stats?${params.toString()}`;
}

export function windowFromUrl(search: string): StatsWindow {
  const win = new URLSearchParams(search).get("win");
  return STATS_WINDOWS.includes(win as StatsWindow)
    ? (win as StatsWindow)
    : DEFAULT_STATS_WINDOW;
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `npx vitest run src/lib/statsLinks.test.ts`
Expected: PASS (5 assertions).

- [ ] **Step 5: Commit**

```bash
git add frontend/src/lib/statsLinks.ts frontend/src/lib/statsLinks.test.ts
git commit -m "feat(stats): statsHighlightHref + windowFromUrl helpers"
```

---

### Task 2: `resolveHighlight` helper

**Files:**
- Modify: `frontend/src/lib/vnapDashboard.ts` (add one export)
- Test: `frontend/src/lib/vnapDashboard.test.ts` (add a describe block)

**Interfaces:**
- Consumes: `VnapAircraft` (already imported in `vnapDashboard.ts`).
- Produces: `resolveHighlight(rows: VnapAircraft[], param: string | null): string | null` — the matching row's `icao24` (canonical case) or `null`.

- [ ] **Step 1: Write the failing test**

Append to `frontend/src/lib/vnapDashboard.test.ts`:

```ts
import { resolveHighlight } from "./vnapDashboard";

describe("resolveHighlight", () => {
  const rows = [
    { icao24: "a23a01" },
    { icao24: "acbc30" },
  ] as unknown as import("./api").VnapAircraft[];

  it("matches case-insensitively and returns the row's icao24", () => {
    expect(resolveHighlight(rows, "A23A01")).toBe("a23a01");
  });
  it("returns null when no row matches", () => {
    expect(resolveHighlight(rows, "ffffff")).toBeNull();
  });
  it("returns null for a null or empty param", () => {
    expect(resolveHighlight(rows, null)).toBeNull();
    expect(resolveHighlight(rows, "")).toBeNull();
  });
  it("returns null for empty rows", () => {
    expect(resolveHighlight([], "a23a01")).toBeNull();
  });
});
```

Note: if `vnapDashboard.test.ts` does not already import `describe/it/expect`, add `import { describe, it, expect } from "vitest";` at the top (check first — do not duplicate).

- [ ] **Step 2: Run test to verify it fails**

Run: `npx vitest run src/lib/vnapDashboard.test.ts`
Expected: FAIL — `resolveHighlight` is not exported.

- [ ] **Step 3: Write minimal implementation**

Add to `frontend/src/lib/vnapDashboard.ts` (near `sortAircraft`):

```ts
export function resolveHighlight(
  rows: VnapAircraft[],
  param: string | null,
): string | null {
  if (!param) return null;
  const target = param.toLowerCase();
  const match = rows.find((a) => a.icao24.toLowerCase() === target);
  return match ? match.icao24 : null;
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `npx vitest run src/lib/vnapDashboard.test.ts`
Expected: PASS (existing tests + 4 new assertions).

- [ ] **Step 5: Commit**

```bash
git add frontend/src/lib/vnapDashboard.ts frontend/src/lib/vnapDashboard.test.ts
git commit -m "feat(stats): resolveHighlight helper for deep-link row match"
```

---

### Task 3: Leaderboard cards become Stats deep-link anchors

**Files:**
- Modify: `frontend/src/App.tsx` — `RepeatOffendersSection` (component ~804-876; usage ~779-789; imports top of file)
- Modify: `frontend/src/styles.css:1701` (`.repeat-offenders .offender-card` rule)

**Interfaces:**
- Consumes: `statsHighlightHref` from `./lib/statsLinks` (Task 1).

- [ ] **Step 1: Import the helper**

At the top of `frontend/src/App.tsx`, add:

```ts
import { statsHighlightHref } from "./lib/statsLinks";
```

- [ ] **Step 2: Drop the `onSelect` prop from the component**

In `RepeatOffendersSection`, change the signature from:

```tsx
function RepeatOffendersSection({
  offenders,
  airportIcao,
  onSelect,
}: {
  offenders: RepeatOffender[];
  airportIcao?: string;
  onSelect: (icao24: string) => void;
}) {
```

to:

```tsx
function RepeatOffendersSection({
  offenders,
  airportIcao,
}: {
  offenders: RepeatOffender[];
  airportIcao?: string;
}) {
```

- [ ] **Step 3: Convert the card `<button>` to an `<a>`**

Replace the card element (currently `App.tsx:840-869`, the `<button className="offender-card" …>…</button>`) — change ONLY the opening tag and the closing tag, leaving the inner markup (`offender-rank`, `offender-body`, …) unchanged:

Opening tag:

```tsx
<a
  className="offender-card"
  href={statsHighlightHref(airportIcao, row.icao24)}
  title="Open this aircraft in the airport stats (VNAP compliance)"
>
```

Closing tag: change `</button>` to `</a>`.

- [ ] **Step 4: Update the usage site**

Replace the `<RepeatOffendersSection …>` usage (`App.tsx:779-789`) with:

```tsx
<RepeatOffendersSection
  offenders={repeatOffenders}
  airportIcao={airport?.icao}
/>
```

- [ ] **Step 5: Style the anchor like the old button**

In `frontend/src/styles.css`, inside the `.repeat-offenders .offender-card { … }` rule (starts at line 1701), add these two declarations:

```css
  text-decoration: none;
  color: inherit;
```

- [ ] **Step 6: Typecheck**

Run: `npx tsc --noEmit`
Expected: no errors. (If `RepeatOffender` becomes unused elsewhere it will not — it is still the prop type.)

- [ ] **Step 7: Commit**

```bash
git add frontend/src/App.tsx frontend/src/styles.css
git commit -m "feat(offenders): leaderboard cards deep-link to the stats page"
```

---

### Task 4: StatsPage reads the window from the URL

**Files:**
- Modify: `frontend/src/components/StatsPage.tsx` (import + `win` state init, line ~16)

**Interfaces:**
- Consumes: `windowFromUrl` from `../lib/statsLinks` (Task 1).

- [ ] **Step 1: Import the helper**

At the top of `frontend/src/components/StatsPage.tsx`, add:

```ts
import { windowFromUrl } from "../lib/statsLinks";
```

- [ ] **Step 2: Initialize `win` from the URL**

Change `StatsPage.tsx:16` from:

```tsx
  const [win, setWin] = useState<StatsWindow>("7d");
```

to:

```tsx
  const [win, setWin] = useState<StatsWindow>(() => windowFromUrl(window.location.search));
```

- [ ] **Step 3: Typecheck**

Run: `npx tsc --noEmit`
Expected: no errors.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/components/StatsPage.tsx
git commit -m "feat(stats): honor ?win= from the URL"
```

---

### Task 5: AircraftDashboard highlights the deep-linked tail

**Files:**
- Modify: `frontend/src/components/AircraftDashboard.tsx` (imports; new state/refs; new effect; `<tr>` id + pulse class)
- Modify: `frontend/src/styles.css:3030` (add pulse keyframes + rule)

**Interfaces:**
- Consumes: `resolveHighlight` from `../lib/vnapDashboard` (Task 2).

- [ ] **Step 1: Update imports**

In `AircraftDashboard.tsx`, change the React import (line 1) to include `useRef`:

```ts
import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
```

and add `resolveHighlight` to the vnapDashboard import (line 8):

```ts
import { sortAircraft, radarData, toCsv, OWNER_LABELS, OWNER_OPTIONS, resolveHighlight } from "../lib/vnapDashboard";
```

- [ ] **Step 2: Add deep-link state and read the param once**

Immediately after the existing `const [selected, setSelected] = useState<string | null>(null);` (line 35), add:

```tsx
  const [pulseIcao, setPulseIcao] = useState<string | null>(null);
  const deepLinkApplied = useRef(false);
  const deepLinkParam = useMemo(
    () => new URLSearchParams(window.location.search).get("aircraft"),
    [],
  );
```

- [ ] **Step 3: Add the apply-once effect**

After the `chart` `useMemo` (ends line 66), add:

```tsx
  useEffect(() => {
    if (deepLinkApplied.current || !data) return;
    const target = resolveHighlight(sorted, deepLinkParam);
    if (!target) return;
    deepLinkApplied.current = true;
    setSelected(target);
    document
      .getElementById(`vnap-row-${target}`)
      ?.scrollIntoView({ behavior: "smooth", block: "center" });
    setPulseIcao(target);
    const t = window.setTimeout(
      () => setPulseIcao((cur) => (cur === target ? null : cur)),
      1200,
    );
    return () => window.clearTimeout(t);
  }, [data, sorted, deepLinkParam]);
```

- [ ] **Step 4: Add row id + pulse class**

Change the `<tr>` (lines 127-129) from:

```tsx
                <tr key={a.icao24}
                    className={`vnap-row${a.icao24 === selectedAc?.icao24 ? " selected" : ""}`}
                    onClick={() => setSelected(a.icao24)}>
```

to:

```tsx
                <tr key={a.icao24}
                    id={`vnap-row-${a.icao24}`}
                    className={`vnap-row${a.icao24 === selectedAc?.icao24 ? " selected" : ""}${a.icao24 === pulseIcao ? " pulse" : ""}`}
                    onClick={() => setSelected(a.icao24)}>
```

- [ ] **Step 5: Add the pulse CSS**

In `frontend/src/styles.css`, immediately after line 3030 (`.vnap-row.selected { … }`), add:

```css
@keyframes vnap-row-pulse {
  0%   { background: rgba(179, 35, 31, 0.35); }
  100% { background: rgba(27, 58, 107, 0.12); }
}
.vnap-row.pulse { animation: vnap-row-pulse 1.2s ease-out; }
```

- [ ] **Step 6: Typecheck**

Run: `npx tsc --noEmit`
Expected: no errors.

- [ ] **Step 7: Browser verification (whole feature)**

Run: `npm run dev`, then in the browser:
1. On the main page, click a card in the bottom "Worst offenders" leaderboard.
2. Confirm the URL is `/stats?airport=<ICAO>&aircraft=<icao24>&win=all` and the page loads the **all** window (the `all` window button is active).
3. Confirm the matching tail's row is `selected` (highlighted), the radar title reads `<tail> vs. average`, the row is scrolled into view, and it flashes once.
4. Visit `/stats?airport=<ICAO>` directly (no `aircraft`) and confirm behavior is unchanged (top row selected, no flash).

- [ ] **Step 8: Commit**

```bash
git add frontend/src/components/AircraftDashboard.tsx frontend/src/styles.css
git commit -m "feat(stats): highlight, scroll to, and pulse the deep-linked tail"
```

---

## Self-Review

**Spec coverage:**
- Card → `/stats?…&win=all` deep-link → Task 3 (`statsHighlightHref`, Task 1).
- `win` honored from URL → Task 4 (`windowFromUrl`, Task 1).
- Pre-select + scroll + pulse the tail; applied once → Task 5.
- Row present-check / silent fallback → Task 2 `resolveHighlight` returns null; Task 5 effect early-returns; `selectedAc` keeps its `sorted[0]` fallback (unchanged).
- Case-insensitive icao24 → Task 2.
- Pure helpers unit-tested → Tasks 1, 2.
- No backend / no placeholder rows / no OffenderTable change → nothing in the plan touches them.

**Placeholder scan:** none — every code step shows full code.

**Type consistency:** `statsHighlightHref(airportIcao, icao24)`, `windowFromUrl(search)`, `resolveHighlight(rows, param)` names/signatures match across Tasks 1-5; `StatsWindow` values match `StatsPage` `WINDOWS`; DOM id `vnap-row-<icao24>` is written in Task 5 Step 4 and read in Step 3.
