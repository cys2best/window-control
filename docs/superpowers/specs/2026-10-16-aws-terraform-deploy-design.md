# AWS Terraform deployment for the shared remote service

Date: 2026-10-16
Status: approved pending owner read-through
Related: `docs/superpowers/specs/2026-10-02-direct-webrtc-shared-turn-design.md`, `infra/shared-remote/README.md`

## Intent

The broker, edge (web + signaling) and coturn stack in `infra/shared-remote` is ready to run but has never been provisioned. The owner wants it running on AWS, reproducibly, at the lowest reasonable cost, so the relay license flow can be tested end to end with the packaged Windows app and iPhone. Terraform owns the infrastructure; a manual GitHub Actions button builds and deploys new versions.

## Goals

- One command (`terraform apply`) creates everything needed in the owner's existing AWS account; one button in GitHub Actions ships a commit.
- Keep the existing compose stack and its security properties (preflight checks, secret never in arguments/logs, two distinct bind addresses, single broker process, persistent data volume).
- Relay license and installation data survive instance replacement and are backed up.
- Predictable cost: about US$18/month fixed in Singapore, plus relay egress beyond the free 100 GB, with a budget alert.

## Non-goals

- High availability, autoscaling, load balancers, multiple brokers, Fargate/ECS (coturn's fixed public IP and 1,000-port relay range do not fit it; cost roughly doubles).
- Moving licenses to Supabase or any database (owner decided: keep `/data/licenses.json`).
- Automatic deploy on push.
- Building the Windows app or iPhone app against the new URL (separate step; needs `REMOTE_SERVICE_URL=https://emuctrl.koeeru.com` at app build/runtime).

## Anti-goals (failure even if it works)

- Touching any existing record, role, or resource in the account other than adding what is listed here. The `koeeru.com` zone holds ~150 unrelated production records.
- Long-lived AWS access keys in GitHub, or the TURN secret appearing in Terraform outputs, GitHub logs, process arguments, the repo, or AMI/user-data in plain text.
- A `terraform destroy` or instance replacement that silently loses the installation/license data.
- An SSH port open to the internet.

## Decisions made by the owner

- Provider AWS, existing account `077174987088`, region **ap-southeast-1 (Singapore)**.
- **One EC2 instance, smallest that fits** (`t3.micro`), not Fargate.
- **Two public IPv4 addresses** on that one instance, keeping TURN-over-TLS on 443 alongside HTTPS on 443 (accepted ≈US$3.65/month extra for the 443-only-network fallback).
- DNS in **Route 53, zone `koeeru.com`**.
- Images built by **GitHub Actions and pushed to ECR**.
- Deploy is a **manual button** (`workflow_dispatch`).
- Licenses stay file-based.

## Design

### Layout in the repo

- `infra/aws/` — Terraform root module (versions, providers, variables, outputs, resources). No nested modules unless a file grows past readability.
- `infra/aws/bootstrap/` — tiny one-time module creating the S3 state bucket (run once with local state, then never touched).
- `.github/workflows/remote-deploy.yml` — manual build + deploy workflow.
- `infra/aws/README.md` — first-time setup, deploy, rollback, backup restore, teardown, cost.

### Terraform state

S3 bucket in ap-southeast-1, versioned, SSE encrypted, public access blocked, locking via the S3 backend's native lockfile (`use_lockfile = true`, Terraform ≥ 1.10; local Terraform is 1.14). No DynamoDB table.

### Network

New dedicated VPC (`10.42.0.0/24` or similar) with one public subnet, internet gateway, route table. Isolated from other workloads; no NAT gateway (cost). IPv4 only for this version.

### Instance

- `t3.micro`, Amazon Linux 2023 x86_64 (pinned coturn digest is validated on amd64), `credit_specification { cpu_credits = "standard" }` to avoid surprise charges.
- One ENI with **two private IPs** in the subnet; **two Elastic IPs** associated to them: EIP-A → web/signaling, EIP-B → TURN.
- Root gp3 8 GB, encrypted; IMDSv2 required.
- 1–2 GB swap file (safety for a 1 GB box; images are not built here).
- No key pair. Shell access only through **SSM Session Manager** (instance role has `AmazonSSMManagedInstanceCore`).

### Data volume

Separate encrypted gp3 1 GB EBS volume attached to the instance, mounted at a fixed path and used as the compose `broker-data` volume (bind/driver mapping so `/data/installations.json` and `/data/licenses.json` live on it). `prevent_destroy` lifecycle on the volume. Daily snapshots via Data Lifecycle Manager, keep 7. Instance replacement re-attaches the same volume.

### Security group

| Port | Protocol | Destination | Purpose |
|---|---|---|---|
| 443 | TCP | IP A | HTTPS/WSS signaling + web |
| 3478 | UDP, TCP | IP B | TURN |
| 443 | TCP | IP B | TURN over TLS |
| 49160–50159 | UDP, TCP | IP B | TURN relay range |

Security groups cannot filter by destination private IP; per-address separation is enforced by the existing compose bind addresses. All other inbound closed (no 22, no 80). Outbound open (image pulls, ACME, AWS APIs).

### DNS

Terraform reads the existing zone with a `data` source only. Verified 2026-10-16: the public zone `koeeru.com` (`Z00472113HMCNMZX5QXC6`) is in account 077174987088, there is no private zone with the same name, no CAA record, and no existing `emuctrl`/`turn-emuctrl` names. It creates two A records, TTL 300:

- `emuctrl.koeeru.com` → EIP-A (`REMOTE_DOMAIN`)
- `turn-emuctrl.koeeru.com` → EIP-B (`REMOTE_TURN_DOMAIN`)

Both names are Terraform variables.

### Certificates

Let's Encrypt via certbot with the **Route 53 DNS-01** plugin on the instance, one certificate per name, auto-renew by systemd timer. `/etc/letsencrypt` lives on the data volume so instance replacement reuses certificates and does not hit Let's Encrypt's duplicate-certificate rate limit (my call; the volume therefore holds installations, licenses and certificates, all backed up by the daily snapshot). Renewal hook copies files into the cert directory as `edge-fullchain.pem`, `edge-key.pem`, `turn-fullchain.pem`, `turn-key.pem` (mode 0600, root) and restarts edge and turn. Port 80 stays closed. The instance role may change records only in the `koeeru.com` zone (`route53:ChangeResourceRecordSets` on that zone ARN, condition limiting to `TXT` records and `_acme-challenge.*` names via `route53:ChangeResourceRecordSetsNormalizedRecordNames` / `RecordTypes`) plus `route53:GetChange` and `ListHostedZones`.

### Secrets

- TURN signing secret: generated by Terraform (`random_password`, URL-safe charset, 64 chars), stored in **SSM Parameter Store SecureString**. Instance reads it at boot/deploy and writes it to a root-only file (0600) referenced by `REMOTE_TURN_SECRET_FILE`. It is in Terraform state, which is why the state bucket is private and encrypted.
- Operator env file written on the instance (0600, root) from Terraform-rendered non-secret values (addresses, domains, capacity, iOS IDs).

### Operator values (Terraform variables with defaults)

| Variable | Default |
|---|---|
| `REMOTE_MAX_ACTIVE_STREAMS` | 4 |
| `REMOTE_TURN_ALLOCATION_BPS` | 2,000,000 bytes/s |
| `REMOTE_TURN_AGGREGATE_BPS` | 8,000,000 bytes/s |
| `REMOTE_TURN_TOTAL_ALLOCATIONS` | 16 |
| `REMOTE_STUN_URLS` | `["stun:turn-emuctrl.koeeru.com:3478"]` |
| iOS team ID / app ID | empty (universal links unconfigured; optional) |

Addresses: `REMOTE_HTTPS_BIND_ADDRESS` = private IP A, `REMOTE_TURN_BIND_ADDRESS` = private IP B, `REMOTE_TURN_RELAY_BIND_ADDRESS` = private IP B, `REMOTE_TURN_PUBLIC_ADDRESS` = EIP-B. The existing preflight must accept these; if it rejects a private relay bind address behind AWS's one-to-one NAT, that is a finding to fix in `configure.py`, not to bypass.

### Images and registry

- ECR repositories `window-control/broker` and `window-control/edge` in ap-southeast-1, scan on push, lifecycle keeps the last 10 images.
- The production compose file currently uses `build:`. Add a small override file (e.g. `compose.images.yml`) that sets `image:` for broker, preflight and edge from ECR with the commit tag, so the instance never builds. coturn stays the public image pinned by digest.
- The edge image bakes `NEXT_PUBLIC_REMOTE_SERVICE_URL` at build time; the workflow passes `REMOTE_ORIGIN=https://emuctrl.koeeru.com`.

### Deploy workflow (manual)

`workflow_dispatch` with an optional `ref` input:

1. Assume an IAM role through the **existing** GitHub OIDC provider (`token.actions.githubusercontent.com`, already in the account; Terraform reads it, does not create or modify it). Trust limited to `repo:cys2best/window-control` on refs `refs/heads/main` and `refs/heads/feat/direct-webrtc-shared-turn` only (owner decision).
2. Build broker and edge images (linux/amd64), push to ECR tagged with the commit SHA.
3. The instance fetches the compose files and `infra/shared-remote` configuration at that commit from the GitHub tarball endpoint. The repo is public, so no token or extra bucket is needed (my call).
4. Run an SSM `send-command` on the instance: log in to ECR, pull, `docker compose -p shared-remote … up -d`, wait for health, report status.
5. Deploy role permissions: push to the two ECR repositories, `ssm:SendCommand` on that one instance and the chosen document, read command results. Nothing else.

Rollback = rerun the button with an older SHA.

### Applying Terraform (my call)

The owner applies locally with their existing AWS CLI credentials (IAM user `nta`); no Terraform runs in CI except `fmt`/`validate`. `user_data` changes are ignored by lifecycle so routine edits don't replace the instance; bootstrap steps are idempotent and re-run by the deploy command. Teardown is documented: `terraform state rm` the data volume (or remove `prevent_destroy` deliberately) before `destroy`.

### Instance bootstrap (user data)

Idempotent script: install Docker + compose plugin, certbot + Route 53 plugin, AWS CLI; configure swap; bring up the secondary private IP (Amazon Linux 2023 does this automatically via `amazon-ec2-net-utils`; verify); mount data volume; fetch secret from SSM; write operator env; obtain certificates; leave the stack ready for the first deploy. The user data contains no secrets.

### Cost guardrails

AWS Budgets monthly cost budget, US$25, email alert at 80% and 100% to `tuananh.ngo@koeeru.com` (variable, owner-provided default).

### Outputs

Public IPs, both DNS names, instance ID, ECR repository URLs, deploy role ARN (for the workflow), the `REMOTE_SERVICE_URL` value for the Windows app. Never the secret.

## Verification

- `terraform fmt -check`, `terraform validate`, and `tflint` if available, runnable in CI without AWS credentials.
- Repo test (pytest, like `tests/test_remote_deployment.py`) asserting contract properties of the Terraform and workflow files: region, instance type, no port 22/80 ingress, relay range 49160–50159, `prevent_destroy` on the data volume, OIDC trust pinned to the repo, no secret in outputs.
- After apply: preflight passes, `https://emuctrl.koeeru.com` serves the web app with a valid certificate, `turn-emuctrl.koeeru.com:443` presents a valid TLS certificate, broker `/installations` reachable, then the Linux integration tests and the license manual checklist from the relay-license plan.

## Left to the builder

- File split inside `infra/aws/`, resource names and tags (tag everything `project = window-control`, `component = shared-remote`).
- Exact mechanism to ship configuration files to the instance per deploy.
- How compose's named `broker-data` volume maps onto the mounted EBS path.
- Whether the bootstrap state module also outputs the backend config snippet.

## Deferred

- iOS team ID and bundle ID: owner will add later; variables stay empty and universal links stay unconfigured until then.
