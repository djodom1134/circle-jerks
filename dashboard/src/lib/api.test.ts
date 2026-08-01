import { afterEach, describe, expect, it, vi } from "vitest";
import { DashboardFetchError, fetchDailyOperations, fetchOrigins } from "./api";
import { DAILY_FIXTURE, ORIGINS_FIXTURE } from "../fixtures/klmo";

afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks(); });

function jsonResponse(body: unknown, ok = true, status = 200) {
  return { ok, status, statusText: ok ? "OK" : "Error", json: async () => body } as Response;
}

describe("fetchDailyOperations", () => {
  it("requests the daily-operations path and returns the parsed payload", async () => {
    const fetchMock = vi.fn((_i: RequestInfo | URL) => Promise.resolve(jsonResponse(DAILY_FIXTURE)));
    vi.stubGlobal("fetch", fetchMock);
    await expect(fetchDailyOperations("KLMO")).resolves.toEqual(DAILY_FIXTURE);
    const url = String(fetchMock.mock.calls[0][0]);
    expect(url).toContain("/api/airports/KLMO/daily-operations");
  });

  it("passes from/to/origin as query params when given", async () => {
    const fetchMock = vi.fn((_i: RequestInfo | URL) => Promise.resolve(jsonResponse(DAILY_FIXTURE)));
    vi.stubGlobal("fetch", fetchMock);
    await fetchDailyOperations("KLMO", { from: "2026-07-01", to: "2026-07-31", origin: "KBDU" });
    const url = String(fetchMock.mock.calls[0][0]);
    expect(url).toContain("from=2026-07-01");
    expect(url).toContain("to=2026-07-31");
    expect(url).toContain("origin=KBDU");
  });

  it("throws DashboardFetchError on a non-2xx response", async () => {
    vi.stubGlobal("fetch", vi.fn(() => Promise.resolve(jsonResponse({}, false, 429))));
    await expect(fetchDailyOperations("KLMO")).rejects.toBeInstanceOf(DashboardFetchError);
  });

  it("throws DashboardFetchError when the network fails", async () => {
    vi.stubGlobal("fetch", vi.fn(() => Promise.reject(new Error("offline"))));
    await expect(fetchDailyOperations("KLMO")).rejects.toBeInstanceOf(DashboardFetchError);
  });
});

describe("fetchOrigins", () => {
  it("returns the parsed origins payload", async () => {
    vi.stubGlobal("fetch", vi.fn(() => Promise.resolve(jsonResponse(ORIGINS_FIXTURE))));
    await expect(fetchOrigins("KLMO")).resolves.toEqual(ORIGINS_FIXTURE);
  });
});
