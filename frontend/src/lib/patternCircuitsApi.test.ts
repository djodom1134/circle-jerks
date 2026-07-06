import { afterEach, describe, expect, it, vi } from "vitest";
import { getPatternCircuits } from "./api";

afterEach(() => vi.unstubAllGlobals());

describe("getPatternCircuits", () => {
  it("requests the pattern-circuits path with the days param", async () => {
    const fn = vi.fn(async () => ({ ok: true, json: async () => ({ circuits: [] }) } as Response));
    vi.stubGlobal("fetch", fn);
    await getPatternCircuits("KBJC", 5);
    const url = String((fn.mock.calls as unknown[][])[0][0]);
    expect(url).toContain("/api/airports/KBJC/pattern-circuits?days=5");
  });
});
