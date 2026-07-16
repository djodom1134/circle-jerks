import { afterEach, describe, expect, it, vi } from "vitest";
import { getPatternTemplate, savePattern, revertPattern, getAirportRunways } from "./api";

function mockFetch(jsonBody: unknown) {
  const fn = vi.fn(async () => ({ ok: true, json: async () => jsonBody } as Response));
  vi.stubGlobal("fetch", fn);
  return fn;
}

type MockCalls = Array<[url: string, init?: RequestInit]>;
function calls(fn: ReturnType<typeof vi.fn>): MockCalls {
  return fn.mock.calls as unknown as MockCalls;
}

afterEach(() => vi.unstubAllGlobals());

describe("pattern API client", () => {
  it("getPatternTemplate builds the template URL with side", async () => {
    const fn = mockFetch({ geometry: { points: [], closed: true, spline: "catmull-rom" } });
    await getPatternTemplate("KBJC", "12L", "right");
    expect(calls(fn)[0][0]).toContain("/api/runways/KBJC/12L/pattern/template?side=right");
  });

  it("savePattern issues a PUT with a JSON body", async () => {
    const fn = mockFetch({ pattern: null });
    await savePattern("KBJC", "12L", { points: [{ lat: 1, lon: 2 }], closed: true, visitor_id: "v-12345678" });
    const [url, init] = calls(fn)[0];
    expect(url).toContain("/api/runways/KBJC/12L/pattern");
    expect(init?.method).toBe("PUT");
    expect(JSON.parse(init?.body as string).points[0].lat).toBe(1);
  });

  it("revertPattern POSTs the version", async () => {
    const fn = mockFetch({ pattern: null });
    await revertPattern("KBJC", "12L", 2, "v-12345678");
    const [url, init] = calls(fn)[0];
    expect(url).toContain("/api/runways/KBJC/12L/pattern/revert");
    expect(init?.method).toBe("POST");
    expect(JSON.parse(init?.body as string).version).toBe(2);
  });

  it("getAirportRunways hits the runways path", async () => {
    const fn = mockFetch({ airport_icao: "KBJC", runways: [] });
    await getAirportRunways("KBJC");
    expect(calls(fn)[0][0]).toContain("/api/airports/KBJC/runways");
  });
});
