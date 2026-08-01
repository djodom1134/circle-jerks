import { describe, expect, it } from "vitest";
import { DAILY_FIXTURE, ORIGINS_FIXTURE } from "./klmo";
import { OPERATION_TYPES, LOCALITIES } from "../lib/types";

describe("fixtures", () => {
  it("daily fixture is internally consistent with the contract", () => {
    expect(DAILY_FIXTURE.types).toEqual([...OPERATION_TYPES]);
    expect(DAILY_FIXTURE.localities).toEqual([...LOCALITIES]);
    expect(DAILY_FIXTURE.days.length).toBeGreaterThan(28);
    for (const day of DAILY_FIXTURE.days) {
      for (const t of OPERATION_TYPES) {
        for (const l of LOCALITIES) {
          expect(typeof day.counts[t][l]).toBe("number");
        }
      }
    }
  });

  it("contains at least one genuine zero-day inside coverage", () => {
    const zero = DAILY_FIXTURE.days.find((d) =>
      OPERATION_TYPES.every((t) => LOCALITIES.every((l) => d.counts[t][l] === 0)),
    );
    expect(zero).toBeTruthy();
  });

  it("origins fixture is ranked descending by arrivals", () => {
    const a = ORIGINS_FIXTURE.origins.map((o) => o.arrivals);
    expect([...a].sort((x, y) => y - x)).toEqual(a);
  });
});
