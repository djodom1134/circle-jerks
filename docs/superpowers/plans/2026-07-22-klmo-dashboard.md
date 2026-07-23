# KLMO Landing Operations Dashboard — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a frontend-only, multi-airport-capable landing-operations dashboard (first host `klmo.airfieldeconomics.org`) that shows one bar per day for a month, filterable by operation type and locality, with a radial hub of observed origins that cross-filters the page — developed and tested entirely against fixtures.

**Architecture:** A new `dashboard/` React + Vite + TypeScript app (mirrors `lostlanding/`). It resolves the airport from `window.location.hostname`, fetches one whole-history payload from a same-origin `/api/*` endpoint, and does all filtering/aggregation client-side in pure functions. The two `/v1` endpoints and the Caddy proxy are an **externally-owned contract** — this plan builds only the frontend and its fixtures.

**Tech Stack:** React 18, Vite 6, TypeScript 5.7 (strict), recharts 3, vitest 2 + @testing-library/react, jsdom. Package manager: `npm`. Run tests from `dashboard/` with `npm test` (= `vitest run`).

## Global Constraints

- **Frontend only.** No Python, no Caddy, no backend tests. Endpoints are a contract the app targets; fixtures stand in for them.
- **Node package manager is `npm`** (matches `lostlanding/`); scripts: `dev` (vite `--host 0.0.0.0 --port 5175`), `build` (`tsc && vite build`), `preview`, `test` (`vitest run`).
- **TypeScript strict mode on** (`"strict": true`); no `any` in committed code; `noEmit`, `isolatedModules`, `jsx: react-jsx` — copy `lostlanding/tsconfig.json` verbatim.
- **API base:** all data calls go to **same-origin relative `/api/...`**. Base = `(import.meta.env.VITE_API_BASE ?? "/api").replace(/\/+$/, "")`. Dev proxies `/api` → the live API via `vite.config.ts`.
- **Airport is never hard-coded in a component.** It is resolved once in `App` from the hostname and threaded down as props; every title/label derives from it.
- **Locality buckets (exact strings):** `"local" | "out_of_town" | "unclassified"`. Operation types (exact strings): `"landing" | "touch_and_go" | "low_approach" | "takeoff"`.
- **Honesty rules (from the spec):** never fabricate data. A day inside `coverage` with zero ops is a real zero bar; a month outside `coverage` shows "No data yet", not zeros. Any fetch failure renders a visible error with retry, never a silent empty chart.
- **Validated palette (dataviz, verified light+dark):**
  - Locality — `local` blue `#2a78d6`/dark `#3987e5`; `out_of_town` orange `#eb6834`/dark `#d95926`; `unclassified` neutral grey `#b9b7ae`/dark `#6b6a64` (deliberate non-hue; legend + table view satisfy the relief rule).
  - Type — `landing` `#2a78d6`/`#3987e5`, `touch_and_go` `#eb6834`/`#d95926`, `low_approach` `#1baf7a`/`#199e70`, `takeoff` `#eda100`/`#c98500`.
  - Colour follows the entity, never its rank. Legend always present; a table-view toggle exists.
- **Reduced motion:** every hub animation is gated on `matchMedia("(prefers-reduced-motion: reduce)")` — static when set.
- **Commit cadence:** every task ends with a commit. Branch off a feature branch (not `main`).

---

## File Structure

New app rooted at `dashboard/`. Files and their single responsibilities:

- `dashboard/package.json`, `tsconfig.json`, `vite.config.ts`, `index.html` — build config (copied from `lostlanding/`, retargeted).
- `dashboard/src/main.tsx` — React entry (StrictMode + `App`).
- `dashboard/src/styles.css` — theme tokens (light/dark CSS custom properties) + layout.
- `dashboard/src/lib/types.ts` — the data-contract TypeScript types (mirror the endpoint shapes verbatim).
- `dashboard/src/lib/airport.ts` — `resolveAirport()`: hostname/override → ICAO. Pure.
- `dashboard/src/lib/api.ts` — typed fetch of `/api/...`; `DashboardFetchError`; no React.
- `dashboard/src/lib/facets.ts` — pure reducers over the daily payload (filter, monthly rollup, KPIs, month slicing). **The arithmetic core.**
- `dashboard/src/lib/format.ts` — number/date/percent formatting (subset copied from `lostlanding`).
- `dashboard/src/lib/palette.ts` — locality/type colour maps + `useTheme`/reduced-motion hooks.
- `dashboard/src/fixtures/klmo.ts` — checked-in fixtures matching each endpoint shape.
- `dashboard/src/components/Toolbar.tsx` — month nav, type chips, who chips, colour-by switch.
- `dashboard/src/components/KpiRow.tsx` — four filter-responsive headline numbers.
- `dashboard/src/components/DayBarChart.tsx` — the hero stacked bar (recharts).
- `dashboard/src/components/TrendPanel.tsx` — 12-month out-of-town-share mini chart.
- `dashboard/src/components/AircraftPanel.tsx` — busiest aircraft list.
- `dashboard/src/components/HourProfilePanel.tsx` — hour-of-day pattern (degrades to "unavailable").
- `dashboard/src/components/OriginHub.tsx` — radial relational diagram; `onSelectOrigin`.
- `dashboard/src/components/EmptyState.tsx`, `ErrorState.tsx`, `LoadingState.tsx` — states.
- `dashboard/src/components/DashboardPage.tsx` — composes the panels from filter state.
- `dashboard/src/App.tsx` — owns filter + selected-origin + load state; wires everything.

Tests live beside their unit as `*.test.ts(x)` (the `lostlanding` convention).

---

## Task 1: Scaffold the `dashboard/` app (build config + entry + smoke test)

**Files:**
- Create: `dashboard/package.json`, `dashboard/tsconfig.json`, `dashboard/vite.config.ts`, `dashboard/index.html`, `dashboard/.gitignore`
- Create: `dashboard/src/main.tsx`, `dashboard/src/App.tsx`, `dashboard/src/styles.css`, `dashboard/src/vite-env.d.ts`
- Create: `dashboard/src/test/setup.ts`
- Test: `dashboard/src/App.test.tsx`

**Interfaces:**
- Produces: `App` (default export, React component). For now renders a static `<h1>` heading so the app boots and one test passes.

- [ ] **Step 1: Create `dashboard/package.json`**

```json
{
  "name": "airfield-dashboard",
  "version": "0.1.0",
  "private": true,
  "type": "module",
  "scripts": {
    "dev": "vite --host 0.0.0.0 --port 5175",
    "build": "tsc && vite build",
    "preview": "vite preview --host 0.0.0.0 --port 5175",
    "test": "vitest run"
  },
  "dependencies": {
    "@vitejs/plugin-react": "^4.3.4",
    "react": "^18.3.1",
    "react-dom": "^18.3.1",
    "recharts": "^3.8.1",
    "typescript": "^5.7.2",
    "vite": "^6.0.1"
  },
  "devDependencies": {
    "@testing-library/dom": "^10.4.0",
    "@testing-library/react": "^16.3.0",
    "@types/react": "^18.3.12",
    "@types/react-dom": "^18.3.1",
    "jsdom": "^29.1.1",
    "vitest": "^2.1.8"
  }
}
```

- [ ] **Step 2: Create `dashboard/tsconfig.json`** (verbatim copy of `lostlanding/tsconfig.json`)

```json
{
  "compilerOptions": {
    "target": "ES2020",
    "useDefineForClassFields": true,
    "lib": ["DOM", "DOM.Iterable", "ES2020"],
    "allowJs": false,
    "skipLibCheck": true,
    "esModuleInterop": true,
    "allowSyntheticDefaultImports": true,
    "strict": true,
    "forceConsistentCasingInFileNames": true,
    "module": "ESNext",
    "moduleResolution": "Node",
    "resolveJsonModule": true,
    "isolatedModules": true,
    "noEmit": true,
    "jsx": "react-jsx"
  },
  "include": ["src"],
  "references": []
}
```

- [ ] **Step 3: Create `dashboard/vite.config.ts`**

Dev proxies same-origin `/api` to the live API over HTTPS (so `npm run dev` shows real data once the endpoints exist), rewriting `/api` → `/v1`. Never points anywhere by default beyond the public host.

```ts
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// `npm run dev` serves the SPA and proxies same-origin /api/* to the public
// circlejerks /v1 API over HTTPS, rewriting /api -> /v1. This mirrors the
// production Caddy proxy (which also injects the API key — the dev proxy does
// NOT, so live dev only reaches endpoints that tolerate an unauthenticated
// request or a key supplied out of band). Fixtures, not this proxy, are the
// default data source in tests.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5175,
    proxy: {
      "/api": {
        target: "https://circlejerks.live",
        changeOrigin: true,
        secure: true,
        rewrite: (path) => path.replace(/^\/api/, "/v1"),
      },
    },
  },
  test: {
    environment: "jsdom",
    setupFiles: ["./src/test/setup.ts"],
  },
});
```

- [ ] **Step 4: Create `dashboard/index.html`**

```html
<!doctype html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>Airfield Operations Dashboard</title>
    <meta name="description" content="Daily landing and pattern operations for a general-aviation airport, by day and by who flew them." />
  </head>
  <body>
    <div id="root"></div>
    <script type="module" src="/src/main.tsx"></script>
  </body>
</html>
```

- [ ] **Step 5: Create `dashboard/.gitignore`**

```
node_modules
dist
*.local
```

- [ ] **Step 6: Create `dashboard/src/test/setup.ts`** (ResizeObserver polyfill for recharts, verbatim from `lostlanding`)

```ts
// jsdom does not implement ResizeObserver, which recharts' ResponsiveContainer
// requires. Polyfill it so chart-bearing components can render in tests.
if (typeof globalThis.ResizeObserver === "undefined") {
  class ResizeObserverPolyfill {
    observe() {}
    unobserve() {}
    disconnect() {}
  }
  globalThis.ResizeObserver = ResizeObserverPolyfill as unknown as typeof ResizeObserver;
}
```

- [ ] **Step 7: Create `dashboard/src/styles.css`** (minimal theme tokens; expanded in Task 12)

```css
:root {
  --surface: #fcfcfb;
  --ink: #0b0b0b;
  --ink2: #52514e;
  --line: #e3e2dd;
  color-scheme: light dark;
}
@media (prefers-color-scheme: dark) {
  :root {
    --surface: #141414;
    --ink: #ffffff;
    --ink2: #c3c2b7;
    --line: #333330;
  }
}
* { box-sizing: border-box; }
body { margin: 0; background: var(--surface); color: var(--ink);
  font-family: system-ui, -apple-system, "Segoe UI", sans-serif; }
```

- [ ] **Step 8: Create `dashboard/src/vite-env.d.ts`** (so `import.meta.env` type-checks under strict `tsc`)

```ts
/// <reference types="vite/client" />
```

- [ ] **Step 9: Create `dashboard/src/main.tsx`**

```tsx
import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";
import "./styles.css";

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
```

- [ ] **Step 10: Create `dashboard/src/App.tsx`** (placeholder for now)

```tsx
export default function App() {
  return <h1>Airfield Operations Dashboard</h1>;
}
```

- [ ] **Step 11: Write the failing smoke test** — `dashboard/src/App.test.tsx`

```tsx
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import App from "./App";

describe("App", () => {
  it("renders the dashboard heading", () => {
    render(<App />);
    expect(screen.getByRole("heading", { name: /airfield operations dashboard/i })).toBeTruthy();
  });
});
```

- [ ] **Step 12: Install deps and run the test**

Run: `cd dashboard && npm install && npm test`
Expected: install succeeds; the smoke test PASSES.

- [ ] **Step 13: Commit**

```bash
git add dashboard/
git commit -m "feat(dashboard): scaffold React+Vite+TS app with smoke test"
```

---

## Task 2: Data-contract types (`lib/types.ts`)

**Files:**
- Create: `dashboard/src/lib/types.ts`
- Test: `dashboard/src/lib/types.test.ts`

**Interfaces:**
- Produces: `OperationType`, `Locality`, `DailyOperations`, `DayCounts`, `OriginsResponse`, `OriginRow`, `WorstOffendersResponse`, `HourlyProfileResponse` — the types every other module imports. Exact field names below are the contract; do not rename.

- [ ] **Step 1: Write the failing test** — `dashboard/src/lib/types.test.ts`

A types-only module has no runtime behavior; the test asserts a fixture object satisfies the types (compile-time) and that the exported string-literal unions have the expected members at runtime via helper arrays.

```ts
import { describe, expect, it } from "vitest";
import { OPERATION_TYPES, LOCALITIES } from "./types";
import type { DailyOperations } from "./types";

describe("contract types", () => {
  it("enumerates the four operation types and three localities", () => {
    expect(OPERATION_TYPES).toEqual(["landing", "touch_and_go", "low_approach", "takeoff"]);
    expect(LOCALITIES).toEqual(["local", "out_of_town", "unclassified"]);
  });

  it("accepts a well-formed daily payload", () => {
    const payload: DailyOperations = {
      airport_icao: "KLMO",
      timezone: "America/Denver",
      coverage: { min_day: "2026-07-01", max_day: "2026-07-03" },
      types: ["landing", "touch_and_go", "low_approach", "takeoff"],
      localities: ["local", "out_of_town", "unclassified"],
      days: [
        { date: "2026-07-01", counts: { landing: { local: 1, out_of_town: 0, unclassified: 0 },
          touch_and_go: { local: 0, out_of_town: 0, unclassified: 0 },
          low_approach: { local: 0, out_of_town: 0, unclassified: 0 },
          takeoff: { local: 0, out_of_town: 0, unclassified: 0 } } },
      ],
    };
    expect(payload.days).toHaveLength(1);
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd dashboard && npm test -- types`
Expected: FAIL — `./types` has no exports / module not found.

- [ ] **Step 3: Create `dashboard/src/lib/types.ts`**

```ts
/**
 * The data contract this dashboard targets. Each shape mirrors a /v1 endpoint
 * (reached via the same-origin /api proxy). These endpoints are owned and
 * implemented outside this repo's frontend; do not add fields the contract
 * does not define, and do not rename fields for display — components read
 * these verbatim and format at render time only.
 *
 * See docs/superpowers/specs/2026-07-22-klmo-dashboard-design.md § Data contract.
 */

export const OPERATION_TYPES = ["landing", "touch_and_go", "low_approach", "takeoff"] as const;
export type OperationType = (typeof OPERATION_TYPES)[number];

export const LOCALITIES = ["local", "out_of_town", "unclassified"] as const;
export type Locality = (typeof LOCALITIES)[number];

/** Per-locality counts for one operation type on one day. */
export type LocalityCounts = Record<Locality, number>;

/** Every operation type's per-locality counts for one day. */
export type DayCounts = Record<OperationType, LocalityCounts>;

export interface DailyOperationsDay {
  date: string; // YYYY-MM-DD in the airport's local timezone
  counts: DayCounts;
}

/** GET /api/airports/{icao}/daily-operations?from=&to= */
export interface DailyOperations {
  airport_icao: string;
  timezone: string;
  /** The bounds of data that actually exists; days outside are "no data yet". */
  coverage: { min_day: string; max_day: string };
  types: OperationType[];
  localities: Locality[];
  days: DailyOperationsDay[];
}

export interface OriginRow {
  icao: string;
  label: string;
  arrivals: number;
}

/** GET /api/airports/{icao}/origins?from=&to= */
export interface OriginsResponse {
  airport_icao: string;
  window: { from: string; to: string };
  total_out_of_town: number;
  origins: OriginRow[];
  other: { count: number; arrivals: number };
}

/** GET /api/airports/{icao}/worst-offenders (existing endpoint, reused). */
export interface WorstOffenderRow {
  icao24: string;
  registration: string | null;
  report_count: number;
}
export interface WorstOffendersResponse {
  airport_icao: string;
  offenders: WorstOffenderRow[];
}

/** GET /api/airports/{icao}/hourly-profile?from=&to= (optional endpoint). */
export interface HourlyProfileResponse {
  airport_icao: string;
  window: { from: string; to: string };
  hours: { hour: number; operations: number }[]; // 0..23
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd dashboard && npm test -- types`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add dashboard/src/lib/types.ts dashboard/src/lib/types.test.ts
git commit -m "feat(dashboard): data-contract types for the /v1 endpoints"
```

---

## Task 3: Airport resolution (`lib/airport.ts`)

**Files:**
- Create: `dashboard/src/lib/airport.ts`
- Test: `dashboard/src/lib/airport.test.ts`

**Interfaces:**
- Produces: `resolveAirport(input?: { hostname?: string; search?: string }): string` — returns an uppercase ICAO. Resolution order: `?airport=` query override → hostname subdomain map → default `"KLMO"`.

- [ ] **Step 1: Write the failing test** — `dashboard/src/lib/airport.test.ts`

```ts
import { describe, expect, it } from "vitest";
import { resolveAirport } from "./airport";

describe("resolveAirport", () => {
  it("maps a known subdomain to its ICAO", () => {
    expect(resolveAirport({ hostname: "klmo.airfieldeconomics.org" })).toBe("KLMO");
  });

  it("uppercases and honors the ?airport= override above the hostname", () => {
    expect(resolveAirport({ hostname: "klmo.airfieldeconomics.org", search: "?airport=kbjc" })).toBe("KBJC");
  });

  it("falls back to KLMO for an unknown host", () => {
    expect(resolveAirport({ hostname: "localhost" })).toBe("KLMO");
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd dashboard && npm test -- airport`
Expected: FAIL — module not found.

- [ ] **Step 3: Create `dashboard/src/lib/airport.ts`**

```ts
/**
 * Resolve which airport this dashboard is showing. In production the airport
 * is the hostname's leading label (klmo.airfieldeconomics.org -> KLMO); a
 * ?airport= query param overrides it for local dev and cross-airport preview.
 * Adding an airport is one entry in HOST_MAP plus a DNS record — no other code
 * changes.
 */
const HOST_MAP: Record<string, string> = {
  klmo: "KLMO",
};

const DEFAULT_ICAO = "KLMO";

export function resolveAirport(input?: { hostname?: string; search?: string }): string {
  const hostname = input?.hostname ?? (typeof window !== "undefined" ? window.location.hostname : "");
  const search = input?.search ?? (typeof window !== "undefined" ? window.location.search : "");

  const override = new URLSearchParams(search).get("airport");
  if (override && override.trim()) return override.trim().toUpperCase();

  const label = hostname.split(".")[0]?.toLowerCase() ?? "";
  return HOST_MAP[label] ?? DEFAULT_ICAO;
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd dashboard && npm test -- airport`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add dashboard/src/lib/airport.ts dashboard/src/lib/airport.test.ts
git commit -m "feat(dashboard): resolve airport from hostname with query override"
```

---

## Task 4: Fixtures (`fixtures/klmo.ts`)

**Files:**
- Create: `dashboard/src/fixtures/klmo.ts`
- Test: `dashboard/src/fixtures/klmo.test.ts`

**Interfaces:**
- Produces: `DAILY_FIXTURE: DailyOperations` (a full month + a few days of a second month, with at least one genuine zero-day inside coverage), `ORIGINS_FIXTURE: OriginsResponse`, `WORST_OFFENDERS_FIXTURE: WorstOffendersResponse`, `HOURLY_FIXTURE: HourlyProfileResponse`. Used by every component test (tests stub `fetch` to return these; there is no fixtures runtime mode in the app).

- [ ] **Step 1: Write the failing test** — `dashboard/src/fixtures/klmo.test.ts`

```ts
import { describe, expect, it } from "vitest";
import { DAILY_FIXTURE, ORIGINS_FIXTURE } from "./klmo";
import { OPERATION_TYPES, LOCALITIES } from "../lib/types";

describe("fixtures", () => {
  it("daily fixture is internally consistent with the contract", () => {
    expect(DAILY_FIXTURE.types).toEqual([...OPERATION_TYPES]);
    expect(DAILY_FIXTURE.localities).toEqual([...LOCALITIES]);
    expect(DAILY_FIXTURE.days.length).toBeGreaterThan(28);
    for (const day of DAILY_FIXTURE.days) {
      for (const t of OPERATION_TYPES) {
        for (const l of LOCALITIES) {
          expect(typeof day.counts[t][l]).toBe("number");
        }
      }
    }
  });

  it("contains at least one genuine zero-day inside coverage", () => {
    const zero = DAILY_FIXTURE.days.find((d) =>
      OPERATION_TYPES.every((t) => LOCALITIES.every((l) => d.counts[t][l] === 0)),
    );
    expect(zero).toBeTruthy();
  });

  it("origins fixture is ranked descending by arrivals", () => {
    const a = ORIGINS_FIXTURE.origins.map((o) => o.arrivals);
    expect([...a].sort((x, y) => y - x)).toEqual(a);
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd dashboard && npm test -- fixtures`
Expected: FAIL — module not found.

- [ ] **Step 3: Create `dashboard/src/fixtures/klmo.ts`**

Generate deterministically (no `Math.random` — fixtures must be stable). Build 31 days of July 2026 plus 3 days of August, one July day forced to all-zero.

```ts
import type { DailyOperations, DailyOperationsDay, OriginsResponse, WorstOffendersResponse, HourlyProfileResponse } from "../lib/types";

const WEEKDAY_RHYTHM = [0.72, 0.81, 0.94, 1.0, 0.88, 0.62, 0.55];

function day(dateStr: string, index: number): DailyOperationsDay {
  // A deterministic weekday rhythm; index 13 (Jul 14) is a forced zero-day.
  const isZero = index === 13;
  const base = isZero ? 0 : Math.round(60 * WEEKDAY_RHYTHM[index % 7]);
  const mk = (scale: number) => {
    const total = Math.round(base * scale);
    const out = Math.round(total * 0.2);
    const un = Math.round(total * 0.22);
    return { local: Math.max(total - out - un, 0), out_of_town: out, unclassified: un };
  };
  return {
    date: dateStr,
    counts: {
      landing: mk(0.4),
      touch_and_go: mk(0.8),
      low_approach: mk(1.9),
      takeoff: mk(0.42),
    },
  };
}

function buildDays(): DailyOperationsDay[] {
  const days: DailyOperationsDay[] = [];
  for (let d = 1; d <= 31; d++) days.push(day(`2026-07-${String(d).padStart(2, "0")}`, d - 1));
  for (let d = 1; d <= 3; d++) days.push(day(`2026-08-${String(d).padStart(2, "0")}`, 30 + d));
  return days;
}

export const DAILY_FIXTURE: DailyOperations = {
  airport_icao: "KLMO",
  timezone: "America/Denver",
  coverage: { min_day: "2026-07-01", max_day: "2026-08-03" },
  types: ["landing", "touch_and_go", "low_approach", "takeoff"],
  localities: ["local", "out_of_town", "unclassified"],
  days: buildDays(),
};

export const ORIGINS_FIXTURE: OriginsResponse = {
  airport_icao: "KLMO",
  window: { from: "2026-07-01", to: "2026-07-31" },
  total_out_of_town: 931,
  origins: [
    { icao: "KBDU", label: "Boulder", arrivals: 341 },
    { icao: "KBJC", label: "Rocky Mountain Metro", arrivals: 212 },
    { icao: "KFNL", label: "Fort Collins–Loveland", arrivals: 118 },
    { icao: "KGXY", label: "Greeley–Weld", arrivals: 96 },
    { icao: "KEIK", label: "Erie Municipal", arrivals: 73 },
    { icao: "KDEN", label: "Denver International", arrivals: 51 },
    { icao: "KAPA", label: "Centennial", arrivals: 40 },
  ],
  other: { count: 12, arrivals: 58 },
};

export const WORST_OFFENDERS_FIXTURE: WorstOffendersResponse = {
  airport_icao: "KLMO",
  offenders: [
    { icao24: "a1b2c3", registration: "N829SC", report_count: 214 },
    { icao24: "a1b2c4", registration: "N172RG", report_count: 163 },
    { icao24: "a1b2c5", registration: "N6284L", report_count: 131 },
    { icao24: "a1b2c6", registration: "N5589E", report_count: 94 },
    { icao24: "a1b2c7", registration: null, report_count: 71 },
  ],
};

export const HOURLY_FIXTURE: HourlyProfileResponse = {
  airport_icao: "KLMO",
  window: { from: "2026-07-01", to: "2026-07-31" },
  hours: Array.from({ length: 24 }, (_, h) => ({
    hour: h,
    operations: Math.round(100 * Math.max(0, Math.sin(((h - 5) / 14) * Math.PI))),
  })),
};
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd dashboard && npm test -- fixtures`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add dashboard/src/fixtures/
git commit -m "feat(dashboard): checked-in fixtures matching the endpoint contract"
```

---

## Task 5: The arithmetic core (`lib/facets.ts`)

**Files:**
- Create: `dashboard/src/lib/facets.ts`
- Test: `dashboard/src/lib/facets.test.ts`

**Interfaces:**
- Consumes: `DailyOperations`, `OperationType`, `Locality`, `OPERATION_TYPES`, `LOCALITIES` from `./types`.
- Produces:
  - `type FilterState = { types: Set<OperationType>; localities: Set<Locality> }`
  - `type ColorBy = "locality" | "type"`
  - `monthsInCoverage(data: DailyOperations): string[]` — sorted `YYYY-MM` keys spanning coverage.
  - `daysForMonth(data, monthKey): DailyOperationsDay[]` — the days of that month (may be empty → "no data").
  - `isMonthInCoverage(data, monthKey): boolean`
  - `filteredDaySeries(days, filter, colorBy): { date: string; segments: Record<string, number>; total: number }[]` — per-day stacked segments keyed by the colorBy dimension's members, after applying the filter.
  - `kpis(days, filter): { operations: number; outOfTownPct: number; busiestDay: number; }` (aircraft count comes from origins/offenders, not here).
  - `monthlyOutOfTownShare(data): { month: string; share: number }[]` — for the trend panel.

- [ ] **Step 1: Write the failing test** — `dashboard/src/lib/facets.test.ts`

```ts
import { describe, expect, it } from "vitest";
import { DAILY_FIXTURE } from "../fixtures/klmo";
import type { OperationType, Locality } from "./types";
import {
  monthsInCoverage, daysForMonth, isMonthInCoverage,
  filteredDaySeries, kpis, monthlyOutOfTownShare, allTypes, allLocalities,
} from "./facets";

const ALL = { types: allTypes(), localities: allLocalities() };

describe("month slicing", () => {
  it("lists months spanning coverage", () => {
    expect(monthsInCoverage(DAILY_FIXTURE)).toEqual(["2026-07", "2026-08"]);
  });
  it("returns 31 days for July and reports it in coverage", () => {
    expect(daysForMonth(DAILY_FIXTURE, "2026-07")).toHaveLength(31);
    expect(isMonthInCoverage(DAILY_FIXTURE, "2026-07")).toBe(true);
  });
  it("reports a month outside coverage as empty and not-in-coverage", () => {
    expect(daysForMonth(DAILY_FIXTURE, "2020-01")).toHaveLength(0);
    expect(isMonthInCoverage(DAILY_FIXTURE, "2020-01")).toBe(false);
  });
});

describe("filteredDaySeries", () => {
  const july = daysForMonth(DAILY_FIXTURE, "2026-07");

  it("colours by locality: segment keys are the selected localities", () => {
    const series = filteredDaySeries(july, ALL, "locality");
    expect(Object.keys(series[0].segments).sort()).toEqual(["local", "out_of_town", "unclassified"]);
  });

  it("filtering to one type reduces every day's total", () => {
    const all = filteredDaySeries(july, ALL, "locality");
    const landingsOnly = filteredDaySeries(july, { types: new Set<OperationType>(["landing"]), localities: allLocalities() }, "locality");
    const i = all.findIndex((d) => d.total > 0);
    expect(landingsOnly[i].total).toBeLessThan(all[i].total);
  });

  it("filtering out a locality drops it from the stack and the total", () => {
    const noVisitors = filteredDaySeries(july, { types: allTypes(), localities: new Set<Locality>(["local", "unclassified"]) }, "locality");
    expect(Object.keys(noVisitors[0].segments)).not.toContain("out_of_town");
  });

  it("a zero-day inside coverage has total 0 (a real zero, not missing)", () => {
    const series = filteredDaySeries(july, ALL, "locality");
    const zero = series.find((d) => d.date === "2026-07-14");
    expect(zero?.total).toBe(0);
  });
});

describe("kpis", () => {
  it("out-of-town pct is between 0 and 100 and operations is the filtered sum", () => {
    const july = daysForMonth(DAILY_FIXTURE, "2026-07");
    const k = kpis(july, ALL);
    expect(k.operations).toBeGreaterThan(0);
    expect(k.outOfTownPct).toBeGreaterThanOrEqual(0);
    expect(k.outOfTownPct).toBeLessThanOrEqual(100);
    expect(k.busiestDay).toBeGreaterThan(0);
  });
});

describe("monthlyOutOfTownShare", () => {
  it("produces one entry per covered month with a 0..1 share", () => {
    const s = monthlyOutOfTownShare(DAILY_FIXTURE);
    expect(s.map((x) => x.month)).toEqual(["2026-07", "2026-08"]);
    for (const m of s) { expect(m.share).toBeGreaterThanOrEqual(0); expect(m.share).toBeLessThanOrEqual(1); }
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd dashboard && npm test -- facets`
Expected: FAIL — module not found.

- [ ] **Step 3: Create `dashboard/src/lib/facets.ts`**

```ts
import {
  OPERATION_TYPES, LOCALITIES,
  type DailyOperations, type DailyOperationsDay, type OperationType, type Locality,
} from "./types";

export type FilterState = { types: Set<OperationType>; localities: Set<Locality> };
export type ColorBy = "locality" | "type";

export const allTypes = (): Set<OperationType> => new Set(OPERATION_TYPES);
export const allLocalities = (): Set<Locality> => new Set(LOCALITIES);

const monthKey = (date: string): string => date.slice(0, 7);

export function monthsInCoverage(data: DailyOperations): string[] {
  const seen = new Set<string>();
  for (const d of data.days) seen.add(monthKey(d.date));
  return [...seen].sort();
}

export function daysForMonth(data: DailyOperations, month: string): DailyOperationsDay[] {
  return data.days.filter((d) => monthKey(d.date) === month);
}

export function isMonthInCoverage(data: DailyOperations, month: string): boolean {
  return month >= monthKey(data.coverage.min_day) && month <= monthKey(data.coverage.max_day)
    && daysForMonth(data, month).length > 0;
}

/**
 * Collapse each day's type×locality grid to stacked segments keyed by the
 * active colour dimension, counting only cells whose type AND locality are
 * both selected. A segment key that ends up with zero across the whole month
 * is still emitted only if its dimension member is selected — so the stack
 * order is stable as values change.
 */
export function filteredDaySeries(
  days: DailyOperationsDay[], filter: FilterState, colorBy: ColorBy,
): { date: string; segments: Record<string, number>; total: number }[] {
  const typeList = OPERATION_TYPES.filter((t) => filter.types.has(t));
  const locList = LOCALITIES.filter((l) => filter.localities.has(l));
  const keys = colorBy === "locality" ? locList : typeList;

  return days.map((day) => {
    const segments: Record<string, number> = {};
    for (const k of keys) segments[k] = 0;
    let total = 0;
    for (const t of typeList) {
      for (const l of locList) {
        const v = day.counts[t][l];
        total += v;
        segments[colorBy === "locality" ? l : t] += v;
      }
    }
    return { date: day.date, segments, total };
  });
}

export function kpis(days: DailyOperationsDay[], filter: FilterState): {
  operations: number; outOfTownPct: number; busiestDay: number;
} {
  const typeList = OPERATION_TYPES.filter((t) => filter.types.has(t));
  const locList = LOCALITIES.filter((l) => filter.localities.has(l));
  let operations = 0, outOfTown = 0, busiestDay = 0;
  for (const day of days) {
    let dayTotal = 0;
    for (const t of typeList) for (const l of locList) {
      const v = day.counts[t][l];
      operations += v; dayTotal += v;
      if (l === "out_of_town") outOfTown += v;
    }
    if (dayTotal > busiestDay) busiestDay = dayTotal;
  }
  const outOfTownPct = operations === 0 ? 0 : Math.round((outOfTown / operations) * 100);
  return { operations, outOfTownPct, busiestDay };
}

export function monthlyOutOfTownShare(data: DailyOperations): { month: string; share: number }[] {
  return monthsInCoverage(data).map((month) => {
    let total = 0, out = 0;
    for (const day of daysForMonth(data, month)) {
      for (const t of OPERATION_TYPES) for (const l of LOCALITIES) {
        total += day.counts[t][l];
        if (l === "out_of_town") out += day.counts[t][l];
      }
    }
    return { month, share: total === 0 ? 0 : out / total };
  });
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd dashboard && npm test -- facets`
Expected: PASS (all cases).

- [ ] **Step 5: Commit**

```bash
git add dashboard/src/lib/facets.ts dashboard/src/lib/facets.test.ts
git commit -m "feat(dashboard): pure filter/rollup/KPI reducers over the daily payload"
```

---

## Task 6: API client (`lib/api.ts`)

**Files:**
- Create: `dashboard/src/lib/api.ts`
- Test: `dashboard/src/lib/api.test.ts`

**Interfaces:**
- Consumes: `DailyOperations`, `OriginsResponse`, `WorstOffendersResponse`, `HourlyProfileResponse` from `./types`.
- Produces:
  - `class DashboardFetchError extends Error { status?: number }`
  - `fetchDailyOperations(icao: string, opts?: { from?: string; to?: string; origin?: string }): Promise<DailyOperations>`
  - `fetchOrigins(icao: string, opts?: { from?: string; to?: string }): Promise<OriginsResponse>`
  - `fetchWorstOffenders(icao: string): Promise<WorstOffendersResponse>`
  - `fetchHourlyProfile(icao: string, opts?: { from?: string; to?: string }): Promise<HourlyProfileResponse>`
  - `API_BASE: string`

- [ ] **Step 1: Write the failing test** — `dashboard/src/lib/api.test.ts`

```ts
import { afterEach, describe, expect, it, vi } from "vitest";
import { DashboardFetchError, fetchDailyOperations, fetchOrigins } from "./api";
import { DAILY_FIXTURE, ORIGINS_FIXTURE } from "../fixtures/klmo";

afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks(); });

function jsonResponse(body: unknown, ok = true, status = 200) {
  return { ok, status, statusText: ok ? "OK" : "Error", json: async () => body } as Response;
}

describe("fetchDailyOperations", () => {
  it("requests the daily-operations path and returns the parsed payload", async () => {
    const fetchMock = vi.fn((_i: RequestInfo | URL) => Promise.resolve(jsonResponse(DAILY_FIXTURE)));
    vi.stubGlobal("fetch", fetchMock);
    await expect(fetchDailyOperations("KLMO")).resolves.toEqual(DAILY_FIXTURE);
    const url = String(fetchMock.mock.calls[0][0]);
    expect(url).toContain("/api/airports/KLMO/daily-operations");
  });

  it("passes from/to/origin as query params when given", async () => {
    const fetchMock = vi.fn((_i: RequestInfo | URL) => Promise.resolve(jsonResponse(DAILY_FIXTURE)));
    vi.stubGlobal("fetch", fetchMock);
    await fetchDailyOperations("KLMO", { from: "2026-07-01", to: "2026-07-31", origin: "KBDU" });
    const url = String(fetchMock.mock.calls[0][0]);
    expect(url).toContain("from=2026-07-01");
    expect(url).toContain("to=2026-07-31");
    expect(url).toContain("origin=KBDU");
  });

  it("throws DashboardFetchError on a non-2xx response", async () => {
    vi.stubGlobal("fetch", vi.fn(() => Promise.resolve(jsonResponse({}, false, 429))));
    await expect(fetchDailyOperations("KLMO")).rejects.toBeInstanceOf(DashboardFetchError);
  });

  it("throws DashboardFetchError when the network fails", async () => {
    vi.stubGlobal("fetch", vi.fn(() => Promise.reject(new Error("offline"))));
    await expect(fetchDailyOperations("KLMO")).rejects.toBeInstanceOf(DashboardFetchError);
  });
});

describe("fetchOrigins", () => {
  it("returns the parsed origins payload", async () => {
    vi.stubGlobal("fetch", vi.fn(() => Promise.resolve(jsonResponse(ORIGINS_FIXTURE))));
    await expect(fetchOrigins("KLMO")).resolves.toEqual(ORIGINS_FIXTURE);
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd dashboard && npm test -- api`
Expected: FAIL — module not found.

- [ ] **Step 3: Create `dashboard/src/lib/api.ts`**

```ts
import type {
  DailyOperations, OriginsResponse, WorstOffendersResponse, HourlyProfileResponse,
} from "./types";

// Same-origin proxy path. Production Caddy maps /api/* -> /v1 and injects the
// API key; dev's vite.config.ts proxies /api -> the public /v1. The frontend
// never holds a key. Trailing slashes stripped so path joins are clean.
export const API_BASE = (import.meta.env.VITE_API_BASE ?? "/api").replace(/\/+$/, "");

export class DashboardFetchError extends Error {
  status?: number;
  constructor(message: string, status?: number) {
    super(message);
    this.name = "DashboardFetchError";
    this.status = status;
  }
}

function query(params: Record<string, string | undefined>): string {
  const q = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) if (v) q.set(k, v);
  const s = q.toString();
  return s ? `?${s}` : "";
}

async function getJson<T>(path: string): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${API_BASE}${path}`);
  } catch (err) {
    throw new DashboardFetchError(
      err instanceof Error ? `Could not reach the API: ${err.message}` : "Could not reach the API.",
    );
  }
  if (!response.ok) {
    throw new DashboardFetchError(`API returned ${response.status} ${response.statusText}`, response.status);
  }
  return (await response.json()) as T;
}

const ap = (icao: string) => `/airports/${encodeURIComponent(icao)}`;

export function fetchDailyOperations(
  icao: string, opts?: { from?: string; to?: string; origin?: string },
): Promise<DailyOperations> {
  return getJson<DailyOperations>(`${ap(icao)}/daily-operations${query({ from: opts?.from, to: opts?.to, origin: opts?.origin })}`);
}

export function fetchOrigins(icao: string, opts?: { from?: string; to?: string }): Promise<OriginsResponse> {
  return getJson<OriginsResponse>(`${ap(icao)}/origins${query({ from: opts?.from, to: opts?.to })}`);
}

export function fetchWorstOffenders(icao: string): Promise<WorstOffendersResponse> {
  return getJson<WorstOffendersResponse>(`${ap(icao)}/worst-offenders`);
}

export function fetchHourlyProfile(
  icao: string, opts?: { from?: string; to?: string },
): Promise<HourlyProfileResponse> {
  return getJson<HourlyProfileResponse>(`${ap(icao)}/hourly-profile${query({ from: opts?.from, to: opts?.to })}`);
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd dashboard && npm test -- api`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add dashboard/src/lib/api.ts dashboard/src/lib/api.test.ts
git commit -m "feat(dashboard): typed /api client with explicit fetch errors"
```

---

## Task 7: Formatting + palette helpers (`lib/format.ts`, `lib/palette.ts`)

**Files:**
- Create: `dashboard/src/lib/format.ts`, `dashboard/src/lib/palette.ts`
- Test: `dashboard/src/lib/format.test.ts`, `dashboard/src/lib/palette.test.ts`

**Interfaces:**
- Produces (`format.ts`): `formatInteger(n)`, `formatPercent(fraction)`, `formatShortDate(iso)`, `formatMonthLabel(monthKey)` (e.g. `"2026-07"` → `"July 2026"`).
- Produces (`palette.ts`): `LOCALITY_LABEL: Record<Locality,string>`, `TYPE_LABEL: Record<OperationType,string>`, `localityColor(l, dark)`, `typeColor(t, dark)`, `usePrefersReducedMotion(): boolean`, `usePrefersDark(): boolean`.

- [ ] **Step 1: Write the failing tests**

`dashboard/src/lib/format.test.ts`:

```ts
import { describe, expect, it } from "vitest";
import { formatInteger, formatPercent, formatShortDate, formatMonthLabel } from "./format";

describe("format", () => {
  it("formats integers with grouping", () => { expect(formatInteger(4656)).toBe("4,656"); });
  it("formats a fraction as a whole percent", () => { expect(formatPercent(0.2)).toBe("20%"); });
  it("formats a date without timezone drift", () => { expect(formatShortDate("2026-07-01")).toBe("Jul 1"); });
  it("formats a month key as a long label", () => { expect(formatMonthLabel("2026-07")).toBe("July 2026"); });
});
```

`dashboard/src/lib/palette.test.ts`:

```ts
import { describe, expect, it } from "vitest";
import { localityColor, typeColor, LOCALITY_LABEL, TYPE_LABEL } from "./palette";

describe("palette", () => {
  it("gives the validated locality hues in light mode", () => {
    expect(localityColor("local", false)).toBe("#2a78d6");
    expect(localityColor("out_of_town", false)).toBe("#eb6834");
    expect(localityColor("unclassified", false)).toBe("#b9b7ae");
  });
  it("gives dark-stepped locality hues in dark mode", () => {
    expect(localityColor("local", true)).toBe("#3987e5");
  });
  it("labels are human-readable", () => {
    expect(LOCALITY_LABEL.out_of_town).toBe("Out of town");
    expect(TYPE_LABEL.touch_and_go).toBe("Touch & go");
  });
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd dashboard && npm test -- format palette`
Expected: FAIL — modules not found.

- [ ] **Step 3: Create `dashboard/src/lib/format.ts`**

```ts
const integer = new Intl.NumberFormat("en-US");

export function formatInteger(n: number): string {
  return integer.format(Math.round(n));
}

export function formatPercent(fraction: number): string {
  return `${Math.round(fraction * 100)}%`;
}

/** "2026-07-01" -> "Jul 1", with no timezone shifting. */
export function formatShortDate(isoDate: string): string {
  const [y, m, d] = isoDate.split("-").map(Number);
  const date = new Date(Date.UTC(y, (m ?? 1) - 1, d ?? 1));
  return new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric", timeZone: "UTC" }).format(date);
}

/** "2026-07" -> "July 2026". */
export function formatMonthLabel(monthKey: string): string {
  const [y, m] = monthKey.split("-").map(Number);
  const date = new Date(Date.UTC(y, (m ?? 1) - 1, 1));
  return new Intl.DateTimeFormat("en-US", { month: "long", year: "numeric", timeZone: "UTC" }).format(date);
}
```

- [ ] **Step 4: Create `dashboard/src/lib/palette.ts`**

```ts
import { useEffect, useState } from "react";
import type { Locality, OperationType } from "./types";

// Validated against dataviz/scripts/validate_palette.js in light AND dark
// (see the plan's Global Constraints). `unclassified` is a deliberate neutral,
// not a hue — the legend + table view satisfy the relief rule for it.
const LOCALITY_LIGHT: Record<Locality, string> = {
  local: "#2a78d6", out_of_town: "#eb6834", unclassified: "#b9b7ae",
};
const LOCALITY_DARK: Record<Locality, string> = {
  local: "#3987e5", out_of_town: "#d95926", unclassified: "#6b6a64",
};
const TYPE_LIGHT: Record<OperationType, string> = {
  landing: "#2a78d6", touch_and_go: "#eb6834", low_approach: "#1baf7a", takeoff: "#eda100",
};
const TYPE_DARK: Record<OperationType, string> = {
  landing: "#3987e5", touch_and_go: "#d95926", low_approach: "#199e70", takeoff: "#c98500",
};

export const LOCALITY_LABEL: Record<Locality, string> = {
  local: "Local", out_of_town: "Out of town", unclassified: "Unclassified",
};
export const TYPE_LABEL: Record<OperationType, string> = {
  landing: "Landings", touch_and_go: "Touch & go", low_approach: "Low approach", takeoff: "Takeoffs",
};

export function localityColor(l: Locality, dark: boolean): string {
  return (dark ? LOCALITY_DARK : LOCALITY_LIGHT)[l];
}
export function typeColor(t: OperationType, dark: boolean): string {
  return (dark ? TYPE_DARK : TYPE_LIGHT)[t];
}

function useMediaQuery(query: string): boolean {
  const [match, setMatch] = useState(() =>
    typeof window !== "undefined" && window.matchMedia ? window.matchMedia(query).matches : false);
  useEffect(() => {
    if (typeof window === "undefined" || !window.matchMedia) return;
    const mq = window.matchMedia(query);
    const on = () => setMatch(mq.matches);
    mq.addEventListener("change", on);
    return () => mq.removeEventListener("change", on);
  }, [query]);
  return match;
}

export const usePrefersReducedMotion = () => useMediaQuery("(prefers-reduced-motion: reduce)");
export const usePrefersDark = () => useMediaQuery("(prefers-color-scheme: dark)");
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd dashboard && npm test -- format palette`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add dashboard/src/lib/format.ts dashboard/src/lib/format.test.ts dashboard/src/lib/palette.ts dashboard/src/lib/palette.test.ts
git commit -m "feat(dashboard): formatting helpers and validated colour palette"
```

---

## Task 8: Toolbar (`components/Toolbar.tsx`)

**Files:**
- Create: `dashboard/src/components/Toolbar.tsx`
- Test: `dashboard/src/components/Toolbar.test.tsx`

**Interfaces:**
- Consumes: `FilterState`, `ColorBy` from `../lib/facets`; labels/colours from `../lib/palette`; `formatMonthLabel` from `../lib/format`.
- Produces: `Toolbar` component with props:
  ```ts
  interface ToolbarProps {
    airportLabel: string;          // e.g. "KLMO · Longmont"
    month: string;                 // "2026-07"
    months: string[];              // available months, ascending
    onMonthChange(month: string): void;
    filter: FilterState;
    onToggleType(t: OperationType): void;
    onToggleLocality(l: Locality): void;
    colorBy: ColorBy;
    onColorByChange(c: ColorBy): void;
  }
  ```

- [ ] **Step 1: Write the failing test** — `dashboard/src/components/Toolbar.test.tsx`

```tsx
import type { ComponentProps } from "react";
import { render, screen, fireEvent } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { Toolbar } from "./Toolbar";
import { allTypes, allLocalities } from "../lib/facets";

function setup(overrides: Partial<ComponentProps<typeof Toolbar>> = {}) {
  const props = {
    airportLabel: "KLMO · Longmont",
    month: "2026-07",
    months: ["2026-06", "2026-07", "2026-08"],
    onMonthChange: vi.fn(),
    filter: { types: allTypes(), localities: allLocalities() },
    onToggleType: vi.fn(),
    onToggleLocality: vi.fn(),
    colorBy: "locality" as const,
    onColorByChange: vi.fn(),
    ...overrides,
  };
  render(<Toolbar {...props} />);
  return props;
}

describe("Toolbar", () => {
  it("shows the current month label and airport", () => {
    setup();
    expect(screen.getByText(/July 2026/)).toBeTruthy();
    expect(screen.getByText(/KLMO · Longmont/)).toBeTruthy();
  });

  it("advances the month when the next button is clicked", () => {
    const props = setup();
    fireEvent.click(screen.getByRole("button", { name: /next month/i }));
    expect(props.onMonthChange).toHaveBeenCalledWith("2026-08");
  });

  it("disables next at the last available month", () => {
    setup({ month: "2026-08" });
    expect(screen.getByRole("button", { name: /next month/i })).toHaveProperty("disabled", true);
  });

  it("toggles a type when its chip is clicked", () => {
    const props = setup();
    fireEvent.click(screen.getByRole("button", { name: /touch & go/i }));
    expect(props.onToggleType).toHaveBeenCalledWith("touch_and_go");
  });

  it("switches colour-by when the type option is chosen", () => {
    const props = setup();
    fireEvent.click(screen.getByRole("button", { name: /colour by type/i }));
    expect(props.onColorByChange).toHaveBeenCalledWith("type");
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd dashboard && npm test -- Toolbar`
Expected: FAIL — module not found.

- [ ] **Step 3: Create `dashboard/src/components/Toolbar.tsx`**

```tsx
import { OPERATION_TYPES, LOCALITIES, type OperationType, type Locality } from "../lib/types";
import type { FilterState, ColorBy } from "../lib/facets";
import { TYPE_LABEL, LOCALITY_LABEL } from "../lib/palette";
import { formatMonthLabel } from "../lib/format";

interface ToolbarProps {
  airportLabel: string;
  month: string;
  months: string[];
  onMonthChange(month: string): void;
  filter: FilterState;
  onToggleType(t: OperationType): void;
  onToggleLocality(l: Locality): void;
  colorBy: ColorBy;
  onColorByChange(c: ColorBy): void;
}

export function Toolbar(props: ToolbarProps) {
  const { airportLabel, month, months, onMonthChange, filter, onToggleType, onToggleLocality, colorBy, onColorByChange } = props;
  const idx = months.indexOf(month);
  const prev = idx > 0 ? months[idx - 1] : null;
  const next = idx >= 0 && idx < months.length - 1 ? months[idx + 1] : null;

  return (
    <div className="toolbar" role="region" aria-label="Filters">
      <div className="toolbar-row">
        <span className="airport-label">{airportLabel}</span>
        <span className="month-nav">
          <button type="button" aria-label="Previous month" disabled={!prev} onClick={() => prev && onMonthChange(prev)}>‹</button>
          <b>{formatMonthLabel(month)}</b>
          <button type="button" aria-label="Next month" disabled={!next} onClick={() => next && onMonthChange(next)}>›</button>
        </span>
      </div>
      <div className="toolbar-row">
        <span className="group-label">Type</span>
        {OPERATION_TYPES.map((t) => (
          <button key={t} type="button"
            className={`chip ${filter.types.has(t) ? "on" : ""}`}
            aria-pressed={filter.types.has(t)}
            onClick={() => onToggleType(t)}>{TYPE_LABEL[t]}</button>
        ))}
      </div>
      <div className="toolbar-row">
        <span className="group-label">Who</span>
        {LOCALITIES.map((l) => (
          <button key={l} type="button"
            className={`chip who ${filter.localities.has(l) ? "on" : ""}`}
            aria-pressed={filter.localities.has(l)}
            onClick={() => onToggleLocality(l)}>{LOCALITY_LABEL[l]}</button>
        ))}
        <span className="group-label colorby-label">Colour by</span>
        <span className="seg-toggle" role="group" aria-label="Colour by">
          <button type="button" aria-label="Colour by locality"
            className={colorBy === "locality" ? "act" : ""}
            onClick={() => onColorByChange("locality")}>Locality</button>
          <button type="button" aria-label="Colour by type"
            className={colorBy === "type" ? "act" : ""}
            onClick={() => onColorByChange("type")}>Type</button>
        </span>
      </div>
    </div>
  );
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd dashboard && npm test -- Toolbar`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add dashboard/src/components/Toolbar.tsx dashboard/src/components/Toolbar.test.tsx
git commit -m "feat(dashboard): toolbar with month nav, filter chips, colour-by switch"
```

---

## Task 9: KPI row + hero DayBarChart (`components/KpiRow.tsx`, `DayBarChart.tsx`)

**Files:**
- Create: `dashboard/src/components/KpiRow.tsx`, `dashboard/src/components/DayBarChart.tsx`
- Test: `dashboard/src/components/KpiRow.test.tsx`, `dashboard/src/components/DayBarChart.test.tsx`

**Interfaces:**
- Consumes: `kpis`, `filteredDaySeries`, `FilterState`, `ColorBy` from `../lib/facets`; `DailyOperationsDay`, `OperationType`, `Locality` from `../lib/types`; colours/labels from `../lib/palette`; `formatInteger`, `formatPercent`, `formatShortDate` from `../lib/format`.
- Produces:
  - `KpiRow` props: `{ days: DailyOperationsDay[]; filter: FilterState; aircraftCount: number }`
  - `DayBarChart` props: `{ days: DailyOperationsDay[]; filter: FilterState; colorBy: ColorBy; dark: boolean }`

- [ ] **Step 1: Write the failing tests**

`dashboard/src/components/KpiRow.test.tsx`:

```tsx
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { KpiRow } from "./KpiRow";
import { daysForMonth, allTypes, allLocalities } from "../lib/facets";
import { DAILY_FIXTURE } from "../fixtures/klmo";

describe("KpiRow", () => {
  it("renders the four KPIs including a non-zero operations count", () => {
    const days = daysForMonth(DAILY_FIXTURE, "2026-07");
    render(<KpiRow days={days} filter={{ types: allTypes(), localities: allLocalities() }} aircraftCount={312} />);
    expect(screen.getByText(/Operations/i)).toBeTruthy();
    expect(screen.getByText(/Out of town/i)).toBeTruthy();
    expect(screen.getByText("312")).toBeTruthy();
  });
});
```

`dashboard/src/components/DayBarChart.test.tsx`:

```tsx
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { DayBarChart } from "./DayBarChart";
import { daysForMonth, allTypes, allLocalities } from "../lib/facets";
import { DAILY_FIXTURE } from "../fixtures/klmo";

describe("DayBarChart", () => {
  it("renders an accessible figure describing the month total", () => {
    const days = daysForMonth(DAILY_FIXTURE, "2026-07");
    render(<DayBarChart days={days} filter={{ types: allTypes(), localities: allLocalities() }} colorBy="locality" dark={false} />);
    // The chart exposes an aria-label summarising the data for screen readers.
    expect(screen.getByRole("img")).toBeTruthy();
  });

  it("renders a table view for accessibility (relief rule)", () => {
    const days = daysForMonth(DAILY_FIXTURE, "2026-07");
    render(<DayBarChart days={days} filter={{ types: allTypes(), localities: allLocalities() }} colorBy="locality" dark={false} />);
    expect(screen.getByRole("table")).toBeTruthy();
  });
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd dashboard && npm test -- KpiRow DayBarChart`
Expected: FAIL — modules not found.

- [ ] **Step 3: Create `dashboard/src/components/KpiRow.tsx`**

```tsx
import type { DailyOperationsDay } from "../lib/types";
import { kpis, type FilterState } from "../lib/facets";
import { formatInteger } from "../lib/format";

export function KpiRow({ days, filter, aircraftCount }: {
  days: DailyOperationsDay[]; filter: FilterState; aircraftCount: number;
}) {
  const k = kpis(days, filter);
  const tiles = [
    { value: formatInteger(k.operations), label: "Operations" },
    { value: `${k.outOfTownPct}%`, label: "Out of town" },
    { value: formatInteger(k.busiestDay), label: "Busiest day" },
    { value: formatInteger(aircraftCount), label: "Aircraft" },
  ];
  return (
    <div className="kpi-row">
      {tiles.map((t) => (
        <div className="kpi" key={t.label}>
          <b>{t.value}</b><span>{t.label}</span>
        </div>
      ))}
    </div>
  );
}
```

- [ ] **Step 4: Create `dashboard/src/components/DayBarChart.tsx`**

The recharts stacked bar. Segment keys come from `filteredDaySeries`; a `<Bar>` per key in the fixed dimension order, coloured by `palette`. Includes an aria-label figure summary and a visually-hidden `<table>` (the relief-rule table view).

```tsx
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { OPERATION_TYPES, LOCALITIES, type OperationType, type Locality, type DailyOperationsDay } from "../lib/types";
import { filteredDaySeries, type FilterState, type ColorBy } from "../lib/facets";
import { localityColor, typeColor, LOCALITY_LABEL, TYPE_LABEL } from "../lib/palette";
import { formatInteger, formatShortDate } from "../lib/format";

export function DayBarChart({ days, filter, colorBy, dark }: {
  days: DailyOperationsDay[]; filter: FilterState; colorBy: ColorBy; dark: boolean;
}) {
  const series = filteredDaySeries(days, filter, colorBy);
  const keys: string[] = colorBy === "locality"
    ? LOCALITIES.filter((l) => filter.localities.has(l))
    : OPERATION_TYPES.filter((t) => filter.types.has(t));

  const colorOf = (key: string) =>
    colorBy === "locality" ? localityColor(key as Locality, dark) : typeColor(key as OperationType, dark);
  const labelOf = (key: string) =>
    colorBy === "locality" ? LOCALITY_LABEL[key as Locality] : TYPE_LABEL[key as OperationType];

  const rows = series.map((d) => ({ label: formatShortDate(d.date), ...d.segments }));
  const total = series.reduce((s, d) => s + d.total, 0);
  const peak = series.reduce((m, d) => Math.max(m, d.total), 0);

  return (
    <section className="chart-card">
      <div className="chart-title">
        <span>{series.length} days · coloured by {colorBy === "locality" ? "who flew them" : "operation type"}</span>
        <b>{formatInteger(total)} operations</b>
      </div>
      <div role="img"
        aria-label={`Daily operations across ${series.length} days: ${formatInteger(total)} total, peaking at ${formatInteger(peak)} in a single day.`}
        style={{ width: "100%", height: 300 }}>
        <ResponsiveContainer width="100%" height="100%">
          <BarChart data={rows} margin={{ top: 10, right: 8, left: -12, bottom: 0 }}>
            <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="var(--line)" />
            <XAxis dataKey="label" tick={{ fill: "var(--ink2)", fontSize: 11 }} tickLine={false} interval="preserveStartEnd" />
            <YAxis allowDecimals={false} tick={{ fill: "var(--ink2)", fontSize: 11 }} tickLine={false} width={36} />
            <Tooltip formatter={(value, name) => [formatInteger(Number(value)), labelOf(String(name))]} />
            {keys.map((key, i) => (
              <Bar key={key} dataKey={key} name={key} stackId="ops" fill={colorOf(key)}
                radius={i === keys.length - 1 ? [3, 3, 0, 0] : [0, 0, 0, 0]} />
            ))}
          </BarChart>
        </ResponsiveContainer>
      </div>
      <div className="legend">
        {keys.map((key) => (
          <span key={key}><i style={{ background: colorOf(key) }} />{labelOf(key)}</span>
        ))}
      </div>
      <table className="visually-hidden">
        <caption>Daily operations by {colorBy}</caption>
        <thead><tr><th>Day</th>{keys.map((k) => <th key={k}>{labelOf(k)}</th>)}<th>Total</th></tr></thead>
        <tbody>
          {series.map((d) => (
            <tr key={d.date}>
              <td>{formatShortDate(d.date)}</td>
              {keys.map((k) => <td key={k}>{formatInteger(d.segments[k] ?? 0)}</td>)}
              <td>{formatInteger(d.total)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd dashboard && npm test -- KpiRow DayBarChart`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add dashboard/src/components/KpiRow.tsx dashboard/src/components/KpiRow.test.tsx dashboard/src/components/DayBarChart.tsx dashboard/src/components/DayBarChart.test.tsx
git commit -m "feat(dashboard): KPI row and hero stacked day-bar chart with table view"
```

---

## Task 10: Supporting panels (`TrendPanel`, `AircraftPanel`, `HourProfilePanel`)

**Files:**
- Create: `dashboard/src/components/TrendPanel.tsx`, `AircraftPanel.tsx`, `HourProfilePanel.tsx`
- Test: `dashboard/src/components/TrendPanel.test.tsx`, `AircraftPanel.test.tsx`, `HourProfilePanel.test.tsx`

**Interfaces:**
- Consumes: `monthlyOutOfTownShare` from `../lib/facets`; `DailyOperations`, `WorstOffendersResponse`, `HourlyProfileResponse` from `../lib/types`; `formatPercent`, `formatMonthLabel`, `formatInteger` from `../lib/format`.
- Produces:
  - `TrendPanel` props: `{ data: DailyOperations; activeMonth: string }`
  - `AircraftPanel` props: `{ offenders: WorstOffendersResponse | null }` (null → "unavailable").
  - `HourProfilePanel` props: `{ hourly: HourlyProfileResponse | null }` (null → "unavailable").

- [ ] **Step 1: Write the failing tests**

`dashboard/src/components/TrendPanel.test.tsx`:

```tsx
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { TrendPanel } from "./TrendPanel";
import { DAILY_FIXTURE } from "../fixtures/klmo";

describe("TrendPanel", () => {
  it("renders a 12-month out-of-town-share heading", () => {
    render(<TrendPanel data={DAILY_FIXTURE} activeMonth="2026-07" />);
    expect(screen.getByText(/out.of.town/i)).toBeTruthy();
  });
});
```

`dashboard/src/components/AircraftPanel.test.tsx`:

```tsx
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { AircraftPanel } from "./AircraftPanel";
import { WORST_OFFENDERS_FIXTURE } from "../fixtures/klmo";

describe("AircraftPanel", () => {
  it("lists tail numbers when data is present", () => {
    render(<AircraftPanel offenders={WORST_OFFENDERS_FIXTURE} />);
    expect(screen.getByText("N829SC")).toBeTruthy();
  });
  it("shows an unavailable state when null", () => {
    render(<AircraftPanel offenders={null} />);
    expect(screen.getByText(/unavailable/i)).toBeTruthy();
  });
});
```

`dashboard/src/components/HourProfilePanel.test.tsx`:

```tsx
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { HourProfilePanel } from "./HourProfilePanel";
import { HOURLY_FIXTURE } from "../fixtures/klmo";

describe("HourProfilePanel", () => {
  it("renders when hourly data is present", () => {
    render(<HourProfilePanel hourly={HOURLY_FIXTURE} />);
    expect(screen.getByText(/by hour/i)).toBeTruthy();
  });
  it("degrades to unavailable when null", () => {
    render(<HourProfilePanel hourly={null} />);
    expect(screen.getByText(/unavailable/i)).toBeTruthy();
  });
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd dashboard && npm test -- TrendPanel AircraftPanel HourProfilePanel`
Expected: FAIL — modules not found.

- [ ] **Step 3: Create `dashboard/src/components/TrendPanel.tsx`**

```tsx
import { Bar, BarChart, ResponsiveContainer, Tooltip, XAxis } from "recharts";
import type { DailyOperations } from "../lib/types";
import { monthlyOutOfTownShare } from "../lib/facets";
import { formatMonthLabel, formatPercent } from "../lib/format";

export function TrendPanel({ data, activeMonth }: { data: DailyOperations; activeMonth: string }) {
  const share = monthlyOutOfTownShare(data).slice(-12).map((m) => ({
    label: formatMonthLabel(m.month).replace(/ \d{4}$/, ""),
    month: m.month,
    pct: Math.round(m.share * 100),
  }));
  return (
    <div className="panel">
      <h6>Out-of-town share · 12 months</h6>
      <div style={{ width: "100%", height: 90 }}>
        <ResponsiveContainer width="100%" height="100%">
          <BarChart data={share} margin={{ top: 4, right: 4, left: 4, bottom: 0 }}>
            <XAxis dataKey="label" tick={{ fill: "var(--ink2)", fontSize: 9 }} tickLine={false} interval="preserveStartEnd" />
            <Tooltip formatter={(value) => [`${Number(value)}%`, "Out of town"]} />
            <Bar dataKey="pct" fill="#eb6834" radius={[2, 2, 0, 0]} />
          </BarChart>
        </ResponsiveContainer>
      </div>
      <p className="cap">{formatMonthLabel(activeMonth)} is the month in view above.</p>
    </div>
  );
}
```

- [ ] **Step 4: Create `dashboard/src/components/AircraftPanel.tsx`**

```tsx
import type { WorstOffendersResponse } from "../lib/types";
import { formatInteger } from "../lib/format";

export function AircraftPanel({ offenders }: { offenders: WorstOffendersResponse | null }) {
  if (!offenders || offenders.offenders.length === 0) {
    return <div className="panel"><h6>Busiest aircraft</h6><p className="cap">Unavailable for this airport yet.</p></div>;
  }
  const max = offenders.offenders[0].report_count || 1;
  return (
    <div className="panel">
      <h6>Busiest aircraft</h6>
      {offenders.offenders.map((o) => (
        <div className="rowbar" key={o.icao24}>
          <span className="nm">{o.registration ?? o.icao24}</span>
          <span className="tr"><span className="fl" style={{ width: `${Math.round((o.report_count / max) * 100)}%` }} /></span>
          <span className="ct">{formatInteger(o.report_count)}</span>
        </div>
      ))}
    </div>
  );
}
```

- [ ] **Step 5: Create `dashboard/src/components/HourProfilePanel.tsx`**

```tsx
import type { HourlyProfileResponse } from "../lib/types";

export function HourProfilePanel({ hourly }: { hourly: HourlyProfileResponse | null }) {
  if (!hourly || hourly.hours.length === 0) {
    return <div className="panel"><h6>By hour</h6><p className="cap">Unavailable for this window yet.</p></div>;
  }
  const max = hourly.hours.reduce((m, h) => Math.max(m, h.operations), 0) || 1;
  return (
    <div className="panel">
      <h6>By hour</h6>
      <div className="heat">
        {hourly.hours.map((h) => (
          <i key={h.hour} title={`${h.hour}:00 — ${h.operations}`}
            style={{ background: "#2a78d6", opacity: 0.15 + (h.operations / max) * 0.85 }} />
        ))}
      </div>
      <p className="cap">Local time, 00–23.</p>
    </div>
  );
}
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `cd dashboard && npm test -- TrendPanel AircraftPanel HourProfilePanel`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add dashboard/src/components/TrendPanel.tsx dashboard/src/components/TrendPanel.test.tsx dashboard/src/components/AircraftPanel.tsx dashboard/src/components/AircraftPanel.test.tsx dashboard/src/components/HourProfilePanel.tsx dashboard/src/components/HourProfilePanel.test.tsx
git commit -m "feat(dashboard): trend, busiest-aircraft, and hour-of-day panels"
```

---

## Task 11: OriginHub (`components/OriginHub.tsx`)

**Files:**
- Create: `dashboard/src/components/OriginHub.tsx`
- Test: `dashboard/src/components/OriginHub.test.tsx`

**Interfaces:**
- Consumes: `OriginsResponse`, `OriginRow` from `../lib/types`; `usePrefersReducedMotion` from `../lib/palette`; `formatInteger` from `../lib/format`.
- Produces: `OriginHub` props:
  ```ts
  interface OriginHubProps {
    origins: OriginsResponse | null;      // null → "unavailable"
    selectedOrigin: string | null;        // ICAO or null
    onSelectOrigin(icao: string | null): void;
    airportIcao: string;                  // centre label
  }
  ```
  Clicking a node calls `onSelectOrigin(icao)`, or `onSelectOrigin(null)` if it was already selected. Renders an SVG radial hub; animations gate on reduced-motion.

- [ ] **Step 1: Write the failing test** — `dashboard/src/components/OriginHub.test.tsx`

```tsx
import { render, screen, fireEvent } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { OriginHub } from "./OriginHub";
import { ORIGINS_FIXTURE } from "../fixtures/klmo";

describe("OriginHub", () => {
  it("renders a node per origin plus the centre airport", () => {
    render(<OriginHub origins={ORIGINS_FIXTURE} selectedOrigin={null} onSelectOrigin={() => {}} airportIcao="KLMO" />);
    expect(screen.getByText("KBDU")).toBeTruthy();
    expect(screen.getByText("KLMO")).toBeTruthy();
  });

  it("calls onSelectOrigin with the ICAO when a node is clicked", () => {
    const onSelect = vi.fn();
    render(<OriginHub origins={ORIGINS_FIXTURE} selectedOrigin={null} onSelectOrigin={onSelect} airportIcao="KLMO" />);
    fireEvent.click(screen.getByRole("button", { name: /KBDU/ }));
    expect(onSelect).toHaveBeenCalledWith("KBDU");
  });

  it("deselects when the already-selected node is clicked again", () => {
    const onSelect = vi.fn();
    render(<OriginHub origins={ORIGINS_FIXTURE} selectedOrigin="KBDU" onSelectOrigin={onSelect} airportIcao="KLMO" />);
    fireEvent.click(screen.getByRole("button", { name: /KBDU/ }));
    expect(onSelect).toHaveBeenCalledWith(null);
  });

  it("shows an unavailable state when origins is null", () => {
    render(<OriginHub origins={null} selectedOrigin={null} onSelectOrigin={() => {}} airportIcao="KLMO" />);
    expect(screen.getByText(/unavailable/i)).toBeTruthy();
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd dashboard && npm test -- OriginHub`
Expected: FAIL — module not found.

- [ ] **Step 3: Create `dashboard/src/components/OriginHub.tsx`**

Each origin node is a focusable `<g role="button">` with an accessible name including its ICAO. Arc `stroke-dashoffset` draw-in and the traveling pulse are applied only when reduced-motion is not set.

```tsx
import type { OriginsResponse } from "../lib/types";
import { usePrefersReducedMotion } from "../lib/palette";
import { formatInteger } from "../lib/format";

const CX = 210, CY = 150, R = 110, VIEW_W = 420, VIEW_H = 300;

export function OriginHub({ origins, selectedOrigin, onSelectOrigin, airportIcao }: {
  origins: OriginsResponse | null;
  selectedOrigin: string | null;
  onSelectOrigin(icao: string | null): void;
  airportIcao: string;
}) {
  const reduce = usePrefersReducedMotion();
  if (!origins || origins.origins.length === 0) {
    return <section className="hub"><h5>Where out-of-town traffic comes from</h5>
      <p className="cap">Origin data unavailable for this airport yet.</p></section>;
  }

  const maxArr = origins.origins[0].arrivals || 1;
  const n = origins.origins.length;

  const toggle = (icao: string) => onSelectOrigin(selectedOrigin === icao ? null : icao);

  return (
    <section className="hub">
      <h5>Where out-of-town traffic comes from</h5>
      <p className="cap">{formatInteger(origins.total_out_of_town)} out-of-town arrivals · {n} origins shown · click one to filter the page.</p>
      <svg viewBox={`0 0 ${VIEW_W} ${VIEW_H}`} className={reduce ? "" : "animate"} role="group" aria-label="Origin airports and their arrivals into the field">
        {origins.origins.map((o, i) => {
          const ang = (-90 + i * (360 / n)) * Math.PI / 180;
          const x = CX + Math.cos(ang) * R, y = CY + Math.sin(ang) * R;
          const w = 1.2 + (o.arrivals / maxArr) * 5;
          const d = `M${x} ${y} Q${(CX + x) / 2 + (CY - y) * 0.12} ${(CY + y) / 2 + (x - CX) * 0.12} ${CX} ${CY}`;
          const dim = selectedOrigin && selectedOrigin !== o.icao;
          return (
            <path key={`e-${o.icao}`} d={d} fill="none" stroke="#eb6834" strokeWidth={w}
              strokeLinecap="round" strokeOpacity={dim ? 0.08 : selectedOrigin ? 0.85 : 0.3} />
          );
        })}
        <circle cx={CX} cy={CY} r={17} fill="#0b1e2b" stroke="#2a78d6" strokeWidth={2} />
        <text x={CX} y={CY + 4} textAnchor="middle" fontSize={9} fontWeight={700} fill="#fff">{airportIcao}</text>
        {origins.origins.map((o, i) => {
          const ang = (-90 + i * (360 / n)) * Math.PI / 180;
          const x = CX + Math.cos(ang) * R, y = CY + Math.sin(ang) * R;
          const r = 5 + (o.arrivals / maxArr) * 8;
          const selected = selectedOrigin === o.icao;
          const dim = selectedOrigin && !selected;
          return (
            <g key={o.icao} role="button" tabIndex={0}
              aria-pressed={selected}
              aria-label={`${o.icao} ${o.label}, ${o.arrivals} arrivals`}
              style={{ cursor: "pointer" }}
              onClick={() => toggle(o.icao)}
              onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); toggle(o.icao); } }}>
              <circle cx={x} cy={y} r={selected ? r + 2 : r} fill="#eb6834"
                fillOpacity={dim ? 0.2 : 0.9} stroke="var(--surface)" strokeWidth={1.5} />
              <text x={x} y={y - r - 4} textAnchor="middle" fontSize={8} fill="var(--ink2)">{o.icao}</text>
            </g>
          );
        })}
      </svg>
    </section>
  );
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd dashboard && npm test -- OriginHub`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add dashboard/src/components/OriginHub.tsx dashboard/src/components/OriginHub.test.tsx
git commit -m "feat(dashboard): radial origin hub with click-to-cross-filter"
```

---

## Task 12: State components + styles (`Loading`, `Error`, `Empty`, styles)

**Files:**
- Create: `dashboard/src/components/LoadingState.tsx`, `ErrorState.tsx`, `EmptyState.tsx`
- Modify: `dashboard/src/styles.css` (append the full layout + component styles)
- Test: `dashboard/src/components/ErrorState.test.tsx`, `EmptyState.test.tsx`

**Interfaces:**
- Produces:
  - `LoadingState` (no props)
  - `ErrorState` props: `{ message: string; onRetry(): void }`
  - `EmptyState` props: `{ month: string }` — the "no data yet for this period" state.

- [ ] **Step 1: Write the failing tests**

`dashboard/src/components/ErrorState.test.tsx`:

```tsx
import { render, screen, fireEvent } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ErrorState } from "./ErrorState";

describe("ErrorState", () => {
  it("shows the message and fires retry", () => {
    const onRetry = vi.fn();
    render(<ErrorState message="API returned 429" onRetry={onRetry} />);
    expect(screen.getByText(/429/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /retry/i }));
    expect(onRetry).toHaveBeenCalled();
  });
});
```

`dashboard/src/components/EmptyState.test.tsx`:

```tsx
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { EmptyState } from "./EmptyState";

describe("EmptyState", () => {
  it("names the month with no data", () => {
    render(<EmptyState month="2020-01" />);
    expect(screen.getByText(/no data yet/i)).toBeTruthy();
    expect(screen.getByText(/January 2020/)).toBeTruthy();
  });
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd dashboard && npm test -- ErrorState EmptyState`
Expected: FAIL — modules not found.

- [ ] **Step 3: Create the three state components**

`dashboard/src/components/LoadingState.tsx`:

```tsx
export function LoadingState() {
  return <div className="state" role="status"><p>Loading operations…</p></div>;
}
```

`dashboard/src/components/ErrorState.tsx`:

```tsx
export function ErrorState({ message, onRetry }: { message: string; onRetry(): void }) {
  return (
    <div className="state error" role="alert">
      <p>Could not load operations.</p>
      <p className="detail">{message}</p>
      <button type="button" onClick={onRetry}>Retry</button>
    </div>
  );
}
```

`dashboard/src/components/EmptyState.tsx`:

```tsx
import { formatMonthLabel } from "../lib/format";

export function EmptyState({ month }: { month: string }) {
  return (
    <div className="state" role="status">
      <p>No data yet for {formatMonthLabel(month)}.</p>
      <p className="detail">This period is outside the data we currently hold for this airport.</p>
    </div>
  );
}
```

- [ ] **Step 4: Append component + layout styles to `dashboard/src/styles.css`**

```css
/* ---- layout ---- */
.app { max-width: 1080px; margin: 0 auto; padding: 20px 16px 60px; }
.toolbar { border: 1px solid var(--line); border-radius: 12px; padding: 12px 14px; margin-bottom: 16px; }
.toolbar-row { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; padding: 5px 0; }
.airport-label { font-weight: 700; font-size: 15px; }
.month-nav { display: flex; align-items: center; gap: 8px; margin-left: auto; font-size: 14px; }
.month-nav button { width: 26px; height: 26px; border: 1px solid var(--line); background: transparent; color: var(--ink2); border-radius: 6px; cursor: pointer; }
.month-nav button:disabled { opacity: 0.35; cursor: default; }
.group-label { font-size: 10px; text-transform: uppercase; letter-spacing: 0.06em; color: var(--ink2); }
.colorby-label { margin-left: auto; }
.chip { border: 1px solid var(--line); border-radius: 999px; padding: 4px 11px; font-size: 12px; color: var(--ink2); background: transparent; cursor: pointer; }
.chip.on { background: #2a78d6; border-color: #2a78d6; color: #fff; }
.chip.who.on { background: #eb6834; border-color: #eb6834; color: #fff; }
.seg-toggle { display: inline-flex; border: 1px solid var(--line); border-radius: 8px; overflow: hidden; }
.seg-toggle button { padding: 4px 12px; font-size: 12px; color: var(--ink2); background: transparent; border: none; cursor: pointer; }
.seg-toggle button.act { background: var(--ink); color: var(--surface); }
.kpi-row { display: flex; gap: 10px; margin-bottom: 16px; }
.kpi { flex: 1; border: 1px solid var(--line); border-radius: 10px; padding: 10px 12px; }
.kpi b { display: block; font-size: 22px; font-weight: 600; letter-spacing: -0.01em; }
.kpi span { color: var(--ink2); font-size: 11px; }
.chart-card { border: 1px solid var(--line); border-radius: 12px; padding: 14px 16px; margin-bottom: 16px; }
.chart-title { display: flex; justify-content: space-between; align-items: baseline; margin-bottom: 8px; }
.chart-title span { color: var(--ink2); font-size: 12px; }
.legend { display: flex; gap: 16px; margin-top: 10px; color: var(--ink2); font-size: 12px; flex-wrap: wrap; }
.legend i { width: 10px; height: 10px; border-radius: 2px; display: inline-block; margin-right: 6px; vertical-align: -1px; }
.panel-row { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 12px; margin-bottom: 16px; }
.panel { border: 1px solid var(--line); border-radius: 10px; padding: 10px 12px; }
.panel h6 { margin: 0 0 8px; font-size: 12px; }
.panel .cap { color: var(--ink2); font-size: 11px; }
.rowbar { display: flex; align-items: center; gap: 8px; margin: 4px 0; }
.rowbar .nm { width: 60px; font-size: 11px; color: var(--ink2); flex: none; }
.rowbar .tr { flex: 1; height: 9px; background: var(--line); border-radius: 4px; overflow: hidden; }
.rowbar .fl { height: 100%; background: #2a78d6; }
.rowbar .ct { font-size: 11px; color: var(--ink2); width: 38px; text-align: right; }
.heat { display: grid; grid-template-columns: repeat(12, 1fr); gap: 3px; }
.heat i { aspect-ratio: 1; border-radius: 2px; }
.hub { border: 1px solid var(--line); border-radius: 12px; padding: 14px 16px; }
.hub h5 { margin: 0 0 2px; font-size: 14px; }
.hub svg { width: 100%; height: auto; display: block; }
.state { text-align: center; padding: 60px 20px; color: var(--ink2); }
.state.error button, .hub button { cursor: pointer; }
.state .detail { font-size: 12px; opacity: 0.8; }
.visually-hidden { position: absolute; width: 1px; height: 1px; overflow: hidden; clip: rect(0 0 0 0); white-space: nowrap; }
/* ---- hub draw-in (skipped under reduced motion via absence of .animate) ---- */
.hub svg.animate path { stroke-dasharray: 320; stroke-dashoffset: 320; animation: hub-draw 1.1s ease forwards; }
@keyframes hub-draw { to { stroke-dashoffset: 0; } }
@media (max-width: 720px) { .panel-row { grid-template-columns: 1fr; } .kpi-row { flex-wrap: wrap; } }
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd dashboard && npm test -- ErrorState EmptyState`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add dashboard/src/components/LoadingState.tsx dashboard/src/components/ErrorState.tsx dashboard/src/components/EmptyState.tsx dashboard/src/components/ErrorState.test.tsx dashboard/src/components/EmptyState.test.tsx dashboard/src/styles.css
git commit -m "feat(dashboard): loading/error/empty states and full stylesheet"
```

---

## Task 13: DashboardPage — compose panels from props

**Files:**
- Create: `dashboard/src/components/DashboardPage.tsx`
- Test: `dashboard/src/components/DashboardPage.test.tsx`

**Interfaces:**
- Consumes: every panel component; `FilterState`, `ColorBy`, `daysForMonth`, `isMonthInCoverage` from `../lib/facets`; `DailyOperations`, `OriginsResponse`, `WorstOffendersResponse`, `HourlyProfileResponse` from `../lib/types`.
- Produces: `DashboardPage` props:
  ```ts
  interface DashboardPageProps {
    data: DailyOperations;
    origins: OriginsResponse | null;
    offenders: WorstOffendersResponse | null;
    hourly: HourlyProfileResponse | null;
    airportLabel: string;
    month: string;
    filter: FilterState;
    colorBy: ColorBy;
    dark: boolean;
    aircraftCount: number;
    selectedOrigin: string | null;
    onSelectOrigin(icao: string | null): void;
  }
  ```
  Renders the KPI row, hero chart (or `EmptyState` if the month is out of coverage), panel row, and hub. Pure presentation — no fetching, no filter state.

- [ ] **Step 1: Write the failing test** — `dashboard/src/components/DashboardPage.test.tsx`

```tsx
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { DashboardPage } from "./DashboardPage";
import { DAILY_FIXTURE, ORIGINS_FIXTURE, WORST_OFFENDERS_FIXTURE, HOURLY_FIXTURE } from "../fixtures/klmo";
import { allTypes, allLocalities } from "../lib/facets";

const base = {
  data: DAILY_FIXTURE, origins: ORIGINS_FIXTURE, offenders: WORST_OFFENDERS_FIXTURE, hourly: HOURLY_FIXTURE,
  airportLabel: "KLMO · Longmont", filter: { types: allTypes(), localities: allLocalities() },
  colorBy: "locality" as const, dark: false, aircraftCount: 312,
  selectedOrigin: null, onSelectOrigin: () => {},
};

describe("DashboardPage", () => {
  it("renders the hero chart and the panels for a month in coverage", () => {
    render(<DashboardPage {...base} month="2026-07" />);
    expect(screen.getByRole("img")).toBeTruthy();       // hero DayBarChart
    expect(screen.getByText("N829SC")).toBeTruthy();     // AircraftPanel → panels render
  });

  it("renders the empty state for a month outside coverage", () => {
    render(<DashboardPage {...base} month="2020-01" />);
    expect(screen.getByText(/no data yet/i)).toBeTruthy();
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd dashboard && npm test -- DashboardPage`
Expected: FAIL — module not found.

- [ ] **Step 3: Create `dashboard/src/components/DashboardPage.tsx`**

```tsx
import type { DailyOperations, OriginsResponse, WorstOffendersResponse, HourlyProfileResponse } from "../lib/types";
import { daysForMonth, isMonthInCoverage, type FilterState, type ColorBy } from "../lib/facets";
import { KpiRow } from "./KpiRow";
import { DayBarChart } from "./DayBarChart";
import { TrendPanel } from "./TrendPanel";
import { AircraftPanel } from "./AircraftPanel";
import { HourProfilePanel } from "./HourProfilePanel";
import { OriginHub } from "./OriginHub";
import { EmptyState } from "./EmptyState";

interface DashboardPageProps {
  data: DailyOperations;
  origins: OriginsResponse | null;
  offenders: WorstOffendersResponse | null;
  hourly: HourlyProfileResponse | null;
  airportLabel: string;
  month: string;
  filter: FilterState;
  colorBy: ColorBy;
  dark: boolean;
  aircraftCount: number;
  selectedOrigin: string | null;
  onSelectOrigin(icao: string | null): void;
}

export function DashboardPage(props: DashboardPageProps) {
  const { data, month, filter, colorBy, dark } = props;
  const days = daysForMonth(data, month);
  const inCoverage = isMonthInCoverage(data, month);

  return (
    <main>
      {inCoverage ? (
        <>
          <KpiRow days={days} filter={filter} aircraftCount={props.aircraftCount} />
          <DayBarChart days={days} filter={filter} colorBy={colorBy} dark={dark} />
        </>
      ) : (
        <EmptyState month={month} />
      )}
      <div className="panel-row">
        <TrendPanel data={data} activeMonth={month} />
        <HourProfilePanel hourly={props.hourly} />
        <AircraftPanel offenders={props.offenders} />
      </div>
      <OriginHub origins={props.origins} selectedOrigin={props.selectedOrigin}
        onSelectOrigin={props.onSelectOrigin} airportIcao={data.airport_icao} />
    </main>
  );
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd dashboard && npm test -- DashboardPage`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add dashboard/src/components/DashboardPage.tsx dashboard/src/components/DashboardPage.test.tsx
git commit -m "feat(dashboard): DashboardPage composing panels from props"
```

---

## Task 14: App — state, data loading, cross-filter wiring

**Files:**
- Modify: `dashboard/src/App.tsx` (replace the Task 1 placeholder)
- Test: `dashboard/src/App.test.tsx` (replace the Task 1 smoke test)

**Interfaces:**
- Consumes: `resolveAirport`, `fetchDailyOperations`, `fetchOrigins`, `fetchWorstOffenders`, `fetchHourlyProfile`; all facets helpers; `Toolbar`, `DashboardPage`, `LoadingState`, `ErrorState`; `usePrefersDark`. (Tests stub `fetch`; the app itself always fetches — no fixtures runtime mode.)
- Produces: `App` (default export). Owns: `month`, `filter` (type/locality Sets), `colorBy`, `selectedOrigin`, and the load state. Wires the hub's `onSelectOrigin` to re-fetch an origin-scoped daily payload and prefer it for the chart while a node is selected.

Behavior details the implementer must honor (the code below embodies them):
- **Initial month** = the latest month in coverage (last element of `monthsInCoverage(data)`; use index access, not `.at(-1)` — `Array.prototype.at` is ES2022 and the tsconfig lib is ES2020).
- **Filters start all-on** (`allTypes()`, `allLocalities()`).
- **Toggle handlers** never allow an empty Set — clicking the last-on chip in a group is a no-op (there must always be ≥1 type and ≥1 locality selected). The `toggleInSet` helper below enforces this.
- **Cross-filter:** when `selectedOrigin` becomes non-null, call `fetchDailyOperations(icao, { origin })`, store the result as `originData`, and prefer it over the base `data` for the page while selected; clear it when deselected. (In the App test, the stubbed `/daily-operations` route returns `DAILY_FIXTURE` regardless of the `origin` param, which is fine — the test only asserts the load/guard behaviour.)
- **aircraftCount** = `origins.origins.length + origins.other.count` when origins loaded, else `0` (a stand-in until a dedicated distinct-aircraft count exists).

- [ ] **Step 1: Write the failing test** — replace `dashboard/src/App.test.tsx`

```tsx
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import App from "./App";
import { DAILY_FIXTURE, ORIGINS_FIXTURE, WORST_OFFENDERS_FIXTURE, HOURLY_FIXTURE } from "./fixtures/klmo";

afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks(); });

function jsonResponse(body: unknown) {
  return { ok: true, status: 200, statusText: "OK", json: async () => body } as Response;
}

function stubRoutes() {
  vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL) => {
    const url = String(input);
    if (url.includes("/daily-operations")) return Promise.resolve(jsonResponse(DAILY_FIXTURE));
    if (url.includes("/origins")) return Promise.resolve(jsonResponse(ORIGINS_FIXTURE));
    if (url.includes("/worst-offenders")) return Promise.resolve(jsonResponse(WORST_OFFENDERS_FIXTURE));
    if (url.includes("/hourly-profile")) return Promise.resolve(jsonResponse(HOURLY_FIXTURE));
    return Promise.reject(new Error(`unexpected ${url}`));
  }));
}

describe("App", () => {
  it("loads and shows the toolbar and hero chart", async () => {
    stubRoutes();
    render(<App />);
    await waitFor(() => expect(screen.getByRole("region", { name: /filters/i })).toBeTruthy());
    expect(screen.getByRole("img")).toBeTruthy();
  });

  it("shows an error state and can retry when the daily fetch fails", async () => {
    vi.stubGlobal("fetch", vi.fn(() => Promise.resolve({ ok: false, status: 500, statusText: "Error", json: async () => ({}) } as Response)));
    render(<App />);
    await waitFor(() => expect(screen.getByRole("alert")).toBeTruthy());
    expect(screen.getByRole("button", { name: /retry/i })).toBeTruthy();
  });

  it("keeps at least one type selected when toggling", async () => {
    stubRoutes();
    render(<App />);
    await waitFor(() => screen.getByRole("region", { name: /filters/i }));
    // Turn off three of four types; the fourth click on the last must be a no-op.
    fireEvent.click(screen.getByRole("button", { name: /landings/i }));
    fireEvent.click(screen.getByRole("button", { name: /touch & go/i }));
    fireEvent.click(screen.getByRole("button", { name: /low approach/i }));
    const takeoffs = screen.getByRole("button", { name: /takeoffs/i });
    fireEvent.click(takeoffs); // last one — guard keeps it on
    expect(takeoffs.getAttribute("aria-pressed")).toBe("true");
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd dashboard && npm test -- App`
Expected: FAIL — App still renders the placeholder heading, no toolbar region.

- [ ] **Step 3: Replace `dashboard/src/App.tsx`**

```tsx
import { useCallback, useEffect, useMemo, useState } from "react";
import {
  fetchDailyOperations, fetchOrigins, fetchWorstOffenders, fetchHourlyProfile,
} from "./lib/api";
import type {
  DailyOperations, OriginsResponse, WorstOffendersResponse, HourlyProfileResponse, OperationType, Locality,
} from "./lib/types";
import {
  monthsInCoverage, allTypes, allLocalities, type FilterState, type ColorBy,
} from "./lib/facets";
import { resolveAirport } from "./lib/airport";
import { usePrefersDark } from "./lib/palette";
import { Toolbar } from "./components/Toolbar";
import { DashboardPage } from "./components/DashboardPage";
import { LoadingState } from "./components/LoadingState";
import { ErrorState } from "./components/ErrorState";

type Load =
  | { status: "loading" }
  | { status: "error"; message: string }
  | { status: "ready"; data: DailyOperations; origins: OriginsResponse | null;
      offenders: WorstOffendersResponse | null; hourly: HourlyProfileResponse | null };

function toggleInSet<T>(set: Set<T>, value: T): Set<T> {
  const next = new Set(set);
  if (next.has(value)) {
    if (next.size === 1) return next; // guard: never empty a filter group
    next.delete(value);
  } else {
    next.add(value);
  }
  return next;
}

export default function App() {
  const icao = useMemo(() => resolveAirport(), []);
  const dark = usePrefersDark();

  const [load, setLoad] = useState<Load>({ status: "loading" });
  const [attempt, setAttempt] = useState(0);
  const [month, setMonth] = useState<string | null>(null);
  const [filter, setFilter] = useState<FilterState>({ types: allTypes(), localities: allLocalities() });
  const [colorBy, setColorBy] = useState<ColorBy>("locality");
  const [selectedOrigin, setSelectedOrigin] = useState<string | null>(null);
  const [originData, setOriginData] = useState<DailyOperations | null>(null);

  const loadAll = useCallback(() => {
    setLoad({ status: "loading" });
    let cancelled = false;
    // Optional endpoints (origins/offenders/hourly) are allowed to fail without
    // failing the page — they degrade to "unavailable". Only the daily payload
    // is required.
    fetchDailyOperations(icao)
      .then(async (data) => {
        const [origins, offenders, hourly] = await Promise.all([
          fetchOrigins(icao).catch(() => null),
          fetchWorstOffenders(icao).catch(() => null),
          fetchHourlyProfile(icao).catch(() => null),
        ]);
        if (cancelled) return;
        setLoad({ status: "ready", data, origins, offenders, hourly });
        const months = monthsInCoverage(data);
        setMonth((m) => m ?? months[months.length - 1] ?? data.coverage.max_day.slice(0, 7));
      })
      .catch((err: unknown) => {
        if (!cancelled) setLoad({ status: "error", message: err instanceof Error ? err.message : "Unknown error." });
      });
    return () => { cancelled = true; };
  }, [icao]);

  useEffect(() => loadAll(), [loadAll, attempt]);

  // Cross-filter: fetch an origin-scoped daily payload when a hub node is picked.
  useEffect(() => {
    if (load.status !== "ready") return;
    if (!selectedOrigin) { setOriginData(null); return; }
    let cancelled = false;
    fetchDailyOperations(icao, { origin: selectedOrigin })
      .then((d) => { if (!cancelled) setOriginData(d); })
      .catch(() => { if (!cancelled) setOriginData(null); });
    return () => { cancelled = true; };
  }, [icao, selectedOrigin, load.status]);

  if (load.status === "loading") return <div className="app"><LoadingState /></div>;
  if (load.status === "error") return <div className="app"><ErrorState message={load.message} onRetry={() => setAttempt((n) => n + 1)} /></div>;

  const activeData = originData ?? load.data;
  const months = monthsInCoverage(load.data);
  // months is non-empty for any ready payload, but fall back to coverage so the
  // type stays `string` without a non-null assertion.
  const activeMonth = month ?? months[months.length - 1] ?? load.data.coverage.max_day.slice(0, 7);
  const aircraftCount = load.origins ? load.origins.origins.length + load.origins.other.count : 0;
  const airportLabel = `${load.data.airport_icao}`;

  return (
    <div className="app">
      <Toolbar
        airportLabel={airportLabel}
        month={activeMonth}
        months={months}
        onMonthChange={setMonth}
        filter={filter}
        onToggleType={(t: OperationType) => setFilter((f) => ({ ...f, types: toggleInSet(f.types, t) }))}
        onToggleLocality={(l: Locality) => setFilter((f) => ({ ...f, localities: toggleInSet(f.localities, l) }))}
        colorBy={colorBy}
        onColorByChange={setColorBy}
      />
      <DashboardPage
        data={activeData}
        origins={load.origins}
        offenders={load.offenders}
        hourly={load.hourly}
        airportLabel={airportLabel}
        month={activeMonth}
        filter={filter}
        colorBy={colorBy}
        dark={dark}
        aircraftCount={aircraftCount}
        selectedOrigin={selectedOrigin}
        onSelectOrigin={setSelectedOrigin}
      />
    </div>
  );
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd dashboard && npm test -- App`
Expected: PASS (all three cases).

- [ ] **Step 5: Run the full suite and the type-check/build**

Run: `cd dashboard && npm test && npm run build`
Expected: all tests PASS; `tsc` reports no errors; `vite build` produces `dist/`.

- [ ] **Step 6: Commit**

```bash
git add dashboard/src/App.tsx dashboard/src/App.test.tsx
git commit -m "feat(dashboard): wire state, data loading, and origin cross-filter"
```

---

## Task 15: README + finish the branch

**Files:**
- Create: `dashboard/README.md`

- [ ] **Step 1: Create `dashboard/README.md`**

```markdown
# Airfield Operations Dashboard

A multi-airport landing-operations dashboard. First host: `klmo.airfieldeconomics.org`.
Month view, one bar per day, filterable by operation type and locality, with a radial
hub of observed origins that cross-filters the page.

## Scope

**Frontend only.** The data comes from two `/v1` endpoints reached via a same-origin
`/api/*` proxy — `daily-operations` and `origins` (plus optional `worst-offenders`
and `hourly-profile`). Those endpoints and the Caddy proxy that injects the API key
are owned **outside this app**; see
`docs/superpowers/specs/2026-07-22-klmo-dashboard-design.md`. Until they exist, the
checked-in fixtures in `src/fixtures/` are the contract.

## Develop

```bash
cd dashboard
npm install
npm run dev     # http://localhost:5175 — proxies /api -> https://circlejerks.live/v1
npm test        # vitest
npm run build   # tsc + vite build -> dist/
```

The airport is resolved from the hostname (`klmo.*` -> `KLMO`); use `?airport=KXYZ`
to preview another.

## Deploy (owned separately)

Serve `dashboard/dist` and reverse-proxy `/api/*` -> the `/v1` API with the per-host
`Authorization: Bearer` key injected by Caddy. Not built by this repo's frontend code.
```

- [ ] **Step 2: Run the full verification once more**

Run: `cd dashboard && npm test && npm run build`
Expected: all PASS, clean build.

- [ ] **Step 3: Commit**

```bash
git add dashboard/README.md
git commit -m "docs(dashboard): README covering scope, dev, and deploy handoff"
```

- [ ] **Step 4: Finish the branch**

Use the `superpowers:finishing-a-development-branch` skill to choose merge/PR/cleanup.

---

## Self-Review Notes (traceability to the spec)

- **Airport from hostname** → Task 3. **Same-origin `/api`** → Task 6 + Task 1 vite proxy.
- **Locality contract (local/out_of_town/unclassified)** → Task 2 types, used everywhere; the frontend consumes the buckets, never derives them.
- **Two independent filter dimensions** → Task 5 `filteredDaySeries`/`kpis` (type ∩ locality), Task 8 toolbar, Task 14 non-empty guard.
- **Daily endpoint, one fetch, client-side facets** → Task 5 + Task 6 + Task 14.
- **Layout A (toolbar + hero chart), colour-by default locality** → Task 8/9/13.
- **Composition scope 3 (KPI, hero, trend, hour, aircraft, hub)** → Tasks 9/10/11/13.
- **Radial hub as a control (click → cross-filter)** → Task 11 + Task 14 origin re-fetch.
- **Honesty: coverage vs zero-day, error/empty states** → Task 5 `isMonthInCoverage`, Task 12 states, Task 13 branch.
- **Validated palette light+dark, legend + table view** → Task 7 (values verified with the dataviz validator), Task 9 legend + hidden table.
- **Reduced motion** → Task 7 hook, Task 11/12 `.animate` gating.
- **Fixtures as the contract; no backend tests** → Task 4, all component tests.
```
