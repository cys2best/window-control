# Supabase multi-user auth — design

## Problem

Today's auth is a single shared secret (`AUTH_TOKEN` env var, `src/server/auth.py`):
no accounts, no register flow, no per-user device ownership. All authenticated
requests see the same global `/instances` list.

Goal: real multi-user accounts (email/password via Supabase Auth), login/register
GUI on web PWA + mobile + desktop tray, and device lists scoped per user — a
user only sees LDPlayer instances they've linked to their account. Web/mobile
clients must be authenticated before they can fetch the device list at all.

## Non-goals

- No change to the WHEP/DataChannel media path itself, quality tiers, or the
  engine's video/input handling.
- No SSO/OAuth providers — email/password only for v1.
- No admin approval queue — signup is open (anyone with the URL can register).
- Engine (`engine/`, C++) does not learn to speak Supabase. It keeps trusting
  short-lived tokens minted by FastAPI, same trust boundary as today
  (`engine_auth.py:EngineTokenIssuer`).

## Architecture

```
                         ┌─────────────────────┐
  Web PWA / Mobile ────► │  Supabase Auth       │  (hosted, email+password,
  (login/register)       │  (JWT issuer)        │   issues access+refresh JWT)
                         └──────────┬───────────┘
                                    │ JWT (sent as Bearer on every request)
                                    ▼
                         ┌─────────────────────┐
                         │  FastAPI (src/)      │  verifies JWT (Supabase JWKS/
                         │  auth.py + app.py    │  secret), reads user_id claim
                         └──────────┬───────────┘
                     ┌──────────────┼───────────────────┐
                     ▼              ▼                   ▼
            /instances filtered  mints engine-role   mints viewer-role
            by device_links      token (existing     signaling token,
            table (Supabase      EngineTokenIssuer,   now embeds user_id
            Postgres)            unchanged trust)     claim
                                                       │
                                                       ▼
                                            infra/vps/signaling/server.js
                                            verifies JWT_SECRET as today,
                                            + checks user_id owns the
                                            instance_id in the token claims
                                            before allowing viewer role join
```

Engine (C++) is unchanged: it still only ever sees the short-lived
engine-role token FastAPI already mints today. FastAPI is the only component
that talks to Supabase directly.

### Why Supabase Auth (not just Supabase Postgres)

Supabase Auth issuing the JWTs means FastAPI never stores or checks passwords
— it verifies a JWT signature (Supabase's JWT secret / JWKS) and trusts the
`sub` (user id) and `email` claims. Register/login/password-reset/session
refresh are all handled by Supabase's REST API or client SDKs. This keeps
custom auth code out of Python and out of the two GUIs.

### Device ownership model

New Supabase Postgres table, `device_links`:

| column | type | notes |
|---|---|---|
| `user_id` | uuid | FK to `auth.users.id` (Supabase-managed) |
| `instance_id` | text | matches `instance_manager`'s existing instance id (LDPlayer serial/label) |
| `linked_at` | timestamptz | default now() |

Primary key `(user_id, instance_id)`. `/instances` (FastAPI) becomes: list
instances known to `InstanceManager` (unchanged local discovery), filtered to
only those with a `device_links` row for the requesting `user_id`. A new
`POST /instances/{id}/link` and `DELETE /instances/{id}/link` let a logged-in
user claim/release a discovered instance — first user to link an unclaimed
instance owns it; already-linked instances aren't linkable by others.

This is the only new database table. Nothing else about instance discovery,
scrcpy launch, or the engine's per-instance runtime changes.

### FastAPI changes (`src/server/`)

- `auth.py`: replace `check_token`/`make_session_cookie`/`verify_session_cookie`
  (shared-secret HMAC cookie) with `verify_supabase_jwt(token) -> UserClaims |
  None` — validates signature + expiry against Supabase's JWT secret
  (`SUPABASE_JWT_SECRET` env var, same shape as today's `AUTH_TOKEN`).
  `auth_enabled()` becomes `bool(config.SUPABASE_URL)` — unset Supabase config
  = auth disabled, same LAN-only escape hatch as today.
- `app.py`: `_auth_gate` middleware now checks `Authorization: Bearer <jwt>`
  (or a cookie holding it, for browser convenience) via `verify_supabase_jwt`
  instead of the session cookie. `POST /login` is removed — clients talk to
  Supabase directly for login/register; FastAPI never sees a password.
  `/instances` filters through `device_links`; add `/instances/{id}/link` and
  `/instances/{id}/link` (DELETE).
- `engine_auth.py`: `EngineTokenIssuer.signaling(..., role="viewer")` gains a
  `user_id` parameter, embedded as a claim so the VPS relay can check
  ownership (see below). `role="engine"` tokens are unaffected — the engine
  process itself has no user identity.
- New `src/server/supabase_client.py`: thin wrapper around Supabase's REST
  API for the one server-side need — verifying JWTs and, for `/instances/
  {id}/link`, confirming the instance isn't already claimed (a plain SELECT
  against `device_links`).

### VPS signaling changes (`infra/vps/signaling/server.js`)

Already verifies `JWT_SECRET`-signed tokens per connection (`role`, `session`
claims). Add: when `role === 'viewer'`, also require and check a `user_id`
claim against... itself — the relay doesn't have its own copy of
`device_links`. Simplest correct approach: FastAPI is the sole minter of
these tokens and already checked ownership before minting one, so the
signaling server's job is unchanged (verify signature + role + session match)
— it does NOT need a second ownership check or a Supabase/DB connection of
its own. (This narrows "propagate to VPS" to: the JWT the relay already
verifies now happens to carry a `user_id` claim, present for audit/logging,
not for a new authorization decision — avoids giving the Node relay its own
Supabase credentials.)

### GUI changes

**Web PWA (`src/client/`)**: `auth_gate.js` rewritten from a single token
prompt to a login/register form pair (email + password, "create account"
toggle) that calls Supabase's REST auth endpoints directly
(`SUPABASE_URL`/`SUPABASE_ANON_KEY` injected into `index.html` like `VERSION`
is today) and stores the returned JWT (localStorage + sent as Bearer on
fetches). `app.js`'s existing fetch calls need the Bearer header added.

**Mobile (`mobile/`)**: new login/register screen(s) in `mobile/src/`, using
`@supabase/supabase-js` (or plain REST) the same way. Device list screen
becomes unreachable until a session exists — gate at the navigation level.

**Desktop tray (`src/gui/`)**: add a login screen shown on tray-app start,
for the PC owner to authenticate with their own Supabase account. Session
token cached locally (Windows credential store, or a local file under the
app's data dir) so the tray doesn't prompt every launch. This is what lets
the PC-local `InstanceManager` know which `user_id` "owns" this PC's
discovered instances for the first-link claim — i.e., the tray's logged-in
user is who instances get auto-attributed to if they choose to link from the
tray directly (optional convenience — linking from web/mobile also works via
`/instances/{id}/link`).

### Config additions (`src/config.py`)

- `SUPABASE_URL`, `SUPABASE_ANON_KEY` (public, safe to ship to clients),
  `SUPABASE_JWT_SECRET` (server-only, used to verify tokens).
- `AUTH_TOKEN` and the old cookie-session code path are removed, not kept
  alongside — this replaces shared-secret auth, it doesn't add to it.

### Error handling

- Expired/invalid JWT → 401 from FastAPI's `_auth_gate`, same shape as today.
- Supabase unreachable at verify time (network blip) → FastAPI fails closed
  (401), does not fall back to trusting an unverifiable token.
- Linking an already-claimed instance → 409 Conflict.
- `PUBLIC_UI_URL` startup check (`app.py:187`) changes from requiring
  `AUTH_TOKEN` to requiring `SUPABASE_URL` — public exposure still requires
  auth to be configured.

## Testing

- `src/`: `uv run pytest tests/ -v` — new tests for `verify_supabase_jwt`
  (valid/expired/bad-signature/missing), `/instances` filtering by
  `device_links`, link/unlink 409 on double-claim, `_auth_gate` 401 paths.
  Supabase itself is mocked/stubbed in tests (no live network dependency for
  the test suite, consistent with the existing `src/stubs/` pattern).
- `infra/vps/signaling`: existing `server.test.js` gets a case asserting a
  viewer token's `user_id` claim round-trips into the connection without
  becoming a new authorization branch (i.e., relay behavior for valid
  role/session tokens is unchanged).
- `mobile/`: `npm test` — new tests for the login/register screen and the
  navigation gate that blocks the device list pre-auth.
- Manual/E2E: register a new account via web, confirm empty device list,
  link an instance, confirm it appears; log in as a second account, confirm
  it does NOT see the first account's linked instance; repeat login (not
  register) on mobile with the same account, confirm same list.

## Open items deferred (not blocking this spec)

- Password reset flow (Supabase supports it out of the box; wiring a
  "forgot password" link into the GUIs can be a fast follow, not required
  for login/register to work).
- Unlinking/re-assigning a device to a different account.
- Desktop tray's local-owner-attribution convenience (auto-link on tray
  discovery) — the manual `/instances/{id}/link` call from any authenticated
  client is sufficient for v1; the tray auto-attribution is a nice-to-have,
  not required for the login gate to work.
