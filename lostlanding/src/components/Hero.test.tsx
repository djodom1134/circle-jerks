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

  it("adds a projected-annual companion figure, labeled as projected and reactive to the fee slider", async () => {
    renderHero(FEES_FIXTURE, true);
    await waitFor(() => expect(screen.getByText("$40.00")).toBeTruthy());

    // SAMPLE_LEDGER_FIXTURE.projection.projected_annual_runway_uses = 3650, fee = $10 -> $36,500.
    expect(screen.getByText("$36,500")).toBeTruthy();
    const projectedEl = document.querySelector(".hero-companion-projected");
    expect(projectedEl).toBeTruthy();
    expect(projectedEl!.textContent?.toLowerCase()).toContain("projected annual");

    await act(async () => {
      screen.getByRole("button", { name: /bump fee/i }).click();
    });
    // Same 3650 runway uses/yr, now at $20 -> $73,000.
    await waitFor(() => expect(screen.getByText("$73,000")).toBeTruthy());
  });

  it("never lets the projected figure be mistaken for a count -- and states the seasonality limit plainly", async () => {
    renderHero();
    await waitFor(() => expect(screen.getByText("$40.00")).toBeTruthy());

    const note = document.querySelector(".hero-projection-note")?.textContent ?? "";
    expect(note.toLowerCase()).toContain("projected, not counted");
    expect(note.toLowerCase()).toContain("peak flying season");
    expect(note.toLowerCase()).toMatch(/over-estimate/);
    expect(note.toLowerCase()).toContain("not a floor");
  });

  it("cites the FAA's 120,000-operations figure only as an independent cross-check, never converted or stated as fact", async () => {
    renderHero();
    await waitFor(() => expect(screen.getByText("$40.00")).toBeTruthy());

    const note = document.querySelector(".hero-projection-note")?.textContent ?? "";
    expect(note).toMatch(/120,000 operations a year/);
    expect(note.toLowerCase()).toContain("an estimate, not a count");
    expect(note.toLowerCase()).toContain("same order of magnitude");
    // Must never do the FAA-operations-to-runway-uses conversion -- that
    // would be the exact double-count this page attacks.
    expect(note).not.toMatch(/120,000\s*(x|\*|times|\/|÷)\s*2/i);
    expect(note.toLowerCase()).not.toContain("60,000 runway uses");
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

  it("adds the self-sustaining obligation callout directly under the ticker, linking to the full section", async () => {
    renderHero();
    await waitFor(() => expect(screen.getByText("$40.00")).toBeTruthy());

    expect(document.body.textContent).toMatch(/as self-sustaining as possible/i);
    expect(document.body.textContent).toMatch(
      /longmont accepted \$725,000 in federal money this year to rebuild a taxilane, while charging nothing at all for the runway/i,
    );

    const link = screen.getByRole("link", { name: /see the obligation longmont signed/i });
    expect(link.getAttribute("href")).toBe("#obligation");

    // Must not bury the ticker: the ticker headline stays before the obligation callout in DOM order.
    const tickerEl = screen.getByText(/we missed out on/i);
    const obligationEl = document.querySelector(".hero-obligation");
    expect(obligationEl).toBeTruthy();
    expect(tickerEl.compareDocumentPosition(obligationEl!) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
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
