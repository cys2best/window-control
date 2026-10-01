# Local-Only Access With Device Pairing — Implementation Plan (Plan A)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** EmuCtrl answers only local-network and Tailscale peers, a device must be paired once with a code shown on the PC, and every public-access path (Supabase login, HTTP tunnel, TURN, public signaling) is gone from the Python server and the TypeScript clients.

**Architecture:** One pure-ASGI middleware in `src/server/app.py` runs two checks on every request: a source-IP allowlist (`network_gate.py`), then a device-token check (`pairing.py`) that loopback and the pairing shell are exempt from. The launcher owns the pairing code and the device list in-process. Clients store one device token per host and route on a `paired` flag read from `GET /pair/status`.

**Tech Stack:** Python 3.11 / FastAPI / Starlette / PyQt5 / pytest (`uv run`); TypeScript / React / react-native-web / Next.js static export / Expo / Jest.

**Spec:** `docs/superpowers/specs/2026-10-01-local-only-pairing-design.md`

**Out of this plan (Plan B):** engine C++ signaling removal, the cutover verifier scripts (`scripts/verify_*_cutover.py`, `scripts/verify_python_orchestration.py`) and their tests, the CI relay step in `.github/workflows/build.yml`, and `infra/vps/signaling`. Those four depend on each other and are untouched here. `engine.exe` already treats an unset `ENGINE_SIGNALING_URL` as "no signaling" (`engine/src/main.cpp:104`), so this plan can stop sending it.

## Global Constraints

- Python commands run through `uv run` (`uv run pytest`, `uv run python`), never bare `python` / `pytest`.
- Allowed peer ranges, exactly: `127.0.0.0/8`, `10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`, `169.254.0.0/16`, `100.64.0.0/10`, `::1`, `fc00::/7`, `fe80::/10`. IPv4-mapped IPv6 is unwrapped first. A missing or unparseable peer is refused.
- uvicorn keeps `host="0.0.0.0"` and `proxy_headers=False` (`src/main.py`). `X-Forwarded-For` never influences the gate.
- Pairing code: 6 digits from `secrets`, valid 300 seconds, single use, cleared after 5 wrong attempts, compared with `hmac.compare_digest`, in memory only, created only by the launcher.
- Device token: `secrets.token_urlsafe(32)`; only its SHA-256 hex digest is stored; no expiry.
- `POST /pair` returns the same 403 body for "wrong code" and "no active code".
- No HTTP endpoint creates a pairing code or removes a device.
- Commit format: `<type>(optional-scope): imperative description`. No plan names, task numbers, agent names, `Co-Authored-By`, or AI attribution in commit messages.
- New files under `docs/` need `git add -f` (`docs/*` is gitignored).
- Python tests run on macOS against `src/stubs/`; a pass does not confirm Windows behaviour.
- The branch is consistent for running the real app only after Task 9: between Task 3 and Task 9 the server serves `/pair` while the web export still emits `login.html`. Unit tests pass at every commit.

## Review Focus

1. **Peer address is not an IP** (Starlette's `TestClient` default peer is the string `"testclient"`; a Unix socket has no peer). Expected: refused with 403, never treated as loopback. Pinned in Task 1 (`test_everything_else_is_refused`) and Task 3 (`test_default_testclient_peer_is_refused`).
2. **`paired_devices.json` is corrupt, truncated, or its directory is not writable.** Expected: the app still starts, pairing still works for the running process, and nothing raises. Pinned in Task 2 (`test_corrupt_store_file_starts_empty_and_is_rewritten`, `test_unwritable_store_still_pairs_in_memory`).
3. **User types the code the way the launcher shows it** (`123 456`, with a space, or with surrounding whitespace). Expected: accepted. Pinned in Task 2 (`test_code_is_accepted_with_spaces`).
4. **A paired device is removed while its app is open.** Expected: its next API call gets 401 and the client returns to the pairing screen instead of showing an empty list. Pinned in Task 3 (`test_removed_device_is_rejected`) and Task 8 (existing `redirects to ... on 401` tests, updated to `Pair`).
5. **Desktop webview on the PC (loopback, no token).** Expected: goes straight to the instance list, never the pairing screen. Pinned in Task 3 (`test_loopback_needs_no_token`) and Task 9 (`when ready and paired without a token, router.replace("/instances")`).

---

## File Structure

| File | Responsibility |
|---|---|
| `src/server/network_gate.py` (new) | `is_allowed_peer`, `is_loopback_peer`. Pure functions, no I/O. |
| `src/server/pairing.py` (new) | `PairingStore` (code lifecycle, device tokens, persistence), `bearer_token`, `default_store_path`. |
| `src/server/app.py` | `AccessGate` ASGI middleware, `/pair` routes; Supabase gate, tunnel and TURN wiring removed. |
| `src/gui/launcher.py` | "Paired Devices" group; login prompt, account band and relay row removed. |
| `src/main.py` | Builds one `PairingStore`, hands it to `create_app` and `LauncherWindow`. |
| `packages/core/src/api/pairing.ts` (new) | `pairDevice`, `deviceTokenKey`. |
| `packages/core/src/api/hostProbe.ts` | Probes `/pair/status`; reports `paired`. |
| `packages/core/src/api/ServerContext.tsx` | Per-host device token, `paired` flag; Supabase state removed. |
| `packages/ui/src/screens/Pair.tsx` (new, replaces `Login.tsx`) | Pairing screen. |
| `apps/web/src/app/pair/page.tsx` (new, replaces `login/page.tsx`) | Web route for the pairing screen. |

Deleted in this plan: `src/server/{auth,supabase_client,install_identity,http_tunnel,ice_config}.py`, `src/gui/supabase_login.py`, `packages/core/src/api/supabaseAuth.ts`, `packages/core/src/webrtc/signaling.ts`, `packages/ui/src/screens/Login.tsx`, `apps/web/src/app/login/`, their tests, and `infra/{terraform,supabase}`, `infra/vps/{coturn,tunnel}`.

---

## Before Task 1

JavaScript dependencies are not installed on the planning machine (`npm run test:core` fails with `jest: command not found`), so install both toolchains and record a baseline first:

```bash
uv sync && npm install
uv run pytest tests/ apps/desktop/ -q
npm run test:core && npm run test:ui && npm test -w apps/web
```

Expected: everything passes. If something already fails here, report it before starting; do not fix it as part of this plan.

How far this plan's code has been run: the Python in Tasks 1–3 (`network_gate.py`, `pairing.py`, the `app.py` edits and their three test files) was applied to a scratch copy of `src/` and passed (77 + 36 tests). The launcher code in Task 4, the edits in Task 5, and all TypeScript in Tasks 6–9 have not been executed. If a step's code fails as written, fix the code to satisfy the step's tests and the Interfaces block, and say what changed in the task report.

---

### Task 1: Network gate

**Files:**
- Create: `src/server/network_gate.py`
- Test: `tests/test_network_gate.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `is_allowed_peer(host: str | None) -> bool`, `is_loopback_peer(host: str | None) -> bool`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_network_gate.py`:

```python
import pytest

from server.network_gate import is_allowed_peer, is_loopback_peer


@pytest.mark.parametrize("host", [
    "127.0.0.1", "127.8.9.10",
    "10.0.0.5",
    "172.16.0.1", "172.31.255.254",
    "192.168.1.50",
    "169.254.10.10",
    "100.64.0.1", "100.127.255.254",
    "::1",
    "fd7a:115c:a1e0::1",
    "fe80::1", "fe80::1%en0",
    "::ffff:192.168.1.50",
])
def test_private_and_tailscale_peers_are_allowed(host):
    assert is_allowed_peer(host) is True


@pytest.mark.parametrize("host", [
    "8.8.8.8", "203.0.113.9",
    "172.15.255.255", "172.32.0.1",
    "100.63.255.255", "100.128.0.1",
    "192.169.0.1",
    "2001:4860:4860::8888",
    "::ffff:8.8.8.8",
    "", None, "testclient", "not-an-ip", "192.168.1", "192.168.1.50:8080",
])
def test_everything_else_is_refused(host):
    assert is_allowed_peer(host) is False


@pytest.mark.parametrize("host,expected", [
    ("127.0.0.1", True),
    ("::1", True),
    ("::ffff:127.0.0.1", True),
    ("192.168.1.50", False),
    ("100.64.0.1", False),
    ("localhost", False),
    (None, False),
])
def test_loopback_detection(host, expected):
    assert is_loopback_peer(host) is expected
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_network_gate.py -q`
Expected: collection error, `ModuleNotFoundError: No module named 'server.network_gate'`.

- [ ] **Step 3: Write the implementation**

Create `src/server/network_gate.py`:

```python
"""Source-address allowlist: local network and Tailscale peers only."""

import ipaddress

_ALLOWED_NETWORKS = tuple(ipaddress.ip_network(cidr) for cidr in (
    "127.0.0.0/8",
    "10.0.0.0/8",
    "172.16.0.0/12",
    "192.168.0.0/16",
    "169.254.0.0/16",
    "100.64.0.0/10",   # Tailscale (CGNAT range)
    "::1/128",
    "fc00::/7",
    "fe80::/10",
))


def _parse(host: str | None):
    if not host:
        return None
    try:
        address = ipaddress.ip_address(host.split("%", 1)[0])
    except ValueError:
        return None
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        return address.ipv4_mapped
    return address


def is_allowed_peer(host: str | None) -> bool:
    address = _parse(host)
    return address is not None and any(address in network for network in _ALLOWED_NETWORKS)


def is_loopback_peer(host: str | None) -> bool:
    address = _parse(host)
    return address is not None and address.is_loopback
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/test_network_gate.py -q`
Expected: all pass (37 cases).

- [ ] **Step 5: Commit**

```bash
git add src/server/network_gate.py tests/test_network_gate.py
git commit -m "feat(server): add local-network peer allowlist"
```

---

### Task 2: Pairing store

**Files:**
- Create: `src/server/pairing.py`
- Test: `tests/test_pairing.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `CODE_TTL_SECONDS = 300`, `MAX_FAILED_ATTEMPTS = 5`
  - `PairedDevice(id: str, name: str, created_at: float)` (frozen dataclass)
  - `PairingStore(path: str | None = None, clock: Callable[[], float] = time.time)`
    - `start_pairing() -> str` — new 6-digit code, replaces any active one
    - `active_code() -> tuple[str, int] | None` — `(code, seconds_remaining)`
    - `pair(code: str, device_name: str) -> str | None` — device token, or `None`
    - `is_valid_token(token: str | None) -> bool`
    - `list_devices() -> list[PairedDevice]`
    - `remove_device(device_id: str) -> bool`
    - `remove_all() -> None`
  - `bearer_token(authorization: str | None) -> str | None`
  - `default_store_path() -> str`

- [ ] **Step 1: Write the failing test**

Create `tests/test_pairing.py`:

```python
import json

import pytest

from server.pairing import (
    CODE_TTL_SECONDS,
    MAX_FAILED_ATTEMPTS,
    PairingStore,
    bearer_token,
)


class FakeClock:
    def __init__(self, now: float = 1_000.0):
        self.now = now

    def __call__(self) -> float:
        return self.now


def test_no_code_is_active_until_pairing_is_started():
    store = PairingStore()
    assert store.active_code() is None
    assert store.pair("000000", "Phone") is None


def test_start_pairing_returns_a_six_digit_code_with_the_full_ttl():
    store = PairingStore(clock=FakeClock())
    code = store.start_pairing()
    assert len(code) == 6 and code.isdigit()
    assert store.active_code() == (code, CODE_TTL_SECONDS)


def test_correct_code_returns_a_token_and_is_single_use():
    store = PairingStore()
    code = store.start_pairing()
    token = store.pair(code, "Phone")
    assert token and store.is_valid_token(token)
    assert store.active_code() is None
    assert store.pair(code, "Second phone") is None
    assert [d.name for d in store.list_devices()] == ["Phone"]


def test_code_is_accepted_with_spaces():
    store = PairingStore()
    code = store.start_pairing()
    assert store.pair(f"  {code[:3]} {code[3:]} ", "Phone") is not None


def test_code_expires_after_the_ttl():
    clock = FakeClock()
    store = PairingStore(clock=clock)
    code = store.start_pairing()
    clock.now += CODE_TTL_SECONDS - 1
    assert store.active_code() == (code, 1)
    clock.now += 1
    assert store.active_code() is None
    assert store.pair(code, "Phone") is None


def test_code_is_cleared_after_too_many_wrong_attempts():
    store = PairingStore()
    code = store.start_pairing()
    wrong = "000000" if code != "000000" else "111111"
    for _ in range(MAX_FAILED_ATTEMPTS):
        assert store.pair(wrong, "Attacker") is None
    assert store.active_code() is None
    assert store.pair(code, "Phone") is None


def test_one_fewer_wrong_attempt_still_allows_the_right_code():
    store = PairingStore()
    code = store.start_pairing()
    wrong = "000000" if code != "000000" else "111111"
    for _ in range(MAX_FAILED_ATTEMPTS - 1):
        assert store.pair(wrong, "Attacker") is None
    assert store.pair(code, "Phone") is not None


def test_starting_again_replaces_the_code_and_resets_attempts():
    store = PairingStore()
    first = store.start_pairing()
    wrong = "000000" if first != "000000" else "111111"
    for _ in range(MAX_FAILED_ATTEMPTS - 1):
        store.pair(wrong, "Attacker")
    second = store.start_pairing()
    wrong = "000000" if second != "000000" else "111111"
    for _ in range(MAX_FAILED_ATTEMPTS - 1):
        store.pair(wrong, "Attacker")
    assert store.pair(second, "Phone") is not None


@pytest.mark.parametrize("code", [None, 123456, "", "abcdef"])
def test_malformed_codes_are_rejected_without_raising(code):
    store = PairingStore()
    store.start_pairing()
    assert store.pair(code, "Phone") is None


@pytest.mark.parametrize("token", [None, "", "unknown", "x" * 300, 123])
def test_unknown_or_malformed_tokens_are_invalid(token):
    store = PairingStore()
    store.pair(store.start_pairing(), "Phone")
    assert store.is_valid_token(token) is False


def test_only_the_token_hash_is_written_to_disk(tmp_path):
    path = tmp_path / "paired_devices.json"
    store = PairingStore(str(path))
    token = store.pair(store.start_pairing(), "Phone")
    text = path.read_text()
    assert token not in text
    saved = json.loads(text)["devices"][0]
    assert saved["name"] == "Phone"
    assert len(saved["token_sha256"]) == 64


def test_devices_survive_a_restart(tmp_path):
    path = str(tmp_path / "paired_devices.json")
    first = PairingStore(path)
    token = first.pair(first.start_pairing(), "Phone")
    second = PairingStore(path)
    assert second.is_valid_token(token)
    assert [d.name for d in second.list_devices()] == ["Phone"]
    assert second.active_code() is None


def test_store_directory_is_created_on_first_save(tmp_path):
    path = tmp_path / "nested" / "dir" / "paired_devices.json"
    store = PairingStore(str(path))
    store.pair(store.start_pairing(), "Phone")
    assert path.exists()


@pytest.mark.parametrize("content", ["", "{not json", "[]", '{"devices": "nope"}',
                                     '{"devices": [{"id": 1}]}'])
def test_corrupt_store_file_starts_empty_and_is_rewritten(tmp_path, content):
    path = tmp_path / "paired_devices.json"
    path.write_text(content)
    store = PairingStore(str(path))
    assert store.list_devices() == []
    token = store.pair(store.start_pairing(), "Phone")
    assert PairingStore(str(path)).is_valid_token(token)


def test_unwritable_store_still_pairs_in_memory(tmp_path):
    blocker = tmp_path / "blocker"
    blocker.write_text("a file where a directory is needed")
    store = PairingStore(str(blocker / "paired_devices.json"))
    token = store.pair(store.start_pairing(), "Phone")
    assert store.is_valid_token(token)


def test_remove_device_revokes_only_that_device():
    store = PairingStore()
    phone = store.pair(store.start_pairing(), "Phone")
    tablet = store.pair(store.start_pairing(), "Tablet")
    phone_id = store.list_devices()[0].id
    assert store.remove_device(phone_id) is True
    assert store.remove_device(phone_id) is False
    assert store.is_valid_token(phone) is False
    assert store.is_valid_token(tablet) is True


def test_remove_all_revokes_every_device(tmp_path):
    path = str(tmp_path / "paired_devices.json")
    store = PairingStore(path)
    token = store.pair(store.start_pairing(), "Phone")
    store.remove_all()
    assert store.list_devices() == []
    assert store.is_valid_token(token) is False
    assert PairingStore(path).list_devices() == []


@pytest.mark.parametrize("given,expected", [
    ("  Kitchen iPad  ", "Kitchen iPad"),
    ("", "Device"),
    ("   ", "Device"),
    (None, "Device"),
    ("n" * 200, "n" * 64),
])
def test_device_names_are_trimmed_capped_and_defaulted(given, expected):
    store = PairingStore()
    store.pair(store.start_pairing(), given)
    assert store.list_devices()[0].name == expected


@pytest.mark.parametrize("header,expected", [
    ("Bearer abc", "abc"),
    (None, None),
    ("", None),
    ("abc", None),
    ("bearer abc", None),
    ("Bearer ", None),
    ("Bearer  abc", None),
    ("Bearer a b", None),
])
def test_bearer_token_accepts_exactly_one_credential(header, expected):
    assert bearer_token(header) == expected
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_pairing.py -q`
Expected: collection error, `ModuleNotFoundError: No module named 'server.pairing'`.

- [ ] **Step 3: Write the implementation**

Create `src/server/pairing.py`:

```python
"""Device pairing: a short-lived code shown on the PC is exchanged once for
a long-lived device token. Only the token's SHA-256 digest is stored."""

import hashlib
import hmac
import json
import logging
import os
import secrets
import sys
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass
from typing import Callable

log = logging.getLogger(__name__)

CODE_TTL_SECONDS = 300
MAX_FAILED_ATTEMPTS = 5
_MAX_NAME_LENGTH = 64
_MAX_TOKEN_LENGTH = 256
_DEVICE_STRING_FIELDS = ("id", "name", "token_sha256")


@dataclass(frozen=True)
class PairedDevice:
    id: str
    name: str
    created_at: float


def bearer_token(authorization: str | None) -> str | None:
    """Return one exact Bearer credential, rejecting every other form."""
    if not authorization or not authorization.startswith("Bearer "):
        return None
    token = authorization[7:]
    if not token or token.strip() != token or " " in token:
        return None
    return token


def default_store_path() -> str:
    if sys.platform == "win32":
        directory = r"C:\ProgramData\EmuCtrl"
    else:
        directory = os.path.join(os.path.expanduser("~"), ".emuctrl")
    return os.path.join(directory, "paired_devices.json")


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class PairingStore:
    def __init__(self, path: str | None = None,
                 clock: Callable[[], float] = time.time):
        self._path = path
        self._clock = clock
        self._lock = threading.Lock()
        self._code: str | None = None
        self._code_expires_at = 0.0
        self._failed_attempts = 0
        self._devices: list[dict] = self._load()

    # -- pairing code ------------------------------------------------------

    def start_pairing(self) -> str:
        with self._lock:
            self._code = f"{secrets.randbelow(1_000_000):06d}"
            self._code_expires_at = self._clock() + CODE_TTL_SECONDS
            self._failed_attempts = 0
            return self._code

    def active_code(self) -> tuple[str, int] | None:
        with self._lock:
            code = self._active_code_locked()
            if code is None:
                return None
            return code, int(self._code_expires_at - self._clock())

    def pair(self, code: str, device_name: str) -> str | None:
        if not isinstance(code, str):
            return None
        candidate = "".join(code.split())
        with self._lock:
            active = self._active_code_locked()
            if active is None:
                return None
            if not hmac.compare_digest(candidate.encode("utf-8"), active.encode("utf-8")):
                self._failed_attempts += 1
                if self._failed_attempts >= MAX_FAILED_ATTEMPTS:
                    self._code = None
                return None
            self._code = None
            token = secrets.token_urlsafe(32)
            name = (device_name or "").strip()[:_MAX_NAME_LENGTH] or "Device"
            self._devices.append({
                "id": uuid.uuid4().hex[:8],
                "name": name,
                "created_at": self._clock(),
                "token_sha256": _digest(token),
            })
            self._save_locked()
            return token

    # -- device tokens -----------------------------------------------------

    def is_valid_token(self, token: str | None) -> bool:
        if not isinstance(token, str) or not token or len(token) > _MAX_TOKEN_LENGTH:
            return False
        digest = _digest(token)
        with self._lock:
            return any(
                hmac.compare_digest(digest, device["token_sha256"])
                for device in self._devices
            )

    def list_devices(self) -> list[PairedDevice]:
        with self._lock:
            return [
                PairedDevice(d["id"], d["name"], d["created_at"])
                for d in self._devices
            ]

    def remove_device(self, device_id: str) -> bool:
        with self._lock:
            remaining = [d for d in self._devices if d["id"] != device_id]
            if len(remaining) == len(self._devices):
                return False
            self._devices = remaining
            self._save_locked()
            return True

    def remove_all(self) -> None:
        with self._lock:
            self._devices = []
            self._save_locked()

    # -- internals ---------------------------------------------------------

    def _active_code_locked(self) -> str | None:
        if self._code is None:
            return None
        if self._clock() >= self._code_expires_at:
            self._code = None
            return None
        return self._code

    def _load(self) -> list[dict]:
        if not self._path:
            return []
        try:
            with open(self._path, "r", encoding="utf-8") as f:
                devices = json.load(f)["devices"]
        except FileNotFoundError:
            return []
        except Exception:
            log.warning("pairing: %s is unreadable; starting with no paired devices", self._path)
            return []
        if not isinstance(devices, list):
            return []
        return [
            d for d in devices
            if isinstance(d, dict)
            and all(isinstance(d.get(key), str) for key in _DEVICE_STRING_FIELDS)
            and isinstance(d.get("created_at"), (int, float))
        ]

    def _save_locked(self) -> None:
        if not self._path:
            return
        directory = os.path.dirname(self._path)
        tmp_path = None
        try:
            os.makedirs(directory, exist_ok=True)
            fd, tmp_path = tempfile.mkstemp(prefix=".paired_devices.", suffix=".tmp", dir=directory)
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump({"devices": self._devices}, f)
                f.flush()
                os.fsync(f.fileno())
            # Atomic on POSIX and Windows: the file holds the old list or the
            # new one, never a half-written one.
            os.replace(tmp_path, self._path)
            tmp_path = None
        except Exception:
            log.warning("pairing: could not write %s; paired devices will not survive a restart", self._path)
        finally:
            if tmp_path is not None:
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/test_pairing.py -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/server/pairing.py tests/test_pairing.py
git commit -m "feat(server): add device pairing store"
```

---

### Task 3: Swap the Supabase gate for the access gate

One commit replaces the auth gate, adds the pairing routes, and stops the HTTP tunnel. They cannot be separate commits: tunnelled requests reach the app from loopback, which the pairing gate exempts.

**Files:**
- Modify: `src/server/app.py`
- Create: `tests/test_app_pairing.py`
- Modify: `tests/test_app.py`
- Delete: `tests/test_app_auth.py`

**Interfaces:**
- Consumes: `is_allowed_peer`, `is_loopback_peer` (Task 1); `PairingStore`, `bearer_token` (Task 2).
- Produces:
  - `create_app(instance_manager: InstanceManager, pairing: PairingStore | None = None) -> FastAPI`
  - `AccessGate(app, pairing: PairingStore)` — ASGI middleware class in `server.app`
  - `GET /pair` → `pair.html`; `POST /pair` body `{"code": str, "device_name": str}` → `200 {"token": str}` or `403 {"detail": "Invalid or expired pairing code"}`
  - `GET /pair/status` → `{"paired": bool}`
  - Select responses no longer contain `signaling_url` or `public_session`; `ice_servers` is exactly `[{"urls": "stun:<host>:3478"}]`.
  - Removed routes: `GET /login`, `GET /auth/config`.
  - `_PAIRING_EXEMPT_PATHS` (renamed from `_AUTH_EXEMPT_PATHS`).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_app_pairing.py`:

```python
import asyncio
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from server.app import AccessGate, create_app
from server.pairing import PairingStore

LOOPBACK = ("127.0.0.1", 5000)
LAN = ("192.168.1.50", 5000)
TAILNET = ("100.101.102.103", 5000)
PUBLIC = ("203.0.113.9", 5000)


def _make(peer, pairing=None, instances=None, **client_kwargs):
    manager = MagicMock()
    manager.list_instances.return_value = instances or []
    manager.active = None
    pairing = pairing or PairingStore()
    with patch("server.app.get_best_ip", return_value="127.0.0.1"):
        app = create_app(manager, pairing)
    return TestClient(app, client=peer, **client_kwargs), pairing


def _pair(client, pairing, name="Phone"):
    response = client.post("/pair", json={"code": pairing.start_pairing(), "device_name": name})
    assert response.status_code == 200
    return response.json()["token"]


# -- network gate ----------------------------------------------------------

@pytest.mark.parametrize("method,path", [
    ("get", "/"), ("get", "/instances"), ("get", "/pair/status"),
    ("get", "/pair"), ("get", "/does-not-exist"),
])
def test_public_peer_is_refused_everywhere(method, path):
    client, _ = _make(PUBLIC)
    assert getattr(client, method)(path).status_code == 403


def test_public_peer_cannot_pair_even_with_the_right_code():
    client, pairing = _make(PUBLIC)
    response = client.post("/pair", json={"code": pairing.start_pairing(), "device_name": "x"})
    assert response.status_code == 403
    assert pairing.list_devices() == []


def test_default_testclient_peer_is_refused():
    manager = MagicMock()
    with patch("server.app.get_best_ip", return_value="127.0.0.1"):
        app = create_app(manager, PairingStore())
    assert TestClient(app).get("/pair/status").status_code == 403


def test_forwarded_for_header_cannot_change_the_peer():
    public, _ = _make(PUBLIC)
    assert public.get("/instances", headers={"X-Forwarded-For": "127.0.0.1"}).status_code == 403
    lan, _ = _make(LAN)
    assert lan.get("/instances", headers={"X-Forwarded-For": "127.0.0.1"}).status_code == 401


# -- pairing gate ----------------------------------------------------------

@pytest.mark.parametrize("peer", [LAN, TAILNET])
@pytest.mark.parametrize("method,path", [
    ("get", "/instances"),
    ("get", "/windows"),
    ("post", "/instances/emulator-5554/select"),
    ("post", "/instances/emulator-5554/keyframe"),
    ("get", "/instances/emulator-5554/preview"),
    ("get", "/preview"),
])
def test_unpaired_private_peer_gets_401_on_api_routes(peer, method, path):
    client, _ = _make(peer)
    response = getattr(client, method)(path)
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


def test_unpaired_private_peer_can_load_the_pairing_shell(tmp_path):
    import server.app as app_module
    (tmp_path / "pair.html").write_text("<html>pair shell</html>")
    (tmp_path / "index.html").write_text("<html>index shell</html>")
    (tmp_path / "pair.txt").write_bytes(b"0:payload\n")
    (tmp_path / "manifest.json").write_text("{}")
    (tmp_path / "_next" / "static").mkdir(parents=True)
    (tmp_path / "_next" / "static" / "a.js").write_text("console.log(1)")
    with patch.object(app_module, "WEB_BUILD_DIR", str(tmp_path)):
        client, _ = _make(LAN)
        assert client.get("/pair").text == "<html>pair shell</html>"
        assert client.get("/").text == "<html>index shell</html>"
        assert client.get("/pair.txt").status_code == 200
        assert client.get("/manifest.json").status_code == 200
        assert client.get("/_next/static/a.js").status_code == 200


def test_unknown_paths_stay_404_for_an_unpaired_peer():
    client, _ = _make(LAN)
    assert client.get("/does-not-exist").status_code == 404
    assert client.get("/login").status_code == 404
    assert client.get("/auth/config").status_code == 404


def test_loopback_needs_no_token():
    client, _ = _make(LOOPBACK, instances=[{"id": "adb:a", "serial": "a"}])
    assert client.get("/instances").json() == [{"id": "adb:a", "serial": "a"}]
    assert client.get("/pair/status").json() == {"paired": True}


# -- pairing flow ----------------------------------------------------------

def test_pairing_issues_a_token_that_opens_the_api():
    client, pairing = _make(LAN, instances=[{"id": "adb:a", "serial": "a"}])
    assert client.get("/pair/status").json() == {"paired": False}
    token = _pair(client, pairing)
    headers = {"Authorization": f"Bearer {token}"}
    assert client.get("/instances", headers=headers).status_code == 200
    assert client.get(f"/instances?token={token}").status_code == 200
    assert client.get("/pair/status", headers=headers).json() == {"paired": True}
    assert [d.name for d in pairing.list_devices()] == ["Phone"]


def test_wrong_code_and_no_active_code_are_indistinguishable():
    client, pairing = _make(LAN)
    closed = client.post("/pair", json={"code": "123456", "device_name": "x"})
    code = pairing.start_pairing()
    wrong = "000000" if code != "000000" else "111111"
    opened = client.post("/pair", json={"code": wrong, "device_name": "x"})
    assert closed.status_code == opened.status_code == 403
    assert closed.json() == opened.json() == {"detail": "Invalid or expired pairing code"}


def test_a_code_cannot_be_used_twice():
    client, pairing = _make(LAN)
    code = pairing.start_pairing()
    assert client.post("/pair", json={"code": code, "device_name": "a"}).status_code == 200
    assert client.post("/pair", json={"code": code, "device_name": "b"}).status_code == 403


@pytest.mark.parametrize("body", [{}, {"device_name": "x"}, {"code": 123456}, {"code": None}])
def test_malformed_pair_bodies_are_rejected_without_pairing(body):
    client, pairing = _make(LAN)
    pairing.start_pairing()
    assert client.post("/pair", json=body).status_code in (403, 422)
    assert pairing.list_devices() == []


def test_removed_device_is_rejected():
    client, pairing = _make(LAN)
    token = _pair(client, pairing)
    headers = {"Authorization": f"Bearer {token}"}
    assert client.get("/instances", headers=headers).status_code == 200
    pairing.remove_device(pairing.list_devices()[0].id)
    assert client.get("/instances", headers=headers).status_code == 401
    assert client.get("/pair/status", headers=headers).json() == {"paired": False}


def test_garbage_tokens_are_rejected():
    client, _ = _make(LAN)
    for value in ("Bearer nope", "Basic abc", "Bearer ", "nope"):
        assert client.get("/instances", headers={"Authorization": value}).status_code == 401
    assert client.get("/instances?token=nope").status_code == 401


# -- websocket scopes ------------------------------------------------------

def _run_gate(scope, pairing):
    sent, reached = [], []

    async def inner(scope, receive, send):
        reached.append(True)

    async def send(message):
        sent.append(message)

    asyncio.run(AccessGate(inner, pairing)(scope, None, send))
    return sent, reached


def _ws_scope(peer, query=b""):
    return {"type": "websocket", "client": peer, "path": "/ws",
            "headers": [], "query_string": query}


def test_websocket_from_a_public_peer_is_closed_before_accept():
    sent, reached = _run_gate(_ws_scope(PUBLIC), PairingStore())
    assert sent == [{"type": "websocket.close", "code": 1008}]
    assert reached == []


def test_websocket_from_an_unpaired_private_peer_is_closed():
    sent, reached = _run_gate(_ws_scope(LAN), PairingStore())
    assert sent == [{"type": "websocket.close", "code": 1008}]
    assert reached == []


def test_websocket_with_a_valid_token_reaches_the_app():
    pairing = PairingStore()
    token = pairing.pair(pairing.start_pairing(), "Phone")
    sent, reached = _run_gate(_ws_scope(LAN, f"token={token}".encode()), pairing)
    assert sent == [] and reached == [True]


def test_non_network_scopes_pass_through():
    sent, reached = _run_gate({"type": "lifespan"}, PairingStore())
    assert sent == [] and reached == [True]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_app_pairing.py -q`
Expected: collection error, `ImportError: cannot import name 'AccessGate' from 'server.app'`.

- [ ] **Step 3: Rewrite the imports and module-level helpers in `src/server/app.py`**

Replace lines 1–75 (from `import asyncio` through the end of `_is_public_web_asset`) with:

```python
import asyncio
import io
import logging
import os
import re
import struct
import subprocess
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from starlette.requests import HTTPConnection
from starlette.routing import Match

from config import WEB_BUILD_DIR, STUN_PORT, TIER_ORDER
from server import adb_manager
from server.instance_manager import InstanceManager
from server.network_gate import is_allowed_peer, is_loopback_peer
from server.pairing import PairingStore, bearer_token
from server.tailscale import get_best_ip

log = logging.getLogger(__name__)

# Routes an unpaired device may load: the pairing API and the static app
# shell that renders the pairing screen. Each is build output with no user
# data; the protected data lives behind the JSON API routes, which stay
# gated.
_PAIRING_EXEMPT_PATHS = {
    "/", "/pair", "/pair/status", "/stream",
    "/index.txt", "/pair.txt", "/stream.txt", "/instances.txt", "/account.txt",
    "/manifest.json", "/icon-192.png", "/icon-512.png", "/favicon.ico", "/404.html",
}

# apps/web's static export emits one `<route>.txt` file per route. Only
# flat, alphanumeric names exist; the route below refuses anything else so a
# crafted name can never escape WEB_BUILD_DIR through os.path.join (on
# Windows a backslash is a separator too, and `{page}`'s default converter
# allows it).
_RSC_PAYLOAD_NAME = re.compile(r"[A-Za-z0-9_-]+")


def _prefers_html(request: HTTPConnection) -> bool:
    """True when the caller is a browser doing a top-level navigation.

    Browsers send `Accept: text/html,...` for document navigations;
    packages/core's API client and apps/mobile use plain `fetch()` with no
    Accept header at all (default `*/*`), so this cleanly separates "load
    the page" from "give me the JSON list" on the one path that must do
    both. Used by BOTH the access gate and GET /instances, deliberately the
    same single predicate on the same request — if the two ever disagreed,
    an unpaired request could be waved past the gate and then answered with
    real instance data.
    """
    return "text/html" in request.headers.get("accept", "")


def _is_pairing_exempt(scope) -> bool:
    path = scope["path"]
    if path in _PAIRING_EXEMPT_PATHS or path.startswith("/_next/"):
        return True
    # Browser navigation shells have no user data, while API-shaped requests
    # on the shared paths remain protected.
    return (
        scope["method"] == "GET"
        and path in {"/instances", "/account"}
        and _prefers_html(HTTPConnection(scope))
    )


def _request_token(request: HTTPConnection) -> str | None:
    return bearer_token(request.headers.get("authorization")) or request.query_params.get("token")


class AccessGate:
    """Network allowlist, then device pairing.

    Pure ASGI rather than `@app.middleware("http")` so WebSocket scopes are
    covered too: an HTTP-only middleware would let a future WebSocket route
    bypass both checks.
    """

    def __init__(self, app, pairing: PairingStore):
        self.app = app
        self.pairing = pairing

    async def __call__(self, scope, receive, send):
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
        client = scope.get("client")
        host = client[0] if client else None
        if not is_allowed_peer(host):
            await self._reject(scope, receive, send, 403, "Forbidden")
            return
        if not is_loopback_peer(host) and self._needs_token(scope):
            if not self.pairing.is_valid_token(_request_token(HTTPConnection(scope))):
                await self._reject(scope, receive, send, 401, "Not paired")
                return
        await self.app(scope, receive, send)

    def _needs_token(self, scope) -> bool:
        if scope["type"] == "websocket":
            return True
        if _is_pairing_exempt(scope):
            return False
        # Only gate paths that resolve to a registered route, so an unknown
        # path still falls through to the router's normal 404.
        return any(
            route.matches(scope)[0] != Match.NONE
            for route in scope["app"].router.routes
        )

    async def _reject(self, scope, receive, send, status: int, detail: str):
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 1008})
            return
        headers = {"WWW-Authenticate": "Bearer"} if status == 401 else None
        response = JSONResponse({"detail": detail}, status_code=status, headers=headers)
        await response(scope, receive, send)
```

- [ ] **Step 4: Replace `current_user` and `_selection_ice_servers`**

Delete the `current_user` function (old lines 201–205) entirely.

Replace the whole `_selection_ice_servers` function (old lines 214–231) with:

```python
def _selection_ice_servers(host: str) -> list[dict]:
    return [{"urls": f"stun:{_format_host(host)}:{STUN_PORT}"}]
```

Leave `_format_host` as it is.

- [ ] **Step 5: Replace the top of `create_app`**

Replace everything from `def create_app(instance_manager: InstanceManager) -> FastAPI:` through the end of the `_shutdown` handler (old lines 234–332) with:

```python
class PairRequest(BaseModel):
    code: str
    device_name: str = ""


def create_app(instance_manager: InstanceManager,
               pairing: PairingStore | None = None) -> FastAPI:
    import asyncio
    if pairing is None:
        pairing = PairingStore()
    app = FastAPI()
    app.add_middleware(AccessGate, pairing=pairing)

    @app.on_event("startup")
    async def _startup():
        loop = asyncio.get_event_loop()
        loop.set_exception_handler(_make_exception_handler(loop.get_exception_handler()))

        # Discover LDPlayer instances on startup
        import threading
        threading.Thread(target=instance_manager.refresh, daemon=True).start()
```

- [ ] **Step 6: Replace the login and auth-config routes**

Replace:

```python
    @app.get("/login")
    async def login_page():
        return _serve_web_page("login.html")
```

with:

```python
    @app.get("/pair")
    async def pair_page():
        return _serve_web_page("pair.html")

    @app.post("/pair")
    async def pair_device(req: PairRequest):
        token = await asyncio.to_thread(pairing.pair, req.code, req.device_name)
        if token is None:
            raise HTTPException(status_code=403, detail="Invalid or expired pairing code")
        return {"token": token}

    @app.get("/pair/status")
    async def pair_status(request: Request):
        host = request.client.host if request.client else None
        return {
            "paired": is_loopback_peer(host)
            or pairing.is_valid_token(_request_token(request)),
        }
```

Delete the whole `auth_config` route (`@app.get("/auth/config")` and its function).

- [ ] **Step 7: Trim the two select responses**

In `select_instance`, delete these two lines from the returned dict:

```python
            "signaling_url": selection.signaling_url,
            "public_session": selection.public_session,
```

In `select_window` (legacy `/select`), delete this line from the returned dict:

```python
                "signaling_url": selection.signaling_url,
```

- [ ] **Step 8: Confirm nothing removed is still referenced**

Run: `grep -nE "auth\.|install_identity|get_ice_servers|run_tunnel|Supabase|_tunnel_task|PUBLIC_UI_URL|TUNNEL_SECRET|_AUTH_EXEMPT|_is_public_web_asset|current_user" src/server/app.py`
Expected: no output.

- [ ] **Step 9: Update `tests/test_app.py` and delete `tests/test_app_auth.py`**

Run: `git rm -q tests/test_app_auth.py`

In `tests/test_app.py`:

1. Replace `test_instance_select_returns_exact_engine_contract`'s `with` block and assertions (from `with patch("server.app.get_best_ip", return_value="100.64.1.4"), \` to the end of the function) with:

```python
    with patch("server.app.get_best_ip", return_value="100.64.1.4"):
        response = client.post("/instances/emulator-5554/select")
    assert response.status_code == 200
    body = response.json()
    assert set(body) == {
        "ok", "id", "serial", "name", "w", "h", "whep_url",
        "whep_token", "ice_servers", "generation",
    }
    assert body["whep_token"] == "whep-token"
    assert body["ice_servers"] == [{"urls": "stun:100.64.1.4:3478"}]
    manager.select.assert_called_once_with("emulator-5554", "100.64.1.4")
```

2. Delete the whole function `test_instance_select_nulls_disabled_public_capabilities_together`.

3. Replace the whole function `test_instance_select_formats_ipv6_stun_and_removes_duplicate_urls` with:

```python
def test_instance_select_formats_ipv6_stun():
    client, manager = _make_client()
    manager.get.return_value = make_instance()
    manager.select.return_value = EngineSelection(
        whep_url="http://[fd7a:115c:a1e0::1]:51000/whep", whep_token="whep-token",
        signaling_url=None, public_session=None, generation=4,
        width=1280, height=720,
    )
    with patch("server.app.get_best_ip", return_value="fd7a:115c:a1e0::1"):
        response = client.post("/instances/emulator-5554/select")

    assert response.status_code == 200
    assert response.json()["ice_servers"] == [
        {"urls": "stun:[fd7a:115c:a1e0::1]:3478"},
    ]
```

4. In the `test_rsc_payloads_are_served_so_soft_navigation_does_not_hard_reload` parametrize list, change `("/login.txt", "login.txt"),` to `("/pair.txt", "pair.txt"),`.

5. Replace the whole function `test_favicon_and_icon_512_are_auth_exempt` with:

```python
def test_favicon_and_icon_512_are_pairing_exempt():
    import server.app as app_module
    assert "/icon-512.png" in app_module._PAIRING_EXEMPT_PATHS
    assert "/favicon.ico" in app_module._PAIRING_EXEMPT_PATHS
```

(`EngineSelection` still takes `signaling_url` and `public_session` here; Task 5 removes them.)

- [ ] **Step 10: Run the server tests**

Run: `uv run pytest tests/test_app.py tests/test_app_pairing.py tests/test_network_gate.py tests/test_pairing.py -q`
Expected: all pass.

Run: `uv run pytest tests/ apps/desktop/ -q`
Expected: all pass. (`tests/test_http_tunnel.py`, `tests/test_auth.py` and the other soon-to-be-deleted modules still import and pass; they are removed in Task 5.)

- [ ] **Step 11: Commit**

```bash
git add src/server/app.py tests/test_app.py tests/test_app_pairing.py
git commit -m "feat(server): gate requests by network and device pairing"
```

---

### Task 4: Launcher pairing UI and startup wiring

**Files:**
- Modify: `src/gui/launcher.py`
- Modify: `src/main.py`
- Modify: `tests/test_launcher_widget.py`
- Modify: `tests/test_main.py`

**Interfaces:**
- Consumes: `PairingStore`, `default_store_path` (Task 2); `create_app(instance_manager, pairing=...)` (Task 3).
- Produces: `LauncherWindow(parent=None, on_stop_server=None, pairing: PairingStore | None = None)` with widgets `_pair_btn`, `_pair_code_label`, `_device_list`, `_remove_device_btn`, `_unpair_all_btn` and method `_refresh_pairing()`. `gui.launcher.maybe_show_login` no longer exists.

- [ ] **Step 1: Write the failing launcher tests**

In `tests/test_launcher_widget.py`:

1. In `test_launcher_window_dimensions_and_close_event`, replace:

```python
        # Option B layout is ~400px width, ~460px height
        assert 380 <= window.width() <= 420
        assert 440 <= window.height() <= 480
```

with:

```python
        assert 380 <= window.width() <= 420
        assert 540 <= window.height() <= 580
```

2. Replace the whole function `test_launcher_window_status_card_lan_mode` with:

```python
def test_launcher_window_status_card_shows_lan_and_tailscale(qapp, monkeypatch):
    monkeypatch.setattr("gui.launcher.detect_local_ip", lambda: "192.168.1.50")
    monkeypatch.setattr("gui.launcher.detect_tailscale_ip", lambda: "100.80.90.100")
    monkeypatch.setattr("gui.launcher.has_tailscale", lambda: True)

    with patch("gui.launcher.check_for_update"):
        from gui.launcher import LauncherWindow
        window = LauncherWindow()
        assert "192.168.1.50" in window._ip_label.text()
        assert "100.80.90.100" in window._ip_label.text()
        assert hasattr(window, "_streams_label")
        for removed in ("_relay_label", "_account_band", "_sign_in_btn", "_sign_out_btn"):
            assert not hasattr(window, removed)


def test_launcher_has_no_login_prompt():
    import gui.launcher as launcher
    assert not hasattr(launcher, "maybe_show_login")
```

3. Delete the whole functions `test_launcher_window_status_card_account_and_tailscale` and `test_sign_out_clears_device_session_without_changing_server_state`.

4. Append:

```python
def test_pair_device_button_shows_a_code(qapp):
    from server.pairing import PairingStore
    store = PairingStore()
    with patch("gui.launcher.check_for_update"):
        from gui.launcher import LauncherWindow
        window = LauncherWindow(pairing=store)
        assert window._pair_code_label.text() == ""
        assert window._pair_btn.text() == "Pair device"
        assert store.active_code() is None

        window._pair_btn.click()

        code, _remaining = store.active_code()
        assert f"{code[:3]} {code[3:]}" in window._pair_code_label.text()
        assert window._pair_btn.text() == "New code"


def test_code_clears_and_device_appears_once_the_code_is_used(qapp):
    from server.pairing import PairingStore
    store = PairingStore()
    with patch("gui.launcher.check_for_update"):
        from gui.launcher import LauncherWindow
        window = LauncherWindow(pairing=store)
        window._pair_btn.click()
        store.pair(store.active_code()[0], "Phone")

        window._refresh_pairing()

        assert window._pair_code_label.text() == ""
        assert window._pair_btn.text() == "Pair device"
        assert window._device_list.count() == 1
        assert "Phone" in window._device_list.item(0).text()


def test_paired_devices_can_be_removed_one_at_a_time_or_all(qapp):
    from server.pairing import PairingStore
    store = PairingStore()
    store.pair(store.start_pairing(), "Phone")
    store.pair(store.start_pairing(), "Tablet")
    with patch("gui.launcher.check_for_update"):
        from gui.launcher import LauncherWindow
        window = LauncherWindow(pairing=store)
        assert window._device_list.count() == 2

        window._device_list.setCurrentRow(0)
        window._remove_device_btn.click()
        assert [d.name for d in store.list_devices()] == ["Tablet"]
        assert window._device_list.count() == 1

        window._unpair_all_btn.click()
        assert store.list_devices() == []
        assert window._device_list.count() == 0
        assert not window._unpair_all_btn.isEnabled()


def test_remove_with_nothing_selected_is_a_no_op(qapp):
    from server.pairing import PairingStore
    store = PairingStore()
    store.pair(store.start_pairing(), "Phone")
    with patch("gui.launcher.check_for_update"):
        from gui.launcher import LauncherWindow
        window = LauncherWindow(pairing=store)
        window._device_list.setCurrentRow(-1)
        window._remove_device_btn.click()
        assert len(store.list_devices()) == 1
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_launcher_widget.py -q`
Expected: failures — `TypeError: __init__() got an unexpected keyword argument 'pairing'`, height assertion, and `maybe_show_login` still present.

- [ ] **Step 3: Rewrite the launcher's imports, constructor and login helper**

In `src/gui/launcher.py`, replace lines 1–49 (from `# src/gui/launcher.py` through `check_for_update(self._on_update_available)`) with:

```python
# src/gui/launcher.py
import sys
import subprocess
import time
from PyQt5.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QLabel, QGroupBox, QFrame, QListWidget, QListWidgetItem
)
from PyQt5.QtCore import Qt, QTimer, pyqtSignal
from PyQt5.QtGui import QFont

from config import PORT, VERSION
from server.pairing import PairingStore
from server.tailscale import has_tailscale, detect_local_ip, detect_tailscale_ip
from updater import check_for_update
from gui.theme import (
    CANVAS, CYAN, DIM, HAIRLINE, INK, MINT, MUTED, SURFACE,
    SURFACE_RAISED, TANGERINE, DESTRUCTIVE_HOVER, register_fonts,
)


class LauncherWindow(QMainWindow):
    server_start_requested = pyqtSignal()
    server_stop_requested = pyqtSignal()
    window_selected = pyqtSignal(str)

    def __init__(self, parent=None, on_stop_server=None, pairing=None):
        super().__init__(parent)
        self.setWindowTitle(f"EmuCtrl Host v{VERSION}")
        self.setFixedSize(400, 560)
        self._enable_windows_dark_title_bar()
        self._on_stop_server = on_stop_server
        self._pairing = pairing if pairing is not None else PairingStore()
        self._device_ids: list[str] | None = None
        self._active_streams_count = 0
        self._pending_update_version = None
        self._fonts = register_fonts()

        self._setup_ui()
        self._refresh_status()
        # The server thread pairs devices and the code expires on its own, so
        # the group is polled rather than pushed to.
        self._pairing_timer = QTimer(self)
        self._pairing_timer.setInterval(1000)
        self._pairing_timer.timeout.connect(self._refresh_pairing)
        self._pairing_timer.start()
        check_for_update(self._on_update_available)
```

- [ ] **Step 4: Replace the account band with nothing, and the relay row with nothing**

In `_setup_ui`, delete the whole `# --- Account ---` block: from the comment line `# --- Account ---` through `layout.addWidget(self._account_band)` inclusive.

In the same method, delete the relay block: from the `# Divider` comment line that precedes `# 2. VPS Relay` through `group_layout.addLayout(relay_layout)` inclusive. The remaining order inside the status group is: Network, `# Divider`, Active Streams. Renumber the comment `# 3. Active Streams` to `# 2. Active Streams`.

- [ ] **Step 5: Add the Paired Devices group**

In `_setup_ui`, immediately after `layout.addWidget(status_group)`, insert:

```python
        # --- Paired devices ---
        devices_group = QGroupBox("Paired Devices")
        devices_layout = QVBoxLayout(devices_group)
        devices_layout.setSpacing(8)
        devices_layout.setContentsMargins(14, 14, 14, 14)

        pair_row = QHBoxLayout()
        pair_row.setSpacing(10)
        self._pair_btn = QPushButton("Pair device")
        self._pair_btn.setFixedHeight(32)
        self._pair_btn.setStyleSheet(self._btn_style(SURFACE, CYAN, INK))
        self._pair_btn.clicked.connect(self._start_pairing)
        pair_row.addWidget(self._pair_btn)
        self._pair_code_label = QLabel("")
        self._pair_code_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self._pair_code_label.setFont(self._mono_font(13, QFont.DemiBold))
        self._pair_code_label.setStyleSheet(f"color: {MINT};")
        pair_row.addWidget(self._pair_code_label, 1)
        devices_layout.addLayout(pair_row)

        self._device_list = QListWidget()
        self._device_list.setFixedHeight(64)
        self._device_list.setFont(self._mono_font(10))
        self._device_list.setStyleSheet(
            f"QListWidget {{ background: {SURFACE}; color: {INK};"
            f" border: 1px solid {HAIRLINE}; border-radius: 6px; }}"
        )
        devices_layout.addWidget(self._device_list)

        device_actions = QHBoxLayout()
        device_actions.setSpacing(10)
        self._remove_device_btn = QPushButton("Remove selected")
        self._remove_device_btn.setFixedHeight(32)
        self._remove_device_btn.setStyleSheet(self._btn_style(SURFACE, DESTRUCTIVE_HOVER, INK))
        self._remove_device_btn.clicked.connect(self._remove_selected_device)
        device_actions.addWidget(self._remove_device_btn)
        self._unpair_all_btn = QPushButton("Unpair all")
        self._unpair_all_btn.setFixedHeight(32)
        self._unpair_all_btn.setStyleSheet(self._btn_style(SURFACE, DESTRUCTIVE_HOVER, INK))
        self._unpair_all_btn.clicked.connect(self._unpair_all)
        device_actions.addWidget(self._unpair_all_btn)
        devices_layout.addLayout(device_actions)

        layout.addWidget(devices_group)
```

- [ ] **Step 6: Replace the account and relay methods with the pairing methods**

Replace `_refresh_status` with:

```python
    def _refresh_status(self):
        self._refresh_ip()
        self._refresh_pairing()
        self.update_active_streams(0)
```

Delete these methods entirely: `_refresh_account`, `_set_account_state`, `_initials`, `_elide_account_detail`, `_sign_out`, `_show_sign_in`, `resizeEvent`, `_refresh_relay`.

Insert after `_refresh_ip`:

```python
    def _start_pairing(self):
        self._pairing.start_pairing()
        self._refresh_pairing()

    def _refresh_pairing(self):
        active = self._pairing.active_code()
        if active is None:
            self._pair_code_label.setText("")
            self._pair_btn.setText("Pair device")
        else:
            code, remaining = active
            self._pair_code_label.setText(
                f"{code[:3]} {code[3:]}  ·  {remaining // 60}:{remaining % 60:02d}"
            )
            self._pair_btn.setText("New code")

        devices = self._pairing.list_devices()
        ids = [device.id for device in devices]
        if ids != self._device_ids:
            self._device_ids = ids
            self._device_list.clear()
            for device in devices:
                paired_on = time.strftime("%Y-%m-%d", time.localtime(device.created_at))
                item = QListWidgetItem(f"{device.name}  ·  {paired_on}")
                item.setData(Qt.UserRole, device.id)
                self._device_list.addItem(item)
        self._remove_device_btn.setEnabled(bool(devices))
        self._unpair_all_btn.setEnabled(bool(devices))

    def _remove_selected_device(self):
        item = self._device_list.currentItem()
        if item is None:
            return
        self._pairing.remove_device(item.data(Qt.UserRole))
        self._refresh_pairing()

    def _unpair_all(self):
        self._pairing.remove_all()
        self._refresh_pairing()
```

- [ ] **Step 7: Confirm the launcher has no leftovers**

Run: `grep -nE "SUPABASE|supabase|VPS_SIGNALING|_account|_relay|sign_in|sign_out|QDialog|QFontMetrics|maybe_show_login" src/gui/launcher.py`
Expected: no output.

- [ ] **Step 8: Wire the store through `src/main.py`**

1. Change the import line `from gui.launcher import LauncherWindow, maybe_show_login` to:

```python
    from gui.launcher import LauncherWindow
    from server.pairing import PairingStore, default_store_path
```

2. Replace `fastapi_app = create_app(instance_manager)` with:

```python
    pairing = PairingStore(default_store_path())
    fastapi_app = create_app(instance_manager, pairing=pairing)
```

3. Replace the comment block above `config = uvicorn.Config(` (the lines from `# proxy_headers=False is load-bearing, not a default restated:` through `# app-level guard actually meaning what it documents.`) with:

```python
        # proxy_headers=False is load-bearing: uvicorn's default trusts
        # X-Forwarded-For from loopback peers and rewrites the client
        # address, which is the address the access gate checks.
```

4. Delete these lines:

```python
    if not maybe_show_login():
        _log("[GUI] login cancelled — exiting without showing launcher")
        return

```

5. Replace `launcher = LauncherWindow(on_stop_server=stop_server)` with:

```python
    launcher = LauncherWindow(on_stop_server=stop_server, pairing=pairing)
```

- [ ] **Step 9: Update `tests/test_main.py`**

In `_patch_main_startup`, change:

```python
    monkeypatch.setattr(main_mod, "create_app", lambda *args: object())
```

to:

```python
    monkeypatch.setattr(main_mod, "create_app", lambda *args, **kwargs: object())
```

In `test_main_starts_server_without_android_mjpeg_pipeline`, after `assert len(app_calls[0][0]) == 1` add:

```python
    assert set(app_calls[0][1]) == {"pairing"}
```

- [ ] **Step 10: Run the tests**

Run: `uv run pytest tests/test_launcher_widget.py tests/test_launcher.py tests/test_main.py -q`
Expected: all pass.

- [ ] **Step 11: Commit**

```bash
git add src/gui/launcher.py src/main.py tests/test_launcher_widget.py tests/test_main.py
git commit -m "feat(host): pair devices from the launcher"
```

---

### Task 5: Remove the public-access Python code

**Files:**
- Delete: `src/server/auth.py`, `src/server/supabase_client.py`, `src/server/install_identity.py`, `src/server/http_tunnel.py`, `src/server/ice_config.py`, `src/gui/supabase_login.py`
- Delete: `tests/test_auth.py`, `tests/test_supabase_client.py`, `tests/test_install_identity.py`, `tests/test_http_tunnel.py`, `tests/test_ice_config.py`, `tests/test_supabase_login.py`, `tests/conftest.py`
- Modify: `src/server/engine_auth.py`, `src/server/engine_runtime.py`, `src/server/engine_orchestrator.py`, `src/main.py`, `src/config.py`, `pyproject.toml`, `requirements.txt`
- Modify: `tests/test_engine_auth.py`, `tests/test_engine_runtime.py`, `tests/test_engine_orchestrator.py`, `tests/test_instance_manager.py`, `tests/test_main.py`, `tests/test_app.py`, `tests/test_config.py`

**Interfaces:**
- Consumes: Task 3 (app.py no longer imports the deleted modules), Task 4 (launcher and main no longer import `supabase_login`).
- Produces:
  - `EngineTokenIssuer(whep_secret: str, whep_ttl_seconds: int = 300, clock=time.time)` with only `whep(instance_name) -> str`.
  - `EngineRuntimeConfig(exe_path: str, whep_secret: str, local_ice_servers: tuple[str, ...])`.
  - `EngineSelection(whep_url: str, whep_token: str, generation: int, width: int, height: int)`.
  - `config` no longer has `VPS_SIGNALING_URL`, `ENGINE_PUBLIC_ICE_SERVERS`, `TURN_HOST`, `TURN_PORT`, `TURN_USERNAME`, `TURN_CREDENTIAL`, `SUPABASE_URL`, `SUPABASE_ANON_KEY`, `SUPABASE_SERVICE_ROLE_KEY`, `SUPABASE_JWT_SECRET`, `PUBLIC_UI_URL`, `TUNNEL_SECRET`.
  - The engine process environment contains only `ENGINE_WHEP_CAPABILITY_SECRET` and `ENGINE_LOCAL_ICE_SERVERS`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_config.py`, at the end of the file:

```python
def test_public_access_config_and_modules_are_absent():
    import config

    for name in (
        "VPS_SIGNALING_URL", "ENGINE_PUBLIC_ICE_SERVERS",
        "TURN_HOST", "TURN_PORT", "TURN_USERNAME", "TURN_CREDENTIAL",
        "SUPABASE_URL", "SUPABASE_ANON_KEY", "SUPABASE_SERVICE_ROLE_KEY",
        "SUPABASE_JWT_SECRET", "PUBLIC_UI_URL", "TUNNEL_SECRET",
    ):
        assert not hasattr(config, name), name

    repo = Path(__file__).parent.parent
    for relative in (
        "src/server/auth.py", "src/server/supabase_client.py",
        "src/server/install_identity.py", "src/server/http_tunnel.py",
        "src/server/ice_config.py", "src/gui/supabase_login.py",
    ):
        assert not (repo / relative).exists(), relative

    source = "\n".join(
        path.read_text(errors="replace") for path in (repo / "src").rglob("*.py")
    )
    for removed in ("supabase", "SUPABASE", "ENGINE_SIGNALING", "ENGINE_SESSION",
                    "TUNNEL_SECRET", "TURN_HOST", "public_session", "import jwt"):
        assert removed not in source, removed
    assert "pyjwt" not in (repo / "pyproject.toml").read_text().lower()
```

In `tests/test_engine_runtime.py`, replace the assertions at the end of the test that checks the engine environment (the two lines `assert decode_role(fakes.engine_env["ENGINE_SIGNALING_TOKEN"]) == "engine"` and `assert fakes.engine_env["ENGINE_SESSION"] == "owner-1.instance0"`) with:

```python
    assert set(fakes.engine_env) == {
        "ENGINE_WHEP_CAPABILITY_SECRET", "ENGINE_LOCAL_ICE_SERVERS",
    }
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_config.py::test_public_access_config_and_modules_are_absent tests/test_engine_runtime.py -q`
Expected: the config test fails on `VPS_SIGNALING_URL`; the engine-env assertion fails because six variables are set.

- [ ] **Step 3: Delete the modules and their tests**

```bash
git rm -q src/server/auth.py src/server/supabase_client.py src/server/install_identity.py \
  src/server/http_tunnel.py src/server/ice_config.py src/gui/supabase_login.py \
  tests/test_auth.py tests/test_supabase_client.py tests/test_install_identity.py \
  tests/test_http_tunnel.py tests/test_ice_config.py tests/test_supabase_login.py \
  tests/conftest.py
```

(`tests/conftest.py` contained only a fixture that cleared the removed environment variables.)

- [ ] **Step 4: Replace `src/server/engine_auth.py`**

Write the whole file:

```python
"""Mint short-lived WHEP capability tokens for the C++ engine.

A compact HMAC scheme that must byte-match the engine's verifier:
`"{expiry}.{instance_name}.{hmac_sha256_hex}"`, HMAC-SHA256 over the WHEP
secret.
"""

import hashlib
import hmac
import threading
import time
from typing import Callable


class EngineTokenIssuer:
    def __init__(
        self,
        whep_secret: str,
        whep_ttl_seconds: int = 300,
        clock: Callable[[], float] = time.time,
    ):
        if not whep_secret:
            raise ValueError("whep_secret must not be empty")
        self._whep_secret = whep_secret
        self._whep_ttl_seconds = whep_ttl_seconds
        self._clock = clock
        self._lock = threading.Lock()
        self._last_whep_expiry = 0

    def whep(self, instance_name: str) -> str:
        with self._lock:
            expiry = max(
                int(self._clock()) + self._whep_ttl_seconds,
                self._last_whep_expiry + 1,
            )
            self._last_whep_expiry = expiry
        payload = f"{expiry}.{instance_name}"
        signature = hmac.new(
            self._whep_secret.encode(), payload.encode(), hashlib.sha256
        ).hexdigest()
        return f"{payload}.{signature}"
```

- [ ] **Step 5: Trim `src/server/engine_runtime.py`**

1. In the module docstring, replace the paragraph beginning `Credentials are minted per call and never cached:` (through `dimensions) is retained between calls.`) with:

```
Credentials are minted per call and never cached: `select()` issues a fresh WHEP
capability token every time, because it is short-lived. Only non-expiring
endpoint metadata (ports, generation, dimensions) is retained between calls.
```

2. Delete these two import lines:

```python
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
```
```python
from server import install_identity
```

3. Replace the `EngineRuntimeConfig` and `EngineSelection` dataclasses with:

```python
@dataclass(frozen=True)
class EngineRuntimeConfig:
    """Immutable process-wide engine configuration."""
    exe_path: str
    whep_secret: str
    local_ice_servers: tuple[str, ...]


@dataclass(frozen=True)
class EngineSelection:
    """What a client needs to start playing. Deliberately has no admin port —
    the admin listener is loopback-only and never client-facing."""
    whep_url: str
    whep_token: str
    generation: int
    width: int
    height: int
```

4. In `select()`, replace everything from the comment `# Viewers no longer get a locally-minted signaling token:` through the end of the `return EngineSelection(...)` statement with:

```python
            return EngineSelection(
                whep_url=f"http://{host}:{endpoint.whep_port}/whep",
                whep_token=self._token_issuer.whep(self.instance_name),
                generation=endpoint.generation,
                width=endpoint.width,
                height=endpoint.height,
            )
```

5. Replace the whole `_build_env_locked` method with:

```python
    def _build_env_locked(self) -> dict[str, str]:
        return {
            "ENGINE_WHEP_CAPABILITY_SECRET": self.config.whep_secret,
            "ENGINE_LOCAL_ICE_SERVERS": ",".join(self.config.local_ice_servers),
        }
```

- [ ] **Step 6: Trim `src/server/engine_orchestrator.py`**

Replace:

```python
        self._token_issuer = EngineTokenIssuer(
            config.whep_secret, config.signaling_private_key
        )
```

with:

```python
        self._token_issuer = EngineTokenIssuer(config.whep_secret)
```

- [ ] **Step 7: Trim `build_engine_orchestrator` in `src/main.py`**

Replace everything from `from server import install_identity` through the end of the `runtime_config = EngineRuntimeConfig(...)` statement with:

```python
    runtime_config = EngineRuntimeConfig(
        exe_path=exe_path,
        whep_secret=secrets.token_hex(32),
        local_ice_servers=config.ENGINE_LOCAL_ICE_SERVERS,
    )
```

Also change the comment on line 13 from `# log.info() (tunnel connect/disconnect, engine lifecycle, etc.) is silently` to `# log.info() (engine lifecycle, pairing, etc.) is silently`.

- [ ] **Step 8: Trim `src/config.py`**

Replace lines 25–78 (from `# Engine / scrcpy` through `TUNNEL_SECRET = os.environ.get("TUNNEL_SECRET")`) with:

```python
# Engine / scrcpy
STUN_PORT = 3478       # embedded STUN server, bound to Tailscale IP (see stun_server.py)
ENGINE_LOCAL_ICE_SERVERS = tuple(filter(None, os.environ.get(
    "ENGINE_LOCAL_ICE_SERVERS", ""
).split(",")))
```

- [ ] **Step 9: Drop unused dependencies**

In `pyproject.toml`:
- delete the line `    "pyjwt[crypto]>=2.9.0",`
- change `    "websockets>=14.0",  # >=14: asyncio client `additional_headers` (http_tunnel)` to `    "websockets>=14.0",  # scripts/verify_engine_cutover.py`

In `requirements.txt`, change the comment on the `websockets>=14.0` line to `# scripts/verify_engine_cutover.py`, and delete any line that starts with `pyjwt` or `PyJWT`.

Run: `uv lock && uv sync`
Expected: the lock file updates; `pyjwt` and (unless another package needs it) `cryptography` are removed.

- [ ] **Step 10: Update `tests/test_engine_auth.py`**

Delete these four functions: `test_engine_token_payload_contains_session_role_and_expiry`, `test_engine_token_issuances_are_unique_and_verifiable`, `test_engine_token_rejects_tampering`, `test_no_signaling_key_returns_empty_token_for_trusted_dev`.

Delete the helpers `_b64url_decode` and `decode_and_verify_eddsa`, and these imports: `import base64`, `import json`, `from cryptography.exceptions import InvalidSignature`, `from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey`.

Three tests remain: `test_whep_token_matches_cpp_fixture`, `test_whep_tokens_are_minted_from_the_current_clock_each_time`, `test_empty_whep_secret_is_invalid`.

- [ ] **Step 11: Update `tests/test_engine_runtime.py`**

1. Delete the import lines `from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey` and `from server import install_identity`.
2. Delete the whole `_fixed_owner` autouse fixture.
3. In `CountingTokenIssuer`: change the docstring's second line to `    whep() -> "whep:<instance>:<n>".`, delete `self.engine_token_calls: list[str] = []`, and delete the whole `engine_token` method.
4. If the function `decode_role` has no remaining callers (`grep -n decode_role tests/test_engine_runtime.py` shows only its definition), delete it.
5. Replace `make_config` with:

```python
def make_config(**overrides) -> EngineRuntimeConfig:
    values = dict(
        exe_path=r"C:\engine\engine.exe",
        whep_secret="whep-secret",
        local_ice_servers=("stun:100.64.1.4:3478",),
    )
    values.update(overrides)
    return EngineRuntimeConfig(**values)
```

6. Replace the whole function `test_select_mints_fresh_whep_tokens_and_public_session` with:

```python
def test_select_mints_fresh_whep_tokens():
    issuer = CountingTokenIssuer()
    runtime, fakes = make_runtime(token_issuer=issuer)
    runtime.start()
    first = runtime.select("100.64.1.4")
    second = runtime.select("100.64.1.4")
    assert first.whep_url == "http://100.64.1.4:51000/whep"
    assert first.whep_token != second.whep_token
    assert not hasattr(first, "admin_port")
    assert not hasattr(first, "signaling_url")
    assert not hasattr(first, "public_session")
```

7. Delete these three functions: `test_select_returns_null_signaling_url_and_session_together_when_disabled`, `test_select_returns_null_public_session_when_owner_not_yet_cached`, `test_build_env_falls_back_to_bare_instance_name_when_owner_not_yet_cached`.

8. Run `grep -nE "signaling|public_session|public_ice|engine_token|install_identity|ENGINE_SESSION|ENGINE_PUBLIC" tests/test_engine_runtime.py`. For each remaining hit: if it is an assertion about an environment variable or field that no longer exists, delete that assertion line; if it is a keyword argument to `make_config(...)`, delete that argument. Expected after edits: no output.

- [ ] **Step 12: Update the other tests**

`tests/test_engine_orchestrator.py` — replace `make_config` with:

```python
def make_config() -> EngineRuntimeConfig:
    return EngineRuntimeConfig(
        exe_path=r"C:\engine\engine.exe",
        whep_secret="whep-secret",
        local_ice_servers=("stun:100.64.1.4:3478",),
    )
```

and delete the `Ed25519PrivateKey` import line if `grep -n Ed25519PrivateKey tests/test_engine_orchestrator.py` shows no other use.

`tests/test_instance_manager.py` — in the nested `TokenIssuer` class delete:

```python
        def engine_token(self, _session):
            return "signal"
```

and replace the `EngineRuntimeConfig(...)` construction with:

```python
    config = EngineRuntimeConfig(
        exe_path=r"C:\engine\engine.exe",
        whep_secret="whep-secret",
        local_ice_servers=(),
    )
```

`tests/test_main.py` — replace the whole function `test_build_engine_orchestrator_passes_configured_runtime_values` with:

```python
def test_build_engine_orchestrator_passes_configured_runtime_values(monkeypatch):
    import main as main_mod

    monkeypatch.setattr(main_mod.config, "engine_exe_path", lambda: "C:/app/engine.exe")
    monkeypatch.setattr(main_mod.os.path, "isfile", lambda _: True)
    monkeypatch.setattr(main_mod.secrets, "token_hex", lambda size: "generated-whep")
    monkeypatch.setattr(main_mod.config, "ENGINE_LOCAL_ICE_SERVERS", ("stun:local",), raising=False)

    orchestrator = main_mod.build_engine_orchestrator()

    assert orchestrator.config.exe_path == "C:/app/engine.exe"
    assert orchestrator.config.whep_secret == "generated-whep"
    assert orchestrator.config.local_ice_servers == ("stun:local",)
```

and delete the whole function `test_build_engine_orchestrator_disables_signaling_without_a_supabase_project`.

`tests/test_app.py`:
- In `_make_client`, delete the two lines `signaling_url=None,` and `public_session=None,`.
- In `test_instance_select_returns_exact_engine_contract`, delete the two lines `signaling_url="wss://signal.example",` and `public_session="owner-1.instance0",`.
- In `test_instance_select_formats_ipv6_stun`, change `signaling_url=None, public_session=None, generation=4,` to `generation=4,`.
- In `test_instance_select_mints_fresh_capabilities_each_time`, change the two constructions to `EngineSelection("http://host/whep", "first", 1, 1280, 720)` and `EngineSelection("http://host/whep", "second", 1, 1280, 720)`.
- In `test_legacy_select_includes_name`, replace the four-line comment above `inst = MagicMock()` with the single line `# Legacy /select must stay in sync with /instances/{id}/select.`

- [ ] **Step 13: Confirm nothing is left**

Run: `grep -rnE "supabase|SUPABASE|install_identity|http_tunnel|ice_config|engine_token|signaling_private_key|public_ice_servers|public_session|TUNNEL_SECRET|PUBLIC_UI_URL|VPS_SIGNALING_URL" src tests/test_app.py tests/test_main.py tests/test_engine_runtime.py tests/test_engine_orchestrator.py tests/test_engine_auth.py tests/test_instance_manager.py apps/desktop --include="*.py"`
Expected: no output.

- [ ] **Step 14: Run the full Python suite**

Run: `uv run pytest tests/ apps/desktop/ -q`
Expected: all pass. (`tests/test_engine_cutover_verifier.py`, `tests/test_windows_verifier.py` and `tests/test_frontend_cutover_verifier.py` test the `scripts/` verifiers with fakes and are unaffected; Plan B rewrites them.)

- [ ] **Step 15: Commit**

```bash
git add -A src tests pyproject.toml requirements.txt uv.lock
git status --short
git commit -m "refactor(server): remove Supabase auth, tunnel, TURN and signaling config"
```

Check the `git status --short` output before committing: only files under `src/`, `tests/`, and the three dependency files should be staged.

---

> **JavaScript test window:** Tasks 6–9 change one package each. `packages/ui` and `apps/web` consume `packages/core` from source, so their suites can fail between Task 6 and Task 9. Each task below runs the suite for the package it changes; Task 9 runs all three and the web build. Do not push between Task 6 and Task 9.

### Task 6: Core API — pairing client, host probe, server context

**Files:**
- Create: `packages/core/src/api/pairing.ts`, `packages/core/src/api/pairing.test.ts`
- Modify: `packages/core/src/api/hostProbe.ts`, `packages/core/src/api/hostProbe.test.ts`
- Modify: `packages/core/src/api/client.ts`, `packages/core/src/api/client.test.ts`
- Modify: `packages/core/src/api/ServerContext.tsx`, `packages/core/src/api/ServerContext.test.tsx`
- Modify: `packages/core/src/index.ts`
- Delete: `packages/core/src/api/supabaseAuth.ts`, `packages/core/src/api/supabaseAuth.test.ts`

**Interfaces:**
- Consumes: server routes `POST /pair`, `GET /pair/status` (Task 3).
- Produces:
  - `pairDevice(base: string, code: string, deviceName: string, fetchImpl?: typeof fetch): Promise<{ token: string } | { error: string }>`
  - `deviceTokenKey(base: string): string`
  - `HostReachability = { state: "checking" | "reachable" | "unreachable"; host: string; rttMs: number | null; paired: boolean | null }` (the `route` field and `classifyHostRoute` are gone)
  - `probeHost(base: string, token?: string | null, fetchImpl?: typeof fetch, now?: () => number): Promise<HostReachability>`
  - `useServer()` returns `{ base, authToken, paired, client, setServer, clearAuth, preferences, updatePreferences, ready, hostReachability }`. `identity`, `supabaseUrl`, `supabaseAnonKey` are gone. `paired` is `boolean | null`.
  - `SelectResp` no longer has `signaling_url` or `public_session`.

- [ ] **Step 1: Write the failing pairing test**

Create `packages/core/src/api/pairing.test.ts`:

```ts
import { deviceTokenKey, pairDevice } from "./pairing";

function response(status: number, body?: unknown) {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => {
      if (body === undefined) throw new Error("no body");
      return body;
    },
  } as Response;
}

test("posts the code and device name and returns the token", async () => {
  const fetchImpl = jest.fn(async () => response(200, { token: "dev-tok" }));
  await expect(pairDevice("http://192.168.1.8:8080", "123456", "iPhone", fetchImpl as any))
    .resolves.toEqual({ token: "dev-tok" });
  expect(fetchImpl).toHaveBeenCalledWith("http://192.168.1.8:8080/pair", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ code: "123456", device_name: "iPhone" }),
  });
});

test("a rejected code explains how to get a new one", async () => {
  const fetchImpl = jest.fn(async () => response(403, { detail: "Invalid or expired pairing code" }));
  const result = await pairDevice("http://h", "000000", "iPhone", fetchImpl as any);
  expect(result).toEqual({ error: "That code is wrong or has expired. Click Pair device on the PC for a new one." });
});

test("an unreachable host is reported, not thrown", async () => {
  const fetchImpl = jest.fn(async () => { throw new Error("offline"); });
  const result = await pairDevice("http://h", "123456", "iPhone", fetchImpl as any);
  expect(result).toEqual({ error: "Can't reach the host. Check the address and that EmuCtrl is running." });
});

test.each([
  [500, undefined, "Pairing failed (500)"],
  [200, {}, "Pairing failed: the host sent no token"],
  [200, undefined, "Pairing failed: the host sent no token"],
  [200, { token: "" }, "Pairing failed: the host sent no token"],
])("status %s with body %j is an error", async (status, body, message) => {
  const fetchImpl = jest.fn(async () => response(status as number, body));
  await expect(pairDevice("http://h", "123456", "iPhone", fetchImpl as any))
    .resolves.toEqual({ error: message });
});

test("token keys are per host and safe for expo-secure-store", () => {
  const key = deviceTokenKey("http://100.101.102.103:8080");
  expect(key).toMatch(/^[A-Za-z0-9._-]+$/);
  expect(key).not.toBe(deviceTokenKey("http://192.168.1.8:8080"));
  expect(deviceTokenKey("http://[fd7a:115c:a1e0::1]:8080")).toMatch(/^[A-Za-z0-9._-]+$/);
});
```

- [ ] **Step 2: Run it to verify it fails**

Run: `npm test -w packages/core -- src/api/pairing.test.ts`
Expected: FAIL, `Cannot find module './pairing'`.

- [ ] **Step 3: Write `packages/core/src/api/pairing.ts`**

```ts
import { httpUrl } from "./urls";

export type PairResult = { token: string } | { error: string };

// expo-secure-store accepts only [A-Za-z0-9._-] in a key, so the host part
// is flattened rather than used verbatim.
export function deviceTokenKey(base: string): string {
  return `wc_device_token.${base.replace(/[^A-Za-z0-9._-]/g, "_")}`;
}

export async function pairDevice(
  base: string,
  code: string,
  deviceName: string,
  fetchImpl: typeof fetch = fetch,
): Promise<PairResult> {
  let response: Response;
  try {
    response = await fetchImpl(httpUrl(base, "/pair"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ code, device_name: deviceName }),
    });
  } catch {
    return { error: "Can't reach the host. Check the address and that EmuCtrl is running." };
  }
  if (response.status === 403) {
    return { error: "That code is wrong or has expired. Click Pair device on the PC for a new one." };
  }
  if (!response.ok) return { error: `Pairing failed (${response.status})` };
  const body = await response.json().catch(() => null);
  return body && typeof body.token === "string" && body.token
    ? { token: body.token }
    : { error: "Pairing failed: the host sent no token" };
}
```

- [ ] **Step 4: Rewrite the host probe and its test**

Write the whole of `packages/core/src/api/hostProbe.ts`:

```ts
export type HostReachability = {
  state: "checking" | "reachable" | "unreachable";
  host: string;
  rttMs: number | null;
  // null until the host has answered; an unreachable host says nothing
  // about whether this device is paired.
  paired: boolean | null;
};

export async function probeHost(
  base: string,
  token: string | null = null,
  fetchImpl: typeof fetch = fetch,
  now: () => number = Date.now,
): Promise<HostReachability> {
  const url = new URL(base);
  const started = now();
  try {
    const response = await fetchImpl(`${base.replace(/\/+$/, "")}/pair/status`, {
      method: "GET",
      headers: token ? { Authorization: `Bearer ${token}` } : undefined,
    });
    if (!response.ok) throw new Error(String(response.status));
    const rttMs = Math.max(0, now() - started);
    let paired = false;
    try {
      paired = (await response.json())?.paired === true;
    } catch {}
    return { state: "reachable", host: url.host, rttMs, paired };
  } catch {
    return { state: "unreachable", host: url.host, rttMs: null, paired: null };
  }
}
```

Write the whole of `packages/core/src/api/hostProbe.test.ts`:

```ts
import * as hostProbe from "./hostProbe";
import { probeHost } from "./hostProbe";

test("reports measured reachability and the paired flag", async () => {
  let clock = 100;
  const fetchImpl = jest.fn().mockImplementation(async () => {
    clock = 128;
    return { ok: true, json: async () => ({ paired: true }) };
  });

  await expect(probeHost("http://192.168.1.8:8080", "dev-tok", fetchImpl as any, () => clock)).resolves.toEqual({
    state: "reachable", host: "192.168.1.8:8080", rttMs: 28, paired: true,
  });
  expect(fetchImpl).toHaveBeenCalledWith("http://192.168.1.8:8080/pair/status", {
    method: "GET", headers: { Authorization: "Bearer dev-tok" },
  });
});

test("sends no Authorization header without a token", async () => {
  const fetchImpl = jest.fn(async () => ({ ok: true, json: async () => ({ paired: false }) }));
  const result = await probeHost("http://192.168.1.8:8080/", null, fetchImpl as any);
  expect(result.paired).toBe(false);
  expect(fetchImpl).toHaveBeenCalledWith("http://192.168.1.8:8080/pair/status", {
    method: "GET", headers: undefined,
  });
});

test.each([
  [{ ok: true, json: async () => ({}) }],
  [{ ok: true, json: async () => { throw new Error("not json"); } }],
  [{ ok: true }],
])("a reachable host with an unusable body counts as not paired", async (response) => {
  const result = await probeHost("http://h:8080", null, (async () => response) as any);
  expect(result).toMatchObject({ state: "reachable", paired: false });
});

test.each([
  [async () => { throw new Error("offline"); }],
  [async () => ({ ok: false, status: 403 })],
])("an unreachable or refusing host leaves paired unknown", async (fetchImpl) => {
  await expect(probeHost("http://h:8080", "tok", fetchImpl as any)).resolves.toEqual({
    state: "unreachable", host: "h:8080", rttMs: null, paired: null,
  });
});

test("route classification is gone", () => {
  expect((hostProbe as any).classifyHostRoute).toBeUndefined();
});
```

- [ ] **Step 5: Update the API client and its test**

In `packages/core/src/api/client.ts`:

Delete these two lines from `SelectResp`:

```ts
  signaling_url: string | null;
  public_session: string | null;
```

In `ping()`, change `await request("/auth/config");` to `await request("/pair/status");`.

In `packages/core/src/api/client.test.ts`:
- rename the test `"ping returns the elapsed auth-config request time"` to `"ping returns the elapsed pair-status request time"`, and add as the last line before `now.mockRestore();`:

```ts
  expect((global.fetch as jest.Mock).mock.calls[0][0]).toBe("https://host/pair/status");
```

- in `"parses the exact final selection shape"`, delete these two lines from `body`:

```ts
    signaling_url: "wss://relay/ws",
    signaling_token: "sig-tok",
```

- [ ] **Step 6: Rewrite the server context test**

Write the whole of `packages/core/src/api/ServerContext.test.tsx`:

```tsx
import React from "react";
import { render, waitFor, act } from "@testing-library/react";
import { ServerProvider, useServer } from "./ServerContext";
import { deviceTokenKey } from "./pairing";
import type { SecureStorageAdapter } from "./storage";

const originalFetch = global.fetch;

afterEach(() => {
  global.fetch = originalFetch;
  jest.useRealTimers();
});

function makeMemoryStorage(): SecureStorageAdapter {
  const store = new Map<string, string>();
  return {
    getItem: async (k) => store.get(k) ?? null,
    setItem: async (k, v) => { store.set(k, v); },
    deleteItem: async (k) => { store.delete(k); },
  };
}

async function flush() {
  for (let i = 0; i < 12; i += 1) {
    await act(async () => { await Promise.resolve(); });
  }
}

function statusFetch(paired: boolean) {
  return jest.fn(async () => ({ ok: true, json: async () => ({ paired }) }));
}

function Probe() {
  const { ready, base, authToken, paired, hostReachability, setServer, clearAuth } = useServer();
  return (
    <div>
      <span data-testid="ready">{String(ready)}</span>
      <span data-testid="base">{base ?? ""}</span>
      <span data-testid="token">{authToken ?? ""}</span>
      <span data-testid="paired">{String(paired)}</span>
      <span data-testid="reachability">{hostReachability.state}</span>
      <button onClick={() => setServer("http://host:8000", "tok")}>set</button>
      <button onClick={() => clearAuth()}>clear</button>
    </div>
  );
}

function PreferencesProbe() {
  const server = useServer();
  return (
    <div>
      <span data-testid="ready">{String(server.ready)}</span>
      <span data-testid="preferences">{JSON.stringify(server.preferences)}</span>
      <button onClick={() => server.updatePreferences({ quality: "720", haptics: false })}>update preferences</button>
      <button onClick={() => server.clearAuth()}>clear auth</button>
    </div>
  );
}

function renderProvider(plain: SecureStorageAdapter, secure: SecureStorageAdapter, child = <Probe />) {
  return render(
    <ServerProvider plainStorage={plain} secureStorage={secure}>{child}</ServerProvider>
  );
}

test("loads the persisted base and that host's device token", async () => {
  const plain = makeMemoryStorage();
  const secure = makeMemoryStorage();
  await plain.setItem("wc_base", "http://saved:8000");
  await secure.setItem(deviceTokenKey("http://saved:8000"), "saved-tok");

  const { getByTestId } = renderProvider(plain, secure);

  await waitFor(() => expect(getByTestId("ready").textContent).toBe("true"));
  expect(getByTestId("base").textContent).toBe("http://saved:8000");
  expect(getByTestId("token").textContent).toBe("saved-tok");
});

test("a token stored for another host is not used", async () => {
  const plain = makeMemoryStorage();
  const secure = makeMemoryStorage();
  await plain.setItem("wc_base", "http://saved:8000");
  await secure.setItem(deviceTokenKey("http://other:8000"), "other-tok");

  const { getByTestId } = renderProvider(plain, secure);

  await waitFor(() => expect(getByTestId("ready").textContent).toBe("true"));
  expect(getByTestId("token").textContent).toBe("");
});

test("a leftover Supabase session token is deleted on load", async () => {
  const plain = makeMemoryStorage();
  const secure = makeMemoryStorage();
  await secure.setItem("wc_auth_token", "old-jwt");

  const { getByTestId } = renderProvider(plain, secure);

  await waitFor(() => expect(getByTestId("ready").textContent).toBe("true"));
  await waitFor(async () => expect(await secure.getItem("wc_auth_token")).toBeNull());
  expect(getByTestId("token").textContent).toBe("");
});

test("probes /pair/status with the device token and publishes paired", async () => {
  jest.useFakeTimers();
  const fetchMock = statusFetch(true);
  global.fetch = fetchMock as any;
  const plain = makeMemoryStorage();
  const secure = makeMemoryStorage();
  await plain.setItem("wc_base", "http://192.168.1.8:8080");
  await secure.setItem(deviceTokenKey("http://192.168.1.8:8080"), "dev-tok");

  const { getByTestId, unmount } = renderProvider(plain, secure);

  await flush();
  expect(getByTestId("reachability").textContent).toBe("reachable");
  expect(getByTestId("paired").textContent).toBe("true");
  expect(fetchMock).toHaveBeenLastCalledWith("http://192.168.1.8:8080/pair/status", {
    method: "GET", headers: { Authorization: "Bearer dev-tok" },
  });
  const initialRequestCount = fetchMock.mock.calls.length;
  act(() => { jest.advanceTimersByTime(30_000); });
  expect(fetchMock).toHaveBeenCalledTimes(initialRequestCount + 1);
  unmount();
  act(() => { jest.advanceTimersByTime(30_000); });
  expect(fetchMock).toHaveBeenCalledTimes(initialRequestCount + 1);
});

test("a host that no longer recognises the token reports paired false", async () => {
  global.fetch = statusFetch(false) as any;
  const plain = makeMemoryStorage();
  const secure = makeMemoryStorage();
  await plain.setItem("wc_base", "http://192.168.1.8:8080");
  await secure.setItem(deviceTokenKey("http://192.168.1.8:8080"), "revoked-tok");

  const { getByTestId } = renderProvider(plain, secure);

  await waitFor(() => expect(getByTestId("paired").textContent).toBe("false"));
  expect(getByTestId("token").textContent).toBe("revoked-tok");
});

test("a host that answers paired without a token (loopback) is paired", async () => {
  global.fetch = statusFetch(true) as any;
  const plain = makeMemoryStorage();
  const secure = makeMemoryStorage();
  await plain.setItem("wc_base", "http://127.0.0.1:8080");

  const { getByTestId } = renderProvider(plain, secure);

  await waitFor(() => expect(getByTestId("paired").textContent).toBe("true"));
  expect(getByTestId("token").textContent).toBe("");
});

test("remains renderable with a saved base when the runtime has no fetch", async () => {
  const plain = makeMemoryStorage();
  const secure = makeMemoryStorage();
  await plain.setItem("wc_base", "http://192.168.1.8:8080");
  delete (global as { fetch?: typeof fetch }).fetch;

  const screen = renderProvider(plain, secure);

  await waitFor(() => expect(screen.getByTestId("ready").textContent).toBe("true"));
  expect(screen.getByTestId("base").textContent).toBe("http://192.168.1.8:8080");
  expect(screen.getByTestId("reachability").textContent).toBe("checking");
  expect(screen.getByTestId("paired").textContent).toBe("null");
  screen.unmount();
});

test("a failed probe keeps the token and leaves paired unknown", async () => {
  global.fetch = jest.fn(async () => { throw new Error("offline"); }) as any;
  const plain = makeMemoryStorage();
  const secure = makeMemoryStorage();
  await plain.setItem("wc_base", "http://192.168.1.8:8080");
  await secure.setItem(deviceTokenKey("http://192.168.1.8:8080"), "active-tok");

  const { getByTestId } = renderProvider(plain, secure);

  await waitFor(() => expect(getByTestId("reachability").textContent).toBe("unreachable"));
  expect(getByTestId("token").textContent).toBe("active-tok");
  expect(getByTestId("paired").textContent).toBe("null");
});

test("setServer persists the base and a per-host token and marks the device paired", async () => {
  const plain = makeMemoryStorage();
  const secure = makeMemoryStorage();
  const { getByTestId, getByText } = renderProvider(plain, secure);
  await waitFor(() => expect(getByTestId("ready").textContent).toBe("true"));

  await act(async () => { getByText("set").click(); });

  expect(await plain.getItem("wc_base")).toBe("http://host:8000");
  expect(await secure.getItem(deviceTokenKey("http://host:8000"))).toBe("tok");
  expect(getByTestId("token").textContent).toBe("tok");
  expect(getByTestId("paired").textContent).toBe("true");
});

test("clearAuth removes this host's token and marks the device unpaired", async () => {
  const plain = makeMemoryStorage();
  const secure = makeMemoryStorage();
  await plain.setItem("wc_base", "http://saved:8000");
  await secure.setItem(deviceTokenKey("http://saved:8000"), "active-tok");
  await secure.setItem(deviceTokenKey("http://other:8000"), "other-tok");

  const { getByTestId, getByText } = renderProvider(plain, secure);
  await waitFor(() => expect(getByTestId("token").textContent).toBe("active-tok"));

  await act(async () => { getByText("clear").click(); });

  expect(getByTestId("token").textContent).toBe("");
  expect(getByTestId("paired").textContent).toBe("false");
  expect(await secure.getItem(deviceTokenKey("http://saved:8000"))).toBeNull();
  expect(await secure.getItem(deviceTokenKey("http://other:8000"))).toBe("other-tok");
});

test("falls back to EXPO_PUBLIC_API_URL when plainStorage has no wc_base", async () => {
  const originalEnv = process.env;
  try {
    process.env = { ...originalEnv, EXPO_PUBLIC_API_URL: "https://api.example.com" };
    const { getByTestId } = renderProvider(makeMemoryStorage(), makeMemoryStorage());
    await waitFor(() => expect(getByTestId("ready").textContent).toBe("true"));
    expect(getByTestId("base").textContent).toBe("https://api.example.com");
  } finally {
    process.env = originalEnv;
  }
});

test("falls back to NEXT_PUBLIC_API_URL when EXPO_PUBLIC_API_URL is unset", async () => {
  const originalEnv = process.env;
  try {
    process.env = { ...originalEnv, EXPO_PUBLIC_API_URL: undefined, NEXT_PUBLIC_API_URL: "https://next.example.com" };
    const { getByTestId } = renderProvider(makeMemoryStorage(), makeMemoryStorage());
    await waitFor(() => expect(getByTestId("ready").textContent).toBe("true"));
    expect(getByTestId("base").textContent).toBe("https://next.example.com");
  } finally {
    process.env = originalEnv;
  }
});

test("prefers persisted wc_base over the environment fallback", async () => {
  const originalEnv = process.env;
  try {
    process.env = { ...originalEnv, EXPO_PUBLIC_API_URL: "https://fallback.example.com" };
    const plain = makeMemoryStorage();
    await plain.setItem("wc_base", "https://persisted.example.com");
    const { getByTestId } = renderProvider(plain, makeMemoryStorage());
    await waitFor(() => expect(getByTestId("ready").textContent).toBe("true"));
    expect(getByTestId("base").textContent).toBe("https://persisted.example.com");
  } finally {
    process.env = originalEnv;
  }
});

test("trims trailing slashes from the environment fallback", async () => {
  const originalEnv = process.env;
  try {
    process.env = { ...originalEnv, NEXT_PUBLIC_API_URL: "https://trailing.example.com///" };
    const { getByTestId } = renderProvider(makeMemoryStorage(), makeMemoryStorage());
    await waitFor(() => expect(getByTestId("ready").textContent).toBe("true"));
    expect(getByTestId("base").textContent).toBe("https://trailing.example.com");
  } finally {
    process.env = originalEnv;
  }
});

test("stream preferences are device-local and survive unpairing", async () => {
  const plain = makeMemoryStorage();
  const secure = makeMemoryStorage();
  await plain.setItem("wc_stream_preferences", JSON.stringify({
    quality: "1080", showHudOnConnect: true, haptics: true, hideRailWhilePlaying: false,
  }));

  const { getByTestId, getByText } = renderProvider(plain, secure, <PreferencesProbe />);

  await waitFor(() => expect(getByTestId("preferences").textContent).toBe(JSON.stringify({
    quality: "1080", showHudOnConnect: true, haptics: true, hideRailWhilePlaying: false,
  })));

  await act(async () => { getByText("update preferences").click(); });
  const updated = JSON.stringify({
    quality: "720", showHudOnConnect: true, haptics: false, hideRailWhilePlaying: false,
  });
  expect(await plain.getItem("wc_stream_preferences")).toBe(updated);

  await act(async () => { getByText("clear auth").click(); });
  expect(await plain.getItem("wc_stream_preferences")).toBe(updated);
});
```

- [ ] **Step 7: Run it to verify it fails**

Run: `npm test -w packages/core -- src/api/ServerContext.test.tsx`
Expected: FAIL — TypeScript reports `Property 'paired' does not exist on type 'Ctx'`.

- [ ] **Step 8: Rewrite `packages/core/src/api/ServerContext.tsx`**

Write the whole file:

```tsx
import React, { createContext, useContext, useEffect, useMemo, useState, useCallback, useRef } from "react";
import { makeClient } from "./client";
import { probeHost, type HostReachability } from "./hostProbe";
import { deviceTokenKey } from "./pairing";
import { normalizeBase } from "./urls";
import type { SecureStorageAdapter } from "./storage";
import {
  DEFAULT_STREAM_PREFERENCES,
  parseStreamPreferences,
  type StreamPreferences,
} from "./preferences";

type ApiClient = ReturnType<typeof makeClient>;

type Ctx = {
  base: string | null;
  // The device token issued by this host when the device was paired.
  authToken: string | null;
  // Whether the host accepts this device: true with a valid token or on
  // loopback, false once the host has said no, null until it has answered.
  paired: boolean | null;
  client: ApiClient | null;
  setServer: (base: string, token: string) => Promise<ApiClient>;
  clearAuth: () => Promise<void>;
  preferences: StreamPreferences;
  updatePreferences: (patch: Partial<StreamPreferences>) => Promise<void>;
  ready: boolean;
  hostReachability: HostReachability;
};
const ServerCtx = createContext<Ctx | null>(null);
const BASE_KEY = "wc_base";
const LEGACY_TOKEN_KEY = "wc_auth_token";
const PREFERENCES_KEY = "wc_stream_preferences";

export function ServerProvider({
  children,
  plainStorage,
  secureStorage,
}: {
  children: React.ReactNode;
  plainStorage: SecureStorageAdapter;
  secureStorage: SecureStorageAdapter;
}) {
  const defaultBase =
    ((typeof process !== "undefined" &&
      (process.env?.EXPO_PUBLIC_API_URL || process.env?.NEXT_PUBLIC_API_URL)) ||
      (typeof window !== "undefined" && window.location?.origin && window.location.origin !== "null" ? window.location.origin : "") ||
      "").replace(/\/+$/, "");
  const [base, setBaseState] = useState<string | null>(defaultBase || null);
  const [authToken, setAuthTokenState] = useState<string | null>(null);
  const [paired, setPaired] = useState<boolean | null>(null);
  const [baseLoaded, setBaseLoaded] = useState(false);
  const [tokenLoaded, setTokenLoaded] = useState(false);
  const [preferencesLoaded, setPreferencesLoaded] = useState(false);
  const [preferences, setPreferences] = useState<StreamPreferences>(DEFAULT_STREAM_PREFERENCES);
  const preferencesRef = useRef<StreamPreferences>(DEFAULT_STREAM_PREFERENCES);
  const baseRef = useRef<string | null>(base);
  baseRef.current = base;
  const [hostReachability, setHostReachability] = useState<HostReachability>(() => ({
    state: "checking",
    host: base ? new URL(base).host : "",
    rttMs: null,
    paired: null,
  }));

  useEffect(() => {
    plainStorage.getItem(BASE_KEY)
      .then((v) => {
        if (v) {
          setBaseState(v);
        } else if (defaultBase) {
          setBaseState(defaultBase);
        }
      })
      .finally(() => setBaseLoaded(true));
    plainStorage.getItem(PREFERENCES_KEY)
      .then((v) => {
        const loaded = parseStreamPreferences(v);
        preferencesRef.current = loaded;
        setPreferences(loaded);
      })
      .finally(() => setPreferencesLoaded(true));
    // A Supabase session token saved before pairing replaced login.
    secureStorage.deleteItem(LEGACY_TOKEN_KEY).catch(() => {});
  }, [plainStorage, secureStorage, defaultBase]);

  // The device token belongs to one host, so it loads only once the base is
  // known and reloads when the base changes.
  useEffect(() => {
    if (!baseLoaded) return;
    if (!base) {
      setAuthTokenState(null);
      setTokenLoaded(true);
      return;
    }
    let current = true;
    secureStorage.getItem(deviceTokenKey(base))
      .then((v) => { if (current) setAuthTokenState(v || null); })
      .catch(() => { if (current) setAuthTokenState(null); })
      .finally(() => { if (current) setTokenLoaded(true); });
    return () => { current = false; };
  }, [baseLoaded, base, secureStorage]);

  const clearAuth = useCallback(async () => {
    const current = baseRef.current;
    if (current) await secureStorage.deleteItem(deviceTokenKey(current));
    setAuthTokenState(null);
    setPaired(false);
  }, [secureStorage]);

  const setServer = useCallback(async (url: string, token: string) => {
    const norm = normalizeBase(url);
    await plainStorage.setItem(BASE_KEY, norm);
    if (token) {
      await secureStorage.setItem(deviceTokenKey(norm), token);
    } else {
      await secureStorage.deleteItem(deviceTokenKey(norm));
    }
    setBaseState(norm);
    setAuthTokenState(token || null);
    setPaired(token ? true : null);
    return makeClient(norm, token || null, clearAuth);
  }, [plainStorage, secureStorage, clearAuth]);

  const updatePreferences = useCallback(async (patch: Partial<StreamPreferences>) => {
    const next = { ...preferencesRef.current, ...patch };
    preferencesRef.current = next;
    setPreferences(next);
    await plainStorage.setItem(PREFERENCES_KEY, JSON.stringify(next));
  }, [plainStorage]);

  const client = useMemo(
    () => (base ? makeClient(base, authToken, clearAuth) : null),
    [base, authToken, clearAuth]
  );

  useEffect(() => {
    if (!base || !tokenLoaded) return;
    if (typeof fetch === "undefined" && typeof globalThis.fetch === "undefined") return;
    let current = true;
    const check = () => {
      probeHost(base, authToken).then((result) => {
        if (!current) return;
        setHostReachability(result);
        // An unreachable host says nothing about pairing; keep what we knew.
        if (result.paired !== null) setPaired(result.paired);
      });
    };
    setHostReachability({
      state: "checking",
      host: new URL(base).host,
      rttMs: null,
      paired: null,
    });
    check();
    const interval = setInterval(check, 30_000);
    return () => {
      current = false;
      clearInterval(interval);
    };
  }, [base, authToken, tokenLoaded]);

  const ready = baseLoaded && tokenLoaded && preferencesLoaded;
  return (
    <ServerCtx.Provider value={{ base, authToken, paired, client, setServer, clearAuth, preferences, updatePreferences, ready, hostReachability }}>
      {children}
    </ServerCtx.Provider>
  );
}

export function useServer(): Ctx {
  const c = useContext(ServerCtx);
  if (!c) throw new Error("useServer outside ServerProvider");
  return c;
}
```

- [ ] **Step 9: Update exports and delete the Supabase helpers**

```bash
git rm -q packages/core/src/api/supabaseAuth.ts packages/core/src/api/supabaseAuth.test.ts
```

In `packages/core/src/index.ts`, replace the line `export * from "./api/supabaseAuth";` with `export * from "./api/pairing";`.

- [ ] **Step 10: Run the core API tests**

Run: `npm test -w packages/core -- src/api`
Expected: all pass.

Run: `grep -rnE "supabase|Supabase|classifyHostRoute|auth/config|identity" packages/core/src/api`
Expected: no output.

- [ ] **Step 11: Commit**

```bash
git add -A packages/core/src/api packages/core/src/index.ts
git commit -m "feat(core): pair devices and track pairing per host"
```

---

### Task 7: Core WebRTC — local transport only

**Files:**
- Modify: `packages/core/src/webrtc/session.ts`, `packages/core/src/webrtc/session.test.ts`
- Modify: `packages/core/src/webrtc/whep.ts`
- Modify: `packages/core/src/webrtc/telemetry.ts`, `packages/core/src/webrtc/telemetry.test.ts`
- Modify: `packages/core/src/index.ts`, `packages/core/src/index.test.ts`
- Delete: `packages/core/src/webrtc/signaling.ts`, `packages/core/src/webrtc/signaling.test.ts`

**Interfaces:**
- Consumes: `SelectResp` without signaling fields (Task 6).
- Produces:
  - `EngineSession = { kind: "local"; stream?: any; input: InputSender; pc?: any; close: () => Promise<void> }`
  - `ConnectEngineSessionOpts = { selection; RTCImpl?; fetchImpl?; timeoutMs?; onStream?; onInputRtt?; onState?; startLocalImpl?; connectWhepImpl? }` — `authToken`, `WebSocketImpl`, `startPublicImpl`, `connectSignalingViewerImpl` are gone.
  - `waitForIceGatheringComplete(pc: any, capMs?: number): Promise<void>` — the `fastPathType` parameter is gone.
  - `StreamTelemetry.transport` is `"LAN"`; the sampler option `transport` is `"local"`.
  - `connectSignalingViewer` is no longer exported.

- [ ] **Step 1: Write the failing test**

Write the whole of `packages/core/src/webrtc/session.test.ts`:

```ts
import { connectEngineSession } from "./session";
import * as core from "../index";

const localSelection = {
  whep_url: "http://192.168.1.10:8080/whep",
  whep_token: "tokLocal",
  ice_servers: [],
} as any;

const fakeInput = () => ({ close: () => {}, send: () => {} }) as any;

describe("connectEngineSession", () => {
  test("connects over the local transport", async () => {
    const session = await connectEngineSession({
      selection: localSelection,
      startLocalImpl: async () => ({
        kind: "local",
        stream: { id: "local-vid" } as any,
        input: fakeInput(),
        close: async () => {},
      }),
    });

    expect(session.kind).toBe("local");
    expect(session.stream).toEqual({ id: "local-vid" });
  });

  test("rejects when no transport is configured", async () => {
    await expect(connectEngineSession({
      selection: { whep_url: "", whep_token: "", ice_servers: [] } as any,
    })).rejects.toThrow("No engine session transport is configured");
  });

  test("rejects with the local transport's own error", async () => {
    const states: string[] = [];
    await expect(connectEngineSession({
      selection: localSelection,
      onState: (state) => states.push(state),
      startLocalImpl: async () => { throw new Error("Local failed"); },
    })).rejects.toThrow("Local failed");
    expect(states).toEqual(["connecting"]);
  });

  test("forwards callbacks and fires disconnected on close", async () => {
    const states: string[] = [];
    const streams: any[] = [];
    const rtts: number[] = [];
    let capturedRttCb: ((ms: number) => void) | null = null;
    let capturedStateCb: ((state: any) => void) | null = null;

    const session = await connectEngineSession({
      selection: localSelection,
      onState: (st) => states.push(st),
      onStream: (st) => streams.push(st),
      onInputRtt: (ms) => rtts.push(ms),
      connectWhepImpl: async (opts) => {
        capturedRttCb = opts.onInputRtt;
        capturedStateCb = opts.onState;
        opts.onStream({ id: "local-vid" });
        return { pc: {} as any, input: fakeInput(), close: async () => {} };
      },
    });

    expect(session.kind).toBe("local");
    expect(states).toEqual(["connecting", "connected"]);
    expect(streams).toEqual([{ id: "local-vid" }]);

    capturedRttCb!(42);
    expect(rtts).toEqual([42]);

    capturedStateCb!("disconnected");
    expect(states).toEqual(["connecting", "connected", "disconnected"]);

    await session.close();
    expect(states).toEqual(["connecting", "connected", "disconnected"]);
  });

  test("close fires disconnected exactly once", async () => {
    const states: string[] = [];
    const session = await connectEngineSession({
      selection: localSelection,
      onState: (st) => states.push(st),
      connectWhepImpl: async () => ({ pc: {} as any, input: fakeInput(), close: async () => {} }),
    });
    await session.close();
    await session.close();
    expect(states).toEqual(["connecting", "connected", "disconnected"]);
  });

  test("passes the selection's WHEP details to connectWhep", async () => {
    let captured: any = null;
    const session = await connectEngineSession({
      selection: { ...localSelection, ice_servers: [{ urls: "stun:192.168.1.10:3478" }] },
      connectWhepImpl: async (opts) => {
        captured = opts;
        opts.onStream({ id: "whep-stream" });
        return { pc: {} as any, input: fakeInput(), close: async () => {} };
      },
    });

    expect(captured.whepUrl).toBe("http://192.168.1.10:8080/whep");
    expect(captured.whepToken).toBe("tokLocal");
    expect(captured.iceServers).toEqual([{ urls: "stun:192.168.1.10:3478" }]);
    expect(session.stream).toEqual({ id: "whep-stream" });
  });

  test("falls back to globalThis.RTCPeerConnection when RTCImpl is omitted", async () => {
    let capturedRTC: any = null;
    const origRTC = (globalThis as any).RTCPeerConnection;
    try {
      (globalThis as any).RTCPeerConnection = function FakeGlobalRTC() {};
      await connectEngineSession({
        selection: localSelection,
        connectWhepImpl: async (opts) => {
          capturedRTC = opts.RTCImpl;
          return { pc: {} as any, input: fakeInput(), close: async () => {} };
        },
      });
      expect(capturedRTC).toBe((globalThis as any).RTCPeerConnection);
    } finally {
      (globalThis as any).RTCPeerConnection = origRTC;
    }
  });

  test("the public signaling client is not exported", () => {
    expect((core as any).connectSignalingViewer).toBeUndefined();
  });
});
```

- [ ] **Step 2: Run it to verify it fails**

Run: `npm test -w packages/core -- src/webrtc/session.test.ts`
Expected: FAIL — `the public signaling client is not exported` fails (it is still exported).

- [ ] **Step 3: Rewrite `packages/core/src/webrtc/session.ts`**

Write the whole file:

```ts
import { connectWhep } from "./whep";
import type { InputSender } from "../input/inputChannel";
import type { SelectResp } from "../api/client";

export type SessionState = "connecting" | "connected" | "disconnected";

export type EngineSession = {
  kind: "local";
  stream?: any;
  input: InputSender;
  pc?: any;
  close: () => Promise<void>;
};

export type ConnectEngineSessionOpts = {
  selection: SelectResp;
  RTCImpl?: any;
  fetchImpl?: typeof fetch;
  timeoutMs?: number;
  onStream?: (stream: any) => void;
  onInputRtt?: (ms: number) => void;
  onState?: (state: SessionState) => void;
  startLocalImpl?: (opts: ConnectEngineSessionOpts) => Promise<EngineSession>;
  connectWhepImpl?: typeof connectWhep;
};

async function defaultStartLocal(
  opts: ConnectEngineSessionOpts,
  onStream: (stream: any) => void,
  onInputRtt: (ms: number) => void,
  onState: (state: SessionState) => void
): Promise<EngineSession> {
  const selection = opts.selection;
  let capturedStream: any = null;
  const connectFn = opts.connectWhepImpl || connectWhep;
  const whepSession = await connectFn({
    whepUrl: selection.whep_url,
    whepToken: selection.whep_token || "",
    iceServers: selection.ice_servers || [],
    RTCImpl: opts.RTCImpl || (globalThis as any).RTCPeerConnection,
    fetchImpl: opts.fetchImpl,
    timeoutMs: opts.timeoutMs,
    onStream: (stream: any) => {
      capturedStream = stream;
      onStream(stream);
    },
    onInputRtt,
    onState,
  });

  return {
    kind: "local",
    pc: whepSession.pc,
    input: whepSession.input,
    get stream() {
      return capturedStream;
    },
    close: whepSession.close,
  };
}

export async function connectEngineSession(opts: ConnectEngineSessionOpts): Promise<EngineSession> {
  if (!opts.selection?.whep_url) {
    throw new Error("No engine session transport is configured");
  }

  let lastState: SessionState | null = null;
  const safeState = (state: SessionState) => {
    if (lastState === state) return;
    lastState = state;
    opts.onState?.(state);
  };

  safeState("connecting");

  // Until the session is handed to the caller, the transport's own callbacks
  // stay private: the caller sees one "connected" and the adopted stream,
  // not the intermediate events of a negotiation that may still fail.
  let adopted = false;
  const winner = opts.startLocalImpl
    ? await opts.startLocalImpl(opts)
    : await defaultStartLocal(
        opts,
        (stream) => { if (adopted) opts.onStream?.(stream); },
        (ms) => { if (adopted) opts.onInputRtt?.(ms); },
        (state) => { if (adopted) safeState(state); }
      );
  adopted = true;

  if (winner.stream) opts.onStream?.(winner.stream);
  safeState("connected");

  return {
    kind: "local",
    pc: winner.pc,
    input: winner.input,
    get stream() {
      return winner.stream;
    },
    close: async () => {
      try {
        await winner.close();
      } finally {
        safeState("disconnected");
        adopted = false;
      }
    },
  };
}
```

- [ ] **Step 4: Delete the signaling client and its exports**

```bash
git rm -q packages/core/src/webrtc/signaling.ts packages/core/src/webrtc/signaling.test.ts
```

In `packages/core/src/index.ts`, delete the line `export * from "./webrtc/signaling";`.

Write the whole of `packages/core/src/index.test.ts`:

```ts
import { CORE_PACKAGE_READY, connectEngineSession, pairDevice } from "./index";

test("core package resolves", () => {
  expect(CORE_PACKAGE_READY).toBe(true);
  expect(typeof connectEngineSession).toBe("function");
  expect(typeof pairDevice).toBe("function");
});
```

- [ ] **Step 5: Remove the relay fast path from `packages/core/src/webrtc/whep.ts`**

Replace the whole `waitForIceGatheringComplete` function with:

```ts
export function waitForIceGatheringComplete(pc: any, capMs = 4000): Promise<void> {
  if (pc.iceGatheringState === "complete") return Promise.resolve();
  return new Promise((resolve, reject) => {
    let done = false;
    let timer: any = null;
    const cleanup = () => {
      if (timer !== null) clearTimeout(timer);
      pc.removeEventListener("icegatheringstatechange", check);
    };
    const finish = () => {
      if (done) return;
      done = true;
      cleanup();
      resolve();
    };
    const check = () => { if (pc.iceGatheringState === "complete") finish(); };
    const expire = () => {
      if (done) return;
      done = true;
      cleanup();
      reject(whepError("ice-gathering-timeout", "ICE gathering timed out"));
    };
    pc.addEventListener("icegatheringstatechange", check);
    timer = setTimeout(expire, Math.max(0, capMs));
    check();
  });
}
```

- [ ] **Step 6: Narrow the telemetry transport**

In `packages/core/src/webrtc/telemetry.ts`:
- change `  transport: "LAN" | "RELAY";` to `  transport: "LAN";`
- change `  transport: "local" | "public";` to `  transport: "local";`
- in `makeTelemetrySampler`'s parameter list, remove `transport, ` so it reads `export function makeTelemetrySampler({ pc, onSample, sampleMs = 1_000, now = Date.now }: TelemetrySamplerOptions) {`
- change `      transport: transport === "local" ? "LAN" : "RELAY",` to `      transport: "LAN",`

In `packages/core/src/webrtc/telemetry.test.ts`, in the test `"preserves unavailable metrics as null and accepts input echo RTT"`, change `transport: "public",` to `transport: "local",` and `transport: "RELAY",` to `transport: "LAN",`.

- [ ] **Step 7: Run the whole core suite**

Run: `npm run test:core`
Expected: all pass.

Run: `grep -rnE "signaling|public_session|\"public\"|RELAY|fastPathType|startPublicImpl" packages/core/src`
Expected: no output.

- [ ] **Step 8: Commit**

```bash
git add -A packages/core/src
git commit -m "refactor(core): drop the public signaling transport"
```

---

### Task 8: Shared UI — pairing screen, settings, network chip

**Files:**
- Create: `packages/ui/src/screens/Pair.tsx`, `packages/ui/src/screens/Pair.test.tsx`
- Delete: `packages/ui/src/screens/Login.tsx`, `packages/ui/src/screens/Login.test.tsx`
- Modify: `packages/ui/src/components/NetChip.tsx`, `packages/ui/src/components/NetChip.test.tsx`
- Modify: `packages/ui/src/screens/Account.tsx`, `packages/ui/src/screens/Account.test.tsx`
- Modify: `packages/ui/src/screens/InstanceList.tsx`, `packages/ui/src/screens/InstanceList.test.tsx`
- Modify: `packages/ui/src/screens/Stream.tsx`, `packages/ui/src/screens/Stream.test.tsx`
- Modify: `packages/ui/src/index.ts`

**Interfaces:**
- Consumes: `pairDevice`, `normalizeBase`, `useServer()` with `paired` / `authToken` / `hostReachability` without `route` (Task 6); `connectEngineSession` without `authToken` (Task 7).
- Produces:
  - `Pair({ navigation })` — navigates with `navigation.replace("InstanceList")` on success.
  - `NetChip({ state, host })` — no `route` prop; `RelayIdleChip` is gone.
  - Screens navigate to the route name `"Pair"` where they used `"Login"`. The route name `"Account"` is unchanged.

- [ ] **Step 1: Write the failing pairing-screen test**

Create `packages/ui/src/screens/Pair.test.tsx`:

```tsx
import React from "react";
import { Platform } from "react-native";
import { act, cleanup, fireEvent, render, waitFor } from "@testing-library/react-native";
import * as Core from "@wc/core";
import { Pair } from "./Pair";

jest.mock("@wc/core", () => ({
  ...jest.requireActual("@wc/core"),
  useServer: jest.fn(),
  pairDevice: jest.fn(),
}));

const setServer = jest.fn();
const navigation = { replace: jest.fn() };
const pairDevice = Core.pairDevice as jest.Mock;

function mockServer(overrides: Record<string, unknown> = {}) {
  (Core.useServer as jest.Mock).mockReturnValue({
    base: null,
    setServer,
    hostReachability: { state: "checking", host: "", rttMs: null, paired: null },
    ...overrides,
  });
}

beforeEach(() => {
  jest.clearAllMocks();
  setServer.mockResolvedValue({});
  pairDevice.mockResolvedValue({ token: "dev-tok" });
  mockServer();
});

afterEach(() => {
  jest.restoreAllMocks();
  cleanup();
});

test("pairs with the entered host and code, stores the token, and opens the instance list", async () => {
  const screen = await render(<Pair navigation={navigation} />);
  await fireEvent.changeText(screen.getByPlaceholderText("Host address"), "http://100.101.102.103:8080");
  await fireEvent.changeText(screen.getByPlaceholderText("Pairing code"), "123456");
  await fireEvent.press(screen.getByText("Pair"));

  await waitFor(() => expect(navigation.replace).toHaveBeenCalledWith("InstanceList"));
  expect(pairDevice).toHaveBeenCalledWith("http://100.101.102.103:8080", "123456", "iPhone");
  expect(setServer).toHaveBeenCalledWith("http://100.101.102.103:8080", "dev-tok");
});

test("a bare host gets http:// and a spaced code is sent as digits", async () => {
  const screen = await render(<Pair navigation={navigation} />);
  await fireEvent.changeText(screen.getByPlaceholderText("Host address"), " 192.168.1.8:8080/ ");
  await fireEvent.changeText(screen.getByPlaceholderText("Pairing code"), " 123 456 ");
  await fireEvent.press(screen.getByText("Pair"));

  await waitFor(() => expect(pairDevice).toHaveBeenCalledWith("http://192.168.1.8:8080", "123456", "iPhone"));
});

test("the host field starts with the saved host", async () => {
  mockServer({ base: "http://192.168.1.8:8080" });
  const screen = await render(<Pair navigation={navigation} />);
  expect(screen.getByPlaceholderText("Host address").props.value).toBe("http://192.168.1.8:8080");
});

test("a missing host or code is reported without calling the host", async () => {
  const screen = await render(<Pair navigation={navigation} />);
  await fireEvent.press(screen.getByText("Pair"));
  expect(await screen.findByText("Enter your host address")).toBeTruthy();

  await fireEvent.changeText(screen.getByPlaceholderText("Host address"), "192.168.1.8:8080");
  await fireEvent.changeText(screen.getByPlaceholderText("Pairing code"), "   ");
  await fireEvent.press(screen.getByText("Pair"));
  expect(await screen.findByText("Enter the pairing code shown on your PC")).toBeTruthy();
  expect(pairDevice).not.toHaveBeenCalled();
});

test("a rejected code shows the host's message and stays on the screen", async () => {
  pairDevice.mockResolvedValue({ error: "That code is wrong or has expired." });
  const screen = await render(<Pair navigation={navigation} />);
  await fireEvent.changeText(screen.getByPlaceholderText("Host address"), "192.168.1.8:8080");
  await fireEvent.changeText(screen.getByPlaceholderText("Pairing code"), "000000");
  await fireEvent.press(screen.getByText("Pair"));

  expect(await screen.findByText("That code is wrong or has expired.")).toBeTruthy();
  expect(setServer).not.toHaveBeenCalled();
  expect(navigation.replace).not.toHaveBeenCalled();
});

test("a second press while pairing does not send a second request", async () => {
  let finish!: (value: { token: string }) => void;
  pairDevice.mockReturnValue(new Promise((resolve) => { finish = resolve; }));
  const screen = await render(<Pair navigation={navigation} />);
  await fireEvent.changeText(screen.getByPlaceholderText("Host address"), "192.168.1.8:8080");
  await fireEvent.changeText(screen.getByPlaceholderText("Pairing code"), "123456");
  await fireEvent.press(screen.getByText("Pair"));
  await fireEvent.press(await screen.findByText("Please wait…"));
  expect(pairDevice).toHaveBeenCalledTimes(1);

  await act(async () => { finish({ token: "dev-tok" }); });
  await waitFor(() => expect(navigation.replace).toHaveBeenCalledWith("InstanceList"));
});

test("on web there is no host field and the page's own host is used", async () => {
  jest.replaceProperty(Platform, "OS", "web");
  mockServer({
    base: "http://192.168.1.8:8080",
    hostReachability: { state: "reachable", host: "192.168.1.8:8080", rttMs: 12, paired: false },
  });
  const screen = await render(<Pair navigation={navigation} />);
  expect(screen.queryByPlaceholderText("Host address")).toBeNull();
  expect(screen.getByText("LAN · 192.168.1.8:8080")).toBeTruthy();

  await fireEvent.changeText(screen.getByPlaceholderText("Pairing code"), "123456");
  await fireEvent.press(screen.getByText("Pair"));

  await waitFor(() => expect(pairDevice).toHaveBeenCalledWith("http://192.168.1.8:8080", "123456", "Browser"));
});
```

- [ ] **Step 2: Run it to verify it fails**

Run: `npm test -w packages/ui -- src/screens/Pair.test.tsx`
Expected: FAIL, `Cannot find module './Pair'`.

- [ ] **Step 3: Write `packages/ui/src/screens/Pair.tsx`**

```tsx
import React, { useState } from "react";
import { View, Text, TextInput, KeyboardAvoidingView, Platform, ScrollView } from "react-native";
import { theme } from "../theme/tokens";
import { Button } from "../components/Button";
import { BrandMark } from "../components/BrandMark";
import { NetChip } from "../components/NetChip";
import { normalizeBase, pairDevice, useServer } from "@wc/core";

function deviceName(): string {
  if (Platform.OS === "ios") return "iPhone";
  if (Platform.OS === "android") return "Android";
  return "Browser";
}

export function Pair({ navigation }: { navigation: any }) {
  const { base, setServer, hostReachability } = useServer();
  // The web app is served by the host it talks to; a native app has to be
  // told where the host is.
  const needsHost = Platform.OS !== "web";
  const [host, setHost] = useState(base ?? "");
  const [code, setCode] = useState("");
  const [focusedField, setFocusedField] = useState<string | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const submit = async () => {
    if (busy) return;
    setError("");
    const origin = typeof window !== "undefined" && window.location?.origin ? window.location.origin : "";
    const entered = needsHost ? host.trim() : (base || origin);
    if (!entered) {
      setError("Enter your host address");
      return;
    }
    const digits = code.replace(/\s+/g, "");
    if (!digits) {
      setError("Enter the pairing code shown on your PC");
      return;
    }
    const target = normalizeBase(/^https?:\/\//.test(entered) ? entered : `http://${entered}`);
    setBusy(true);
    try {
      const result = await pairDevice(target, digits, deviceName());
      if ("error" in result) {
        setError(result.error);
        return;
      }
      await setServer(target, result.token);
      navigation.replace("InstanceList");
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Pairing failed. Please try again.");
    } finally {
      setBusy(false);
    }
  };

  const fieldStyle = (field: string) => ({
    height: 50, backgroundColor: theme.color.surface, borderWidth: 1,
    borderColor: focusedField === field ? theme.color.accent : theme.color.border,
    borderRadius: theme.radius.input,
    shadowColor: theme.color.accent, shadowOpacity: focusedField === field ? 0.25 : 0,
    shadowRadius: 8, shadowOffset: { width: 0, height: 0 }, elevation: focusedField === field ? 4 : 0,
    paddingHorizontal: 16, fontSize: 15, color: theme.color.text,
  });
  const labelStyle = {
    fontFamily: theme.font.mono, fontSize: 9.5, letterSpacing: 1.3, color: theme.color.textDim, marginBottom: 7,
  };

  return (
    <KeyboardAvoidingView style={{ flex: 1, backgroundColor: theme.color.screen }} behavior={Platform.OS === "ios" ? "padding" : undefined}>
      <ScrollView keyboardShouldPersistTaps="handled" contentContainerStyle={{ flexGrow: 1, padding: 24, paddingTop: 64 }}>
        <BrandMark />
        <Text style={{ fontFamily: theme.font.bold, fontSize: 29, letterSpacing: -0.6, color: theme.color.text, marginTop: 26 }}>
          Pair this device
        </Text>
        <Text style={{ fontFamily: theme.font.regular, fontSize: 13.5, lineHeight: 21, color: theme.color.textMuted, marginTop: 9, marginBottom: 22 }}>
          On your PC, open EmuCtrl Host and click Pair device. Enter the code it shows. You only do this once per device.
        </Text>
        {needsHost ? null : (
          <View style={{ flexDirection: "row", flexWrap: "wrap", gap: 8, marginBottom: 22 }}>
            <NetChip state={hostReachability.state} host={hostReachability.host} />
          </View>
        )}
        {needsHost ? (
          <View style={{ marginBottom: 12 }}>
            <Text style={labelStyle}>HOST ADDRESS</Text>
            <TextInput value={host} onChangeText={(value) => { setHost(value); setError(""); }}
              accessibilityLabel="Host address" placeholder="Host address" placeholderTextColor={theme.color.textDim}
              autoCapitalize="none" autoCorrect={false} keyboardType="url" editable={!busy}
              onFocus={() => setFocusedField("host")} onBlur={() => setFocusedField(null)}
              style={{ ...fieldStyle("host"), fontFamily: theme.font.mono }} />
          </View>
        ) : null}
        <Text style={labelStyle}>PAIRING CODE</Text>
        <TextInput value={code} onChangeText={(value) => { setCode(value); setError(""); }}
          accessibilityLabel="Pairing code" placeholder="Pairing code" placeholderTextColor={theme.color.textDim}
          autoCapitalize="none" autoCorrect={false} keyboardType="number-pad" maxLength={12} editable={!busy}
          onFocus={() => setFocusedField("code")} onBlur={() => setFocusedField(null)}
          onSubmitEditing={() => { void submit(); }}
          style={{ ...fieldStyle("code"), fontFamily: theme.font.mono, letterSpacing: 2 }} />
        {error ? <Text accessibilityRole="alert" style={{ fontFamily: theme.font.semibold, fontSize: 13, color: theme.color.error, marginTop: 12 }}>{error}</Text> : null}
        <View style={{ marginTop: 20 }}>
          <Button label={busy ? "Please wait…" : "Pair"} onPress={() => { void submit(); }} loading={busy} />
        </View>
      </ScrollView>
    </KeyboardAvoidingView>
  );
}
```

- [ ] **Step 4: Replace the Login screen with it**

```bash
git rm -q packages/ui/src/screens/Login.tsx packages/ui/src/screens/Login.test.tsx
```

In `packages/ui/src/index.ts`, change `export * from "./screens/Login";` to `export * from "./screens/Pair";`.

- [ ] **Step 5: Simplify the network chip**

Write the whole of `packages/ui/src/components/NetChip.tsx`:

```tsx
import React, { useEffect, useRef } from "react";
import { Animated, View, Text } from "react-native";
import { theme } from "../theme/tokens";
import type { HostReachability } from "@wc/core";

type NetChipProps = {
  state: HostReachability["state"];
  host: string;
};

export function NetChip({ state, host }: NetChipProps) {
  const pulse = useRef(new Animated.Value(1)).current;
  const failed = state === "unreachable";
  const reachable = state === "reachable";
  const color = failed ? theme.color.live : reachable ? theme.color.telemetry : theme.color.textMuted;
  const backgroundColor = failed ? theme.net.disconnected.chipBg
    : reachable ? theme.net.connected.chipBg : theme.color.surfaceRaised;
  const label = `LAN · ${host}`;
  useEffect(() => {
    if (!reachable) { pulse.setValue(1); return undefined; }
    const animation = Animated.loop(Animated.sequence([
      Animated.timing(pulse, { toValue: .35, duration: 1000, useNativeDriver: true }),
      Animated.timing(pulse, { toValue: 1, duration: 1000, useNativeDriver: true }),
    ]));
    animation.start();
    return () => animation.stop();
  }, [pulse, reachable]);
  if (!host) return null;
  return (
    <View accessible accessibilityLabel={`${label}, ${state}`}
      style={{ flexDirection: "row", alignItems: "center", gap: 7, paddingHorizontal: 12, paddingVertical: 8,
        backgroundColor, borderRadius: theme.radius.pill, maxWidth: "100%" }}>
      <Animated.View style={{ width: 6, height: 6, borderRadius: 3, backgroundColor: color, opacity: pulse }} />
      <Text numberOfLines={1} style={{ flexShrink: 1, fontFamily: theme.font.mono, fontSize: 10, color }}>{label}</Text>
    </View>
  );
}
```

Write the whole of `packages/ui/src/components/NetChip.test.tsx`:

```tsx
import React from "react";
import { render } from "@testing-library/react-native";
import * as NetChipModule from "./NetChip";
import { NetChip } from "./NetChip";
import { theme } from "../theme/tokens";

test.each([
  ["reachable", theme.color.telemetry],
  ["unreachable", theme.color.live],
  ["checking", theme.color.textMuted],
] as const)("%s shows the host with its actual state", async (state, color) => {
  const screen = await render(<NetChip state={state} host="192.168.1.8:8080" />);
  expect(screen.getByText("LAN · 192.168.1.8:8080")).toHaveStyle({ color });
  expect(screen.getByLabelText(`LAN · 192.168.1.8:8080, ${state}`)).toBeTruthy();
});

test("omits a chip when there is no host to report", async () => {
  const screen = await render(<NetChip state="checking" host="" />);
  expect(screen.queryByText(/LAN/)).toBeNull();
});

test("there is no relay chip", () => {
  expect((NetChipModule as any).RelayIdleChip).toBeUndefined();
});
```

- [ ] **Step 6: Turn the Account screen into device settings**

In `packages/ui/src/screens/Account.tsx`, replace everything from `export function Account(` to the end of the file with:

```tsx
export function Account({ navigation }: { navigation: any }) {
  const { authToken, hostReachability, preferences, updatePreferences, clearAuth } = useServer() as any;
  const [unpairing, setUnpairing] = useState(false);
  const host = hostReachability?.host || null;
  const quality = preferences?.quality ?? "auto";

  const selectNextQuality = () => {
    const index = QUALITY_OPTIONS.indexOf(quality);
    void updatePreferences({ quality: QUALITY_OPTIONS[(index + 1) % QUALITY_OPTIONS.length] });
  };
  const unpair = async () => {
    if (unpairing) return;
    setUnpairing(true);
    try {
      await clearAuth();
      navigation.replace("Pair");
    } finally {
      setUnpairing(false);
    }
  };

  return (
    <View style={{ flex: 1, backgroundColor: theme.color.screen }}>
      <ScrollView contentContainerStyle={{ padding: 24, paddingTop: 56, paddingBottom: 48 }}>
        <Text style={{ fontFamily: theme.font.semibold, fontSize: 22, letterSpacing: -0.2, color: theme.color.text, marginBottom: 2 }}>Settings</Text>

        <Section title="STREAM DEFAULTS">
          <Row>
            <Pressable accessibilityRole="button" accessibilityLabel="Stream quality" onPress={selectNextQuality} style={{ minHeight: 56, flexDirection: "row", alignItems: "center", justifyContent: "space-between" }}>
              <Text style={{ fontFamily: theme.font.medium, fontSize: 14.5, color: theme.color.text }}>Default quality</Text>
              <View style={{ flexDirection: "row", alignItems: "center", gap: 8 }}><Text style={{ fontFamily: theme.font.monoMedium, fontSize: 12, color: theme.color.textMuted }}>{quality === "auto" ? "AUTO" : `${quality}P`}</Text><Text style={{ fontFamily: theme.font.mono, fontSize: 14, color: theme.color.textDim }}>›</Text></View>
            </Pressable>
          </Row>
          <Row><Toggle label="Show HUD on connect" value={Boolean(preferences?.showHudOnConnect)} onChange={(value) => void updatePreferences({ showHudOnConnect: value })} /></Row>
          <Row><Toggle label="Touch haptics" value={Boolean(preferences?.haptics)} onChange={(value) => void updatePreferences({ haptics: value })} /></Row>
        </Section>

        {host ? <Section title="HOST">
          <Row>
            <View style={{ flexDirection: "row", justifyContent: "space-between", gap: 16 }}>
              <Text style={{ fontFamily: theme.font.medium, fontSize: 14.5, color: theme.color.text }}>Current host</Text>
              <Text numberOfLines={1} style={{ flexShrink: 1, fontFamily: theme.font.mono, fontSize: 12, color: theme.color.textMuted }}>{host}</Text>
            </View>
          </Row>
        </Section> : null}

        {/* The PC's own window is trusted without a token, so it has nothing to unpair. */}
        {authToken ? <Section title="THIS DEVICE">
          <Row borderColor={theme.color.live}>
            <Pressable accessibilityRole="button" accessibilityLabel="Unpair this device" disabled={unpairing} onPress={() => { void unpair(); }} style={{ minHeight: 56, justifyContent: "center" }}>
              <Text style={{ fontFamily: theme.font.semibold, fontSize: 14, color: theme.color.live }}>{unpairing ? "Unpairing…" : "Unpair this device"}</Text>
            </Pressable>
          </Row>
          <Text style={{ marginTop: 10, fontFamily: theme.font.regular, fontSize: 12, lineHeight: 18, color: theme.color.textMuted }}>Removes this device's saved access. To use it again, pair with a new code from the PC.</Text>
        </Section> : null}
      </ScrollView>
    </View>
  );
}
```

Write the whole of `packages/ui/src/screens/Account.test.tsx`:

```tsx
import React from "react";
import { fireEvent, render, waitFor } from "@testing-library/react-native";
import * as SC from "@wc/core";
import { Account } from "./Account";

jest.mock("@wc/core", () => ({
  ...jest.requireActual("@wc/core"),
  TIER_ORDER: ["1440", "480"],
  useServer: jest.fn(),
}));

const navigation = { navigate: jest.fn(), replace: jest.fn() };

function mockServer(overrides: Record<string, unknown> = {}) {
  (SC.useServer as jest.Mock).mockReturnValue({
    authToken: "dev-tok",
    base: "http://192.168.1.8:8080",
    hostReachability: { state: "reachable", host: "192.168.1.8:8080", rttMs: 31, paired: true },
    preferences: { quality: "auto", showHudOnConnect: false, haptics: true, hideRailWhilePlaying: true },
    updatePreferences: jest.fn().mockResolvedValue(undefined),
    clearAuth: jest.fn().mockResolvedValue(undefined),
    ...overrides,
  });
}

beforeEach(() => {
  jest.clearAllMocks();
  mockServer();
});

test("shows the real host and no account identity", async () => {
  const view = await render(<Account navigation={navigation} />);
  expect(view.getByText("192.168.1.8:8080")).toBeTruthy();
  expect(view.queryByText("IDENTITY")).toBeNull();
  expect(view.queryByText("Connection route")).toBeNull();
  expect(view.queryByText(/Sign out/)).toBeNull();
});

test("unpairing clears this device's access and returns to Pair", async () => {
  const clearAuth = jest.fn().mockResolvedValue(undefined);
  const replace = jest.fn();
  mockServer({ clearAuth });
  const view = await render(<Account navigation={{ replace }} />);
  await fireEvent.press(view.getByText("Unpair this device"));
  await waitFor(() => expect(clearAuth).toHaveBeenCalled());
  expect(replace).toHaveBeenCalledWith("Pair");
});

test("a device with no token (the PC itself) has nothing to unpair", async () => {
  mockServer({ authToken: null });
  const view = await render(<Account navigation={navigation} />);
  expect(view.queryByText("Unpair this device")).toBeNull();
  expect(view.getByText("Default quality")).toBeTruthy();
});

test("stream defaults persist only the preference selected by each row", async () => {
  const updatePreferences = jest.fn().mockResolvedValue(undefined);
  mockServer({ updatePreferences });
  const view = await render(<Account navigation={navigation} />);
  await fireEvent.press(view.getByLabelText("Stream quality"));
  await fireEvent.press(view.getByLabelText("Show HUD on connect"));
  await fireEvent.press(view.getByLabelText("Touch haptics"));
  await waitFor(() => expect(updatePreferences).toHaveBeenCalledWith({ quality: "1440" }));
  expect(updatePreferences).toHaveBeenCalledWith({ showHudOnConnect: true });
  expect(updatePreferences).toHaveBeenCalledWith({ haptics: false });
});

test("quality cycling follows the core tier order", async () => {
  const updatePreferences = jest.fn().mockResolvedValue(undefined);
  mockServer({
    preferences: { quality: "480", showHudOnConnect: false, haptics: true, hideRailWhilePlaying: true },
    updatePreferences,
  });
  const view = await render(<Account navigation={navigation} />);
  await fireEvent.press(view.getByLabelText("Stream quality"));
  await waitFor(() => expect(updatePreferences).toHaveBeenCalledWith({ quality: "auto" }));
});

test("omits the host row when reachability has no real value", async () => {
  mockServer({ base: null, hostReachability: null });
  const view = await render(<Account navigation={navigation} />);
  expect(view.queryByText("Current host")).toBeNull();
  expect(view.queryByText("Host unavailable")).toBeNull();
});
```

- [ ] **Step 7: Update the instance list and stream screens**

In `packages/ui/src/screens/InstanceList.tsx`:
- change `<NetChip route={hostReachability.route} state={hostReachability.state} host={hostReachability.host} />` to `<NetChip state={hostReachability.state} host={hostReachability.host} />`
- change both `"Login"` strings (in `navigation.replace("Login")` and `navigation.navigate("Login")`) to `"Pair"`.

In `packages/ui/src/screens/InstanceList.test.tsx`:
- change `hostReachability: { route: "lan", state: "reachable", host: "actual-host:8080", rttMs: 99 } });` to `hostReachability: { state: "reachable", host: "actual-host:8080", rttMs: 99, paired: true } });`
- rename the test `"redirects to Login on 401 response and clears auth"` to `"redirects to Pair on 401 response and clears auth"` and change its last line to `expect(nav.replace).toHaveBeenCalledWith("Pair");`

In `packages/ui/src/screens/Stream.tsx`:
- change `const { client, authToken, clearAuth, preferences = ...` to `const { client, clearAuth, preferences = ...` (remove `authToken, ` only)
- in the `connectEngineSession({` call, delete the line `        authToken,`
- change the dependency array `[client, authToken, clearAuth, serial, releaseActiveDrag, navigation]` to `[client, clearAuth, serial, releaseActiveDrag, navigation]`
- change all four `"Login"` strings to `"Pair"`
- change `sampler.current = makeTelemetrySampler({ pc: s.pc, transport: s.kind, onSample: setTelemetry });` — no edit needed; `s.kind` is `"local"`.

In `packages/ui/src/screens/Stream.test.tsx`:
- in `selectResp`, delete the line `    signaling_url: "wss://relay.example.com/ws", signaling_token: null, public_session: "user1.A",`
- in the first test's `expect(connectEngineSessionSpy).toHaveBeenCalledWith(...)`, delete these three lines:

```tsx
      signaling_url: "wss://relay.example.com/ws",
      public_session: "user1.A",
```
```tsx
    authToken: "auth-tok-123",
```

- [ ] **Step 8: Run the UI suite**

Run: `npm run test:ui`
Expected: all pass.

Run: `grep -rnE "Login|RelayIdleChip|signInWithPassword|signUpWithPassword|supabase|identity\b|\.route\b|route=\{host" packages/ui/src`
Expected: no output.

- [ ] **Step 9: Commit**

```bash
git add -A packages/ui/src
git commit -m "feat(ui): replace sign-in with device pairing"
```

---

### Task 9: Web and mobile routing

**Files:**
- Create: `apps/web/src/app/pair/page.tsx`, `apps/web/src/app/pair/page.test.tsx`
- Delete: `apps/web/src/app/login/page.tsx`
- Modify: `apps/web/src/app/page.tsx`, `apps/web/src/app/page.test.tsx`
- Modify: `apps/web/src/app/instances/page.tsx`, `apps/web/src/app/instances/page.test.tsx`
- Modify: `apps/web/src/app/account/page.tsx`, `apps/web/src/app/account/page.test.tsx`
- Modify: `apps/web/src/app/stream/StreamPageClient.tsx`
- Modify: `apps/mobile/src/navigation/Root.tsx`
- Modify: `scripts/verify_all.py`

**Interfaces:**
- Consumes: `Pair` (Task 8); `useServer().paired` (Task 6); server page route `GET /pair` → `pair.html` (Task 3).
- Produces: the static export emits `pair.html` / `pair.txt` and no `login.html`.

- [ ] **Step 1: Write the failing web tests**

Write the whole of `apps/web/src/app/page.test.tsx`:

```tsx
import React from "react";
import { render } from "@testing-library/react";
import { useServer } from "@wc/core";
import RootPage from "./page";

const replaceMock = jest.fn();

jest.mock("next/navigation", () => ({
  useRouter: () => ({ replace: replaceMock, push: jest.fn() }),
}));

jest.mock("@wc/core", () => ({
  useServer: jest.fn(),
}));

describe("RootPage redirection", () => {
  beforeEach(() => {
    jest.clearAllMocks();
  });

  test("when not ready, does not navigate", () => {
    (useServer as jest.Mock).mockReturnValue({ ready: false, paired: null });
    render(<RootPage />);
    expect(replaceMock).not.toHaveBeenCalled();
  });

  test("when ready but the host has not answered yet, does not navigate", () => {
    (useServer as jest.Mock).mockReturnValue({ ready: true, paired: null });
    render(<RootPage />);
    expect(replaceMock).not.toHaveBeenCalled();
  });

  test("when ready and not paired, router.replace(\"/pair\") is called", () => {
    (useServer as jest.Mock).mockReturnValue({ ready: true, paired: false });
    render(<RootPage />);
    expect(replaceMock).toHaveBeenCalledWith("/pair");
  });

  test("when ready and paired without a token, router.replace(\"/instances\") is called", () => {
    (useServer as jest.Mock).mockReturnValue({ ready: true, paired: true, authToken: null });
    render(<RootPage />);
    expect(replaceMock).toHaveBeenCalledWith("/instances");
  });
});
```

Write the whole of `apps/web/src/app/instances/page.test.tsx`:

```tsx
import React from "react";
import { render } from "@testing-library/react";
import { useServer } from "@wc/core";
import InstancesPage from "./page";

const replaceMock = jest.fn();

jest.mock("next/navigation", () => ({
  useRouter: () => ({ replace: replaceMock, push: jest.fn() }),
}));

jest.mock("@wc/core", () => ({
  useServer: jest.fn(),
}));

jest.mock("@wc/ui", () => ({
  InstanceList: () => <div data-testid="instance-list">InstanceList</div>,
}));

describe("InstancesPage pairing gate", () => {
  beforeEach(() => {
    jest.clearAllMocks();
  });

  test.each([
    [{ ready: false, paired: null }],
    [{ ready: true, paired: null }],
  ])("while pairing is unknown (%j), renders nothing and does not navigate", (state) => {
    (useServer as jest.Mock).mockReturnValue(state);
    const { queryByTestId } = render(<InstancesPage />);
    expect(queryByTestId("instance-list")).toBeNull();
    expect(replaceMock).not.toHaveBeenCalled();
  });

  test("when not paired, redirects to /pair", () => {
    (useServer as jest.Mock).mockReturnValue({ ready: true, paired: false });
    const { queryByTestId } = render(<InstancesPage />);
    expect(queryByTestId("instance-list")).toBeNull();
    expect(replaceMock).toHaveBeenCalledWith("/pair");
  });

  test("when paired, renders InstanceList even without a token", () => {
    (useServer as jest.Mock).mockReturnValue({ ready: true, paired: true, authToken: null });
    const { getByTestId } = render(<InstancesPage />);
    expect(getByTestId("instance-list")).toBeTruthy();
    expect(replaceMock).not.toHaveBeenCalled();
  });
});
```

Write the whole of `apps/web/src/app/account/page.test.tsx`:

```tsx
import React from "react";
import { fireEvent, render } from "@testing-library/react";
import { useServer } from "@wc/core";
import AccountPage from "./page";

const replaceMock = jest.fn();

jest.mock("next/navigation", () => ({
  useRouter: () => ({ replace: replaceMock, push: jest.fn() }),
}));

jest.mock("@wc/core", () => ({ useServer: jest.fn() }));

jest.mock("@wc/ui", () => ({
  Account: ({ navigation }: any) => <button onClick={() => navigation.replace("Pair")}>Unpair this device</button>,
}));

describe("AccountPage pairing gate", () => {
  beforeEach(() => jest.clearAllMocks());

  test("when not paired, redirects to /pair", () => {
    (useServer as jest.Mock).mockReturnValue({ ready: true, paired: false });
    const { queryByText } = render(<AccountPage />);
    expect(queryByText("Unpair this device")).toBeNull();
    expect(replaceMock).toHaveBeenCalledWith("/pair");
  });

  test("when paired, renders the shared screen and maps the Pair route to /pair", () => {
    (useServer as jest.Mock).mockReturnValue({ ready: true, paired: true });
    const { getByText } = render(<AccountPage />);
    fireEvent.click(getByText("Unpair this device"));
    expect(replaceMock).toHaveBeenCalledWith("/pair");
  });
});
```

Create `apps/web/src/app/pair/page.test.tsx`:

```tsx
import React from "react";
import { fireEvent, render } from "@testing-library/react";
import PairPage from "./page";

const replaceMock = jest.fn();

jest.mock("next/navigation", () => ({
  useRouter: () => ({ replace: replaceMock, push: jest.fn() }),
}));

jest.mock("@wc/ui", () => ({
  Pair: ({ navigation }: any) => <button onClick={() => navigation.replace("InstanceList")}>Pair</button>,
}));

test("a successful pairing lands on /instances", () => {
  const { getByText } = render(<PairPage />);
  fireEvent.click(getByText("Pair"));
  expect(replaceMock).toHaveBeenCalledWith("/instances");
});
```

- [ ] **Step 2: Run them to verify they fail**

Run: `npm test -w apps/web`
Expected: FAIL — `Cannot find module './page'` for `pair/page.test.tsx`, and `/login` where `/pair` is expected.

- [ ] **Step 3: Move the login page to `/pair`**

```bash
git mv apps/web/src/app/login/page.tsx apps/web/src/app/pair/page.tsx
```

Write the whole of `apps/web/src/app/pair/page.tsx`:

```tsx
"use client";
import { Pair } from "@wc/ui";
import { useRouter } from "next/navigation";

// Screens navigate by PascalCase route name (e.g. "Pair", "InstanceList")
// which doesn't lowercase-map onto this app's actual path segments 1:1.
const ROUTE_PATH: Record<string, string> = { Pair: "/pair", InstanceList: "/instances", Account: "/account" };
const toPath = (route: string) => ROUTE_PATH[route] ?? `/${route.toLowerCase()}`;

export default function PairPage() {
  const router = useRouter();
  return (
    <Pair
      navigation={{
        navigate: (route: string) => router.push(toPath(route)),
        replace: (route: string) => router.replace(toPath(route)),
      }}
    />
  );
}
```

- [ ] **Step 4: Route the other pages on `paired`**

Write the whole of `apps/web/src/app/page.tsx`:

```tsx
"use client";
import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { useServer } from "@wc/core";

export default function RootPage() {
  const router = useRouter();
  const { ready, paired } = useServer();
  useEffect(() => {
    // paired stays null until the host has answered /pair/status; routing on
    // it rather than on a stored token is what lets the PC's own window
    // (loopback, no token) skip the pairing screen.
    if (!ready || paired === null) return;
    router.replace(paired ? "/instances" : "/pair");
  }, [ready, paired, router]);
  return null;
}
```

In each of `apps/web/src/app/instances/page.tsx`, `apps/web/src/app/account/page.tsx` and `apps/web/src/app/stream/StreamPageClient.tsx`, make the same four edits:

1. In the `ROUTE_PATH` constant, change `Login: "/login"` to `Pair: "/pair"`.
2. Change `const { ready, authToken } = useServer();` to `const { ready, paired } = useServer();`
3. Replace the effect:

```tsx
  useEffect(() => {
    if (!ready) return;
    if (!authToken) router.replace("/login");
  }, [ready, authToken, router]);
```

with:

```tsx
  useEffect(() => {
    if (!ready || paired === null) return;
    if (!paired) router.replace("/pair");
  }, [ready, paired, router]);
```

4. Change `if (!ready || !authToken) return null;` to `if (!ready || !paired) return null;`

Where a comment in those files says `(e.g. "Login", "InstanceList")`, change `"Login"` to `"Pair"`.

- [ ] **Step 5: Update the mobile navigator**

Write the whole of `apps/mobile/src/navigation/Root.tsx`:

```tsx
import React from "react";
import { createNativeStackNavigator } from "@react-navigation/native-stack";
import { RTCPeerConnection } from "react-native-webrtc";
import * as Haptics from "expo-haptics";
import { Account, Pair, InstanceList, Stream } from "@wc/ui";
import { VideoView } from "../platform/VideoView";
import { useServer } from "@wc/core";

const Stack = createNativeStackNavigator();

function StreamScreen(props: any) {
  return (
    <Stream
      {...props}
      RTCImpl={RTCPeerConnection}
      VideoView={VideoView}
      performHaptic={() => { void Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Light); }}
    />
  );
}

export function RootNavigator() {
  const { authToken } = useServer();
  // A saved token goes to the list even if the host is unreachable right
  // now; a revoked one is caught there by the 401 handler.
  const initialRoute = authToken ? "InstanceList" : "Pair";
  return (
    <Stack.Navigator screenOptions={{ headerShown: false }} initialRouteName={initialRoute}>
      <Stack.Screen name="Pair" component={Pair} />
      <Stack.Screen name="InstanceList" component={InstanceList} />
      <Stack.Screen name="Account" component={Account} />
      <Stack.Screen name="Stream" component={StreamScreen} />
    </Stack.Navigator>
  );
}
```

- [ ] **Step 6: Update the export check in `scripts/verify_all.py`**

Change:

```python
    required_pages = ["index.html", "login.html", "instances.html", "stream.html", "404.html"]
```

to:

```python
    required_pages = ["index.html", "pair.html", "instances.html", "stream.html", "404.html"]
```

and, directly below the `if (out_dir / "setup.html").exists():` block, add:

```python
    if (out_dir / "login.html").exists():
        unwanted.append("login.html (replaced by pair.html)")
```

- [ ] **Step 7: Run every JavaScript suite and the build**

Run: `npm run test:core && npm run test:ui && npm test -w apps/web`
Expected: all pass.

Run: `npm run build -w apps/web && ls apps/web/out/pair.html apps/web/out/pair.txt && ! ls apps/web/out/login.html`
Expected: the build succeeds, both `pair` files are listed, and `login.html` does not exist.

Run: `grep -rnE "\"/login\"|\"Login\"|app/login|\bLogin\b" apps/web/src apps/mobile/src apps/mobile/App.tsx`
Expected: no output.

`apps/mobile` has no test suite of its own, so `Root.tsx` is not exercised by any command above. Say so in the task report; it is covered only by the manual check in Step 8.

- [ ] **Step 8: Run the app once and pair a browser**

```bash
uv run pytest tests/ apps/desktop/ -q
```
Expected: all pass.

Then, on the Windows PC (this cannot be done on macOS — the app needs `engine.exe`): start the app, click **Pair device**, open `http://<LAN-IP>:8080` on a phone, enter the code, and confirm the instance list loads; reload the page and confirm no code is asked for; remove the device in the launcher and confirm the phone returns to the pairing screen within 30 seconds. Record the result in the task report. If no Windows PC is available, say so in the report rather than marking this verified.

- [ ] **Step 9: Commit**

```bash
git add -A apps/web/src apps/mobile/src scripts/verify_all.py
git commit -m "feat(web): route on pairing instead of login"
```

---

### Task 10: Version and documentation

**Files:**
- Modify: `src/config.py`, `pyproject.toml`, `build/installer.iss` (via `scripts/bump_version.py`)
- Modify: `README.md`, `MEMORY.md`

**Interfaces:**
- Consumes: everything above.
- Produces: `VERSION = "3.2.0"`.

- [ ] **Step 1: Bump the version**

In `src/config.py`, change `VERSION = "3.1.3"` to `VERSION = "3.2.0"`.

Run: `uv run python scripts/bump_version.py && grep -n "3\.2\.0" src/config.py pyproject.toml build/installer.iss`
Expected: one line from each of the three files.

- [ ] **Step 2: Replace the README's authentication section**

In `README.md`, delete everything from the heading `## Multi-user authentication (optional)` up to, but not including, the next line that starts with `## `. In its place write:

```markdown
## Access and pairing

EmuCtrl answers only devices on the same local network or on your Tailscale
network. Requests from any other address are refused.

A device has to be paired once before it can use the app:

1. On the PC, open EmuCtrl Host and click **Pair device**. A 6-digit code
   appears for 5 minutes.
2. On the phone or browser, open the app and enter the code. On the mobile
   app, also enter the PC's address (for example `100.101.102.103:8080`).
3. The device is remembered. Remove it from **Paired Devices** in the host
   window to revoke its access.

The window on the PC itself needs no pairing.

Traffic on the local network is plain HTTP, so anyone able to intercept
traffic on that network can read a pairing code or a device's token.
Tailscale traffic is encrypted. On networks you do not control, connect over
Tailscale.
```

Run: `grep -n "infra/terraform\|infra/vps/coturn\|infra/vps/tunnel\|infra/supabase\|SUPABASE\|TURN_\|TUNNEL_SECRET\|PUBLIC_UI_URL" README.md`
For each hit, delete that line (they are entries in the directory listing and environment-variable notes for removed pieces). Expected after edits: no output.

- [ ] **Step 3: Update `MEMORY.md`**

Replace the `- Auth Gate: ...` line with these two lines:

```markdown
- Access Gate: Requests relayed through a local proxy arrive from loopback, which skips pairing → Never front the app with a local relay; keep uvicorn `proxy_headers=False`.
- Secure Storage: expo-secure-store rejects keys outside `[A-Za-z0-9._-]` → Derive per-host keys through `deviceTokenKey`, never from a raw URL.
```

The file then holds five lessons, which is the limit.

- [ ] **Step 4: Run the build-file tests**

Run: `uv run pytest tests/test_build_files.py tests/test_config.py -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/config.py pyproject.toml build/installer.iss README.md MEMORY.md
git commit -m "docs: describe pairing and bump version to 3.2.0"
```

---

### Task 11: Tear down the VPS and delete its code

`terraform destroy` terminates a billed AWS instance and cannot be undone. **Stop and get the human's explicit confirmation at Step 2. Do not run Step 3 without it.**

**Files:**
- Delete: `infra/terraform/`, `infra/vps/coturn/`, `infra/vps/tunnel/`, `infra/supabase/`
- Keep: `infra/vps/signaling/` (the engine tests and verifiers run it as a local relay; Plan B removes it)

**Interfaces:**
- Consumes: Tasks 3 and 5 (nothing in the app uses the VPS any more).
- Produces: no running cloud resources; `infra/` contains only `vps/signaling`.

- [ ] **Step 1: Show what will be destroyed**

```bash
cd infra/terraform
terraform init -input=false
terraform state list
terraform plan -destroy -input=false
cd ../..
```

Every variable in `variables.tf` has a default, so no `-var` flags are needed.

Expected: `terraform state list` prints `data.aws_ami.ubuntu_22_04`, `aws_instance.webrtc_poc` and `aws_security_group.webrtc_poc`; the plan ends with `Plan: 0 to add, 0 to change, 2 to destroy.`

If `terraform state list` prints no `aws_instance` or `aws_security_group` line, the resources are already gone: skip to Step 4. If the plan would destroy anything other than those two resources, stop and report it. If the plan fails for missing AWS credentials, stop and ask the human to provide them; do not delete `infra/terraform`.

- [ ] **Step 2: Ask for confirmation**

Show the human the plan summary and ask, in these words: "This will permanently terminate the AWS instance and security group listed above. Proceed with terraform destroy?" Continue only on a clear yes. On anything else, skip Step 3, and in Step 4 leave `infra/terraform` in place.

- [ ] **Step 3: Destroy**

```bash
cd infra/terraform
terraform destroy -input=false -auto-approve
terraform state list
cd ../..
```

Expected: `Destroy complete! Resources: 2 destroyed.`, and `terraform state list` prints no `aws_instance` or `aws_security_group` line.

If destroy fails, stop and report the error. Do not delete `infra/terraform`: its state file is the only record of what is still running.

- [ ] **Step 4: Delete the infrastructure code**

```bash
git rm -r -q infra/vps/coturn infra/vps/tunnel infra/supabase
```

Only if Step 3 completed and the state lists no `aws_` resource (or Step 1 found none):

```bash
git rm -r -q infra/terraform
rm -rf infra/terraform
```

(The second command removes the untracked local state and provider cache, which describe infrastructure that no longer exists.)

- [ ] **Step 5: Confirm nothing references the deleted directories**

Run: `grep -rnE "infra/(terraform|supabase)|infra/vps/(coturn|tunnel)|infra\\\\vps\\\\(coturn|tunnel)" --include="*.py" --include="*.yml" --include="*.json" --include="*.md" --include="*.ps1" --include="*.bat" --include="*.spec" . | grep -vE "node_modules|\.venv|docs/superpowers|\.superpowers|CHECKLIST.md|docs/WINDOWS_MANUAL_VALIDATION.md"`
Expected: no output. (`CHECKLIST.md` and `docs/WINDOWS_MANUAL_VALIDATION.md` describe the verifier runs and are rewritten in Plan B.)

Run: `uv run pytest tests/ apps/desktop/ -q && npm run test:signaling`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add -A infra
git commit -m "chore(infra): remove VPS provisioning, TURN and tunnel"
```

- [ ] **Step 7: Report what is left for the human**

State in the final report:
- Delete the Supabase project in the Supabase dashboard (no credentials for it exist in this repo).
- On the Windows PC, remove `SUPABASE_URL`, `SUPABASE_ANON_KEY`, `SUPABASE_SERVICE_ROLE_KEY`, `SUPABASE_JWT_SECRET`, `VPS_SIGNALING_URL`, `ENGINE_PUBLIC_ICE_SERVERS`, `TURN_HOST`, `TURN_PORT`, `TURN_USERNAME`, `TURN_CREDENTIAL`, `PUBLIC_UI_URL`, `TUNNEL_SECRET` from `.env`, and delete `C:\ProgramData\EmuCtrl\install_key.bin` and `install_owner.txt`.
- Plan B is still to do: engine C++ signaling, cutover verifiers, the CI relay step, `infra/vps/signaling`, `CHECKLIST.md`.
