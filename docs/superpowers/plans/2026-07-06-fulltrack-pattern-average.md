# Full-Track Pattern Averages Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:executing-plans / subagent-driven-development. Steps use `- [ ]`.

**Supersedes:** the backend loop-segmentation approach
(`2026-07-06-loop-segmented-pattern-average*.md`). That approach hit a data wall:
the `pattern-circuits` endpoint only stores ±75 s arcs around each op, which
never close and cluster near the runway. The rich data lives in `track-history`
(full ADS-B flights, 11 s cadence) — already fetched by the frontend for Lines
mode. The design was **validated by an offline prototype** (approved preview):
KLMO rwy 29 = 44 laps → closed oval (0.11 nm closure, 3.16 nm span), rwy 11 = 31
laps (0.09 nm, 2.72 nm), long axis aligned to the runway. Reference:
`$CLAUDE_JOB_DIR/tmp/proto.py`.

**Goal:** Average mode reconstructs one closed oval + σ band per runway by
detecting real pattern laps in the full ADS-B tracks it already has.

**Architecture:** Pure frontend. A new `patternLoops.ts` module detects laps
(winding number over tracks clipped to the pattern area), classifies each by the
nearest airport runway (real `heading_deg`/threshold), phase-aligns at the
threshold, and averages into a mean loop + per-point cross-track σ. App runs it
(memoized) from the already-fetched `historyData.tracks` + the airport's runways,
passes the result to MapView for rendering, and derives the caption/key counts
from it. Average no longer uses the `pattern-circuits` endpoint.

**Tech Stack:** React + TypeScript + OpenLayers; vitest.

## Global Constraints

- Clip radius `CLIP_NM = 3`, resample `N = 72`, `MIN_LAPS_PER_CLASS = 3`.
- Lap accept filters: span ∈ [1.2, 6] nm, start→end closure < 0.7 nm, ≥ 8 samples.
- σ is returned **per-point in nm** (not pre-scaled); the σ control scales the
  band at render (`outer/inner = mean ± sigmaK·σ` along the local normal), so
  changing k needs no recompute.
- Classification uses real runways (`RunwayInfo.heading_deg`, true heading — no
  magnetic/declination guessing); a lap whose final heading is > 45° from every
  runway is dropped.
- No backend change. The `pattern-circuits` endpoint is left in place but unused
  by Average (follow-up: remove it and revert `pattern_circuits.py`).

---

### Task 1: `patternLoops.ts` — detect, classify, average

**Files:**
- Create: `frontend/src/lib/patternLoops.ts`
- Test: `frontend/src/lib/patternLoops.test.ts`

**Interfaces (Produces):**
```ts
export type LonLat = [number, number];
export interface LoopRunway { runway_id: string; heading_deg: number; lat_threshold: number; lon_threshold: number; }
export interface LoopSample { lat: number; lon: number; }
export interface LoopClassAverage { class: string; count: number; mean: LonLat[]; sigma: number[]; } // sigma[i] in nm
export interface LoopResult { classes: LoopClassAverage[]; laps: LonLat[][]; countsByClass: Record<string, number>; }
export function averagePatternLoops(
  tracks: Array<{ samples: LoopSample[] }>,
  airport: { lat: number; lon: number },
  runways: LoopRunway[],
  opts?: { clipNm?: number; resampleN?: number; minLaps?: number }
): LoopResult;
```

**Algorithm (port `proto.py`):**
- Local nm metric around `airport.lat` (`kx = cos(lat0)`).
- Per track: keep samples within `clipNm` of the airport; need ≥ 8. Accumulate
  signed heading change; each |Σ| ≥ 360° cuts one lap. Accept a lap if
  span ∈ [1.2,6] nm, closure < 0.7 nm.
- Classify: heading of travel at the lap's closest-to-airport sample → the runway
  with the smallest angular difference to `heading_deg`; drop if > 45°.
- Per class with ≥ `minLaps`: ensure consistent winding sign (flip the minority),
  phase-align each lap by rotating to its point closest to that runway's
  threshold, resample-closed to `N`, average → `mean`; per point, cross-track σ
  (nm) = stddev of members' signed normal offset. Sort classes by count desc.
- `laps` = every accepted lap polyline (for faint rendering). `countsByClass` =
  lap count per drawn class.

- [ ] **Step 1 (RED):** write `patternLoops.test.ts` with these cases, run, watch fail:
  - Three synthetic square laps near a threshold on heading 290 → one class `29`,
    mean closes (first≈last < 0.05), `sigma` all ≈ 0.
  - Same laps offset ±0.005° cross-track → `sigma[i]` > 0 and scales with the offset.
  - A straight fly-through track (no winding) → 0 laps, empty classes.
  - Laps split across two runways (headings 290 and 110) → two classes `29`,`11`.
  - A class with < `minLaps` laps → omitted from `classes` (but its laps still in `laps`).
  Run: `npx vitest run src/lib/patternLoops.test.ts` → FAIL (module missing).
- [ ] **Step 2 (GREEN):** implement `patternLoops.ts` porting `proto.py`. Run tests → PASS.
- [ ] **Step 3:** `npx tsc --noEmit` clean. Commit.

### Task 2: Wire into App + MapView; drop pattern-circuits from Average

**Files:**
- Modify: `frontend/src/App.tsx` (fetch runways in history mode; `useMemo` the loop result from `historyData.tracks`; pass to MapView; caption/key from it; remove `getPatternCircuits`/`historyCircuits`/`historyCircuitsError` and its effect).
- Modify: `frontend/src/components/MapView.tsx` (Average branch renders the passed `LoopResult`: faint laps → `history_context`; per class annulus band `outer/inner = mean ± sigmaK·σ·normal` → `history_band`; closed mean line → `history_avg`. Remove `historyCircuits`/`meanAndBand` usage).
- Modify: `frontend/src/lib/api.ts` — reuse existing `getAirportRunways`/`RunwayInfo` (no change) ; may delete now-unused `PatternCircuit*` types in a follow-up.

**Interfaces (Consumes):** `averagePatternLoops(...) -> LoopResult` (Task 1); existing `getAirportRunways(icao) -> {runways: RunwayInfo[]}`; `historyData.tracks: HistoricalTrack[]` (already fetched); `RunwayInfo{runway_id,heading_deg,lat_threshold,lon_threshold}`.

- [ ] **Step 1:** App — add `historyRunways` state + fetch `getAirportRunways(airport.icao)` in the existing `mapOverlay==="history"` effect (alongside track-history). Add
  `const historyAverage = useMemo(() => (historyData && historyMode==="average" && historyRunways) ? averagePatternLoops(historyData.tracks, airport, historyRunways, {sigmaK not needed}) : null, [historyData, historyMode, historyRunways, airport])`.
- [ ] **Step 2:** App — pass `historyAverage={mapOverlay==="history" && historyMode==="average" ? historyAverage : null}` and keep `historySigmaK` to MapView. Replace the `historyCircuits`-based `averageDrawable`/key/caption with `historyAverage.countsByClass` (`averageDrawable = historyAverage && historyAverage.classes.length>0`; caption e.g. `"{Σcounts} laps · {runway list}"`; key from `countsByClass`; a "context" faint note if `laps.length>Σcounts`). Remove `historyCircuits`, `historyCircuitsError`, `getPatternCircuits` import, and the pattern-circuits effect.
- [ ] **Step 3:** MapView — replace prop `historyCircuits` with `historyAverage: LoopResult | null`. Rewrite the `historyMode==="average"` branch:
  - faint: `for (lap of historyAverage.laps) addFeature(lineFeature(smoothSegment(lap.map(([lon,lat])=>fromLonLat([lon,lat]))), {kind:"history_context"}))`.
  - per class (ordered runways-first): compute `outer/inner` from `mean` + `sigmaK·sigma[i]` along the wrap-around normal (nm→lonlat), `ringPolygonFeature` band + closed `history_avg` mean line. (Reuse `ringPolygonFeature`, `classColor`, `history_context`/`history_band`/`history_avg` styles added earlier.)
  - Delete the `meanAndBand` import/use for Average. `averagePath.ts` may stay (unused) or be removed in follow-up.
- [ ] **Step 4:** `npx tsc --noEmit` clean; `npx vitest run` all pass; `npx vite build` succeeds. Commit.

### Task 3: Deploy + verify (frontend-only → web rebuild)

- [ ] rsync; rebuild **web** only (detached, `nice`/`ionice`) per `DEPLOY.md`.
- [ ] Verify healthz + fresh bundle; open KLMO Average → two closed ovals (29 blue, 11 amber) + σ bands over faint laps, matching the approved preview.

## Self-Review

- Coverage: detect+classify+average (T1) · App/MapView wiring + drop pattern-circuits (T2) · deploy (T3). Matches the approved preview.
- Types: `LoopResult{classes,laps,countsByClass}` produced T1, consumed T2 (App memo → MapView prop). `sigma[]` per-point nm produced T1, scaled by `sigmaK` at render T2/MapView. `RunwayInfo` fields used for classify (heading_deg) + align (thresholds).
- Placeholders: caption/key exact strings finalized in T2 step 2; band normal math mirrors `averagePath`'s `normalAt` (wrap-around) scaled by `sigma[i]`.
