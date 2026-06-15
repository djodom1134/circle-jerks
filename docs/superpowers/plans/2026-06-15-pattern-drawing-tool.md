# Pattern Drawing Tool (Frontend) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let users draw, edit, and save a per-runway-end VNAP pattern directly on the OpenLayers map — click-to-place control points smoothed into a Catmull-Rom spline with direction arrows — backed by the pattern REST API from Plan 2, with a side panel for runway selection, "generate from runway", save, version history, and revert.

**Architecture:** Pure, unit-tested logic is extracted into small libs (`spline.ts`, `patternGeometry.ts`) and the API client (`api.ts`). The map gets a non-interactive **pattern display layer** (saved patterns + arrows) and, in edit mode, an **edit layer** driven by OpenLayers `Draw`(LineString) + `Modify` interactions. The control points are the LineString vertices; the Catmull-Rom smoothing is for display only. A `PatternEditorPanel` in `App.tsx` owns editing state and talks to the API. A small backend endpoint `GET /airports/{icao}/runways` feeds the runway-end selector.

**Tech Stack:** React 18, OpenLayers 10.9, TypeScript, Vite, **vitest** (configured; used for pure-logic tests). Backend: FastAPI + pytest for the one new endpoint.

This is Plan 3 of 6. It builds on Plan 2's pattern API (live and verified). Frontend pure logic is TDD with vitest; OL/React integration is gated on `tsc` + `vite build` + a runtime smoke check (no canvas unit-test harness exists).

---

### Setup (run once before Task 2)

Ensure frontend deps are installed (idempotent):

```bash
cd /Users/d/Code/FAA_circle_jerk/frontend && npm install
```

**Command cheat-sheet (frontend):** from `/Users/d/Code/FAA_circle_jerk/frontend`:
- Unit test one file: `npx vitest run src/lib/<file>.test.ts`
- Typecheck: `npx tsc --noEmit`
- Build (typecheck + bundle): `npm run build`

**Backend test (Task 1 only):** `cd /Users/d/Code/FAA_circle_jerk/backend && /Users/d/Code/FAA_circle_jerk/.venv/bin/python -m pytest tests/ -q`

### Background facts (verified)

- **API base:** `api.ts` has `export const API_BASE = ... ?? "/api"` and helpers `getJson<T>(path)`, `postJson<T>(path, body)`. There is **no `putJson`** — Task 4 adds it. Caddy strips `/api`, so backend route `/foo` is called as `getJson("/foo")`.
- **Plan 2 endpoints (browser paths, relative to `/api`):** `GET /airports/{icao}/patterns`, `GET /runways/{icao}/{rwy}/pattern`, `GET /runways/{icao}/{rwy}/pattern/template?side=left|right`, `PUT /runways/{icao}/{rwy}/pattern` (body `{points, closed, name, change_note, visitor_id}`), `GET /runways/{icao}/{rwy}/pattern/history`, `POST /runways/{icao}/{rwy}/pattern/revert` (body `{version, visitor_id}`). Pattern response shape: `{pattern: {id, icao, runway_id, version, name, locked, geometry:{points:[{lat,lon}], closed, spline}, change_note, created_at} | null}`.
- **MapView.tsx:** OL `Map` in `mapRef`, static `VectorSource` in `sourceRef`, aircraft source in `aircraftSourceRef`, `mapReady` state gates effects. `smoothSegment(points: number[][], steps=12)` (Catmull-Rom on projected coords) lives at lines ~302-330. `styleForFeature(feature)` dispatches on `feature.get("kind")`. Interactions are added in a `useEffect` gated on `mapReady` (see the `contextmenu` handler). `Draw`/`Modify`/`Snap` are NOT imported yet. `fromLonLat([lon,lat])` / `toLonLat([x,y])`.
- **App.tsx:** path-branch routing; renders `<MapView .../>` inside `<section className="map-panel">` with a `<div className="map-controls">` holding `.map-icon-toggle` buttons. `getVisitorId()` from `./lib/visitor` (localStorage). React hooks only.
- **Styling:** plain CSS in `styles.css`, vars `--ink #1b3a6b`, `--red #d64b2c`, `--line`, `--text`, `--muted`, `--panel-warm`; `.map-icon-toggle` button pattern; floating panels like `.map-context-menu` / `.wind-overlay`.
- **Backend conventions (Task 1):** `@app.get(...)`, `settings: Annotated[Settings, Depends(settings_dep)]`, `with db_session(settings.database_path) as conn:`, return plain dict. `db.runways_for_airport(conn, icao) -> list[dict]` exists. API tests use `TestClient(app)` + `monkeypatch.setenv(CIRCLEJERK_DATABASE_PATH/REDIS_URL/ENVIRONMENT)` + `get_settings.cache_clear()`.

---

## Task 1: Backend `GET /airports/{icao}/runways`

**Files:**
- Modify: `backend/app/main.py` (one GET route, near the other airport routes)
- Test: `backend/tests/test_patterns.py` (append; reuses its `api_client` helper)

- [ ] **Step 1: Append the failing test**

```python
def test_airport_runways_endpoint(tmp_path, monkeypatch):
    with api_client(tmp_path, monkeypatch) as client:
        resp = client.get("/airports/KBJC/runways")
        assert resp.status_code == 200
        data = resp.json()
        assert data["airport_icao"] == "KBJC"
        ids = {r["runway_id"] for r in data["runways"]}
        assert {"12L", "30R"} <= ids
        first = next(r for r in data["runways"] if r["runway_id"] == "12L")
        assert first["heading_deg"] == 120
        assert client.get("/airports/ZZZZ/runways").json()["runways"] == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/d/Code/FAA_circle_jerk/backend && /Users/d/Code/FAA_circle_jerk/.venv/bin/python -m pytest tests/test_patterns.py::test_airport_runways_endpoint -v`
Expected: FAIL — 404 (route not registered) → assertion error.

- [ ] **Step 3: Add the route to `backend/app/main.py`** (near the other `/airports/...` routes)

```python
@app.get("/airports/{icao}/runways")
async def get_airport_runways(
    icao: str,
    settings: Annotated[Settings, Depends(settings_dep)],
):
    with db_session(settings.database_path) as conn:
        runways = db.runways_for_airport(conn, icao)
    return {"airport_icao": icao.upper(), "runways": runways}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/d/Code/FAA_circle_jerk/backend && /Users/d/Code/FAA_circle_jerk/.venv/bin/python -m pytest tests/test_patterns.py::test_airport_runways_endpoint -v`
Expected: PASS. Then run the full backend suite to confirm no regression: `... -m pytest tests/ -q`.

- [ ] **Step 5: Commit**

```bash
git add backend/app/main.py backend/tests/test_patterns.py
git commit -m "feat: GET /airports/{icao}/runways endpoint"
```

---

## Task 2: Extract `smoothSegment` into `frontend/src/lib/spline.ts`

**Files:**
- Create: `frontend/src/lib/spline.ts`
- Create: `frontend/src/lib/spline.test.ts`
- Modify: `frontend/src/components/MapView.tsx` (remove the local `smoothSegment`, import from the new module)

- [ ] **Step 1: Write the failing test** — `frontend/src/lib/spline.test.ts`

```ts
import { describe, expect, it } from "vitest";
import { smoothSegment } from "./spline";

describe("smoothSegment", () => {
  it("returns input unchanged for fewer than 3 points", () => {
    expect(smoothSegment([[0, 0], [1, 1]])).toEqual([[0, 0], [1, 1]]);
  });

  it("interpolates extra points and keeps the first point", () => {
    const pts = [[0, 0], [1, 0], [2, 0]];
    const out = smoothSegment(pts, 4);
    expect(out.length).toBeGreaterThan(pts.length);
    expect(out[0]).toEqual([0, 0]);
    // a straight horizontal line stays at y≈0
    expect(Math.max(...out.map((p) => Math.abs(p[1])))).toBeLessThan(1e-9);
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/d/Code/FAA_circle_jerk/frontend && npx vitest run src/lib/spline.test.ts`
Expected: FAIL — cannot resolve `./spline`.

- [ ] **Step 3: Create `frontend/src/lib/spline.ts`** (move the exact body from MapView.tsx)

```ts
/** Catmull-Rom smoothing over projected [x, y] coordinates. */
export function smoothSegment(points: number[][], steps = 12): number[][] {
  if (points.length < 3) return points;
  const out: number[][] = [points[0]];
  for (let i = 0; i < points.length - 1; i += 1) {
    const p0 = points[i - 1] ?? points[i];
    const p1 = points[i];
    const p2 = points[i + 1];
    const p3 = points[i + 2] ?? points[i + 1];
    for (let s = 1; s <= steps; s += 1) {
      const t = s / steps;
      const t2 = t * t;
      const t3 = t2 * t;
      const x = 0.5 * (2 * p1[0] + (-p0[0] + p2[0]) * t + (2 * p0[0] - 5 * p1[0] + 4 * p2[0] - p3[0]) * t2 + (-p0[0] + 3 * p1[0] - 3 * p2[0] + p3[0]) * t3);
      const y = 0.5 * (2 * p1[1] + (-p0[1] + p2[1]) * t + (2 * p0[1] - 5 * p1[1] + 4 * p2[1] - p3[1]) * t2 + (-p0[1] + 3 * p1[1] - 3 * p2[1] + p3[1]) * t3);
      out.push([x, y]);
    }
  }
  return out;
}
```

In `MapView.tsx`: delete the local `function smoothSegment(...) {...}` definition and add to the imports block at the top:

```ts
import { smoothSegment } from "../lib/spline";
```

- [ ] **Step 4: Run test + typecheck to verify it passes**

Run: `cd /Users/d/Code/FAA_circle_jerk/frontend && npx vitest run src/lib/spline.test.ts && npx tsc --noEmit`
Expected: test PASS and tsc clean (MapView still references `smoothSegment` via the import).

- [ ] **Step 5: Commit**

```bash
git add frontend/src/lib/spline.ts frontend/src/lib/spline.test.ts frontend/src/components/MapView.tsx
git commit -m "refactor: extract smoothSegment into shared spline module"
```

---

## Task 3: `frontend/src/lib/patternGeometry.ts` — projected path + direction arrows

**Files:**
- Create: `frontend/src/lib/patternGeometry.ts`
- Create: `frontend/src/lib/patternGeometry.test.ts`

- [ ] **Step 1: Write the failing test** — `frontend/src/lib/patternGeometry.test.ts`

```ts
import { describe, expect, it } from "vitest";
import { projectedPath, directionArrows, type LonLat } from "./patternGeometry";

const square: LonLat[] = [
  { lat: 0, lon: 0 },
  { lat: 0, lon: 0.01 },
  { lat: 0.01, lon: 0.01 },
  { lat: 0.01, lon: 0 },
];

describe("projectedPath", () => {
  it("closes the ring when closed=true", () => {
    const open = projectedPath(square, false);
    const closed = projectedPath(square, true);
    expect(closed.length).toBeGreaterThan(open.length);
  });
  it("returns projected coords for a 2-point open path", () => {
    const path = projectedPath([{ lat: 0, lon: 0 }, { lat: 0, lon: 0.01 }], false);
    expect(path.length).toBeGreaterThanOrEqual(2);
    expect(path[0].length).toBe(2);
  });
});

describe("directionArrows", () => {
  it("places the requested number of arrows with finite rotations", () => {
    const path = projectedPath(square, true);
    const arrows = directionArrows(path, 3);
    expect(arrows.length).toBe(3);
    for (const a of arrows) {
      expect(a.coord.length).toBe(2);
      expect(Number.isFinite(a.rotation)).toBe(true);
    }
  });
  it("returns no arrows for a degenerate path", () => {
    expect(directionArrows([[0, 0]], 3)).toEqual([]);
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/d/Code/FAA_circle_jerk/frontend && npx vitest run src/lib/patternGeometry.test.ts`
Expected: FAIL — cannot resolve `./patternGeometry`.

- [ ] **Step 3: Create `frontend/src/lib/patternGeometry.ts`**

```ts
import { fromLonLat } from "ol/proj";
import { smoothSegment } from "./spline";

export interface LonLat {
  lat: number;
  lon: number;
}

/** Project control points to map coords and Catmull-Rom smooth them.
 *  When closed, the ring is wrapped back to its first point before smoothing. */
export function projectedPath(points: LonLat[], closed: boolean): number[][] {
  const coords = points.map((p) => fromLonLat([p.lon, p.lat]));
  if (closed && coords.length > 2) {
    coords.push(coords[0]);
  }
  return smoothSegment(coords);
}

export interface DirectionArrow {
  coord: number[];
  rotation: number; // radians, clockwise from north (matches ol RegularShape.rotation)
}

/** Evenly place `count` direction arrows along a projected path. */
export function directionArrows(path: number[][], count = 3): DirectionArrow[] {
  if (path.length < 2 || count < 1) return [];
  const arrows: DirectionArrow[] = [];
  for (let i = 1; i <= count; i += 1) {
    const frac = i / (count + 1);
    const idx = Math.min(path.length - 1, Math.max(1, Math.round(frac * (path.length - 1))));
    const [x1, y1] = path[idx - 1];
    const [x2, y2] = path[idx];
    const dx = x2 - x1;
    const dy = y2 - y1;
    // ol RegularShape rotation is clockwise from up (north); atan2(dx, dy) gives that.
    const rotation = Math.atan2(dx, dy);
    arrows.push({ coord: [x2, y2], rotation });
  }
  return arrows;
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/d/Code/FAA_circle_jerk/frontend && npx vitest run src/lib/patternGeometry.test.ts`
Expected: PASS (4 assertions).

- [ ] **Step 5: Commit**

```bash
git add frontend/src/lib/patternGeometry.ts frontend/src/lib/patternGeometry.test.ts
git commit -m "feat: pattern geometry helpers (projected path + direction arrows)"
```

---

## Task 4: API client — `putJson`, pattern types, and pattern functions

**Files:**
- Modify: `frontend/src/lib/api.ts` (add `putJson`, types, six functions)
- Create: `frontend/src/lib/patternApi.test.ts`

- [ ] **Step 1: Write the failing test** — `frontend/src/lib/patternApi.test.ts`

```ts
import { afterEach, describe, expect, it, vi } from "vitest";
import { getPatternTemplate, savePattern, revertPattern, getAirportRunways } from "./api";

function mockFetch(jsonBody: unknown) {
  const fn = vi.fn(async () => ({ ok: true, json: async () => jsonBody } as Response));
  vi.stubGlobal("fetch", fn);
  return fn;
}

afterEach(() => vi.unstubAllGlobals());

describe("pattern API client", () => {
  it("getPatternTemplate builds the template URL with side", async () => {
    const fn = mockFetch({ geometry: { points: [], closed: true, spline: "catmull-rom" } });
    await getPatternTemplate("KBJC", "12L", "right");
    expect(fn.mock.calls[0][0]).toContain("/api/runways/KBJC/12L/pattern/template?side=right");
  });

  it("savePattern issues a PUT with a JSON body", async () => {
    const fn = mockFetch({ pattern: null });
    await savePattern("KBJC", "12L", { points: [{ lat: 1, lon: 2 }], closed: true, visitor_id: "v-12345678" });
    const [url, init] = fn.mock.calls[0];
    expect(url).toContain("/api/runways/KBJC/12L/pattern");
    expect((init as RequestInit).method).toBe("PUT");
    expect(JSON.parse((init as RequestInit).body as string).points[0].lat).toBe(1);
  });

  it("revertPattern POSTs the version", async () => {
    const fn = mockFetch({ pattern: null });
    await revertPattern("KBJC", "12L", 2, "v-12345678");
    const [url, init] = fn.mock.calls[0];
    expect(url).toContain("/api/runways/KBJC/12L/pattern/revert");
    expect((init as RequestInit).method).toBe("POST");
    expect(JSON.parse((init as RequestInit).body as string).version).toBe(2);
  });

  it("getAirportRunways hits the runways path", async () => {
    const fn = mockFetch({ airport_icao: "KBJC", runways: [] });
    await getAirportRunways("KBJC");
    expect(fn.mock.calls[0][0]).toContain("/api/airports/KBJC/runways");
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/d/Code/FAA_circle_jerk/frontend && npx vitest run src/lib/patternApi.test.ts`
Expected: FAIL — `getPatternTemplate`/`savePattern`/`revertPattern`/`getAirportRunways` are not exported.

- [ ] **Step 3: Add to `frontend/src/lib/api.ts`**

Add a `putJson` helper next to `postJson` (mirror it):

```ts
async function putJson<T>(path: string, body: unknown, timeoutMs?: number): Promise<T> {
  const response = await fetchWithTimeout(`${API_BASE}${path}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    timeoutMs,
  });
  if (!response.ok) throw await errorFromResponse(response);
  return response.json() as Promise<T>;
}
```

Add types (near the other interfaces):

```ts
export interface PatternPoint {
  lat: number;
  lon: number;
}

export interface PatternGeometry {
  points: PatternPoint[];
  closed: boolean;
  spline: string;
}

export interface RunwayPattern {
  id: number;
  icao: string;
  runway_id: string;
  version: number;
  name: string | null;
  locked: boolean;
  geometry: PatternGeometry;
  change_note: string | null;
  created_at: number;
}

export interface PatternResponse {
  pattern: RunwayPattern | null;
}

export interface AirportPatternsResponse {
  airport_icao: string;
  patterns: RunwayPattern[];
}

export interface PatternTemplateResponse {
  geometry: PatternGeometry;
}

export interface PatternVersion {
  id: number;
  version: number;
  name: string | null;
  is_current: boolean;
  locked: boolean;
  editor_visitor_id: string | null;
  change_note: string | null;
  created_at: number;
}

export interface PatternHistoryResponse {
  versions: PatternVersion[];
}

export interface RunwayInfo {
  icao: string;
  runway_id: string;
  lat_threshold: number;
  lon_threshold: number;
  heading_deg: number;
  length_ft: number;
}

export interface AirportRunwaysResponse {
  airport_icao: string;
  runways: RunwayInfo[];
}

export interface PatternSaveBody {
  points: PatternPoint[];
  closed: boolean;
  name?: string | null;
  change_note?: string | null;
  visitor_id?: string | null;
}
```

Add the functions (near the other exported API functions):

```ts
const enc = encodeURIComponent;

export function getAirportRunways(icao: string) {
  return getJson<AirportRunwaysResponse>(`/airports/${enc(icao)}/runways`);
}

export function getAirportPatterns(icao: string) {
  return getJson<AirportPatternsResponse>(`/airports/${enc(icao)}/patterns`);
}

export function getRunwayPattern(icao: string, runwayId: string) {
  return getJson<PatternResponse>(`/runways/${enc(icao)}/${enc(runwayId)}/pattern`);
}

export function getPatternTemplate(icao: string, runwayId: string, side: "left" | "right" = "left") {
  return getJson<PatternTemplateResponse>(`/runways/${enc(icao)}/${enc(runwayId)}/pattern/template?side=${side}`);
}

export function savePattern(icao: string, runwayId: string, body: PatternSaveBody) {
  return putJson<PatternResponse>(`/runways/${enc(icao)}/${enc(runwayId)}/pattern`, body);
}

export function getPatternHistory(icao: string, runwayId: string) {
  return getJson<PatternHistoryResponse>(`/runways/${enc(icao)}/${enc(runwayId)}/pattern/history`);
}

export function revertPattern(icao: string, runwayId: string, version: number, visitorId: string | null) {
  return postJson<PatternResponse>(`/runways/${enc(icao)}/${enc(runwayId)}/pattern/revert`, {
    version,
    visitor_id: visitorId,
  });
}
```

(If `enc` already exists in the file, reuse it instead of redeclaring.)

- [ ] **Step 4: Run test + typecheck to verify it passes**

Run: `cd /Users/d/Code/FAA_circle_jerk/frontend && npx vitest run src/lib/patternApi.test.ts && npx tsc --noEmit`
Expected: PASS and tsc clean.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/lib/api.ts frontend/src/lib/patternApi.test.ts
git commit -m "feat: pattern API client (putJson, types, 6 functions)"
```

---

## Task 5: Pattern display layer in MapView (read-only render)

**Files:**
- Modify: `frontend/src/components/MapView.tsx`

Verification for this task is `npx tsc --noEmit && npm run build` (OL render isn't unit-testable here).

- [ ] **Step 1: Add a `patterns` prop and render saved patterns + arrows**

In `MapView.tsx`:

1. Extend the imports:
```ts
import { directionArrows, projectedPath } from "../lib/patternGeometry";
import type { Airport, Offender, RunwayPattern, ScanResponse, TrackSample } from "../lib/api";
```

2. Add to the `Props` interface:
```ts
  patterns?: RunwayPattern[] | null;
```

3. Add a dedicated pattern source + layer. Near `sourceRef`/`aircraftSourceRef`, add:
```ts
  const patternSourceRef = useRef<VectorSource | null>(null);
```
In the map-init `useEffect`, create the source and layer and include it in `layers` (above aircraft so arrows sit under planes):
```ts
  patternSourceRef.current = new VectorSource();
  const patternLayer = new VectorLayer({
    source: patternSourceRef.current,
    style: (feature) => styleForFeature(feature as Feature),
    zIndex: 5,
  });
```
Add `patternLayer` to the `layers: [...]` array (after `vectorLayer`, before `aircraftLayer`).

4. Add a build-features effect:
```ts
  useEffect(() => {
    const source = patternSourceRef.current;
    if (!mapReady || !source) return;
    source.clear();
    for (const pattern of patterns ?? []) {
      const path = projectedPath(pattern.geometry.points, pattern.geometry.closed);
      if (path.length < 2) continue;
      const line = new Feature(new LineString(path));
      line.set("kind", "pattern");
      line.set("runwayId", pattern.runway_id);
      source.addFeature(line);
      for (const arrow of directionArrows(path, 3)) {
        const marker = new Feature(new Point(arrow.coord));
        marker.set("kind", "pattern_arrow");
        marker.set("rotation", arrow.rotation);
        source.addFeature(marker);
      }
    }
  }, [patterns, mapReady]);
```

5. In `styleForFeature`, add cases for the two new kinds (place before the default return):
```ts
    if (kind === "pattern") {
      return new Style({
        stroke: new Stroke({ color: "#1b3a6b", width: 2.5 }),
      });
    }
    if (kind === "pattern_arrow") {
      return new Style({
        image: new RegularShape({
          points: 3,
          radius: 7,
          rotation: (feature.get("rotation") as number) ?? 0,
          fill: new Fill({ color: "#d64b2c" }),
          stroke: new Stroke({ color: "#ffffff", width: 1 }),
        }),
      });
    }
```
(`kind` is the existing `feature.get("kind")` local in `styleForFeature` — match how the function already reads it.)

- [ ] **Step 2: Typecheck + build**

Run: `cd /Users/d/Code/FAA_circle_jerk/frontend && npx tsc --noEmit && npm run build`
Expected: clean typecheck, successful build.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/MapView.tsx
git commit -m "feat: render saved VNAP patterns + direction arrows on the map"
```

---

## Task 6: Edit interactions in MapView (Draw + Modify)

**Files:**
- Modify: `frontend/src/components/MapView.tsx`

Verification: `npx tsc --noEmit && npm run build`, plus the runtime smoke check in Task 7 Step 4.

- [ ] **Step 1: Add editing props + a Draw/Modify edit layer**

In `MapView.tsx`:

1. Extend imports:
```ts
import Draw from "ol/interaction/Draw";
import Modify from "ol/interaction/Modify";
```

2. Add to `Props`:
```ts
  editingRunwayId?: string | null;
  editingPoints?: { lat: number; lon: number }[];
  editingClosed?: boolean;
  editSeedKey?: number;
  onEditingPointsChange?: (points: { lat: number; lon: number }[]) => void;
```

3. Add refs:
```ts
  const editSourceRef = useRef<VectorSource | null>(null);
  const editLineRef = useRef<Feature<LineString> | null>(null);
  const onEditChangeRef = useRef(onEditingPointsChange);
  onEditChangeRef.current = onEditingPointsChange;
```

4. In map-init `useEffect`, create the edit source + layer with `zIndex: 6` and add to `layers`:
```ts
  editSourceRef.current = new VectorSource();
  const editLayer = new VectorLayer({
    source: editSourceRef.current,
    style: (feature) => styleForFeature(feature as Feature),
    zIndex: 6,
  });
```

5. Add a helper near the other module helpers (outside the component) to read lonlat from a LineString feature:
```ts
function lineToLonLat(feature: Feature<LineString>): { lat: number; lon: number }[] {
  return feature.getGeometry()!.getCoordinates().map((c) => {
    const [lon, lat] = toLonLat(c);
    return { lat, lon };
  });
}
```

6. Add the editing effect (seed on `editSeedKey`/`editingRunwayId`, wire Draw+Modify, emit on change):
```ts
  useEffect(() => {
    const map = mapRef.current;
    const source = editSourceRef.current;
    if (!mapReady || !map || !source) return;

    source.clear();
    editLineRef.current = null;

    if (!editingRunwayId) return; // edit mode off

    const seedCoords = (editingPoints ?? []).map((p) => fromLonLat([p.lon, p.lat]));
    if (editingClosed && seedCoords.length > 2) {
      // keep the ring visually closed while editing
    }
    if (seedCoords.length >= 2) {
      const line = new Feature(new LineString(seedCoords));
      line.set("kind", "pattern_edit");
      editLineRef.current = line;
      source.addFeature(line);
    }

    const emit = () => {
      if (editLineRef.current) onEditChangeRef.current?.(lineToLonLat(editLineRef.current));
    };

    const modify = new Modify({ source });
    modify.on("modifyend", emit);
    map.addInteraction(modify);

    let draw: Draw | null = null;
    if (!editLineRef.current) {
      draw = new Draw({ source, type: "LineString" });
      draw.on("drawend", (event) => {
        const f = event.feature as Feature<LineString>;
        f.set("kind", "pattern_edit");
        editLineRef.current = f;
        if (draw) map.removeInteraction(draw);
        onEditChangeRef.current?.(lineToLonLat(f));
      });
      map.addInteraction(draw);
    }

    return () => {
      map.removeInteraction(modify);
      if (draw) map.removeInteraction(draw);
    };
    // Re-seed only when the runway or an explicit seed token changes — NOT on every
    // editingPoints update (those originate from this effect's own emits).
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mapReady, editingRunwayId, editSeedKey, editingClosed]);
```

7. In `styleForFeature`, add a `pattern_edit` case (a dashed editable line with visible vertices):
```ts
    if (kind === "pattern_edit") {
      return new Style({
        stroke: new Stroke({ color: "#d64b2c", width: 2.5, lineDash: [6, 4] }),
        image: new CircleStyle({
          radius: 5,
          fill: new Fill({ color: "#ffffff" }),
          stroke: new Stroke({ color: "#1b3a6b", width: 2 }),
        }),
      });
    }
```
(The `image` style draws a handle at each vertex for `Modify` to grab.)

- [ ] **Step 2: Typecheck + build**

Run: `cd /Users/d/Code/FAA_circle_jerk/frontend && npx tsc --noEmit && npm run build`
Expected: clean typecheck, successful build.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/MapView.tsx
git commit -m "feat: Draw/Modify pattern editing interactions on the map"
```

---

## Task 7: `PatternEditorPanel` + App.tsx wiring

**Files:**
- Create: `frontend/src/components/PatternEditorPanel.tsx`
- Modify: `frontend/src/App.tsx`
- Modify: `frontend/src/styles.css`

- [ ] **Step 1: Create `frontend/src/components/PatternEditorPanel.tsx`**

```tsx
import { useEffect, useState } from "react";
import {
  getAirportRunways,
  getPatternHistory,
  getPatternTemplate,
  getRunwayPattern,
  revertPattern,
  savePattern,
  type PatternPoint,
  type PatternVersion,
  type RunwayInfo,
} from "../lib/api";
import { getVisitorId } from "../lib/visitor";

interface Props {
  airportIcao: string;
  runwayId: string | null;
  points: PatternPoint[];
  onSelectRunway: (runwayId: string) => void;
  onSeedPoints: (points: PatternPoint[]) => void;
  onClose: () => void;
  onSaved: () => void;
}

export default function PatternEditorPanel({
  airportIcao, runwayId, points, onSelectRunway, onSeedPoints, onClose, onSaved,
}: Props) {
  const [runways, setRunways] = useState<RunwayInfo[]>([]);
  const [history, setHistory] = useState<PatternVersion[]>([]);
  const [side, setSide] = useState<"left" | "right">("left");
  const [status, setStatus] = useState<string | null>(null);

  useEffect(() => {
    getAirportRunways(airportIcao).then((r) => setRunways(r.runways)).catch(() => setRunways([]));
  }, [airportIcao]);

  useEffect(() => {
    if (!runwayId) return;
    getRunwayPattern(airportIcao, runwayId)
      .then((r) => { if (r.pattern) onSeedPoints(r.pattern.geometry.points); })
      .catch(() => undefined);
    refreshHistory();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [airportIcao, runwayId]);

  function refreshHistory() {
    if (!runwayId) return;
    getPatternHistory(airportIcao, runwayId).then((r) => setHistory(r.versions)).catch(() => setHistory([]));
  }

  async function handleTemplate() {
    if (!runwayId) return;
    const r = await getPatternTemplate(airportIcao, runwayId, side);
    onSeedPoints(r.geometry.points);
    setStatus("Template loaded — drag points to adjust.");
  }

  async function handleSave() {
    if (!runwayId || points.length < 1) return;
    setStatus("Saving…");
    try {
      await savePattern(airportIcao, runwayId, { points, closed: true, visitor_id: getVisitorId() });
      setStatus("Saved.");
      refreshHistory();
      onSaved();
    } catch (err) {
      setStatus(err instanceof Error ? err.message : "Save failed.");
    }
  }

  async function handleRevert(version: number) {
    if (!runwayId) return;
    const r = await revertPattern(airportIcao, runwayId, version, getVisitorId());
    if (r.pattern) onSeedPoints(r.pattern.geometry.points);
    setStatus(`Reverted to v${version}.`);
    refreshHistory();
    onSaved();
  }

  return (
    <div className="pattern-editor">
      <div className="pattern-editor-head">
        <strong>Edit VNAP pattern</strong>
        <button className="pattern-editor-close" onClick={onClose} aria-label="Close pattern editor">×</button>
      </div>

      <label className="pattern-editor-row">
        Runway end
        <select value={runwayId ?? ""} onChange={(e) => onSelectRunway(e.target.value)}>
          <option value="" disabled>Select…</option>
          {runways.map((r) => <option key={r.runway_id} value={r.runway_id}>{r.runway_id}</option>)}
        </select>
      </label>

      <div className="pattern-editor-row">
        <label>
          Traffic
          <select value={side} onChange={(e) => setSide(e.target.value as "left" | "right")}>
            <option value="left">Left</option>
            <option value="right">Right</option>
          </select>
        </label>
        <button className="pattern-editor-btn" onClick={handleTemplate} disabled={!runwayId}>Generate from runway</button>
      </div>

      <div className="pattern-editor-meta">{points.length} control point(s). Click the map to add, drag to move.</div>

      <div className="pattern-editor-row">
        <button className="pattern-editor-btn primary" onClick={handleSave} disabled={!runwayId || points.length < 1}>Save pattern</button>
      </div>

      {status && <div className="pattern-editor-status">{status}</div>}

      {history.length > 0 && (
        <div className="pattern-editor-history">
          <div className="pattern-editor-history-title">History</div>
          {history.map((v) => (
            <div key={v.id} className="pattern-editor-history-row">
              <span>v{v.version}{v.is_current ? " (current)" : ""}{v.change_note ? ` — ${v.change_note}` : ""}</span>
              {!v.is_current && <button className="pattern-editor-link" onClick={() => handleRevert(v.version)}>revert</button>}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
```

- [ ] **Step 2: Wire it into `frontend/src/App.tsx`**

1. Add imports:
```ts
import PatternEditorPanel from "./components/PatternEditorPanel";
import { getAirportPatterns, type PatternPoint, type RunwayPattern } from "./lib/api";
import { MapPin } from "lucide-react"; // or another available lucide icon for the toggle
```

2. Add state near the other `useState` hooks (inside `App`):
```ts
  const [patternEditing, setPatternEditing] = useState(false);
  const [editingRunwayId, setEditingRunwayId] = useState<string | null>(null);
  const [editingPoints, setEditingPoints] = useState<PatternPoint[]>([]);
  const [editSeedKey, setEditSeedKey] = useState(0);
  const [patterns, setPatterns] = useState<RunwayPattern[]>([]);

  function seedEditingPoints(points: PatternPoint[]) {
    setEditingPoints(points);
    setEditSeedKey((k) => k + 1);
  }

  function reloadPatterns() {
    if (!airport?.icao) return;
    getAirportPatterns(airport.icao).then((r) => setPatterns(r.patterns)).catch(() => setPatterns([]));
  }

  useEffect(() => { reloadPatterns(); /* eslint-disable-next-line */ }, [airport?.icao]);
```

3. Add a toggle button in the `<div className="map-controls">` block (alongside the heatmap toggle):
```tsx
  <button
    className={`map-icon-toggle${patternEditing ? " active" : ""}`}
    title="Edit VNAP patterns"
    aria-label="Edit VNAP patterns"
    onClick={() => { setPatternEditing((v) => !v); if (patternEditing) { setEditingRunwayId(null); setEditingPoints([]); } }}
    disabled={!airport?.icao}
  >
    <MapPin size={16} />
  </button>
```

4. Pass the new props to `<MapView .../>`:
```tsx
    patterns={patterns}
    editingRunwayId={patternEditing ? editingRunwayId : null}
    editingPoints={editingPoints}
    editingClosed={true}
    editSeedKey={editSeedKey}
    onEditingPointsChange={setEditingPoints}
```

5. Render the panel when editing (inside `<section className="map-panel">`, after `<MapView/>`):
```tsx
  {patternEditing && airport?.icao && (
    <PatternEditorPanel
      airportIcao={airport.icao}
      runwayId={editingRunwayId}
      points={editingPoints}
      onSelectRunway={(rwy) => { setEditingRunwayId(rwy); setEditingPoints([]); setEditSeedKey((k) => k + 1); }}
      onSeedPoints={seedEditingPoints}
      onClose={() => { setPatternEditing(false); setEditingRunwayId(null); setEditingPoints([]); }}
      onSaved={reloadPatterns}
    />
  )}
```

- [ ] **Step 3: Add styles to `frontend/src/styles.css`**

```css
.pattern-editor {
  position: absolute;
  top: 16px;
  right: 16px;
  z-index: 12;
  width: 260px;
  padding: 12px;
  background: var(--panel-warm);
  border: 1px solid var(--line);
  border-radius: 8px;
  box-shadow: 0 8px 24px rgba(15, 23, 42, 0.18);
  font-size: 13px;
  color: var(--text);
}
.pattern-editor-head { display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px; }
.pattern-editor-close { border: 0; background: transparent; font-size: 18px; cursor: pointer; color: var(--muted); }
.pattern-editor-row { display: flex; gap: 8px; align-items: center; justify-content: space-between; margin: 6px 0; }
.pattern-editor-row select { flex: 1; }
.pattern-editor-meta { font-size: 12px; color: var(--muted); margin: 6px 0; }
.pattern-editor-btn { padding: 6px 10px; border: 1px solid var(--line); border-radius: 6px; background: #fff; cursor: pointer; font-weight: 700; color: var(--ink); }
.pattern-editor-btn.primary { background: var(--ink); color: #fff; border-color: var(--ink); }
.pattern-editor-btn:disabled { opacity: 0.5; cursor: not-allowed; }
.pattern-editor-status { font-size: 12px; color: var(--ink-2); margin-top: 6px; }
.pattern-editor-history { margin-top: 10px; border-top: 1px solid var(--line); padding-top: 8px; }
.pattern-editor-history-title { font-weight: 800; font-size: 12px; margin-bottom: 4px; }
.pattern-editor-history-row { display: flex; justify-content: space-between; gap: 8px; font-size: 12px; margin: 3px 0; }
.pattern-editor-link { border: 0; background: transparent; color: var(--red); cursor: pointer; font-weight: 700; }
```

- [ ] **Step 4: Typecheck, build, and runtime smoke check**

Run: `cd /Users/d/Code/FAA_circle_jerk/frontend && npx tsc --noEmit && npm run build`
Expected: clean typecheck and successful build.

Runtime smoke check (do not skip — these tasks have no unit tests):
- Start the backend and frontend dev servers locally and confirm: the "Edit VNAP patterns" toggle appears; selecting a runway end + "Generate from runway" draws a 5-point spline with direction arrows; clicking the map adds points; dragging a vertex moves it; "Save pattern" succeeds and the saved pattern renders after reload; "History" lists versions and "revert" restores an earlier one.
- If you have the gstack/browse skill available, use it to load the dev URL and verify visually. If a runtime bug appears that the plan's code doesn't cover, STOP and report it as DONE_WITH_CONCERNS or BLOCKED with the specific symptom — do not silently paper over it.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/PatternEditorPanel.tsx frontend/src/App.tsx frontend/src/styles.css
git commit -m "feat: pattern editor panel + map wiring (draw, template, save, history, revert)"
```

---

## Self-review

**Spec coverage:** Feature 1's frontend is covered — drawing via control points smoothed to a Catmull-Rom spline (Tasks 2/3/6), direction arrows (Task 3/5), "generate standard pattern from runway" (Task 7 template button → Plan 2 template endpoint), save with `visitor_id` (Task 7 → PUT), version history + revert (Task 7), and read-only display of saved patterns so everyone can see them (Task 5). The runway-end selector is fed by the new `GET /airports/{icao}/runways` (Task 1).

**Placeholder scan:** No TBD/vague steps. Pure logic (Tasks 1–4) is TDD with real tests + commands. OL/React integration (Tasks 5–7) is gated on `tsc --noEmit` + `npm run build` and an explicit runtime smoke check, because the repo has no canvas/DOM test harness for OL — this limitation is called out, not hidden.

**Type consistency:** `RunwayPattern.geometry` (`PatternGeometry{points,closed,spline}`) flows from `api.ts` → MapView `projectedPath(pattern.geometry.points, pattern.geometry.closed)` → `directionArrows`. Editing emits `{lat,lon}[]` via `onEditingPointsChange`; App stores it in `editingPoints` and passes it back with `editSeedKey` to control re-seeding. `savePattern` body matches Plan 2's `PatternSaveRequest` (`points, closed, name?, change_note?, visitor_id?`). `getVisitorId()` supplies `visitor_id`.

**Known risk / deferred:** The controlled/uncontrolled editing seed loop is handled with `editSeedKey` (re-seed only on explicit seed, not on every emit) — this is the most likely place for a runtime bug and is why Task 7 mandates a runtime smoke check. Self-intersection/again-closed-loop validation and a dedicated "delete pattern" UI are deferred (YAGNI; revert + overwrite cover iteration). Per-vertex insertion/deletion uses OL `Modify`'s built-in behavior.
