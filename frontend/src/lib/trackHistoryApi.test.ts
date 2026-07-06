import { afterEach, describe, expect, it, vi } from "vitest";
import { getTrackHistory } from "./api";

afterEach(() => vi.unstubAllGlobals());

describe("getTrackHistory", () => {
  it("requests the track-history path with days and ceiling params", async () => {
    const fn = vi.fn(async () => ({ ok: true, json: async () => ({ tracks: [] }) } as Response));
    vi.stubGlobal("fetch", fn);
    await getTrackHistory("KBJC", 3, 4000);
    const url = String((fn.mock.calls as unknown[][])[0][0]);
    expect(url).toContain("/api/airports/KBJC/track-history?days=3&ceiling_ft=4000");
  });

  it("defaults the ceiling to 5000", async () => {
    const fn = vi.fn(async () => ({ ok: true, json: async () => ({ tracks: [] }) } as Response));
    vi.stubGlobal("fetch", fn);
    await getTrackHistory("KBJC", 7);
    const url = String((fn.mock.calls as unknown[][])[0][0]);
    expect(url).toContain("ceiling_ft=5000");
  });
});
