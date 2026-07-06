import { describe, expect, it } from "vitest";
import { averagePaths, bearingBucket, resamplePath } from "./averagePath";

describe("resamplePath", () => {
  it("resamples a straight line to evenly spaced points", () => {
    const out = resamplePath([[0, 0], [0, 10]], 3);
    expect(out).toHaveLength(3);
    expect(out[0]).toEqual([0, 0]);
    expect(out[2]).toEqual([0, 10]);
    expect(out[1][1]).toBeCloseTo(5, 5);
  });

  it("handles a degenerate single-point path", () => {
    const out = resamplePath([[2, 3], [2, 3]], 4);
    expect(out).toHaveLength(4);
    out.forEach((p) => expect(p).toEqual([2, 3]));
  });
});

describe("bearingBucket", () => {
  it("maps bearings into sector indices", () => {
    expect(bearingBucket(0, 8)).toBe(0);
    expect(bearingBucket(359, 8)).toBe(0);
    expect(bearingBucket(90, 8)).toBe(2);
    expect(bearingBucket(180, 4)).toBe(2);
  });
});

describe("averagePaths", () => {
  it("averages two parallel tracks into a mid path", () => {
    const airport = { lat: 0, lon: 0 };
    const tracks = [
      { samples: [{ lon: 1, lat: 1.0 }, { lon: 2, lat: 1.0 }] },
      { samples: [{ lon: 1, lat: 1.2 }, { lon: 2, lat: 1.2 }] },
    ];
    const out = averagePaths(tracks, airport, { buckets: 8, resampleN: 2, minCount: 2 });
    expect(out).toHaveLength(1);
    expect(out[0].count).toBe(2);
    // both endpoints averaged to lat 1.1
    out[0].points.forEach((p) => expect(p[1]).toBeCloseTo(1.1, 5));
  });

  it("drops buckets below minCount", () => {
    const airport = { lat: 0, lon: 0 };
    const tracks = [{ samples: [{ lon: 1, lat: 1 }, { lon: 2, lat: 1 }] }];
    expect(averagePaths(tracks, airport, { minCount: 2 })).toHaveLength(0);
  });
});
