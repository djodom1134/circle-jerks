import { afterEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, render, screen } from "@testing-library/react";
import { useLiveClock } from "./useLiveClock";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

function stubMatchMedia(matches: boolean) {
  vi.stubGlobal(
    "matchMedia",
    vi.fn().mockImplementation((query: string) => ({
      matches,
      media: query,
      addEventListener: () => {},
      removeEventListener: () => {},
    })),
  );
}

function Probe({ trueUses, usesPerSecond }: { trueUses: number | null; usesPerSecond: number }) {
  const estimated = useLiveClock(trueUses, usesPerSecond);
  return <div data-testid="estimate">{estimated.toFixed(4)}</div>;
}

describe("useLiveClock", () => {
  it("initializes at the true count and does not move when the rate is zero", () => {
    stubMatchMedia(false);
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval", "Date", "performance"] });

    render(<Probe trueUses={10} usesPerSecond={0} />);
    expect(screen.getByTestId("estimate").textContent).toBe("10.0000");

    act(() => {
      vi.advanceTimersByTime(2000);
    });
    expect(screen.getByTestId("estimate").textContent).toBe("10.0000");
  });

  it("ticks upward over time at the given rate", () => {
    stubMatchMedia(false);
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval", "Date", "performance"] });

    render(<Probe trueUses={10} usesPerSecond={1} />); // 1 use/sec -> 0.1 per 100ms tick

    act(() => {
      vi.advanceTimersByTime(500); // 5 ticks
    });
    expect(Number(screen.getByTestId("estimate").textContent)).toBeCloseTo(10.5, 1);
  });

  it("never ticks at all under prefers-reduced-motion", () => {
    stubMatchMedia(true);
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval", "Date", "performance"] });

    render(<Probe trueUses={10} usesPerSecond={5} />);
    act(() => {
      vi.advanceTimersByTime(5000);
    });
    expect(screen.getByTestId("estimate").textContent).toBe("10.0000");
  });

  it("reconciles to a new true value on prop change instead of continuing to drift freely", () => {
    stubMatchMedia(false);
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval", "Date", "performance"] });

    const { rerender } = render(<Probe trueUses={10} usesPerSecond={0} />);
    rerender(<Probe trueUses={50} usesPerSecond={0} />);

    // Reconcile eases up over EASE_DURATION_MS (400ms); after that it must
    // have arrived at the new true value.
    act(() => {
      vi.advanceTimersByTime(1000);
    });
    expect(Number(screen.getByTestId("estimate").textContent)).toBeCloseTo(50, 1);
  });
});
