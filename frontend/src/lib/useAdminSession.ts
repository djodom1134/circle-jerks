import { useCallback, useEffect, useState } from "react";
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
  return value === "pending" || value === "rejected" || value === "suspended" || value === "approved";
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

  const refresh = useCallback(async () => {
    setLoading(true);
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
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  return { session, blockedStatus, loading, refresh };
}
