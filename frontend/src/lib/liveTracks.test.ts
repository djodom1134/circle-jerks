import { describe, expect, it } from "vitest";
import { mergeLiveTracks } from "./liveTracks";
import type { Track } from "./api";

const s = (timestamp: number, lat = 0, lon = 0) => ({ timestamp, lat, lon, in_window: true });
const track = (icao24: string, callsign: string, ts: number[]): Track => ({
  icao24,
  callsign,
  samples: ts.map((t) => s(t)),
});

describe("mergeLiveTracks", () => {
  it("keeps the full base history and extends it with fresher live samples (no dup of the shared tip)", () => {
    const base = [track("a1", "N1", [100, 200, 300])];
    const live = [track("a1", "N1", [300, 310, 320])];
    const out = mergeLiveTracks(base, live);
    expect(out).toHaveLength(1);
    expect(out[0].samples.map((x) => x.timestamp)).toEqual([100, 200, 300, 310, 320]);
  });

  it("preserves base tracks with no live counterpart (historical trails don't vanish)", () => {
    const base = [track("gone", "N2", [10, 20])];
    expect(mergeLiveTracks(base, [])).toEqual(base);
  });

  it("adds live aircraft the scan has not included yet", () => {
    const out = mergeLiveTracks([], [track("new", "N3", [500])]);
    expect(out.map((t) => t.icao24)).toEqual(["new"]);
  });

  it("does not change a base track when live carries nothing newer", () => {
    const base = [track("a1", "N1", [100, 200])];
    const live = [track("a1", "N1", [150])]; // older than the base tip
    const out = mergeLiveTracks(base, live);
    expect(out[0].samples.map((x) => x.timestamp)).toEqual([100, 200]);
  });

  it("matches icao24 case-insensitively", () => {
    const base = [track("ABC123", "N1", [100])];
    const live = [track("abc123", "N1", [110])];
    const out = mergeLiveTracks(base, live);
    expect(out).toHaveLength(1);
    expect(out[0].samples.map((x) => x.timestamp)).toEqual([100, 110]);
  });
});
