import { describe, expect, it } from "vitest";
import { projectedPath, directionArrows, type LonLat } from "./patternGeometry";

const square: LonLat[] = [
  { lat: 0, lon: 0 },
  { lat: 0, lon: 0.01 },
  { lat: 0.01, lon: 0.01 },
  { lat: 0.01, lon: 0 },
];

describe("projectedPath", () => {
  it("closes the ring when closed=true", () => {
    const open = projectedPath(square, false);
    const closed = projectedPath(square, true);
    expect(closed.length).toBeGreaterThan(open.length);
  });
  it("returns projected coords for a 2-point open path", () => {
    const path = projectedPath([{ lat: 0, lon: 0 }, { lat: 0, lon: 0.01 }], false);
    expect(path.length).toBeGreaterThanOrEqual(2);
    expect(path[0].length).toBe(2);
  });
});

describe("directionArrows", () => {
  it("places the requested number of arrows with finite rotations", () => {
    const path = projectedPath(square, true);
    const arrows = directionArrows(path, 3);
    expect(arrows.length).toBe(3);
    for (const a of arrows) {
      expect(a.coord.length).toBe(2);
      expect(Number.isFinite(a.rotation)).toBe(true);
    }
  });
  it("returns no arrows for a degenerate path", () => {
    expect(directionArrows([[0, 0]], 3)).toEqual([]);
  });
});
