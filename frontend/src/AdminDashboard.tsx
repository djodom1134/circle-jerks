import { useEffect, useMemo, useState, type FormEvent } from "react";
import {
  ClipboardList,
  LockKeyhole,
  LogOut,
  MapPin,
  Network,
  Plane,
  Radio,
  RefreshCw,
  ShieldCheck,
  Users
} from "lucide-react";
import {
  adminDashboard,
  adminLogin,
  adminLogout,
  ApiError,
  type ActivityAircraft,
  type AdminDashboardResponse
} from "./lib/api";
import { formatDateTime } from "./lib/format";
import ApiKeysPanel from "./components/ApiKeysPanel";

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

function LoginPanel({ onAuthed }: { onAuthed: () => void }) {
  const [username, setUsername] = useState("admin");
  const [password, setPassword] = useState("");
  const [status, setStatus] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setStatus("");
    try {
      await adminLogin(username, password);
      onAuthed();
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "Login failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="admin-login-shell">
      <form className="admin-login-card" onSubmit={submit}>
        <div className="admin-login-icon"><LockKeyhole size={24} /></div>
        <div>
          <div className="eyebrow">Admin dashboard</div>
          <h1>Sign in</h1>
        </div>
        <label>
          <span>Username</span>
          <input value={username} onChange={(event) => setUsername(event.target.value)} autoComplete="username" />
        </label>
        <label>
          <span>Password</span>
          <input
            type="password"
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            autoComplete="current-password"
            autoFocus
          />
        </label>
        {status && <div className="admin-form-error">{status}</div>}
        <button className="primary-action" type="submit" disabled={busy}>
          <ShieldCheck size={17} /> {busy ? "Signing in" : "Sign in"}
        </button>
      </form>
    </main>
  );
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

export default function AdminDashboard() {
  const [dashboard, setDashboard] = useState<AdminDashboardResponse | null>(null);
  const [authed, setAuthed] = useState(false);
  const [loading, setLoading] = useState(true);
  const [status, setStatus] = useState("");

  const recentSubmissions = dashboard?.recent_submissions ?? [];

  const summary = useMemo(() => dashboard?.summary, [dashboard]);

  async function load() {
    setLoading(true);
    setStatus("");
    try {
      const data = await adminDashboard();
      setDashboard(data);
      setAuthed(true);
    } catch (error) {
      if (error instanceof ApiError && error.status === 401) {
        setAuthed(false);
      } else {
        setStatus(error instanceof Error ? error.message : "Dashboard failed");
      }
    } finally {
      setLoading(false);
    }
  }

  async function logout() {
    await adminLogout().catch(() => undefined);
    setDashboard(null);
    setAuthed(false);
  }

  useEffect(() => {
    load();
    const id = window.setInterval(() => {
      if (authed) void load();
    }, 30000);
    return () => window.clearInterval(id);
  }, [authed]);

  if (!authed && !loading) return <LoginPanel onAuthed={load} />;

  return (
    <main className="admin-shell">
      <header className="admin-topbar">
        <div>
          <div className="eyebrow">Circle Jerks admin</div>
          <h1>Operations dashboard</h1>
          <p>Submissions are recorded when copied from the complaint panel.</p>
        </div>
        <div className="admin-actions">
          <button onClick={load} disabled={loading}><RefreshCw size={17} /> Refresh</button>
          <button onClick={logout}><LogOut size={17} /> Sign out</button>
        </div>
      </header>

      {status && <div className="admin-banner">{status}</div>}

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

      <ApiKeysPanel />
    </main>
  );
}
