import { describe, expect, it } from "vitest";
import { latestPositions } from "./livePositions";
import type { LiveTrack, LiveTrackSample } from "./liveTypes";

function sample(overrides: Partial<LiveTrackSample>): LiveTrackSample {
  return {
    timestamp: 0,
    lat: 40.16,
    lon: -105.16,
    heading_deg: null,
    altitude_ft: 5000,
    vertical_rate_fpm: 0,
    in_window: true,
    ...overrides,
  };
}

function track(icao24: string, samples: LiveTrackSample[], callsign = "N1TEST"): LiveTrack {
  return { icao24, callsign, samples };
}

describe("latestPositions", () => {
  it("skips tracks with no samples", () => {
    expect(latestPositions([track("a1", [])])).toEqual([]);
  });

  it("picks the sample with the highest timestamp regardless of array order", () => {
    const t = track("a1", [
      sample({ timestamp: 200, lat: 40.2, lon: -105.2, heading_deg: 90 }),
      sample({ timestamp: 100, lat: 40.1, lon: -105.1, heading_deg: 45 }),
    ]);
    const [pos] = latestPositions([t]);
    expect(pos.timestamp).toBe(200);
    expect(pos.lat).toBe(40.2);
    expect(pos.heading).toBe(90);
  });

  it("resolves a null heading via bearing fallback from prior distinct samples", () => {
    const t = track("a1", [
      sample({ timestamp: 100, lat: 40.0, lon: -105.0, heading_deg: null }),
      sample({ timestamp: 200, lat: 40.01, lon: -105.0, heading_deg: null }),
    ]);
    const [pos] = latestPositions([t]);
    expect(pos.heading).toBeCloseTo(0, 0); // moved due north
  });

  it("returns one position per track, in track order", () => {
    const tracks = [
      track("a1", [sample({ timestamp: 1 })]),
      track("a2", [sample({ timestamp: 1 })]),
    ];
    const positions = latestPositions(tracks);
    expect(positions.map((p) => p.icao24)).toEqual(["a1", "a2"]);
  });
});
