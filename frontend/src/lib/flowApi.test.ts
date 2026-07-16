import { afterEach, describe, expect, it, vi } from "vitest";
import { getAirportFlow } from "./api";

afterEach(() => vi.unstubAllGlobals());

type MockCalls = Array<[url: string, init?: RequestInit]>;
function calls(fn: ReturnType<typeof vi.fn>): MockCalls {
  return fn.mock.calls as unknown as MockCalls;
}

describe("getAirportFlow", () => {
  it("requests the flow path", async () => {
    const fn = vi.fn(async () => ({ ok: true, json: async () => ({ airport_icao: "KBJC", active: null, recent_changes: [] }) } as Response));
    vi.stubGlobal("fetch", fn);
    const r = await getAirportFlow("KBJC");
    expect(calls(fn)[0][0]).toContain("/api/airports/KBJC/flow");
    expect(r.recent_changes).toEqual([]);
  });
});
