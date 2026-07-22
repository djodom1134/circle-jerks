import { describe, expect, it } from "vitest";
import {
  ALL_TABS,
  airportRequired,
  allowedScopes,
  canManageUsers,
  clampScopes,
  describeGrant,
  resolveAirportsSelection,
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

describe("resolveAirportsSelection", () => {
  // This is the single most likely way to over-grant a partner by accident:
  // the backend maps an empty/absent airport list to granted_airports = NULL,
  // which means every airport. "All airports" must therefore be a choice you
  // make on purpose, never the thing you get from leaving a box blank.

  it("uppercases and trims a valid restricted list", () => {
    expect(resolveAirportsSelection("restricted", "klmo, kbjc")).toEqual({
      ok: true,
      airports: ["KLMO", "KBJC"]
    });
  });

  it("rejects an empty restricted input instead of silently granting everything", () => {
    const result = resolveAirportsSelection("restricted", "");
    expect(result.ok).toBe(false);
    if (!result.ok) expect(result.error).toMatch(/airport/i);
  });

  it("rejects a whitespace-only restricted input", () => {
    const result = resolveAirportsSelection("restricted", "   ");
    expect(result.ok).toBe(false);
  });

  it("returns null airports only when 'all' is explicitly chosen", () => {
    expect(resolveAirportsSelection("all", "")).toEqual({ ok: true, airports: null });
  });

  it("ignores any leftover restricted-box text when 'all' is chosen", () => {
    // Switching from "restricted" to "all" without clearing the text field
    // must not leak the stale text back in as a restriction.
    expect(resolveAirportsSelection("all", "KLMO")).toEqual({ ok: true, airports: null });
  });

  it("splits on commas and whitespace alike", () => {
    expect(resolveAirportsSelection("restricted", "KLMO KBJC, KDEN\tKAPA\nKBJC")).toEqual({
      ok: true,
      airports: ["KLMO", "KBJC", "KDEN", "KAPA"]
    });
  });

  it("deduplicates case-insensitively while preserving first-seen order", () => {
    expect(resolveAirportsSelection("restricted", "klmo, KLMO, Klmo, kbjc")).toEqual({
      ok: true,
      airports: ["KLMO", "KBJC"]
    });
  });
});

describe("describeGrant", () => {
  // This backstops the decided-users table: once the Role/Scopes/Airports
  // cells became editable drafts, this is the only remaining read of what is
  // actually stored, which is what makes a stray edit (or a stale
  // Reinstate) visible before it is submitted.

  it("summarizes a restricted partner grant", () => {
    expect(describeGrant("partner", ["ops:read", "aggregates:read"], ["KLMO", "KBJC"])).toBe(
      "partner · ops:read, aggregates:read · KLMO, KBJC"
    );
  });

  it("summarizes an unrestricted partner grant distinctly from a restricted one", () => {
    expect(describeGrant("partner", ["ops:read"], null)).toBe("partner · ops:read · all airports");
  });

  it("calls out a partner with no scopes or airports rather than hiding it", () => {
    expect(describeGrant("partner", [], [])).toBe("partner · no scopes · no airports");
  });

  it("describes admin and super_admin as role-granted, ignoring stored scopes/airports", () => {
    expect(describeGrant("admin", [], null)).toBe("admin · all airports (role-granted)");
    expect(describeGrant("super_admin", ["ops:read"], ["KLMO"])).toBe(
      "super_admin · all airports (role-granted)"
    );
  });
});
