"""Contract checks for the AWS + Cloudflare Terraform; `terraform validate` is the syntax authority."""
from pathlib import Path
import re
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]
AWS = ROOT / 'infra/aws'


def tf(name):
    path = AWS / name
    assert path.is_file(), f'missing {name}'
    return path.read_text()


def all_tf():
    return '\n'.join(path.read_text() for path in sorted(AWS.glob('*.tf')))


def block(text, header):
    """Body of the first `header {` block, brace-matched."""
    start = text.index(header)
    depth, index = 0, text.index('{', start)
    for position in range(index, len(text)):
        depth += {'{': 1, '}': -1}.get(text[position], 0)
        if depth == 0:
            return text[index:position + 1]
    raise AssertionError(f'unbalanced block {header}')


def test_region_instance_and_metadata_hardening():
    text = all_tf()
    assert 'region = "ap-southeast-1"' in tf('providers.tf')
    variable = re.sub(r'\s+', ' ', block(tf('variables.tf'), 'variable "instance_type"'))
    assert 'default = "t4g.nano"' in variable and 't4g.micro' in variable
    instance = block(tf('instance.tf'), 'resource "aws_instance" "shared_remote"')
    assert 'http_tokens = "required"' in re.sub(r'\s+', ' ', instance)
    assert 'cpu_credits = "standard"' in re.sub(r'\s+', ' ', instance)
    assert 'key_name' not in text
    assert 'al2023-ami-kernel-default-arm64' in text
    assert 'ignore_changes = [user_data, ami]' in instance


def test_only_https_from_cloudflare_is_open():
    network = tf('network.tf')
    group = block(network, 'resource "aws_security_group" "shared_remote"')
    assert group.count('ingress {') == 1
    ingress = re.sub(r'\s+', ' ', block(group, 'ingress {'))
    assert 'from_port = 443' in ingress and 'to_port = 443' in ingress and 'protocol = "tcp"' in ingress
    assert 'cidr_blocks = data.cloudflare_ip_ranges.cloudflare.ipv4_cidrs' in ingress
    assert 'aws_security_group_rule' not in all_tf() and 'aws_vpc_security_group_ingress_rule' not in all_tf()
    assert not re.search(r'from_port\s*=\s*(22|80)\b', all_tf())


def test_data_volume_is_protected_encrypted_and_snapshotted():
    storage = re.sub(r'\s+', ' ', tf('storage.tf'))
    volume = re.sub(r'\s+', ' ', block(tf('storage.tf'), 'resource "aws_ebs_volume" "data"'))
    assert 'prevent_destroy = true' in volume and 'encrypted = true' in volume
    assert 'size = 1' in volume and 'type = "gp3"' in volume
    assert 'resource "aws_dlm_lifecycle_policy"' in storage and 'count = 7' in storage


def test_deploy_role_trusts_only_two_refs():
    iam = tf('iam.tf')
    assert 'data "aws_iam_openid_connect_provider" "github"' in iam
    assert 'resource "aws_iam_openid_connect_provider"' not in all_tf()
    assert 'token.actions.githubusercontent.com:aud' in iam and 'sts.amazonaws.com' in iam
    assert 'token.actions.githubusercontent.com:sub' in iam
    assert '"repo:${var.github_repository}:ref:refs/heads/${branch}"' in iam
    variables = re.sub(r'\s+', ' ', tf('variables.tf'))
    assert 'default = ["main", "feat/direct-webrtc-shared-turn"]' in variables
    assert 'default = "cys2best/window-control"' in variables
    assert '*' not in re.sub(r'"[^"]*"', '', block(iam, 'data "aws_iam_policy_document" "deploy_trust"'))


def test_no_sensitive_values_in_outputs_or_user_data():
    outputs = tf('outputs.tf').lower()
    for word in ('private_key', 'origin', 'token', 'secret', 'sensitive'):
        assert word not in outputs
    for name in ('public_ip', 'hostname', 'instance_id', 'broker_repository_url', 'edge_repository_url',
                 'deploy_role_arn', 'remote_service_url'):
        assert f'output "{name}"' in outputs
    user_data = tf('user-data.sh.tftpl')
    assert 'get-parameter' not in user_data and 'aws_ssm_parameter' not in user_data
    assert '/srv/shared-remote/data' in user_data and '/srv/shared-remote/secrets' in user_data
    assert '/srv/shared-remote/releases' in user_data and 'sha256sum' in user_data


def test_cloudflare_turn_secrets_are_not_terraform_managed():
    text = all_tf()
    for match in re.finditer(r'resource "aws_ssm_parameter" "\w+" \{', text):
        assert 'cloudflare-turn' not in block(text[match.start():], match.group(0))
    assert '/window-control/shared-remote/origin-key' in tf('ssm.tf')


def test_proxied_record_strict_tls_and_origin_certificate():
    dns = re.sub(r'\s+', ' ', tf('dns.tf'))
    assert 'proxied = true' in dns
    assert 'value = "strict"' in dns
    assert 'resource "cloudflare_origin_ca_certificate"' in dns


def test_state_backend_uses_bootstrap_bucket_with_lockfile():
    versions = re.sub(r'\s+', ' ', tf('versions.tf'))
    assert 'backend "s3"' in versions and 'use_lockfile = true' in versions
    assert 'bucket = "window-control-tfstate-077174987088"' in versions
    bootstrap = re.sub(r'\s+', ' ', (AWS / 'bootstrap/main.tf').read_text())
    assert 'window-control-tfstate-077174987088' in bootstrap
    assert 'aws_s3_bucket_versioning' in bootstrap and 'aws_s3_bucket_public_access_block' in bootstrap
    assert 'aws_s3_bucket_server_side_encryption_configuration' in bootstrap


@pytest.mark.parametrize('directory', [AWS, AWS / 'bootstrap'], ids=['main', 'bootstrap'])
def test_terraform_validates(directory):
    terraform = shutil.which('terraform')
    assert terraform, 'terraform is required for this check'
    init = subprocess.run([terraform, f'-chdir={directory}', 'init', '-backend=false', '-input=false', '-no-color'],
                          capture_output=True, text=True, timeout=600)
    assert init.returncode == 0, init.stderr + init.stdout
    result = subprocess.run([terraform, f'-chdir={directory}', 'validate', '-no-color'],
                            capture_output=True, text=True, timeout=300)
    assert result.returncode == 0, result.stderr + result.stdout
    fmt = subprocess.run([terraform, f'-chdir={directory}', 'fmt', '-check', '-recursive'],
                         capture_output=True, text=True, timeout=60)
    assert fmt.returncode == 0, fmt.stdout
