"""Generate disposable local TLS/secret assets without printing credentials."""
import argparse
import json
import os
from pathlib import Path
import secrets
import subprocess


def openssl(directory, *arguments):
    result = subprocess.run(['openssl', *arguments], cwd=directory, capture_output=True, timeout=20)
    if result.returncode:
        raise RuntimeError('Test certificate generation failed')


def generate(directory):
    directory = directory.resolve()
    root = Path(__file__).resolve().parents[1]
    if directory == root or root in directory.parents:
        raise ValueError('Generated assets must be outside the repository')
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    if any(directory.iterdir()):
        raise ValueError('Use an empty one-run test directory')
    directory.chmod(0o700)
    previous = os.umask(0o077)
    try:
        (directory / 'turn-secret').write_text(secrets.token_urlsafe(48))
        for prefix in ('ca', 'other-ca'):
            openssl(directory, 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '2', '-subj', '/CN=Disposable Shared Remote ' + prefix, '-keyout', prefix + '-key.pem', '-out', prefix + '.pem')
        (directory / 'server.ext').write_text('subjectAltName=DNS:localhost,IP:127.0.0.1\nextendedKeyUsage=serverAuth\n')
        openssl(directory, 'req', '-newkey', 'rsa:2048', '-nodes', '-subj', '/CN=localhost', '-keyout', 'turn-key.pem', '-out', 'server.csr')
        openssl(directory, 'x509', '-req', '-in', 'server.csr', '-CA', 'ca.pem', '-CAkey', 'ca-key.pem', '-CAcreateserial', '-days', '2', '-extfile', 'server.ext', '-out', 'turn-fullchain.pem')
        (directory / 'edge-key.pem').write_bytes((directory / 'turn-key.pem').read_bytes())
        (directory / 'edge-fullchain.pem').write_bytes((directory / 'turn-fullchain.pem').read_bytes())
        (directory / 'association').mkdir()
        (directory / 'test.env').write_text('REMOTE_TEST_DIRECTORY=' + str(directory) + '\n')
    finally:
        os.umask(previous)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--directory', type=Path, required=True)
    args = parser.parse_args()
    try:
        generate(args.directory)
    except (ValueError, OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
        raise SystemExit('Unable to generate disposable assets; use an empty directory outside Git') from None
    print('Disposable test assets generated; values withheld')
