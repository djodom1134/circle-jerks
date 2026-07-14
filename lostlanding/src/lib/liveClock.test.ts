import { describe, expect, it } from "vitest";
import { EASE_DURATION_MS, advanceClock, initClockState, reconcileClock } from "./liveClock";

describe("initClockState", () => {
  it("starts exactly at the true count, not frozen, not easing", () => {
    const state = initClockState(42);
    expect(state).toEqual({ estimatedUses: 42, frozen: false, easeFrom: null, easeTo: null, easeStartMs: null });
  });
});

describe("advanceClock", () => {
  it("ticks the estimate forward by usesPerSecond * tickSeconds", () => {
    const state = initClockState(10);
    const next = advanceClock(state, 0.05, 0.1, 1000);
    expect(next.estimatedUses).toBeCloseTo(10.005, 6);
  });

  it("stands still when the rate is zero -- no traffic in the last 20 minutes", () => {
    const state = initClockState(10);
    const next = advanceClock(state, 0, 0.1, 1000);
    expect(next.estimatedUses).toBe(10);
  });

  it("does not advance while frozen", () => {
    const state = { estimatedUses: 10, frozen: true, easeFrom: null, easeTo: null, easeStartMs: null };
    const next = advanceClock(state, 0.05, 0.1, 1000);
    expect(next).toEqual(state);
  });

  it("interpolates smoothly partway through an easing transition", () => {
    const state = { estimatedUses: 5, frozen: false, easeFrom: 5, easeTo: 10, easeStartMs: 1000 };
    const partway = advanceClock(state, 0, 0.1, 1000 + EASE_DURATION_MS / 2);
    expect(partway.estimatedUses).toBeGreaterThan(5);
    expect(partway.estimatedUses).toBeLessThan(10);
    expect(partway.easeTo).toBe(10); // still easing
  });

  it("settles exactly on the target and clears easing state once the duration elapses", () => {
    const state = { estimatedUses: 5, frozen: false, easeFrom: 5, easeTo: 10, easeStartMs: 1000 };
    const done = advanceClock(state, 0, 0.1, 1000 + EASE_DURATION_MS + 1);
    expect(done).toEqual({ estimatedUses: 10, frozen: false, easeFrom: null, easeTo: null, easeStartMs: null });
  });
});

describe("reconcileClock", () => {
  it("eases up toward a true count that has caught up to or passed the estimate", () => {
    const state = initClockState(10);
    const next = reconcileClock(state, 12, 5000, false);
    expect(next.frozen).toBe(false);
    expect(next.easeFrom).toBe(10);
    expect(next.easeTo).toBe(12);
    expect(next.easeStartMs).toBe(5000);
  });

  it("freezes in place, never snapping backwards, when the estimate drifted ahead of truth", () => {
    const state = { ...initClockState(10), estimatedUses: 10.7 };
    const next = reconcileClock(state, 10, 5000, false);
    expect(next.frozen).toBe(true);
    expect(next.estimatedUses).toBe(10.7); // held flat, not snapped down to 10
    expect(next.easeTo).toBeNull();
  });

  it("a later reconcile unfreezes and eases up once truth catches back up", () => {
    const frozen = { estimatedUses: 10.7, frozen: true, easeFrom: null, easeTo: null, easeStartMs: null };
    const next = reconcileClock(frozen, 11, 6000, false);
    expect(next.frozen).toBe(false);
    expect(next.easeFrom).toBe(10.7);
    expect(next.easeTo).toBe(11);
  });

  it("snaps directly to the true value with no easing/freezing when reduced motion is requested", () => {
    const state = { estimatedUses: 10.7, frozen: true, easeFrom: null, easeTo: null, easeStartMs: null };
    const next = reconcileClock(state, 15, 9000, true);
    expect(next).toEqual(initClockState(15));
  });
});
