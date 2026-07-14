import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { RealVisits } from "./RealVisits";
import { SAMPLE_LEDGER_FIXTURE, ZERO_LEDGER_FIXTURE } from "../test/fixtures";
import type { LedgerResponse } from "../lib/types";

afterEach(() => cleanup());

describe("RealVisits", () => {
  it("makes the runway-use-isn't-a-visit argument explicit as supporting copy", () => {
    render(<RealVisits data={SAMPLE_LEDGER_FIXTURE} />);
    expect(screen.getByText(/who actually stopped/i)).toBeTruthy();
    expect(
      screen.getByText(/brings visitors who spend money in town/i),
    ).toBeTruthy();
    expect(screen.getByText(/that is a claim, and it is measurable/i)).toBeTruthy();
  });

  it("computes the never-stopped share from by_type, not a hardcoded figure", () => {
    // SAMPLE fixture: low_approach 30 + touch_and_go 150 = 180 of 300 runway uses = 60%.
    render(<RealVisits data={SAMPLE_LEDGER_FIXTURE} />);
    expect(screen.getByText("60%")).toBeTruthy();
    expect(screen.getByText(/30 low approaches/i)).toBeTruthy();
    expect(screen.getByText(/150 touch-and-goes/i)).toBeTruthy();
    expect(screen.getByText(/only 120 were landings at all/i)).toBeTruthy();
  });

  it("states paired and landings together (N of M), never paired alone", () => {
    render(<RealVisits data={SAMPLE_LEDGER_FIXTURE} />);
    // visits.paired = 105, visits.landings = 120.
    expect(screen.getByText(/105 of 120/i)).toBeTruthy();
  });

  it("shows the stayed/quick-turn split with its own honest shares and the median stay", () => {
    render(<RealVisits data={SAMPLE_LEDGER_FIXTURE} />);
    expect(screen.getByText(/stayed 20\+ minutes/i)).toBeTruthy();
    expect(screen.getByText(/quick turn, under 20 minutes/i)).toBeTruthy();
    // 40 stayed / 105 paired ~= 38%; 65 quick_turn / 105 paired ~= 62%. These
    // percentages sit inside longer sentences, so match by substring (regex),
    // not exact node text.
    expect(screen.getByText(/38%/)).toBeTruthy();
    expect(screen.getByText(/62%/)).toBeTruthy();
    // median_stay_seconds: 2400 -> "40m 0s"
    expect(screen.getByText(/40m 0s/)).toBeTruthy();
  });

  it("never claims a definite count of aircraft that 'visited' -- only what was measured", () => {
    render(<RealVisits data={SAMPLE_LEDGER_FIXTURE} />);
    const text = document.body.textContent?.toLowerCase() ?? "";
    expect(text).not.toMatch(/only \d+ aircraft (visited|stopped)/);
    // The judgment-call framing must be explicit, not dressed up as a standard.
    expect(text).toContain("judgment call");
    expect(text).not.toMatch(/faa (standard|requirement) .*20.minute/);
  });

  it("states the coverage gap plainly and calls the stayed figure a floor, not an estimate", () => {
    render(<RealVisits data={SAMPLE_LEDGER_FIXTURE} />);
    // visits.coverage = 0.875 -> 88% (rounded)
    expect(screen.getByText(/88%/)).toBeTruthy();
    expect(screen.getByText(/higher than 40, not lower/i)).toBeTruthy();
    expect(screen.getByText(/floor, not an estimate/i)).toBeTruthy();
  });

  it("renders an honest zero banner, not a fabricated split, when nothing could be paired", () => {
    const noPairs: LedgerResponse = {
      ...SAMPLE_LEDGER_FIXTURE,
      visits: {
        min_seconds: 1200,
        stayed: 0,
        quick_turn: 0,
        paired: 0,
        landings: 120,
        coverage: 0,
        median_stay_seconds: null,
      },
    };
    render(<RealVisits data={noPairs} />);
    expect(screen.getByText(/no landing-to-takeoff pairs to measure/i)).toBeTruthy();
    expect(screen.queryByText(/stayed 20\+ minutes/i)).toBeNull();
  });

  it("handles the fully-zero window without dividing by zero", () => {
    render(<RealVisits data={ZERO_LEDGER_FIXTURE} />);
    // 0 of 0 runway uses never-stopped-share falls back to 0%, not NaN.
    expect(screen.getByText("0%")).toBeTruthy();
    expect(screen.getByText(/no landing-to-takeoff pairs to measure/i)).toBeTruthy();
  });
});
