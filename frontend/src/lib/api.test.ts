import { afterEach, describe, expect, it, vi } from "vitest";
import { getWorstOffenders, getOnlineCount } from "./api";

afterEach(() => vi.unstubAllGlobals());

describe("worst offenders + online", () => {
  it("requests the worst_offenders path with limit", async () => {
    const fn = vi.fn(async () => ({ ok: true, json: async () => ({ offenders: [] }) } as Response));
    vi.stubGlobal("fetch", fn);
    await getWorstOffenders("KLMO", 5);
    expect((fn.mock.calls as unknown[][])[0][0]).toContain("/api/airports/KLMO/worst_offenders?limit=5");
  });

  it("requests the online count path", async () => {
    const fn = vi.fn(async () => ({ ok: true, json: async () => ({ count: 3 }) } as Response));
    vi.stubGlobal("fetch", fn);
    const out = await getOnlineCount();
    expect((fn.mock.calls as unknown[][])[0][0]).toContain("/api/activity/online");
    expect(out.count).toBe(3);
  });
});
