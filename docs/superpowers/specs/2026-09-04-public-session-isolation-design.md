# Public session isolation + auth simplification — design

## Problem

Two related gaps, discovered together while scoping an "auto-discover and
auto-link instances" request:

**1. Public signaling sessions collide across different PC installs.**
`instance_name()` (`src/server/instance_manager.py:30`) derives session ids
purely from local LDPlayer index — `instance0`, `instance1`, etc. That name
is used verbatim as the session id on the shared VPS relay
(`infra/vps/signaling/server.js`), by both the C++ engine
(`engine/src/main.cpp:106`) and the browser client
(`src/client/engine_session.js:255`). Every install that runs this app talks
to the same relay (`signaling.koeeru.com` in current use). Two different PCs
both publishing their own "instance0" at the same time collide on the exact
same session slot: the relay's `session[role]` check
(`infra/vps/signaling/server.js:52`) closes the second comer, and in a bad
timing window a viewer meant for PC B's instance0 can be bridged to PC A's
engine instead. `src/config.py:50-52` already flagged this in a comment when
`VPS_SIGNALING_URL` was first added ("session ids on the VPS signaling relay
are sequential/enumerable and there is no auth on that path yet") — the
follow-up auth work referenced there (`2026-09-03-supabase-multi-user-auth`)
added Supabase JWT auth to FastAPI's `/select` endpoint, but never touched
the relay's session-id scheme itself, so this exact gap is still open. The
original Supabase-auth design (`2026-09-03-supabase-auth-design.md`) also
called for the relay to check `user_id` ownership before allowing a viewer to
join a session (see its architecture diagram) — that check was never
implemented in `server.js`, which only verifies `session`/`role`/`exp` match
today.

**2. `device_links` per-instance ownership doesn't fix (1), and there's no
UI to use it.** `device_links` (Supabase Postgres table + `/instances/{id}/link`)
gates who is allowed to call `/select` on *one PC's own* FastAPI app. It does
nothing about session-id collisions on the *shared relay*, which is the layer
where a request actually could reach the wrong PC. Separately, no client
(web, mobile, tray) was ever wired to call `POST /instances/{id}/link`
(`HANDOFF.md`, 2026-09-04 19:30 entry) — the only way to link an instance
today is a DevTools console workaround.

## Decision

Confirmed with the project owner: this app is one-owner-per-install (a
single Supabase account owns everything a given PC's app discovers). Given
that, per-instance ownership tracking is solving a problem that doesn't
apply here — fix the actual cross-PC leak instead, and remove per-instance
linking entirely rather than build UI for it.

## Non-goals

- No change to WHEP/local-path auth (`WhepCapabilityConfig`,
  `ENGINE_WHEP_CAPABILITY_SECRET`) — that path is same-machine/LAN-scoped,
  not exposed on the shared relay, not affected by the collision.
- No multi-owner-per-PC support (explicitly ruled out this round).
- No relay-side (`server.js`) ownership/JWT-claim enforcement — made
  unnecessary by (1)'s fix (a colliding session id was the actual way
  ownership could be bypassed; once collision is impossible, reaching a
  session at all already required a token minted by that specific PC's own
  authenticated FastAPI app).
- No change to Supabase Auth itself (login/register/JWT verification stays
  as shipped in `2026-09-03-supabase-multi-user-auth`).

## Design

### 1. Install-scoped public session ids

Give every FastAPI process a random, ephemeral install id at startup —
`secrets.token_hex(8)` in `src/main.py:build_engine_orchestrator()`, the same
pattern already used one line above for `whep_secret=secrets.token_hex(32)`.
No persistence needed: the id only has to be unique among *concurrently
running* installs on the shared relay, not stable across restarts (a fresh
one each start is fine, same as `whep_secret` already is).

Thread it through as a new `install_id` field on `EngineRuntimeConfig`
(`src/server/engine_runtime.py:63`). Both places that currently mint a
signaling token/session use the bare `self.instance_name` as the session —
change both to `f"{self.config.install_id}.{self.instance_name}"`:

- `EngineRuntime.select()` (`engine_runtime.py:176-178`, viewer token)
- `EngineRuntime._build_env_locked()` (`engine_runtime.py:349-351`, engine
  token + the engine process's own copy of the session id)

`EngineTokenIssuer.signaling()` (`src/server/engine_auth.py:63`) already
takes this as its first positional arg and puts it straight into the JWT's
`session` claim — no signature change needed, just what callers pass in.

The engine process needs the *same* session string independently (it builds
its own connect URL, `engine/src/signaling_client.cpp:242`,
`?session=<id>&role=engine`, which must byte-match the token's `session`
claim for the relay to accept it — `server.js:40`). Pass the install id down
as a new env var, `ENGINE_INSTALL_ID`, alongside the existing
`ENGINE_SIGNALING_URL`/`ENGINE_SIGNALING_TOKEN` (`engine_runtime.py:344-353`).
In `engine/src/main.cpp:105-106`, build
`sessionId = installId.empty() ? instanceName : installId + "." + instanceName`
before constructing `SignalingClient(signalingUrl, sessionId, "engine", signalingToken)`
— empty-install-id falls back to today's behavior, so a manually-launched
engine (tests, `--gtest_filter` local runs without Python spawning it) still
works.

On the browser side, `POST /instances/{id}/select`'s response
(`src/server/app.py`, the `select_instance` handler) needs to return the
install-scoped session string, since `engine_session.js:255` currently sends
`selection.name` (bare instance name) as the relay session. Add a new field
to the selection response — `public_session` — set to the same
`f"{install_id}.{instance_name}"` string the server used to mint the
viewer's own token. `engine_session.js:255` changes from
`selection.name` to `selection.public_session`. (`selection.name` has no
other use in the client — grepped, only that one call site.)

### 2. Remove per-instance linking

Delete the `device_links` ownership layer; being a valid authenticated
request (Supabase JWT, already enforced by `_auth_gate` middleware,
`app.py:223`) becomes sufficient to see and use every instance this PC
discovers.

- `app.py`: `GET /instances` and `GET /windows` drop their `device_links`
  filtering (`app.py:302-310`, `397-405`) — auth-enabled mode returns the
  same full list `instance_manager.list_instances()` that unauthenticated
  mode already returns.
- `app.py`: delete `POST /instances/{id}/link`, `DELETE /instances/{id}/link`
  (`app.py:312-326`).
- `app.py`: `_authorize_instance_access` (`app.py:210-221`) drops its
  `device_links` lookup — becomes a no-op now that the middleware's "valid
  JWT or 401" check is the only gate. Evaluate at implementation time
  whether the helper is worth keeping as a named no-op (documents *why*
  every instance route is safe under auth) or inlining/removing it.
- `src/server/supabase_client.py`: delete — its only job was `device_links`
  reads/writes.
- `app.py`: remove the `SupabaseClient(...)` construction and
  `_supabase_call` wrapper if nothing else in the file uses them after the
  above (check at implementation time — `_supabase_call` was written to be
  reusable but grep shows `device_links` calls as the only callers).
- `infra/supabase/device_links.sql`: delete the file. The live Supabase
  project still has the table — note in the PR/HANDOFF that the owner should
  drop it manually via the Supabase SQL editor (`drop table device_links;`)
  since nothing in this repo runs migrations against a live project.
- `src/gui/supabase_login.py:4`: docstring references
  `/instances/{id}/link` as a not-yet-wired TODO — update the comment, no
  behavior there today.

## Data flow (after)

```
Web PWA / Mobile / Tray ──login──► Supabase Auth ──JWT──► FastAPI
FastAPI (_auth_gate): valid JWT required for every route, no per-instance
ownership check.
GET /instances → instance_manager.list_instances() (all of this PC's
discovered instances, same set for every authenticated caller).
POST /instances/{id}/select → mints:
  - viewer signaling token, session = f"{install_id}.{instance_name}"
  - engine already has the matching session (same formula, from its own
    env vars at spawn time)
Relay (server.js): unchanged — still just checks session/role/exp match.
Now safe because a colliding session name across two PCs is astronomically
unlikely (random install_id prefix) instead of guaranteed for any two
installs sharing a local LDPlayer index.
```

## Error handling

- `ENGINE_INSTALL_ID` unset when the engine is launched by something other
  than Python (manual `engine.exe` invocation, C++ test harness): falls back
  to bare `instanceName` as the session, matching current behavior exactly.
  Only matters if that manual launch also talks to the real shared relay,
  which existing engine tests don't (`SignalingClient.*` is excluded from
  the offline suite per `CLAUDE.md`/`PROJECT_CONTEXT.md`).
- No change to what happens when `VPS_SIGNALING_URL`/`signaling_secret` are
  unset — public path stays fully disabled as today, install_id is unused.

## Testing

- Python: `tests/test_engine_runtime.py` (or equivalent) — assert
  `select()`'s viewer token and `_build_env_locked()`'s engine token both
  carry `session == f"{install_id}.{instance_name}"` given a config with a
  known `install_id`. `tests/test_app_auth.py` — `GET /instances` returns
  the full discovered list for any authenticated user, no `device_links`
  setup needed; a 403 test that relied on non-ownership should be deleted or
  rewritten to confirm *unauthenticated* still 401s (the actual remaining
  gate). Delete/rewrite the `link`/`unlink` endpoint tests entirely (routes
  no longer exist).
- `tests/test_supabase_client.py`: delete (module is deleted).
- C++: extend `engine/test/test_signaling_client.cpp` (or add) to cover
  `main.cpp`'s session-id construction — since that logic lives in `main()`
  rather than a testable unit today, this may mean lifting the
  `installId.empty() ? ... : ...` line into a small free function so it's
  unit-testable without a live relay. Confirm at implementation time.
- Node: `infra/vps/signaling/server.test.js` is unaffected (relay protocol
  itself doesn't change) — no new coverage needed there.
- Manual/Windows gate: same shape as every other `engine/` change per
  `CLAUDE.md` — needs a real Windows build; can't be verified from this
  session. Specifically worth confirming on the Windows Host PC: two engine
  processes with different install ids but the same local instance name
  (e.g. run twice with `ENGINE_INSTALL_ID` forced to two different values)
  both connect to the real relay without colliding.

## Open risk (acknowledged, not fixed here)

`install_id` is regenerated every FastAPI restart. Between an old process's
engine still holding a signaling connection under its old session id and a
new process starting with a new one, there's no session continuity — this
matches how `whep_secret` already behaves today (also regenerated per
restart, same blast radius: a restart already tears down and respawns every
engine instance), so no new failure mode, just noting the parallel.
