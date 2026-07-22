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
