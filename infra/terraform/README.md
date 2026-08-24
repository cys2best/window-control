# infra/terraform/README.md

Provisions the EC2 instance that Task 1 (coturn) and Task 4 (signaling
server) deploy onto. Replaces manually creating a VPS with any provider —
this module is AWS-specific.

## Prerequisites

- [Terraform](https://developer.hashicorp.com/terraform/install) >= 1.5.0
- AWS credentials configured (`aws configure`, or `AWS_ACCESS_KEY_ID` /
  `AWS_SECRET_ACCESS_KEY` env vars)
- An existing EC2 key pair in your target region (create one in the AWS
  Console under EC2 > Key Pairs, or via `aws ec2 create-key-pair`)

## Deploy

    cd infra/terraform
    terraform init
    terraform plan -var="key_pair_name=YOUR_KEY_PAIR_NAME"
    terraform apply -var="key_pair_name=YOUR_KEY_PAIR_NAME"

Confirm with `yes` when prompted. Takes 1-2 minutes to provision.

## After apply

    terraform output instance_public_ip

Use this IP:
- In Task 1's `turnserver.conf`, replacing `<VPS_PUBLIC_IP>`
- Throughout Task 4's README wherever the VPS's public IP is needed
- In Task 9's end-to-end validation (`VPS_IP` placeholders)

SSH in with:

    terraform output ssh_command

## Variables

| Variable | Default | Purpose |
|---|---|---|
| `aws_region` | `ap-southeast-1` | AWS region |
| `instance_type` | `t3.small` | 2 vCPU / 2GB RAM, covers coturn+signaling PoC load per spec sizing |
| `key_pair_name` | *(required)* | Existing EC2 key pair name for SSH |
| `ssh_allowed_cidr` | `0.0.0.0/0` | Restrict to your IP (`x.x.x.x/32`) before any real use — the default is open-to-internet for initial setup convenience only |

## Teardown

    terraform destroy -var="key_pair_name=YOUR_KEY_PAIR_NAME"
