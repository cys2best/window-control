"""Real nginx edge: CF-Connecting-IP is honored only from trusted (Cloudflare) peers."""
from pathlib import Path
import subprocess
import tempfile
import time
import uuid

import httpx

ROOT = Path(__file__).resolve().parents[2]
INFRA = ROOT / 'infra/shared-remote'
NGINX = 'nginx:1.28-alpine'
FORGED = '203.0.113.9'
ECHO = 'server { listen 8000; location / { default_type text/plain; return 200 "$http_x_forwarded_for"; } }\n'


def docker(*args, check=True):
    return subprocess.run(['docker', *args], check=check, capture_output=True, text=True, timeout=120)


def echoed_address(extra_trust: str = '') -> str:
    name = uuid.uuid4().hex[:12]
    network, edge, broker = f'realip-{name}', f'realip-edge-{name}', f'realip-broker-{name}'
    with tempfile.TemporaryDirectory() as directory:
        work = Path(directory)
        (work / 'certs').mkdir()
        subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '1',
                        '-subj', '/CN=edge.test', '-keyout', str(work / 'certs/edge-key.pem'),
                        '-out', str(work / 'certs/edge-fullchain.pem')], check=True, capture_output=True)
        (work / 'realip.conf').write_text((INFRA / 'cloudflare-realip.conf').read_text() + extra_trust)
        (work / 'echo.conf').write_text(ECHO)
        for path in work.rglob('*'):
            path.chmod(0o755 if path.is_dir() else 0o644)
        docker('network', 'create', network)
        try:
            docker('run', '-d', '--name', broker, '--network', network, '--network-alias', 'broker',
                   '-v', f'{work / "echo.conf"}:/etc/nginx/conf.d/default.conf:ro', NGINX)
            docker('run', '-d', '--name', edge, '--network', network, '-p', '127.0.0.1::443',
                   '-e', 'REMOTE_DOMAIN=edge.test', '-e', 'NGINX_ENVSUBST_FILTER=REMOTE_DOMAIN',
                   '-v', f'{INFRA / "edge.conf.template"}:/etc/nginx/templates/default.conf.template:ro',
                   '-v', f'{work / "realip.conf"}:/etc/nginx/cloudflare-realip.conf:ro',
                   '-v', f'{work / "certs"}:/run/certs:ro', NGINX)
            port = docker('port', edge, '443/tcp').stdout.strip().rsplit(':', 1)[1]
            deadline = time.monotonic() + 30
            while True:
                try:
                    response = httpx.get(f'https://127.0.0.1:{port}/installations', verify=False,
                                         headers={'CF-Connecting-IP': FORGED}, timeout=2)
                    if response.status_code == 200:
                        return response.text
                except httpx.HTTPError:
                    pass
                if time.monotonic() > deadline:
                    raise AssertionError(docker('logs', edge, check=False).stderr)
                time.sleep(0.5)
        finally:
            docker('rm', '-f', edge, broker, check=False)
            docker('network', 'rm', network, check=False)


def test_forged_cf_connecting_ip_from_non_cloudflare_peer_is_ignored():
    assert echoed_address() not in ('', FORGED)
    # Control: the same edge does honor the header once the peer is a trusted range.
    assert echoed_address('set_real_ip_from 0.0.0.0/0;\n') == FORGED
