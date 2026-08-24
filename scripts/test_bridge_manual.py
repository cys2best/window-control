"""Manual E2E driver for signaling_bridge.relay_one_instance().

Not part of the app's runtime — a standalone script for manually testing
the bridge against the real VPS signaling server and a real local mediamtx
instance, since relay_one_instance() itself is intentionally bare (no CLI
wrapper, no reconnect loop) and only unit-tested against fakes.

Usage (from repo root, with mediamtx already running and the target
instance selected/active):
    $env:PYTHONPATH = "src"
    uv run python scripts/test_bridge_manual.py <instance_name> <whep_port>

Example:
    uv run python scripts/test_bridge_manual.py instance0 8889
"""
import asyncio
import sys

from server.signaling_bridge import relay_one_instance

SIGNALING_URL = "ws://13.214.163.82:8443"


async def main():
    instance_name = sys.argv[1] if len(sys.argv) > 1 else "instance0"
    whep_port = int(sys.argv[2]) if len(sys.argv) > 2 else 8889

    print(f"[bridge] relaying instance={instance_name} whep_port={whep_port} "
          f"via {SIGNALING_URL}, session={instance_name}")
    print("[bridge] waiting for an offer from the browser...")
    try:
        await relay_one_instance(instance_name, SIGNALING_URL, whep_port)
    except ConnectionError as e:
        print(f"[bridge] connection ended: {e}")
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    asyncio.run(main())
