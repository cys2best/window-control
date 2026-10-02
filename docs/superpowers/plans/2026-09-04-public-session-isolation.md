# Public Session Isolation via Account-Verified Relay Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the shared, copyable HMAC secret that currently authorizes public-relay signaling (accidentally colliding across PC installs, and forgeable by anyone who has the one global secret) with account-verified access: viewers present their own live Supabase login, engines present a signature from a per-install Ed25519 keypair whose public half is registered to the owning account. Remove `device_links` per-instance linking, which solves a different, no-longer-applicable problem.

**Architecture:** Each PC generates its own Ed25519 keypair once (private key never leaves the machine). On login, FastAPI registers the public half against the authenticated account in a new Supabase `installs` table. The VPS relay (`infra/vps/signaling/server.js`) verifies a connecting viewer's own Supabase JWT directly (via JWKS, same ES256 check FastAPI already does), and verifies a connecting engine's registration signature against the `installs` row for the session's declared account. Session ids become `f"{user_id}.{instance_name}"`. The C++ engine binary is unchanged — it only ever forwards strings FastAPI hands it via env vars.

**Tech Stack:** Python/FastAPI (`src/`), `cryptography` (Ed25519, already a transitive dependency of `pyjwt[crypto]`), C++ (`engine/`, passthrough only, no changes to its crypto), Node.js relay (`infra/vps/signaling/`, new `jose` dependency for JWKS), Supabase Postgres (new `installs` table, removed `device_links` table).

**Spec:** `docs/superpowers/specs/2026-09-04-public-session-isolation-design.md`

## Global Constraints

- Python: `uv run pytest tests/ -v` / `uv run python`, never bare `python`/`pytest`.
- Commit messages: `<type>(optional-scope): imperative description`. No task/plan identifiers, no AI attribution, no "Co-Authored-By" trailers.
- After any frontend JS/CSS edit, bump `VERSION` in `src/config.py`.
- A newly created file under `docs/` needs `git add -f` (gitignored by default).
- Writable-path fallback order used throughout this repo:
  `[r"C:\ProgramData\WindowControl", r"C:\Windows\Temp", r"C:\Temp", "/tmp"]`.
- Supabase JWT verification settings (must match exactly, both in Python and in the new Node relay code): JWKS URL `{SUPABASE_URL}/auth/v1/.well-known/jwks.json`, `algorithms=["ES256"]`, `audience="authenticated"`.
- Engine tokens use JWT header `{"alg": "EdDSA", "typ": "JWT"}`; payload `{"session", "role", "exp", "jti"}`, base64url-no-pad encoded segments, same shape `header_b64.payload_b64.signature_b64` as the scheme it replaces.
- `engine/` (C++) is Windows-only and has never been built from this macOS session — any task touching it can only be verified by inspection here; real verification is the final manual task.

---

## Task 1: Remove per-instance linking (`device_links`)

Independent of every other task — deletes a mechanism the project no longer needs before the new one is built into the same files.

**Files:**
- Modify: `src/server/app.py:198-243` (drop `SupabaseClient` construction, `_supabase_call`, `_authorize_instance_access`'s body and every call site), `src/server/app.py:302-310` (`GET /instances`), `src/server/app.py:312-326` (delete link/unlink routes), `src/server/app.py:397-405` (`GET /windows`)
- Delete: `src/server/supabase_client.py`, `tests/test_supabase_client.py`, `infra/supabase/device_links.sql`
- Modify: `src/gui/supabase_login.py:4`
- Modify: `tests/test_app_auth.py`

**Interfaces:**
- Consumes: nothing from other tasks.
- Produces: `GET /instances` and `GET /windows` return every discovered instance to any authenticated caller. Instance-scoped routes (`select`, `keyframe`, `quality`, `preview`, legacy `/select`) no longer call any authorization helper beyond the existing `_auth_gate` middleware. Later tasks (2-5) build on this simplified `app.py`.

- [ ] **Step 1: Read the current state of the four files being touched**

Read `src/server/app.py:195-330` and `:390-420`, `src/gui/supabase_login.py:1-10` to confirm line numbers match this plan before editing (a prior task in a different session could have shifted them).

- [ ] **Step 2: Simplify `app.py`'s auth/instance-access plumbing**

Replace lines 198-243 (from `app = FastAPI()` through the end of `_auth_gate`) with:

```python
    app = FastAPI()

    @app.middleware("http")
    async def _auth_gate(request: Request, call_next):
        request.state.user = None
        path = request.url.path
        # This middleware runs before Starlette's router does path matching,
        # so a naive "not exempt -> require auth" check would 401 requests
        # to *nonexistent* routes too (e.g. the removed POST /login) instead
        # of letting them fall through to the router's normal 404. Only gate
        # paths that actually resolve to a registered route.
        if auth.auth_enabled() and path not in _AUTH_EXEMPT_PATHS \
                and not path.startswith("/static/") \
                and any(route.matches(request.scope)[0] != Match.NONE
                         for route in app.router.routes):
            user = current_user(request)
            if user is None:
                return JSONResponse(
                    {"detail": "Not authenticated"}, status_code=401,
                    headers={"WWW-Authenticate": "Bearer"},
                )
            request.state.user = user
        return await call_next(request)
```

(This drops the `supabase = SupabaseClient(...)` line, the `_supabase_call` helper, and `_authorize_instance_access` entirely — being a valid authenticated request is now sufficient for every instance route.)

- [ ] **Step 3: Remove the now-dead `SupabaseClient`/`SupabaseUnavailable` import**

At the top of `app.py`, delete the line importing `SupabaseClient`/`SupabaseUnavailable` from `server.supabase_client`.

- [ ] **Step 4: Simplify `GET /instances` and `GET /windows`**

Replace (around line 302-310):

```python
    @app.get("/instances")
    async def get_instances(request: Request):
        instances = instance_manager.list_instances()
        if not auth.auth_enabled():
            return instances
        user = request.state.user
        linked = await _supabase_call(supabase.list_linked_instance_ids, user.user_id)
        linked_ids = set(linked)
        return [i for i in instances if i["id"] in linked_ids]
```

with:

```python
    @app.get("/instances")
    async def get_instances(request: Request):
        return instance_manager.list_instances()
```

And the legacy `GET /windows` (around line 397-405), same shape, with:

```python
    @app.get("/windows")
    async def get_windows(request: Request):
        return instance_manager.list_instances()
```

- [ ] **Step 5: Delete the link/unlink routes**

Delete the `POST /instances/{instance_id}/link` and `DELETE /instances/{instance_id}/link` handlers (the block right after the new `GET /instances`).

- [ ] **Step 6: Remove every `_authorize_instance_access` call site**

Search `app.py` for `await _authorize_instance_access(request, ` and delete each such line (they appear in `select_instance`, `request_keyframe`, `set_instance_quality`, `instance_preview`, and the legacy `/select` and `/windows`-adjacent handlers). Leave the rest of each function body unchanged — the existing "instance not found" 404 checks stay exactly as they are.

- [ ] **Step 7: Delete `supabase_client.py`, its test file, and the SQL file**

```bash
git rm src/server/supabase_client.py tests/test_supabase_client.py infra/supabase/device_links.sql
```

- [ ] **Step 8: Fix the stale docstring in `src/gui/supabase_login.py`**

Read line 4, then replace the sentence referencing `/instances/{id}/link` as a not-yet-wired TODO with a plain statement that this dialog only handles login/register — instance access itself needs no client-side action beyond being logged in.

- [ ] **Step 9: Rewrite `tests/test_app_auth.py`'s ownership-related tests**

Delete these tests entirely (they test a mechanism that no longer exists): `test_valid_jwt_unlocks_instances_and_filters_by_device_links`, `test_link_instance_succeeds_when_unclaimed`, `test_link_instance_conflict_when_already_claimed`, `test_link_unknown_instance_404s`, `test_unlink_instance`, `test_supabase_unavailable_on_instances_fails_closed_401`, `test_select_instance_403s_when_not_linked`, `test_set_instance_quality_403s_when_not_linked`, `test_request_keyframe_403s_when_not_linked`, `test_instance_preview_403s_when_not_linked`, `test_scoped_routes_allow_access_when_instance_is_linked`, `test_legacy_select_403s_when_not_linked_using_raw_id`.

Replace them with:

```python
def test_any_authenticated_user_sees_every_discovered_instance():
    client, im, _ = _make_authed_client(
        instances=[{"id": "adb:a", "serial": "a"}, {"id": "adb:b", "serial": "b"}]
    )

    r = client.get("/instances", headers={"Authorization": f"Bearer {_jwt()}"})

    assert r.status_code == 200
    assert [i["id"] for i in r.json()] == ["adb:a", "adb:b"]


def test_scoped_routes_no_longer_check_ownership():
    client, im, _ = _make_authed_client()
    im.get.return_value = MagicMock(id="adb:a", serial="a", name="i0")
    im.select.return_value = None  # short-circuit to 503 after passing authz

    r = client.post("/instances/adb:a/select", headers={"Authorization": f"Bearer {_jwt()}"})

    # 503 (engine not ready), not 401/403 -- proves the route only requires
    # a valid JWT now, no per-instance ownership check.
    assert r.status_code == 503


def test_legacy_select_no_longer_checks_ownership():
    client, im, _ = _make_authed_client()
    im.get.return_value = MagicMock(id="adb:a", serial="a", name="i0")
    im.select.return_value = None

    r = client.post(
        "/select", json={"id": "adb:a"}, headers={"Authorization": f"Bearer {_jwt()}"}
    )

    assert r.status_code == 503
```

Update `_make_authed_client` (around line 25-41): remove the `supabase=None` parameter and the `patch("server.app.SupabaseClient", return_value=supabase)` context manager (the name no longer exists in `app.py`), and stop returning `supabase` — it now returns `(TestClient, MagicMock)`. Update every remaining call site in the file (e.g. `test_protected_route_rejected_without_token`, `test_malformed_or_wrong_bearer_is_rejected`, etc.) to unpack two values instead of three.

- [ ] **Step 10: Run the full test suite**

Run: `uv run pytest tests/ -v`
Expected: PASS (aside from the two pre-existing unrelated failures documented in `HANDOFF.md` — `test_windows_verifier.py` env-var pollution).

- [ ] **Step 11: Commit**

```bash
git add src/server/app.py src/gui/supabase_login.py tests/test_app_auth.py
git rm src/server/supabase_client.py tests/test_supabase_client.py infra/supabase/device_links.sql
git commit -m "refactor(auth): remove per-instance device linking"
```

---

## Task 2: Per-install Ed25519 keypair + owner cache

**Files:**
- Create: `src/server/install_identity.py`
- Test: `tests/test_install_identity.py`

**Interfaces:**
- Consumes: nothing from other tasks.
- Produces: `get_or_create_install_keypair() -> tuple[Ed25519PrivateKey, str]` (private key object, base64url-no-pad-encoded public key string). `get_cached_owner_user_id() -> str | None`. `set_cached_owner_user_id(user_id: str) -> None`. Tasks 3-5 depend on all three.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_install_identity.py`:

```python
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from server import install_identity


def test_get_or_create_install_keypair_persists_and_is_reused(tmp_path, monkeypatch):
    monkeypatch.setattr(install_identity, "_CANDIDATE_DIRS", [str(tmp_path)])

    private_key, public_key = install_identity.get_or_create_install_keypair()
    private_key2, public_key2 = install_identity.get_or_create_install_keypair()

    assert isinstance(private_key, Ed25519PrivateKey)
    assert public_key == public_key2
    assert os.path.isfile(tmp_path / "install_key.bin")


def test_get_or_create_install_keypair_falls_back_in_memory_when_unwritable(tmp_path, monkeypatch):
    blocked = tmp_path / "not_a_directory"
    blocked.write_text("i am a file, not a dir")
    monkeypatch.setattr(install_identity, "_CANDIDATE_DIRS", [str(blocked / "sub")])

    private_key, public_key = install_identity.get_or_create_install_keypair()

    assert isinstance(private_key, Ed25519PrivateKey)
    assert isinstance(public_key, str) and public_key


def test_get_cached_owner_user_id_returns_none_when_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(install_identity, "_CANDIDATE_DIRS", [str(tmp_path)])

    assert install_identity.get_cached_owner_user_id() is None


def test_set_then_get_cached_owner_user_id_roundtrips(tmp_path, monkeypatch):
    monkeypatch.setattr(install_identity, "_CANDIDATE_DIRS", [str(tmp_path)])

    install_identity.set_cached_owner_user_id("user-123")

    assert install_identity.get_cached_owner_user_id() == "user-123"


def test_set_cached_owner_user_id_falls_back_silently_when_unwritable(tmp_path, monkeypatch):
    blocked = tmp_path / "not_a_directory"
    blocked.write_text("i am a file, not a dir")
    monkeypatch.setattr(install_identity, "_CANDIDATE_DIRS", [str(blocked / "sub")])

    install_identity.set_cached_owner_user_id("user-123")  # must not raise

    assert install_identity.get_cached_owner_user_id() is None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_install_identity.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'server.install_identity'`

- [ ] **Step 3: Write `src/server/install_identity.py`**

```python
"""Per-install identity: a locally-persisted Ed25519 keypair that proves
"this specific PC" to the public signaling relay, and a cache of which
Supabase account most recently authenticated against this install.

Neither the private key nor the owner cache is ever transmitted anywhere.
Only the *public* key half is uploaded (once per login) to Supabase's
`installs` table (see src/server/supabase_client.py) -- the private key
never leaves this machine.
"""

import base64
import os

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

_CANDIDATE_DIRS = [
    r"C:\ProgramData\WindowControl", r"C:\Windows\Temp", r"C:\Temp", "/tmp",
]
_KEY_FILENAME = "install_key.bin"
_OWNER_FILENAME = "install_owner.txt"


def _b64url_no_pad(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _read_first_existing(filename: str) -> bytes | None:
    for directory in _CANDIDATE_DIRS:
        path = os.path.join(directory, filename)
        try:
            with open(path, "rb") as f:
                return f.read()
        except Exception:
            continue
    return None


def _write_first_writable(filename: str, data: bytes) -> bool:
    for directory in _CANDIDATE_DIRS:
        try:
            os.makedirs(directory, exist_ok=True)
            path = os.path.join(directory, filename)
            with open(path, "wb") as f:
                f.write(data)
            return True
        except Exception:
            continue
    return False


def get_or_create_install_keypair() -> tuple[Ed25519PrivateKey, str]:
    """Return (private_key, base64url-no-pad public key).

    Persists the private key the first time it's called; every later call,
    in this or a future process, reads the same one back. Falls back to an
    in-memory keypair (not persisted) if no candidate directory is
    writable -- the public path just won't survive a restart until a
    writable path exists.
    """
    existing = _read_first_existing(_KEY_FILENAME)
    if existing is not None:
        private_key = Ed25519PrivateKey.from_private_bytes(existing)
    else:
        private_key = Ed25519PrivateKey.generate()
        raw = private_key.private_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PrivateFormat.Raw,
            encryption_algorithm=serialization.NoEncryption(),
        )
        _write_first_writable(_KEY_FILENAME, raw)

    public_raw = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return private_key, _b64url_no_pad(public_raw)


def get_cached_owner_user_id() -> str | None:
    raw = _read_first_existing(_OWNER_FILENAME)
    if raw is None:
        return None
    value = raw.decode("utf-8").strip()
    return value or None


def set_cached_owner_user_id(user_id: str) -> None:
    _write_first_writable(_OWNER_FILENAME, user_id.encode("utf-8"))
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_install_identity.py -v`
Expected: PASS (5 passed)

- [ ] **Step 5: Run the full suite to confirm no regressions**

Run: `uv run pytest tests/ -v`
Expected: same pass count as Task 1's end state, plus 5 new passes.

- [ ] **Step 6: Commit**

```bash
git add src/server/install_identity.py tests/test_install_identity.py
git commit -m "feat(auth): add per-install Ed25519 keypair and owner cache"
```

---

## Task 3: Ed25519-signed engine registration token

**Files:**
- Modify: `src/server/engine_auth.py`, `src/server/engine_runtime.py:62-75,344-353`, `src/server/engine_orchestrator.py:17-30`, `src/main.py:101-117`, `src/config.py:28`
- Test: `tests/test_engine_auth.py`, `tests/test_engine_runtime.py`, `tests/test_engine_orchestrator.py`

**Interfaces:**
- Consumes: `install_identity.get_or_create_install_keypair()` (Task 2).
- Produces: `EngineTokenIssuer(whep_secret, signaling_private_key=None, ...)`. `EngineTokenIssuer.engine_token(session: str) -> str` (replaces `signaling(instance_name, role, user_id=None)`). `EngineRuntimeConfig.signaling_private_key: Ed25519PrivateKey | None` (replaces `signaling_secret: str`). Task 5 builds the `session` string this consumes; for now `_build_env_locked()` still passes `self.instance_name` as the session (Task 5 upgrades it to `f"{owner}.{instance_name}"`).

- [ ] **Step 1: Read the current files**

Read `src/server/engine_auth.py` in full, `src/server/engine_runtime.py:62-75` and `:344-353`, `src/server/engine_orchestrator.py:17-30`, `src/main.py:101-117`, `src/config.py:25-30` to confirm line numbers.

- [ ] **Step 2: Rewrite `tests/test_engine_auth.py` for the new API**

Replace the whole file:

```python
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

import base64
import hashlib
import hmac
import json

import pytest
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from server.engine_auth import EngineTokenIssuer


def _b64url_decode(segment: str) -> bytes:
    padding = "=" * (-len(segment) % 4)
    return base64.urlsafe_b64decode(segment + padding)


def decode_and_verify_eddsa(token: str, public_key) -> dict:
    header_b64, payload_b64, signature_b64 = token.split(".")
    signing_input = f"{header_b64}.{payload_b64}".encode()
    public_key.verify(_b64url_decode(signature_b64), signing_input)  # raises on mismatch
    return json.loads(_b64url_decode(payload_b64))


def test_whep_token_matches_cpp_fixture():
    issuer = EngineTokenIssuer("secret", clock=lambda: 1_700_000_000)
    token = issuer.whep("instance0")
    payload = "1700000300.instance0"
    expected = hmac.new(b"secret", payload.encode(), hashlib.sha256).hexdigest()
    assert token == f"{payload}.{expected}"


def test_whep_tokens_are_minted_from_the_current_clock_each_time():
    now = iter([1000.0, 1010.0])
    issuer = EngineTokenIssuer("whep", clock=lambda: next(now))
    first = issuer.whep("instance0")
    second = issuer.whep("instance0")
    assert first.split(".", 1)[0] == "1300"
    assert second.split(".", 1)[0] == "1310"


def test_engine_token_payload_contains_session_role_and_expiry():
    private_key = Ed25519PrivateKey.generate()
    issuer = EngineTokenIssuer("whep", private_key, clock=lambda: 1000.0)
    payload = decode_and_verify_eddsa(issuer.engine_token("user-1.instance0"), private_key.public_key())
    assert payload == {
        "session": "user-1.instance0", "role": "engine", "exp": 605800, "jti": "1",
    }


def test_engine_token_issuances_are_unique_and_verifiable():
    private_key = Ed25519PrivateKey.generate()
    issuer = EngineTokenIssuer("whep", private_key, clock=lambda: 1000.0)

    first = issuer.engine_token("user-1.instance0")
    second = issuer.engine_token("user-1.instance0")

    assert first != second
    first_claims = decode_and_verify_eddsa(first, private_key.public_key())
    second_claims = decode_and_verify_eddsa(second, private_key.public_key())
    assert first_claims["jti"] != second_claims["jti"]


def test_engine_token_rejects_tampering():
    private_key = Ed25519PrivateKey.generate()
    issuer = EngineTokenIssuer("whep", private_key, clock=lambda: 1000.0)
    token = issuer.engine_token("user-1.instance0")
    header_b64, payload_b64, signature_b64 = token.split(".")
    tampered_payload = base64.urlsafe_b64encode(b'{"session":"attacker.instance0","role":"engine","exp":605800,"jti":"1"}').rstrip(b"=").decode()
    tampered = f"{header_b64}.{tampered_payload}.{signature_b64}"
    with pytest.raises(InvalidSignature):
        decode_and_verify_eddsa(tampered, private_key.public_key())


def test_no_signaling_key_returns_empty_token_for_trusted_dev():
    issuer = EngineTokenIssuer("whep", None, clock=lambda: 1000.0)
    assert issuer.engine_token("instance0") == ""


def test_empty_whep_secret_is_invalid():
    with pytest.raises(ValueError):
        EngineTokenIssuer("", clock=lambda: 1000.0)
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run pytest tests/test_engine_auth.py -v`
Expected: FAIL (`EngineTokenIssuer.__init__() got an unexpected keyword argument` / `AttributeError: 'EngineTokenIssuer' object has no attribute 'engine_token'`)

- [ ] **Step 4: Rewrite `src/server/engine_auth.py`**

Replace the whole file:

```python
"""Mint short-lived WHEP capability tokens and a long-lived, Ed25519-signed
engine registration token for the C++ engine.

WHEP tokens are a compact HMAC scheme that must byte-match the C++ engine's
verifier: `"{expiry}.{instance_name}.{hmac_sha256_hex}"` signed with
HMAC-SHA256 over the WHEP secret. Local/LAN-only path, never touches the
shared relay -- unaffected by anything below.

Engine registration tokens are compact JWTs (UTF-8, unpadded base64url,
minified JSON), signed with this install's own Ed25519 private key
(src/server/install_identity.py) instead of a secret shared across every
install on the relay -- see docs/superpowers/specs/2026-09-04-public-session-isolation-design.md.
The VPS relay (infra/vps/signaling/server.js) verifies the signature against
the public key registered for the session's account in Supabase's
`installs` table. A missing private key (signaling_private_key=None)
disables signing entirely (trusted local/dev relay) and callers get back an
empty string.
"""

import base64
import hashlib
import hmac
import json
import threading
import time
from typing import Callable

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


def _b64url_no_pad(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


class EngineTokenIssuer:
    def __init__(
        self,
        whep_secret: str,
        signaling_private_key: Ed25519PrivateKey | None = None,
        whep_ttl_seconds: int = 300,
        engine_ttl_seconds: int = 604800,
        clock: Callable[[], float] = time.time,
    ):
        if not whep_secret:
            raise ValueError("whep_secret must not be empty")
        self._whep_secret = whep_secret
        self._signaling_private_key = signaling_private_key
        self._whep_ttl_seconds = whep_ttl_seconds
        self._engine_ttl_seconds = engine_ttl_seconds
        self._clock = clock
        self._lock = threading.Lock()
        self._last_whep_expiry = 0
        self._jwt_issuance = 0

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

    def engine_token(self, session: str) -> str:
        """Sign a registration token for `session` (the full relay session
        id, e.g. "{owner_user_id}.{instance_name}"). Role is always
        "engine" -- viewers now present their own Supabase access token to
        the relay directly, they no longer need one of these."""
        if self._signaling_private_key is None:
            return ""

        expiry = int(self._clock()) + self._engine_ttl_seconds

        header = {"alg": "EdDSA", "typ": "JWT"}
        with self._lock:
            self._jwt_issuance += 1
            jti = str(self._jwt_issuance)
        payload = {"session": session, "role": "engine", "exp": expiry, "jti": jti}

        header_b64 = _b64url_no_pad(
            json.dumps(header, separators=(",", ":")).encode("utf-8")
        )
        payload_b64 = _b64url_no_pad(
            json.dumps(payload, separators=(",", ":")).encode("utf-8")
        )
        signing_input = f"{header_b64}.{payload_b64}".encode("utf-8")
        signature = _b64url_no_pad(self._signaling_private_key.sign(signing_input))
        return f"{header_b64}.{payload_b64}.{signature}"
```

- [ ] **Step 5: Run the engine_auth tests**

Run: `uv run pytest tests/test_engine_auth.py -v`
Expected: PASS (7 passed)

- [ ] **Step 6: Update `EngineRuntimeConfig` and `_build_env_locked()` in `engine_runtime.py`**

In the `EngineRuntimeConfig` dataclass (around line 62-75), replace the `signaling_secret: str` field with `signaling_private_key: "Ed25519PrivateKey | None"`, and add the import at the top of the file:

```python
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
```

In `_build_env_locked()` (around line 344-353), replace:

```python
            "ENGINE_SIGNALING_TOKEN": self._token_issuer.signaling(
                self.instance_name, "engine"
            ),
```

with:

```python
            "ENGINE_SIGNALING_TOKEN": self._token_issuer.engine_token(
                self.instance_name  # Task 5 upgrades this to "{owner}.{instance_name}"
            ),
```

- [ ] **Step 7: Update `test_engine_runtime.py`'s `CountingTokenIssuer` fake and `make_config()`**

Replace the `CountingTokenIssuer` class (lines 28-51):

```python
class CountingTokenIssuer:
    """Mints distinguishable, monotonically-numbered tokens.

    whep() -> "whep:<instance>:<n>"; engine_token() -> "engine:<session>:<n>".
    """

    def __init__(self):
        self.counter = 0
        self.whep_calls: list[str] = []
        self.engine_token_calls: list[str] = []

    def _next(self) -> int:
        self.counter += 1
        return self.counter

    def whep(self, instance_name: str) -> str:
        self.whep_calls.append(instance_name)
        return f"whep:{instance_name}:{self._next()}"

    def engine_token(self, session: str) -> str:
        self.engine_token_calls.append(session)
        return f"engine:{session}:{self._next()}"
```

In `make_config()` (around line 222-232), replace `signaling_secret="signal-secret",` with:

```python
        signaling_private_key=Ed25519PrivateKey.generate(),
```

and add the import at the top of the file: `from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey`.

- [ ] **Step 8: Fix the tests that referenced the old viewer-token API**

`decode_role` (line 54-56) still works unchanged (splits on the first `:`). Update `test_start_launches_generation_zero_before_engine_and_mints_engine_jwt` (line 323-330) — no change needed, it already only asserts `decode_role(...) == "engine"`. Delete `test_select_passes_user_id_to_signaling_token` (lines 345-352) — viewer tokens no longer exist; this test's behavior is superseded by Task 5. Leave `test_select_mints_fresh_whep_and_viewer_tokens_without_admin_port` (lines 333-342) as-is for now — Task 5 rewrites it when it removes viewer-token minting from `select()` entirely.

- [ ] **Step 9: Update `engine_orchestrator.py` and its test**

In `src/server/engine_orchestrator.py:28-30`, replace:

```python
        self._token_issuer = EngineTokenIssuer(
            config.whep_secret, config.signaling_secret
        )
```

with:

```python
        self._token_issuer = EngineTokenIssuer(
            config.whep_secret, config.signaling_private_key
        )
```

In `tests/test_engine_orchestrator.py`'s `make_config()` (around line 77-85), replace `signaling_secret="signaling-secret",` with `signaling_private_key=Ed25519PrivateKey.generate(),` and add the same import as Step 7.

- [ ] **Step 10: Update `main.py`'s `build_engine_orchestrator()` and `config.py`**

In `src/config.py:28`, delete the line `ENGINE_SIGNALING_SECRET = os.environ.get("ENGINE_SIGNALING_SECRET", "")` — replaced by the locally-generated keypair, no longer an env var.

In `src/main.py:101-117`, replace:

```python
    runtime_config = EngineRuntimeConfig(
        exe_path=exe_path,
        whep_secret=secrets.token_hex(32),
        signaling_url=config.VPS_SIGNALING_URL or "",
        signaling_secret=config.ENGINE_SIGNALING_SECRET,
        local_ice_servers=config.ENGINE_LOCAL_ICE_SERVERS,
        public_ice_servers=config.ENGINE_PUBLIC_ICE_SERVERS,
    )
```

with:

```python
    from server import install_identity

    signaling_url = config.VPS_SIGNALING_URL or ""
    signaling_private_key = None
    if signaling_url and not config.SUPABASE_URL:
        _log(
            "[config] VPS_SIGNALING_URL is set but SUPABASE_URL isn't -- "
            "the public signaling path needs a real account to route by, "
            "keeping it disabled. Set SUPABASE_URL to enable it."
        )
        signaling_url = ""
    elif signaling_url:
        signaling_private_key, _ = install_identity.get_or_create_install_keypair()

    runtime_config = EngineRuntimeConfig(
        exe_path=exe_path,
        whep_secret=secrets.token_hex(32),
        signaling_url=signaling_url,
        signaling_private_key=signaling_private_key,
        local_ice_servers=config.ENGINE_LOCAL_ICE_SERVERS,
        public_ice_servers=config.ENGINE_PUBLIC_ICE_SERVERS,
    )
```

- [ ] **Step 11: Run the full test suite**

Run: `uv run pytest tests/ -v`
Expected: PASS (aside from the two documented pre-existing unrelated failures).

- [ ] **Step 12: Commit**

```bash
git add src/server/engine_auth.py src/server/engine_runtime.py \
        src/server/engine_orchestrator.py src/main.py src/config.py \
        tests/test_engine_auth.py tests/test_engine_runtime.py tests/test_engine_orchestrator.py
git commit -m "feat(auth): sign engine registration tokens with this install's Ed25519 key"
```

---

## Task 4: Supabase `installs` registry + ownership upsert on login

**Files:**
- Create: `infra/supabase/installs.sql`, `src/server/supabase_client.py` (recreated), `tests/test_supabase_client.py` (recreated)
- Modify: `src/server/app.py` (auth gate)
- Test: `tests/test_app_auth.py`

**Interfaces:**
- Consumes: `install_identity.get_or_create_install_keypair()`, `get_cached_owner_user_id()`, `set_cached_owner_user_id()` (Task 2).
- Produces: `SupabaseClient.upsert_install(user_id: str, public_key: str) -> None`. Task 8 (Node relay) reads the `installs` table this creates.

- [ ] **Step 1: Write `infra/supabase/installs.sql`**

```sql
-- infra/supabase/installs.sql
-- Run once against the project's Supabase Postgres (SQL editor or
-- `supabase db push`). Tracks which account owns which physical PC
-- install, keyed by that install's own Ed25519 public key (generated and
-- persisted locally by src/server/install_identity.py -- the private key
-- never leaves the machine or reaches this table). FastAPI is the only
-- writer/reader, using the service-role key, same trust boundary as every
-- other server-side write in this repo.

create table if not exists installs (
    public_key text primary key,
    user_id uuid not null references auth.users(id) on delete cascade,
    updated_at timestamptz not null default now()
);

alter table installs enable row level security;

create policy "users manage their own installs"
    on installs
    for all
    using (auth.uid() = user_id)
    with check (auth.uid() = user_id);
```

- [ ] **Step 2: Write the failing tests for `SupabaseClient`**

Create `tests/test_supabase_client.py`:

```python
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

import httpx
import pytest
from unittest.mock import patch

from server.supabase_client import SupabaseClient, SupabaseUnavailable

BASE = "https://project.supabase.co"


@pytest.fixture
def client():
    return SupabaseClient(BASE, "service-role-key")


def test_upsert_install_posts_with_merge_on_conflict(client):
    with patch("server.supabase_client.httpx.post") as mock_post:
        mock_post.return_value = httpx.Response(
            201, json=[{"public_key": "pub-1", "user_id": "user-1"}],
            request=httpx.Request("POST", f"{BASE}/rest/v1/installs"),
        )

        client.upsert_install("user-1", "pub-1")

    mock_post.assert_called_once()
    args, kwargs = mock_post.call_args
    assert args[0] == f"{BASE}/rest/v1/installs"
    assert kwargs["params"] == {"on_conflict": "public_key"}
    assert kwargs["json"] == {"public_key": "pub-1", "user_id": "user-1"}
    assert kwargs["headers"]["Prefer"] == "resolution=merge-duplicates"


def test_upsert_install_raises_on_network_failure(client):
    with patch("server.supabase_client.httpx.post", side_effect=httpx.ConnectError("boom")):
        with pytest.raises(SupabaseUnavailable):
            client.upsert_install("user-1", "pub-1")
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run pytest tests/test_supabase_client.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'server.supabase_client'`

- [ ] **Step 4: Write `src/server/supabase_client.py`**

```python
"""Thin REST wrapper around Supabase PostgREST for the `installs` table.

JWT verification happens locally in auth.py (verify_supabase_jwt) -- this
module only talks to Supabase for the one thing that needs a live round
trip: registering which account owns this PC install. Uses the
service-role key because FastAPI has already authenticated the caller.
"""

import httpx


class SupabaseUnavailable(Exception):
    pass


class SupabaseClient:
    def __init__(self, url: str, service_role_key: str, timeout: float = 5.0):
        self._base = url.rstrip("/") + "/rest/v1"
        self._headers = {
            "apikey": service_role_key,
            "Authorization": f"Bearer {service_role_key}",
            "Content-Type": "application/json",
        }
        self._timeout = timeout

    def upsert_install(self, user_id: str, public_key: str) -> None:
        try:
            r = httpx.post(
                f"{self._base}/installs",
                params={"on_conflict": "public_key"},
                json={"public_key": public_key, "user_id": user_id},
                headers={**self._headers, "Prefer": "resolution=merge-duplicates"},
                timeout=self._timeout,
            )
            r.raise_for_status()
        except httpx.HTTPError as e:
            raise SupabaseUnavailable(str(e)) from e
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_supabase_client.py -v`
Expected: PASS (2 passed)

- [ ] **Step 6: Wire the upsert into `app.py`'s auth gate**

Read `src/server/app.py`'s current `create_app(instance_manager)` opening (post-Task-1) and its imports. Add near the top of the file:

```python
from server import install_identity
from server.supabase_client import SupabaseClient, SupabaseUnavailable
```

Inside `create_app`, right before the `@app.middleware("http")` decorator, add:

```python
    supabase = SupabaseClient(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY) if auth.auth_enabled() else None
    _install_public_key = None
    if auth.auth_enabled():
        _, _install_public_key = install_identity.get_or_create_install_keypair()
    _cached_owner_user_id = install_identity.get_cached_owner_user_id()
```

Then extend `_auth_gate` — replace the body from `request.state.user = user` to the function's `return await call_next(request)` with:

```python
            request.state.user = user

            nonlocal _cached_owner_user_id
            if user.user_id != _cached_owner_user_id:
                # Best-effort: a Supabase hiccup here must not fail this
                # unrelated request. The cache only advances on success, so
                # the next request naturally retries.
                try:
                    await asyncio.to_thread(supabase.upsert_install, user.user_id, _install_public_key)
                except SupabaseUnavailable:
                    pass
                else:
                    install_identity.set_cached_owner_user_id(user.user_id)
                    _cached_owner_user_id = user.user_id
        return await call_next(request)
```

(`asyncio` is already imported at the top of `app.py` for other routes.)

- [ ] **Step 7: Write the failing test for the upsert-on-login behavior**

Add to `tests/test_app_auth.py`:

```python
def test_login_upserts_install_public_key_once_per_distinct_owner():
    client, _, supabase = _make_authed_client()

    client.get("/instances", headers={"Authorization": f"Bearer {_jwt(sub='user-1')}"})
    client.get("/instances", headers={"Authorization": f"Bearer {_jwt(sub='user-1')}"})
    client.get("/instances", headers={"Authorization": f"Bearer {_jwt(sub='user-2')}"})

    assert supabase.upsert_install.call_count == 2
    first_call, second_call = supabase.upsert_install.call_args_list
    assert first_call.args[0] == "user-1"
    assert second_call.args[0] == "user-2"


def test_login_upsert_failure_does_not_fail_the_request(monkeypatch):
    from server.supabase_client import SupabaseUnavailable
    client, _, supabase = _make_authed_client()
    supabase.upsert_install.side_effect = SupabaseUnavailable("boom")

    r = client.get("/instances", headers={"Authorization": f"Bearer {_jwt()}"})

    assert r.status_code == 200
```

`_make_authed_client` needs to return `(client, im, supabase)` again (Task 1's Step 9 dropped `supabase` from the return tuple since the mechanism didn't exist yet) — restore the third return value and the `patch("server.app.SupabaseClient", return_value=supabase)` context manager from before Task 1, now pointing at the new `SupabaseClient` import.

- [ ] **Step 8: Run the tests to verify they fail, then pass**

Run: `uv run pytest tests/test_app_auth.py -v`
Expected: first FAIL (no upsert wired), then PASS after Step 6's edit is in place.

- [ ] **Step 9: Run the full suite**

Run: `uv run pytest tests/ -v`
Expected: PASS (aside from the two documented pre-existing unrelated failures).

- [ ] **Step 10: Commit**

```bash
git add -f infra/supabase/installs.sql
git add src/server/supabase_client.py src/server/app.py tests/test_supabase_client.py tests/test_app_auth.py
git commit -m "feat(auth): register this install's public key with the owning account on login"
```

---

## Task 5: Session naming — `{owner_user_id}.{instance_name}`, drop viewer FastAPI token

**Files:**
- Modify: `src/server/engine_runtime.py:78-88,157-188,344-353`, `src/server/instance_manager.py:124-129`, `src/server/engine_orchestrator.py:71-78`, `src/server/app.py` (`select_instance` handler and its call into `instance_manager.select`)
- Test: `tests/test_engine_runtime.py`, `tests/test_app_auth.py` or wherever the `/select` HTTP response shape is tested

**Interfaces:**
- Consumes: `EngineTokenIssuer.engine_token(session)` (Task 3), `install_identity.get_cached_owner_user_id()` (Task 2).
- Produces: `EngineSelection.public_session: str | None` (replaces `signaling_token`). Task 7 (client) and Task 8 (Node relay) both depend on this exact field/format.

- [ ] **Step 1: Read the current `engine_runtime.py`, `instance_manager.py`, `engine_orchestrator.py`, and `app.py` select handler**

Read `src/server/engine_runtime.py:78-188` and `:344-353`, `src/server/instance_manager.py:124-129`, `src/server/engine_orchestrator.py:71-78`, and `src/server/app.py`'s `select_instance` handler (and its legacy counterparts around lines 418/422) to confirm line numbers post-Task-4.

- [ ] **Step 2: Update `EngineSelection` and `EngineRuntime.__init__`/`select()`**

In `EngineSelection` (around line 78-88), replace `signaling_token: str | None` with `public_session: str | None`.

`EngineRuntime` needs to know the current owner at `select()` time. Add an import at the top of `engine_runtime.py`:

```python
from server import install_identity
```

Replace `select()` (around line 157-188):

```python
    def select(self, advertised_host: str) -> EngineSelection | None:
        """Mint a fresh, short-lived credential set for one client.

        Returns None when no engine endpoint is currently published (never
        started, start failed, respawn failed, or stopped).
        """
        with self._lock:
            if self._stopped or self._endpoint is None:
                return None

            endpoint = self._endpoint
            host = _format_host(advertised_host)

            public_session: str | None = None
            if self.config.signaling_url:
                owner = install_identity.get_cached_owner_user_id()
                if owner is not None:
                    public_session = f"{owner}.{self.instance_name}"

            return EngineSelection(
                whep_url=f"http://{host}:{endpoint.whep_port}/whep",
                whep_token=self._token_issuer.whep(self.instance_name),
                signaling_url=self.config.signaling_url if self.config.signaling_url else None,
                public_session=public_session,
                generation=endpoint.generation,
                width=endpoint.width,
                height=endpoint.height,
            )
```

(The browser now presents its own Supabase access token directly to the relay — it already holds one for calling FastAPI itself — so this no longer mints a viewer-role token at all. `user_id` is dropped from the signature entirely: grepped `instance_manager.py` and `engine_orchestrator.py` — `user_id` was threaded through `InstanceManager.select()` → `EngineOrchestrator.select()` → here purely to feed the now-deleted viewer-token mint, nothing else in either intermediate method reads it.)

- [ ] **Step 3: Remove the now-dead `user_id` parameter from the two callers above `select()`**

In `src/server/instance_manager.py:124-129`, replace:

```python
        self, serial: str, advertised_host: str, user_id: str | None = None
    ) -> EngineSelection | None:
        ...
            serial, advertised_host, user_id=user_id
```

(the exact surrounding method is `InstanceManager.select`) with the `user_id` parameter and its pass-through removed — signature becomes `def select(self, serial: str, advertised_host: str) -> EngineSelection | None:` and the inner call becomes `... .select(serial, advertised_host)` (read the full method first so the edit lands correctly around its existing body).

In `src/server/engine_orchestrator.py:71-78`, apply the same removal to `EngineOrchestrator.select`.

In `src/server/app.py`'s `select_instance` handler, remove the `user.user_id if user else None` argument from the `instance_manager.select(...)` call (around line 337-338) — it becomes a two-argument call, `instance_manager.select, instance_id, host`. Do the same for the legacy `/select`/`/windows`-adjacent call sites around lines 418 and 422 if they still reference a user id there (read them first — they may already omit it).

- [ ] **Step 4: Update `_build_env_locked()` to use the install-scoped session**

Replace (around line 344-353):

```python
    def _build_env_locked(self) -> dict[str, str]:
        return {
            "ENGINE_WHEP_CAPABILITY_SECRET": self.config.whep_secret,
            "ENGINE_LOCAL_ICE_SERVERS": ",".join(self.config.local_ice_servers),
            "ENGINE_SIGNALING_URL": self.config.signaling_url,
            "ENGINE_SIGNALING_TOKEN": self._token_issuer.engine_token(
                self.instance_name
            ),
            "ENGINE_PUBLIC_ICE_SERVERS": ",".join(self.config.public_ice_servers),
        }
```

with:

```python
    def _build_env_locked(self) -> dict[str, str]:
        owner = install_identity.get_cached_owner_user_id()
        session = f"{owner}.{self.instance_name}" if owner is not None else self.instance_name
        return {
            "ENGINE_WHEP_CAPABILITY_SECRET": self.config.whep_secret,
            "ENGINE_LOCAL_ICE_SERVERS": ",".join(self.config.local_ice_servers),
            "ENGINE_SIGNALING_URL": self.config.signaling_url,
            "ENGINE_SIGNALING_TOKEN": self._token_issuer.engine_token(session),
            "ENGINE_SESSION": session,
            "ENGINE_PUBLIC_ICE_SERVERS": ",".join(self.config.public_ice_servers),
        }
```

(`ENGINE_SESSION` is new — Task 6 wires the C++ engine to forward it verbatim instead of building the session id itself. No cached owner yet at boot — see the spec's §6 — falls back to the bare instance name, same fail-closed behavior as before this task: the engine registers under a session no live viewer will ever request, so it simply doesn't get selected, not selected-by-the-wrong-party.)

- [ ] **Step 5: Update `tests/test_engine_runtime.py`**

Add a fixture-level default so `install_identity.get_cached_owner_user_id()` returns a known value in these tests — add near the top of the file:

```python
from server import install_identity


@pytest.fixture(autouse=True)
def _fixed_owner(monkeypatch):
    monkeypatch.setattr(install_identity, "get_cached_owner_user_id", lambda: "owner-1")
```

Update `test_start_launches_generation_zero_before_engine_and_mints_engine_jwt` (line 323-330) — no assertion changes needed (`decode_role` still just splits on `:`), but note the session embedded is now `"owner-1.instance0"` — add an assertion:

```python
def test_start_launches_generation_zero_before_engine_and_mints_engine_jwt():
    runtime, fakes = make_runtime()
    runtime.start()
    assert fakes.events[:2] == [
        ("scrcpy.launch", "720", 0),
        ("engine.start", "instance0", 27183),
    ]
    assert decode_role(fakes.engine_env["ENGINE_SIGNALING_TOKEN"]) == "engine"
    assert fakes.engine_env["ENGINE_SESSION"] == "owner-1.instance0"
```

Replace `test_select_mints_fresh_whep_and_viewer_tokens_without_admin_port` (lines 333-342):

```python
def test_select_mints_fresh_whep_tokens_and_public_session():
    issuer = CountingTokenIssuer()
    runtime, fakes = make_runtime(token_issuer=issuer)
    runtime.start()
    first = runtime.select("100.64.1.4")
    second = runtime.select("100.64.1.4")
    assert first.whep_url == "http://100.64.1.4:51000/whep"
    assert first.whep_token != second.whep_token
    assert first.public_session == "owner-1.instance0"
    assert not hasattr(first, "admin_port")
```

Update `test_select_returns_null_signaling_url_and_token_together_when_disabled` (lines 457-462):

```python
def test_select_returns_null_signaling_url_and_session_together_when_disabled():
    runtime, fakes = make_runtime(config=make_config(signaling_url=""))
    runtime.start()
    selection = runtime.select("100.64.1.4")
    assert selection.signaling_url is None
    assert selection.public_session is None
```

Update `test_start_passes_every_configured_env_overlay_to_the_engine` (lines 403-418) to include `ENGINE_SESSION` in the expected `set(env)`.

- [ ] **Step 6: Update `app.py`'s `select_instance` response**

Find the response dict inside `select_instance` (built from `EngineSelection`) — replace the `"signaling_token": selection.signaling_token,` line with `"public_session": selection.public_session,`. Do the same for the legacy `/select` handler's response, if it mirrors the same fields.

- [ ] **Step 7: Update any HTTP-level test asserting the select response shape**

Search `tests/test_app_auth.py` (and `tests/test_app.py` if it also asserts this) for `signaling_token` in a JSON response assertion and rename to `public_session`.

- [ ] **Step 8: Run the full test suite**

Run: `uv run pytest tests/ -v`
Expected: PASS (aside from the two documented pre-existing unrelated failures).

- [ ] **Step 9: Commit**

```bash
git add src/server/engine_runtime.py src/server/instance_manager.py src/server/engine_orchestrator.py \
        src/server/app.py tests/test_engine_runtime.py tests/test_app_auth.py
git commit -m "feat(auth): scope public signaling sessions to the owning account"
```

---

## Task 6: C++ engine forwards `ENGINE_SESSION`

**Files:**
- Modify: `engine/src/main.cpp:64-71,105-106`

**Interfaces:**
- Consumes: `ENGINE_SESSION` env var (Task 5).
- Produces: no change to any function signature — `main.cpp` still just forwards a string to `SignalingClient`'s existing constructor.

- [ ] **Step 1: Read the current file**

Read `engine/src/main.cpp:60-115` to confirm line numbers.

- [ ] **Step 2: Update the usage string and session construction**

Replace line 66-69 (the `Usage:` message):

```cpp
        std::cerr << "Usage: engine.exe <instance_name> <scrcpy_port>\n"
                     "Environment: ENGINE_WHEP_CAPABILITY_SECRET, "
                     "ENGINE_LOCAL_ICE_SERVERS, ENGINE_SIGNALING_URL, "
                     "ENGINE_SIGNALING_TOKEN, ENGINE_SESSION, "
                     "ENGINE_PUBLIC_ICE_SERVERS\n";
```

Replace lines 102-106:

```cpp
        std::string signalingUrl = GetEnvOrEmpty("ENGINE_SIGNALING_URL");
        if (!signalingUrl.empty()) {
            std::string signalingToken = GetEnvOrEmpty("ENGINE_SIGNALING_TOKEN");
            std::string session = GetEnvOrEmpty("ENGINE_SESSION");
            if (session.empty()) session = instanceName;
            signaling = std::make_unique<SignalingClient>(
                signalingUrl, session, "engine", signalingToken);
```

(Empty `ENGINE_SESSION` falls back to the bare instance name — matches today's behavior exactly, so a manually-launched engine, e.g. under the C++ test harness, keeps working unchanged.)

- [ ] **Step 3: Verify the offline C++ test suite still compiles (Windows Host PC only — cannot run from this session)**

This repo has never been successfully compiled from macOS (`engine/BUILD_WINDOWS.md`). Note in the commit message and this plan's final task that `engine_tests.exe`'s offline suite (`--gtest_filter` excluding `SignalingClient.*:PublicSignalingBridge.*`) needs to be run on the Windows Host PC or via the `build-engine` GitHub Actions workflow before this change can be called verified.

- [ ] **Step 4: Commit**

```bash
git add engine/src/main.cpp
git commit -m "feat(engine): forward the account-scoped session id from FastAPI"
```

---

## Task 7: Client field rename (`public_session`, own Supabase token)

**Files:**
- Modify: `src/client/engine_session.js:240-270`, `mobile/src/api/client.ts:15-25`
- Bump: `VERSION` in `src/config.py` (frontend JS changed)

**Interfaces:**
- Consumes: `selection.public_session` (Task 5's HTTP response field).
- Produces: no new interface — this is the last consumer in the chain for the browser path.

- [ ] **Step 1: Read the current files**

Read `src/client/engine_session.js:230-280` and `mobile/src/api/client.ts:1-30` to confirm line numbers.

- [ ] **Step 2: Update `engine_session.js`'s `startPublic()`**

Find how the client currently obtains its own Supabase access token for authenticating FastAPI requests (grep the file for `Authorization` or `Bearer` — this repo's auth client code already holds it for every API call; reuse the same accessor, e.g. if it's `getAccessToken()` or similar exported from an auth module, import and call it here). Replace lines 254-257:

```js
          const url = new URL(selection.signaling_url);
          url.searchParams.set('session', selection.name);
          url.searchParams.set('role', 'viewer');
          url.searchParams.set('token', selection.signaling_token);
```

with:

```js
          const url = new URL(selection.signaling_url);
          url.searchParams.set('session', selection.public_session);
          url.searchParams.set('role', 'viewer');
          url.searchParams.set('token', getAccessToken());
```

(Substitute the actual accessor name found in Step 1's grep — every other authenticated fetch in this client already needs one, reuse it rather than inventing a new one.) Also update the `publicConfigured` check a few lines below (`selection.signaling_url && selection.signaling_token`) to check `selection.public_session` instead of `selection.signaling_token`.

- [ ] **Step 3: Update `mobile/src/api/client.ts`'s selection type**

Replace the `signaling_url`/`signaling_token` fields (around line 20-21):

```ts
  signaling_url: string | null;
  signaling_token: string | null;
```

with:

```ts
  signaling_url: string | null;
  public_session: string | null;
```

(No relay-connect logic exists in `mobile/` yet — confirmed by grep, this is purely the type definition, ready for whenever mobile's own public-path connect code is built.)

- [ ] **Step 4: Bump the frontend cache-busting version**

In `src/config.py`, increment `VERSION` (e.g. `"2.3.23"` → `"2.3.24"`) — `app.py` appends `?v={VERSION}` to asset URLs, required after any `src/client/*.js` edit per this repo's convention.

- [ ] **Step 5: Run the mobile and Python test suites**

Run: `uv run pytest tests/ -v` (confirms nothing Python-side broke)
Run (inside `mobile/`): `npm test` (confirms the TypeScript type change doesn't break any existing test referencing `signaling_token`)

- [ ] **Step 6: Commit**

```bash
git add src/client/engine_session.js mobile/src/api/client.ts src/config.py
git commit -m "feat(client): use own Supabase token and account-scoped session for public signaling"
```

---

## Task 8: Node relay — verify the viewer's real Supabase JWT

**Files:**
- Modify: `infra/vps/signaling/package.json`, `infra/vps/signaling/server.js`, `infra/vps/signaling/README.md`
- Test: `infra/vps/signaling/server.test.js`

**Interfaces:**
- Consumes: session format `"{user_id}.{instance_name}"` (Task 5), `SUPABASE_URL` env var.
- Produces: relay rejects a `role=viewer` connection unless its `token` is a genuine, unexpired Supabase access token whose `sub` matches the `user_id` portion of the requested session.

- [ ] **Step 1: Read the current relay and its tests**

Read `infra/vps/signaling/server.js` and `infra/vps/signaling/server.test.js` in full to confirm current structure before editing. In particular, note the existing helpers `openClient(port, session, role)`, `openClientWithToken(port, session, role, token)`, and `waitForCloseCode(ws)` — reuse these exactly, don't reinvent them.

Note also: three existing tests use `role: 'viewer'` tokens under the old HMAC scheme and are now obsolete — delete them in Step 5 below: `'accepts connection where token role matches requested role param'`, `'accepts a token carrying a user_id claim and does not treat it as a new authorization check'`, `'accepts a token missing the user_id claim exactly as before'`.

- [ ] **Step 2: Add the `jose` dependency**

Edit `infra/vps/signaling/package.json`, add to `"dependencies"`:

```json
    "jose": "^5.9.6",
```

Run (inside `infra/vps/signaling/`): `npm install`

- [ ] **Step 3: Write the failing tests**

Add to `infra/vps/signaling/server.test.js`, reusing the file's existing `openClientWithToken`/`waitForCloseCode` helpers:

```js
import { generateKeyPair, SignJWT, jwtVerify } from 'jose';

async function makeSupabaseToken({ sub, privateKey, audience = 'authenticated', expiresInSeconds = 3600 }) {
  return new SignJWT({ sub })
    .setProtectedHeader({ alg: 'ES256' })
    .setIssuedAt()
    .setAudience(audience)
    .setExpirationTime(Math.floor(Date.now() / 1000) + expiresInSeconds)
    .sign(privateKey);
}

function makeVerifyViewerToken(publicKey) {
  return async (token) => {
    const { payload } = await jwtVerify(token, publicKey, {
      algorithms: ['ES256'], audience: 'authenticated',
    });
    return payload.sub;
  };
}

test('viewer with a valid Supabase JWT whose sub matches the session is accepted', async () => {
  const { publicKey, privateKey } = await generateKeyPair('ES256');
  const { server, port } = await createSignalingServer({
    port: 0, verifyViewerToken: makeVerifyViewerToken(publicKey),
  });
  const token = await makeSupabaseToken({ sub: 'user-1', privateKey });

  const ws = await openClientWithToken(port, 'user-1.instance0', 'viewer', token);

  ws.close();
  server.close();
  assert.ok(true); // openClientWithToken resolves only on 'open'
});

test('viewer whose sub does not match the session user id is rejected', async () => {
  const { publicKey, privateKey } = await generateKeyPair('ES256');
  const { server, port } = await createSignalingServer({
    port: 0, verifyViewerToken: makeVerifyViewerToken(publicKey),
  });
  const token = await makeSupabaseToken({ sub: 'user-2', privateKey });

  const ws = new WebSocket(`ws://localhost:${port}/?session=user-1.instance0&role=viewer&token=${token}`);
  const closeCode = await waitForCloseCode(ws);

  assert.strictEqual(closeCode, 1008);
  server.close();
});

test('viewer with an expired Supabase JWT is rejected', async () => {
  const { publicKey, privateKey } = await generateKeyPair('ES256');
  const { server, port } = await createSignalingServer({
    port: 0, verifyViewerToken: makeVerifyViewerToken(publicKey),
  });
  const token = await makeSupabaseToken({ sub: 'user-1', privateKey, expiresInSeconds: -10 });

  const ws = new WebSocket(`ws://localhost:${port}/?session=user-1.instance0&role=viewer&token=${token}`);
  const closeCode = await waitForCloseCode(ws);

  assert.strictEqual(closeCode, 1008);
  server.close();
});

test('viewer connection is still trusted with no verification configured (dev/local relay)', async () => {
  // Matches the existing "no jwtSecret = trusted" behavior for engine role:
  // an operator who hasn't configured SUPABASE_URL gets the old trusted-relay
  // behavior, not a hard failure.
  const { server, port } = await createSignalingServer({ port: 0 });

  const ws = await openClient(port, 'sess-1', 'viewer');

  ws.close();
  server.close();
  assert.ok(true);
});
```

- [ ] **Step 4: Run the tests to verify they fail**

Run (inside `infra/vps/signaling/`): `npm test`
Expected: FAIL (relay doesn't yet distinguish Supabase JWTs from the old HMAC scheme for viewers)

- [ ] **Step 5: Implement JWKS verification for the viewer role, opt-in like the existing engine check**

In `server.js`, add near the top:

```js
import { createRemoteJWKSet, jwtVerify } from 'jose';
```

In `createSignalingServer`, accept two new options — `supabaseUrl` and an overridable `verifyViewerToken` (tests inject a local-key verifier instead of hitting a real JWKS endpoint over the network):

```js
export async function createSignalingServer({
  port = 8443, jwtSecret = null, supabaseUrl = null, verifyViewerToken = null, tls = null,
} = {}) {
  const httpServer = tls ? createSecureServer(tls) : createServer();
  const wss = new WebSocketServer({ server: httpServer });

  const resolveViewerToken = verifyViewerToken || (supabaseUrl ? (() => {
    const jwks = createRemoteJWKSet(
      new URL(`${supabaseUrl.replace(/\/$/, '')}/auth/v1/.well-known/jwks.json`),
    );
    return async (token) => {
      const { payload } = await jwtVerify(token, jwks, {
        algorithms: ['ES256'], audience: 'authenticated',
      });
      return payload.sub;
    };
  })() : null);
```

Replace the connection handler's token-verification block (the `if (jwtSecret) { ... }` block) with a role-specific branch. This preserves the existing "unconfigured = trusted local/dev relay" behavior for *both* roles — verification only runs when the corresponding option was actually configured:

```js
    if (role === 'viewer') {
      if (resolveViewerToken) {
        let userId;
        try {
          userId = await resolveViewerToken(token);
        } catch {
          ws.close(1008, 'invalid or expired viewer token');
          return;
        }
        if (userId !== sessionId.split('.', 1)[0]) {
          ws.close(1008, "token does not match this session's account");
          return;
        }
      }
    } else if (jwtSecret) {
      // engine role: HMAC check replaced with Ed25519 signature verification
      // against this session's registered install key -- see Task 9.
      try {
        const payload = jwt.verify(token, jwtSecret, { algorithms: ['HS256'] });
        if (payload.session !== sessionId || payload.role !== role
            || typeof payload.exp !== 'number' || !Number.isFinite(payload.exp)) {
          ws.close(1008, 'token claims do not match session, role, and expiry requirements');
          return;
        }
      } catch {
        ws.close(1008, 'invalid or missing token');
        return;
      }
    }
```

(The `else if (jwtSecret)` branch keeps today's HMAC engine check working for now — Task 9 replaces it with the Ed25519/`installs`-lookup check. Also change the connection handler to `async (ws, req) => { ... }` since it now `await`s the viewer verification.)

Delete the three now-obsolete viewer-role HMAC tests named in Step 1.

Update the standalone-run block at the bottom of the file to read `SUPABASE_URL` from the environment and pass it through:

```js
  const supabaseUrl = process.env.SUPABASE_URL || null;
  const servers = [createSignalingServer({ port, jwtSecret, supabaseUrl })];
```

(apply to both the plain and TLS server construction calls in that block), and warn if it's unset the same way the existing `JWT_SECRET` warning does.

- [ ] **Step 6: Run the tests to verify they pass**

Run (inside `infra/vps/signaling/`): `npm test`
Expected: PASS

- [ ] **Step 7: Update the deploy README**

Add to `infra/vps/signaling/README.md`'s Configure section:

```
echo "SUPABASE_URL=https://<project>.supabase.co" | sudo tee -a /opt/webrtc-signaling/.env
```

Note that viewer connections now require this to be set — without it, every viewer connection is rejected (`viewer auth not configured`).

- [ ] **Step 8: Commit**

```bash
git add infra/vps/signaling/package.json infra/vps/signaling/package-lock.json \
        infra/vps/signaling/server.js infra/vps/signaling/server.test.js infra/vps/signaling/README.md
git commit -m "feat(relay): verify viewers with their own Supabase JWT instead of a shared secret"
```

---

## Task 9: Node relay — verify the engine's signature against its registered public key

**Files:**
- Modify: `infra/vps/signaling/server.js`, `infra/vps/signaling/README.md`
- Test: `infra/vps/signaling/server.test.js`

**Interfaces:**
- Consumes: `installs` table schema (Task 4), session format (Task 5), the engine-role branch left in place by Task 8.
- Produces: relay rejects a `role=engine` connection unless its token's Ed25519 signature verifies against the `public_key` registered in Supabase for the session's `user_id`.

- [ ] **Step 1: Read the current relay post-Task-8**

Read `infra/vps/signaling/server.js` in full to confirm the engine-role branch's exact current shape.

Note also: six existing tests exercise the old engine-role HMAC scheme and are now obsolete — delete them in Step 4 below: `'rejects connection with missing token'`, `'rejects connection with token for a different session'`, `'accepts connection with valid token matching session'`, `'rejects connection where token role does not match requested role param'`, `'rejects an otherwise matching token without an expiry claim'`, `'rejects an expired token with otherwise matching claims'`.

- [ ] **Step 2: Write the failing tests**

Add to `infra/vps/signaling/server.test.js`, reusing the file's existing `openClientWithToken`/`waitForCloseCode` helpers:

```js
import { generateKeyPair as generateEdKeyPair, SignJWT as SignEdJWT, exportJWK as exportEdJWK } from 'jose';

async function makeEngineToken({ session, privateKey, expiresInSeconds = 3600 }) {
  return new SignEdJWT({ session, role: 'engine' })
    .setProtectedHeader({ alg: 'EdDSA' })
    .setJti('1')
    .setExpirationTime(Math.floor(Date.now() / 1000) + expiresInSeconds)
    .sign(privateKey);
}

test('engine whose signature matches the registered install key is accepted', async () => {
  const { publicKey, privateKey } = await generateEdKeyPair('EdDSA');
  const jwk = await exportEdJWK(publicKey);
  const { server, port } = await createSignalingServer({
    port: 0,
    installLookup: async (userId) =>
      userId === 'user-1' ? [{ public_key: jwk.x, user_id: 'user-1' }] : [],
  });
  const token = await makeEngineToken({ session: 'user-1.instance0', privateKey });

  const ws = await openClientWithToken(port, 'user-1.instance0', 'engine', token);

  ws.close();
  server.close();
  assert.ok(true);
});

test('engine whose signature does not match the registered install key is rejected', async () => {
  const { privateKey } = await generateEdKeyPair('EdDSA');
  const { publicKey: someoneElsesKey } = await generateEdKeyPair('EdDSA');
  const someoneElsesJwk = await exportEdJWK(someoneElsesKey);
  const { server, port } = await createSignalingServer({
    port: 0,
    installLookup: async () => [{ public_key: someoneElsesJwk.x, user_id: 'user-1' }],
  });
  const token = await makeEngineToken({ session: 'user-1.instance0', privateKey });

  const ws = new WebSocket(`ws://localhost:${port}/?session=user-1.instance0&role=engine&token=${token}`);
  const closeCode = await waitForCloseCode(ws);

  assert.strictEqual(closeCode, 1008);
  server.close();
});

test('engine for a user_id with no installs row is rejected', async () => {
  const { privateKey } = await generateEdKeyPair('EdDSA');
  const { server, port } = await createSignalingServer({
    port: 0,
    installLookup: async () => [],
  });
  const token = await makeEngineToken({ session: 'user-1.instance0', privateKey });

  const ws = new WebSocket(`ws://localhost:${port}/?session=user-1.instance0&role=engine&token=${token}`);
  const closeCode = await waitForCloseCode(ws);

  assert.strictEqual(closeCode, 1008);
  server.close();
});

test('engine token with a mismatched session claim is rejected even with a valid signature', async () => {
  const { publicKey, privateKey } = await generateEdKeyPair('EdDSA');
  const jwk = await exportEdJWK(publicKey);
  const { server, port } = await createSignalingServer({
    port: 0,
    installLookup: async () => [{ public_key: jwk.x, user_id: 'user-1' }],
  });
  const token = await makeEngineToken({ session: 'user-1.some-other-instance', privateKey });

  const ws = new WebSocket(`ws://localhost:${port}/?session=user-1.instance0&role=engine&token=${token}`);
  const closeCode = await waitForCloseCode(ws);

  assert.strictEqual(closeCode, 1008);
  server.close();
});

test('expired engine token is rejected even with a valid signature', async () => {
  const { publicKey, privateKey } = await generateEdKeyPair('EdDSA');
  const jwk = await exportEdJWK(publicKey);
  const { server, port } = await createSignalingServer({
    port: 0,
    installLookup: async () => [{ public_key: jwk.x, user_id: 'user-1' }],
  });
  const token = await makeEngineToken({ session: 'user-1.instance0', privateKey, expiresInSeconds: -10 });

  const ws = new WebSocket(`ws://localhost:${port}/?session=user-1.instance0&role=engine&token=${token}`);
  const closeCode = await waitForCloseCode(ws);

  assert.strictEqual(closeCode, 1008);
  server.close();
});
```

- [ ] **Step 3: Run the tests to verify they fail**

Run (inside `infra/vps/signaling/`): `npm test`
Expected: FAIL

- [ ] **Step 4: Implement Ed25519 verification for the engine role, opt-in like every other check here**

In `createSignalingServer`'s options, replace `jwtSecret` with `serviceRoleKey` and an overridable `installLookup` (mirrors Task 8's `verifyViewerToken` pattern exactly — unconfigured stays trusted, matching every existing "no secret = trusted local/dev relay" test in this file):

```js
export async function createSignalingServer({
  port = 8443, supabaseUrl = null, serviceRoleKey = null,
  verifyViewerToken = null, installLookup = null, tls = null,
} = {}) {
  const httpServer = tls ? createSecureServer(tls) : createServer();
  const wss = new WebSocketServer({ server: httpServer });

  const resolveViewerToken = verifyViewerToken || (supabaseUrl ? (() => {
    const jwks = createRemoteJWKSet(
      new URL(`${supabaseUrl.replace(/\/$/, '')}/auth/v1/.well-known/jwks.json`),
    );
    return async (token) => {
      const { payload } = await jwtVerify(token, jwks, {
        algorithms: ['ES256'], audience: 'authenticated',
      });
      return payload.sub;
    };
  })() : null);
  const lookupInstall = installLookup || ((supabaseUrl && serviceRoleKey) ? (async (userId) => {
    const res = await fetch(
      `${supabaseUrl.replace(/\/$/, '')}/rest/v1/installs?user_id=eq.${encodeURIComponent(userId)}&select=public_key`,
      { headers: { apikey: serviceRoleKey, Authorization: `Bearer ${serviceRoleKey}` } },
    );
    if (!res.ok) throw new Error(`installs lookup failed: ${res.status}`);
    return res.json();
  }) : null);
```

Replace the `else if (jwtSecret) { ... }` branch (Task 8's HMAC placeholder) with:

```js
    } else if (lookupInstall) {
      // role === 'engine'
      const expectedUserId = sessionId.split('.', 1)[0];
      let rows;
      try {
        rows = await lookupInstall(expectedUserId);
      } catch {
        ws.close(1008, 'install lookup failed');
        return;
      }
      if (!rows.length) {
        ws.close(1008, 'no registered install for this account');
        return;
      }
      try {
        const [header, payload, signature] = token.split('.');
        const claims = JSON.parse(Buffer.from(payload, 'base64url').toString('utf8'));
        if (claims.session !== sessionId || claims.role !== 'engine'
            || typeof claims.exp !== 'number' || !Number.isFinite(claims.exp)
            || claims.exp < Date.now() / 1000) {
          throw new Error('claims mismatch');
        }
        const publicKeyObject = crypto.createPublicKey({
          key: { kty: 'OKP', crv: 'Ed25519', x: rows[0].public_key },
          format: 'jwk',
        });
        const verified = crypto.verify(
          null,
          Buffer.from(`${header}.${payload}`),
          publicKeyObject,
          Buffer.from(signature, 'base64url'),
        );
        if (!verified) throw new Error('bad signature');
      } catch {
        ws.close(1008, 'invalid engine registration token');
        return;
      }
    }
```

(`lookupInstall` being falsy — neither `installLookup` nor both `supabaseUrl`+`serviceRoleKey` configured — means this whole branch is skipped, same trusted-relay fallback the file has always had. This is why the basic pre-existing tests like `'relays a message from engine to viewer in the same session'`, which use `role: 'engine'` with zero auth config, keep passing unchanged.)

Add `import crypto from 'node:crypto';` at the top of the file. Delete the now-unused `import jwt from 'jsonwebtoken';` and remove `jsonwebtoken` from `package.json`'s dependencies (grep the file first to confirm nothing else references it — after this task, nothing does: `jwtSecret` is fully replaced by `serviceRoleKey`/`installLookup`).

Delete the six obsolete tests named in Step 1.

Update the standalone-run block to read `SUPABASE_SERVICE_ROLE_KEY` from the environment and pass it as `serviceRoleKey`, and drop the `jwtSecret`/`JWT_SECRET` reads there too.

- [ ] **Step 5: Run the tests to verify they pass**

Run (inside `infra/vps/signaling/`): `npm test`
Expected: PASS

- [ ] **Step 6: Update the deploy README**

Add to `infra/vps/signaling/README.md`'s Configure section:

```
echo "SUPABASE_SERVICE_ROLE_KEY=<service-role-key>" | sudo tee -a /opt/webrtc-signaling/.env
```

Remove the README's `JWT_SECRET` generation step entirely — Step 4 removed that plumbing, `installs`-based Ed25519 verification replaces it.

- [ ] **Step 7: Commit**

```bash
git add infra/vps/signaling/server.js infra/vps/signaling/server.test.js infra/vps/signaling/README.md
git commit -m "feat(relay): verify engine registrations against the account's registered install key"
```

---

## Task 10: Final manual verification (Windows Host PC + real Supabase project)

Nothing in Tasks 6, 8, and 9 can be verified from this macOS session — the C++ engine has never been built here, and the relay's real Supabase-backed behavior needs a live project. This task is the checklist to run before considering the whole plan done.

**Files:** none (verification only).

- [ ] **Step 1: Apply the new Supabase schema**

In the Supabase SQL editor for the real project: run `infra/supabase/installs.sql`. If `device_links` still exists from before Task 1: run `drop table device_links;`.

- [ ] **Step 2: Redeploy the relay**

On the VPS: pull this branch, `cd /opt/webrtc-signaling && sudo -u webrtc npm install --production`, add `SUPABASE_URL` and `SUPABASE_SERVICE_ROLE_KEY` to `.env` per Task 8/9's README updates, `sudo systemctl restart webrtc-signaling`, then `sudo journalctl -u webrtc-signaling -n 50 --no-pager` — expect no `WARNING` lines about missing config.

- [ ] **Step 3: Build the Windows installer and engine**

On the Windows Host PC: pull this branch, run the project's normal build steps (`build\build.bat` / `build\build_installer.bat` per `docs/PROJECT_CONTEXT.md`). Confirm `engine_tests.exe`'s offline suite (excluding `SignalingClient.*:PublicSignalingBridge.*`) passes.

- [ ] **Step 4: Single-PC login and public-path smoke test**

Install and run the app with `SUPABASE_URL`/`SUPABASE_ANON_KEY`/`SUPABASE_SERVICE_ROLE_KEY`/`VPS_SIGNALING_URL` configured. Log in via the web PWA. Confirm: `/instances` returns every discovered instance (no linking step needed). Select an instance from off-network (public path) and confirm video/input work.

Expect one caveat on a brand-new install: engine processes spawn at app startup, when no owner is cached yet, so the session they register under is only known after that first login — and the engine does not re-register on its own once its session becomes stale. If the public path doesn't work immediately after the very first login on a fresh install, restart the app once and retry before treating it as a failure. This is pre-existing engine behavior this plan did not change. The local path is unaffected.

- [ ] **Step 5: Two-PC, same-account gate**

Repeat Step 4 on a second physical (or virtual) Windows install, same Supabase account. Confirm both PCs' instances are independently selectable over the public path at the same time (no collision, no cross-talk) — this is the scenario confirmed in the design doc's review.

- [ ] **Step 6: Account-switch gate**

An install locks to the first account that authenticates against it. An HTTP login alone therefore cannot move an already-claimed install to a different account — that would let any self-registered account seize a PC it doesn't own. Switching owners requires local access to the machine. Verify both halves:

*Rejection (no filesystem access):* on PC #1 from Step 4, log out and log in with a second, different Supabase account. Confirm that account's authenticated requests are rejected with `403 This install belongs to a different account`, and that the *first* account's own sessions keep working unaffected. The install must not change hands.

*Deliberate transfer (with filesystem access):* stop the app, delete `install_owner.txt` from `C:\ProgramData\WindowControl\` (the first writable-path candidate; check the other candidates in `src/server/install_identity.py` if it isn't there), then start the app and log in as the second account. Confirm that login now claims the install, that the second account's instances are selectable over the public path, and that the first account is now the one getting `403`. Allow one app restart before the public path works, per Step 4's fresh-install note — the engine spawned before the new owner was cached and does not re-register on its own.

*Unclaimed installs are unchanged:* a fresh install with no `install_owner.txt` still claims to whoever authenticates first (trust-on-first-use), exactly as before. Only the transfer of an *already-claimed* install now requires local access.

- [ ] **Step 7: Leaked-secret scenario sanity check**

Copy PC #1's `install_key.bin` (from `C:\ProgramData\WindowControl\`) onto a third machine. Confirm that machine's engine process, using the copied key, can only ever register under PC #1's *own* session slot (and only if PC #1's real engine isn't already holding it) — it cannot forge a token for any other install's session, since the relay verifies purely against the specific public key registered for that session's account, not a value anyone can reuse to claim a different account. Confirms the design's core guarantee end-to-end, not just in isolated unit tests.

- [ ] **Step 8: Update HANDOFF.md**

Append an entry to `HANDOFF.md` per the template, recording: which of Steps 1-7 passed on real hardware, any gaps found, and next steps for whoever picks this up (e.g. if the delete-`install_owner.txt` transfer flow in Step 6 was awkward in practice, if the fresh-install restart from Step 4 was needed, or if `device_links` still needs manual dropping on a project that wasn't touched yet).
