import { describe, expect, it } from "vitest";
import {
  DEFAULT_RENDER_CLOCK_CONFIG,
  createRenderClock,
  fleetNewestTimestamp,
} from "./renderClock";

const track = (...ts: number[]) => ({ samples: ts.map((timestamp) => ({ timestamp })) });
const { marginSeconds: MARGIN, minDelaySeconds: MIN, maxDelaySeconds: MAX } =
  DEFAULT_RENDER_CLOCK_CONFIG;

describe("fleetNewestTimestamp", () => {
  it("returns the max sample timestamp across all tracks", () => {
    expect(fleetNewestTimestamp([track(100, 300, 200), track(150, 420, 90)])).toBe(420);
  });

  it("returns 0 when there are no tracks or no samples", () => {
    expect(fleetNewestTimestamp([])).toBe(0);
    expect(fleetNewestTimestamp([track()])).toBe(0);
  });
});

describe("createRenderClock", () => {
  const NOW = 1_000_000;

  it("advances the render clock with wall time between data updates (smooth motion)", () => {
    // The core property the freeze bug violated: with no new data, displayTime
    // must keep advancing so markers glide instead of stepping/freezing.
    const clock = createRenderClock();
    clock.onData(NOW - 6, NOW); // freshest sample 6s old
    const a = clock.displayTime(NOW);
    const b = clock.displayTime(NOW + 5);
    expect(b - a).toBeCloseTo(5, 6);
  });

  it("never clamps the freshest plane: displayTime stays behind the newest sample", () => {
    // Across the coverable lag range (up to maxDelay - margin), the freshest
    // sample must always be ahead of the render clock so that plane
    // interpolates rather than freezes. (Beyond maxDelay the delay is
    // deliberately capped and very-stale planes are allowed to clamp — they are
    // near the freshness cutoff anyway.)
    for (const lag of [2, 8, 15, 25, 35]) {
      const clock = createRenderClock();
      const fleetNewest = NOW - lag;
      clock.onData(fleetNewest, NOW);
      expect(clock.displayTime(NOW)).toBeLessThanOrEqual(fleetNewest - MARGIN);
    }
  });

  it("sizes the delay to lag + margin, clamped to [min, max]", () => {
    const fresh = createRenderClock();
    fresh.onData(NOW - 1, NOW); // 1s lag -> 1+margin below the floor
    expect(fresh.delaySeconds).toBe(MIN);

    const mid = createRenderClock();
    mid.onData(NOW - 20, NOW); // 20s lag
    expect(mid.delaySeconds).toBe(20 + MARGIN);

    const extreme = createRenderClock();
    extreme.onData(NOW - 300, NOW); // absurd lag -> capped
    expect(extreme.delaySeconds).toBe(MAX);
  });

  it("jumps the delay up instantly on a lag spike, then decays it back down", () => {
    const clock = createRenderClock();
    clock.onData(NOW - 5, NOW);
    const baseline = clock.delaySeconds;
    // Spike: freshest sample suddenly 30s old.
    clock.onData(NOW + 1 - 30, NOW + 1);
    expect(clock.delaySeconds).toBeGreaterThan(baseline);
    const peaked = clock.delaySeconds;
    // Data recovers to fresh; the peak should decay (latency recovers) over time.
    clock.onData(NOW + 60 - 5, NOW + 60);
    expect(clock.delaySeconds).toBeLessThan(peaked);
  });

  it("falls back to the floor delay before any data has arrived", () => {
    const clock = createRenderClock();
    expect(clock.displayTime(NOW)).toBe(NOW - MIN);
  });
});
