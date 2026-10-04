# Shared signaling and TURN deployment proposal

This directory prepares a Linux deployment. It does not provision or purchase a
service. Provider, region, public DNS names, iOS team/app identifiers, and monthly
budget remain operator decisions. Windows/iPhone TURN/TLS on port 443 and media
performance require the separate packaged-device acceptance run; this Linux
integration does not establish those results.

## Required operator inputs

Use an operator-owned environment file outside Git, readable only by its owner.
Compose rejects missing inputs; the preflight container checks addresses, DNS,
capacity, certificate file presence, and secret syntax before starting services.

| Input | Required value |
| --- | --- |
| `REMOTE_HTTPS_BIND_ADDRESS` | Address A on the Linux host, for web/signaling TCP 443 |
| `REMOTE_TURN_BIND_ADDRESS` | Distinct address B on the host, for TURN UDP/TCP 3478 and TLS TCP 443 |
| `REMOTE_TURN_RELAY_BIND_ADDRESS` | Local relay address; it may be private behind a provider's one-to-one NAT |
| `REMOTE_TURN_PUBLIC_ADDRESS` | Public relay address corresponding to that local relay address |
| `REMOTE_DOMAIN` | Public web/signaling DNS name resolving to address A's public mapping |
| `REMOTE_STUN_URLS` | JSON array of operator-selected STUN URLs; independently hosted endpoints are needed for independent STUN availability |
| `REMOTE_TURN_DOMAIN` | Public TURN DNS name resolving to address B's public mapping |
| `REMOTE_CERT_DIRECTORY` | Absolute external directory containing `edge-fullchain.pem`, `edge-key.pem`, `turn-fullchain.pem`, `turn-key.pem` |
| `REMOTE_TURN_SECRET_FILE` | Absolute external file containing a cryptographically random 32–256 character URL-safe secret |
| `REMOTE_MAX_ACTIVE_STREAMS` | Positive measured aggregate stream admission cap; one stream per installation remains enforced |
| `REMOTE_TURN_ALLOCATION_BPS` | Allocation allowance in **bytes/second**, at least 2,000,000 for the validation profile |
| `REMOTE_TURN_AGGREGATE_BPS` | Explicit total allocation bandwidth allowance in bytes/second |
| `REMOTE_TURN_TOTAL_ALLOCATIONS` | Explicit allocation cap, including both endpoints and reconnection headroom |

Issue certificates for the corresponding DNS names with complete public chains.
Do not use the disposable test CA in production. Confirm certificate names,
chains, expiry and renewal with your certificate issuer. Required input files
are mounted read-only; protect the operator environment file and TLS private
keys. Generate the signing secret directly into its file, with a private umask,
without printing it or putting its value in command arguments.

The image is pinned to coturn 4.6.3 index digest
`sha256:71c3c990283385567f11794ee692e3a47b66fd9b0bb39e42afbe776e331dd888`.
The Linux amd64 manifest is
`sha256:908d02955aee04adac06b4b04805de55ca0fda04c2677cb50efa3e8407bb4366`.
See the acceptance workflow artifact for the executed image and transport
results. Do not replace the digest until the same allocation tests pass.

## Startup and ownership

After reviewing the concrete operator inputs and obtaining deployment approval:

```sh
docker compose --project-name shared-remote \
  --env-file /secure/shared-remote/operator.env \
  -f infra/shared-remote/compose.production.yml up -d --build
```

The preflight rejects equal A/B addresses, wildcard listeners, insufficient
capacity and public service names resolving into denied address ranges. It
renders only named TURN inputs. The secret enters the generated config from its
mounted file; config mode is 0600 in a dedicated RAM-backed volume mounted only
by preflight and coturn. It is absent from Compose configuration, process
arguments, build context and logs. There is no static endpoint account or
anonymous TURN access.

The broker has one process, one worker and one digest-store owner. Its fixed
container name prevents Compose scaling; never run another broker against the
same data volume or introduce additional workers. The persistent `broker-data`
volume stores installation IDs and credential digests, never viewer tokens.
Restart preserves PC identity but requires each phone to authenticate through
the connected PC again. Back up this volume privately; loss invalidates existing
PC identities. Do not use `down --volumes` in production.

Only the edge publishes signaling. The broker trusts forwarded addresses solely
on its dedicated container network; the edge replaces forwarded headers with
the actual peer address. Do not publish port 8000 or attach untrusted services to
that network. The local Windows app and its restrictive AccessGate remain
separate. `/connect` upgrades to WSS; fixed installation routes reach the broker;
`/pair`, static HTML and Next.js `.txt` payloads are served by the edge.
`NEXT_PUBLIC_REMOTE_SERVICE_URL` is set during the web image build from the
operator's HTTPS origin. Changing the origin requires rebuilding the image.

## iOS association

Set all three optional values together: `REMOTE_IOS_TEAM_ID` (10-character Apple
team ID), `REMOTE_IOS_APP_ID` (bundle ID), and
`REMOTE_IOS_ASSOCIATED_DOMAIN` (exactly `REMOTE_DOMAIN`). Preflight generates
`/.well-known/apple-app-site-association` allowing `/pair` for that application.
The packaged iOS app must also have the matching `applinks:<domain>` associated
domain entitlement. Without these inputs, universal-link deployment is
explicitly unconfigured and the association endpoint returns 404; ordinary web
pairing remains available. Partial or mismatching values fail preflight.

## Network and abuse boundaries

Allow ingress only to A TCP 443; B UDP/TCP 3478 and TCP 443; and the relay address
UDP/TCP 49160–49200. Preserve those relay ports through provider NAT. The host and
provider firewall both need these rules. For an existing nftables `inet filter`
input chain, equivalent rules are below (substitute operator values; preserve
existing SSH/established-connection rules and default policy):

```text
ip daddr ADDRESS_A tcp dport 443 accept
ip daddr ADDRESS_B udp dport 3478 accept
ip daddr ADDRESS_B tcp dport { 3478, 443 } accept
ip daddr RELAY_LOCAL_ADDRESS udp dport 49160-49200 accept
ip daddr RELAY_LOCAL_ADDRESS tcp dport 49160-49200 accept
```

For IPv6 host listeners use equivalent `ip6 daddr` rules with the operator's
IPv6 addresses. TCP here includes endpoint TURN/TCP and TURN/TLS; allocation
relays use UDP (`no-tcp-relay` prevents RFC6062 arbitrary TCP forwarding).
Permit outbound UDP to public peer addresses and established reply traffic;
mirror the denied destination ranges in the host/provider egress firewall.

The production template denies private, loopback, link-local, shared-address,
metadata/infrastructure, multicast and reserved destination ranges in both
families, including IPv4-mapped IPv6 and transition prefixes. The renderer also
denies the configured service host addresses. Coturn permission requests contain
IP addresses, not hostnames: a private hostname resolved by a client still
produces a denied destination IP. Public service DNS inputs are resolved and
checked at preflight; re-run preflight after DNS changes. Add provider-specific
infrastructure addresses to the deny template and firewall before deployment.
There are no production allow exceptions. The test-only exception
`127.0.0.2:3480` is a disposable bundled echo peer and must never be deployed.

Broker limits: 12 credential issuances per installation per rolling hour,
registration 5/IP/hour, pairing 10/IP/minute, commands 120/viewer/minute, previews
2/viewer/second. Buckets allow their documented initial burst and natural refill.
Both key collections cap at 4,096; new keys fail closed at saturation. Active
keys are never evicted for new traffic. Fully refillable buckets and expired
issuance windows are reclaimed under one lock, including HTTP worker threads.

Stream admission bounds **new admissions**. Already-issued credential misuse
and remaining allocation lifetime also require coturn quotas, an egress budget,
monitoring and operator intervention. Monitor aggregate ingress/egress,
allocation count, quota refusals, disk persistence, CPU and certificate expiry
without collecting credentials or viewer tokens. Coturn session logs and HTTP
access logs are disabled to avoid credential-bearing identifiers.

## Capacity and cost proposal

An illustrative two-stream profile is 8 Mbps media per stream: 16 Mbps relay
egress before overhead, approximately **7.2 decimal GB/hour**. Provision ingress
as well as egress, both endpoint allocations, protocol/retransmission overhead,
and reconnection headroom. An illustrative starting test profile is two stream
admissions, 2,000,000 bytes/s per allocation, 8,000,000 aggregate bytes/s and
eight allocations. These are examples, never hidden deployment defaults; set a
measured stream cap after testing the intended instance and network.

```text
monthly_cost = fixed_compute + fixed_addresses
             + billable_egress_GB * price_per_GB
```

Use the selected provider's actual billing definition (including both endpoints
where applicable), region, address charges and measured overhead. No provider
quote or production monthly cost is assumed. Record a monthly budget and alert
threshold before authorizing paid deployment.

## Real integration and teardown

Linux with Docker Engine/Compose and OpenSSL is required. From the repository:

```sh
uv run pytest tests/test_remote_deployment.py tests/integration/test_shared_remote.py -v -s --tb=short
```

Each fixture invokes `scripts/shared_remote_test_env.py --directory <temp>` to
generate a fresh CA, certificates and signing secret outside Git. It builds the
real HTTPS/WSS broker/edge, obtains endpoint credentials through a PC-approved
select, and runs bundled `turnutils_uclient` for UDP, TCP and TLS. `-t` selects
client-to-server TCP; `-S` adds TLS; `-E` verifies the CA. A minimal TURN wire
probe checks exact echoed payload bytes and enforces TLS hostname verification;
bundled-client counts alone cannot prove arbitrary payload contents. Wrong and
expired credentials, CA/hostname failures, denied permissions, admission
isolation, restart and TURN-down direct/STUN configuration are separate cases.
A missing daemon is a test failure, never a skipped success.

For manual stack inspection, generate a new empty external directory and use
`test.env` with `compose.test.yml` under a unique `--project-name`. Test listeners
are HTTPS/WSS 8443, TURN UDP/TCP 3478, TLS 5349 and relay 49160–49200. Host-network
TURN listeners mean only one such test stack may run per Linux host at a time.
Always use the same project, file and env file for teardown:

```sh
docker compose --project-name YOUR_UNIQUE_TEST_PROJECT \
  --env-file /absolute/test-directory/test.env \
  -f infra/shared-remote/compose.test.yml down --volumes --remove-orphans
```

Remove only that run's generated directory afterwards. The fixture does this in
`finally`, including failed tests. CI runs the same tests through the
`frontend-packages` workflow's `real-turn` job and uploads sanitized assertion,
image and payload-hash evidence. No general Docker prune or unrelated project
teardown is used.

For production secret or certificate rotation, update the external files,
recreate preflight and affected services, then check TLS/allocation behavior.
Broker and coturn must use the same new secret; existing credentials may fail
during rotation. Plan an announced maintenance window. On TURN failure, direct
ICE and STUN configuration remain accessible through signaling; relay-only
clients cannot connect until TURN recovers. STUN hosted by the failed coturn shares
that outage; configure independent operator-selected STUN endpoints if independent
STUN availability is required. The TURN-down test proves configuration access,
not STUN/media reachability or a live relay-health signal. Production DNS/certificates, public
address routing, paid capacity and packaged-device compatibility remain operator
and device acceptance work.

Primary option references:
[coturn 4.6.3 server configuration](https://github.com/coturn/coturn/blob/4.6.3/README.turnserver)
and [bundled client flags](https://github.com/coturn/coturn/blob/4.6.3/README.turnutils).
