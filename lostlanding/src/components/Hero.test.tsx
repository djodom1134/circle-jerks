import { afterEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import { Hero } from "./Hero";
import { FeeProvider, useFee } from "../lib/feeContext";
import { AircraftFeesProvider } from "../lib/aircraftFeesContext";
import { SAMPLE_LEDGER_FIXTURE } from "../test/fixtures";
import type { AircraftFeesResponse } from "../lib/liveTypes";

const { fetchAircraftFeesMock } = vi.hoisted(() => ({ fetchAircraftFeesMock: vi.fn() }));

vi.mock("../lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../lib/api")>();
  return { ...actual, fetchAircraftFees: fetchAircraftFeesMock };
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

// uses_per_second: 0 -- the debt clock's tick rate is zero, so the displayed
// figure is deterministic (rollingUses * fee) regardless of real wall-clock
// time elapsed during the test, with no reliance on faking timers.
const FEES_FIXTURE: AircraftFeesResponse = {
  airport_icao: "KLMO",
  timezone: "America/Denver",
  counting_since: 1750003200, // 2025-06-15 UTC-ish
  today: { window: "rolling_24h", since_ts: 0, until_ts: 1, runway_uses: 4 },
  rate_window: { seconds: 1200, runway_uses: 0, uses_per_second: 0 },
  aircraft: {
    a1: { tail: "N1AA", total: 50, today: 2, month: 20, year: 50 },
    a2: { tail: "N2BB", total: 30, today: 2, month: 10, year: 30 },
  },
};

function FeeBumpButton() {
  const { setFee } = useFee();
  return (
    <button type="button" onClick={() => setFee(20)}>
      bump fee
    </button>
  );
}

function renderHero(fees: AircraftFeesResponse = FEES_FIXTURE, withBumpButton = false) {
  fetchAircraftFeesMock.mockResolvedValue(fees);
  return render(
    <FeeProvider initialFee={10}>
      <AircraftFeesProvider icao="KLMO" pollIntervalMs={999_999}>
        {withBumpButton && <FeeBumpButton />}
        <Hero data={SAMPLE_LEDGER_FIXTURE} />
      </AircraftFeesProvider>
    </FeeProvider>,
  );
}

describe("Hero", () => {
  it("leads with the ticking dollar figure as the headline, reconciled to today's rolling-24h count", async () => {
    renderHero();
    expect(screen.getByText(/we missed out on/i)).toBeTruthy();

    // 4 rolling-24h runway uses * $10 fee = $40.00 (rate is zero, so exact).
    await waitFor(() => expect(screen.getByText("$40.00")).toBeTruthy());
    expect(screen.getByText(/the last 24 hours of traffic/i)).toBeTruthy();
  });

  it("shows the fee assumption adjacent to the headline, tied to the live slider value", async () => {
    renderHero();
    await waitFor(() => expect(screen.getByText("$40.00")).toBeTruthy());
    expect(document.querySelector(".hero-fee-line")?.textContent).toMatch(/at \$10 per runway use/i);
  });

  it("frames the figure as illustrative, never as money owed or a proposal", async () => {
    renderHero();
    await waitFor(() => expect(screen.getByText("$40.00")).toBeTruthy());
    expect(screen.getByText(/illustrative gross revenue/i)).toBeTruthy();
    expect(screen.getByText(/not money owed, not collected/i)).toBeTruthy();
  });

  it("explains the ticking mechanism and says so plainly when there's no recent activity", async () => {
    renderHero();
    await waitFor(() => expect(screen.getByText(/reconciled to the counted total/i)).toBeTruthy());
    expect(screen.getByText(/no runway use in the last 20 minutes/i)).toBeTruthy();
  });

  it("carries companion month + since-counting figures, honestly labeled and never called 'lifetime'", async () => {
    renderHero();
    // month: (20 + 10) = 30 uses * $10 = $300; since counting: (50 + 30) = 80 * $10 = $800
    await waitFor(() => expect(screen.getByText("$300")).toBeTruthy());
    expect(screen.getByText("$800")).toBeTruthy();
    expect(screen.getByText(/this month/i)).toBeTruthy();
    expect(screen.getByText(/since we started counting/i)).toBeTruthy();
    expect(document.body.textContent?.toLowerCase()).not.toContain("lifetime");
  });

  it("keeps the counting-methodology story (untowered, FAA 5010 estimates, floor language) as supporting copy", async () => {
    renderHero();
    await waitFor(() => expect(screen.getByText("$40.00")).toBeTruthy());
    expect(screen.getByText(/untowered/i)).toBeTruthy();
    expect(screen.getByText(/FAA Form 5010/i)).toBeTruthy();
    expect(screen.getAllByText(/floor, never an estimate/i).length).toBeGreaterThan(0);
  });

  it("reacts instantly to the shared fee slider with no extra plumbing", async () => {
    renderHero(FEES_FIXTURE, true);
    await waitFor(() => expect(screen.getByText("$40.00")).toBeTruthy());

    await act(async () => {
      screen.getByRole("button", { name: /bump fee/i }).click();
    });

    // 4 rolling-24h uses * new $20 fee = $80.00
    await waitFor(() => expect(screen.getByText("$80.00")).toBeTruthy());
  });

  it("shows an em dash placeholder, not a fabricated dollar figure, before the first successful load", () => {
    fetchAircraftFeesMock.mockReturnValue(new Promise(() => {})); // never resolves
    render(
      <FeeProvider>
        <AircraftFeesProvider icao="KLMO">
          <Hero data={SAMPLE_LEDGER_FIXTURE} />
        </AircraftFeesProvider>
      </FeeProvider>,
    );
    expect(screen.getByText("—")).toBeTruthy();
  });
});
