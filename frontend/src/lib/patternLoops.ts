// Reconstructs averaged traffic-pattern ovals from full ADS-B flight tracks.
//
// Each flight's track is clipped to the pattern area, split into individual laps
// by winding number (each ~360deg of turning is one lap), classified by the
// nearest airport runway, phase-aligned at the runway threshold, and averaged
// per runway into a closed mean loop with a per-point cross-track sigma. This is
// the data the "Lines" overlay already fetches (track-history), so Average needs
// no extra request. Validated offline against KLMO before shipping.

export type LonLat = [number, number];

export interface LoopRunway {
  runway_id: string;
  heading_deg: number; // true heading of the landing direction
  lat_threshold: number;
  lon_threshold: number;
}

export interface LoopSample {
  lat: number;
  lon: number;
}

export interface LoopClassAverage {
  class: string;
  count: number;
  mean: LonLat[];
  sigma: number[]; // cross-track std-dev per mean point, in nautical miles
}

export interface LoopResult {
  classes: LoopClassAverage[];
  laps: LonLat[][]; // every accepted lap polyline, for faint context rendering
  countsByClass: Record<string, number>;
}

const NM_PER_DEG = 60;

function angDiff(a: number, b: number): number {
  let d = Math.abs(a - b) % 360;
  return d > 180 ? 360 - d : d;
}

// signed turn from heading a to b, in (-180, 180]
function turn(a: number, b: number): number {
  let d = ((b - a) % 360 + 360) % 360;
  return d > 180 ? d - 360 : d;
}

function makeGeo(lat0: number) {
  const kx = Math.cos((lat0 * Math.PI) / 180); // lon-degree -> lat-degree scale
  return {
    kx,
    distNm: (a: LoopSample, b: LoopSample) =>
      Math.hypot((a.lon - b.lon) * kx, a.lat - b.lat) * NM_PER_DEG,
    headingDeg: (a: LoopSample, b: LoopSample) => {
      const d = (Math.atan2((b.lon - a.lon) * kx, b.lat - a.lat) * 180) / Math.PI;
      return ((d % 360) + 360) % 360;
    },
    signedArea: (pts: LoopSample[]) => {
      let s = 0;
      for (let i = 0; i < pts.length; i += 1) {
        const p = pts[i];
        const q = pts[(i + 1) % pts.length];
        s += p.lon * kx * q.lat - q.lon * kx * p.lat;
      }
      return s;
    },
  };
}

function resampleClosed(pts: LoopSample[], n: number, kx: number): LonLat[] {
  const ring = [...pts, pts[0]];
  const cum = [0];
  for (let i = 1; i < ring.length; i += 1) {
    const a = ring[i - 1];
    const b = ring[i];
    cum.push(cum[i - 1] + Math.hypot((b.lon - a.lon) * kx, b.lat - a.lat));
  }
  const total = cum[cum.length - 1];
  const out: LonLat[] = [];
  if (total === 0) {
    for (let i = 0; i < n; i += 1) out.push([pts[0].lon, pts[0].lat]);
    return out;
  }
  let seg = 1;
  for (let i = 0; i < n; i += 1) {
    const target = (total * i) / n;
    while (seg < ring.length - 1 && cum[seg] < target) seg += 1;
    const t = (target - cum[seg - 1]) / (cum[seg] - cum[seg - 1] || 1);
    const a = ring[seg - 1];
    const b = ring[seg];
    out.push([a.lon + (b.lon - a.lon) * t, a.lat + (b.lat - a.lat) * t]);
  }
  return out;
}

function detectLaps(
  samples: LoopSample[],
  airport: LoopSample,
  geo: ReturnType<typeof makeGeo>,
  clipNm: number
): LoopSample[][] {
  // clip to pattern area, drop consecutive duplicates that would inject noise
  const clipped: LoopSample[] = [];
  for (const p of samples) {
    if (geo.distNm(p, airport) > clipNm) continue;
    const prev = clipped[clipped.length - 1];
    if (prev && geo.distNm(prev, p) < 1e-4) continue;
    clipped.push(p);
  }
  const laps: LoopSample[][] = [];
  if (clipped.length < 8) return laps;
  let cum = 0;
  let start = 0;
  let prevb: number | null = null;
  for (let i = 1; i < clipped.length; i += 1) {
    const b = geo.headingDeg(clipped[i - 1], clipped[i]);
    if (prevb !== null) cum += turn(prevb, b);
    prevb = b;
    if (Math.abs(cum) >= 360) {
      const seg = clipped.slice(start, i + 1);
      start = i;
      cum = 0;
      prevb = null;
      if (seg.length < 8) continue;
      const lons = seg.map((p) => p.lon);
      const lats = seg.map((p) => p.lat);
      const span = Math.hypot(
        (Math.max(...lons) - Math.min(...lons)) * geo.kx,
        Math.max(...lats) - Math.min(...lats)
      ) * NM_PER_DEG;
      if (span < 1.2 || span > 6) continue;
      if (geo.distNm(seg[0], seg[seg.length - 1]) > 0.7) continue;
      laps.push(seg);
    }
  }
  return laps;
}

function classify(
  lap: LoopSample[],
  airport: LoopSample,
  runways: LoopRunway[],
  geo: ReturnType<typeof makeGeo>
): LoopRunway | null {
  let ci = 0;
  let best = Infinity;
  for (let k = 0; k < lap.length; k += 1) {
    const d = geo.distNm(lap[k], airport);
    if (d < best) {
      best = d;
      ci = k;
    }
  }
  const a = lap[Math.max(0, ci - 1)];
  const b = lap[Math.min(lap.length - 1, ci + 1)];
  const hc = geo.headingDeg(a, b);
  let bestR: LoopRunway | null = null;
  let bestD = Infinity;
  for (const r of runways) {
    const dd = angDiff(hc, r.heading_deg);
    if (dd < bestD) {
      bestD = dd;
      bestR = r;
    }
  }
  return bestD <= 45 ? bestR : null;
}

export function averagePatternLoops(
  tracks: Array<{ samples: LoopSample[] }>,
  airport: LoopSample,
  runways: LoopRunway[],
  opts?: { clipNm?: number; resampleN?: number; minLaps?: number }
): LoopResult {
  const clipNm = opts?.clipNm ?? 3;
  const N = opts?.resampleN ?? 72;
  const minLaps = opts?.minLaps ?? 3;
  const geo = makeGeo(airport.lat);
  const rwById = new Map(runways.map((r) => [r.runway_id, r]));

  const byClass = new Map<string, LoopSample[][]>();
  const laps: LonLat[][] = [];
  for (const track of tracks) {
    for (const lap of detectLaps(track.samples, airport, geo, clipNm)) {
      const r = classify(lap, airport, runways, geo);
      if (!r) continue;
      laps.push(lap.map((p) => [p.lon, p.lat] as LonLat));
      const list = byClass.get(r.runway_id) ?? [];
      list.push(lap);
      byClass.set(r.runway_id, list);
    }
  }

  const classes: LoopClassAverage[] = [];
  const countsByClass: Record<string, number> = {};
  for (const [rid, members] of byClass) {
    if (members.length < minLaps) continue;
    const rwy = rwById.get(rid)!;
    const thr = { lat: rwy.lat_threshold, lon: rwy.lon_threshold };
    const positive = members.filter((m) => geo.signedArea(m) >= 0).length;
    const sign = positive >= members.length / 2 ? 1 : -1;
    const aligned = members.map((m) => {
      const oriented = (geo.signedArea(m) >= 0 ? 1 : -1) === sign ? m : [...m].reverse();
      let k0 = 0;
      let best = Infinity;
      for (let k = 0; k < oriented.length; k += 1) {
        const d = geo.distNm(oriented[k], thr);
        if (d < best) {
          best = d;
          k0 = k;
        }
      }
      return resampleClosed([...oriented.slice(k0), ...oriented.slice(0, k0)], N, geo.kx);
    });
    const mean: LonLat[] = [];
    for (let i = 0; i < N; i += 1) {
      let sx = 0;
      let sy = 0;
      for (const a of aligned) {
        sx += a[i][0];
        sy += a[i][1];
      }
      mean.push([sx / aligned.length, sy / aligned.length]);
    }
    const sigma: number[] = [];
    for (let i = 0; i < N; i += 1) {
      const a = mean[(i - 1 + N) % N];
      const b = mean[(i + 1) % N];
      const tx = (b[0] - a[0]) * geo.kx;
      const ty = b[1] - a[1];
      const L = Math.hypot(tx, ty) || 1;
      const nx = -ty / L;
      const ny = tx / L;
      let sq = 0;
      for (const al of aligned) {
        const dx = (al[i][0] - mean[i][0]) * geo.kx;
        const dy = al[i][1] - mean[i][1];
        const off = dx * nx + dy * ny;
        sq += off * off;
      }
      sigma.push(Math.sqrt(sq / aligned.length) * NM_PER_DEG);
    }
    classes.push({ class: rid, count: members.length, mean, sigma });
    countsByClass[rid] = members.length;
  }
  classes.sort((a, b) => b.count - a.count);
  return { classes, laps, countsByClass };
}
