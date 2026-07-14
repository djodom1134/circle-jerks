import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import App from "./App";
import { ZERO_LEDGER_FIXTURE } from "./test/fixtures";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

function jsonResponse(body: unknown, ok = true, status = 200) {
  return {
    ok,
    status,
    statusText: ok ? "OK" : "Error",
    json: async () => body,
  } as Response;
}

// App now mounts three independent pollers on separate endpoints (the 30-day
// ledger snapshot, this service's own aircraft-fees, and the MAIN api's live
// positions via the /live proxy) -- a global fetch stub has to branch by URL
// or the live map / hero ticker would try to parse the ledger fixture's shape
// and throw. Fixtures below are the "nothing to show yet" shape for the two
// live endpoints, used whenever a test isn't specifically exercising them.
const EMPTY_AIRCRAFT_FEES_FIXTURE = {
  airport_icao: "KLMO",
  timezone: "America/Denver",
  counting_since: null,
  today: { window: "rolling_24h", since_ts: 0, until_ts: 1, runway_uses: 0 },
  rate_window: { seconds: 1200, runway_uses: 0, uses_per_second: 0 },
  aircraft: {},
};

const EMPTY_LIVE_POSITIONS_FIXTURE = {
  airport_icao: "KLMO",
  window: { code: "1h", label: "last hour", start_ts: 0, end_ts: 1, seconds: 3600 },
  tracks: [],
  active_now: 0,
  updated_at: 0,
};

function stubMultiEndpointFetch(ledgerBody: unknown, ledgerOk = true, ledgerStatus = 200) {
  vi.stubGlobal(
    "fetch",
    vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("aircraft-fees")) return Promise.resolve(jsonResponse(EMPTY_AIRCRAFT_FEES_FIXTURE));
      if (url.includes("/positions")) return Promise.resolve(jsonResponse(EMPTY_LIVE_POSITIONS_FIXTURE));
      return Promise.resolve(jsonResponse(ledgerBody, ledgerOk, ledgerStatus));
    }),
  );
}

describe("App", () => {
  it("renders a loading state before the fetch resolves", () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() => new Promise<Response>(() => {})), // never resolves
    );

    render(<App />);

    expect(screen.getByRole("status")).toBeTruthy();
    expect(screen.getByText(/counting runway uses/i)).toBeTruthy();
  });

  it("renders an error state when the fetch fails", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() => Promise.reject(new Error("network unreachable"))),
    );

    render(<App />);

    await waitFor(() => expect(screen.getByRole("alert")).toBeTruthy());
    expect(screen.getByText(/couldn't load the runway-use ledger/i)).toBeTruthy();
    expect(screen.getByText(/network unreachable/i)).toBeTruthy();
  });

  it("renders an error state when the API returns a non-2xx response", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() => Promise.resolve(jsonResponse({}, false, 500))),
    );

    render(<App />);

    await waitFor(() => expect(screen.getByRole("alert")).toBeTruthy());
  });

  it("renders a deliberate zero state -- not fabricated data -- when the API returns all zeroes", async () => {
    stubMultiEndpointFetch(ZERO_LEDGER_FIXTURE);

    render(<App />);

    // Note: native <output> elements (in the calculator) also carry an
    // implicit role="status", so wait on the loading copy specifically
    // rather than the role, which is not unique once the page is ready.
    await waitFor(() => expect(screen.queryByText(/counting runway uses/i)).toBeNull());

    // The deliberate zero banner explaining the zero, not a broken UI.
    expect(screen.getByText(/shown deliberately/i)).toBeTruthy();
    // The real headline number is rendered as 0, not hidden or replaced with a placeholder.
    expect(screen.getAllByText("0").length).toBeGreaterThan(0);
    // The operator ledger explains there is nothing to show, rather than an empty table.
    expect(screen.getByText(/no operators recorded/i)).toBeTruthy();
  });
});
