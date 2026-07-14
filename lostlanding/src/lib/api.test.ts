import { afterEach, describe, expect, it, vi } from "vitest";
import { LedgerFetchError, LivePositionsFetchError, fetchAircraftFees, fetchLivePositions } from "./api";

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

function jsonResponse(body: unknown, ok = true, status = 200) {
  return { ok, status, statusText: ok ? "OK" : "Error", json: async () => body } as Response;
}

describe("fetchAircraftFees", () => {
  it("returns the parsed payload on success", async () => {
    const payload = {
      airport_icao: "KLMO",
      timezone: "America/Denver",
      counting_since: 1750000000,
      today: { window: "rolling_24h", since_ts: 0, until_ts: 1, runway_uses: 3 },
      rate_window: { seconds: 1200, runway_uses: 1, uses_per_second: 1 / 1200 },
      aircraft: {},
    };
    vi.stubGlobal("fetch", vi.fn(() => Promise.resolve(jsonResponse(payload))));

    await expect(fetchAircraftFees("KLMO")).resolves.toEqual(payload);
  });

  it("throws LedgerFetchError on a non-2xx response", async () => {
    vi.stubGlobal("fetch", vi.fn(() => Promise.resolve(jsonResponse({}, false, 500))));
    await expect(fetchAircraftFees("KLMO")).rejects.toBeInstanceOf(LedgerFetchError);
  });

  it("throws LedgerFetchError when the network request itself fails", async () => {
    vi.stubGlobal("fetch", vi.fn(() => Promise.reject(new Error("offline"))));
    await expect(fetchAircraftFees("KLMO")).rejects.toBeInstanceOf(LedgerFetchError);
  });
});

describe("fetchLivePositions", () => {
  it("requests the /live/positions path with the given query params", async () => {
    const payload = {
      airport_icao: "KLMO",
      window: { code: "1h", label: "last hour", start_ts: 0, end_ts: 1, seconds: 3600 },
      tracks: [],
      active_now: 0,
      updated_at: 123,
    };
    const fetchMock = vi.fn((_input: RequestInfo | URL) => Promise.resolve(jsonResponse(payload)));
    vi.stubGlobal("fetch", fetchMock);

    const result = await fetchLivePositions("KLMO", 40.1637, -105.1633, 8);
    expect(result).toEqual(payload);

    const calledUrl = String(fetchMock.mock.calls[0][0]);
    expect(calledUrl).toContain("/live/positions?");
    expect(calledUrl).toContain("airport_icao=KLMO");
    expect(calledUrl).toContain("ring_nm=8");
  });

  it("throws LivePositionsFetchError on a non-2xx response", async () => {
    vi.stubGlobal("fetch", vi.fn(() => Promise.resolve(jsonResponse({}, false, 502))));
    await expect(fetchLivePositions("KLMO", 40.1637, -105.1633)).rejects.toBeInstanceOf(LivePositionsFetchError);
  });

  it("throws LivePositionsFetchError when the network request itself fails", async () => {
    vi.stubGlobal("fetch", vi.fn(() => Promise.reject(new Error("offline"))));
    await expect(fetchLivePositions("KLMO", 40.1637, -105.1633)).rejects.toBeInstanceOf(LivePositionsFetchError);
  });
});
