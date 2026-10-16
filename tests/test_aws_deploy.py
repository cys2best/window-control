"""Contract checks for the AWS deploy script, deploy workflow and infrastructure checks."""
import os
from pathlib import Path
import re
import shutil
import subprocess

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'infra/aws/files/shared-remote-deploy.sh'
DEPLOY_WORKFLOW = ROOT / '.github/workflows/remote-deploy.yml'
CHECK_WORKFLOW = ROOT / '.github/workflows/infra-check.yml'
SHA = '0123456789abcdef0123456789abcdef01234567'
ACCOUNT = '077174987088'


def workflow(path):
    assert path.is_file(), f'missing {path.name}'
    data = yaml.safe_load(path.read_text())
    # YAML 1.1 reads the bare key `on` as True.
    data['on'] = data.pop(True, data.get('on'))
    return data


@pytest.mark.parametrize('args', [('main', SHA, ACCOUNT), (SHA, 'x', ACCOUNT), (SHA, SHA, '1'),
                                  (SHA.upper(), SHA, ACCOUNT), (SHA, SHA), (SHA, f'{SHA};id', ACCOUNT)])
def test_deploy_script_rejects_malformed_revision_before_changes(tmp_path, args):
    record = tmp_path / 'calls'
    record.touch()
    stubs = tmp_path / 'bin'
    stubs.mkdir()
    for name in ('docker', 'aws', 'curl', 'install', 'mkdir', 'cp', 'rm', 'tee'):
        stub = stubs / name
        stub.write_text(f'#!/bin/sh\necho "{name} $*" >> "{record}"\n')
        stub.chmod(0o755)
    env = {'PATH': f'{stubs}:/usr/bin:/bin', 'HOME': str(tmp_path)}
    result = subprocess.run(['bash', str(SCRIPT), *args], env=env, capture_output=True, text=True, timeout=30)
    assert result.returncode != 0
    assert record.read_text() == ''


def test_deploy_script_never_traces_or_passes_secrets_as_arguments():
    text = SCRIPT.read_text()
    assert 'set -euo pipefail' in text and 'umask 077' in text
    assert not re.search(r'set -[a-z]*x', text)
    assert '--password-stdin' in text
    for line in text.splitlines():
        if '--with-decryption' in line:
            assert '$(' not in line and '`' not in line, line
            assert re.search(r'>\s*"?\$', line), f'decrypted value must go straight to a file: {line}'
    # Log masking reads secret patterns from a file, never from the command line.
    assert 'grep -v -F -f' in text


def test_deploy_script_uses_instance_facts_and_cloudflare_stack():
    text = SCRIPT.read_text()
    for fact in ('/srv/shared-remote/releases', '/srv/shared-remote/secrets', '/srv/shared-remote/data',
                 'compose.cloudflare.yml', '/window-control/shared-remote/cloudflare-turn-key-id',
                 '/window-control/shared-remote/cloudflare-turn-api-token', '/window-control/shared-remote/origin-cert',
                 '/window-control/shared-remote/origin-key', 'X-aws-ec2-metadata-token', 'tags/instance/remote-hostname',
                 'tags/instance/max-active-streams', '-p shared-remote', '405'):
        assert fact in text, fact
    instance = (ROOT / 'infra/aws/instance.tf').read_text()
    assert re.search(r'instance_metadata_tags\s*=\s*"enabled"', instance)
    assert re.search(r'"remote-hostname"\s*=\s*var\.hostname', instance)


def test_deploy_workflow_is_manual_oidc_and_pinned():
    data = workflow(DEPLOY_WORKFLOW)
    assert set(data['on']) == {'workflow_dispatch'}
    assert set(data['on']['workflow_dispatch']['inputs']) == {'image_tag'}
    assert data['permissions'] == {'id-token': 'write', 'contents': 'read'}
    text = DEPLOY_WORKFLOW.read_text()
    assert 'ap-southeast-1' in text and 'secrets.AWS_' not in text
    assert 'vars.AWS_DEPLOY_ROLE_ARN' in text and 'vars.REMOTE_INSTANCE_ID' in text
    for uses in re.findall(r'uses:\s*(\S+)', text):
        assert re.fullmatch(r'[\w.-]+/[\w./-]+@[0-9a-f]{40}', uses), uses
    assert data['jobs']['build']['runs-on'] == 'ubuntu-24.04-arm'
    assert 'codeload.github.com/cys2best/window-control/tar.gz' in text
    assert 'AWS-RunShellScript' in text and 'get-command-invocation' in text
    assert 'REMOTE_ORIGIN=https://${{ vars.REMOTE_HOSTNAME }}' in text


def test_infra_check_runs_fmt_validate_and_ip_drift():
    data = workflow(CHECK_WORKFLOW)
    assert 'schedule' in data['on'] and 'pull_request' in data['on']
    text = CHECK_WORKFLOW.read_text()
    assert 'fmt -check -recursive' in text and 'validate' in text and 'init -backend=false' in text
    assert 'infra/aws/bootstrap' in text and 'scripts/check_cloudflare_ips.py' in text
    for uses in re.findall(r'uses:\s*(\S+)', text):
        assert re.fullmatch(r'[\w.-]+/[\w./-]+@[0-9a-f]{40}', uses), uses


def test_deploy_script_passes_shellcheck():
    shellcheck = shutil.which('shellcheck')
    assert shellcheck, 'shellcheck is required for this check'
    result = subprocess.run([shellcheck, str(SCRIPT)], capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout


def test_runbook_covers_first_run_and_never_inlines_secrets():
    readme = ROOT / 'infra/aws/README.md'
    assert readme.is_file(), 'missing AWS runbook'
    text = readme.read_text()
    for needle in ('use_lockfile', 'bootstrap', 'put-parameter', 'file://', 'image_tag', 'prevent_destroy',
                   't4g.micro', 'broker.admin issue', 'AWS_DEPLOY_ROLE_ARN', 'REMOTE_INSTANCE_ID',
                   'REMOTE_HOSTNAME', 'REMOTE_SERVICE_URL=https://emuctrl.nendojo.com', 'CLOUDFLARE_API_TOKEN'):
        assert needle in text, needle
    for line in text.splitlines():
        if 'put-parameter' in line:
            assert '--value "' not in line and "--value '" not in line, line
    assert 'infra/aws/README.md' in (ROOT / 'infra/shared-remote/README.md').read_text()
