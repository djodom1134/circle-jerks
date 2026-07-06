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
  // Three horizontal circuits at lat 1.0, 1.1, 1.2 -> mean lat 1.1.
  const circuits = [1.0, 1.1, 1.2].map((lat) => ({
    class: "29",
    samples: [{ lon: 0, lat }, { lon: 1, lat }, { lon: 2, lat }],
  }));

  it("averages same-class circuits into a mean between them", () => {
    const out = meanAndBand(circuits, { resampleN: 3, minCount: 3, sigmaK: 1 });
    expect(out).toHaveLength(1);
    expect(out[0].class).toBe("29");
    expect(out[0].count).toBe(3);
    out[0].mean.forEach((p) => expect(p[1]).toBeCloseTo(1.1, 6));
  });

  it("band half-width scales with sigmaK", () => {
    const width = (k: number) => {
      const [avg] = meanAndBand(circuits, { resampleN: 3, minCount: 3, sigmaK: k });
      // band ring = forward offsets then reversed backward offsets; sample the
      // vertical spread at the first mean point vs its mirrored ring point.
      const top = avg.band[0][1];
      const bottom = avg.band[avg.band.length - 1][1];
      return Math.abs(top - bottom);
    };
    const w1 = width(1);
    const w2 = width(2);
    expect(w2).toBeGreaterThan(w1 * 1.8);
    expect(w2).toBeLessThan(w1 * 2.2);
  });

  it("omits a class below minCount", () => {
    const one = [{ class: "11", samples: [{ lon: 0, lat: 0 }, { lon: 1, lat: 0 }] }];
    expect(meanAndBand(one, { minCount: 3 })).toHaveLength(0);
  });
});
