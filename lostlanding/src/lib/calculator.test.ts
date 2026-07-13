import { describe, expect, it } from "vitest";
import { computeRevenue } from "./calculator";

describe("computeRevenue", () => {
  it("computes daily/monthly/annual gross revenue from the observed daily rate", () => {
    const result = computeRevenue({ runwayUses: 300, windowDays: 30, feePerUse: 10 });

    expect(result.dailyRate).toBe(10);
    expect(result.perDay).toBe(100);
    expect(result.perMonth).toBe(3000);
    expect(result.perYear).toBe(36500);
    expect(result.fiveYear).toBe(182500);
    expect(result.tenYear).toBe(365000);
  });

  it("scales linearly with the fee", () => {
    const base = computeRevenue({ runwayUses: 300, windowDays: 30, feePerUse: 10 });
    const doubled = computeRevenue({ runwayUses: 300, windowDays: 30, feePerUse: 20 });
    expect(doubled.perYear).toBe(base.perYear * 2);
  });

  it("applies an illustrative billable share as a separate, explicit assumption", () => {
    const full = computeRevenue({ runwayUses: 300, windowDays: 30, feePerUse: 10, billableShare: 1 });
    const half = computeRevenue({ runwayUses: 300, windowDays: 30, feePerUse: 10, billableShare: 0.5 });
    expect(half.perYear).toBe(full.perYear / 2);
  });

  it("does not divide by zero when the window has no days", () => {
    const result = computeRevenue({ runwayUses: 0, windowDays: 0, feePerUse: 10 });
    expect(result.dailyRate).toBe(0);
    expect(result.perYear).toBe(0);
    expect(Number.isFinite(result.perYear)).toBe(true);
  });

  it("does NOT double-count: a touch-and-go is one runway use, not two operations", () => {
    // A field with 300 runway uses in a 30-day window, all touch-and-gos.
    // summary.runway_uses is already landing + touch_and_go + low_approach --
    // it must NEVER be multiplied again by an "operations" factor of 2 for
    // touch-and-gos (FAA operations count a touch-and-go as a takeoff AND a
    // landing).
    const byType = { landing: 0, touch_and_go: 300, low_approach: 0 };
    const runwayUses = byType.landing + byType.touch_and_go + byType.low_approach;
    expect(runwayUses).toBe(300);

    const correct = computeRevenue({ runwayUses, windowDays: 30, feePerUse: 10 });

    // What you'd get if you (wrongly) multiplied by FAA "operations" instead
    // of runway uses -- each touch-and-go double-counted as takeoff+landing.
    const wronglyDoubledOperations = byType.landing + byType.touch_and_go * 2 + byType.low_approach;
    const wrong = computeRevenue({ runwayUses: wronglyDoubledOperations, windowDays: 30, feePerUse: 10 });

    expect(correct.perYear).toBe(36500);
    expect(wrong.perYear).toBe(73000);
    expect(correct.perYear).not.toBe(wrong.perYear);
    expect(correct.perYear).toBeLessThan(wrong.perYear);
  });
});
