import { useEffect, useState, type FormEvent } from "react";
import { KeyRound, Copy, Ban } from "lucide-react";
import {
  adminCreateApiKey,
  adminListApiKeys,
  adminRevokeApiKey,
  API_KEY_SCOPES,
  ApiError,
  type ApiKeyRecord
} from "../lib/api";
import { formatDateTime } from "../lib/format";

export default function ApiKeysPanel() {
  const [keys, setKeys] = useState<ApiKeyRecord[]>([]);
  const [name, setName] = useState("");
  const [scopes, setScopes] = useState<string[]>(["aggregates:read"]);
  const [airports, setAirports] = useState("");
  const [revealed, setRevealed] = useState<string | null>(null);
  const [status, setStatus] = useState("");
  const [busy, setBusy] = useState(false);

  async function load() {
    try {
      setKeys((await adminListApiKeys()).keys);
    } catch (error) {
      setStatus(error instanceof ApiError ? error.message : "Could not load API keys");
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
    if (!name.trim() || scopes.length === 0) {
      setStatus("A name and at least one scope are required.");
      return;
    }
    setBusy(true);
    setStatus("");
    try {
      const parsedAirports = airports
        .split(",")
        .map((icao) => icao.trim().toUpperCase())
        .filter(Boolean);
      const created = await adminCreateApiKey(name.trim(), scopes, parsedAirports);
      setRevealed(created.key);
      setName("");
      setAirports("");
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
            <button
              type="button"
              onClick={() => void navigator.clipboard.writeText(revealed)}
            >
              <Copy size={14} /> Copy
            </button>
            <button type="button" onClick={() => setRevealed(null)}>
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
          {API_KEY_SCOPES.map((scope) => (
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
          placeholder="Airports (comma separated, blank = all)"
          value={airports}
          onChange={(event) => setAirports(event.target.value)}
        />
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
          <span>Last used</span>
          <span>Status</span>
        </div>
        {keys.length === 0 && <div className="admin-empty">No API keys yet.</div>}
        {keys.map((key) => (
          <div className="admin-row" key={key.id}>
            <span>{key.name}</span>
            <span><code>{key.prefix}</code></span>
            <span>{key.scopes.join(", ")}</span>
            <span>{key.airports ? key.airports.join(", ") : "all"}</span>
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
