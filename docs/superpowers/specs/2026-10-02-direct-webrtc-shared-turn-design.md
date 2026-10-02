# Direct WebRTC With Shared TURN Fallback — Design

Date: 2026-10-02
Status: Written design for user review; implementation has not started.

## Intent and scope

People install EmuCtrl on their own Windows PCs and pair their own phones. Remote use must work without a Tailscale account, tailnet policy changes, a personal VPS, or router configuration. Prefer a direct media connection; provide an operator-managed shared TURN fallback when direct connectivity fails. Preserve device authorization, capture, encoding, pacing, recovery, and recent UI fixes.

This supersedes the public-access removal decisions in the October 1 local-only design and Plans A/B. It does not undo the existing implementation wholesale. LAN and optional Tailscale access remain supported.

Product assumptions for this design: no app login is introduced; the PC owner authorizes phones through pairing. Initial support is one active remote stream per PC installation. The shared service is operated by the application publisher. These are design choices for review, rather than previously demonstrated behavior.

## Chosen approach

Use an authenticated outbound Windows-to-service WSS connection for pairing, discovery, authorized control, and session negotiation. The phone uses the same public service over HTTPS/WSS. Native WebRTC transports video and input directly when ICE finds a working direct pair, or through shared TURN otherwise.

Start with shared coturn and our signaling service on operator infrastructure. Hosted TURN remains an alternative if operating relay capacity becomes burdensome; it still requires our pairing and signaling service. Per-tailnet peer relay is excluded from the default product flow because it adds user enrollment and policy coordination.

Public signaling does not imply relayed media. Both endpoints receive STUN and TURN configuration, gather candidates, and use the normal ICE priorities with transport policy `all`. Direct connectivity cannot be guaranteed across arbitrary NATs or firewalls. See [WebRTC peer connections](https://webrtc.org/getting-started/peer-connections) and [ICE candidate priorities](https://www.rfc-editor.org/rfc/rfc8445.html#section-5.1.2.2).

## Components and boundaries

| Component | Responsibility |
| --- | --- |
| Windows Python host | Outbound authenticated connection; PC-authoritative pairing; device-token validation; instance selection; authorized remote command dispatch; engine lifecycle |
| C++ engine | Existing capture/encode/RTP/input; peer creation with session-specific ICE credentials; SDP negotiation and peer cleanup |
| Shared service | Installation routing and connection authentication; pairing rendezvous; bounded command forwarding; expiring TURN credentials; quotas |
| Shared TURN | Media/data relay for candidate pairs requiring it; authenticated allocations and capacity limits |
| Shared TypeScript core and clients | Pairing by installation identity; local or remote session negotiation; peer connection, input, and diagnostics |

Only the Python host maintains a public WSS connection. Do not restore a second, independent C++ public signaling client. Adapt the existing engine session interface for remote SDP/ICE configuration while retaining local WHEP support.

Remote dispatch uses explicit operations: pair, authenticate, list instances, select instance, obtain preview, request keyframe, change quality, negotiate SDP, and close session. Refactor shared authorized application actions as needed; do not forward arbitrary HTTP methods, paths, headers, filesystem locations, or caller-supplied URLs. Device removal and pairing-code generation remain owner-only launcher actions.

The existing local `AccessGate` stays restrictive, with `proxy_headers=False`. A remote command must pass device validation even though the outbound bridge runs on the PC. It must never acquire authorization from the local HTTP loopback exemption. Engine calls use internally generated capabilities and trusted engine endpoints, never a remote URL. The local admin surface is not published.

## Installation registration and pairing

1. On first launch, the PC automatically registers with the service and stores an installation ID and installation credential in owner-only local storage. The service stores a credential digest; neither value is shipped as a common secret. Registration alone grants no TURN access. A reset creates a new identity and invalidates the old registration.
2. The owner clicks Pair device. Retain the current six-digit code, 300-second lifetime, single use, and invalidation after five wrong attempts. For remote pairing, also create an unguessable rendezvous handle containing at least 128 random bits. Show a QR/link containing that handle and a separately entered code. A globally searchable six-digit code is insufficient.
3. The handle routes the attempt to the connected PC. The PC verifies the code and creates the device token using the existing `PairingStore`; only the token digest is persisted on the PC. Success or expiry closes the rendezvous. All attempts count toward the same PC pairing window, regardless of sender.
4. The phone stores the token keyed by installation ID using existing secure-storage conventions. Local per-host tokens remain usable without automatic migration. A remote token is not tied to a mutable IP address.
5. On later connections, the service routes authentication to that installation. The PC validates the token before opening a device session. The service does not persist viewer tokens or log them. TLS protects their transit; this design trusts the operator service with signaling and authorization messages.
6. Revocation rejects subsequent commands, closes that device's active peer, and invalidates its service session. Device renaming, revocation, and new pairing windows remain launcher actions.

An offline PC produces a clear offline result without accepting a pairing attempt or issuing relay credentials. Invalid, expired, or unknown pairing attempts reveal no instance details. No installation may select or join another installation's session.

## Remote session flow

1. The paired viewer authenticates to its installation. The PC confirms validity before the broker admits an authenticated device session.
2. List/select and preview operations travel through bounded commands to the Python host. The host retains existing instance ownership and generation checks. Only one active remote stream is admitted per installation; an explicit switch closes the prior peer and cancels outstanding work.
3. After selection, the service supplies short-lived ICE configuration to both endpoints for that session. The engine must consume username and credential as well as TURN URLs. Do not place the operator signing secret in either endpoint.
4. The viewer creates a receive-video peer with the existing input data channel, gathers candidates, and sends its SDP through the service. The host negotiates with its selected engine and returns the answer through the service. Neither endpoint receives an unreachable private HTTP URL as its remote signaling transport.
5. For the first implementation, exchange complete gathered SDP, reusing the current non-trickle WHEP model. Use a 30-second overall remote setup deadline; gathering and command dispatch consume that deadline rather than each resetting it. Trickle ICE is deferred unless real setup measurements require it.
6. Video and interactive input use the peer connection. Selection, quality, keyframe, previews, and lifecycle commands use authenticated signaling. A late answer after cancellation is discarded and its engine peer is closed.
7. Report the selected candidate pair after connectivity is established. An allocated relay candidate or a WSS connection must not be reported as proof that media is relayed.

ICE uses all supported host, server-reflexive, and relay candidates without stripping address families or forcing relay in normal use. Do not promise that an established relay session automatically switches to direct: a new connection or supported ICE restart must re-evaluate the route. Test-only relay forcing is permitted for validation.

## TURN credentials, deployment, and compatibility

Use coturn secret-derived credentials with a 3,600-second validity, scoped in the username to installation, session, and endpoint. Issue only after PC-confirmed authorization and enforce per-installation admission at the service. Coturn's signing secret remains backend-only. See [coturn authentication and bandwidth configuration](https://github.com/coturn/coturn/blob/master/README.turnserver).

Obtain fresh credentials before expiry. Replacing client configuration alone does not refresh an existing allocation: validate the actual engine/client refresh behavior and use a controlled peer replacement with fresh credentials if required. This behavior is a release criterion; an hour-old stream must not silently lose its relay path.

Anonymous automatic registration does not prevent relay abuse by itself. Bound registration and pairing attempts, session admission, credential issuance, allocation counts, aggregate egress, and message sizes on the operator service. Configure coturn to deny private, loopback, link-local, multicast, and infrastructure destinations. Expiring credentials do not instantly revoke existing allocations; device revocation closes the legitimate engine peer, while quotas and expiry bound residual credential abuse. A public launch requires a measured capacity limit and cost ceiling.

Support TURN/UDP plus TURN over TCP/TLS for networks blocking endpoint UDP. TURN/TLS on port 443 and HTTPS/WSS must use separate listener addresses or an explicitly tested multiplexing setup; two services cannot independently bind the same address and port. Initial infrastructure must reserve sufficient public addresses or equivalent routing for both. Domains, cloud resources, and the paid capacity limit are deployment configuration, not user setup.

The current engine manifest selects libdatachannel without a libnice backend. Official [libdatachannel documentation](https://libdatachannel.org/pages/reference.html) states TURN TCP/TLS support requires libnice. Verify the actual Windows dependency versions/backend and test the packaged binary before selecting the smallest required backend/build change. ICE-TCP support alone is not evidence of TURN/TLS compatibility. Preserve capture and encoding regardless of the ICE backend choice.

The old public implementation is reference material, not a drop-in replacement. In particular, do not restore static distributed TURN credentials, the generic loopback HTTP tunnel, Supabase login, or an engine-level public WSS client merely to recover connectivity.

## Failure and recovery behavior

- Service disconnect: retain an already working media peer for a bounded 60-second reconnect grace period, then close if authorization cannot be re-established. Reconnection revalidates the device; stale or canceled commands are not replayed. LAN connections remain independent of the service.
- Direct connectivity unavailable: normal ICE can select TURN without requiring a manual switch. Failure of both direct and relay produces an actionable connection error and closes partial sessions.
- TURN unavailable: direct setup remains usable with STUN. Relay-dependent users receive a relay availability error; no silent indefinite connection spinner.
- Quality recovery: preserve existing loss protection. After an explicit reconnect or new media route, reset stale sampling and allow the source's chosen quality to be retried. A previous 360p downgrade must not permanently constrain a new healthy session. Automatic upgrades during the same session are outside initial scope.
- Diagnostics: record selected route, candidate address family, TURN transport, source/decoded dimensions, bitrate, packet loss, FPS, and freeze duration. Redact tokens, pairing handles/codes, credentials, and complete SDP from ordinary logs.

## Acceptance criteria

All connectivity/media cases use the actual packaged Windows engine and iPhone client, with Tailscale disabled and no router changes. Unit tests on macOS cannot establish these results.

1. A fresh PC and phone pair remotely using the owner-provided QR/link and code, then reconnect without entering private addresses or creating infrastructure accounts.
2. With a reachable direct pair, diagnostics confirm a direct candidate pair and TURN carries no continuous media payload. Allocations/checks alone do not fail this criterion.
3. With direct connectivity unavailable, diagnostics confirm TURN/UDP and sustain the same intended quality. With endpoint UDP blocked, test TURN/TCP/TLS on the packaged engine and iPhone, including port 443.
4. On a controlled path independently sustaining at least 12 Mbps, a landscape 1920×1080 source streams at the 8 Mbps target and at least 30 decoded FPS for ten minutes. Verify actual dimensions, zero adaptive downgrades, no single freeze over one second, and under 1% total freeze time. A tier label or the previous 1080×608 source is not full-HD evidence.
5. Repeat IPv4 and IPv6 media tests and mixed-family relay reachability. Investigate RTP/SRTP packet size, fragmentation, and MTU if the earlier IPv6 failure recurs; changing signaling does not prove it fixed.
6. Two independent installations connect concurrently through the shared service. Cross-installation joins, wrong tokens, revoked tokens, replayed pairing, stale generations, and arbitrary remote forwarding are rejected. Revocation stops active input and media within five seconds while the PC is connected.
7. Service restart, PC offline, TURN unavailable, cancellation during SDP negotiation, and viewer/network reconnect clean up peers and display the expected state. Local LAN access still works during service outage.
8. A relay session survives a credential-expiry boundary with successful reauthorization/renewal or controlled reconnection. Expired credentials cannot create a new allocation. Capacity/quota rejection is explicit and does not interfere with a different installation's existing stream.

The completed Tailscale peer-relay capacity tests are context only. They demonstrate neither shared TURN performance nor resolution of the media-quality problem. Preserve separate evidence for network capacity and decoded video quality.

## Migration and delivery boundaries

Work forward from the current branch. Keep `PairingStore`, launcher device management, local `AccessGate`, local WHEP, and recent engine/UI changes. Replace only the local-only remote policy and its removal guards when corresponding authenticated public behavior is implemented and tested.

Expected areas are the Python host lifecycle/dispatch, engine session ICE configuration, shared core API/session transport, web/mobile pairing and route status, and new broker/coturn deployment configuration. Existing source/tests must determine exact task boundaries in the implementation plan.

Sequence implementation around a local fake broker and test TURN first, then real Windows/iPhone compatibility and media validation, then an operator-managed deployment. Restoring files from git cannot recreate deleted cloud resources. No new paid resources or production service are created by this design change.

This document is the architectural review artifact. After its review, write the implementation plan against these requirements, including exact interfaces, regression tests, build compatibility checks, and deployment validation. Product code changes begin after the written plan review.
