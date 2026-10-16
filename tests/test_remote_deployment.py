"""Deployment contract checks; real TURN behavior lives in integration/."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
INFRA = ROOT / 'infra/shared-remote'


def load_configure():
    path = INFRA / 'configure.py'
    assert path.is_file(), 'missing deployment preflight and private config renderer'
    spec = importlib.util.spec_from_file_location('remote_configure', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_production_compose_requires_explicit_capacity_and_single_store_owner():
    path = INFRA / 'compose.production.yml'
    assert path.is_file(), 'missing production stack'
    text = path.read_text()
    for name in ('REMOTE_HTTPS_BIND_ADDRESS', 'REMOTE_TURN_BIND_ADDRESS', 'REMOTE_TURN_RELAY_BIND_ADDRESS', 'REMOTE_TURN_PUBLIC_ADDRESS', 'REMOTE_DOMAIN', 'REMOTE_TURN_DOMAIN', 'REMOTE_STUN_URLS', 'REMOTE_MAX_ACTIVE_STREAMS', 'REMOTE_TURN_ALLOCATION_BPS', 'REMOTE_TURN_AGGREGATE_BPS', 'REMOTE_TURN_TOTAL_ALLOCATIONS', 'REMOTE_TURN_SECRET_FILE', 'REMOTE_CERT_DIRECTORY'):
        assert '${' + name + ':?required}' in text
    assert '"--workers", "1"' in text
    assert 'broker-data:/data' in text
    assert 'network_mode: host' in text
    assert 'sha256:71c3c990283385567f11794ee692e3a47b66fd9b0bb39e42afbe776e331dd888' in text
    assert 'static-auth-secret' not in text


def test_production_turn_denies_internal_destinations_without_echo_exception():
    config = load_configure()
    text = (INFRA / 'turnserver.production.conf.template').read_text()
    assert 'allowed-peer-ip' not in text
    assert 'allow-loopback-peers' not in text
    for address in ('0.0.0.0', '10.1.2.3', '127.0.0.2', '169.254.169.254', '172.16.0.1', '192.168.1.1', '100.100.100.200', '224.0.0.1', '::1', '::ffff:127.0.0.1', 'fc00::1', 'fe80::1', 'ff02::1'):
        assert config.denied_address(address)
        assert any(config.address_in_range(address, line.split('=', 1)[1]) for line in text.splitlines() if line.startswith('denied-peer-ip='))
    assert 'use-auth-secret' in text
    assert 'no-auth' not in text
    assert 'user=' not in text
    assert 'no-multicast-peers' in text


def test_production_preflight_rejects_missing_equal_bind_addresses_and_private_dns(monkeypatch, tmp_path):
    config = load_configure()
    with pytest.raises(ValueError):
        config.validate_production({})
    env = config_test_environment(tmp_path)
    monkeypatch.setattr(config.socket, 'getaddrinfo', lambda *a, **kw: [(2, 1, 6, '', ('93.184.216.34', 0))])
    config.validate_production(env)
    for key, value in [('REMOTE_TURN_BIND_ADDRESS', env['REMOTE_HTTPS_BIND_ADDRESS']), ('REMOTE_TURN_ALLOCATION_BPS', '1999999'), ('REMOTE_MAX_ACTIVE_STREAMS', '0')]:
        with pytest.raises(ValueError):
            config.validate_production({**env, key: value})
    monkeypatch.setattr(config.socket, 'getaddrinfo', lambda *a, **kw: [(2, 1, 6, '', ('169.254.169.254', 0))])
    with pytest.raises(ValueError):
        config.validate_production(env)


def config_test_environment(tmp_path):
    secret = tmp_path / 'secret'
    secret.write_text('a' * 64)
    for name in ('edge-fullchain.pem', 'edge-key.pem', 'turn-fullchain.pem', 'turn-key.pem'):
        (tmp_path / name).write_text('operator certificate')
    return dict(REMOTE_HTTPS_BIND_ADDRESS='93.184.216.34', REMOTE_TURN_BIND_ADDRESS='93.184.216.35', REMOTE_TURN_RELAY_BIND_ADDRESS='93.184.216.35', REMOTE_TURN_PUBLIC_ADDRESS='93.184.216.35', REMOTE_DOMAIN='control.example.com', REMOTE_TURN_DOMAIN='turn.example.com', REMOTE_MAX_ACTIVE_STREAMS='2', REMOTE_TURN_ALLOCATION_BPS='2000000', REMOTE_TURN_AGGREGATE_BPS='8000000', REMOTE_TURN_TOTAL_ALLOCATIONS='8', REMOTE_TURN_SECRET_FILE=str(secret), REMOTE_CERT_DIRECTORY=str(tmp_path))


def test_private_renderer_does_not_expand_unknown_variables_or_leak_secret(tmp_path):
    config = load_configure()
    env = config_test_environment(tmp_path)
    target = tmp_path / 'private.conf'
    config.render_turn(env, INFRA / 'turnserver.production.conf.template', target)
    assert target.stat().st_mode & 0o777 == 0o600
    assert 'static-auth-secret=' + 'a' * 64 in target.read_text()
    assert '${' not in target.read_text()
    bad = tmp_path / 'bad.template'
    bad.write_text('${UNAPPROVED}')
    with pytest.raises(ValueError):
        config.render_turn(env, bad, target)


def test_generated_assets_are_private_and_print_no_credentials(tmp_path):
    script = ROOT / 'scripts/shared_remote_test_env.py'
    assert script.is_file(), 'missing disposable test asset generator'
    directory = tmp_path / 'assets'
    result = subprocess.run([sys.executable, str(script), '--directory', str(directory)], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    secret = (directory / 'turn-secret').read_text().strip()
    assert len(secret) >= 32 and secret not in result.stdout + result.stderr
    assert (directory / 'turn-secret').stat().st_mode & 0o777 == 0o600
    assert (directory / 'test.env').is_file()
    assert (directory / 'ca.pem').is_file()


def test_rate_limit_keys_expire_and_full_maps_fail_closed(monkeypatch):
    from broker.limits import BrokerLimits
    now = [1000.0]
    limits = BrokerLimits(lambda: now[0])
    monkeypatch.setattr("broker.limits.MAX_RATE_LIMIT_KEYS", 4)
    for index in range(4):
        assert limits.check_registration(str(index))
        assert limits.check_credential_issuance(str(index))
    assert not limits.check_registration('overflow')
    assert not limits.check_credential_issuance('overflow')
    assert len(limits.buckets) == len(limits.issuances) == 4
    now[0] += 3601
    assert limits.check_registration('fresh')
    assert limits.check_credential_issuance('fresh')
    assert len(limits.buckets) == len(limits.issuances) == 1


def test_entrypoint_uses_explicit_settings_and_fails_closed(tmp_path):
    path = ROOT / 'src/broker/main.py'
    assert path.is_file(), 'missing ASGI production entrypoint'
    env = {**os.environ, 'PYTHONPATH': str(ROOT / 'src')}
    for key in list(env):
        if key.startswith('REMOTE_'):
            del env[key]
    result = subprocess.run([sys.executable, '-c', 'import broker.main'], env=env, capture_output=True, text=True, timeout=10)
    assert result.returncode != 0
    secret = tmp_path / 'secret'
    secret.write_text('secret-value-never-print')
    env.update(REMOTE_ALLOWED_ORIGINS='["https://control.example.com"]', REMOTE_STORE_PATH=str(tmp_path / 'store'), REMOTE_STUN_URLS='["stun:stun.example.com:3478"]', REMOTE_TURN_URLS='["turns:turn.example.com:443?transport=tcp"]', REMOTE_TURN_SECRET_FILE=str(secret), REMOTE_MAX_ACTIVE_STREAMS='2')
    result = subprocess.run([sys.executable, '-c', 'from broker.main import app; assert app.state.media.settings.max_active_streams == 2'], env=env, capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert secret.read_text() not in result.stdout + result.stderr
    env['REMOTE_STORE_PATH'] = ''
    result = subprocess.run([sys.executable, '-c', 'import broker.main'], env=env, capture_output=True, text=True, timeout=10)
    assert result.returncode != 0, 'empty durable store path must prevent launch'


@pytest.mark.parametrize('method,capacity,horizon', [('check_registration', 5, 3600), ('check_pairing', 10, 60), ('check_authenticated_command', 120, 60), ('check_preview', 2, 1), ('check_credential_issuance', 12, 3600)])
def test_rate_limits_preserve_active_keys_and_natural_expiry(monkeypatch, method, capacity, horizon):
    from broker.limits import BrokerLimits
    monkeypatch.setattr('broker.limits.MAX_RATE_LIMIT_KEYS', 1)
    now = [0.0]
    limits = BrokerLimits(lambda: now[0])
    consume = getattr(limits, method)
    assert all(consume('owner') for _ in range(capacity))
    assert not consume('owner')
    assert not consume('other')
    now[0] = horizon - 0.001
    assert not consume('other')
    now[0] = horizon
    assert consume('other')


def test_rate_limit_atomic_registration_across_http_workers():
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from broker.limits import BrokerLimits
    limits = BrokerLimits(lambda: 0.0)
    barrier = Barrier(16)
    def register(_):
        barrier.wait(timeout=5)
        return limits.check_registration('same-ip')
    with ThreadPoolExecutor(max_workers=16) as pool:
        assert sum(pool.map(register, range(16))) == 5


def test_association_requires_complete_operator_values_and_defaults_to_unconfigured(tmp_path, monkeypatch):
    config = load_configure()
    env = config_test_environment(tmp_path)
    monkeypatch.setattr(config.socket, 'getaddrinfo', lambda *a, **kw: [(2, 1, 6, '', ('93.184.216.34', 0))])
    output = tmp_path / 'association'
    config.write_association(env, output)
    assert not (output / 'apple-app-site-association').exists()
    with pytest.raises(ValueError):
        config.validate_production({**env, 'REMOTE_IOS_TEAM_ID': 'ABCDE12345'})
    env.update(REMOTE_IOS_TEAM_ID='ABCDE12345', REMOTE_IOS_APP_ID='com.example.control', REMOTE_IOS_ASSOCIATED_DOMAIN='control.example.com')
    config.validate_production(env)
    config.write_association(env, output)
    assert json.loads((output / 'apple-app-site-association').read_text())['applinks']['details'] == [{'appIDs': ['ABCDE12345.com.example.control'], 'components': [{'/': '/pair'}]}]


def test_license_file_is_durable_and_reachable_through_edge(tmp_path):
    compose = (INFRA / 'compose.production.yml').read_text()
    assert 'REMOTE_LICENSE_PATH: /data/licenses.json' in compose
    edge = (INFRA / 'edge.conf.template').read_text()
    assert '"^/installations(/[a-f0-9]{32}(/license)?)?$"' in edge
    env = {key: value for key, value in os.environ.items() if not key.startswith('REMOTE_')}
    secret = tmp_path / 'secret'
    secret.write_text('secret')
    env.update(PYTHONPATH=str(ROOT / 'src'), REMOTE_ALLOWED_ORIGINS='["https://control.example.com"]',
               REMOTE_STORE_PATH=str(tmp_path / 'store'), REMOTE_STUN_URLS='["stun:stun.example.com:3478"]',
               REMOTE_TURN_URLS='["turns:turn.example.com:443?transport=tcp"]', REMOTE_TURN_SECRET_FILE=str(secret),
               REMOTE_MAX_ACTIVE_STREAMS='2', REMOTE_LICENSE_PATH=str(tmp_path / 'keys.json'))
    check = f'from broker.main import app; assert str(app.state.licenses.path) == {str(tmp_path / "keys.json")!r}'
    result = subprocess.run([sys.executable, '-c', check], env=env, capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr


def test_turn_configs_cap_allocations_per_credential_and_share_port_range():
    for name in ('turnserver.production.conf.template', 'turnserver.test.conf'):
        lines = (INFRA / name).read_text().splitlines()
        for setting in ('user-quota=4', 'min-port=49160', 'max-port=50159'):
            assert setting in lines, (name, setting)
    readme = (INFRA / 'README.md').read_text()
    assert '49160-50159' in readme and '49200' not in readme


def test_cloudflare_environment_loads_without_coturn_inputs(tmp_path):
    env = {key: value for key, value in os.environ.items() if not key.startswith('REMOTE_')}
    key_file, token_file = tmp_path / 'key-id', tmp_path / 'token'
    key_file.write_text('keyid0123456789abcdef\n')
    token_file.write_text('token-secret-never-print-0123\n')
    env.update(PYTHONPATH=str(ROOT / 'src'), REMOTE_ALLOWED_ORIGINS='["https://control.example.com"]',
               REMOTE_STORE_PATH=str(tmp_path / 'store'), REMOTE_STUN_URLS='["stun:stun.cloudflare.com:3478"]',
               REMOTE_MAX_ACTIVE_STREAMS='2', REMOTE_RELAY_PROVIDER='cloudflare',
               REMOTE_CLOUDFLARE_TURN_KEY_ID_FILE=str(key_file), REMOTE_CLOUDFLARE_TURN_TOKEN_FILE=str(token_file))
    check = ('from broker.main import app; s = app.state.media.settings; '
             'assert s.relay_provider == "cloudflare", s.relay_provider; '
             'assert s.cloudflare_turn_key_id == "keyid0123456789abcdef"; print(repr(s))')
    result = subprocess.run([sys.executable, '-c', check], env=env, capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    output = result.stdout + result.stderr
    assert 'token-secret-never-print' not in output and 'keyid0123456789abcdef' not in output


def test_edge_trusts_cloudflare_header_only_from_vendored_ranges():
    import ipaddress
    template = (INFRA / 'edge.conf.template').read_text()
    assert 'include /etc/nginx/cloudflare-realip.conf;' in template
    conf = (INFRA / 'cloudflare-realip.conf').read_text()
    assert 'real_ip_header CF-Connecting-IP;' in conf
    assert 'real_ip_recursive off;' in conf
    cidrs = [line.split()[1].rstrip(';') for line in conf.splitlines() if line.startswith('set_real_ip_from ')]
    assert len(cidrs) >= 10
    for cidr in cidrs:
        network = ipaddress.IPv4Network(cidr)
        assert network.is_global and network.prefixlen >= 8
    dockerfile = (INFRA / 'edge.Dockerfile').read_text()
    assert 'COPY infra/shared-remote/cloudflare-realip.conf /etc/nginx/cloudflare-realip.conf' in dockerfile
    assert '!infra/shared-remote/cloudflare-realip.conf' in (INFRA / 'edge.Dockerfile.dockerignore').read_text()


def test_cloudflare_ip_drift_check(tmp_path, capsys):
    spec = importlib.util.spec_from_file_location('check_cloudflare_ips', ROOT / 'scripts/check_cloudflare_ips.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    vendored = module.cloudflare_cidrs((INFRA / 'cloudflare-realip.conf').read_text())
    live = tmp_path / 'ips-v4'
    live.write_text('\n'.join(vendored) + '\n')
    assert module.main(['--live-file', str(live)]) == 0
    live.write_text('\n'.join(vendored + ['198.51.100.0/24']) + '\n')
    assert module.main(['--live-file', str(live)]) == 1
    assert '198.51.100.0/24' in capsys.readouterr().out


def cloudflare_test_environment(tmp_path):
    (tmp_path / 'key-id').write_text('0123456789abcdef0123456789abcdef\n')
    (tmp_path / 'token').write_text('cloudflare-token-value_0123456789\n')
    for name in ('edge-fullchain.pem', 'edge-key.pem'):
        (tmp_path / name).write_text('origin certificate')
    return dict(REMOTE_HTTPS_BIND_ADDRESS='10.0.1.10', REMOTE_DOMAIN='emuctrl.example.com',
                REMOTE_MAX_ACTIVE_STREAMS='4', REMOTE_CERT_DIRECTORY=str(tmp_path),
                REMOTE_CLOUDFLARE_TURN_KEY_ID_FILE=str(tmp_path / 'key-id'),
                REMOTE_CLOUDFLARE_TURN_TOKEN_FILE=str(tmp_path / 'token'))


def test_cloudflare_preflight_accepts_minimal_inputs_and_rejects_bad_ones(monkeypatch, tmp_path):
    config = load_configure()
    env = cloudflare_test_environment(tmp_path)
    monkeypatch.setattr(config.socket, 'getaddrinfo', lambda *a, **kw: [(2, 1, 6, '', ('104.16.0.1', 0))])
    config.validate_cloudflare(env)
    (tmp_path / 'short').write_text('short')
    for change in ({'REMOTE_CLOUDFLARE_TURN_KEY_ID_FILE': str(tmp_path / 'missing')},
                   {'REMOTE_CLOUDFLARE_TURN_TOKEN_FILE': str(tmp_path / 'short')},
                   {'REMOTE_HTTPS_BIND_ADDRESS': '0.0.0.0'},
                   {'REMOTE_MAX_ACTIVE_STREAMS': '0'},
                   {'REMOTE_DOMAIN': ''}):
        with pytest.raises((ValueError, OSError)):
            config.validate_cloudflare({**env, **change})
    (tmp_path / 'edge-key.pem').unlink()
    with pytest.raises(ValueError):
        config.validate_cloudflare(env)
    (tmp_path / 'edge-key.pem').write_text('origin key')
    monkeypatch.setattr(config.socket, 'getaddrinfo', lambda *a, **kw: [(2, 1, 6, '', ('10.0.0.1', 0))])
    with pytest.raises(ValueError):
        config.validate_cloudflare(env)


def test_cloudflare_preflight_entrypoint_skips_turn_rendering(monkeypatch, tmp_path):
    config = load_configure()
    env = {**cloudflare_test_environment(tmp_path), 'REMOTE_RELAY_PROVIDER': 'cloudflare'}
    monkeypatch.setattr(config.socket, 'getaddrinfo', lambda *a, **kw: [(2, 1, 6, '', ('104.16.0.1', 0))])
    config.run(env, tmp_path / 'private/turnserver.conf', tmp_path / 'association')
    assert not (tmp_path / 'private/turnserver.conf').exists()


def test_cloudflare_compose_runs_only_preflight_broker_edge_from_images():
    import yaml
    text = (INFRA / 'compose.cloudflare.yml').read_text()
    compose = yaml.safe_load(text)
    assert set(compose['services']) == {'preflight', 'broker', 'edge'}
    assert all('build' not in service for service in compose['services'].values())
    for name in ('REMOTE_BROKER_IMAGE', 'REMOTE_EDGE_IMAGE', 'REMOTE_DATA_DIRECTORY', 'REMOTE_CLOUDFLARE_TURN_KEY_ID_FILE',
                 'REMOTE_CLOUDFLARE_TURN_TOKEN_FILE', 'REMOTE_HTTPS_BIND_ADDRESS', 'REMOTE_DOMAIN',
                 'REMOTE_MAX_ACTIVE_STREAMS', 'REMOTE_CERT_DIRECTORY'):
        assert '${' + name + ':?required}' in text
    services = compose['services']
    assert services['preflight']['image'] == services['broker']['image'] == '${REMOTE_BROKER_IMAGE:?required}'
    assert services['edge']['image'] == '${REMOTE_EDGE_IMAGE:?required}'
    broker = services['broker']
    assert '"--workers", "1"' in text and '--forwarded-allow-ips' in broker['command']
    assert '${REMOTE_DATA_DIRECTORY:?required}:/data' in broker['volumes']
    assert broker['environment']['REMOTE_RELAY_PROVIDER'] == 'cloudflare'
    assert broker['environment']['REMOTE_STUN_URLS'] == '["stun:stun.cloudflare.com:3478"]'
    assert broker['environment']['REMOTE_LICENSE_PATH'] == '/data/licenses.json'
    assert broker['environment']['REMOTE_STORE_PATH'] == '/data/installations.json'
    assert 'REMOTE_TURN_URLS' not in broker['environment'] and 'REMOTE_TURN_SECRET_FILE' not in broker['environment']
    for service in ('preflight', 'broker'):
        mounts = services[service]['volumes']
        assert any(m.startswith('${REMOTE_CLOUDFLARE_TURN_KEY_ID_FILE:?required}:') and m.endswith(':ro') for m in mounts)
        assert any(m.startswith('${REMOTE_CLOUDFLARE_TURN_TOKEN_FILE:?required}:') and m.endswith(':ro') for m in mounts)
    assert services['edge']['ports'] == ['${REMOTE_HTTPS_BIND_ADDRESS:?required}:443:443/tcp']
    assert services['preflight']['environment']['REMOTE_RELAY_PROVIDER'] == 'cloudflare'
