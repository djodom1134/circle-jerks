import { useEffect, useState, type FormEvent } from "react";
import { KeyRound, Copy, Ban } from "lucide-react";
import {
  adminCreateApiKey,
  adminListApiKeys,
  adminRevokeApiKey,
  ApiError,
  type ApiKeyRecord
} from "../lib/api";
import { formatDateTime } from "../lib/format";
import { airportRequired, allowedScopes, clampScopes, type AdminSession } from "../lib/adminAccess";

export default function ApiKeysPanel({ session }: { session: AdminSession | null }) {
  const scopeOptions = allowedScopes(session);
  const requireAirports = airportRequired(session);
  const permittedAirports = session?.airports ?? null;

  const [keys, setKeys] = useState<ApiKeyRecord[]>([]);
  const [name, setName] = useState("");
  // Default to "aggregates:read" when it is in the caller's grant (matching
  // the previous default for admins); otherwise fall back to whatever the
  // grant actually allows, so a partner never opens the form to a dead
  // checkbox for a scope they cannot pick.
  const [scopes, setScopes] = useState<string[]>(() => {
    const preferred = clampScopes(["aggregates:read"], session);
    return preferred.length ? preferred : scopeOptions;
  });
  const [airports, setAirports] = useState(() =>
    requireAirports ? (permittedAirports ?? []).join(", ") : ""
  );
  const [revealed, setRevealed] = useState<string | null>(null);
  const [status, setStatus] = useState("");
  const [busy, setBusy] = useState(false);
  // Distinct from `keys.length === 0`. Without it a failed load renders the
  // "No API keys yet" empty state, which an admin can read as truth and mint a
  // duplicate of a key they have already issued.
  const [loadFailed, setLoadFailed] = useState(false);
  const [copied, setCopied] = useState(false);

  async function load() {
    try {
      setKeys((await adminListApiKeys()).keys);
      setLoadFailed(false);
    } catch (error) {
      setLoadFailed(true);
      setStatus(error instanceof ApiError ? error.message : "Could not load API keys");
    }
  }

  async function copyKey(value: string) {
    // navigator.clipboard is undefined in insecure contexts, and writeText
    // rejects when permission is denied. Either way the admin must be told,
    // because the key is unrecoverable once this box is dismissed.
    try {
      if (!navigator.clipboard?.writeText) throw new Error("clipboard unavailable");
      await navigator.clipboard.writeText(value);
      setCopied(true);
    } catch {
      setCopied(false);
      setStatus("Could not copy automatically — select the key above and copy it manually.");
    }
  }

  useEffect(() => {
    void load();
  }, []);

  function toggleScope(scope: string) {
    setScopes((current) =>
      current.includes(scope) ? current.filter((s) => s !== scope) : [...current, scope]
    );
  }

  async function create(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    // Presentation only — the server's enforce_grant is the real boundary.
    // Clamping here just keeps the form from submitting a request that is
    // certain to be refused.
    const clamped = clampScopes(scopes, session);
    if (!name.trim() || clamped.length === 0) {
      setStatus("A name and at least one allowed scope are required.");
      return;
    }
    const parsedAirports = airports
      .split(",")
      .map((icao) => icao.trim().toUpperCase())
      .filter(Boolean);
    if (requireAirports && parsedAirports.length === 0) {
      setStatus(
        `Airports required — this grant is restricted to: ${(permittedAirports ?? []).join(", ")}`
      );
      return;
    }
    setBusy(true);
    setStatus("");
    try {
      const created = await adminCreateApiKey(name.trim(), clamped, parsedAirports);
      setRevealed(created.key);
      setName("");
      setAirports(requireAirports ? (permittedAirports ?? []).join(", ") : "");
      await load();
    } catch (error) {
      setStatus(error instanceof ApiError ? error.message : "Could not create the key");
    } finally {
      setBusy(false);
    }
  }

  async function revoke(id: string, keyName: string) {
    setBusy(true);
    try {
      await adminRevokeApiKey(id);
      setStatus(`Revoked ${keyName}.`);
      await load();
    } catch (error) {
      setStatus(error instanceof ApiError ? error.message : "Could not revoke the key");
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="admin-panel">
      <div className="admin-section-title">
        <KeyRound size={16} /> API keys
      </div>

      {revealed && (
        <div className="api-key-reveal">
          <strong>Copy this key now — it will not be shown again.</strong>
          <code>{revealed}</code>
          <div className="api-key-reveal-actions">
            <button type="button" onClick={() => void copyKey(revealed)}>
              <Copy size={14} /> {copied ? "Copied" : "Copy"}
            </button>
            <button
              type="button"
              onClick={() => {
                setRevealed(null);
                setCopied(false);
              }}
            >
              Done
            </button>
          </div>
        </div>
      )}

      <form className="api-key-form" onSubmit={create}>
        <input
          type="text"
          placeholder="Key name (e.g. lmco-mirror)"
          value={name}
          onChange={(event) => setName(event.target.value)}
        />
        <div className="api-key-scopes">
          {scopeOptions.map((scope) => (
            <label key={scope}>
              <input
                type="checkbox"
                checked={scopes.includes(scope)}
                onChange={() => toggleScope(scope)}
              />
              {scope}
            </label>
          ))}
        </div>
        <input
          type="text"
          placeholder={
            requireAirports
              ? "Airports (required)"
              : "Airports (comma separated, blank = all)"
          }
          value={airports}
          onChange={(event) => setAirports(event.target.value)}
          required={requireAirports}
        />
        {requireAirports && (
          <p className="admin-panel-status">
            Your access is restricted to: {(permittedAirports ?? []).join(", ")}
          </p>
        )}
        <button type="submit" disabled={busy}>
          Create key
        </button>
      </form>

      {status && <div className="admin-empty">{status}</div>}

      <div className="admin-table api-key-table">
        <div className="admin-row header">
          <span>Name</span>
          <span>Prefix</span>
          <span>Scopes</span>
          <span>Airports</span>
          <span>Owner</span>
          <span>Last used</span>
          <span>Status</span>
        </div>
        {keys.length === 0 && (
          <div className="admin-empty">
            {loadFailed ? (
              <>
                Could not load the key list — this is <em>not</em> confirmation that
                no keys exist.{" "}
                <button type="button" onClick={() => void load()}>Retry</button>
              </>
            ) : (
              "No API keys yet."
            )}
          </div>
        )}
        {keys.map((key) => (
          <div className={`admin-row${key.owned ? " admin-row-owned" : ""}`} key={key.id}>
            <span>{key.name}</span>
            <span><code>{key.prefix}</code></span>
            <span>{key.scopes.join(", ")}</span>
            <span>{key.airports ? key.airports.join(", ") : "all"}</span>
            <span>
              {key.owner_user_id === null ? (
                <em>legacy (unowned)</em>
              ) : (
                <>
                  {key.created_by ?? "—"}
                  {key.owned && <span className="admin-badge" title="Owned by you">you</span>}
                </>
              )}
            </span>
            <span>{formatDateTime(key.last_used_at)}</span>
            <span>
              {key.revoked_at ? (
                `Revoked ${formatDateTime(key.revoked_at)}`
              ) : (
                <button
                  type="button"
                  disabled={busy}
                  onClick={() => void revoke(key.id, key.name)}
                >
                  <Ban size={14} /> Revoke
                </button>
              )}
            </span>
          </div>
        ))}
      </div>
    </section>
  );
}
