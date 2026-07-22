import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, adminSession } from "./api";
import type { AdminSession, AdminStatus } from "./adminAccess";

export interface SessionState {
  session: AdminSession | null;
  /** Set when authenticated but not approved, so the shell can explain why. */
  blockedStatus: AdminStatus | null;
  loading: boolean;
  refresh: () => Promise<void>;
}

function isAdminStatus(value: unknown): value is AdminStatus {
  // Deliberately excludes "approved": this predicate feeds
  // `blockedStatusFromError`, which decides whether to show the "you can't
  // be here" screen. `require_admin`/`require_super_admin` return
  // `403 {"status": "approved", "role": ...}` for an approved user who
  // simply lacks the role a route requires — that is not a blocked-account
  // state, and showing that screen to an approved partner would be a wrong
  // "access denied" for a user who very much has access. Today only
  // GET /admin/session feeds this helper (guarded by require_user, which
  // cannot 403 an approved user), so this case cannot fire yet — but the
  // moment any require_admin/require_super_admin 403 routes through here,
  // it must not be read as a blocked status.
  return value === "pending" || value === "rejected" || value === "suspended";
}

/**
 * Read the blocked status out of a 403's `detail`, defensively. `detail` is
 * `unknown` on the wire — a non-object (or a shape we don't recognize) is
 * treated the same as "signed out" rather than trusted or allowed to throw.
 */
function blockedStatusFromError(error: unknown): AdminStatus | null {
  if (!(error instanceof ApiError) || error.status !== 403) return null;
  const detail = error.detail;
  if (typeof detail !== "object" || detail === null) return null;
  const status = (detail as { status?: unknown }).status;
  return isAdminStatus(status) ? status : null;
}

export function useAdminSession(): SessionState {
  const [session, setSession] = useState<AdminSession | null>(null);
  const [blockedStatus, setBlockedStatus] = useState<AdminStatus | null>(null);
  const [loading, setLoading] = useState(true);
  // Tracks whether the very first probe has completed. Only that first probe
  // should blank the whole shell to a loading state — a background re-probe
  // (e.g. the dashboard's 401/403 handler retrying every 30s against a
  // session stuck denied) must not flicker the shell back to "Loading…" on
  // every cycle.
  const hasLoadedOnce = useRef(false);

  const refresh = useCallback(async () => {
    if (!hasLoadedOnce.current) setLoading(true);
    try {
      const body = await adminSession();
      setSession(body);
      setBlockedStatus(null);
    } catch (error) {
      setSession(null);
      // A 403 carries the caller's own status, which is the difference
      // between "sign in" and "you are waiting for approval". Anything else
      // (401, network error, malformed detail) just means signed out.
      setBlockedStatus(blockedStatusFromError(error));
    } finally {
      hasLoadedOnce.current = true;
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  return { session, blockedStatus, loading, refresh };
}
