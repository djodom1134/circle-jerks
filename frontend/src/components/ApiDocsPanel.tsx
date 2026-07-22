import { useState } from "react";
import { BookOpen, Copy } from "lucide-react";
import spec from "../generated/openapi.json";

interface ParameterObject {
  name: string;
  in: string;
  required?: boolean;
  description?: string;
}

interface Operation {
  summary?: string;
  description?: string;
  security?: Array<Record<string, string[]>>;
  // This generated spec declares path/query parameters as `$ref` pointers
  // into components.parameters rather than inlining them, so each entry is
  // one or the other.
  parameters?: Array<ParameterObject | { $ref: string }>;
}

const METHODS = ["get", "post", "put", "patch", "delete"] as const;

const components = (spec as any).components as
  | { parameters?: Record<string, ParameterObject> }
  | undefined;

/** Resolve a `$ref` parameter against components.parameters; pass inline
 *  parameter objects through unchanged. Returns null if the ref is dangling
 *  (shouldn't happen for a spec generated from real routes, but a stray
 *  bullet with no name is worse than quietly skipping it). */
function resolveParameter(param: ParameterObject | { $ref: string }): ParameterObject | null {
  if (!("$ref" in param)) return param;
  const key = param.$ref.split("/").pop();
  return (key && components?.parameters?.[key]) || null;
}

function curlFor(path: string): string {
  const base = (spec as any).servers?.[0]?.url ?? "https://circlejerks.live/api/v1";
  return `curl -H "X-Api-Key: $CIRCLEJERK_API_KEY" \\\n  "${base}${path}"`;
}

export default function ApiDocsPanel() {
  const [copied, setCopied] = useState<string | null>(null);
  const paths = (spec as any).paths as Record<string, Record<string, Operation>>;

  async function copy(value: string, id: string) {
    try {
      if (!navigator.clipboard?.writeText) throw new Error("clipboard unavailable");
      await navigator.clipboard.writeText(value);
      setCopied(id);
    } catch {
      setCopied(null);
    }
  }

  return (
    <section className="admin-panel admin-docs">
      <h2><BookOpen size={18} /> API documentation</h2>
      <p>
        Authenticate with the <code>X-Api-Key</code> header. A key only reaches the scopes and
        airports it was issued for.
      </p>
      {Object.entries(paths).map(([path, operations]) => (
        <article key={path} className="admin-docs-endpoint">
          <h3><code>{path}</code></h3>
          {METHODS.filter((m) => operations[m]).map((method) => {
            const operation = operations[method];
            const scopes = operation.security?.flatMap((s) => Object.values(s).flat()) ?? [];
            return (
              <div key={method}>
                <p>
                  <span className="admin-docs-method">{method.toUpperCase()}</span>
                  {operation.summary}
                </p>
                {scopes.length > 0 && (
                  <p className="admin-docs-scopes">
                    Requires: {scopes.map((s) => <code key={s}>{s}</code>)}
                  </p>
                )}
                {operation.description && <p>{operation.description}</p>}
                {operation.parameters && operation.parameters.length > 0 && (
                  <ul className="admin-docs-params">
                    {operation.parameters.map((raw, index) => {
                      const p = resolveParameter(raw);
                      if (!p) return null;
                      return (
                        <li key={`${p.in}:${p.name}:${index}`}>
                          <code>{p.name}</code> <em>({p.in}{p.required ? ", required" : ""})</em>
                          {p.description ? ` — ${p.description}` : ""}
                        </li>
                      );
                    })}
                  </ul>
                )}
              </div>
            );
          })}
          <pre className="admin-docs-curl">
            <code>{curlFor(path)}</code>
            <button onClick={() => copy(curlFor(path), path)}>
              <Copy size={14} /> {copied === path ? "Copied" : "Copy"}
            </button>
          </pre>
        </article>
      ))}
    </section>
  );
}
