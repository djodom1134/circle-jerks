import { describe, expect, it } from "vitest";
import {
  ALL_TABS,
  airportRequired,
  allowedScopes,
  canManageUsers,
  clampScopes,
  visibleTabs,
  type AdminSession
} from "./adminAccess";

function session(overrides: Partial<AdminSession> = {}): AdminSession {
  return {
    id: "u1",
    email: "p@example.com",
    name: "P",
    username: "P",
    role: "partner",
    status: "approved",
    scopes: ["ops:read"],
    airports: ["KLMO"],
    ...overrides
  };
}

describe("visibleTabs", () => {
  it("gives a partner only keys and docs", () => {
    expect(visibleTabs(session())).toEqual(["keys", "docs"]);
  });

  it("gives an admin everything except user management", () => {
    const tabs = visibleTabs(session({ role: "admin" }));
    expect(tabs).toContain("dashboard");
    expect(tabs).not.toContain("users");
  });

  it("gives a super-admin every tab", () => {
    expect(visibleTabs(session({ role: "super_admin" }))).toEqual(ALL_TABS);
  });

  it("gives a non-approved user nothing, whatever their role says", () => {
    for (const status of ["pending", "rejected", "suspended"] as const) {
      expect(visibleTabs(session({ role: "super_admin", status }))).toEqual([]);
    }
  });

  it("gives a signed-out visitor nothing", () => {
    expect(visibleTabs(null)).toEqual([]);
  });
});

describe("canManageUsers", () => {
  it("is true only for an approved super-admin", () => {
    expect(canManageUsers(session({ role: "super_admin" }))).toBe(true);
    expect(canManageUsers(session({ role: "admin" }))).toBe(false);
    expect(canManageUsers(session({ role: "super_admin", status: "suspended" }))).toBe(false);
    expect(canManageUsers(null)).toBe(false);
  });
});

describe("allowedScopes", () => {
  it("returns the granted scopes for a partner", () => {
    expect(allowedScopes(session())).toEqual(["ops:read"]);
  });

  it("returns every scope for an admin", () => {
    expect(allowedScopes(session({ role: "admin", scopes: ["ops:read"] })).length).toBe(4);
  });
});

describe("airportRequired", () => {
  it("is true when the grant names airports", () => {
    expect(airportRequired(session())).toBe(true);
  });

  it("is false when the grant is unrestricted", () => {
    expect(airportRequired(session({ airports: null }))).toBe(false);
  });
});

describe("clampScopes", () => {
  it("drops scopes outside the grant", () => {
    // Presentation only. The server's enforce_grant is the real boundary;
    // this exists so the form cannot submit a request that is certain to 400.
    expect(clampScopes(["ops:read", "ledger:read"], session())).toEqual(["ops:read"]);
  });

  it("preserves order and removes duplicates", () => {
    expect(clampScopes(["ops:read", "ops:read"], session())).toEqual(["ops:read"]);
  });

  it("leaves an admin's selection untouched", () => {
    const picked = ["ops:read", "ledger:read"];
    expect(clampScopes(picked, session({ role: "admin" }))).toEqual(picked);
  });
});
