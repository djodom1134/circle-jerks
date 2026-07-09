import { describe, it, expect } from "vitest";
import { sortAircraft, radarData, toCsv } from "./vnapDashboard";
import type { VnapAircraft } from "./api";

function acOwner(icao24: string, owner_class: string): VnapAircraft {
  return ac({ icao24, owner_class });
}

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
  it("sorts owner_class by its displayed label, not the raw enum key", () => {
    const rows = [
      acOwner("a", "llc"),
      acOwner("b", "flight_school"),
      acOwner("c", "individual"),
    ];
    // Labels: "Flight school" < "Individual" < "LLC" alphabetically
    expect(sortAircraft(rows, "owner_class", "asc").map((r) => r.icao24)).toEqual(["b", "c", "a"]);
  });
});

describe("radarData", () => {
  it("maps selected+average per axis, null -> 0, and flags nulls per axis", () => {
    const axes = ["tightness", "altitude"];
    const sel = ac({ scores: { tightness: 80, altitude: null } });
    const out = radarData(axes, sel, { tightness: 60, altitude: 40 });
    expect(out).toEqual([
      { axis: "tightness", label: "Pattern tightness", selected: 80, average: 60, selectedNull: false, averageNull: false },
      { axis: "altitude", label: "Altitude", selected: 0, average: 40, selectedNull: true, averageNull: false },
    ]);
  });
  it("flags averageNull when the fleet average itself is missing", () => {
    const axes = ["timeofday"];
    const sel = ac({ scores: { timeofday: 70 } });
    const out = radarData(axes, sel, { timeofday: null });
    expect(out[0]).toEqual({
      axis: "timeofday", label: "Time of day", selected: 70, average: 0, selectedNull: false, averageNull: true,
    });
  });
  it("handles no selection (selected 0s, selectedNull true)", () => {
    const out = radarData(["tightness"], null, { tightness: 55 });
    expect(out[0].selected).toBe(0);
    expect(out[0].average).toBe(55);
    expect(out[0].selectedNull).toBe(true);
    expect(out[0].averageNull).toBe(false);
  });
});

describe("toCsv", () => {
  it("emits a header + one row per aircraft incl. axis scores, escaping commas", () => {
    const rows = [
      ac({ tail: "N1, Jr", aircraft_type: "C172", owner_class: "flight_school",
           vnap_score: 42.5, reports: 3, operations: 10, touch_and_gos: 4,
           cowboy_count: 1, deviation_mean_nm: 0.5, circles: 8,
           scores: { tightness: 12, altitude: null } }),
    ];
    const csv = toCsv(rows, ["tightness", "altitude"]);
    const [header, row] = csv.split("\n");
    expect(header).toBe(
      "tail,aircraft_type,owner_class,owner_source,vnap_score,reports,operations,touch_and_gos,cowboy_count,deviation_mean_nm,circles,tightness,altitude",
    );
    // comma-containing tail is quoted; null axis -> empty; axis value included
    expect(row).toBe('"N1, Jr",C172,flight_school,inferred,42.5,3,10,4,1,0.5,8,12,');
  });
});
