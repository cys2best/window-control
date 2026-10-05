"""Real Linux Docker broker/coturn acceptance, never daemon-skipped GREEN.

The bundled clients check coturn's shipped interface. WireProbe supplies arbitrary
payloads and returns actual DATA-indication bytes (not the input argument).
"""
import base64
from contextlib import ExitStack
from dataclasses import dataclass
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import shutil
import socket
import ssl
import struct
import subprocess
import sys
import tempfile
import time
import uuid

import httpx
import pytest
from websockets.sync.client import connect

ROOT = Path(__file__).resolve().parents[2]
IMAGE = 'coturn/coturn@sha256:71c3c990283385567f11794ee692e3a47b66fd9b0bb39e42afbe776e331dd888'
COOKIE = 0x2112A442


@dataclass(frozen=True)
class AllocationResult:
    allocation_ok: bool
    received: bytes


def attr(kind, value):
    return struct.pack('!HH', kind, len(value)) + value + b'\0' * (-len(value) % 4)


def packet(kind, attributes, transaction=None, key=None):
    transaction = transaction or os.urandom(12)
    header = struct.pack('!HHI12s', kind, len(attributes) + (24 if key else 0), COOKIE, transaction)
    data = header + attributes
    if key:
        data += attr(8, hmac.new(key, data, hashlib.sha1).digest())
    return data


def attributes(data):
    values = {}
    end = 20 + struct.unpack('!H', data[2:4])[0]
    offset = 20
    while offset < end:
        kind, size = struct.unpack('!HH', data[offset:offset + 4])
        values[kind] = data[offset + 4:offset + 4 + size]
        offset += 4 + size + (-size % 4)
    return values


def peer_address(address, port=3480):
    return b'\0\1' + struct.pack('!H', port ^ (COOKIE >> 16)) + bytes(a ^ b for a, b in zip(socket.inet_aton(address), struct.pack('!I', COOKIE)))


class WireProbe:
    def __init__(self, credentials, transport, ca, hostname='localhost', server='127.0.0.1', port=None):
        self.credentials = credentials
        self.transport = transport
        self.sock = socket.socket(type=socket.SOCK_DGRAM if transport == 'udp' else socket.SOCK_STREAM)
        self.sock.settimeout(5)
        if transport == 'tls':
            self.sock = ssl.create_default_context(cafile=str(ca)).wrap_socket(self.sock, server_hostname=hostname)
        self.sock.connect((server, port or (5349 if transport == 'tls' else 3478)))
        self.auth = b''
        self.key = None

    def receive(self):
        if self.transport == 'udp':
            return self.sock.recv(65535)
        def exact(size):
            data = b''
            while len(data) < size:
                chunk = self.sock.recv(size - len(data))
                if not chunk:
                    raise EOFError('TURN stream closed')
                data += chunk
            return data
        header = exact(20)
        return header + exact(struct.unpack('!H', header[2:4])[0])

    def request(self, kind, body):
        data = packet(kind, body + self.auth, key=self.key)
        self.sock.sendall(data)
        response = self.receive()
        assert response[8:20] == data[8:20], 'TURN transaction mismatch'
        return struct.unpack('!H', response[:2])[0], attributes(response)

    def allocate(self):
        kind, challenge = self.request(3, attr(0x19, b'\x11\0\0\0'))
        assert kind == 0x113 and 0x14 in challenge and 0x15 in challenge, 'missing authenticated allocation challenge'
        username = self.credentials['username'].encode()
        realm = challenge[0x14]
        self.auth = attr(6, username) + attr(0x14, realm) + attr(0x15, challenge[0x15])
        self.key = hashlib.md5(username + b':' + realm + b':' + self.credentials['credential'].encode()).digest()
        kind, response = self.request(3, attr(0x19, b'\x11\0\0\0'))
        return kind == 0x103 and 0x16 in response

    def permission(self, address):
        kind, response = self.request(8, attr(0x12, peer_address(address)))
        if kind == 0x118:
            code = response[9]
            return code[2] * 100 + code[3]
        assert kind == 0x108
        return 0

    def transfer(self, payload):
        assert self.permission('127.0.0.2') == 0
        self.sock.sendall(packet(0x16, attr(0x12, peer_address('127.0.0.2')) + attr(0x13, payload)))
        data = self.receive()
        assert struct.unpack('!H', data[:2])[0] == 0x17, 'expected relayed DATA indication'
        return attributes(data)[0x13]

    def close(self):
        self.sock.close()


def wire(op, payload=None):
    return json.dumps(dict(v=1, id=str(uuid.uuid4()), op=op, payload=payload or {}))


class SharedRemoteStack:
    def __init__(self, directory):
        self.directory = directory
        self.project = 'shared-test-' + uuid.uuid4().hex[:12]
        self.command = ['docker', 'compose', '--project-name', self.project, '--env-file', str(directory / 'test.env'), '-f', str(ROOT / 'infra/shared-remote/compose.test.yml')]
        self.ssl = ssl.create_default_context(cafile=str(directory / 'ca.pem'))
        self.http = httpx.Client(base_url='https://localhost:8443', verify=self.ssl, timeout=10)
        self.sockets = ExitStack()
        self.saved = None

    def compose(self, *args, timeout=240):
        result = subprocess.run([*self.command, *args], capture_output=True, text=True, timeout=timeout)
        if result.returncode:
            secret = (self.directory / 'turn-secret').read_text().strip()
            diagnostic = (result.stdout + result.stderr).replace(secret, '[REDACTED]')
            pytest.fail('Docker compose failed: ' + ' '.join(args[:2]) + '\n' + diagnostic[-6000:])
        return result.stdout

    def ready(self):
        deadline = time.monotonic() + 30
        last_status = 'no response'
        while time.monotonic() < deadline:
            try:
                status = self.http.get('/').status_code
                last_status = f'HTTP {status}'
                if status == 200:
                    # A static page can be ready before the upstream ASGI app.
                    broker_status = self.http.get('/installations').status_code
                    last_status = f'broker HTTP {broker_status}'
                    if broker_status == 405:
                        return
            except httpx.HTTPError as exc:
                last_status = type(exc).__name__
            time.sleep(.25)
        # Startup-only diagnostics, before any credential-bearing session traffic.
        secret = (self.directory / 'turn-secret').read_text().strip()
        for arguments in [('ps', '--all'), ('logs', '--no-color', '--tail', '40', 'edge', 'broker', 'turn', 'turn-production')]:
            result = subprocess.run([*self.command, *arguments], capture_output=True, text=True, timeout=15)
            print((result.stdout + result.stderr).replace(secret, '[REDACTED]')[-8000:])
        pytest.fail('HTTPS stack did not become ready: ' + last_status)

    def socket(self):
        try:
            return self.sockets.enter_context(connect('wss://localhost:8443/connect', ssl=self.ssl, origin='https://localhost:8443', open_timeout=5, close_timeout=2))
        except (TimeoutError, OSError):
            result = subprocess.run([*self.command, 'logs', '--no-color', '--tail', '50', 'edge', 'broker'], capture_output=True, text=True, timeout=15)
            secret = (self.directory / 'turn-secret').read_text().strip()
            print((result.stdout + result.stderr).replace(secret, '[REDACTED]')[-8000:])
            pytest.fail('WSS opening handshake failed before authentication', pytrace=False)

    def receive(self, ws):
        return json.loads(ws.recv(timeout=5))

    def approve(self, identity=None):
        if identity is None:
            response = self.http.post('/installations')
            status = response.status_code
            content_type = response.headers.get('content-type', '')
            if status != 200 or 'application/json' not in content_type:
                print(f'POST /installations: status={status}; content_type={content_type}; bytes={len(response.content)}')
                result = subprocess.run([*self.command, 'logs', '--no-color', '--tail', '50', 'edge', 'broker'], capture_output=True, text=True, timeout=15)
                secret = (self.directory / 'turn-secret').read_text().strip()
                print((result.stdout + result.stderr).replace(secret, '[REDACTED]')[-8000:])
                pytest.fail('Installation registration failed before credentials were issued', pytrace=False)
            identity = response.json()
        assert 'credential' in identity
        host = self.socket()
        host.send(wire('host_auth', identity))
        assert self.receive(host)['ok']
        viewer = self.socket()
        token = uuid.uuid4().hex
        viewer.send(wire('viewer_auth', dict(installation_id=identity['installation_id'], token=token)))
        request = self.receive(host)
        assert request['op'] == 'viewer_auth'
        host.send(json.dumps(dict(v=1, id=request['id'], ok=True, result=dict(device_id='test-device'))))
        assert self.receive(viewer)['ok']
        self.saved = identity, token
        return host, viewer

    def select(self, host, viewer):
        viewer.send(wire('select', {'serial': 'emulator-5554'}))
        routed = self.receive(host)
        assert routed['op'] == 'select'
        session = str(uuid.uuid4())
        host.send(wire('media_authorize', dict(routing_id=routed['id'], session_id=session, generation=1)))
        admission = self.receive(host)
        if not admission['ok']:
            host.send(json.dumps(dict(v=1, id=routed['id'], ok=False, error=admission['error'])))
            return self.receive(viewer)
        result = dict(ok=True, id='emulator-5554', serial='emulator-5554', name='test', w=1920, h=1080, tier='1080p', session_id=session, generation=1)
        host.send(json.dumps(dict(v=1, id=routed['id'], ok=True, result=result)))
        reply = self.receive(viewer)
        assert reply['ok']
        # Mark negotiation established so a slow client probe cannot expire setup.
        viewer.send(wire('negotiate', dict(session_id=session, generation=1, offer='v=0', timeout_ms=10000)))
        routed = self.receive(host)
        host.send(json.dumps(dict(v=1, id=routed['id'], ok=True, result=dict(session_id=session, generation=1, answer='v=0'))))
        assert self.receive(viewer)['ok']
        return reply

    def approved_credentials(self):
        reply = self.select(*self.approve())
        return next(server for server in reply['result']['ice_servers'] if 'credential' in server)

    def probe(self, credentials, transport, hostname='localhost'):
        return WireProbe(credentials, transport, self.directory / 'ca.pem', hostname)

    def bundled(self, credentials, transport):
        # Explicit ephemeral endpoint credentials only; signing secret never in argv.
        flags = [] if transport == 'udp' else ['-t']
        if transport == 'tls':
            flags += ['-S', '-E', '/assets/ca.pem']
        cmd = [*self.command, 'exec', '-T', 'turn', 'turnutils_uclient', '-c', '-n', '2', '-m', '1', '-l', '32', '-e', '127.0.0.2', '-r', '3480', '-p', '5349' if transport == 'tls' else '3478', *flags, '-u', credentials['username'], '-w', credentials['credential'], '127.0.0.1']
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        except subprocess.TimeoutExpired:
            pytest.fail('bundled allocation client exceeded its deadline', pytrace=False)
        returncode = result.returncode
        assert returncode == 0, 'bundled allocation client failed'
        received_two = bool(re.search(r'tot_recv_msgs\s*=\s*2\b', result.stdout + result.stderr))
        assert received_two, 'bundled client did not receive two echoes'
        print(f'bundled {transport}: allocation and 2 echoed messages verified')

    def transfer(self, credentials, transport, payload):
        self.bundled(credentials, transport)
        probe = self.probe(credentials, transport)
        try:
            ok = probe.allocate()
            received = probe.transfer(payload) if ok else b''
            print(f'wire {transport}: allocation={ok}; received_bytes={len(received)}; sha256={hashlib.sha256(received).hexdigest()}')
            return AllocationResult(ok, received)
        finally:
            probe.close()

    def allocate(self, credentials, transport):
        probe = self.probe(credentials, transport)
        try:
            return probe.allocate()
        finally:
            probe.close()

    def deny_permission(self, credentials, address):
        probe = self.probe(credentials, 'udp')
        try:
            assert probe.allocate()
            return probe.permission(address) == 403
        finally:
            probe.close()

    def restart_broker(self):
        self.compose('restart', 'broker')
        self.ready()

    def connect_saved_viewer(self):
        identity, token = self.saved
        viewer = self.socket()
        viewer.send(wire('viewer_auth', dict(installation_id=identity['installation_id'], token=token)))
        return viewer


@pytest.fixture
def shared_remote_stack():
    directory = Path(tempfile.mkdtemp(prefix='shared-remote-'))
    stack = None
    try:
        result = subprocess.run([sys.executable, str(ROOT / 'scripts/shared_remote_test_env.py'), '--directory', str(directory)], capture_output=True, timeout=30)
        assert result.returncode == 0, 'missing or failed one-run test environment generator'
        result = subprocess.run(['docker', 'info'], capture_output=True, timeout=20)
        assert result.returncode == 0, 'real Docker daemon required; never skip this integration suite'
        stack = SharedRemoteStack(directory)
        stack.compose('up', '-d', '--build', timeout=600)
        stack.ready()
        print('tested coturn image: ' + IMAGE)
        version = stack.compose('exec', '-T', 'turn', 'turnserver', '--version', timeout=10).strip()
        assert '4.6.3' in version
        print('coturn version: ' + version)
        yield stack
    finally:
        try:
            if stack is not None:
                stack.sockets.close()
                stack.http.close()
                stack.compose('down', '--volumes', '--remove-orphans', timeout=60)
        finally:
            shutil.rmtree(directory)


@pytest.mark.parametrize('transport', ['udp', 'tcp', 'tls'])
def test_approved_turn_allocation_transfers_payload(shared_remote_stack, transport):
    credentials = shared_remote_stack.approved_credentials()
    payload = b'shared-remote-allocation-check'
    result = shared_remote_stack.transfer(credentials, transport, payload)
    assert result.allocation_ok
    assert result.received == payload
    invalid = {**credentials, 'credential': 'wrong-credential'}
    assert not shared_remote_stack.allocate(invalid, transport)


@pytest.mark.parametrize('transport', ['udp', 'tcp', 'tls'])
def test_expired_credentials_fail_real_allocation(shared_remote_stack, transport):
    credentials = shared_remote_stack.approved_credentials()
    username = str(int(time.time()) - 60) + ':' + credentials['username'].split(':', 1)[1]
    secret = (shared_remote_stack.directory / 'turn-secret').read_text().strip()
    password = base64.b64encode(hmac.new(secret.encode(), username.encode(), hashlib.sha1).digest()).decode()
    assert not shared_remote_stack.allocate({**credentials, 'username': username, 'credential': password}, transport)


def test_tls_hostname_and_untrusted_ca_fail(shared_remote_stack):
    credentials = shared_remote_stack.approved_credentials()
    with pytest.raises(ssl.SSLCertVerificationError):
        shared_remote_stack.probe(credentials, 'tls', hostname='wrong.example.com')
    with pytest.raises(ssl.SSLCertVerificationError):
        WireProbe(credentials, 'tls', shared_remote_stack.directory / 'other-ca.pem')


def test_denied_private_loopback_and_metadata_permissions(shared_remote_stack):
    credentials = shared_remote_stack.approved_credentials()
    for address in ('10.0.0.1', '127.0.0.3', '169.254.169.254', '192.168.1.1', '100.100.100.200'):
        assert shared_remote_stack.deny_permission(credentials, address)


def test_aggregate_admission_rejection_keeps_b_alive(shared_remote_stack):
    stack = shared_remote_stack
    host_b, viewer_b = stack.approve()
    reply_b = stack.select(host_b, viewer_b)
    credentials_b = next(server for server in reply_b['result']['ice_servers'] if 'credential' in server)
    rejection = stack.select(*stack.approve())
    assert rejection['error']['code'] == 'quota_exceeded'
    viewer_b.send(wire('instances'))
    routed = stack.receive(host_b)
    host_b.send(json.dumps(dict(v=1, id=routed['id'], ok=True, result={'instances': []})))
    assert stack.receive(viewer_b)['ok']
    assert stack.transfer(credentials_b, 'udp', b'B-still-alive').received == b'B-still-alive'


def test_restart_requires_pc_reauthentication_and_preserves_identity(shared_remote_stack):
    stack = shared_remote_stack
    stack.approved_credentials()
    identity, _ = stack.saved
    stack.restart_broker()
    viewer = stack.connect_saved_viewer()
    assert stack.receive(viewer)['error']['code'] == 'offline'
    host = stack.socket()
    host.send(wire('host_auth', identity))
    assert stack.receive(host)['ok']
    viewer = stack.connect_saved_viewer()
    routed = stack.receive(host)
    assert routed['op'] == 'viewer_auth'
    host.send(json.dumps(dict(v=1, id=routed['id'], ok=True, result={'device_id': 'persisted-pc-device'})))
    assert stack.receive(viewer)['ok']


def test_turn_down_preserves_direct_stun_configuration(shared_remote_stack):
    stack = shared_remote_stack
    stack.compose('stop', 'turn')
    reply = stack.select(*stack.approve())
    assert reply['ok']
    assert any(any(url.startswith('stun:') for url in server['urls']) for server in reply['result']['ice_servers'])
    # Admission/configuration stays usable for direct ICE even if relay is down.
    assert reply['result']['session_id']


def test_production_template_denies_destinations_without_test_exception(shared_remote_stack):
    stack = shared_remote_stack
    credentials = stack.approved_credentials()
    probe = WireProbe(credentials, 'udp', stack.directory / 'ca.pem', server='127.0.0.4', port=3479)
    try:
        assert probe.allocate(), 'production template must permit authenticated allocation'
        for address in ('127.0.0.2', '10.0.0.1', '169.254.169.254', '192.168.1.1', '100.100.100.200'):
            assert probe.permission(address) == 403
    finally:
        probe.close()
