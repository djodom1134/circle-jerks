import { API_KEY_SCOPES } from "./api";

export type AdminRole = "super_admin" | "admin" | "partner";
export type AdminStatus = "pending" | "approved" | "rejected" | "suspended";
export type TabId = "dashboard" | "users" | "keys" | "docs";

export const ALL_TABS: TabId[] = ["dashboard", "users", "keys", "docs"];

export interface AdminSession {
  id: string;
  email: string;
  name: string | null;
  username: string;
  role: AdminRole;
  status: AdminStatus;
  scopes: string[];
  /** null means every airport. */
  airports: string[] | null;
}

function approved(session: AdminSession | null): session is AdminSession {
  return session != null && session.status === "approved";
}

function isAdmin(session: AdminSession | null): boolean {
  return approved(session) && (session.role === "admin" || session.role === "super_admin");
}

export function visibleTabs(session: AdminSession | null): TabId[] {
  if (!approved(session)) return [];
  if (session.role === "super_admin") return ALL_TABS;
  if (session.role === "admin") return ["dashboard", "keys", "docs"];
  return ["keys", "docs"];
}

export function canManageUsers(session: AdminSession | null): boolean {
  return approved(session) && session.role === "super_admin";
}

export function allowedScopes(session: AdminSession | null): string[] {
  if (!approved(session)) return [];
  return isAdmin(session) ? [...API_KEY_SCOPES] : session.scopes;
}

export function airportRequired(session: AdminSession | null): boolean {
  return approved(session) && !isAdmin(session) && session.airports != null;
}

/**
 * Trim a scope selection to what the session may actually request.
 *
 * Presentation only — the server's enforce_grant is the real boundary. This
 * exists so the form cannot submit a request that is certain to be refused.
 */
export function clampScopes(selected: string[], session: AdminSession | null): string[] {
  const permitted = new Set(allowedScopes(session));
  const seen = new Set<string>();
  return selected.filter((scope) => {
    if (!permitted.has(scope) || seen.has(scope)) return false;
    seen.add(scope);
    return true;
  });
}

export type AirportMode = "restricted" | "all";

export type AirportsSelectionResult =
  | { ok: true; airports: string[] | null }
  | { ok: false; error: string };

/**
 * Turn the two-way "restrict to specific airports" / "all airports
 * (unrestricted)" choice into the `airports` value the API expects.
 *
 * The backend maps an empty or absent airport list to `granted_airports =
 * NULL`, and NULL means unrestricted — every airport at the site. That makes
 * a blank text box the most dangerous default imaginable: leave it empty and
 * you have (silently) granted everything. This function exists so "all
 * airports" can only ever come from the caller explicitly picking `"all"`,
 * never from an empty or whitespace-only restricted box, which is rejected
 * as a validation error instead.
 */
export function resolveAirportsSelection(mode: AirportMode, raw: string): AirportsSelectionResult {
  if (mode === "all") {
    // Deliberate: whatever is left over in the restricted text box is
    // discarded here. A stale value from a prior mode must never leak back
    // in as a restriction once "all airports" has been chosen on purpose.
    return { ok: true, airports: null };
  }

  const seen = new Set<string>();
  const airports: string[] = [];
  for (const token of raw.split(/[\s,]+/)) {
    const icao = token.trim().toUpperCase();
    if (!icao || seen.has(icao)) continue;
    seen.add(icao);
    airports.push(icao);
  }

  if (airports.length === 0) {
    return {
      ok: false,
      error: "Enter at least one airport, or choose \"All airports\" to grant every airport explicitly."
    };
  }

  return { ok: true, airports };
}
