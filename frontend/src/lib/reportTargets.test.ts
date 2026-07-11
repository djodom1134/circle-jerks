import { describe, expect, it } from "vitest";
import { worstOffenderTargets, habitLabel } from "./reportTargets";

describe("worstOffenderTargets", () => {
  it("keeps the worst N by vnap_score * circles", () => {
    const rows = [
      { icao24: "a", vnap_score: 40, circles: 2 },  // 80
      { icao24: "b", vnap_score: 20, circles: 10 }, // 200
      { icao24: "c", vnap_score: 90, circles: 1 },  // 90
    ];
    expect(worstOffenderTargets(rows, 2).map((r) => r.icao24)).toEqual(["b", "c"]);
  });

  it("treats null vnap_score as 0 and does not mutate input", () => {
    const rows = [{ icao24: "a", vnap_score: null, circles: 9 }, { icao24: "b", vnap_score: 10, circles: 1 }];
    const copy = [...rows];
    expect(worstOffenderTargets(rows, 1).map((r) => r.icao24)).toEqual(["b"]);
    expect(rows).toEqual(copy);
  });
});

describe("habitLabel", () => {
  it("maps axes to human phrases with a fallback", () => {
    expect(habitLabel("altitude")).toMatch(/low/i);
    expect(habitLabel("timeofday")).toMatch(/quiet/i);
    expect(habitLabel(null)).toBe("Repeat pattern flyer");
    expect(habitLabel("unknown_axis")).toBe("Repeat pattern flyer");
  });
});
