import { useEffect, useState, type FormEvent } from "react";
import { LockKeyhole } from "lucide-react";
import { API_BASE, ApiError, adminAuthMethods, adminLogin } from "../lib/api";

export default function AdminLogin({ onAuthed }: { onAuthed: () => void }) {
  const [username, setUsername] = useState("admin");
  const [password, setPassword] = useState("");
  const [status, setStatus] = useState("");
  const [busy, setBusy] = useState(false);
  const [methods, setMethods] = useState<{ google: boolean; password: boolean }>({
    google: false,
    password: true
  });

  useEffect(() => {
    adminAuthMethods()
      .then(setMethods)
      // A failed probe must not hide the password form, or a backend hiccup
      // locks the operator out of the only login that still works.
      .catch(() => setMethods({ google: false, password: true }));
  }, []);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setStatus("");
    try {
      await adminLogin(username, password);
      onAuthed();
    } catch (error) {
      setStatus(error instanceof ApiError ? error.message : "Login failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="admin-login-shell">
      <div className="admin-login-card">
        <div className="admin-login-icon"><LockKeyhole size={24} /></div>
        <div>
          <div className="eyebrow">Circle Jerks admin</div>
          <h1>Sign in</h1>
        </div>
        {methods.google && (
          <>
            {/*
              Plain <a href>, not a fetch: the browser must follow the
              redirect to Google as a top-level navigation. An XHR would be
              blocked by CORS and would not set the session cookie.
            */}
            <a className="admin-google-button" href={`${API_BASE}/admin/auth/google/start`}>
              Continue with Google
            </a>
            {methods.password && <p className="admin-login-divider">or</p>}
          </>
        )}
        {methods.password && (
          <form onSubmit={submit}>
            <label>
              <span>Username</span>
              <input
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                autoComplete="username"
              />
            </label>
            <label>
              <span>Password</span>
              <input
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                autoComplete="current-password"
              />
            </label>
            <button className="primary-action" type="submit" disabled={busy}>
              {busy ? "Signing in…" : "Sign in"}
            </button>
          </form>
        )}
        {status && <div className="admin-form-error">{status}</div>}
      </div>
    </main>
  );
}
