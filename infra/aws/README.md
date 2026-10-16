# Shared remote service on AWS + Cloudflare

One `t4g.nano` in `ap-southeast-1` runs the broker and web edge behind Cloudflare's proxy at
`https://emuctrl.nendojo.com`. Relay credentials come from Cloudflare Realtime TURN; no coturn runs here.
Terraform owns the infrastructure, and a manual GitHub Actions button (`Remote deploy`) builds and ships a commit.
Design: `docs/superpowers/specs/2026-10-16-aws-terraform-deploy-design.md`.

```
PC app / iPhone ──HTTPS/WSS──▶ Cloudflare proxy (TLS, Full strict)
                                   │  security group: 443 from Cloudflare IPv4 ranges only
                                   ▼
                     EC2 edge nginx (Origin CA cert) ─▶ broker (licenses + installations on the data volume)
Media: phone ⇄ PC direct; fallback ⇄ turn.cloudflare.com (credentials issued by the broker to licensed PCs)
```

## Prerequisites

- AWS CLI v2 signed in as IAM user `nta` for account `077174987088` (`aws sts get-caller-identity`).
- Terraform ≥ 1.10.
- A Cloudflare API token with **Zone · DNS · Edit** and **Zone · SSL and Certificates · Edit** on `nendojo.com`,
  exported for the shell that runs Terraform only:

  ```sh
  export CLOUDFLARE_API_TOKEN=...   # never written to any file in this repo
  ```

- The GitHub OIDC provider `token.actions.githubusercontent.com` already exists in the account (Terraform only reads it).

## 1. Bootstrap the state bucket (once)

```sh
terraform -chdir=infra/aws/bootstrap init
terraform -chdir=infra/aws/bootstrap apply
```

This creates `window-control-tfstate-077174987088` (versioned, encrypted, public access blocked) with local state.
The main configuration stores its state there with S3's native lock (`use_lockfile = true`); no DynamoDB table.

## 2. Apply the infrastructure

```sh
terraform -chdir=infra/aws init
terraform -chdir=infra/aws plan
terraform -chdir=infra/aws apply
terraform -chdir=infra/aws output
```

Creates the VPC, instance, Elastic IP, encrypted 1 GB data volume (`prevent_destroy`, daily snapshots kept 7 days),
ECR repositories, instance and deploy roles, the US$25 budget, the proxied DNS record, SSL mode Full (strict), and the
Cloudflare Origin CA certificate. The origin certificate and key go into SSM SecureString
`/window-control/shared-remote/origin-cert` and `/origin-key`. The key is therefore in Terraform state, which is private,
encrypted and versioned; it never appears in outputs.

Variables (defaults in `variables.tf`): `hostname`, `cloudflare_zone`, `instance_type`, `budget_email`,
`github_repository`, `deploy_branches`, `max_active_streams`.

## 3. Store the Cloudflare TURN key in SSM

In the Cloudflare dashboard, **Realtime → TURN Server → Create**, and copy the TURN key ID and API token. Write each to a
private temp file and store it with `file://` input so the value never appears in your shell history or process list:

```sh
umask 077
pbpaste > /tmp/cf-turn-key-id      # paste the key ID first
aws ssm put-parameter --region ap-southeast-1 --type SecureString --overwrite --name /window-control/shared-remote/cloudflare-turn-key-id --value file:///tmp/cf-turn-key-id
pbpaste > /tmp/cf-turn-token       # then the API token
aws ssm put-parameter --region ap-southeast-1 --type SecureString --overwrite --name /window-control/shared-remote/cloudflare-turn-api-token --value file:///tmp/cf-turn-token
rm -f /tmp/cf-turn-key-id /tmp/cf-turn-token
```

Terraform never manages these two parameters, so their values never enter Terraform state.

## 4. Repository variables

GitHub → Settings → Secrets and variables → Actions → **Variables** (not secrets; none of these is secret):

| Variable | Value |
|---|---|
| `AWS_DEPLOY_ROLE_ARN` | `terraform output -raw deploy_role_arn` |
| `REMOTE_INSTANCE_ID` | `terraform output -raw instance_id` |
| `REMOTE_HOSTNAME` | `emuctrl.nendojo.com` |

## 5. Get the workflow onto `main` (owner action)

GitHub only shows the **Run workflow** button for workflows on the default branch. After review, push
`.github/workflows/remote-deploy.yml` and `infra-check.yml` to `main`. The deploy role trusts only runs from `main`
and `feat/direct-webrtc-shared-turn`.

## 6. Deploy

Actions → **Remote deploy** → Run workflow, choosing `main` or `feat/direct-webrtc-shared-turn`. The run builds arm64
broker and edge images tagged with the commit SHA, pushes them to ECR, then asks the instance through SSM to fetch that
commit's tarball and run `infra/aws/files/shared-remote-deploy.sh`. The script writes the secret files (0600) from SSM,
pulls the images, starts `compose.cloudflare.yml`, waits for preflight and checks that `/installations` answers 405.

Until the first deploy the instance runs only its bootstrap (Docker, compose plugin, swap, data volume mount).

## 7. First license

```sh
aws ssm start-session --region ap-southeast-1 --target "$(terraform -chdir=infra/aws output -raw instance_id)"
sudo docker exec shared-remote-broker python -m broker.admin issue --note self
```

Copy the printed key and activate it in the EmuCtrl launcher.

## 8. Point the Windows app at the service

Start EmuCtrl with:

```
REMOTE_SERVICE_URL=https://emuctrl.nendojo.com
```

Then check: the web app loads; the PC registers and connects; license activation shows active; a forced-relay stream
uses `turn.cloudflare.com`; an unlicensed PC gets STUN only; the relay-license manual checklist passes.

## 9. Memory check

With preflight finished and broker + edge running, from a session on the instance:

```sh
free -m
sudo docker stats --no-stream
```

If swap use keeps growing or stays well above zero under normal use, set `instance_type = "t4g.micro"` (about
US$4/month more) and apply. The data volume and Elastic IP survive the change.

## 10. Cloudflare billing notification

Cloudflare dashboard → **Notifications → Add → Billing / Usage-based billing**, threshold e.g. 500 GB of Realtime
egress. There is **no hard relay spend cap** during the private test: TURN is free to 1,000 GB/month, then $0.05/GB.
A per-license relay-hours limit must land before the service opens to anyone else.

## Rollback

Run **Remote deploy** again with `image_tag` set to an older commit SHA already in ECR. The build job is skipped and
the instance redeploys those images. ECR keeps the last 10 images per repository.

## Restore the data volume from a snapshot

1. EC2 → Snapshots → pick the `shared-remote-data` snapshot (daily, 7 kept) → **Create volume** in the instance's AZ.
2. Stop the stack (`sudo docker compose -p shared-remote down` from the release directory), detach the current data
   volume, attach the restored one as `/dev/sdf`, `sudo mount /srv/shared-remote/data`.
3. Re-run **Remote deploy**. Then reconcile Terraform: `terraform state rm aws_ebs_volume.data aws_volume_attachment.data`
   and `terraform import aws_ebs_volume.data <new-volume-id>` (plus the attachment) so state matches.

## Teardown

```sh
terraform -chdir=infra/aws destroy
```

stops at the data volume because of `prevent_destroy`. To really delete everything:

1. Copy `installations.json` and `licenses.json` off the instance if they matter.
2. Remove the `lifecycle { prevent_destroy = true }` block from `aws_ebs_volume.data` in `storage.tf`, then destroy again.
3. Delete the two Cloudflare TURN parameters (`aws ssm delete-parameter --name ...`) and revoke the TURN key in Cloudflare.
4. Optionally remove the state bucket: empty it (all versions), remove its `prevent_destroy`, and destroy `bootstrap`.

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

## Keeping Cloudflare ranges current

The edge trusts `CF-Connecting-IP` only from the ranges vendored in `infra/shared-remote/cloudflare-realip.conf`; the
security group reads Cloudflare's live list at apply time. The weekly **Infra check** workflow runs
`scripts/check_cloudflare_ips.py` and fails on drift: update the file, redeploy, and re-apply Terraform.

## Known limits

- No hard relay spend cap until the per-license relay-hours limit lands.
- Single instance, no high availability; licenses stay file-based on the data volume.
- iOS universal links stay unconfigured until team and bundle IDs are set.
