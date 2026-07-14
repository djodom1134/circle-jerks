/**
 * Reduces a live-positions response's per-track sample history down to what
 * the map actually renders: one LATEST position per aircraft, plus its
 * resolved heading (see bearing.ts for the null-heading fallback rules).
 * Kept framework-free so it is directly unit-testable without mounting OL.
 */
import { resolveHeading } from "./bearing";
import type { LiveTrack } from "./liveTypes";

export interface LatestAircraftPosition {
  icao24: string;
  callsign: string;
  lat: number;
  lon: number;
  timestamp: number;
  heading: number | null;
}

export function latestPositions(tracks: LiveTrack[]): LatestAircraftPosition[] {
  const out: LatestAircraftPosition[] = [];
  for (const track of tracks) {
    if (track.samples.length === 0) continue;
    const latest = [...track.samples].sort((a, b) => b.timestamp - a.timestamp)[0];
    out.push({
      icao24: track.icao24,
      callsign: track.callsign,
      lat: latest.lat,
      lon: latest.lon,
      timestamp: latest.timestamp,
      heading: resolveHeading(track.samples),
    });
  }
  return out;
}
