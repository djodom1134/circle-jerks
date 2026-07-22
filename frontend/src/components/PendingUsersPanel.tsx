import { useEffect, useState } from "react";
import { ShieldCheck, UserCheck, UserX, Ban } from "lucide-react";
import {
  API_KEY_SCOPES,
  ApiError,
  adminApproveUser,
  adminListUsers,
  adminRejectUser,
  adminSuspendUser,
  adminUpdateUser,
  type AdminUserRecord
} from "../lib/api";
import { formatDateTime } from "../lib/format";
import { describeGrant, resolveAirportsSelection, type AirportMode } from "../lib/adminAccess";

type Draft = {
  role: string;
  scopes: string[];
  /** null = not yet chosen. Forces the reviewer to pick explicitly instead
   *  of an unrestricted grant ever being the thing you get by default. */
  airportMode: AirportMode | null;
  airportsRaw: string;
};

const EMPTY_DRAFT: Draft = {
  role: "partner",
  scopes: ["aggregates:read"],
  airportMode: null,
  airportsRaw: ""
};

/** Seed an edit draft from an already-decided user's real, current grant. */
function draftFromRecord(user: AdminUserRecord): Draft {
  return {
    role: user.role,
    scopes: user.scopes.length ? user.scopes : EMPTY_DRAFT.scopes,
    airportMode: user.airports === null ? "all" : "restricted",
    airportsRaw: (user.airports ?? []).join(", ")
  };
}

type ResolvedGrant = { role: string; scopes: string[]; airports: string[] | null };

/**
 * The explicit "restrict to specific airports" / "all airports (unrestricted)"
 * choice, shared by the pending-approval form and the decided-user edit form.
 *
 * Defined at module scope (not inline in a render function) deliberately: a
 * component defined inside another component's body gets a new identity every
 * render, which makes React unmount and remount its subtree on every
 * keystroke — losing focus in the airports text input the moment someone
 * types into it.
 */
function AirportChoice({
  groupName,
  draft,
  onChange
}: {
  groupName: string;
  draft: Draft;
  onChange: (patch: Partial<Draft>) => void;
}) {
  return (
    <fieldset className="admin-airport-choice">
      <legend>Airports</legend>
      <label>
        <input
          type="radio"
          name={groupName}
          checked={draft.airportMode === "restricted"}
          onChange={() => onChange({ airportMode: "restricted" })}
        />
        Restrict to specific airports
      </label>
      {draft.airportMode === "restricted" && (
        <input
          value={draft.airportsRaw}
          onChange={(e) => onChange({ airportsRaw: e.target.value })}
          placeholder="KLMO, KBJC"
        />
      )}
      <label>
        <input
          type="radio"
          name={groupName}
          checked={draft.airportMode === "all"}
          onChange={() => onChange({ airportMode: "all" })}
        />
        All airports (unrestricted — every airport on the site)
      </label>
    </fieldset>
  );
}

export default function PendingUsersPanel() {
  const [users, setUsers] = useState<AdminUserRecord[]>([]);
  const [pendingDrafts, setPendingDrafts] = useState<Record<string, Draft>>({});
  const [editDrafts, setEditDrafts] = useState<Record<string, Draft>>({});
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [status, setStatus] = useState("");
  // Distinct from an empty list: a failed load must not render as "no pending
  // requests", which a super-admin can read as truth and leave someone waiting.
  const [loadFailed, setLoadFailed] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);

  async function load() {
    try {
      setUsers((await adminListUsers()).users);
      setLoadFailed(false);
    } catch (error) {
      setLoadFailed(true);
      setStatus(error instanceof ApiError ? error.message : "Could not load users");
    }
  }

  useEffect(() => {
    void load();
  }, []);

  function pendingDraftFor(user: AdminUserRecord): Draft {
    return pendingDrafts[user.id] ?? EMPTY_DRAFT;
  }

  function setPendingDraft(id: string, patch: Partial<Draft>) {
    setPendingDrafts((prev) => ({ ...prev, [id]: { ...(prev[id] ?? EMPTY_DRAFT), ...patch } }));
  }

  function editDraftFor(user: AdminUserRecord): Draft {
    return editDrafts[user.id] ?? draftFromRecord(user);
  }

  function setEditDraft(user: AdminUserRecord, patch: Partial<Draft>) {
    setEditDrafts((prev) => ({
      ...prev,
      [user.id]: { ...(prev[user.id] ?? draftFromRecord(user)), ...patch }
    }));
  }

  function setFieldError(key: string, message: string | null) {
    setErrors((prev) => {
      if (message === null) {
        if (!(key in prev)) return prev;
        const next = { ...prev };
        delete next[key];
        return next;
      }
      return { ...prev, [key]: message };
    });
  }

  /**
   * Resolve a draft into what the API call needs, or record a validation
   * error against `errorKey` and return null.
   *
   * For admin/super_admin the server ignores submitted scopes/airports
   * entirely and grants access by role instead (see `_normalize_access` on
   * the backend) — so those are sent empty/null regardless of anything left
   * over in the draft's partner-only fields. Only the partner path can grant
   * or restrict anything, which is exactly where the airport choice below
   * matters: it is deliberately impossible to reach `airports: null` for a
   * partner without picking "All airports" on purpose.
   */
  function resolveGrant(errorKey: string, draft: Draft): ResolvedGrant | null {
    if (draft.role !== "partner") {
      setFieldError(errorKey, null);
      return { role: draft.role, scopes: [], airports: null };
    }
    if (draft.scopes.length === 0) {
      setFieldError(errorKey, "Choose at least one scope this partner may request.");
      return null;
    }
    if (draft.airportMode === null) {
      setFieldError(
        errorKey,
        "Choose whether to restrict this grant to specific airports or allow all airports."
      );
      return null;
    }
    const resolved = resolveAirportsSelection(draft.airportMode, draft.airportsRaw);
    if (!resolved.ok) {
      setFieldError(errorKey, resolved.error);
      return null;
    }
    setFieldError(errorKey, null);
    return { role: draft.role, scopes: draft.scopes, airports: resolved.airports };
  }

  async function run(
    id: string,
    action: () => Promise<{ user: AdminUserRecord; revoked_keys?: number }>
  ) {
    setBusy(id);
    setStatus("");
    try {
      const result = await action();
      // A narrowing that kills live keys must never be silent.
      setStatus(result.revoked_keys ? `Done. ${result.revoked_keys} key(s) revoked.` : "Done.");
      await load();
    } catch (error) {
      setStatus(error instanceof ApiError ? error.message : "Action failed");
    } finally {
      setBusy(null);
    }
  }

  const pending = users.filter((u) => u.status === "pending");
  const decided = users.filter((u) => u.status !== "pending");

  return (
    <section className="admin-panel">
      <div className="admin-section-title">
        <ShieldCheck size={16} /> Access requests
      </div>
      {status && <p className="admin-panel-status">{status}</p>}
      {loadFailed && <p className="admin-panel-error">Could not load the user list.</p>}

      <h3>Pending {pending.length > 0 && <span className="admin-badge">{pending.length}</span>}</h3>
      {!loadFailed && pending.length === 0 && <p className="admin-empty">No pending requests.</p>}
      {pending.map((user) => {
        const draft = pendingDraftFor(user);
        const error = errors[user.id];
        return (
          <div key={user.id} className="admin-user-row">
            <div className="admin-user-identity">
              <strong>{user.name ?? user.email}</strong>
              <span>{user.email}</span>
              <span>requested {formatDateTime(user.requested_at)}</span>
            </div>
            <label>
              Role
              <select value={draft.role} onChange={(e) => setPendingDraft(user.id, { role: e.target.value })}>
                <option value="partner">Partner — keys and docs only</option>
                <option value="admin">Admin — full dashboard</option>
                <option value="super_admin">Super-admin — full dashboard and user management</option>
              </select>
            </label>
            {draft.role === "partner" && (
              <>
                <fieldset>
                  <legend>Scopes they may request</legend>
                  {API_KEY_SCOPES.map((scope) => (
                    <label key={scope}>
                      <input
                        type="checkbox"
                        checked={draft.scopes.includes(scope)}
                        onChange={(e) =>
                          setPendingDraft(user.id, {
                            scopes: e.target.checked
                              ? [...draft.scopes, scope]
                              : draft.scopes.filter((s) => s !== scope)
                          })
                        }
                      />
                      {scope}
                    </label>
                  ))}
                </fieldset>
                <AirportChoice
                  groupName={`airports-${user.id}`}
                  draft={draft}
                  onChange={(patch) => setPendingDraft(user.id, patch)}
                />
              </>
            )}
            {error && <p className="admin-panel-error">{error}</p>}
            <div className="admin-user-actions">
              <button
                disabled={busy === user.id}
                onClick={() => {
                  const grant = resolveGrant(user.id, draft);
                  if (!grant) return;
                  void run(user.id, () =>
                    adminApproveUser(user.id, grant.role, grant.scopes, grant.airports)
                  );
                }}
              >
                <UserCheck size={14} /> Approve
              </button>
              <button
                className="admin-danger"
                disabled={busy === user.id}
                onClick={() => run(user.id, () => adminRejectUser(user.id))}
              >
                <UserX size={14} /> Reject
              </button>
            </div>
          </div>
        );
      })}

      <h3>Decided</h3>
      {!loadFailed && decided.length === 0 && <p className="admin-empty">Nobody has been approved yet.</p>}
      {decided.length > 0 && (
        <table className="admin-users-table">
          <thead>
            <tr>
              <th>User</th>
              <th>Role</th>
              <th>Status</th>
              <th>Scopes</th>
              <th>Airports</th>
              <th>Last seen</th>
              <th>Grant</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {decided.map((user) => {
              const draft = editDraftFor(user);
              const errorKey = `edit-${user.id}`;
              const error = errors[errorKey];
              return (
                <tr key={user.id}>
                  <td>
                    <strong>{user.name ?? user.email}</strong>
                    <small>{user.email}</small>
                    {/* The controls to the right are an editable draft, seeded
                        from this record but free to diverge from it as soon as
                        the operator touches them. This line is the only
                        remaining read of what is actually stored — without it
                        there is no way to tell a pending edit apart from the
                        real grant. */}
                    <small className="admin-grant-current">
                      currently: {describeGrant(user.role, user.scopes, user.airports)}
                    </small>
                  </td>
                  <td>
                    <select value={draft.role} onChange={(e) => setEditDraft(user, { role: e.target.value })}>
                      <option value="partner">Partner</option>
                      <option value="admin">Admin</option>
                      <option value="super_admin">Super-admin</option>
                    </select>
                  </td>
                  <td>{user.status}</td>
                  <td>
                    {draft.role === "partner" ? (
                      <div className="admin-edit-scopes">
                        {API_KEY_SCOPES.map((scope) => (
                          <label key={scope}>
                            <input
                              type="checkbox"
                              checked={draft.scopes.includes(scope)}
                              onChange={(e) =>
                                setEditDraft(user, {
                                  scopes: e.target.checked
                                    ? [...draft.scopes, scope]
                                    : draft.scopes.filter((s) => s !== scope)
                                })
                              }
                            />
                            {scope}
                          </label>
                        ))}
                      </div>
                    ) : (
                      <em>all (role-granted)</em>
                    )}
                  </td>
                  <td>
                    {draft.role === "partner" ? (
                      <AirportChoice
                        groupName={`edit-airports-${user.id}`}
                        draft={draft}
                        onChange={(patch) => setEditDraft(user, patch)}
                      />
                    ) : (
                      <em>all</em>
                    )}
                  </td>
                  <td>{user.last_login_at ? formatDateTime(user.last_login_at) : "—"}</td>
                  <td>
                    {error && <p className="admin-panel-error">{error}</p>}
                    <button
                      disabled={busy === user.id}
                      onClick={() => {
                        const grant = resolveGrant(errorKey, draft);
                        if (!grant) return;
                        void run(user.id, () =>
                          adminUpdateUser(user.id, grant.role, grant.scopes, grant.airports)
                        );
                      }}
                    >
                      Save grant
                    </button>
                  </td>
                  <td>
                    {user.status === "approved" && (
                      <button
                        className="admin-danger"
                        disabled={busy === user.id}
                        title="Suspends access and revokes every key they hold"
                        onClick={() => run(user.id, () => adminSuspendUser(user.id))}
                      >
                        <Ban size={14} /> Suspend
                      </button>
                    )}
                    {user.status === "suspended" && (
                      <button
                        disabled={busy === user.id}
                        onClick={() => {
                          // Route through the same funnel as Save grant: the
                          // operator is looking at the draft on screen, not the
                          // stored record, so reinstating must submit what the
                          // form shows (and bail on the same validation error
                          // when the draft is incomplete) rather than silently
                          // reinstating the old stored grant underneath it.
                          const grant = resolveGrant(errorKey, draft);
                          if (!grant) return;
                          void run(user.id, () =>
                            adminApproveUser(user.id, grant.role, grant.scopes, grant.airports)
                          );
                        }}
                      >
                        Reinstate
                      </button>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
    </section>
  );
}
