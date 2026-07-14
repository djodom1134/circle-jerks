import { describe, expect, it } from "vitest";
import { computeBearing, resolveHeading } from "./bearing";
import type { LiveTrackSample } from "./liveTypes";

function sample(overrides: Partial<LiveTrackSample>): LiveTrackSample {
  return {
    timestamp: 0,
    lat: 40.1637,
    lon: -105.1633,
    heading_deg: null,
    altitude_ft: 5000,
    vertical_rate_fpm: 0,
    in_window: true,
    ...overrides,
  };
}

describe("computeBearing", () => {
  it("returns ~0 for due north", () => {
    const bearing = computeBearing({ lat: 40.0, lon: -105.0 }, { lat: 40.01, lon: -105.0 });
    expect(bearing).toBeCloseTo(0, 0);
  });

  it("returns ~90 for due east", () => {
    const bearing = computeBearing({ lat: 40.0, lon: -105.0 }, { lat: 40.0, lon: -104.99 });
    expect(bearing).toBeCloseTo(90, 0);
  });
});

describe("resolveHeading", () => {
  it("returns null for an empty sample list", () => {
    expect(resolveHeading([])).toBeNull();
  });

  it("uses the latest sample's own heading_deg when present", () => {
    const samples = [
      sample({ timestamp: 1, lat: 40.0, lon: -105.0, heading_deg: null }),
      sample({ timestamp: 2, lat: 40.01, lon: -105.0, heading_deg: 271 }),
    ];
    expect(resolveHeading(samples)).toBe(271);
  });

  it("falls back to a computed bearing from the last two DISTINCT positions when heading_deg is null", () => {
    const samples = [
      sample({ timestamp: 1, lat: 40.0, lon: -105.0, heading_deg: null }),
      sample({ timestamp: 2, lat: 40.01, lon: -105.0, heading_deg: null }),
    ];
    expect(resolveHeading(samples)).toBeCloseTo(0, 0); // moved due north
  });

  it("skips over a stale duplicate-position sample to find an earlier distinct one", () => {
    const samples = [
      sample({ timestamp: 1, lat: 40.0, lon: -105.0, heading_deg: null }),
      sample({ timestamp: 2, lat: 40.01, lon: -105.0, heading_deg: null }),
      // Ground-stationary duplicate immediately before the latest sample.
      sample({ timestamp: 3, lat: 40.01, lon: -105.0, heading_deg: null }),
    ];
    expect(resolveHeading(samples)).toBeCloseTo(0, 0);
  });

  it("returns null (never a fake rotation) when every sample shares the same position and no heading_deg exists", () => {
    const samples = [
      sample({ timestamp: 1, lat: 40.0, lon: -105.0, heading_deg: null }),
      sample({ timestamp: 2, lat: 40.0, lon: -105.0, heading_deg: null }),
    ];
    expect(resolveHeading(samples)).toBeNull();
  });

  it("does not require samples to be pre-sorted by timestamp", () => {
    const samples = [
      sample({ timestamp: 2, lat: 40.01, lon: -105.0, heading_deg: 99 }),
      sample({ timestamp: 1, lat: 40.0, lon: -105.0, heading_deg: null }),
    ];
    expect(resolveHeading(samples)).toBe(99);
  });
});
