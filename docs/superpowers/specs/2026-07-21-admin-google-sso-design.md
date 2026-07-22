# Admin Google SSO, user approval, and self-service API keys

**Date:** 2026-07-21
**Status:** Approved design, ready for planning
**Branch base:** `feat/public-api-keys` (PR #2). Not `main` — see the repo branch note in `.superpowers/sdd/progress.md`.

## Problem

The admin dashboard has exactly one account: a username and password from
`Settings`, verified against `admin_username` / `admin_password_hash`, producing an
HMAC-signed `circlejerk_admin` cookie whose only claim is `sub == admin_username`.
Every `/admin/*` route sits behind the single `require_admin` dependency.

That works for one operator. It does not work for the thing we now want: letting a
handful of partners sign in with Google, wait for approval, and then mint their own
`/v1` API keys without an operator hand-delivering credentials — and without any one
of them being able to mint a key wider than what they were granted.

## Goals

1. Sign in with Google. New accounts land in a pending state and can do nothing.
2. A super-admin sees pending requests, approves or rejects them, and assigns a role
   plus a scope/airport ceiling.
3. Approved users mint, list, and revoke **their own** API keys, bounded by that ceiling.
4. The partner-facing API documentation is published in the same area where keys are made.

## Non-goals

- Any identity provider other than Google.
- Email notification when someone requests access. There is no mail infrastructure in
  this project; the pending count is a badge in the dashboard. Revisit if the list is
  ever missed in practice.
- Per-key rate limits, expiry, or row caps. Explicitly declined in the previous design
  (`2026-07-20-public-api-keys-design.md`); guardrails remain fixed globals.
- Self-service registration for the *public* site. This is admin/partner access only.

## Decisions

Seven decisions were settled before this document was written. Each is recorded with
the reasoning, because the reasoning is what a future reader will need.

### D1. The password login survives as break-glass

Google becomes the normal path. The username/password login keeps working and always
resolves to a super-admin identity. If the OAuth client secret expires, the consent
screen is misconfigured, or Google is unreachable, the operator can still get in
without a redeploy.

**Mechanically, the password login upserts a real row** (`id = "local-admin"`,
`email = "local-admin@invalid"`, `role = super_admin`, `status = approved`)
rather than minting a synthetic identity. Every downstream consumer — key ownership,
`created_by`, `decided_by`, the audit trail — then has exactly one identity model with
no special case to thread through.

### D2. Super-admins are bootstrapped from an env allowlist

`ADMIN_SUPERUSERS` holds a comma-separated list of email addresses. Any address on
that list is forced to `role = super_admin`, `status = approved` **on every login**,
not merely at creation.

- Survives a database wipe.
- No chicken-and-egg problem on a fresh deploy.
- No "first visitor to hit a fresh database owns the admin" race.
- A UI misclick cannot demote you, because the next login restores the role.

Rejected: first-sign-in-wins (race on a fresh database), and promote-via-password
(couples bootstrap to a login we want to keep vestigial).

### D3. Roles cap what keys a user may mint

Self-service key creation is a privilege-escalation surface. A role that only gated
which *pages* you see would let a partner approved for KLMO hand themselves a
full-fleet `ops:read` key. So the role, together with a per-user grant, is the ceiling
the server enforces on every key request.

| Role | Dashboard | May mint keys | Manage users |
|---|---|---|---|
| `super_admin` | full | unrestricted | yes |
| `admin` | full | unrestricted | no |
| `partner` | Keys + Docs only | within their granted scopes and airports | no |

The approval form is where a super-admin sets a partner's ceiling.

### D4. Anyone with a verified Google email may request access

Open sign-in, gated on `email_verified == true`. This matches the intended flow: a
partner signs in, lands in pending, and asks you to approve them.

The pending list is therefore internet-reachable and could be filled with junk rows by
a bot. Accepted, because a pending user can do *nothing* — no data, no keys, no routes.
Mitigated by rate-limiting the start and callback endpoints. Invite-first was rejected
for inverting the flow (approving people before they ask).

### D5. Four lifecycle verbs, with cascades

`approve`, `reject`, `suspend`, and editing a grant.

- **Reject** moves a pending user to a terminal state so they stop reappearing in the
  list on every re-login.
- **Suspend** revokes all of that user's API keys in the same transaction. Suspending
  someone while their keys stay live is the obvious foot-gun; the cascade removes it.
- **Editing a grant** never retroactively widens existing keys, because each key stores
  its own scopes. **Narrowing** a grant revokes the keys that now exceed it, in the
  same transaction, for the same reason suspend cascades.

### D6. Partners land on `/developers`, served by the same component

`App.tsx` routes on pathname prefix with no router library. Adding `/developers`
alongside `/admin` in that switch gives partners a URL that isn't labelled "admin",
while keeping one component, one session path, and one login screen. Which URL was
used does not affect what renders — **role does**.

### D7. The docs panel is built from `docs/api/openapi.yaml`

A build step converts the hand-written partner spec to committed JSON that the SPA
imports. This makes `docs/api/openapi.yaml` the single source of truth and resolves the
open question carried over from the previous session: the served `/v1/openapi.json` was
thinner than the partner document. It will now be generated from the same file.

Rejected: bundling Scalar or Redoc (heavy dependency that will not match the admin's
visual language) and a hand-written prose page (drifts the moment an endpoint changes).

## Architecture

### Data model

One new table:

```sql
CREATE TABLE IF NOT EXISTS admin_users (
  id              TEXT PRIMARY KEY,
  email           TEXT NOT NULL UNIQUE,   -- lowercased at write time
  google_sub      TEXT UNIQUE,            -- Google's stable subject identifier
  name            TEXT,
  picture         TEXT,
  role            TEXT NOT NULL,          -- super_admin | admin | partner
  status          TEXT NOT NULL,          -- pending | approved | rejected | suspended
  granted_scopes  TEXT,                   -- JSON array; ceiling for minted keys
  granted_airports TEXT,                  -- JSON array; NULL means unrestricted
  requested_at    INTEGER NOT NULL,
  decided_at      INTEGER,
  decided_by      TEXT,                   -- admin_users.id
  last_login_at   INTEGER
);
CREATE INDEX IF NOT EXISTS idx_admin_users_status ON admin_users(status, requested_at DESC);
```

Added to the `SCHEMA` string in `db.py` alongside the existing `CREATE TABLE IF NOT
EXISTS` statements.

`api_keys` gains one column through the existing `_migrate` additions dict:

```python
"api_keys": [("owner_user_id", "TEXT")],
```

Keys created before this feature have `owner_user_id IS NULL`. They render as
*legacy / unowned* and are visible only to super-admins. They are not deleted or
reassigned; ownership is unknowable and guessing it would be worse than labelling it.

### Identity resolution

On a successful callback, in order:

1. Match on `google_sub`.
2. Failing that, match on the verified `email` (lowercased).
3. Failing that, create a new row: `status = pending`, `role = partner`, empty grant.

Step 2 is safe **only** because `email_verified` is asserted before this point. Without
that assertion, matching on email is an account-takeover primitive.

If the email appears in `ADMIN_SUPERUSERS`, force `role = super_admin` and
`status = approved` after resolution, regardless of what the row said (D2).

### Session

The cookie name (`circlejerk_admin`), the HMAC signing scheme, and the TTL
(`admin_session_seconds`) are unchanged. The payload claim changes:

```diff
- {"sub": settings.admin_username, "exp": ..., "nonce": ...}
+ {"uid": <admin_users.id>,        "exp": ..., "nonce": ...}
```

`decode_admin_token` stops comparing against `settings.admin_username` and the auth
dependency **loads the user row on every request**. That per-request indexed read is
the entire reason suspension takes effect immediately rather than whenever a 12-hour
cookie happens to expire. On a SQLite database this cost is negligible.

Three dependencies replace the single `require_admin`:

| Dependency | Passes for | Applied to |
|---|---|---|
| `require_user` | any `approved` user | key routes |
| `require_admin` | `admin`, `super_admin` | every existing `/admin/*` route, unchanged |
| `require_super_admin` | `super_admin` only | user management, all-keys view |

**The name `require_admin` is deliberately retained** with tightened semantics, so the
roughly twenty existing routes that depend on it are untouched by this change. A
rename would inflate the diff and bury the parts that actually need review.

A non-approved user's request fails with a status the SPA can act on: the response
distinguishes `pending`, `rejected`, and `suspended` so the correct screen renders,
while leaking nothing beyond the caller's own state.

### Cookie SameSite change

`samesite="strict"` becomes `samesite="lax"`.

Strict is unreliable across the Google → callback → `/admin` redirect chain, because
the final navigation continues from a cross-site initiator. Lax still refuses to send
the cookie on cross-site POST, and every state-changing admin route is POST or PATCH,
so the practical CSRF surface is unchanged.

This is recorded explicitly because it is a deliberate weakening of a flag on an admin
session cookie, and a reviewer should be able to find the reasoning rather than
rediscover it.

### OAuth flow

**`GET /admin/auth/google/start`**

Generates a random `state` and a PKCE verifier, stores both in a signed, `HttpOnly`,
10-minute cookie, and redirects to Google's authorization endpoint with
`scope=openid email profile` and `prompt=select_account`.

**`GET /admin/auth/google/callback?code&state`**

1. Compare `state` against the cookie; reject on mismatch or absence.
2. Exchange the code server-side at `https://oauth2.googleapis.com/token` via `httpx`,
   sending the client secret and the PKCE verifier.
3. Decode the `id_token` payload and assert `aud == client_id`,
   `iss ∈ {accounts.google.com, https://accounts.google.com}`, `exp > now`, and
   `email_verified is true`.
4. Resolve identity (above), stamp `last_login_at`, set the session cookie, redirect
   to `/admin`. The SPA routes by role from there.

**There is no JWKS fetch, cache, or key-rotation handling.** The ID token arrives over
a direct TLS channel from Google's token endpoint, which OpenID Connect Core §3.1.3.7
explicitly permits as sufficient. Avoiding that machinery — and the failure modes of a
stale key cache — is the primary reason this flow was chosen over a frontend Google
Identity Services button.

Both endpoints are rate-limited (D4).

### Settings

| Variable | Purpose |
|---|---|
| `GOOGLE_OAUTH_CLIENT_ID` | OAuth client |
| `GOOGLE_OAUTH_CLIENT_SECRET` | OAuth client |
| `GOOGLE_OAUTH_REDIRECT_URI` | Stated explicitly, never inferred |
| `ADMIN_SUPERUSERS` | Comma-separated emails, always super-admin |

The redirect URI is configuration, not inference. Deriving it from `X-Forwarded-Prefix`
or request headers is exactly the pattern that produced the inert-servers failure
recorded in `.superpowers/sdd/progress.md`; a wrong value here fails at Google's
consent screen with an error the operator cannot act on.

When the client ID or secret is absent, `/admin/auth/google/start` returns 503 and an
unauthenticated `GET /admin/auth/methods` reports `{"google": false, "password": true}`
so the login screen hides the button rather than offering a dead one.

### Authorization boundary

Grant enforcement is a single function in **`api_keys.py`** — the module kept
deliberately free of FastAPI, sqlite3, and all I/O so the security-critical half of the
auth path is testable in isolation:

```python
def enforce_grant(requested_scopes, requested_airports, grant) -> None:  # raises on violation
```

Rules:

- Requested scopes must be a subset of `granted_scopes`.
- The requested airport must fall within `granted_airports`, or the grant must be
  unrestricted (`NULL`).
- `admin` and `super_admin` carry an implicit unrestricted grant.
- An empty grant can mint nothing.

Every key-minting request passes through it. No route re-implements the check.

**Ownership checks return 404, not 403.** A partner asking about a key they do not own
gets the same response as for a key that does not exist, so key IDs are not enumerable.

### Key routes

| Route | Dependency | Behavior |
|---|---|---|
| `GET /admin/api-keys` | `require_user` | admin and super-admin see all keys plus legacy unowned; partner sees only their own |
| `POST /admin/api-keys` | `require_user` | runs `enforce_grant`; stamps `owner_user_id` and `created_by` (email) |
| `POST /admin/api-keys/{id}/revoke` | `require_user` | partner may revoke only their own; otherwise 404 |

### User management routes

All `require_super_admin`:

| Route | Behavior |
|---|---|
| `GET /admin/users?status=` | list, filterable |
| `POST /admin/users/{id}/approve` | body carries `role`, `scopes`, `airports`. When `role` is `admin` or `super_admin` the grant fields are ignored and stored as unrestricted, because those roles carry an implicit unrestricted grant — storing a narrower value would be a lie the server never enforces |
| `POST /admin/users/{id}/reject` | terminal state |
| `POST /admin/users/{id}/suspend` | cascades: revokes all of that user's keys |
| `PATCH /admin/users/{id}` | edits role and grant; narrowing cascades revocation of out-of-grant keys |

Guards, enforced server-side:

- A user cannot modify their own row.
- A user whose email is in `ADMIN_SUPERUSERS` cannot be modified through the UI.

A third guard — "the last `super_admin` cannot be demoted" — was specified and then
**dropped during plan review, because it can never fire.** Only an approved
super-admin reaches these routes and nobody may target their own row, so any
super-admin target implies at least two exist and demoting one always leaves the
actor. The single edge case where the check *would* have triggered is a target who is
a suspended super-admin, and therefore uncounted — a legitimate edit it would have
wrongly blocked. `ADMIN_SUPERUSERS` (D2) is the actual lockout backstop, and it
survives cases a database-count guard never could.

### Frontend

`AdminDashboard.tsx` is 298 lines today and would roughly double. Extract before adding:

- `components/AdminLogin.tsx` — Google button plus the password fallback, driven by
  `/admin/auth/methods`.
- `lib/useAdminSession.ts` — session and role state.
- `AdminDashboard.tsx` becomes a shell that selects tabs by role.

New:

- `components/PendingUsersPanel.tsx` — pending queue with the approval form (role,
  scopes, airports), plus the approved-user table with suspend and edit.
- `components/ApiDocsPanel.tsx` — renders the generated spec.
- `ApiKeysPanel.tsx` gains owner scoping and a scope picker clamped to the viewer's
  grant. **The clamp is presentation only**; `enforce_grant` on the server is the
  actual boundary.

`App.tsx` gains `/developers` in the pathname switch (currently line 106), rendering
`AdminDashboard`.

Non-approved users never reach the dashboard shell:

| Status | Screen |
|---|---|
| `pending` | "Access requested, awaiting approval" + sign out |
| `rejected` | "Access denied" + sign out |
| `suspended` | "Access denied" + sign out |

### Docs pipeline

`scripts/build_openapi_json.py`, a sibling of the existing `verify_openapi_doc.py`
(which already imports `yaml`, so no new dependency is introduced), reads
`docs/api/openapi.yaml` and writes `frontend/src/generated/openapi.json`. The output is
committed, so the frontend build has no Python dependency.

`verify_openapi_doc.py` gains a check that the committed JSON matches the YAML, so
drift fails loudly through the same mechanism that already catches route drift.

The backend serves the same enriched document at `/v1/openapi.json`, replacing the
thinner generated one — closing the open question from the previous session.

`ApiDocsPanel` renders, per endpoint: path and method, the scope badge required,
parameters, and a copy-paste `curl` example using the `X-Api-Key` header.

## Error handling

| Condition | Response |
|---|---|
| OAuth not configured | 503 from `start`; button hidden via `/admin/auth/methods` |
| `state` mismatch or absent | 400, generic message, no detail on which check failed |
| Token exchange fails | 502, Google's error logged but not surfaced |
| `email_verified` false | 403, "a verified Google account is required" |
| `aud` / `iss` / `exp` invalid | 400, generic |
| Session cookie invalid or expired | 401 |
| Authenticated but `pending` / `rejected` / `suspended` | 403 with the caller's own status |
| Approved but insufficient role | 403 |
| Key request exceeds grant | 400, naming the scopes or airport that exceeded it |
| Key not owned by caller | 404 |

Every `/v1` response continues to use the existing error envelope.

## Testing

The centerpiece is a **role × endpoint authorization matrix**: every route, every role
(`super_admin`, `admin`, `partner`, `pending`, `rejected`, `suspended`, anonymous),
asserted against the expected status. Authorization features fail by leaving one route
on the wrong dependency, and only a matrix catches that.

Beyond it:

**`api_keys.enforce_grant`, in isolation** — scope subset accepted, superset rejected,
airport within grant, airport outside grant, unrestricted grant, empty grant mints
nothing.

**OAuth callback** — `state` mismatch, absent state cookie, `email_verified: false`,
`aud` mismatch, expired `id_token`, code replay, new user becomes pending, allowlisted
email becomes super-admin even when the stored row says otherwise.

**Cascades** — suspend revokes that user's keys and no one else's; narrowing a grant
revokes exactly the out-of-grant keys; widening revokes nothing; editing a grant leaves
already-minted keys unchanged.

**Ownership** — a partner revoking another user's key gets 404; a partner's list
contains only their own keys; legacy unowned keys appear for super-admins only.

**Guards** — self-modification rejected; `ADMIN_SUPERUSERS` account not modifiable; one
super-admin may demote another (pinning the absence of the dropped third guard, so it
is not reintroduced).

**Frontend (vitest)** — tabs render by role; the scope picker clamps to the grant; each
non-approved status renders its screen.

**Docs** — `verify_openapi_doc.py` fails when the committed JSON and the YAML diverge.

## Deployment

Operator actions, outside this codebase:

1. Create an OAuth client in Google Cloud Console (type: Web application).
2. Authorized redirect URI: `https://circlejerks.live/api/admin/auth/google/callback`
   (Caddy strips `/api` before the app sees it).
3. Configure the consent screen and either publish it or add the intended users as
   test users.

Deploy order gains a second constraint alongside the existing Caddy-first rule recorded
in `.superpowers/sdd/progress.md`: **the environment variables must land before the
app**, or `/admin/auth/methods` reports Google as unavailable and the login screen
renders without the button.

The `@blocked_probe` regex in the Caddyfile matches `adminer` and `phpmyadmin` but not
bare `admin`, so the new auth paths are not caught by it. Verify this still holds if
that regex is ever edited.

## Risks

**The pending list is internet-reachable.** Accepted (D4), mitigated by rate limiting
and by pending users having zero capability.

**Per-request user lookup on every admin route.** Deliberate — it is what makes
suspension immediate. If the admin surface ever grows hot enough for this to matter, a
short-TTL cache is the fix, but it would trade away immediate revocation.

**`samesite=lax` is a real, if small, weakening.** Documented above with the reasoning.

**Grant enforcement is the whole feature's security.** It is one function, in an
I/O-free module, with the authorization matrix on top of it. If a future route mints a
key without calling it, the matrix should be what catches that.

## Open items carried forward

Unchanged by this work, still open from `2026-07-20-public-api-keys-design.md`:

- Six of seven aggregate endpoints splat internal shapes through; freeze them behind
  explicit field lists before the first external key.
- FastAPI's own `/docs` and `/openapi.json` remain live in production, enumerating
  internal routes. Set `docs_url=None, openapi_url=None`.
- `/runways` responses leak an internal `icao` field alongside `airport_icao`.

The served-spec-thinner-than-partner-doc question is **closed** by D7.
