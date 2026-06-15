import { describe, expect, it } from "vitest";
import { smoothSegment } from "./spline";

describe("smoothSegment", () => {
  it("returns input unchanged for fewer than 3 points", () => {
    expect(smoothSegment([[0, 0], [1, 1]])).toEqual([[0, 0], [1, 1]]);
  });

  it("interpolates extra points and keeps the first point", () => {
    const pts = [[0, 0], [1, 0], [2, 0]];
    const out = smoothSegment(pts, 4);
    expect(out.length).toBeGreaterThan(pts.length);
    expect(out[0]).toEqual([0, 0]);
    // a straight horizontal line stays at y≈0
    expect(Math.max(...out.map((p) => Math.abs(p[1])))).toBeLessThan(1e-9);
  });
});
