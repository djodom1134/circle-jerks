/**
 * Heading resolution for the live map's plane icons.
 *
 * `heading_deg` on a live position sample is frequently null (not every
 * ADS-B feed/aircraft broadcasts it). The fallback order, per spec:
 *   1. The sample's own `heading_deg`, if present.
 *   2. A great-circle bearing computed from the last two samples that have
 *      DISTINCT lat/lon (a stationary aircraft on the ground produces
 *      identical consecutive points, which have no well-defined bearing).
 *   3. Null -- the caller points the icon north and does NOT fake a rotation.
 */
import type { LiveTrackSample } from "./liveTypes";

/** Initial great-circle bearing from `from` to `to`, in degrees [0, 360). */
export function computeBearing(
  from: { lat: number; lon: number },
  to: { lat: number; lon: number },
): number {
  const fromLat = (from.lat * Math.PI) / 180;
  const toLat = (to.lat * Math.PI) / 180;
  const deltaLon = ((to.lon - from.lon) * Math.PI) / 180;
  const y = Math.sin(deltaLon) * Math.cos(toLat);
  const x = Math.cos(fromLat) * Math.sin(toLat) - Math.sin(fromLat) * Math.cos(toLat) * Math.cos(deltaLon);
  return ((Math.atan2(y, x) * 180) / Math.PI + 360) % 360;
}

function samePosition(a: { lat: number; lon: number }, b: { lat: number; lon: number }): boolean {
  return a.lat === b.lat && a.lon === b.lon;
}

/**
 * Resolve a heading for the LATEST sample in `samples` (which need not be
 * sorted). Returns null when no heading can be determined honestly --
 * callers must point the icon north and skip rotation, never invent one.
 */
export function resolveHeading(samples: LiveTrackSample[]): number | null {
  if (samples.length === 0) return null;
  const sorted = [...samples].sort((a, b) => a.timestamp - b.timestamp);
  const latest = sorted[sorted.length - 1];
  if (latest.heading_deg != null && Number.isFinite(latest.heading_deg)) {
    return latest.heading_deg;
  }

  // Walk backward from the latest sample for the nearest earlier sample with
  // a DISTINCT lat/lon -- two identical points have no bearing.
  for (let i = sorted.length - 2; i >= 0; i -= 1) {
    const candidate = sorted[i];
    if (!samePosition(candidate, latest)) {
      return computeBearing(candidate, latest);
    }
  }
  return null;
}
