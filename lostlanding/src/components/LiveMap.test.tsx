import { afterEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { LiveMap } from "./LiveMap";
import { FeeProvider } from "../lib/feeContext";
import { AircraftFeesProvider } from "../lib/aircraftFeesContext";
import type { AircraftFeesResponse } from "../lib/liveTypes";

const { fetchLivePositionsMock, fetchAircraftFeesMock } = vi.hoisted(() => ({
  fetchLivePositionsMock: vi.fn(),
  fetchAircraftFeesMock: vi.fn(),
}));

vi.mock("../lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../lib/api")>();
  return {
    ...actual,
    fetchLivePositions: fetchLivePositionsMock,
    fetchAircraftFees: fetchAircraftFeesMock,
  };
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

const FEES_FIXTURE: AircraftFeesResponse = {
  airport_icao: "KLMO",
  timezone: "America/Denver",
  counting_since: 1750000000, // 2025-06-15ish
  today: { window: "rolling_24h", since_ts: 0, until_ts: 1, runway_uses: 4 },
  rate_window: { seconds: 1200, runway_uses: 0, uses_per_second: 0 },
  aircraft: {
    priced: { tail: "N42PR", total: 20, today: 2, month: 8, year: 20 },
  },
};

function renderMap(fees: AircraftFeesResponse = FEES_FIXTURE) {
  fetchAircraftFeesMock.mockResolvedValue(fees);
  return render(
    <FeeProvider initialFee={10}>
      <AircraftFeesProvider icao="KLMO" pollIntervalMs={999_999}>
        <LiveMap />
      </AircraftFeesProvider>
    </FeeProvider>,
  );
}

describe("LiveMap", () => {
  it("shows an honest empty state when there is no live traffic", async () => {
    fetchLivePositionsMock.mockResolvedValue({
      airport_icao: "KLMO",
      window: { code: "1h", label: "last hour", start_ts: 0, end_ts: 1, seconds: 3600 },
      tracks: [],
      active_now: 0,
      updated_at: 1,
    });
    renderMap();

    await waitFor(() => expect(screen.getByText(/no aircraft in the pattern right now/i)).toBeTruthy());
  });

  it("shows an error state, not a perpetual spinner, when the positions fetch fails", async () => {
    fetchLivePositionsMock.mockRejectedValue(new Error("upstream unavailable"));
    renderMap();

    await waitFor(() => expect(screen.getByRole("alert")).toBeTruthy());
    expect(screen.getByText(/upstream unavailable/i)).toBeTruthy();
  });

  it("renders a price tag only for an aircraft present in the aircraft-fees payload", async () => {
    fetchLivePositionsMock.mockResolvedValue({
      airport_icao: "KLMO",
      window: { code: "1h", label: "last hour", start_ts: 0, end_ts: 1, seconds: 3600 },
      tracks: [
        {
          icao24: "priced",
          callsign: "N42PR",
          samples: [{ timestamp: 100, lat: 40.17, lon: -105.16, heading_deg: 10, altitude_ft: 4000, vertical_rate_fpm: 0, in_window: true }],
        },
        {
          icao24: "unpriced",
          callsign: "N99ZZ",
          samples: [{ timestamp: 100, lat: 40.18, lon: -105.15, heading_deg: 200, altitude_ft: 4500, vertical_rate_fpm: 0, in_window: true }],
        },
      ],
      active_now: 2,
      updated_at: 1,
    });
    renderMap();

    await waitFor(() => expect(document.querySelectorAll(".plane-marker").length).toBe(2));

    const markers = Array.from(document.querySelectorAll<HTMLButtonElement>(".plane-marker"));
    const priced = markers.find((m) => m.getAttribute("aria-label")?.includes("N42PR"));
    const unpriced = markers.find((m) => m.getAttribute("aria-label")?.includes("N99ZZ"));

    expect(priced?.querySelector(".plane-price-tag")?.textContent).toBe("$200"); // 20 total * $10 fee
    expect(unpriced?.querySelector(".plane-price-tag")).toBeNull();
    expect(unpriced?.getAttribute("aria-label")).toMatch(/no recorded runway use/i);
  });

  it("shows the full honest breakdown on focus, labeled 'Last 24 hours' (not 'Today') and never 'lifetime'", async () => {
    fetchLivePositionsMock.mockResolvedValue({
      airport_icao: "KLMO",
      window: { code: "1h", label: "last hour", start_ts: 0, end_ts: 1, seconds: 3600 },
      tracks: [
        {
          icao24: "priced",
          callsign: "N42PR",
          samples: [{ timestamp: 100, lat: 40.17, lon: -105.16, heading_deg: 10, altitude_ft: 4000, vertical_rate_fpm: 0, in_window: true }],
        },
      ],
      active_now: 1,
      updated_at: 1,
    });
    renderMap();

    await waitFor(() => expect(document.querySelector(".plane-marker")).toBeTruthy());
    const marker = document.querySelector(".plane-marker") as HTMLButtonElement;

    act(() => {
      fireEvent.focus(marker);
    });

    await waitFor(() => expect(screen.getByRole("tooltip")).toBeTruthy());
    const tooltip = screen.getByRole("tooltip");
    expect(tooltip.textContent).toMatch(/Last 24 hours/);
    expect(tooltip.textContent).toMatch(/This month/);
    expect(tooltip.textContent).toMatch(/This year/);
    expect(tooltip.textContent).toMatch(/Since we started counting/);
    expect(tooltip.textContent?.toLowerCase()).not.toContain("lifetime");
    expect(tooltip.textContent).toContain("$20"); // today: 2 * $10

    act(() => {
      fireEvent.blur(marker);
    });
    await waitFor(() => expect(screen.queryByRole("tooltip")).toBeNull());
  });

  it("shows a 'no recorded runway use' message on focus for an aircraft absent from aircraft-fees", async () => {
    fetchLivePositionsMock.mockResolvedValue({
      airport_icao: "KLMO",
      window: { code: "1h", label: "last hour", start_ts: 0, end_ts: 1, seconds: 3600 },
      tracks: [
        {
          icao24: "unpriced",
          callsign: "N99ZZ",
          samples: [{ timestamp: 100, lat: 40.18, lon: -105.15, heading_deg: 200, altitude_ft: 4500, vertical_rate_fpm: 0, in_window: true }],
        },
      ],
      active_now: 1,
      updated_at: 1,
    });
    renderMap();

    await waitFor(() => expect(document.querySelector(".plane-marker")).toBeTruthy());
    const marker = document.querySelector(".plane-marker") as HTMLButtonElement;
    act(() => {
      fireEvent.focus(marker);
    });

    await waitFor(() => expect(screen.getByRole("tooltip")).toBeTruthy());
    expect(screen.getByRole("tooltip").textContent).toMatch(/no recorded runway use/i);
  });
});
