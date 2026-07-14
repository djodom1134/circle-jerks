import { afterEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import { AircraftFeesProvider, useAircraftFees } from "./aircraftFeesContext";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

function jsonResponse(body: unknown) {
  return { ok: true, status: 200, statusText: "OK", json: async () => body } as Response;
}

function Probe() {
  const { data, error, loading } = useAircraftFees();
  if (loading) return <div>loading</div>;
  if (error) return <div>error: {error}</div>;
  return <div>runway_uses: {data?.today.runway_uses}</div>;
}

const PAYLOAD = (runwayUses: number) => ({
  airport_icao: "KLMO",
  timezone: "America/Denver",
  counting_since: 1750000000,
  today: { window: "rolling_24h", since_ts: 0, until_ts: 1, runway_uses: runwayUses },
  rate_window: { seconds: 1200, runway_uses: 0, uses_per_second: 0 },
  aircraft: {},
});

describe("AircraftFeesProvider", () => {
  it("loads and exposes the payload", async () => {
    vi.stubGlobal("fetch", vi.fn(() => Promise.resolve(jsonResponse(PAYLOAD(3)))));

    render(
      <AircraftFeesProvider icao="KLMO">
        <Probe />
      </AircraftFeesProvider>,
    );

    await waitFor(() => expect(screen.getByText(/runway_uses: 3/)).toBeTruthy());
  });

  it("polls again after the interval and reflects a changed count", async () => {
    vi.useFakeTimers();
    let call = 0;
    vi.stubGlobal(
      "fetch",
      vi.fn(() => {
        call += 1;
        return Promise.resolve(jsonResponse(PAYLOAD(call === 1 ? 3 : 5)));
      }),
    );

    render(
      <AircraftFeesProvider icao="KLMO" pollIntervalMs={1000}>
        <Probe />
      </AircraftFeesProvider>,
    );

    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(screen.getByText(/runway_uses: 3/)).toBeTruthy();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(1000);
    });
    expect(screen.getByText(/runway_uses: 5/)).toBeTruthy();

    vi.useRealTimers();
  });

  it("surfaces a fetch failure as an explicit error, not a silent hang", async () => {
    vi.stubGlobal("fetch", vi.fn(() => Promise.reject(new Error("network down"))));

    render(
      <AircraftFeesProvider icao="KLMO">
        <Probe />
      </AircraftFeesProvider>,
    );

    await waitFor(() => expect(screen.getByText(/error:.*network down/)).toBeTruthy());
  });

  it("throws when useAircraftFees is called outside a provider", () => {
    const Broken = () => {
      useAircraftFees();
      return null;
    };
    const spy = vi.spyOn(console, "error").mockImplementation(() => {});
    expect(() => render(<Broken />)).toThrow(/AircraftFeesProvider/);
    spy.mockRestore();
  });
});
