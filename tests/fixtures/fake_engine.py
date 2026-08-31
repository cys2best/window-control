"""Fake stand-in for engine.exe used by tests/test_engine_process.py.

Invoked as: python fake_engine.py <instance_name> <scrcpy_port>
Behavior is selected by the FAKE_ENGINE_MODE env var so tests can drive it
through EngineInstance's real subprocess/env plumbing instead of mocking.
"""

import json
import os
import sys
import time

instance_name = sys.argv[1]
scrcpy_port = int(sys.argv[2])
mode = os.environ.get("FAKE_ENGINE_MODE", "ready")


def _ready_record(**overrides) -> dict:
    record = {
        "instance_name": instance_name,
        "pid": os.getpid(),
        "whep_port": 8443,
        "admin_port": 8080,
        "generation": 0,
        "width": 1280,
        "height": 720,
    }
    record.update(overrides)
    return record


def _emit(record: dict):
    print(json.dumps(record), flush=True)


def _idle():
    while True:
        time.sleep(0.05)


if mode == "ready":
    _emit(_ready_record())
    _idle()

elif mode == "noise_then_ready":
    print("engine starting up", flush=True)
    print("still warming up caches...", flush=True)
    _emit(_ready_record())
    _idle()

elif mode == "wrong_instance":
    _emit(_ready_record(instance_name=instance_name + "-not-it"))
    _idle()

elif mode == "invalid_port":
    _emit(_ready_record(whep_port=0))
    _idle()

elif mode == "slow":
    delay = float(os.environ.get("FAKE_ENGINE_DELAY_SECONDS", "0"))
    time.sleep(delay)
    _emit(_ready_record())
    _idle()

elif mode == "crash":
    print("fatal: fake engine crash", file=sys.stderr, flush=True)
    sys.exit(3)

elif mode == "exit_after_ready":
    _emit(_ready_record())
    sys.exit(0)

else:
    print(f"unknown FAKE_ENGINE_MODE: {mode}", file=sys.stderr, flush=True)
    sys.exit(1)
