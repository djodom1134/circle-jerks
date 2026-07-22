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

/**
 * The tab a session should land on right after sign-in.
 *
 * `AdminDashboard` used to always initialize to "keys", which happened to be
 * in `visibleTabs` for every role, so the "keep the selected tab reachable"
 * effect never fired to correct it. A super_admin or admin landed on API
 * keys — and the pending-requests badge lives inside the users tab, which
 * they now had no reason to open. With email notification out of scope, that
 * badge is the entire notification mechanism for new access requests, so the
 * wrong landing tab made it effectively invisible. A partner has no
 * "dashboard" tab at all, so they still land on keys.
 */
export function defaultTabFor(session: AdminSession | null): TabId {
  if (approved(session) && (session.role === "super_admin" || session.role === "admin")) {
    return "dashboard";
  }
  return "keys";
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

/** Sent for a partner grant's "all airports" choice. The server maps this
 *  exact single-element list to a stored NULL (unrestricted) grant — see
 *  `_normalize_access` on the backend. It never accepts `null`/omitted or
 *  `[]` for a partner grant, precisely because those used to be silently
 *  read as "every airport" one layer down. */
export const ALL_AIRPORTS_SENTINEL = "*";

export type AirportsSelectionResult =
  | { ok: true; airports: string[] }
  | { ok: false; error: string };

/**
 * Turn the two-way "restrict to specific airports" / "all airports
 * (unrestricted)" choice into the `airports` value the API expects.
 *
 * The server requires a partner grant to state `airports` explicitly: a list
 * of ICAO codes, or the single-element sentinel `["*"]` for every airport.
 * `null`/omitted and `[]` are both rejected with a 400, because
 * `api_keys.serialize_airports` would otherwise read either one as "every
 * airport" — the most dangerous possible default for a text box left blank.
 * This function exists so that hazard can never reach the wire from here:
 * "all airports" only ever comes from the caller explicitly picking `"all"`,
 * which is sent as `["*"]`, never from an empty or whitespace-only
 * restricted box, which is rejected as a validation error instead.
 */
export function resolveAirportsSelection(mode: AirportMode, raw: string): AirportsSelectionResult {
  if (mode === "all") {
    // Deliberate: whatever is left over in the restricted text box is
    // discarded here. A stale value from a prior mode must never leak back
    // in as a restriction once "all airports" has been chosen on purpose.
    return { ok: true, airports: [ALL_AIRPORTS_SENTINEL] };
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

/**
 * Render a stored grant (role / scopes / airports) as a short, unambiguous
 * summary — e.g. "partner · ops:read, aggregates:read · KLMO, KBJC", or
 * "partner · ops:read · all airports" when unrestricted.
 *
 * Exists so a decided-user row whose editable controls have been seeded from
 * a draft (and may since have been changed) still has one place that reads
 * back the record as it actually is, not what a pending edit says. Purely a
 * display formatter — it derives nothing that gets sent to the server.
 */
export function describeGrant(role: string, scopes: string[], airports: string[] | null): string {
  if (role !== "partner") {
    return `${role} · all airports (role-granted)`;
  }
  const scopesLabel = scopes.length ? scopes.join(", ") : "no scopes";
  const airportsLabel =
    airports === null ? "all airports" : airports.length ? airports.join(", ") : "no airports";
  return `${role} · ${scopesLabel} · ${airportsLabel}`;
}
