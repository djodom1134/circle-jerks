import { useEffect, useMemo, useRef, useState } from "react";
import {
  ClipboardList,
  LogOut,
  MapPin,
  Network,
  Plane,
  Radio,
  RefreshCw,
  Users
} from "lucide-react";
import {
  adminDashboard,
  adminListUsers,
  adminLogout,
  ApiError,
  type ActivityAircraft,
  type AdminDashboardResponse
} from "./lib/api";
import { formatDateTime } from "./lib/format";
import { canManageUsers, defaultTabFor, visibleTabs, type TabId } from "./lib/adminAccess";
import { useAdminSession } from "./lib/useAdminSession";
import AdminLogin from "./components/AdminLogin";
import PendingUsersPanel from "./components/PendingUsersPanel";
import ApiKeysPanel from "./components/ApiKeysPanel";
import ApiDocsPanel from "./components/ApiDocsPanel";

function shortId(value?: string | null) {
  if (!value) return "-";
  if (value.length <= 14) return value;
  return `${value.slice(0, 8)}...${value.slice(-4)}`;
}

function locationLabel(lat?: number | null, lon?: number | null) {
  if (lat == null || lon == null) return "-";
  return `${lat.toFixed(5)}, ${lon.toFixed(5)}`;
}

function aircraftLabel(row: ActivityAircraft & { registration?: string | null }) {
  const primary = row.callsign || row.registration || row.icao24.toUpperCase();
  const detail = row.registration && row.registration !== primary ? ` / ${row.registration}` : "";
  return `${primary}${detail} (${row.icao24})`;
}

function mapUrl(lat?: number | null, lon?: number | null) {
  if (lat == null || lon == null) return null;
  return `https://www.openstreetmap.org/?mlat=${lat}&mlon=${lon}#map=15/${lat}/${lon}`;
}

function StatCard({ icon: Icon, label, value }: { icon: typeof ClipboardList; label: string; value: number }) {
  return (
    <div className="admin-stat">
      <Icon size={19} />
      <span>{label}</span>
      <strong>{value.toLocaleString()}</strong>
    </div>
  );
}

const TAB_LABELS: Record<TabId, string> = {
  dashboard: "Dashboard",
  users: "Access requests",
  keys: "API keys",
  docs: "API docs"
};

/**
 * The "you can't be here" screen — used both for an explicitly blocked
 * status (pending/rejected/suspended, surfaced via a 403's `detail`) and as a
 * fallback for a session that isn't blocked but also isn't approved for any
 * tab. Defined at module scope: an inline definition would get a new
 * identity every render and remount its subtree needlessly.
 */
function BlockedScreen({
  title,
  body,
  onSignOut
}: {
  title: string;
  body: string;
  onSignOut: () => void;
}) {
  return (
    <main className="admin-login-shell">
      <div className="admin-login-card">
        <h1>{title}</h1>
        <p>{body}</p>
        <button className="primary-action" onClick={onSignOut}>
          <LogOut size={17} /> Sign out
        </button>
      </div>
    </main>
  );
}

export default function AdminDashboard() {
  const { session, blockedStatus, loading: sessionLoading, refresh } = useAdminSession();
  const tabs = useMemo(() => visibleTabs(session), [session]);
  const [tab, setTab] = useState<TabId>("keys");
  // Applies defaultTabFor exactly once per sign-in, not on every session
  // refresh: a super_admin who has deliberately switched to another tab must
  // not get yanked back to "dashboard" the next time the session is
  // re-probed (e.g. the dashboard poll's 401/403 handler).
  const appliedDefaultTab = useRef(false);

  useEffect(() => {
    if (session) {
      if (!appliedDefaultTab.current) {
        appliedDefaultTab.current = true;
        setTab(defaultTabFor(session));
      }
    } else {
      appliedDefaultTab.current = false;
    }
  }, [session]);

  useEffect(() => {
    // Keep the selected tab reachable: a partner must never be left staring
    // at a dashboard tab that no longer exists for them, and a role edit
    // (e.g. narrowing someone from admin to partner) must not leave the
    // shell parked on a tab that just disappeared either.
    if (tabs.length > 0 && !tabs.includes(tab)) setTab(tabs[0]);
  }, [tabs, tab]);

  const [dashboard, setDashboard] = useState<AdminDashboardResponse | null>(null);
  const [dashboardLoading, setDashboardLoading] = useState(false);
  const [dashboardStatus, setDashboardStatus] = useState("");

  // The pending-requests badge is the entire notification mechanism for new
  // access requests (email notification is deliberately out of scope, per
  // the design doc). It has to be visible on the tab label itself, not only
  // inside the panel, or a super_admin who lands on "dashboard" by default
  // has no reason to ever discover it.
  const [pendingCount, setPendingCount] = useState<number | null>(null);

  useEffect(() => {
    if (!canManageUsers(session)) {
      setPendingCount(null);
      return;
    }
    let cancelled = false;
    async function loadPendingCount() {
      try {
        const body = await adminListUsers("pending");
        if (!cancelled) setPendingCount(body.users.length);
      } catch {
        // Silent: the users tab itself surfaces a load failure when opened;
        // this badge is a convenience, not the source of truth.
      }
    }
    void loadPendingCount();
    const id = window.setInterval(() => void loadPendingCount(), 30000);
    return () => {
      cancelled = true;
      window.clearInterval(id);
    };
  }, [session]);

  const recentSubmissions = dashboard?.recent_submissions ?? [];
  const summary = useMemo(() => dashboard?.summary, [dashboard]);

  async function loadDashboard() {
    setDashboardLoading(true);
    setDashboardStatus("");
    try {
      setDashboard(await adminDashboard());
    } catch (error) {
      if (error instanceof ApiError && (error.status === 401 || error.status === 403)) {
        // The session cookie died mid-visit. Re-probe through the shared
        // session hook rather than just showing an error over stale numbers,
        // so the shell drops to the login/blocked screen like it should.
        void refresh();
      } else {
        setDashboardStatus(error instanceof ApiError ? error.message : "Dashboard failed");
      }
    } finally {
      setDashboardLoading(false);
    }
  }

  useEffect(() => {
    if (tab !== "dashboard") return;
    void loadDashboard();
    const id = window.setInterval(() => void loadDashboard(), 30000);
    return () => window.clearInterval(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tab]);

  async function signOut() {
    await adminLogout().catch(() => undefined);
    await refresh();
  }

  if (sessionLoading) {
    return (
      <main className="admin-shell">
        <p>Loading…</p>
      </main>
    );
  }

  if (blockedStatus) {
    const copy =
      blockedStatus === "pending"
        ? {
            title: "Access requested",
            body: "Your request is waiting for an administrator to review it."
          }
        : {
            title: "Access denied",
            body: "This account does not have access to the developer area."
          };
    return <BlockedScreen title={copy.title} body={copy.body} onSignOut={signOut} />;
  }

  if (!session) return <AdminLogin onAuthed={refresh} />;

  if (tabs.length === 0) {
    // Today the backend returns 403 for any non-approved status, which the
    // blockedStatus branch above already catches, making this unreachable in
    // practice. It's a defensive fallback: without it, a session object that
    // somehow carries a non-approved status without a matching 403 would
    // render a topbar and an empty nav with no panel and no explanation.
    return (
      <BlockedScreen
        title="Access denied"
        body="This account does not have access to the developer area."
        onSignOut={signOut}
      />
    );
  }

  return (
    <main className="admin-shell">
      <header className="admin-topbar">
        <div>
          <div className="eyebrow">Circle Jerks admin</div>
          <h1>{session.name ?? session.email}</h1>
          <p>
            Signed in as {session.email} · {session.role.replace("_", "-")}
          </p>
        </div>
        <div className="admin-actions">
          {tab === "dashboard" && (
            <button onClick={() => void loadDashboard()} disabled={dashboardLoading}>
              <RefreshCw size={17} /> Refresh
            </button>
          )}
          <button onClick={signOut}>
            <LogOut size={17} /> Sign out
          </button>
        </div>
      </header>

      <nav className="segmented">
        {tabs.map((t) => (
          <button key={t} className={tab === t ? "active" : ""} onClick={() => setTab(t)}>
            {TAB_LABELS[t]}
            {t === "users" && !!pendingCount && <span className="admin-badge">{pendingCount}</span>}
          </button>
        ))}
      </nav>

      {tab === "dashboard" && (
        <>
          {dashboardStatus && <div className="admin-banner">{dashboardStatus}</div>}

          <section className="admin-stats">
            <StatCard icon={ClipboardList} label="Submissions" value={summary?.submissions ?? 0} />
            <StatCard icon={Plane} label="Aircraft reports" value={summary?.aircraft_reports ?? 0} />
            <StatCard icon={Users} label="Submitters" value={summary?.submitters ?? 0} />
            <StatCard icon={Radio} label="Current users" value={summary?.current_users ?? 0} />
            <StatCard icon={MapPin} label="Airports" value={summary?.airports ?? 0} />
            <StatCard icon={Network} label="Distinct aircraft" value={summary?.distinct_aircraft ?? 0} />
          </section>

          <section className="admin-grid two">
            <div className="admin-panel">
              <div className="admin-section-title">Current users</div>
              <div className="admin-table compact">
                <div className="admin-row header"><span>User</span><span>IP</span><span>Airport</span><span>Location</span><span>Last seen</span><span>Submissions</span></div>
                {dashboard?.current_users.length === 0 && <div className="admin-empty">No active users in the current window.</div>}
                {dashboard?.current_users.map((row) => (
                  <div className="admin-row" key={row.visitor_id}>
                    <span>{shortId(row.visitor_id)}</span>
                    <span>{row.ip_address ?? "-"}</span>
                    <span>{row.airport_icao ?? "-"}<small>{row.airport_city ?? ""}</small></span>
                    <span>{locationLabel(row.user_lat, row.user_lon)}</span>
                    <span>{formatDateTime(row.last_seen)}</span>
                    <span>{row.submission_count}</span>
                  </div>
                ))}
              </div>
            </div>

            <div className="admin-panel">
              <div className="admin-section-title">IP submission history</div>
              <div className="admin-table ip-table">
                <div className="admin-row header"><span>IP address</span><span>Submissions</span><span>Visitors</span><span>Active</span><span>Last</span></div>
                {dashboard?.ip_history.length === 0 && <div className="admin-empty">No copied submissions yet.</div>}
                {dashboard?.ip_history.map((row) => (
                  <div className="admin-row" key={row.ip_address}>
                    <span>{row.ip_address}</span>
                    <span>{row.submissions}</span>
                    <span>{row.visitors}</span>
                    <span>{row.active_visitors}</span>
                    <span>{formatDateTime(row.last_submission_at)}</span>
                  </div>
                ))}
              </div>
            </div>
          </section>

          <section className="admin-panel">
            <div className="admin-section-title">Recent submissions</div>
            <div className="admin-submissions">
              {recentSubmissions.length === 0 && <div className="admin-empty">No copied complaint text has been recorded.</div>}
              {recentSubmissions.map((row) => (
                <article className="submission-card" key={row.id}>
                  <div className="submission-meta">
                    <strong>{formatDateTime(row.created_at)}</strong>
                    <span>{row.airport_icao ?? "-"} {row.airport_city ? `· ${row.airport_city}` : ""}</span>
                    <span>{row.mode ?? "-"} · {row.window_code ?? "-"}</span>
                    <span>{row.ip_address ?? "-"}</span>
                    <span>{locationLabel(row.user_lat, row.user_lon)}</span>
                  </div>
                  <div className="submission-aircraft">
                    {row.aircraft.map(aircraftLabel).join(", ") || "No aircraft captured"}
                  </div>
                  <p>{row.text}</p>
                </article>
              ))}
            </div>
          </section>

          <section className="admin-grid two">
            <div className="admin-panel">
              <div className="admin-section-title">Aircraft report counts</div>
              <div className="admin-table aircraft-table">
                <div className="admin-row header"><span>Aircraft GUID</span><span>Callsign</span><span>Tail</span><span>Reports</span><span>Last</span></div>
                {dashboard?.aircraft_reports.length === 0 && <div className="admin-empty">No aircraft have been reported yet.</div>}
                {dashboard?.aircraft_reports.map((row) => (
                  <div className="admin-row" key={row.icao24}>
                    <span>{row.icao24}</span>
                    <span>{row.callsign ?? "-"}</span>
                    <span>{row.registration ?? "-"}</span>
                    <span>{row.report_count}</span>
                    <span>{formatDateTime(row.last_reported_at)}</span>
                  </div>
                ))}
              </div>
            </div>

            <div className="admin-panel">
              <div className="admin-section-title">Submitter locations</div>
              <div className="admin-table location-table">
                <div className="admin-row header"><span>User</span><span>IP</span><span>Airport</span><span>Location</span><span>Submissions</span></div>
                {dashboard?.locations.length === 0 && <div className="admin-empty">No submitter locations have been recorded.</div>}
                {dashboard?.locations.map((row) => {
                  const url = mapUrl(row.user_lat, row.user_lon);
                  return (
                    <div className="admin-row" key={row.visitor_id}>
                      <span>{shortId(row.visitor_id)}</span>
                      <span>{row.ip_address ?? "-"}</span>
                      <span>{row.airport_icao ?? "-"}<small>{row.airport_city ?? ""}</small></span>
                      <span>
                        {url ? <a href={url} target="_blank" rel="noreferrer">{locationLabel(row.user_lat, row.user_lon)}</a> : "-"}
                      </span>
                      <span>{row.submission_count}</span>
                    </div>
                  );
                })}
              </div>
            </div>
          </section>

          <section className="admin-panel">
            <div className="admin-section-title">Airport submissions</div>
            <div className="admin-table airport-table">
              <div className="admin-row header"><span>Airport</span><span>Name</span><span>City</span><span>Submissions</span><span>Submitters</span><span>Last</span></div>
              {dashboard?.airports.length === 0 && <div className="admin-empty">No airport submission activity yet.</div>}
              {dashboard?.airports.map((row) => (
                <div className="admin-row" key={row.airport_icao}>
                  <span>{row.airport_icao}</span>
                  <span>{row.name ?? "-"}</span>
                  <span>{row.city ?? "-"}</span>
                  <span>{row.submissions}</span>
                  <span>{row.submitters}</span>
                  <span>{formatDateTime(row.last_submission_at)}</span>
                </div>
              ))}
            </div>
          </section>
        </>
      )}

      {tab === "users" && <PendingUsersPanel />}
      {tab === "keys" && <ApiKeysPanel session={session} />}
      {tab === "docs" && <ApiDocsPanel />}
    </main>
  );
}
