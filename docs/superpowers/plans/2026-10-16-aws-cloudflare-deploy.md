# AWS + Cloudflare Deployment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deploy the existing broker and web edge to one small EC2 instance behind Cloudflare's proxy, with relay credentials from Cloudflare Realtime TURN, provisioned by Terraform and shipped by a manual GitHub Actions button.

**Architecture:** The broker gains a Cloudflare TURN provider: before the synchronous admission decision, the PC's `media_authorize` handler fetches two Cloudflare credentials (host and viewer) with a bounded timeout, and `MediaAdmission.authorize` uses them only if the installation is still licensed. A coturn-free compose file runs preflight, broker and edge from ECR images on the instance. Terraform creates the VPC, instance, data volume, ECR, IAM, budget and the Cloudflare DNS record and origin certificate; the deploy workflow builds arm64 images and runs a versioned deploy script on the instance through SSM.

**Tech Stack:** Python 3.11+ (FastAPI, Pydantic v2, httpx), nginx 1.28, Docker Compose, Terraform ≥ 1.10 (AWS provider ~> 6.0, Cloudflare provider ~> 5.0, tls, random), Amazon Linux 2023 arm64, GitHub Actions (OIDC), pytest via `uv run pytest`.

**Spec:** `docs/superpowers/specs/2026-10-16-aws-terraform-deploy-design.md` (revision 2).

## Global Constraints

- Region `ap-southeast-1`; instance `t4g.nano` default, `t4g.micro` allowed by variable; Amazon Linux 2023 arm64; `cpu_credits = "standard"`; IMDSv2 required; no key pair; SSM Session Manager only.
- Hostname `emuctrl.nendojo.com` (variable `hostname`), zone `nendojo.com` on Cloudflare, record proxied, SSL mode Full (strict), origin cert = Cloudflare Origin CA.
- Security group: inbound TCP 443 from Cloudflare IPv4 ranges only; nothing else inbound.
- Deploy role trusted only for `repo:cys2best/window-control:ref:refs/heads/main` and `repo:cys2best/window-control:ref:refs/heads/feat/direct-webrtc-shared-turn`; existing OIDC provider `token.actions.githubusercontent.com` read via data source, never created or modified.
- ECR repositories `window-control/broker`, `window-control/edge`; scan on push; keep last 10 images.
- SSM SecureString names: `/window-control/shared-remote/cloudflare-turn-key-id`, `/window-control/shared-remote/cloudflare-turn-api-token` (owner puts values; Terraform never holds them), `/window-control/shared-remote/origin-cert`, `/window-control/shared-remote/origin-key` (Terraform creates).
- Budget US$25/month, alerts at 80% and 100% to `tuananh.ngo@koeeru.com`.
- Tags on every AWS resource: `project = window-control`, `component = shared-remote`.
- Cloudflare TURN credential TTL 3600 s; credential call timeout 3 s total; STUN `stun:stun.cloudflare.com:3478`; max active streams default 4.
- Secrets never in Terraform outputs, GitHub logs, process arguments, the repo, or user data.
- Data volume: encrypted gp3 1 GB, `prevent_destroy`, daily DLM snapshots keep 7.
- coturn mode remains the default for existing tests and `tests/integration/test_shared_remote.py`; nothing in this plan may break them.
- Tests: `uv run pytest`; commits follow `COMMIT_CONVENTION.md`, no AI attribution.
- Pushing the deploy workflow to `main` needs the owner's explicit go-ahead at push time; no task pushes.
- Running `terraform apply` against the real account is the owner's step, never a task step.

## Review Focus

1. **Cloudflare API slow, down or returning 4xx/5xx** → the select still succeeds within ~3 s with a STUN-only bundle and `relay_available: false` (Task 2 `test_cloudflare_failure_falls_back_to_stun_only`).
2. **Cloudflare response with port-53 URLs, extra keys, STUN entries, or URLs our validator rejects** → sanitized to a valid TURN server dict, or treated as failure; never a `SessionIceBundle` validation error that fails admission (Task 1 `test_response_is_sanitized_to_valid_turn_server`).
3. **License revoked while credentials are being fetched** → prefetched credentials are discarded and the bundle is STUN-only (Task 2 `test_revocation_during_fetch_discards_prefetched_credentials`).
4. **Client sends a forged `CF-Connecting-IP` directly to the origin** → broker sees the real peer address, not the forged one (Task 3 `test_forged_cf_connecting_ip_from_non_cloudflare_peer_is_ignored`).
5. **Deploy triggered with a malformed SHA or image tag** → script refuses before stopping or changing the running stack (Task 6 `test_deploy_script_rejects_malformed_revision_before_changes`).

---

### Task 1: Cloudflare TURN credential client

**Files:**
- Create: `src/broker/cloudflare_turn.py`
- Modify: `infra/shared-remote/broker.Dockerfile` (add `httpx==0.28.1` to the pip install line)
- Test: `tests/test_cloudflare_turn.py`

**Interfaces:**
- Produces:
  - `class CloudflareTurnError(RuntimeError)`
  - `async def fetch_turn_server(key_id: str, api_token: str, *, ttl: int = 3600, client: httpx.AsyncClient) -> dict` — returns exactly `{"urls": [...], "username": str, "credential": str}`.
  - `CLOUDFLARE_TURN_URL = "https://rtc.live.cloudflare.com/v1/turn/keys/{key_id}/credentials/generate-ice-servers"`

Request: `POST`, header `Authorization: Bearer <api_token>`, JSON `{"ttl": ttl}`; success is HTTP 201 (accept 200 too) with body `{"iceServers": [ ... ]}` (a list of server objects; verify current shape against Cloudflare's "Generate credentials" doc page while implementing and adjust the parser, not the return contract). Pick the first entry containing `turn:`/`turns:` URLs plus `username`/`credential`; keep only URLs that pass `remote_protocol.validate_ice_url` and do not use port `53`; cap at 16 URLs. Any HTTP error, timeout, non-JSON, missing TURN entry or zero remaining URLs → `CloudflareTurnError` with a message that never contains the token or credential. Validate `key_id` matches `[A-Za-z0-9]{1,64}` before building the URL.

- [ ] **Step 1: Write failing tests** using `httpx.MockTransport`:
  - `test_requests_credentials_with_bearer_token_and_ttl` — asserts method POST, URL with key id, `Authorization` header, JSON body `{"ttl": 3600}`.
  - `test_response_is_sanitized_to_valid_turn_server` — response contains a STUN entry, a TURN entry with `:53` URLs, `turns:turn.cloudflare.com:443?transport=tcp`, and an extra key; result has only the TURN URLs without `:53`, keys exactly `{"urls","username","credential"}`, and `SessionIceBundle(ice_servers=[result], expires_at=1, renew_after=3300, relay_available=True)` validates.
  - `test_http_errors_and_bad_bodies_raise_without_leaking_token` — parametrize 401, 500, `"not json"`, `{"iceServers": []}`; each raises `CloudflareTurnError` and `"secret-token"` not in `str(exc)`.
  - `test_invalid_key_id_is_rejected_before_request` — `key_id="../x"` raises without calling the transport.
- [ ] **Step 2: Run, expect FAIL** — `uv run pytest tests/test_cloudflare_turn.py -v` → `ModuleNotFoundError: broker.cloudflare_turn`.
- [ ] **Step 3: Implement** per the Interfaces block.
- [ ] **Step 4: Run, expect PASS** — same command.
- [ ] **Step 5: Commit** — `feat(broker): fetch relay credentials from Cloudflare TURN`

---

### Task 2: Relay provider in settings, admission and the media_authorize handler

**Files:**
- Modify: `src/broker/app.py` (`BrokerSettings`, `create_broker_app`, `media_authorize` branch at ~line 377)
- Modify: `src/broker/media.py` (`MediaAdmission.authorize`, `_bundle`, new `wants_relay`)
- Modify: `src/broker/main.py` (`settings_from_environment`)
- Test: `tests/test_broker_cloudflare_relay.py`, `tests/test_remote_deployment.py` (env loading)

**Interfaces:**
- Consumes: `fetch_turn_server`, `CloudflareTurnError` (Task 1); `LicenseStore.is_licensed`.
- Produces:
  - `BrokerSettings.relay_provider: Literal["coturn", "cloudflare"] = "coturn"`; `cloudflare_turn_key_id: str | None = Field(None, repr=False)`; `cloudflare_turn_api_token: str | None = Field(None, repr=False)`; `turn_shared_secret: str = Field("", repr=False)`. Validator: coturn requires non-empty `turn_shared_secret`; cloudflare requires both Cloudflare values and ignores `turn_urls`.
  - `MediaAdmission.wants_relay(installation_id: str) -> bool` — `relay_available and licensed and (provider == "cloudflare" or turn_urls)`.
  - `MediaAdmission.authorize(host, request, relay_servers: dict[str, dict] | None = None) -> MediaBundles` — cloudflare provider: relay iff `relay_servers` has both `"host"` and `"viewer"` and `wants_relay` is still true at commit; bundle = STUN servers + `relay_servers[endpoint]`. coturn provider: unchanged behavior, `relay_servers` ignored.
  - App: `create_broker_app(settings, *, turn_client: httpx.AsyncClient | None = None)`; `app.state.turn_client`.
  - Env (main.py): `REMOTE_RELAY_PROVIDER` (`coturn` default), `REMOTE_CLOUDFLARE_TURN_KEY_ID_FILE`, `REMOTE_CLOUDFLARE_TURN_TOKEN_FILE`; in cloudflare mode `REMOTE_TURN_SECRET_FILE` and `REMOTE_TURN_URLS` are not required.

Handler: when `settings.relay_provider == "cloudflare"` and `media.wants_relay(host.installation_id)`, fetch host and viewer credentials concurrently with `asyncio.wait_for(..., 3)`; any `CloudflareTurnError`/timeout → `relay_servers=None`. Then call `media.authorize(host, request, relay_servers)` exactly as today. Keep `MediaAdmission` free of awaits.

- [ ] **Step 1: Write failing tests** (real broker app via `TestClient`, Cloudflare faked with `httpx.MockTransport` passed as `turn_client`, reuse `register`/`activate`/`select_bundle` helpers from `tests/test_broker_license.py`):
  - `test_licensed_cloudflare_select_returns_distinct_cloudflare_credentials` — two transport calls; viewer bundle has `relay_available True`, `stun:stun.cloudflare.com:3478` server, and a TURN server whose credential differs from the host bundle's.
  - `test_unlicensed_cloudflare_select_never_calls_cloudflare` — transport call count 0, STUN-only.
  - `test_cloudflare_failure_falls_back_to_stun_only` — transport raises `httpx.ConnectTimeout` (and separately returns 503); select reply ok, `relay_available False`.
  - `test_revocation_during_fetch_discards_prefetched_credentials` — transport handler calls `licenses.revoke(key)` before responding; bundle STUN-only.
  - `test_cloudflare_settings_require_key_and_token_and_hide_them` — missing either → `ValidationError`; `repr(settings)` contains neither value; coturn settings without secret → `ValidationError`.
  - In `tests/test_remote_deployment.py`: `test_cloudflare_environment_loads_without_coturn_inputs` — subprocess like the existing entrypoint test with `REMOTE_RELAY_PROVIDER=cloudflare`, the two `_FILE` vars, no TURN secret/URLs → `app.state.media.settings.relay_provider == "cloudflare"`; secret file contents absent from output.
- [ ] **Step 2: Run, expect FAIL** — `uv run pytest tests/test_broker_cloudflare_relay.py tests/test_remote_deployment.py -k "cloudflare" -v`.
- [ ] **Step 3: Implement** per the Interfaces block.
- [ ] **Step 4: Run the broker suite, expect PASS** — `uv run pytest tests/ -k "broker or remote_deployment or cloudflare" -q`.
- [ ] **Step 5: Commit** — `feat(broker): issue Cloudflare TURN credentials to licensed installations`

---

### Task 3: Edge trusts Cloudflare's client address only from Cloudflare

**Files:**
- Create: `infra/shared-remote/cloudflare-realip.conf` (one `set_real_ip_from <cidr>;` per current Cloudflare IPv4 range from `https://www.cloudflare.com/ips-v4`, then `real_ip_header CF-Connecting-IP;` and `real_ip_recursive off;`)
- Modify: `infra/shared-remote/edge.conf.template` (include the file inside `server`), `infra/shared-remote/edge.Dockerfile` (copy it to `/etc/nginx/cloudflare-realip.conf`)
- Create: `scripts/check_cloudflare_ips.py` — `main(argv=None) -> int`: fetches the live list (or reads `--live-file` in tests), compares to the conf's CIDRs, prints the diff, exit 1 on drift.
- Test: `tests/test_remote_deployment.py`, `tests/integration/test_edge_real_ip.py`

**Interfaces:**
- Produces: `cloudflare_cidrs(conf_text: str) -> list[str]` in `scripts/check_cloudflare_ips.py` (reused by Task 6's workflow).

`X-Forwarded-For $remote_addr` stays as is: after `real_ip`, `$remote_addr` is the client for Cloudflare peers and the true peer otherwise. Origin-only-from-Cloudflare is enforced by the security group (Task 5), so the same edge image still serves the coturn stack and its integration test.

- [ ] **Step 1: Write failing tests**
  - `test_edge_trusts_cloudflare_header_only_from_vendored_ranges` (unit): template includes `cloudflare-realip.conf`; conf has `real_ip_header CF-Connecting-IP`, `real_ip_recursive off`, ≥ 10 `set_real_ip_from` lines, each a valid public IPv4 CIDR; Dockerfile copies it.
  - `test_cloudflare_ip_drift_check` (unit): `main(["--live-file", same])` → 0; with one extra CIDR → 1 and the CIDR printed.
  - `test_forged_cf_connecting_ip_from_non_cloudflare_peer_is_ignored` (integration, Docker): run the rendered edge config in `nginx:1.28-alpine` (self-signed cert generated in a temp dir) in front of a stub upstream named `broker` that echoes `X-Forwarded-For`; request `/installations` with `CF-Connecting-IP: 203.0.113.9`; echoed address ≠ `203.0.113.9`.
- [ ] **Step 2: Run, expect FAIL** — `uv run pytest tests/test_remote_deployment.py -k "cloudflare" -v` and `uv run pytest tests/integration/test_edge_real_ip.py -v`.
- [ ] **Step 3: Implement**
- [ ] **Step 4: Run, expect PASS** — same commands.
- [ ] **Step 5: Commit** — `feat(infra): trust Cloudflare client addresses only from Cloudflare`

---

### Task 4: Cloudflare preflight mode and coturn-free compose

**Files:**
- Modify: `infra/shared-remote/configure.py` (`validate_production`, `__main__`)
- Create: `infra/shared-remote/compose.cloudflare.yml`
- Test: `tests/test_remote_deployment.py`

**Interfaces:**
- Consumes: env names from Task 2.
- Produces:
  - `validate_cloudflare(env: Mapping[str, str]) -> None` in `configure.py`; `__main__` dispatches on `REMOTE_RELAY_PROVIDER` (`cloudflare` → `validate_cloudflare` + `write_association`, no `render_turn`).
  - `compose.cloudflare.yml` services `preflight`, `broker`, `edge`; images `${REMOTE_BROKER_IMAGE:?required}` (preflight + broker) and `${REMOTE_EDGE_IMAGE:?required}`; no `build:`, no `turn` service; broker data bind-mounted from `${REMOTE_DATA_DIRECTORY:?required}` to `/data`; secret files `${REMOTE_CLOUDFLARE_TURN_KEY_ID_FILE:?required}` and `${REMOTE_CLOUDFLARE_TURN_TOKEN_FILE:?required}` mounted read-only; broker env `REMOTE_RELAY_PROVIDER: cloudflare`, `REMOTE_STUN_URLS: '["stun:stun.cloudflare.com:3478"]'`, `REMOTE_LICENSE_PATH: /data/licenses.json`, `REMOTE_STORE_PATH: /data/installations.json`; edge publishes `${REMOTE_HTTPS_BIND_ADDRESS:?required}:443:443/tcp`; broker command keeps `--workers 1` and `--forwarded-allow-ips "*"`.

`validate_cloudflare` requires `REMOTE_HTTPS_BIND_ADDRESS` (explicit, not unspecified), `REMOTE_DOMAIN` (same name regex and denied-range DNS check as today; a proxied name resolves to public Cloudflare addresses and passes), `REMOTE_MAX_ACTIVE_STREAMS` (positive int), `REMOTE_CERT_DIRECTORY` containing `edge-fullchain.pem` and `edge-key.pem`, and the two Cloudflare files whose stripped contents match `[A-Za-z0-9_-]{16,256}`.

- [ ] **Step 1: Write failing tests**
  - `test_cloudflare_preflight_accepts_minimal_inputs_and_rejects_bad_ones` — valid env passes with DNS monkeypatched to `104.16.0.1`; each of: missing key file, token `"short"`, DNS → `10.0.0.1`, missing `edge-key.pem`, `REMOTE_HTTPS_BIND_ADDRESS=0.0.0.0` → `ValueError`.
  - `test_cloudflare_compose_runs_only_preflight_broker_edge_from_images` — parse YAML: services exactly `{preflight, broker, edge}`, no `build` keys, every `${...:?required}` listed above present, `"--workers", "1"` present, `/data` bind from `REMOTE_DATA_DIRECTORY`.
  - Existing coturn tests unchanged and passing.
- [ ] **Step 2: Run, expect FAIL** — `uv run pytest tests/test_remote_deployment.py -v`.
- [ ] **Step 3: Implement**
- [ ] **Step 4: Run, expect PASS** — same command.
- [ ] **Step 5: Commit** — `feat(infra): add Cloudflare relay production stack`

---

### Task 5: Terraform state bootstrap and infrastructure

**Files:**
- Create: `infra/aws/bootstrap/main.tf` (S3 state bucket `window-control-tfstate-077174987088`, versioning, SSE, public access block; local state)
- Create: `infra/aws/versions.tf`, `providers.tf`, `variables.tf`, `network.tf`, `instance.tf`, `storage.tf`, `dns.tf`, `ecr.tf`, `iam.tf`, `ssm.tf`, `budget.tf`, `outputs.tf`, `user-data.sh.tftpl`
- Test: `tests/test_aws_terraform.py`

**Interfaces:**
- Produces outputs: `public_ip`, `hostname`, `instance_id`, `broker_repository_url`, `edge_repository_url`, `deploy_role_arn`, `remote_service_url` (= `https://${var.hostname}`). No sensitive outputs.
- Variables: `hostname` (default `emuctrl.nendojo.com`), `cloudflare_zone` (`nendojo.com`), `instance_type` (`t4g.nano`, validation: `t4g.nano` or `t4g.micro`), `budget_email` (`tuananh.ngo@koeeru.com`), `github_repository` (`cys2best/window-control`), `deploy_branches` (`["main", "feat/direct-webrtc-shared-turn"]`), `max_active_streams` (4).
- Instance facts the deploy script relies on: data volume mounted at `/srv/shared-remote/data`; release directory `/srv/shared-remote/releases`; secrets directory `/srv/shared-remote/secrets` (0700); Docker and compose plugin installed; tag `Name = shared-remote`.

Key resources: backend `s3` with `use_lockfile = true`; VPC + one public subnet + IGW; `aws_instance` (AL2023 arm64 via SSM public parameter, IMDSv2 required, `cpu_credits = "standard"`, `lifecycle { ignore_changes = [user_data, ami] }`, instance profile); `aws_eip`; `aws_ebs_volume` 1 GB gp3 encrypted with `prevent_destroy` + attachment; `aws_dlm_lifecycle_policy` daily keep 7 targeting the volume's tag; security group 443 from `data.cloudflare_ip_ranges` IPv4 CIDRs only; Cloudflare zone data source, proxied A record, zone SSL setting `strict`, `tls_private_key` + `tls_cert_request` + `cloudflare_origin_ca_certificate` for the hostname, cert/key into the two Terraform-owned SSM SecureStrings; ECR repos + lifecycle policy; instance role (SSM core, ECR pull on the two repos, `ssm:GetParameter(s)` on `/window-control/shared-remote/*`, `kms:Decrypt` via SSM default key); deploy role (OIDC trust with `token.actions.githubusercontent.com:aud = sts.amazonaws.com` and `sub` in the two refs; ECR push on the two repos + `ecr:GetAuthorizationToken`; `ssm:SendCommand` on the instance ARN and `arn:aws:ssm:ap-southeast-1::document/AWS-RunShellScript`; `ssm:GetCommandInvocation`); budget. `user-data.sh.tftpl`: install docker, compose plugin (pinned version + sha256), enable docker, 1 GB swap, format-if-empty and mount the data volume by its `/dev/disk/by-id/nvme-Amazon_Elastic_Block_Store_vol…` path, create the three directories — no secrets.

Provider resource names differ between Cloudflare provider major versions; `terraform validate` is the authority — adjust names to whatever validates under `~> 5.0`.

- [ ] **Step 1: Write failing contract tests** in `tests/test_aws_terraform.py` reading the `.tf` text:
  - `test_region_instance_and_metadata_hardening` — `ap-southeast-1`, default `t4g.nano`, `http_tokens = "required"`, `cpu_credits = "standard"`, no `key_name`.
  - `test_only_https_from_cloudflare_is_open` — exactly one ingress block, port 443 TCP, CIDRs from the Cloudflare ranges data source; no `0.0.0.0/0` ingress; no 22/80.
  - `test_data_volume_is_protected_encrypted_and_snapshotted` — `prevent_destroy = true`, `encrypted = true`, DLM retain 7.
  - `test_deploy_role_trusts_only_two_refs` — trust `sub` values are exactly the two refs; OIDC provider referenced via `data`, no `resource "aws_iam_openid_connect_provider"`.
  - `test_no_sensitive_values_in_outputs_or_user_data` — outputs contain none of `private_key`, `origin`, `token`, `secret`; template contains no `ssm get-parameter`, no `aws_ssm_parameter` reference.
  - `test_cloudflare_turn_secrets_are_not_terraform_managed` — no `aws_ssm_parameter` resource whose name contains `cloudflare-turn`.
  - `test_terraform_validates` — skip-free: runs `terraform -chdir=infra/aws init -backend=false` then `validate` (and the same for `bootstrap`); fails if terraform is missing.
- [ ] **Step 2: Run, expect FAIL** — `uv run pytest tests/test_aws_terraform.py -v`.
- [ ] **Step 3: Implement** the files.
- [ ] **Step 4: Run, expect PASS** — same command, plus `terraform -chdir=infra/aws fmt -check -recursive` → exit 0.
- [ ] **Step 5: Commit** — `feat(infra): provision the shared remote service on AWS with Terraform`

---

### Task 6: Deploy script, deploy workflow and infra checks

**Files:**
- Create: `infra/aws/files/shared-remote-deploy.sh`
- Create: `.github/workflows/remote-deploy.yml`, `.github/workflows/infra-check.yml`
- Test: `tests/test_aws_deploy.py`

**Interfaces:**
- Consumes: instance facts and outputs (Task 5); `compose.cloudflare.yml` env names (Task 4); `scripts/check_cloudflare_ips.py` (Task 3).
- Produces: `shared-remote-deploy.sh <git-sha> <image-tag> <account-id>` — the SSM command downloads `https://codeload.github.com/cys2best/window-control/tar.gz/<git-sha>` into a temp dir and runs this script from it.

Script, under `set -euo pipefail` and `umask 077`, no `set -x`: validate `git-sha` `^[0-9a-f]{40}$` and `image-tag` `^[0-9a-f]{40}$`, account `^[0-9]{12}$` **before any change**; copy the release's `infra/shared-remote` into `/srv/shared-remote/releases/<git-sha>`; write the two Cloudflare secret files and the origin cert/key (`edge-fullchain.pem`, `edge-key.pem`) from SSM into `/srv/shared-remote/secrets` (0600), reading values through files or stdin, never as arguments; write the operator env file (0600) with the instance's private IP (from IMDSv2), domain, data dir, image URIs; `aws ecr get-login-password | docker login --password-stdin`; `docker compose -p shared-remote --env-file … -f compose.cloudflare.yml pull` then `up -d`; wait for preflight exit 0; health = `curl --resolve <domain>:443:<private-ip> https://<domain>/installations` returns 405 within 60 s; on failure print `docker compose ps` and the last 50 log lines of edge/broker with secret values masked, exit 1.

`remote-deploy.yml`: `on: workflow_dispatch` with input `image_tag` (optional); `permissions: id-token: write, contents: read`; jobs `build` (skipped when `image_tag` given; `runs-on: ubuntu-24.04-arm`; `aws-actions/configure-aws-credentials` with `vars.AWS_DEPLOY_ROLE_ARN`, region `ap-southeast-1`; ECR login; build+push `infra/shared-remote/broker.Dockerfile` and `edge.Dockerfile` with `REMOTE_ORIGIN=https://${{ vars.REMOTE_HOSTNAME }}`, tags `${{ github.sha }}`) and `deploy` (SSM `send-command` with `AWS-RunShellScript` on `vars.REMOTE_INSTANCE_ID`, commands fetching the tarball for `github.sha` and running the script with `image_tag || github.sha`; poll `get-command-invocation` until done; fail the job on non-success). Pin third-party actions to full commit SHAs.

`infra-check.yml`: on pull requests touching `infra/**` and weekly `schedule`; jobs: `terraform fmt -check -recursive` + `init -backend=false` + `validate` for `infra/aws` and `infra/aws/bootstrap`; `python scripts/check_cloudflare_ips.py`.

- [ ] **Step 1: Write failing tests** in `tests/test_aws_deploy.py`:
  - `test_deploy_script_rejects_malformed_revision_before_changes` — run the script with `PATH` pointing to stub `docker`/`aws`/`curl` that record calls into a temp file; args `("main", sha, acct)`, `(sha, "x", acct)`, `(sha, sha, "1")` each exit non-zero and the record file is empty.
  - `test_deploy_script_never_traces_or_passes_secrets_as_arguments` — text has `set -euo pipefail`, `umask 077`, no `set -x`, `--password-stdin`, and no `--with-decryption` result substituted into a command line (`$(aws ssm get-parameter` only appears redirected to a file).
  - `test_deploy_workflow_is_manual_oidc_and_pinned` — YAML: only `workflow_dispatch` trigger, `id-token: write`, region `ap-southeast-1`, every `uses:` pinned to a 40-hex SHA, no `secrets.AWS_` keys, build `runs-on: ubuntu-24.04-arm`.
  - `test_infra_check_runs_fmt_validate_and_ip_drift` — workflow contains the three checks and a `schedule`.
  - `shellcheck infra/aws/files/shared-remote-deploy.sh` inside a test that fails if shellcheck is missing.
- [ ] **Step 2: Run, expect FAIL** — `uv run pytest tests/test_aws_deploy.py -v`.
- [ ] **Step 3: Implement**
- [ ] **Step 4: Run full suite, expect PASS** — `uv run pytest tests/ --ignore=tests/integration -q` and `uv run pytest tests/integration/test_edge_real_ip.py -q`.
- [ ] **Step 5: Commit** — `feat(ci): add manual AWS deploy and infrastructure checks`

---

### Task 7: Runbook

**Files:**
- Create: `infra/aws/README.md`
- Modify: `infra/shared-remote/README.md` (short pointer to the Cloudflare/AWS path and the coturn stack's remaining role)
- Test: `tests/test_aws_deploy.py`

Contents, in order: prerequisites (AWS CLI as `nta`, Terraform ≥ 1.10, Cloudflare API token with DNS edit + SSL/Certificates edit on `nendojo.com`, exported as `CLOUDFLARE_API_TOKEN`); bootstrap state; `terraform init/plan/apply`; create the Cloudflare TURN key and `aws ssm put-parameter --type SecureString` for both values using `file://` input (never inline values); set repo variables `AWS_DEPLOY_ROLE_ARN`, `REMOTE_INSTANCE_ID`, `REMOTE_HOSTNAME`; get the workflow onto `main` (owner action); press Deploy; first license (`aws ssm start-session` → `docker exec shared-remote-broker python -m broker.admin issue --note self`); set `REMOTE_SERVICE_URL=https://emuctrl.nendojo.com` for the Windows app; memory check (`free -m`, `docker stats --no-stream`) with the switch-to-`t4g.micro` rule; Cloudflare billing notification setup; rollback with `image_tag`; restore from snapshot; teardown including deliberate removal of the protected data volume; cost table from the spec; known limit: no hard relay spend cap until the per-license relay-hours limit lands.

- [ ] **Step 1: Write failing test** `test_runbook_covers_first_run_and_never_inlines_secrets` — README contains `use_lockfile`/bootstrap, `put-parameter`, `file://`, `image_tag`, `prevent_destroy`, `t4g.micro`, `broker.admin issue`; no line containing `put-parameter` also contains `--value "` or `--value '`.
- [ ] **Step 2: Run, expect FAIL** — `uv run pytest tests/test_aws_deploy.py -k runbook -v`.
- [ ] **Step 3: Write the README.**
- [ ] **Step 4: Run, expect PASS** — same command, then the full suite `uv run pytest tests/ --ignore=tests/integration -q`.
- [ ] **Step 5: Commit** — `docs(infra): document AWS and Cloudflare deployment runbook`

---

## Owner steps after the plan (not tasks)

1. Review, then approve pushing `.github/workflows/remote-deploy.yml` (and `infra-check.yml`) to `main`.
2. Follow `infra/aws/README.md`: bootstrap, apply, SSM values, repo variables, Deploy, first license, Windows app URL, relay-license manual checklist.
