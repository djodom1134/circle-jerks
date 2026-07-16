import { afterEach, describe, expect, it, vi } from "vitest";
import { getAirportStats } from "./api";

afterEach(() => vi.unstubAllGlobals());

describe("getAirportStats", () => {
  it("requests the stats path with the window param", async () => {
    const fn = vi.fn(async () => ({ ok: true, json: async () => ({ airport_icao: "KBJC" }) } as Response));
    vi.stubGlobal("fetch", fn);
    await getAirportStats("KBJC", "30d");
    expect((fn.mock.calls as unknown[][])[0][0]).toContain("/api/airports/KBJC/stats?window=30d");
  });
});
