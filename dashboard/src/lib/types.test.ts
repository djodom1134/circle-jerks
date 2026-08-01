import { describe, expect, it } from "vitest";
import { OPERATION_TYPES, LOCALITIES } from "./types";
import type { DailyOperations } from "./types";

describe("contract types", () => {
  it("enumerates the four operation types and three localities", () => {
    expect(OPERATION_TYPES).toEqual(["landing", "touch_and_go", "low_approach", "takeoff"]);
    expect(LOCALITIES).toEqual(["local", "out_of_town", "unclassified"]);
  });

  it("accepts a well-formed daily payload", () => {
    const payload: DailyOperations = {
      airport_icao: "KLMO",
      timezone: "America/Denver",
      coverage: { min_day: "2026-07-01", max_day: "2026-07-03" },
      types: ["landing", "touch_and_go", "low_approach", "takeoff"],
      localities: ["local", "out_of_town", "unclassified"],
      days: [
        { date: "2026-07-01", counts: { landing: { local: 1, out_of_town: 0, unclassified: 0 },
          touch_and_go: { local: 0, out_of_town: 0, unclassified: 0 },
          low_approach: { local: 0, out_of_town: 0, unclassified: 0 },
          takeoff: { local: 0, out_of_town: 0, unclassified: 0 } } },
      ],
    };
    expect(payload.days).toHaveLength(1);
  });
});
