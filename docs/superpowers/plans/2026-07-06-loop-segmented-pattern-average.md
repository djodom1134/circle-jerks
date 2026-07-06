# Loop-Segmented Pattern Averages Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Average mode draw one closed oval + σ band per runway by segmenting real pattern laps, keeping non-loop tracks as faint context.

**Architecture:** Backend `build_circuits` cuts a lap from the archived track between two consecutive same-runway operations of one aircraft (gap 60–480 s) — a closed, touchdown-anchored loop — and tags every circuit `is_loop`. Non-loop / unpaired ops become ±75 s context slices. The frontend averages only the laps (already phase- and direction-aligned, so plain point-wise mean works) into a closed oval + annular σ band, and draws context faintly beneath.

**Tech Stack:** Python (FastAPI, sqlite) backend; React + TypeScript + OpenLayers frontend; pytest + vitest.

## Global Constraints

- Lap gap window: `MIN_LAP_S = 60`, `MAX_LAP_S = 480` seconds (verbatim).
- Context window: `CIRCUIT_WINDOW_S = 75` seconds (existing).
- `MIN_LAP_POINTS = 4` (a lap must keep ≥4 points after RDP).
- `counts_by_class` counts **laps only** (`is_loop: true`) per runway class; `context_count` counts `is_loop: false` circuits.
- No DB schema / migration change. Reads existing `operations` + `track_archive`.
- icao24 join stays lowercased (`track_archive` keys are lowercase).
- Deploy: rebuild **api** (worker shares image) + **web**, one service at a time, per `DEPLOY.md`.

---

### Task 1: Backend — lap segmentation in `build_circuits`

**Files:**
- Modify: `backend/app/pattern_circuits.py`
- Test: `backend/tests/test_pattern_circuits.py`

**Interfaces:**
- Consumes: `classify(op) -> (class_label, runway_id)`, `rdp_keep_mask`, existing `_slice_window`.
- Produces: `build_circuits(operations, tracks_by_icao, *, window_s=75, epsilon=1e-4, cap=2000, min_lap_s=60, max_lap_s=480, min_lap_points=4) -> tuple[list[dict], dict[str,int], int, int]` returning `(circuits, counts_by_class, total, context_count)`. Each circuit dict: `{"class","runway_id","icao24","is_loop","samples":[{"lat","lon","timestamp"}]}`.

- [ ] **Step 1: Add constants + `_slice_range` helper**

In `pattern_circuits.py`, add below the existing constants:

```python
RUNWAY_TYPES = ("touch_and_go", "low_approach")
MIN_LAP_S = 60
MAX_LAP_S = 480
MIN_LAP_POINTS = 4
```

And below `_slice_window`:

```python
def _slice_range(samples: list[dict], lo_ts: int, hi_ts: int) -> list[dict]:
    return [s for s in samples if lo_ts <= s["timestamp"] <= hi_ts]
```

- [ ] **Step 2: Write failing tests** (replace the three existing `build_circuits` unpackings — they now return a 4-tuple — and add lap cases)

```python
# backend/tests/test_pattern_circuits.py
import app.pattern_circuits as pc

def _track(base, n, step=5):
    # n samples every `step`s from base, tracing a little square so RDP keeps points
    pts = [(40.10, -105.10), (40.11, -105.10), (40.11, -105.11), (40.10, -105.11)]
    return [{"lat": pts[i % 4][0], "lon": pts[i % 4][1], "timestamp": base + i * step} for i in range(n)]

def test_two_consecutive_same_runway_ops_make_one_lap():
    ops = [
        {"icao24": "ABC123", "timestamp": 1000, "type": "touch_and_go", "runway_id": "29"},
        {"icao24": "ABC123", "timestamp": 1300, "type": "touch_and_go", "runway_id": "29"},
    ]
    tracks = {"abc123": _track(900, 120)}  # covers 900..1495
    circuits, counts, total, context = pc.build_circuits(ops, tracks)
    laps = [c for c in circuits if c["is_loop"]]
    assert len(laps) == 1
    assert laps[0]["class"] == "29"
    assert all(1000 <= s["timestamp"] <= 1300 for s in laps[0]["samples"])
    assert counts == {"29": 1}

def test_gap_too_large_no_lap():
    ops = [
        {"icao24": "ABC123", "timestamp": 1000, "type": "touch_and_go", "runway_id": "29"},
        {"icao24": "ABC123", "timestamp": 3000, "type": "touch_and_go", "runway_id": "29"},  # 2000s > 480
    ]
    tracks = {"abc123": _track(900, 500)}
    circuits, counts, total, context = pc.build_circuits(ops, tracks)
    assert [c for c in circuits if c["is_loop"]] == []
    assert context == 2  # both become context arcs

def test_gap_too_small_no_lap():
    ops = [
        {"icao24": "A", "timestamp": 1000, "type": "touch_and_go", "runway_id": "29"},
        {"icao24": "A", "timestamp": 1030, "type": "touch_and_go", "runway_id": "29"},  # 30s < 60
    ]
    tracks = {"a": _track(950, 40, step=3)}
    circuits, counts, total, context = pc.build_circuits(ops, tracks)
    assert [c for c in circuits if c["is_loop"]] == []

def test_different_runways_no_lap():
    ops = [
        {"icao24": "A", "timestamp": 1000, "type": "touch_and_go", "runway_id": "29"},
        {"icao24": "A", "timestamp": 1300, "type": "touch_and_go", "runway_id": "11"},
    ]
    tracks = {"a": _track(900, 120)}
    circuits, counts, total, context = pc.build_circuits(ops, tracks)
    assert [c for c in circuits if c["is_loop"]] == []
    assert context == 2

def test_chain_of_three_makes_two_laps():
    ops = [
        {"icao24": "A", "timestamp": 1000, "type": "touch_and_go", "runway_id": "29"},
        {"icao24": "A", "timestamp": 1300, "type": "touch_and_go", "runway_id": "29"},
        {"icao24": "A", "timestamp": 1600, "type": "touch_and_go", "runway_id": "29"},
    ]
    tracks = {"a": _track(900, 200)}
    circuits, counts, total, context = pc.build_circuits(ops, tracks)
    assert len([c for c in circuits if c["is_loop"]]) == 2
    assert counts == {"29": 2}
    assert context == 0

def test_circle_op_is_context_area():
    ops = [{"icao24": "A", "timestamp": 1000, "type": "circle", "runway_id": None}]
    tracks = {"a": _track(950, 30, step=3)}
    circuits, counts, total, context = pc.build_circuits(ops, tracks)
    assert circuits and circuits[0]["is_loop"] is False
    assert circuits[0]["class"] == "area"
    assert counts == {}
    assert context == 1

def test_lone_runway_op_is_context():
    ops = [{"icao24": "A", "timestamp": 1000, "type": "low_approach", "runway_id": "29"}]
    tracks = {"a": _track(950, 30, step=3)}
    circuits, counts, total, context = pc.build_circuits(ops, tracks)
    assert circuits[0]["is_loop"] is False
    assert counts == {}
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_pattern_circuits.py -x -q`
Expected: FAIL (ValueError unpacking 4-tuple / no `is_loop`).

- [ ] **Step 4: Rewrite `build_circuits`**

Replace the body of `build_circuits` with:

```python
def build_circuits(
    operations: list[dict],
    tracks_by_icao: dict[str, list[dict]],
    *,
    window_s: int = CIRCUIT_WINDOW_S,
    epsilon: float = SIMPLIFY_EPSILON_DEG,
    cap: int = DEFAULT_CIRCUIT_CAP,
    min_lap_s: int = MIN_LAP_S,
    max_lap_s: int = MAX_LAP_S,
    min_lap_points: int = MIN_LAP_POINTS,
) -> tuple[list[dict], dict[str, int], int, int]:
    """Segment closed laps between consecutive same-runway ops; everything else
    becomes a faint ±window context slice. Returns
    (circuits, counts_by_class(laps only), total, context_count)."""
    ops_sorted = sorted(operations, key=lambda o: int(o["timestamp"]))
    by_icao: dict[str, list[dict]] = {}
    for op in ops_sorted:
        by_icao.setdefault(str(op["icao24"]).lower(), []).append(op)

    def _simplify(rows: list[dict]) -> list[dict]:
        coords = [(s["lat"], s["lon"]) for s in rows]
        mask = rdp_keep_mask(coords, epsilon)
        return [s for s, keep in zip(rows, mask) if keep]

    def _emit(cls: str, rwy, icao_lower: str, is_loop: bool, ts: int, rows: list[dict]) -> dict:
        return {
            "class": cls, "runway_id": rwy, "icao24": icao_lower,
            "is_loop": is_loop, "ts": ts,
            "samples": [{"lat": s["lat"], "lon": s["lon"], "timestamp": s["timestamp"]} for s in rows],
        }

    built: list[dict] = []
    consumed: set[int] = set()  # id() of ops captured as a lap endpoint

    # Pass 1 — laps between consecutive same-runway ops.
    for icao_lower, ops in by_icao.items():
        samples = tracks_by_icao.get(icao_lower)
        if not samples:
            continue
        for a, b in zip(ops, ops[1:]):
            cls_a, rwy_a = classify(a)
            _, rwy_b = classify(b)
            if rwy_a is None or rwy_b is None or classify(b)[0] != cls_a:
                continue
            gap = int(b["timestamp"]) - int(a["timestamp"])
            if not (min_lap_s <= gap <= max_lap_s):
                continue
            lap = _slice_range(samples, int(a["timestamp"]), int(b["timestamp"]))
            if len(lap) < 2:
                continue
            kept = _simplify(lap)
            if len(kept) < min_lap_points:
                continue
            consumed.add(id(a)); consumed.add(id(b))
            built.append(_emit(cls_a, rwy_a, icao_lower, True, int(a["timestamp"]), kept))

    # Pass 2 — context (±window) for every op not consumed by a lap.
    kept_ranges: dict[tuple[str, str], list[tuple[int, int]]] = {}
    for icao_lower, ops in by_icao.items():
        samples = tracks_by_icao.get(icao_lower)
        if not samples:
            continue
        for op in ops:
            if id(op) in consumed:
                continue
            ts = int(op["timestamp"])
            cls, rwy = classify(op)
            lo, hi = ts - window_s, ts + window_s
            ranges = kept_ranges.setdefault((icao_lower, cls), [])
            if any(lo <= r_hi and r_lo <= hi for (r_lo, r_hi) in ranges):
                continue
            window = _slice_window(samples, ts, window_s)
            if len(window) < 2:
                continue
            kept = _simplify(window)
            if len(kept) < 2:
                continue
            ranges.append((lo, hi))
            built.append(_emit(cls, rwy, icao_lower, False, ts, kept))

    total = len(built)
    counts_by_class: dict[str, int] = {}
    for c in built:
        if c["is_loop"]:
            counts_by_class[c["class"]] = counts_by_class.get(c["class"], 0) + 1
    context_count = sum(1 for c in built if not c["is_loop"])
    if total > cap:
        # keep laps first, then most-recent context
        built.sort(key=lambda c: (0 if c["is_loop"] else 1, -c["ts"]))
        built = built[:cap]
    for c in built:
        del c["ts"]
    return built, counts_by_class, total, context_count
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_pattern_circuits.py -q`
Expected: PASS (all).

- [ ] **Step 6: Commit**

```bash
git add backend/app/pattern_circuits.py backend/tests/test_pattern_circuits.py
git commit -m "feat(pattern-circuits): segment closed laps between consecutive same-runway ops"
```

---

### Task 2: Backend — endpoint returns `is_loop` + `context_count`

**Files:**
- Modify: `backend/app/main.py:1434` (the `build_circuits` call + response dict)
- Test: `backend/tests/test_api.py`

**Interfaces:**
- Consumes: `build_circuits(...) -> (circuits, counts_by_class, total, context_count)` (Task 1).
- Produces: response JSON with per-circuit `is_loop` and top-level `context_count`.

- [ ] **Step 1: Write failing test** (find the existing pattern-circuits endpoint test in `test_api.py`; add assertions or a focused new test)

```python
def test_pattern_circuits_endpoint_reports_is_loop_and_context(client_with_ops):
    # (reuse whatever fixture seeds operations+track_archive in test_api.py;
    #  if none, seed two same-runway touch_and_go ops 300s apart + a circle op)
    resp = client_with_ops.get("/airports/KLMO/pattern-circuits?days=7")
    body = resp.json()
    assert "context_count" in body
    assert all("is_loop" in c for c in body["circuits"])
    assert any(c["is_loop"] for c in body["circuits"])
```

- [ ] **Step 2: Run to verify fail**

Run: `cd backend && python -m pytest tests/test_api.py -k pattern_circuits -q`
Expected: FAIL (`context_count` missing / unpack error).

- [ ] **Step 3: Update the endpoint** (`main.py`)

```python
    circuits, counts_by_class, total, context_count = pattern_circuits.build_circuits(ops, tracks_by_icao)
    return {
        "airport": {
            "icao": airport.icao,
            "lat": airport.lat,
            "lon": airport.lon,
            "elevation_ft": airport.elevation_ft,
        },
        "days": days,
        "window": {"start_ts": start_ts, "end_ts": now},
        "circuits": circuits,
        "counts_by_class": counts_by_class,
        "context_count": context_count,
        "total_circuits": total,
        "truncated": total > len(circuits),
    }
```

- [ ] **Step 4: Run to verify pass**

Run: `cd backend && python -m pytest tests/test_api.py -k pattern_circuits -q`
Expected: PASS.

- [ ] **Step 5: Run full backend suite + commit**

```bash
cd backend && python -m pytest -q && cd ..
git add backend/app/main.py backend/tests/test_api.py
git commit -m "feat(api): pattern-circuits returns is_loop + context_count"
```

---

### Task 3: Frontend geometry — closed oval + annular σ band (drop arc-era orientation)

**Files:**
- Modify: `frontend/src/lib/averagePath.ts`
- Test: `frontend/src/lib/averagePath.test.ts`

**Interfaces:**
- Consumes: `resamplePath` (unchanged).
- Produces: `meanAndBand(circuits, opts?: {resampleN?; minCount?; sigmaK?}) -> ClassAverage[]` where `ClassAverage = { class: string; count: number; mean: LonLat[]; outer: LonLat[]; inner: LonLat[] }`. `mean` traces the loop (first ≈ last); `outer`/`inner` are the ±k·σ rings.

Note: inputs are now closed, touchdown-anchored laps of one class — already phase- and direction-aligned — so **no orientation step** is needed. Remove `orientFarthestFirst`, `centroidOf`, and the `origin` option added in the earlier hotfix; make `normalAt` wrap-around (closed loop).

- [ ] **Step 1: Rewrite the `meanAndBand` describe block in the test**

```typescript
import { meanAndBand, resamplePath } from "./averagePath";

describe("meanAndBand", () => {
  // A closed unit square lap (returns to start), three identical members.
  const square = [
    { lon: 0, lat: 0 }, { lon: 1, lat: 0 }, { lon: 1, lat: 1 }, { lon: 0, lat: 1 }, { lon: 0, lat: 0 },
  ];
  const circuits = [0, 0, 0].map(() => ({ class: "29", samples: square }));

  it("produces a closed mean whose first and last points coincide", () => {
    const [avg] = meanAndBand(circuits, { resampleN: 16, minCount: 3, sigmaK: 1 });
    expect(avg.class).toBe("29");
    expect(avg.count).toBe(3);
    const first = avg.mean[0], last = avg.mean[avg.mean.length - 1];
    expect(Math.hypot(first[0] - last[0], first[1] - last[1])).toBeLessThan(0.05);
  });

  it("has zero-width band when all members are identical", () => {
    const [avg] = meanAndBand(circuits, { resampleN: 16, minCount: 3, sigmaK: 2 });
    for (let i = 0; i < avg.mean.length; i += 1) {
      expect(Math.hypot(avg.outer[i][0] - avg.inner[i][0], avg.outer[i][1] - avg.inner[i][1])).toBeCloseTo(0, 4);
    }
  });

  it("band half-width scales with sigmaK when members spread", () => {
    const spread = [ -0.02, 0, 0.02 ].map((d) => ({
      class: "29",
      samples: square.map((p) => ({ lon: p.lon, lat: p.lat + d })),
    }));
    const width = (k: number) => {
      const [a] = meanAndBand(spread, { resampleN: 16, minCount: 3, sigmaK: k });
      return Math.hypot(a.outer[0][0] - a.inner[0][0], a.outer[0][1] - a.inner[0][1]);
    };
    expect(width(2)).toBeGreaterThan(width(1) * 1.8);
    expect(width(2)).toBeLessThan(width(1) * 2.2);
  });

  it("omits a class below minCount", () => {
    const one = [{ class: "11", samples: square }];
    expect(meanAndBand(one, { minCount: 3 })).toHaveLength(0);
  });
});
```

- [ ] **Step 2: Run to verify fail**

Run: `cd frontend && npx vitest run src/lib/averagePath.test.ts`
Expected: FAIL (`outer`/`inner` undefined).

- [ ] **Step 3: Rewrite `averagePath.ts`** (keep `resamplePath` as-is; replace from `ClassAverage` down)

```typescript
export interface ClassAverage {
  class: string;
  count: number;
  mean: LonLat[];
  outer: LonLat[];
  inner: LonLat[];
}

// Wrap-around unit normal (left of tangent) at index i of a closed loop.
function normalAt(points: LonLat[], i: number): LonLat {
  const n = points.length;
  const a = points[(i - 1 + n) % n];
  const b = points[(i + 1) % n];
  const tx = b[0] - a[0];
  const ty = b[1] - a[1];
  const len = Math.hypot(tx, ty) || 1;
  return [-ty / len, tx / len];
}

// Averages closed same-class laps (phase-aligned at the runway) into a mean loop
// with a per-point cross-track ±k·σ band. Laps arrive already oriented, so no
// alignment step is needed.
export function meanAndBand(
  circuits: Array<{ class: string; samples: Array<{ lon: number; lat: number }> }>,
  opts?: { resampleN?: number; minCount?: number; sigmaK?: number }
): ClassAverage[] {
  const resampleN = opts?.resampleN ?? 48;
  const minCount = opts?.minCount ?? 3;
  const sigmaK = opts?.sigmaK ?? 1;

  const groups = new Map<string, LonLat[][]>();
  for (const circuit of circuits) {
    if (circuit.samples.length < 2) continue;
    const resampled = resamplePath(circuit.samples.map((p) => [p.lon, p.lat] as LonLat), resampleN);
    const list = groups.get(circuit.class) ?? [];
    list.push(resampled);
    groups.set(circuit.class, list);
  }

  const out: ClassAverage[] = [];
  for (const [cls, members] of groups) {
    if (members.length < minCount) continue;
    const mean: LonLat[] = [];
    for (let i = 0; i < resampleN; i += 1) {
      let sx = 0, sy = 0;
      for (const m of members) { sx += m[i][0]; sy += m[i][1]; }
      mean.push([sx / members.length, sy / members.length]);
    }
    const outer: LonLat[] = [];
    const inner: LonLat[] = [];
    for (let i = 0; i < resampleN; i += 1) {
      const [nx, ny] = normalAt(mean, i);
      let sumSq = 0;
      for (const m of members) {
        const dx = m[i][0] - mean[i][0];
        const dy = m[i][1] - mean[i][1];
        const off = dx * nx + dy * ny;
        sumSq += off * off;
      }
      const d = sigmaK * Math.sqrt(sumSq / members.length);
      outer.push([mean[i][0] + nx * d, mean[i][1] + ny * d]);
      inner.push([mean[i][0] - nx * d, mean[i][1] - ny * d]);
    }
    out.push({ class: cls, count: members.length, mean, outer, inner });
  }
  out.sort((a, b) => b.count - a.count);
  return out;
}
```

- [ ] **Step 4: Run to verify pass**

Run: `cd frontend && npx vitest run src/lib/averagePath.test.ts`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/lib/averagePath.ts frontend/src/lib/averagePath.test.ts
git commit -m "feat(geometry): closed-loop mean + annular sigma band; drop arc-era orientation"
```

---

### Task 4: Frontend render + types — ovals from laps, faint context, key/caption

**Files:**
- Modify: `frontend/src/lib/api.ts` (`PatternCircuit`, `PatternCircuitsResponse`)
- Modify: `frontend/src/components/MapView.tsx` (average render effect, add `ringPolygonFeature`, `history_context` style)
- Modify: `frontend/src/App.tsx` (key + caption)

**Interfaces:**
- Consumes: `meanAndBand(...) -> ClassAverage[]` with `mean/outer/inner` (Task 3); circuits carry `is_loop`.

- [ ] **Step 1: Add types** (`api.ts`)

In `PatternCircuit` add `is_loop: boolean;`. In `PatternCircuitsResponse` add `context_count: number;`.

- [ ] **Step 2: Add render helpers** (`MapView.tsx`)

Next to `polygonFeature`:

```typescript
function ringPolygonFeature(outer: number[][], inner: number[][], properties: Record<string, unknown>) {
  const feature = new Feature(new Polygon([outer, inner]));
  feature.setProperties(properties);
  return feature;
}
```

In `styleForHistoryFeature`, before the final `return styleForFeature(feature);`:

```typescript
  if (kind === "history_context") {
    return new Style({ stroke: new Stroke({ color: "rgba(120, 127, 140, 0.16)", width: 1 }) });
  }
```

- [ ] **Step 3: Rewrite the `historyMode === "average"` branch** (MapView average effect)

```typescript
    } else if (historyMode === "average") {
      if (!historyCircuits) return;
      const loops = historyCircuits.filter((c) => c.is_loop);
      const context = historyCircuits.filter((c) => !c.is_loop);

      // Faint context tracks beneath the ovals.
      for (const c of context) {
        if (c.samples.length < 2) continue;
        source.addFeature(lineFeature(
          smoothSegment(c.samples.map((s) => fromLonLat([s.lon, s.lat]))),
          { kind: "history_context" }
        ));
      }

      const classes = meanAndBand(
        loops.map((c) => ({ class: c.class, samples: c.samples })),
        { sigmaK: historySigmaK }
      );
      const order = classes
        .map((c) => c.class)
        .sort((a, b) => (a === "area" ? 1 : b === "area" ? -1 : Number(a.replace(/[^0-9]/g, "")) - Number(b.replace(/[^0-9]/g, ""))));
      for (const cls of classes) {
        const color = classColor(cls.class, order.indexOf(cls.class));
        const outer = cls.outer.map(([lon, lat]) => fromLonLat([lon, lat]));
        const inner = cls.inner.map(([lon, lat]) => fromLonLat([lon, lat]));
        if (outer.length >= 3 && inner.length >= 3) {
          source.addFeature(ringPolygonFeature(outer, inner, { kind: "history_band", color }));
        }
        const meanProj = cls.mean.map(([lon, lat]) => fromLonLat([lon, lat]));
        meanProj.push(meanProj[0]); // close the oval
        source.addFeature(lineFeature(smoothSegment(meanProj), { kind: "history_avg", color }));
      }
    }
```

Remove the now-unused `origin` argument (Task 3 dropped that option).

- [ ] **Step 4: Key + caption** (`App.tsx`)

In the key block (`averageDrawable`), after the runway entries `.map(...)`, append a muted context entry:

```tsx
                      {historyCircuits.context_count > 0 && (
                        <span className="history-key-item history-key-context">
                          <i className="history-key-dot cls-context" />
                          Area (context)
                        </span>
                      )}
```

Update the Average caption success line to mention laps + context, e.g.:

```tsx
                        : `${Object.values(historyCircuits.counts_by_class).reduce((a, b) => a + b, 0).toLocaleString()} laps · ${historyCircuits.context_count.toLocaleString()} context${historyCircuits.truncated ? " (capped)" : ""}`
```

Add CSS for `.history-key-context` / `.cls-context` (muted grey) matching the existing key-dot styles.

- [ ] **Step 5: Typecheck + tests + commit**

```bash
cd frontend && npx tsc --noEmit && npx vitest run && cd ..
git add frontend/src/lib/api.ts frontend/src/components/MapView.tsx frontend/src/App.tsx frontend/src/App.css
git commit -m "feat(map): Average draws closed runway ovals + annular bands over faint context"
```

---

### Task 5: Deploy + live verify

- [ ] **Step 1:** rsync working tree to droplet (per `DEPLOY.md` step 1).
- [ ] **Step 2:** Rebuild **api** then **web**, one at a time, detached under `nice`/`ionice` (per `DEPLOY.md` step 2). Worker shares the api image; recreate it too.
- [ ] **Step 3:** Verify: `docker compose ps` healthy; `curl https://circlejerks.live/healthz`; `curl ".../api/airports/KLMO/pattern-circuits?days=7"` shows `is_loop` + `context_count`; fresh web asset hash served.
- [ ] **Step 4:** Eyeball Average mode at KLMO — two closed ovals (29 & 11) with σ bands over faint context.

---

## Self-Review

**Spec coverage:** lap segmentation (T1) · is_loop/context_count endpoint (T2) · closed oval + annular band + drop arc orientation (T3) · loops-averaged/context-faint render + key/caption (T4) · deploy (T5). All spec sections covered.

**Placeholder scan:** the only soft spot is T2's fixture name `client_with_ops` — the implementer must reuse/extend the actual pattern-circuits fixture already in `test_api.py`; seeding two same-runway `touch_and_go` ops 300 s apart plus a `circle` op is specified inline.

**Type consistency:** `build_circuits` 4-tuple used identically in T1 tests, T2 endpoint. `is_loop` (bool) added in T1 dict, T4 `api.ts` type, and consumed in T4 render. `context_count` produced T1, surfaced T2, typed + used T4. `ClassAverage` gains `outer`/`inner` in T3 and is consumed in T4 (`ringPolygonFeature`). `history_context` kind emitted T4 step 3, styled T4 step 2.
