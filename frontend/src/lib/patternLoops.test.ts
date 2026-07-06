import { describe, expect, it } from "vitest";
import { averagePatternLoops } from "./patternLoops";

const AIRPORT = { lat: 40.16, lon: -105.16 };
const COS = Math.cos((AIRPORT.lat * Math.PI) / 180);

// nm offset (east, north) from the airport -> {lat, lon}
function pt(east: number, north: number) {
  return { lat: AIRPORT.lat + north / 60, lon: AIRPORT.lon + east / (60 * COS) };
}

// A smooth oval circuit (like a real pattern) for a runway of the given true
// heading, traced `loops` times. It's elongated along the runway axis and passes
// through the threshold on heading `headingDeg` at the near turn; `cross` shifts
// the whole circuit perpendicular to the runway. Winding detection cuts on each
// completed 360deg, so `loops` full laps yield `loops - 1` detected laps (the
// trailing partial lap is dropped).
function ovalTrack(headingDeg: number, cross: number, loops: number) {
  const h = (headingDeg * Math.PI) / 180;
  const u = [Math.sin(h), Math.cos(h)];
  const q = [Math.sin(h - Math.PI / 2), Math.cos(h - Math.PI / 2)];
  const A = 1.2, B = 0.5; // semi-axes (nm) along runway / perpendicular
  const cen = [(0.5 + cross) * q[0], (0.5 + cross) * q[1]];
  const steps = 48;
  const samples: Array<{ lat: number; lon: number }> = [];
  for (let k = 0; k <= loops * steps; k += 1) {
    const phi = (k / steps) * 2 * Math.PI;
    const a = A * Math.cos(phi), b = B * Math.sin(phi);
    samples.push(pt(cen[0] + a * u[0] + b * q[0], cen[1] + a * u[1] + b * q[1]));
  }
  return { samples };
}

const RWY29 = { runway_id: "29", heading_deg: 290, lat_threshold: AIRPORT.lat, lon_threshold: AIRPORT.lon };
const RWY11 = { runway_id: "11", heading_deg: 110, lat_threshold: AIRPORT.lat, lon_threshold: AIRPORT.lon };

describe("averagePatternLoops", () => {
  it("detects repeated laps, classifies by runway, closes the mean oval", () => {
    const res = averagePatternLoops([ovalTrack(290, 0, 4)], AIRPORT, [RWY29, RWY11]);
    const c = res.classes.find((x) => x.class === "29");
    expect(c).toBeTruthy();
    expect(c!.count).toBe(3);
    const first = c!.mean[0];
    const last = c!.mean[c!.mean.length - 1];
    expect(Math.hypot(first[0] - last[0], first[1] - last[1])).toBeLessThan(0.01);
    expect(res.countsByClass["29"]).toBe(3);
  });

  it("identical members have ~zero sigma; spread raises it", () => {
    const tight = averagePatternLoops([ovalTrack(290, 0, 4)], AIRPORT, [RWY29]);
    const spread = averagePatternLoops(
      [ovalTrack(290, -0.15, 4), ovalTrack(290, 0, 4), ovalTrack(290, 0.15, 4)],
      AIRPORT,
      [RWY29]
    );
    const maxSig = (r: ReturnType<typeof averagePatternLoops>) => Math.max(...r.classes[0].sigma);
    expect(maxSig(tight)).toBeLessThan(0.02);
    expect(maxSig(spread)).toBeGreaterThan(0.1);
  });

  it("separates two runways into two classes", () => {
    const res = averagePatternLoops(
      [ovalTrack(290, 0, 4), ovalTrack(110, 0, 4)],
      AIRPORT,
      [RWY29, RWY11]
    );
    expect(res.classes.map((c) => c.class).sort()).toEqual(["11", "29"]);
  });

  it("returns no classes for a straight fly-through (no laps)", () => {
    const straight = { samples: Array.from({ length: 20 }, (_, i) => pt(-2 + i * 0.2, 0)) };
    const res = averagePatternLoops([straight], AIRPORT, [RWY29, RWY11]);
    expect(res.classes).toHaveLength(0);
    expect(res.laps).toHaveLength(0);
  });

  it("omits a class below minLaps but keeps its laps for context", () => {
    const res = averagePatternLoops([ovalTrack(290, 0, 3)], AIRPORT, [RWY29], { minLaps: 3 });
    expect(res.classes).toHaveLength(0);
    expect(res.laps.length).toBe(2); // 3 loops -> 2 detected laps
  });
});
