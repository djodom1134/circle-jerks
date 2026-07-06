import { describe, expect, it } from "vitest";
import { meanAndBand, resamplePath } from "./averagePath";

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

describe("meanAndBand", () => {
  // A closed unit-square lap (returns to its start), three identical members.
  const square = [
    { lon: 0, lat: 0 }, { lon: 1, lat: 0 }, { lon: 1, lat: 1 }, { lon: 0, lat: 1 }, { lon: 0, lat: 0 },
  ];
  const circuits = [0, 0, 0].map(() => ({ class: "29", samples: square }));

  it("produces a closed mean whose first and last points coincide", () => {
    const [avg] = meanAndBand(circuits, { resampleN: 16, minCount: 3, sigmaK: 1 });
    expect(avg.class).toBe("29");
    expect(avg.count).toBe(3);
    const first = avg.mean[0];
    const last = avg.mean[avg.mean.length - 1];
    expect(Math.hypot(first[0] - last[0], first[1] - last[1])).toBeLessThan(0.05);
  });

  it("has zero-width band when all members are identical", () => {
    const [avg] = meanAndBand(circuits, { resampleN: 16, minCount: 3, sigmaK: 2 });
    for (let i = 0; i < avg.mean.length; i += 1) {
      const w = Math.hypot(avg.outer[i][0] - avg.inner[i][0], avg.outer[i][1] - avg.inner[i][1]);
      expect(w).toBeCloseTo(0, 4);
    }
  });

  it("band half-width scales with sigmaK when members spread", () => {
    const spread = [-0.02, 0, 0.02].map((d) => ({
      class: "29",
      samples: square.map((p) => ({ lon: p.lon, lat: p.lat + d })),
    }));
    const width = (k: number) => {
      const [a] = meanAndBand(spread, { resampleN: 16, minCount: 3, sigmaK: k });
      return Math.hypot(a.outer[0][0] - a.inner[0][0], a.outer[0][1] - a.inner[0][1]);
    };
    const w1 = width(1);
    const w2 = width(2);
    expect(w2).toBeGreaterThan(w1 * 1.8);
    expect(w2).toBeLessThan(w1 * 2.2);
  });

  it("omits a class below minCount", () => {
    const one = [{ class: "11", samples: square }];
    expect(meanAndBand(one, { minCount: 3 })).toHaveLength(0);
  });
});
