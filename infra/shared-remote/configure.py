"""Fail-closed production preflight and private coturn config rendering."""
import argparse
import ipaddress
import json
import os
from pathlib import Path
import re
import socket
from string import Template

REQUIRED = (
    'REMOTE_HTTPS_BIND_ADDRESS', 'REMOTE_TURN_BIND_ADDRESS',
    'REMOTE_TURN_RELAY_BIND_ADDRESS', 'REMOTE_TURN_PUBLIC_ADDRESS',
    'REMOTE_DOMAIN', 'REMOTE_TURN_DOMAIN', 'REMOTE_MAX_ACTIVE_STREAMS',
    'REMOTE_TURN_ALLOCATION_BPS', 'REMOTE_TURN_AGGREGATE_BPS',
    'REMOTE_TURN_TOTAL_ALLOCATIONS', 'REMOTE_TURN_SECRET_FILE',
    'REMOTE_CERT_DIRECTORY',
)
TURN_INPUTS = {
    'REMOTE_TURN_BIND_ADDRESS', 'REMOTE_TURN_RELAY_BIND_ADDRESS',
    'REMOTE_TURN_PUBLIC_ADDRESS', 'REMOTE_TURN_DOMAIN',
    'REMOTE_TURN_ALLOCATION_BPS', 'REMOTE_TURN_AGGREGATE_BPS',
    'REMOTE_TURN_TOTAL_ALLOCATIONS', 'REMOTE_TURN_SECRET',
}


def address_in_range(address, value):
    address = ipaddress.ip_address(address)
    low, _, high = value.partition('-')
    low, high = ipaddress.ip_address(low), ipaddress.ip_address(high or low)
    return address.version == low.version and low <= address <= high


def denied_address(address):
    template = Path(__file__).with_name('turnserver.production.conf.template')
    return any(address_in_range(address, line.split('=', 1)[1]) for line in template.read_text().splitlines() if line.startswith('denied-peer-ip='))


def validate_production(env):
    if any(not env.get(key) for key in REQUIRED):
        raise ValueError('Required production settings are missing')
    for key in REQUIRED:
        if '\n' in env[key] or '\r' in env[key]:
            raise ValueError('Invalid production setting')
    binds = [ipaddress.ip_address(env[key]) for key in ('REMOTE_HTTPS_BIND_ADDRESS', 'REMOTE_TURN_BIND_ADDRESS', 'REMOTE_TURN_RELAY_BIND_ADDRESS', 'REMOTE_TURN_PUBLIC_ADDRESS')]
    if binds[0] == binds[1] or any(address.is_unspecified for address in binds):
        raise ValueError('HTTPS and TURN require distinct explicit bind addresses')
    if denied_address(str(binds[3])):
        raise ValueError('TURN public address must be publicly routable')
    for key in ('REMOTE_DOMAIN', 'REMOTE_TURN_DOMAIN'):
        domain = env[key]
        if not re.fullmatch(r'(?=.{1,253}$)[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?', domain):
            raise ValueError('Invalid public DNS name')
        addresses = socket.getaddrinfo(domain, 443, type=socket.SOCK_STREAM)
        if not addresses or any(denied_address(address[4][0]) for address in addresses):
            raise ValueError('Public service DNS resolves to a denied destination')
    for key in ('REMOTE_MAX_ACTIVE_STREAMS', 'REMOTE_TURN_ALLOCATION_BPS', 'REMOTE_TURN_AGGREGATE_BPS', 'REMOTE_TURN_TOTAL_ALLOCATIONS'):
        if not env[key].isdigit() or int(env[key]) <= 0:
            raise ValueError('Explicit positive capacity settings required')
    if int(env['REMOTE_TURN_ALLOCATION_BPS']) < 2000000:
        raise ValueError('Validation profile requires at least 2000000 bytes/s per allocation')
    if int(env['REMOTE_TURN_AGGREGATE_BPS']) < int(env['REMOTE_TURN_ALLOCATION_BPS']):
        raise ValueError('Aggregate capacity is below per-allocation capacity')
    if int(env['REMOTE_TURN_TOTAL_ALLOCATIONS']) < 2 * int(env['REMOTE_MAX_ACTIVE_STREAMS']):
        raise ValueError('Reserve both endpoint allocations per active stream')
    secret = Path(env['REMOTE_TURN_SECRET_FILE']).read_text().strip()
    if not re.fullmatch(r'[A-Za-z0-9_-]{32,256}', secret):
        raise ValueError('TURN secret must be a random 32-256 character file value')
    certs = Path(env['REMOTE_CERT_DIRECTORY'])
    for name in ('edge-fullchain.pem', 'edge-key.pem', 'turn-fullchain.pem', 'turn-key.pem'):
        if not (certs / name).is_file():
            raise ValueError('Required certificate files are missing')
    association = [env.get(key, '') for key in ('REMOTE_IOS_TEAM_ID', 'REMOTE_IOS_APP_ID', 'REMOTE_IOS_ASSOCIATED_DOMAIN')]
    if any(association) and not all(association):
        raise ValueError('iOS association requires team, app and domain together')
    if all(association):
        if not re.fullmatch(r'[A-Z0-9]{10}', association[0]) or not re.fullmatch(r'[A-Za-z0-9.-]+', association[1]) or association[2] != env['REMOTE_DOMAIN']:
            raise ValueError('Invalid iOS association inputs')


def render_turn(env, template, target):
    values = {key: env[key] for key in TURN_INPUTS if key != 'REMOTE_TURN_SECRET'}
    values['REMOTE_TURN_SECRET'] = Path(env['REMOTE_TURN_SECRET_FILE']).read_text().strip()
    if not re.fullmatch(r'[A-Za-z0-9_-]{32,256}', values['REMOTE_TURN_SECRET']):
        raise ValueError('Invalid TURN secret file')
    source = Template(Path(template).read_text())
    if set(source.get_identifiers()) - TURN_INPUTS:
        raise ValueError('Unknown TURN template variable')
    rendered = source.substitute(values)
    for key in ("REMOTE_HTTPS_BIND_ADDRESS", "REMOTE_TURN_BIND_ADDRESS", "REMOTE_TURN_PUBLIC_ADDRESS"):
        rendered += "denied-peer-ip=" + str(ipaddress.ip_address(env[key])) + "\n"
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(target, os.O_CREAT | os.O_TRUNC | os.O_WRONLY, 0o600)
    with os.fdopen(descriptor, 'w') as stream:
        os.fchmod(stream.fileno(), 0o600)
        stream.write(rendered)


def write_association(env, directory):
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / 'apple-app-site-association'
    if not env.get('REMOTE_IOS_TEAM_ID'):
        target.unlink(missing_ok=True)
        print('Universal links unconfigured: operator iOS inputs absent')
        return
    target.write_text(json.dumps({'applinks': {'details': [{'appIDs': [env['REMOTE_IOS_TEAM_ID'] + '.' + env['REMOTE_IOS_APP_ID']], 'components': [{'/': '/pair'}]}]}}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, default=Path('/private/turnserver.conf'))
    parser.add_argument('--association', type=Path, default=Path('/association'))
    args = parser.parse_args()
    try:
        validate_production(os.environ)
        render_turn(os.environ, Path(__file__).with_name('turnserver.production.conf.template'), args.output)
        write_association(os.environ, args.association)
    except (ValueError, OSError, KeyError):
        raise SystemExit('Production preflight failed; check required inputs, DNS, certificate and secret files') from None
