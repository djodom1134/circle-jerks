// Groups many tracks into a few "typical" representative paths for the
// historical-density Average mode. Bucketing is by the compass bearing from
// the airport to each track's centroid, so inbound corridors from different
// directions don't get averaged together into mush.

export type LonLat = [number, number];

export interface AveragedPath {
  points: LonLat[];
  count: number;
}

function dist2(a: { lon: number; lat: number }, b: { lon: number; lat: number }): number {
  const dx = a.lon - b.lon;
  const dy = a.lat - b.lat;
  return dx * dx + dy * dy;
}

export function resamplePath(points: LonLat[], n: number): LonLat[] {
  if (n < 2 || points.length === 0) {
    return points.slice(0, Math.max(1, n));
  }
  if (points.length === 1) {
    return Array.from({ length: n }, () => [points[0][0], points[0][1]] as LonLat);
  }
  const cum: number[] = [0];
  for (let i = 1; i < points.length; i += 1) {
    const dx = points[i][0] - points[i - 1][0];
    const dy = points[i][1] - points[i - 1][1];
    cum.push(cum[i - 1] + Math.hypot(dx, dy));
  }
  const total = cum[cum.length - 1];
  if (total === 0) {
    return Array.from({ length: n }, () => [points[0][0], points[0][1]] as LonLat);
  }
  const out: LonLat[] = [];
  let seg = 1;
  for (let i = 0; i < n; i += 1) {
    const target = (total * i) / (n - 1);
    while (seg < points.length - 1 && cum[seg] < target) seg += 1;
    const segStart = cum[seg - 1];
    const segLen = cum[seg] - segStart || 1;
    const t = Math.max(0, Math.min(1, (target - segStart) / segLen));
    out.push([
      points[seg - 1][0] + (points[seg][0] - points[seg - 1][0]) * t,
      points[seg - 1][1] + (points[seg][1] - points[seg - 1][1]) * t,
    ]);
  }
  return out;
}

export function bearingBucket(bearingDeg: number, buckets: number): number {
  const norm = ((bearingDeg % 360) + 360) % 360;
  const offset = (360 / buckets) / 2;
  const adjusted = (norm + offset) % 360;
  return Math.floor((adjusted / 360) * buckets) % buckets;
}

function bearingFromAirport(
  airport: { lat: number; lon: number },
  point: { lat: number; lon: number }
): number {
  const dLon = ((point.lon - airport.lon) * Math.PI) / 180;
  const lat1 = (airport.lat * Math.PI) / 180;
  const lat2 = (point.lat * Math.PI) / 180;
  const y = Math.sin(dLon) * Math.cos(lat2);
  const x = Math.cos(lat1) * Math.sin(lat2) - Math.sin(lat1) * Math.cos(lat2) * Math.cos(dLon);
  return (Math.atan2(y, x) * 180) / Math.PI;
}

export function averagePaths(
  tracks: Array<{ samples: Array<{ lon: number; lat: number }> }>,
  airport: { lat: number; lon: number },
  opts?: { buckets?: number; resampleN?: number; minCount?: number }
): AveragedPath[] {
  const buckets = opts?.buckets ?? 8;
  const resampleN = opts?.resampleN ?? 40;
  const minCount = opts?.minCount ?? 3;

  const groups = new Map<number, LonLat[][]>();
  for (const track of tracks) {
    const samples = track.samples;
    if (samples.length < 2) continue;
    const centroid = {
      lon: samples.reduce((s, p) => s + p.lon, 0) / samples.length,
      lat: samples.reduce((s, p) => s + p.lat, 0) / samples.length,
    };
    const bucket = bearingBucket(bearingFromAirport(airport, centroid), buckets);
    // Orient so index 0 is the endpoint farther from the airport.
    const oriented = dist2(samples[0], airport) >= dist2(samples[samples.length - 1], airport)
      ? samples
      : samples.slice().reverse();
    const resampled = resamplePath(oriented.map((p) => [p.lon, p.lat] as LonLat), resampleN);
    const list = groups.get(bucket) ?? [];
    list.push(resampled);
    groups.set(bucket, list);
  }

  const out: AveragedPath[] = [];
  for (const list of groups.values()) {
    if (list.length < minCount) continue;
    const avg: LonLat[] = [];
    for (let i = 0; i < resampleN; i += 1) {
      let sx = 0;
      let sy = 0;
      for (const path of list) {
        sx += path[i][0];
        sy += path[i][1];
      }
      avg.push([sx / list.length, sy / list.length]);
    }
    out.push({ points: avg, count: list.length });
  }
  out.sort((a, b) => b.count - a.count);
  return out;
}
