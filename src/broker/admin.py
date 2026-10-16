"""Operator license administration; shares the broker's license file under its lock."""
import argparse
import os
import sys
import time
from datetime import datetime, timezone
from typing import Callable

from broker.license_store import LicenseStore


def _iso(value) -> str:
    if value is None:
        return "-"
    return datetime.fromtimestamp(value, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def main(argv: list[str] | None = None, *, now: Callable[[], float] = time.time) -> int:
    parser = argparse.ArgumentParser(prog="python -m broker.admin")
    parser.add_argument("--path", default=os.environ.get("REMOTE_LICENSE_PATH") or "/data/licenses.json")
    commands = parser.add_subparsers(dest="command", required=True)
    issue = commands.add_parser("issue")
    issue.add_argument("--days", type=int)
    issue.add_argument("--note", default="")
    for name in ("reset", "revoke"):
        commands.add_parser(name).add_argument("key")
    commands.add_parser("list")
    args = parser.parse_args(argv)

    store = LicenseStore(args.path)
    if args.command == "issue":
        print(store.issue(now=now(), days=args.days, note=args.note))
    elif args.command in ("reset", "revoke"):
        if not getattr(store, args.command)(args.key):
            print("unknown key", file=sys.stderr)
            return 1
        print("ok")
    else:
        for view in store.list():
            print(f"{view.digest_prefix} {view.installation_id or '-'} issued={_iso(view.issued_at)}"
                  f" bound={_iso(view.bound_at)} moved={_iso(view.moved_at)} expires={_iso(view.expires_at)}"
                  f" {'revoked' if view.revoked else 'active'} {view.note}".rstrip())
    return 0


if __name__ == "__main__":
    sys.exit(main())
