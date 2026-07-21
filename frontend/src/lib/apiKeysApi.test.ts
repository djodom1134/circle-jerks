import { afterEach, describe, expect, it, vi } from "vitest";
import { adminCreateApiKey, adminListApiKeys, adminRevokeApiKey } from "./api";

function mockFetch(body: unknown, status = 200) {
  const spy = vi.fn().mockResolvedValue({
    ok: status < 400,
    status,
    json: async () => body,
    text: async () => JSON.stringify(body),
  });
  vi.stubGlobal("fetch", spy);
  return spy;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("admin api key client", () => {
  it("lists keys with credentials included", async () => {
    const spy = mockFetch({ keys: [] });
    const result = await adminListApiKeys();
    expect(result.keys).toEqual([]);
    const [url, init] = spy.mock.calls[0];
    expect(url).toBe("/api/admin/api-keys");
    expect(init.credentials).toBe("include");
  });

  it("posts the create payload", async () => {
    const spy = mockFetch({ key: "cj_test_x", record: { id: "x" } });
    const result = await adminCreateApiKey("partner", ["ops:read"], ["KLMO"]);
    expect(result.key).toBe("cj_test_x");
    const [url, init] = spy.mock.calls[0];
    expect(url).toBe("/api/admin/api-keys");
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body)).toEqual({
      name: "partner",
      scopes: ["ops:read"],
      airports: ["KLMO"],
    });
  });

  it("sends null airports when the list is empty", async () => {
    const spy = mockFetch({ key: "cj_test_x", record: { id: "x" } });
    await adminCreateApiKey("partner", ["ops:read"], []);
    expect(JSON.parse(spy.mock.calls[0][1].body).airports).toBeNull();
  });

  it("posts to the revoke route", async () => {
    const spy = mockFetch({ ok: true, revoked: true });
    const result = await adminRevokeApiKey("abc");
    expect(result.revoked).toBe(true);
    expect(spy.mock.calls[0][0]).toBe("/api/admin/api-keys/abc/revoke");
  });
});
