"""Fail when Cloudflare's published IPv4 ranges differ from the edge's vendored list."""
import argparse
from pathlib import Path
import re
import sys
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
CONF = ROOT / 'infra/shared-remote/cloudflare-realip.conf'
LIVE_URL = 'https://www.cloudflare.com/ips-v4'


def cloudflare_cidrs(conf_text: str) -> list[str]:
    return re.findall(r'^set_real_ip_from\s+([0-9./]+);', conf_text, re.MULTILINE)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live-file', help='read the live list from a file instead of Cloudflare')
    args = parser.parse_args(argv)
    if args.live_file:
        live_text = Path(args.live_file).read_text()
    else:
        request = urllib.request.Request(LIVE_URL, headers={'User-Agent': 'window-control-infra-check'})
        with urllib.request.urlopen(request, timeout=30) as response:
            live_text = response.read().decode()
    live = {line.strip() for line in live_text.splitlines() if line.strip()}
    vendored = set(cloudflare_cidrs(CONF.read_text()))
    if live == vendored:
        print(f'Cloudflare IPv4 ranges match ({len(live)} ranges).')
        return 0
    for cidr in sorted(live - vendored):
        print(f'missing from vendored list: {cidr}')
    for cidr in sorted(vendored - live):
        print(f'no longer published by Cloudflare: {cidr}')
    print(f'Update {CONF.relative_to(ROOT)}, redeploy the edge, and re-apply Terraform.')
    return 1


if __name__ == '__main__':
    sys.exit(main())
