# Local-Only Access With Device Pairing — Design

Date: 2026-10-01

## Goal

EmuCtrl is reachable only from the local network or Tailscale. All public
access is removed: the VPS relay, TURN, the HTTP tunnel, public signaling, and
Supabase login. In place of login, a device must be paired once with a code
shown on the PC before it can use the app.

Success means:

- A request from a non-private address is refused before any route runs.
- A device on the LAN or tailnet that has not paired can load the pairing
  screen and nothing else.
- A paired device works with no further prompts, over LAN or Tailscale.
- No Supabase, TURN, tunnel, or signaling code, config, or infrastructure
  remains in the repo or running in the cloud.

## Decisions already made

| Question | Decision |
|---|---|
| Network enforcement | Source-IP allowlist middleware; uvicorn keeps binding `0.0.0.0` |
| Accounts / "my instances" identity | Removed entirely |
| Mobile off-LAN | Works over Tailscale only |
| Pairing code visibility | Shown only after clicking "Pair device", 5-minute expiry |
| VPS | Destroyed with `terraform destroy`, confirmed by the user at that step |

## Out of scope

- TLS on the LAN. LAN traffic stays plain HTTP, so someone actively
  intercepting traffic on the same network can read a pairing code or device
  token. Tailscale traffic is encrypted. This is an accepted limitation.
- Distinguishing a Tailscale peer from a carrier-grade-NAT peer. Both use
  `100.64.0.0/10`. A PC connected straight to a CGNAT modem with no router
  would admit ISP neighbours to the pairing screen (not past it).
- Per-device permissions. A paired device has full access.

## Architecture

Two checks run on every request, in this order, in one ASGI middleware that
replaces `_auth_gate` in `src/server/app.py`.

### 1. Network gate — `src/server/network_gate.py`

`is_allowed_peer(host: str) -> bool` returns true only for:

- IPv4: `127.0.0.0/8`, `10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`,
  `169.254.0.0/16`, `100.64.0.0/10`
- IPv6: `::1`, `fc00::/7`, `fe80::/10`

IPv4-mapped IPv6 addresses (`::ffff:a.b.c.d`) are unwrapped before the check.
An unparseable or missing peer address is refused.

The peer is the direct socket peer (`scope["client"]`). uvicorn's
`proxy_headers` stays disabled, as it is today, so `X-Forwarded-For` cannot
influence the result. There are no exempt paths.

The middleware is pure ASGI rather than `@app.middleware("http")` so that it
also covers WebSocket scopes. The app has no WebSocket routes today; this
keeps a future one from bypassing the gate. Refusal is HTTP 403, or a
WebSocket close before accept.

### 2. Pairing gate — `src/server/pairing.py`

A request passes if any of these hold:

- the peer is loopback (the desktop webview on the PC itself),
- the path is exempt: `POST /pair`, `GET /pair/status`, and the static web
  assets needed to render the pairing screen (the existing
  `_is_public_web_asset` rule, minus `/account`),
- it carries a valid device token.

Otherwise the response is 401. As today, only paths that resolve to a
registered route are gated, so unknown paths still return 404.

Token transport is unchanged from the current client: `Authorization: Bearer
<token>`, plus the existing `?token=` query parameter for preview image URLs
that cannot set headers.

#### Pairing code

- Created only when the user clicks "Pair device" in the launcher.
- 6 digits from `secrets`, valid for 5 minutes, held in memory only.
- Single use: cleared on the first successful pairing.
- Cleared after 5 wrong attempts; the user must click "Pair device" again.
- Compared with `hmac.compare_digest`.
- When no code is active, `POST /pair` fails the same way as a wrong code,
  so a caller cannot tell whether pairing is open.

#### Device tokens

- `POST /pair {code, device_name}` returns a token from
  `secrets.token_urlsafe(32)`.
- The PC stores only the SHA-256 hash, with the device name, a short id, and
  the creation time, in `paired_devices.json` in the app data directory.
  Writes are atomic, using the temp-file-and-replace approach currently in
  `install_identity.py`.
- Tokens do not expire. They end when the device is removed.
- `GET /pair/status` returns whether the caller's token is valid, so a client
  can decide between the pairing screen and the app.

#### Launcher

`src/gui/launcher.py` loses the login prompt and account band and gains:

- a "Pair device" button that shows the code and a countdown,
- a list of paired devices with per-device remove and "Unpair all".

The launcher runs in the same process as the server and calls the pairing
module directly; there is no HTTP endpoint for creating a code or removing a
device.

### Threat walkthrough

| Case | Result |
|---|---|
| Public internet reaches port 8080 (port-forward, public IP) | 403 at the network gate |
| Stranger on the same Wi-Fi | Pairing screen only; no active code unless the owner just clicked "Pair device" |
| Code seen over the owner's shoulder | Useless once the owner pairs; it is single use |
| Brute force of the code | 5 attempts, then the code is gone; no code is active by default |
| Phone lost or token leaked | Owner removes that device; other devices unaffected |
| Two PCs | Independent device lists; the client stores a token per host |
| PC reinstalled | Device list is gone; every device pairs again |
| Repeated `POST /pair` | Cannot remove or replace devices; can only add one with a valid code |
| Sniffer on the same LAN | Not covered (see Out of scope) |

The engine's WHEP listener (`engine/src/main.cpp`) stays bound to `0.0.0.0`.
It requires the short-lived HMAC WHEP token, which only the gated app issues.

## Removal

### Python

Deleted, with their tests: `src/server/auth.py`, `supabase_client.py`,
`install_identity.py`, `http_tunnel.py`, `ice_config.py`,
`src/gui/supabase_login.py`. `bearer_token()` moves into `pairing.py`.

Trimmed:

- `engine_auth.py`: keep `whep()`, drop `engine_token()` and the Ed25519 key.
- `engine_runtime.py`, `engine_orchestrator.py`: drop `signaling_url`,
  `signaling_private_key`, `public_ice_servers`, `public_session`.
- `main.py`: drop `maybe_show_login`, signaling wiring, and the tunnel
  comments around the uvicorn config.
- `app.py`: drop the tunnel task, the Supabase client, the first-use owner
  claim, the public ICE endpoint, and the `auth_enabled` / `supabase_*`
  fields of the config response.
- `config.py`: drop `VPS_SIGNALING_URL`, `ENGINE_PUBLIC_ICE_SERVERS`,
  `TURN_*`, `SUPABASE_*`, `PUBLIC_UI_URL`, `TUNNEL_SECRET`.
- `pyproject.toml`: drop dependencies left unused (`pyjwt`; `websockets` and
  `cryptography` if nothing else imports them).

### TypeScript

- `packages/core`: delete `api/supabaseAuth.ts` and `webrtc/signaling.ts`;
  remove the public path from `webrtc/session.ts`; remove relay
  classification from `api/hostProbe.ts`. `authToken` in `ServerContext` and
  `client.ts` stays and now holds the device token, stored per host.
- `packages/ui`: delete `Account`; `Login` becomes the pairing screen;
  remove relay states from `NetChip` and related components.
- `apps/web`: delete the `account` page; the `login` page becomes `pair`.
- `apps/mobile`: the first screen is the pairing screen. `Login` already
  resolves the host and has a pairing-code field (sent today as Supabase
  sign-up metadata); the email and password fields and both Supabase calls
  go, and the code is posted to `/pair` instead.

### Engine, CI, verifiers

- `engine/`: remove `public_signaling`, `signaling_client`, and their tests.
  This cannot be compiled locally; the `build-engine` workflow is the only
  validation, and the change lands as its own phase so a failure is isolated.
- `.github/workflows/build.yml`: remove the signaling-relay install and
  startup steps.
- `scripts/verify_*.py` and their tests: remove public-path stages.

### Infrastructure

1. `terraform destroy` in `infra/terraform` (one AWS instance and one
   security group), after explicit confirmation.
2. Delete `infra/terraform`, `infra/vps`, `infra/supabase`.

Left to the owner: deleting the Supabase project in its dashboard, and
clearing the removed environment variables on the Windows PC.

## Order of work

1. Network gate and pairing gate, added alongside the existing auth.
2. Launcher pairing UI; client pairing screen.
3. Python removal.
4. TypeScript removal.
5. Verifiers and CI.
6. Engine C++ removal.
7. Infrastructure teardown and deletion.
8. `VERSION` bump in `src/config.py`; `AGENTS.md` and `MEMORY.md` updated
   where they describe the removed systems.

The gates go in before anything is removed so the app is never without a
check between commits.

## Testing

- `network_gate`: table of addresses per range, IPv4-mapped IPv6, malformed
  input, and proof that `X-Forwarded-For` is ignored.
- `pairing`: code expiry, single use, 5-attempt lockout, no-active-code
  response matches wrong-code response, token hash storage, removal,
  persistence across restart, corrupt or missing device file.
- `app`: unpaired LAN request gets 401 on API routes and can load the pairing
  screen; loopback is exempt; a removed device gets 401; unknown paths still
  404.
- Client: pairing screen flow, token stored per host, 401 returns the user
  to pairing.
- Full suite: `uv run pytest tests/ -v`, `npm run test:core`,
  `npm run test:ui`, `npm test -w apps/web`.

Python tests run on macOS against `src/stubs/`, so a pass does not confirm
Windows behaviour. The launcher UI and the real network gate need a manual
check on the Windows PC from a LAN device and a Tailscale device.
