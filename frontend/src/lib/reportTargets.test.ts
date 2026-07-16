import { describe, expect, it } from "vitest";
import { worstOffenderTargets, habitLabel, displayTail } from "./reportTargets";

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

describe("displayTail", () => {
  it("returns the N-number as-is when the callsign is a valid N-number", () => {
    expect(displayTail("N4632F", "a5a764")).toBe("N4632F");
    expect(displayTail("n123ab", "a5a764")).toBe("N123AB");
    expect(displayTail("  N42AB  ", "a5a764")).toBe("N42AB");
  });

  it("falls back to the uppercased hex for airline-style callsigns", () => {
    expect(displayTail("UAL237", "a5a764")).toBe("A5A764");
    expect(displayTail("AAL100", "abc123")).toBe("ABC123");
  });

  it("falls back to the uppercased hex for empty or undefined callsigns", () => {
    expect(displayTail("", "abc123")).toBe("ABC123");
    expect(displayTail(undefined, "abc123")).toBe("ABC123");
    expect(displayTail(null, "abc123")).toBe("ABC123");
  });

  it("never returns a raw hex suffix in parentheses", () => {
    expect(displayTail("UAL237", "abc123")).not.toMatch(/\(/);
  });
});
