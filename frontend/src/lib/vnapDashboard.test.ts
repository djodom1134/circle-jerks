import { describe, it, expect } from "vitest";
import { sortAircraft, radarData } from "./vnapDashboard";
import type { VnapAircraft } from "./api";

function ac(partial: Partial<VnapAircraft>): VnapAircraft {
  return {
    icao24: "x", callsign: "X", registration: null, tail: "X", aircraft_type: null,
    owner_class: "unknown", owner_source: "inferred", vnap_score: null, reports: 0,
    operations: 0, touch_and_gos: 0, cowboy_count: 0, deviation_mean_nm: null, circles: 0,
    scores: {}, ...partial,
  };
}

describe("sortAircraft", () => {
  it("sorts numeric desc with nulls last", () => {
    const rows = [ac({ icao24: "a", vnap_score: 50 }), ac({ icao24: "b", vnap_score: null }),
                  ac({ icao24: "c", vnap_score: 90 })];
    const out = sortAircraft(rows, "vnap_score", "desc").map((r) => r.icao24);
    expect(out).toEqual(["c", "a", "b"]); // 90, 50, null-last
  });
  it("sorts numeric asc with nulls still last", () => {
    const rows = [ac({ icao24: "a", operations: 5 }), ac({ icao24: "b", operations: 1 })];
    expect(sortAircraft(rows, "operations", "asc").map((r) => r.icao24)).toEqual(["b", "a"]);
  });
  it("keeps nulls last in ascending order too", () => {
    const rows = [ac({ icao24: "a", vnap_score: 50 }), ac({ icao24: "b", vnap_score: null }),
                  ac({ icao24: "c", vnap_score: 90 })];
    expect(sortAircraft(rows, "vnap_score", "asc").map((r) => r.icao24)).toEqual(["a", "c", "b"]);
  });
  it("sorts strings", () => {
    const rows = [ac({ icao24: "a", tail: "N9" }), ac({ icao24: "b", tail: "N1" })];
    expect(sortAircraft(rows, "tail", "asc").map((r) => r.tail)).toEqual(["N1", "N9"]);
  });
});

describe("radarData", () => {
  it("maps selected+average per axis, null -> 0", () => {
    const axes = ["tightness", "altitude"];
    const sel = ac({ scores: { tightness: 80, altitude: null } });
    const out = radarData(axes, sel, { tightness: 60, altitude: 40 });
    expect(out).toEqual([
      { axis: "tightness", label: "Pattern tightness", selected: 80, average: 60 },
      { axis: "altitude", label: "Altitude", selected: 0, average: 40 },
    ]);
  });
  it("handles no selection (selected 0s)", () => {
    const out = radarData(["tightness"], null, { tightness: 55 });
    expect(out[0].selected).toBe(0);
    expect(out[0].average).toBe(55);
  });
});
