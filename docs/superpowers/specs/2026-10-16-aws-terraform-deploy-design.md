# AWS deployment with Cloudflare TURN for the shared remote service

Date: 2026-10-16
Status: approved 2026-10-16 (revision 2: replaces the coturn/two-IP design)
Related: `docs/superpowers/specs/2026-10-02-direct-webrtc-shared-turn-design.md`, `infra/shared-remote/README.md`

## Intent

The remote service (broker + web edge) has never been deployed. The owner wants to test the full remote and relay-license flow privately, with only their own PC, before opening it to anyone else, at the lowest reasonable cost and without new load on their resource-limited Windows PC. Terraform owns the infrastructure; a manual GitHub Actions button builds and deploys.

Relay moves from self-hosted coturn to **Cloudflare Realtime TURN**, which is used for this test and is intended for the later public launch too, so the test exercises what will ship.

## Goals

- `terraform apply` creates the AWS side and the Cloudflare DNS record; one GitHub Actions button ships a commit.
- Existing Python broker and web edge run unchanged except for a Cloudflare TURN credential issuer and edge changes needed behind Cloudflare's proxy.
- Relay credentials still go only to licensed installations (`/data/licenses.json`).
- Fixed cost about US$8–9/month; relay free up to Cloudflare's monthly allowance.
- Installation and license data survive instance replacement and are backed up.

## Non-goals

- Self-hosted coturn, a second IP, TURN ports on AWS, Let's Encrypt.
- Running anything on the Windows PC beyond the existing EmuCtrl app (no Docker there).
- Cloudflare Workers / Durable Objects port of the broker (possible later; would remove the server entirely).
- High availability, load balancers, autoscaling, IPv6-only hosting (later cost optimization).
- Moving licenses into a database.
- iOS universal links (owner adds team/bundle IDs later).

## Anti-goals

- Long-lived AWS keys in GitHub; Cloudflare or AWS secrets in Terraform outputs, GitHub logs, process arguments, the repo, or user data in plain text.
- Losing installation/license data on `terraform destroy` or instance replacement.
- Rate limits keyed on Cloudflare's addresses instead of real client addresses (would let one abuser exhaust everyone's pairing/auth budget, or rate-limit all users together).
- An origin reachable directly from the internet, bypassing Cloudflare.
- SSH open to the internet.

## Decisions made by the owner

- Single user for now; test before going public.
- AWS account `077174987088`, region **ap-southeast-1 (Singapore)**, EC2 (not Fargate, not Workers yet).
- Relay: **Cloudflare Realtime TURN**, for test and public.
- Domain: **`nendojo.com`**, registered and DNS-hosted on Cloudflare.
- Images built in GitHub Actions, pushed to ECR; **manual deploy button**; deploy role trusted only for `refs/heads/main` and `refs/heads/feat/direct-webrtc-shared-turn` of `cys2best/window-control` (public repo).
- Budget alert email `tuananh.ngo@koeeru.com`.
- Licenses stay file-based.
- Relay spend: no hard cap during the private test; a Cloudflare billing notification is set up now; a per-license relay-hours limit is required before going public.
- The deploy workflow file is pushed to `main` once written and reviewed (owner confirms at push time).

## Design

### Request path

```
PC app / iPhone ──HTTPS/WSS──▶ Cloudflare proxy (emuctrl.nendojo.com, TLS)
                                   │  only Cloudflare IP ranges allowed in
                                   ▼
                     EC2 :443 edge nginx (Cloudflare Origin CA cert)
                       ├─ /connect, /installations… ─▶ broker :8000 (container network)
                       └─ /pair, static web, AASA
Media: phone ⇄ PC direct; fallback ⇄ turn.cloudflare.com (credentials from broker)
```

### Domain and Cloudflare

- Hostname `emuctrl.nendojo.com` (variable), proxied A record → the instance's Elastic IP, managed by Terraform's Cloudflare provider. Apply uses a Cloudflare API token in the environment (`CLOUDFLARE_API_TOKEN`) scoped to DNS edit on `nendojo.com`; never stored in Terraform files.
- SSL mode **Full (strict)**; origin presents a **Cloudflare Origin CA** certificate for the hostname (free, long-lived). Terraform may create it via the Cloudflare provider, or the owner creates it once and stores cert+key in SSM SecureString; either way the key never appears in outputs or logs.
- WebSockets through the proxy are supported. Cloudflare closes idle WebSockets after about 100 s. The PC client uses the `websockets` default ping (20 s), and nginx already closes sockets idle for 75 s today, so Cloudflare's limit is looser than the current one (my call: no new keepalive; revisit only if the test shows drops).

### Real client addresses (required)

The broker rate-limits registration, pairing and `viewer_auth` by client IP. Behind Cloudflare every connection arrives from a Cloudflare address. The edge must:

- accept connections only from Cloudflare's published IP ranges (also enforced in the security group), and
- derive the client address from `CF-Connecting-IP` only for those peers (nginx `real_ip` with Cloudflare ranges), then pass it to the broker as it does today via `X-Forwarded-For`.

A test proves a spoofed `CF-Connecting-IP` from a non-Cloudflare peer is not trusted.

Keeping ranges current (my call): the repo vendors Cloudflare's IPv4 list (used by the edge image's `real_ip` config); the security group uses the Cloudflare provider's IP-ranges data source at apply time; a scheduled CI check fetches `https://www.cloudflare.com/ips-v4` and fails when it differs from the vendored list, telling the owner to update the file, redeploy and re-apply. Nginx forwards the post-`real_ip` client address to the broker.

### Relay: Cloudflare Realtime TURN

- Owner creates a TURN key in the Cloudflare dashboard once. Its key ID and API token are secrets stored in **SSM Parameter Store SecureString**, put by the owner with the AWS CLI (Terraform references the parameter names only, so the values never enter Terraform state).
- New broker credential issuer replacing the coturn shared-secret signer: for each admitted endpoint (host and viewer separately, as today) it calls Cloudflare's credential-generation API with a TTL matching the bundle lifetime (3600 s) and returns the ICE servers Cloudflare gives (UDP/TCP 3478, TLS 5349 and TLS 443 — so the 443-only-network fallback is kept for free). Exact endpoint and response shape: builder verifies against current Cloudflare docs.
- Issuer shape (my call): one Cloudflare credential call per endpoint, so two per admitted select/renew, giving distinct host and viewer credentials. No per-session bookkeeping and no early revocation; credentials expire with their TTL, and license revocation takes effect at the next admission as today.
- Licensing unchanged: unlicensed installations get the STUN-only bundle; revocation applies at the next admission.
- **Constraint:** `MediaAdmission.authorize` is synchronous and documented as never awaiting network I/O. The design keeps admission decisions synchronous and moves the Cloudflare call to a bounded async step (with timeout) before the admission is committed, or an equivalent the builder chooses. On API failure or timeout: fail to STUN-only with `relay_available: false`, never hang and never fail the whole select.
- STUN URL: Cloudflare's public STUN (`stun:stun.cloudflare.com:3478`).
- Settings: `BrokerSettings` gains a relay provider choice; Cloudflare mode requires key ID and token files and no `turn_shared_secret`. The coturn mode stays for the existing tests and Linux integration suite.

### Compose on the instance

`configure.py` gains a Cloudflare mode (my call, not a fork): it skips TURN files, TURN capacity and the second bind address, still requires the origin certificate, key and the Cloudflare TURN secret files, and its DNS check accepts that a proxied name resolves to Cloudflare's public addresses (it still rejects private/denied results). A production compose variant without coturn and without TURN preflight inputs (e.g. `compose.cloudflare.yml` or profile flags). Preflight still validates domain, origin certificate files, capacity and secret file syntax for the inputs that remain. Images come from ECR by commit tag (override with `image:`); broker data volume on the EBS data disk.

### AWS resources (Terraform, `infra/aws/`)

- **State:** S3 bucket in ap-southeast-1 (versioned, encrypted, public access blocked) with native lockfile; created by a one-time `infra/aws/bootstrap/` module.
- **Network:** dedicated small VPC, one public subnet, internet gateway; IPv4 only.
- **Instance:** `t4g.nano` (ARM, 0.5 GB) default, `t4g.micro` as a variable (my call: after the first deploy, the runbook checks memory and swap use with preflight, broker and nginx running; if the box swaps steadily, switch to `t4g.micro`, about $4/month more); Amazon Linux 2023 arm64; `cpu_credits = "standard"`; IMDSv2 required; encrypted root gp3 8 GB; 1 GB swap; no key pair; SSM Session Manager only. `user_data` changes ignored by lifecycle; bootstrap is idempotent and re-run by deploy.
- **Public IP:** one Elastic IP.
- **Security group:** inbound TCP 443 from Cloudflare IPv4 ranges only (fetched via the Cloudflare provider's IP-ranges data source); nothing else inbound. Outbound open.
- **Data volume:** separate encrypted gp3 1 GB EBS, `prevent_destroy`, mounted for the broker data (`installations.json`, `licenses.json`); Data Lifecycle Manager daily snapshots, keep 7.
- **ECR:** `window-control/broker`, `window-control/edge`, scan on push, keep last 10.
- **Instance role:** SSM core, ECR pull on those repositories, read the named SSM parameters. Nothing else.
- **Deploy role:** via the existing GitHub OIDC provider (read with a data source, never modified), trusted only for the two refs above; ECR push to the two repositories, `ssm:SendCommand` on this instance and the chosen document, read command results.
- **Budget:** US$25/month, alerts at 80% and 100% to `tuananh.ngo@koeeru.com`.
- **Tags:** `project = window-control`, `component = shared-remote`.
- **Applying:** owner runs Terraform locally with existing AWS CLI credentials (IAM user `nta`) and a Cloudflare token; CI runs only `fmt`/`validate`. Teardown documented, including deliberately removing the protected data volume.

### Deploy workflow (`.github/workflows/remote-deploy.yml`)

`workflow_dispatch`, run from one of the two trusted branches; it builds that branch's HEAD (no arbitrary-SHA input, so the OIDC branch pin and the built code always match). Optional input `image_tag` redeploys an existing ECR tag without building, for rollback. GitHub only offers dispatch for workflows present on the default branch, so the workflow file must reach `main` before the button exists.

1. Assume the deploy role via OIDC.
2. Build broker and edge for `linux/arm64` (ARM runner or buildx), push to ECR tagged with the commit SHA. Edge build gets `REMOTE_ORIGIN=https://emuctrl.nendojo.com`.
3. SSM `send-command`: instance downloads that commit's `infra/shared-remote` files from GitHub's public tarball endpoint, writes the operator env (0600) and secret files (0600) from SSM parameters, pulls images, `docker compose up -d`, waits for preflight and health, reports status.

Rollback: rerun with `image_tag` set to an older SHA.

### First run order (my call)

1. `terraform apply` (bootstrap state module first). Terraform creates the Cloudflare Origin CA certificate and stores cert and key in SSM SecureString; the private key is therefore in Terraform state, which is private, encrypted and versioned.
2. Owner puts the Cloudflare TURN key ID and API token into SSM with the AWS CLI.
3. Owner presses the deploy button. Until then the instance runs bootstrap only and tolerates missing images and secrets.

### Operator values (defaults)

| Setting | Default |
|---|---|
| Max active streams | 4 |
| Domain | `emuctrl.nendojo.com` |
| STUN | `["stun:stun.cloudflare.com:3478"]` |
| TURN credential TTL | 3600 s |
| iOS team/app ID | empty |

### Outputs

Elastic IP, hostname, instance ID, ECR URLs, deploy role ARN, and `REMOTE_SERVICE_URL=https://emuctrl.nendojo.com` for the Windows app. Never secrets.

## Cost (Singapore, approximate)

| Item | Monthly |
|---|---|
| `t4g.nano` 24/7 | ≈ $3.90 |
| 1 public IPv4 | $3.65 |
| EBS root + data | ≈ $0.80 |
| ECR, snapshots, SSM | < $0.50 |
| **Fixed** | **≈ $8.50** |
| Cloudflare TURN | free to 1,000 GB/month (shared with Realtime SFU), then $0.05/GB |
| Cloudflare proxy, DNS, Origin CA | free |

AWS egress stays small because media never passes through the instance.

## Verification

- Unit: Cloudflare issuer (distinct host/viewer credentials, TTL, API failure → STUN-only, timeout bounded, token never logged), settings validation, real-IP handling, compose/Terraform/workflow contract tests in the style of `tests/test_remote_deployment.py` (region, instance type, only 443 from Cloudflare ranges, `prevent_destroy`, OIDC trust pinned to the two refs, no secret outputs).
- `terraform fmt -check`, `terraform validate` (and `tflint` if available) in CI.
- After apply: `https://emuctrl.nendojo.com` serves the web app; PC registers and connects; license issue → activate → launcher shows active; forced-relay stream uses `turn.cloudflare.com`; unlicensed PC gets STUN-only; the relay-license manual checklist passes.

## Left to the builder

- File layout inside `infra/aws/`, how the Cloudflare call is made non-blocking, how compose's broker volume maps to the EBS mount, Origin CA creation path (Terraform vs one-time manual), keepalive mechanism if needed.

## Deferred

- Per-license relay-hours limit (required before public launch).

- iOS team/bundle IDs.
- IPv6-only origin to drop the IPv4 charge.
- Workers + Durable Objects broker (no server).
