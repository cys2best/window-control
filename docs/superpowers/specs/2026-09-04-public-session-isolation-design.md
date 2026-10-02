# Public session isolation via account-verified relay — design

## Problem

Two related gaps, discovered together while scoping an "auto-discover and
auto-link instances" request:

**1. Public signaling sessions collide across different PC installs, and the
relay trusts a secret shared by every install.**
`instance_name()` (`src/server/instance_manager.py:30`) derives session ids
purely from local LDPlayer index — `instance0`, `instance1`, etc. That name
is used verbatim as the session id on the shared VPS relay
(`infra/vps/signaling/server.js`), by both the C++ engine
(`engine/src/main.cpp:106`) and the browser client
(`src/client/engine_session.js:255`). Every install that runs this app talks
to the same relay (`signaling.koeeru.com` in current use), so two PCs both
publishing "instance0" collide on the same session slot. Worse: the relay's
token check (`server.js:37-49`) verifies an HS256 signature against ONE
`JWT_SECRET` env var, set once for the whole relay — every install's
`ENGINE_SIGNALING_SECRET` must equal that same value to work at all. That
secret is copyable: whoever has it can forge a token claiming *any* session
name, on *any* install, from *any* machine. Session-name uniqueness alone
(e.g. a random per-install string) narrows accidental collisions but not
this — copy the string to another PC and that PC's engine can still register
under the original session slot. `src/config.py:50-52` flagged the
session-id half of this when `VPS_SIGNALING_URL` was first added; the
follow-up auth work (`2026-09-03-supabase-multi-user-auth`) added Supabase
JWT auth to FastAPI's `/select` but never touched the relay itself, so both
halves are still open. The original Supabase-auth design
(`2026-09-03-supabase-auth-design.md`) called for the relay to check
`user_id` ownership before pairing a viewer — never implemented.

**2. `device_links` per-instance ownership doesn't fix (1), and there's no
UI to use it.** `device_links` gates who is allowed to call `/select` on
*one PC's own* FastAPI app. It does nothing about the relay itself, which is
where a request could actually reach the wrong PC or be forged outright.
Separately, no client was ever wired to call `POST /instances/{id}/link`
(`HANDOFF.md`, 2026-09-04 19:30 entry) — the only way to link an instance
today is a DevTools console workaround.

## Decision

Confirmed with the project owner: this app is one-owner-per-install (a
single Supabase account owns everything a given PC discovers), and the relay
should route by *logged-in account*, not by a copyable secret string. That
rules out any scheme where possessing a string is sufficient to act as
someone else's PC. Concretely:

- **Viewer → relay**: verified with the viewer's own live Supabase access
  token (the same one already used for every FastAPI call), checked by the
  relay itself against Supabase's JWKS. Nothing new to leak — it's the
  viewer's own login, already short-lived.
- **Engine → relay**: each install generates its own Ed25519 keypair once,
  locally. The private key never leaves that machine or gets transmitted
  anywhere, including to Supabase. Only the public key is registered (once,
  on login) against the owning account. The relay verifies the engine's
  registration signature against that stored public key. Stealing the
  *public* key or the signed token gives an attacker nothing — forging a
  new registration requires the private key file itself, i.e. filesystem
  access to that specific PC, a materially higher bar than "knew a string
  that happened to leak."
- `device_links` per-instance linking is removed (unrelated mechanism to the
  above — since this app is one-owner-per-install, per-instance ACLs inside
  one PC's own instance list are unnecessary; see §7).

## Non-goals

- No change to WHEP/local-path auth (`WhepCapabilityConfig`,
  `ENGINE_WHEP_CAPABILITY_SECRET`) — that path is same-machine/LAN-scoped,
  never touches the shared relay.
- No multi-owner-per-PC support (explicitly ruled out).
- No key rotation UI or revocation list — a fresh login from a different
  account on the same PC simply overwrites that install's registered owner
  (see "Case 2" below); there is no way today to revoke a still-logged-in
  session early.
- No hardware-backed key storage (TPM, etc.) — a plain file on disk,
  consistent with how `whep_secret`/every other local credential in this
  repo is handled today.
- No change to Supabase Auth itself (login/register/JWT verification stays
  as shipped in `2026-09-03-supabase-multi-user-auth`).
- Public signaling now *requires* auth to be configured (`SUPABASE_URL` set)
  — there is no more secret-only fallback for exposing the public path
  without Supabase. If `VPS_SIGNALING_URL` is set but `SUPABASE_URL` isn't,
  treat it as misconfigured and keep the public path disabled (log a
  warning); LAN-only/Tailscale access is unaffected either way.

## Design

### 1. Per-install Ed25519 keypair

New small module, e.g. `src/server/install_identity.py`:

- `get_or_create_install_keypair() -> (PrivateKey, public_key_bytes)` —
  generates an Ed25519 keypair with `cryptography.hazmat.primitives.asymmetric.ed25519`
  (already a transitive dependency via `pyjwt[crypto]`, `pyproject.toml:18`
  — no new Python dependency) the first time it's called, persists the
  private key to disk, and reads it back on every later call. Mirrors the
  repo's existing writable-path-fallback pattern (see `_log()` in
  `src/server/stun_server.py:34-42` — try
  `[r"C:\ProgramData\WindowControl", r"C:\Windows\Temp", r"C:\Temp", "/tmp"]`
  in order). If every path is unwritable, fall back to an in-memory keypair
  for that process only (degraded: public path won't work across a restart
  until a writable path exists, fails closed rather than crashing).
- `get_cached_owner_user_id()` / `set_cached_owner_user_id(user_id)` — a
  second small file (same directory) caching the most recently seen
  authenticated user, used to bootstrap the engine's session label at
  startup (see §4).

### 2. Install ownership registry (Supabase)

New table, `infra/supabase/installs.sql`:

```sql
create table if not exists installs (
    public_key text primary key,   -- base64/hex-encoded Ed25519 public key
    user_id uuid not null references auth.users(id) on delete cascade,
    updated_at timestamptz not null default now()
);
alter table installs enable row level security;
create policy "users manage their own installs"
    on installs for all
    using (auth.uid() = user_id) with check (auth.uid() = user_id);
```

Keyed by `public_key`, not `user_id` — an account can own multiple installs
(rows), and each install's row is independently upserted. FastAPI (service
role, same trust pattern the removed `device_links` used) upserts this row
on `public_key` conflict whenever the currently-authenticated request's
`user_id` differs from the cached owner (`get_cached_owner_user_id()`):
write the new `user_id`, update the local cache. This is what makes account
switching work (see "Case 2" below) — the row's `user_id` always reflects
whoever most recently logged into *that specific PC*, regardless of what
happens on any other install.

**Multiple PCs, one account** (confirmed in review): each PC has its own
keypair, so logging into a second PC just inserts a second row
(`public_key_B, user_id`) — no conflict with the first PC's row. Both stay
valid independently. **Same PC, different account** (also confirmed): the
keypair doesn't change: the row's `user_id` is overwritten to the new
account, and the *previous* account's viewer sessions stop resolving against
this install immediately (no stale trust carried over).

### 3. Relay verifies the viewer's real Supabase JWT

`infra/vps/signaling/server.js` gains a new dependency, `jose`
(`createRemoteJWKSet` + `jwtVerify`), mirroring `src/server/auth.py`'s
`PyJWKClient` approach: fetch `{SUPABASE_URL}/auth/v1/.well-known/jwks.json`,
verify `algorithms: ['ES256']`, `audience: 'authenticated'`
(`auth.py:52,69-70` for the exact settings to match). On a `role=viewer`
connection, the `token` query param is now the viewer's own Supabase access
token (not a FastAPI-minted one) — the relay verifies it, reads `sub` as
`user_id`.

### 4. Relay verifies the engine's registration signature

On a `role=engine` connection, `token` is a FastAPI-minted payload signed
with the install's Ed25519 private key (replaces the HMAC scheme in
`EngineTokenIssuer.signaling()`, `src/server/engine_auth.py:63-103` — same
payload shape `{session, role, exp, jti}`, same TTL constants, different
signature algorithm; `whep()`'s HMAC scheme is untouched, that's the
separate local-only path). The relay parses `session` as
`"{user_id}.{instance_name}"`, fetches `installs` for that `user_id` via
Supabase REST (new relay-side HTTP call, needs `SUPABASE_URL` +
a read-scoped key in the relay's own env — extend
`infra/vps/signaling/README.md`'s Configure section), and verifies the
signature against the returned `public_key` using Node's built-in
`crypto.verify(null, data, { key: publicKeyObject, format: 'raw' }, signature)`
(Ed25519 is natively supported, no extra dependency for this half). No
`installs` row for that `user_id` → reject the connection.

### 5. Session naming

Both roles now agree on `session = f"{user_id}.{instance_name}"` — replaces
the earlier install-id-based scheme entirely. `EngineRuntime.select()`
(`engine_runtime.py:157-188`) drops its own viewer-token minting altogether
(the browser no longer needs a FastAPI-issued signaling token at all — it
already holds its own Supabase access token for calling FastAPI itself, and
now reuses that directly against the relay). `EngineSelection.signaling_token`
is removed; `EngineSelection.signaling_url` and the new field
`public_session` (`f"{owner_user_id}.{instance_name}"`, using the cached
owner — see §6) remain. `src/client/engine_session.js:254-257` changes to
send the client's own Supabase access token as `token` and
`selection.public_session` as `session`. `mobile/src/api/client.ts:20-21`
has the matching TypeScript selection type (`signaling_url`/
`signaling_token` fields) — no relay-connect logic exists in `mobile/` yet
(grepped, none found), so this is just the type definition: drop
`signaling_token`, add `public_session: string | null`, ready for whenever
mobile's own public-path connect code is built.

`EngineRuntime._build_env_locked()` (`engine_runtime.py:344-353`) still
mints the *engine's* token (now Ed25519-signed) and passes the session
string down via env var, same shape as today's `ENGINE_SIGNALING_TOKEN`;
add `ENGINE_SESSION` (the full `"{user_id}.{instance_name}"` string) so
`engine/src/main.cpp:105-106` no longer needs to construct it itself — it
just forwards `GetEnvOrEmpty("ENGINE_SESSION")` as `SignalingClient`'s
`sessionId` argument, empty-string fallback to bare `instanceName` preserved
for manually-launched/test engine processes exactly as before. No new
crypto code needed in the C++ engine at all — main.cpp keeps just forwarding
strings, same as today.

### 6. Engine startup timing (bootstrap)

The engine registers on the relay proactively at process startup
(`main.cpp:98-109`), before any HTTP request has necessarily happened this
boot, so `user_id` isn't always freshly known. `_build_env_locked()` uses
`get_cached_owner_user_id()` (§1) — the value from the *last* time any
account authenticated against this install, persisted across restarts. If
that's stale (a different account logged in since the last restart, but
this engine hasn't been respawned yet), the engine registers under the
*old* session label until its next respawn/restart — the new owner's viewer
simply won't find it (fails closed: no access, not wrong access) until a
respawn picks up the freshly-cached value. Same shape as the rest of this
design: staleness degrades to "doesn't connect," never to "connects as the
wrong party."

### 7. Remove per-instance linking

Unchanged from the prior version of this design:

- `app.py`: `GET /instances` and `GET /windows` drop `device_links`
  filtering (`app.py:302-310`, `397-405`) — any authenticated caller gets
  the full discovered list.
- `app.py`: delete `POST /instances/{id}/link`, `DELETE /instances/{id}/link`
  (`app.py:312-326`); `_authorize_instance_access` (`app.py:210-221`) drops
  its lookup (becomes a no-op / candidate for removal, decide at
  implementation time).
- `src/server/supabase_client.py`: delete (its only job was `device_links`);
  remove now-unused `SupabaseClient(...)`/`_supabase_call` wiring in
  `app.py` if nothing else uses them (check at implementation time — the
  new `installs` upsert from §2 may end up as a new, small use of the same
  service-role REST pattern, possibly living in this file instead of being
  deleted outright).
- `infra/supabase/device_links.sql`: delete the file; note in the PR/HANDOFF
  that the owner should `drop table device_links;` manually via the
  Supabase SQL editor.
- `src/gui/supabase_login.py:4`: update the stale docstring reference to
  `/instances/{id}/link`.

## Data flow (after)

```
Web PWA / Mobile / Tray ──login──► Supabase Auth ──JWT──► FastAPI
FastAPI (_auth_gate): valid JWT required for every route.
  On each authenticated request: if request.user_id != cached owner,
  upsert installs(public_key, user_id) and refresh the cache.
GET /instances → instance_manager.list_instances() (all of this PC's
discovered instances, same set for every authenticated caller).
POST /instances/{id}/select → returns public_session = f"{owner}.{name}";
  browser reuses its own Supabase access token as the relay's viewer token.
Engine (spawned earlier, using the cached owner at that time) already
registered as role=engine under session = f"{cached_owner}.{instance_name}",
signed with this install's Ed25519 private key.

Relay:
  role=viewer → verify token against Supabase JWKS, require sub == the
    user_id parsed out of the requested session.
  role=engine → parse user_id out of session, fetch installs.public_key
    for that user_id from Supabase, verify signature.
  Only on both checks passing does normal session/role pairing proceed
  (server.js's existing engine<->viewer bridge, unchanged).
```

## Error handling

- Relay unreachable from Supabase (network blip during an engine's
  registration or a viewer's JWKS fetch): reject the connection, log it —
  same fail-closed posture as `_supabase_call`'s existing 401-on-unreachable
  pattern in `app.py`.
- `installs` row missing for a session's `user_id` (never logged in on this
  relay before, or DB row lost): engine registration rejected. Public path
  simply doesn't come up until the next authenticated FastAPI request
  re-upserts it and the engine is respawned.
- Cached owner file unreadable/corrupt: treat as no cached owner (engine
  skips minting a public-path token for that boot, matching today's
  `signaling_url` empty → public path disabled behavior); refreshed on the
  next authenticated request + respawn.
- `VPS_SIGNALING_URL` set without `SUPABASE_URL`: log a misconfiguration
  warning at startup, keep the public path disabled (see Non-goals).

## Testing

- Python: new test module for `install_identity.py` — keypair
  generate-once-then-reuse (same fixture directory returns the same public
  key across two calls), owner-cache read/write/missing-file behavior,
  unwritable-directory fallback. `engine_auth.py` — `EngineTokenIssuer`'s
  engine-role signing verifies against the corresponding Ed25519 public key
  (still rejects tampered payloads, mismatched session/role/expired `exp`,
  same test shapes as today just swapping the signature algorithm).
  `app.py` — the owner-cache-diff upsert fires exactly once per distinct
  `user_id` seen, not on every request; `GET /instances` returns the full
  list for any authenticated user; delete/rewrite the `link`/`unlink`
  endpoint tests (routes no longer exist).
- `tests/test_supabase_client.py`: rewrite for whatever remains in that
  module after §7 (or delete, per implementation-time decision above).
- Node: extend `infra/vps/signaling/server.test.js` — a viewer with a
  validly-signed-but-wrong-audience/expired Supabase-shaped JWT is
  rejected; a viewer whose `sub` doesn't match the session's `user_id`
  portion is rejected; an engine whose signature doesn't verify against the
  looked-up `installs.public_key` is rejected; the existing
  engine<->viewer bridging behavior still passes for a correctly-signed
  pair of connections (mock the Supabase REST calls in tests, same way
  `test_supabase_client.py` mocks `httpx` today).
- C++: no new coverage needed — `main.cpp`'s session handling stays a
  string passthrough (`GetEnvOrEmpty("ENGINE_SESSION")`), same shape as the
  existing `ENGINE_SIGNALING_TOKEN` passthrough it already has.
- Manual/Windows gate: same as every other `engine/`-touching change per
  `CLAUDE.md` — needs a real Windows build, real Supabase project, and the
  real relay deployed with the new `installs` table + relay env vars before
  it can be verified end-to-end; can't be done from this session.

## Open risks (acknowledged, not fully closed here)

- **Engine session staleness** (§6): an account switch on one PC doesn't
  take effect for that PC's already-running engine processes until their
  next respawn/restart. Fails closed (no access) in the interim, not a
  security hole, just a usability lag worth calling out if it surprises
  someone in testing.
- **Relay now depends on Supabase being reachable** for every engine
  registration and every viewer JOIN (JWKS fetch, `installs` lookup) —
  previously it only needed a local static secret. A Supabase outage now
  takes down the public path entirely (LAN/Tailscale path is unaffected).
  Acceptable given the alternative is the copyable-secret model this
  design exists to remove, but worth monitoring once live.
