# Relay License Keys Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Gate shared TURN relay access behind a one-PC license key, and close two relay/broker abuse gaps (per-credential allocation quota, `viewer_auth` rate limit).

**Architecture:** A file-backed `LicenseStore` in the broker binds key digests to installation IDs; `MediaAdmission` issues TURN servers only to licensed installations, so unlicensed PCs fall back to the existing STUN-only `relay_available: false` path. The PC activates/deactivates over two credential-authenticated HTTP endpoints, learns its status from the `host_auth` reply, and the launcher exposes key entry, status and deactivation. An admin CLI shares the license file under an exclusive file lock.

**Tech Stack:** Python 3.11+, FastAPI/Starlette, Pydantic v2, PyQt5, pytest (`uv run pytest`), coturn, nginx.

**Spec:** `docs/superpowers/specs/2026-10-02-direct-webrtc-shared-turn-design.md`, section "Relay license keys (added 2026-10-16)". Extends `docs/superpowers/plans/2026-10-02-direct-webrtc-shared-turn.md`; independent of the engine merge from `feature/engine`, so it can run before or after it.

## Global Constraints

- License gates relay only. Registration, pairing, signaling and STUN-only sessions never require a key.
- Key: `EMU-` + 4 groups of 5 Crockford base32 chars (alphabet `0123456789ABCDEFGHJKMNPQRSTVWXYZ`), 100 random bits from `secrets`.
- Normalize: uppercase, drop spaces and `-`, strip leading `EMU`, map `O`→`0`, `I`/`L`→`1`; reject anything not exactly 20 alphabet chars.
- Store only `sha256(normalized_key).hexdigest()`; never log or return a key except once from `admin issue`.
- Move cooldown: 7 days = `604800` seconds, measured from `moved_at`; first activation leaves `moved_at` null.
- A move revokes the previous installation exactly like `DELETE /installations/{id}`.
- Error bodies: `{"detail": "invalid_license"}` (403), `{"detail": "license_in_use", "retry_after": <int seconds>}` (409), `{"detail": "Unauthorized"}` (401) for a bad installation credential.
- `viewer_auth` limits: 10/minute per client IP, 30/minute per installation ID.
- Coturn: `user-quota=4`; relay ports `49160-50159`.
- Commit messages follow `COMMIT_CONVENTION.md`; no AI attribution.
- Run tests with `uv run pytest`, never `python -m pytest`.

## Review Focus

1. **Key typed with lowercase, spaces or `O`/`I` look-alikes** → must activate the same key (Task 1 normalization test).
2. **PC reset its identity, then re-activates** → old installation no longer exists, so bind is immediate with no cooldown (Task 1 `test_binding_to_deleted_installation_rebinds_without_cooldown`).
3. **Admin revokes while a licensed stream is live** → next admission (renew/new select) is STUN-only; the running allocation ends at TURN credential expiry. No crash (Task 2 `test_revoked_license_stops_relay_on_next_admission`).
4. **Corrupt or unreadable license file** → broker fails closed for relay (STUN-only) and HTTP returns 503, never treats it as "all licensed" or wipes it (Task 1 `test_corrupt_license_file_fails_closed`).
5. **Activation while the broker is unreachable** → launcher shows an error and stays usable; local pairing unaffected (Task 7 `test_license_activation_failure_keeps_launcher_usable`).

---

### Task 1: License store

**Files:**
- Create: `src/broker/license_store.py`
- Test: `tests/test_broker_license.py`

**Interfaces:**
- Produces:
  - `normalize_key(raw: str) -> str` (20 chars) — raises `ValueError` on invalid input.
  - `generate_key() -> str` — display form `EMU-XXXXX-XXXXX-XXXXX-XXXXX`.
  - `class LicenseStoreError(RuntimeError)`
  - `@dataclass(frozen=True) class Activation: status: Literal["bound", "moved", "in_use", "invalid"]; previous_installation: str | None = None; retry_after: int | None = None`
  - `@dataclass(frozen=True) class LicenseView: digest_prefix: str; installation_id: str | None; issued_at: int; bound_at: int | None; moved_at: int | None; expires_at: int | None; revoked: bool; note: str`
  - `class LicenseStore(path: str | Path)` with methods
    `issue(*, now: float, days: int | None = None, note: str = "") -> str`,
    `activate(key: str, installation_id: str, *, now: float, installation_exists: Callable[[str], bool]) -> Activation`,
    `deactivate(installation_id: str) -> bool`,
    `is_licensed(installation_id: str, *, now: float) -> bool`,
    `reset(key: str) -> bool`, `revoke(key: str) -> bool`, `list() -> list[LicenseView]`.
  - `MOVE_COOLDOWN_SECONDS = 604800`

File format: JSON object keyed by digest; record keys exactly `{"installation_id", "issued_at", "bound_at", "moved_at", "expires_at", "revoked", "note"}`. Every method is one transaction: thread `RLock` + `fcntl.flock(LOCK_EX)` on `<path>.lock`, re-read file, validate, mutate, atomic write (tempfile + fsync + `os.replace`, same pattern as `broker/identity_store.py`). Missing file = empty. Invalid file → `LicenseStoreError`, file bytes untouched. `is_licensed` returns `False` on `LicenseStoreError` (fail closed).

- [ ] **Step 1: Write failing tests**

```python
from broker.license_store import (LicenseStore, LicenseStoreError, normalize_key,
                                  generate_key, MOVE_COOLDOWN_SECONDS)

A, B = "a" * 32, "b" * 32
alive = lambda _id: True

def test_generated_key_normalizes_from_sloppy_input():
    key = generate_key()
    assert re.fullmatch(r"EMU(-[0-9A-HJKMNP-TV-Z]{5}){4}", key)
    sloppy = " " + key.lower().replace("0", "o").replace("1", "l") + " "
    assert normalize_key(sloppy) == normalize_key(key)
    with pytest.raises(ValueError):
        normalize_key("EMU-ABCDE")

def test_first_activation_binds_without_cooldown(tmp_path):
    store = LicenseStore(tmp_path / "licenses.json")
    key = store.issue(now=1000)
    assert store.activate(key, A, now=1000, installation_exists=alive).status == "bound"
    assert store.is_licensed(A, now=1001) and not store.is_licensed(B, now=1001)
    moved = store.activate(key, B, now=1002, installation_exists=alive)
    assert (moved.status, moved.previous_installation) == ("moved", A)
    assert store.is_licensed(B, now=1003) and not store.is_licensed(A, now=1003)

def test_second_move_within_cooldown_is_refused(tmp_path):
    store = LicenseStore(tmp_path / "licenses.json")
    key = store.issue(now=0)
    store.activate(key, A, now=0, installation_exists=alive)
    store.activate(key, B, now=10, installation_exists=alive)
    refused = store.activate(key, A, now=20, installation_exists=alive)
    assert (refused.status, refused.retry_after) == ("in_use", MOVE_COOLDOWN_SECONDS - 10)
    assert store.is_licensed(B, now=20)
    assert store.activate(key, A, now=10 + MOVE_COOLDOWN_SECONDS, installation_exists=alive).status == "moved"

def test_reactivating_same_installation_is_idempotent(tmp_path): ...  # status "bound", moved_at unchanged

def test_binding_to_deleted_installation_rebinds_without_cooldown(tmp_path):
    # bind A, move to B (sets moved_at), then installation_exists(B) is False:
    # activate for A at now=moved_at+1 → "bound", is_licensed(A)

def test_deactivate_clears_binding_and_cooldown(tmp_path):
    # bind A, move to B, deactivate(B) → True; activate A immediately → "bound"

def test_unknown_revoked_and_expired_keys_are_invalid(tmp_path):
    # unknown generate_key() → "invalid"; revoke(key) then activate → "invalid";
    # issue(now=0, days=1) → is_licensed at 86400 is False, activate at 86400 → "invalid"

def test_reset_unbinds_without_cooldown(tmp_path): ...  # move A→B, reset(key), activate A → "bound"

def test_file_holds_digests_only(tmp_path):
    store = LicenseStore(tmp_path / "licenses.json")
    key = store.issue(now=0, note="friend")
    text = (tmp_path / "licenses.json").read_text()
    assert normalize_key(key) not in text and hashlib.sha256(normalize_key(key).encode()).hexdigest() in text

def test_corrupt_license_file_fails_closed(tmp_path):
    path = tmp_path / "licenses.json"
    path.write_text("{not json")
    store = LicenseStore(path)
    assert store.is_licensed(A, now=0) is False
    with pytest.raises(LicenseStoreError):
        store.activate(generate_key(), A, now=0, installation_exists=alive)
    assert path.read_text() == "{not json"

def test_second_store_instance_sees_admin_changes(tmp_path):
    # broker = LicenseStore(p); admin = LicenseStore(p); admin.issue → broker.activate works;
    # admin.revoke → broker.is_licensed False without reconstructing broker
```

- [ ] **Step 2: Run, expect FAIL (`ModuleNotFoundError: broker.license_store`)**

Run: `uv run pytest tests/test_broker_license.py -v`

- [ ] **Step 3: Implement `src/broker/license_store.py` per the Interfaces block**

`list()` returns `digest_prefix = digest[:8]`. `issue` retries on the (theoretical) digest collision.

- [ ] **Step 4: Run, expect PASS**

Run: `uv run pytest tests/test_broker_license.py -v`

- [ ] **Step 5: Commit**

```bash
git add src/broker/license_store.py tests/test_broker_license.py
git commit -m "feat(broker): store relay license keys bound to one installation"
```

---

### Task 2: Broker license endpoints, relay gate and status

**Files:**
- Modify: `src/broker/app.py` (`BrokerSettings`, `create_broker_app`, `host_auth` branch)
- Modify: `src/broker/media.py` (`MediaAdmission.__init__`, `_bundle`)
- Modify: `src/broker/identity_store.py` (`delete`)
- Modify: `src/broker/main.py` (`REMOTE_LICENSE_PATH`, optional)
- Modify: `infra/shared-remote/compose.production.yml` (broker env `REMOTE_LICENSE_PATH: /data/licenses.json`)
- Modify: `infra/shared-remote/edge.conf.template:26` (regex → `^/installations(/[a-f0-9]{32}(/license)?)?$`)
- Test: `tests/test_broker_license.py` (HTTP + admission section), `tests/test_broker_media.py`

**Interfaces:**
- Consumes: `LicenseStore`, `Activation` (Task 1).
- Produces:
  - `BrokerSettings.license_path: str | None = None` — `None` → `Path(storage_path).with_name("licenses.json")`.
  - `MediaAdmission(settings, limits, registry, clock, licenses: LicenseStore)`; relay is issued iff `settings.relay_available and settings.turn_urls and licenses.is_licensed(installation_id, now=now)`.
  - `POST /installations/{installation_id}/license` body `{"credential": str, "key": str}` → `200 {"relay_licensed": true}` | 401 | 403 `invalid_license` (malformed, unknown, revoked and expired keys all look identical) | 409 `license_in_use` + `retry_after`.
  - `InstallationStore.delete(installation_id: str) -> bool` in `src/broker/identity_store.py` — removes a record without its credential; used only by a license move.
  - `DELETE /installations/{installation_id}/license` body `{"credential": str}` → `200 {"relay_licensed": false}` | 401.
  - `host_auth` reply result gains `"relay_licensed": bool`.
  - `app.state.licenses` exposes the store for tests.

Move handling in the POST handler: on `Activation.status == "moved"`, run the same revocation sequence as `delete_installation` for `previous_installation` (cancel its viewers' media, `flush_cancellations()`, `registry.invalidate_installation`) and delete its record via a new `InstallationStore.delete(installation_id) -> bool` (no credential; the license move is the authority). Factor the shared sequence into one local `async def revoke_installation(installation_id)` used by both handlers. Authenticate the caller with `store.authenticate` before touching licenses; registration rate limit does not apply. `LicenseStoreError` → 503 `{"detail": "License storage unavailable"}`.

- [ ] **Step 1: Write failing tests**

```python
def test_unlicensed_installation_gets_stun_only_and_licensed_gets_turn(broker_env): ...
    # register host A, approve viewer, select → bundle has no turn: URLs, relay_available False
    # licenses.issue + POST /installations/A/license → 200 relay_licensed True
    # new select → bundle has turn: URL with username containing A, relay_available True

def test_license_endpoint_requires_installation_credential(broker_env):
    # wrong credential → 401, license unchanged

def test_invalid_and_malformed_keys_return_same_403(broker_env):
    # "EMU-AAAAA" and unknown generate_key() → both 403 {"detail": "invalid_license"}

def test_move_revokes_previous_installation_and_its_viewers(broker_env):
    # A licensed with a connected host socket + viewer; B POSTs same key →
    # 200; A's host socket closed; store.exists(A) False; A's viewer closed

def test_move_within_cooldown_returns_retry_after(broker_env):
    # A→B move, then A' (fresh registration) POST → 409, body retry_after == 604800 - elapsed

def test_deactivate_turns_relay_off(broker_env): ...

def test_host_auth_reports_license_status(broker_env):
    # host_auth result["relay_licensed"] False before, True after activation (reconnect)

def test_revoked_license_stops_relay_on_next_admission(broker_env):
    # licensed select has TURN; licenses.revoke(key); renew/select → STUN-only, no exception
```

Add to `tests/test_broker_media.py` `admission` fixture a `LicenseStore(tmp_path / "licenses.json")` with installation `"a"` licensed, so existing TURN-issuing assertions keep passing.

- [ ] **Step 2: Run, expect FAIL**

Run: `uv run pytest tests/test_broker_license.py tests/test_broker_media.py -v`

- [ ] **Step 3: Implement the Interfaces block**

- [ ] **Step 4: Run the full broker suite, expect PASS**

Run: `uv run pytest tests/ -k "broker or remote_deployment" -v`

- [ ] **Step 5: Commit**

```bash
git add src/broker infra/shared-remote/compose.production.yml infra/shared-remote/edge.conf.template tests/test_broker_license.py tests/test_broker_media.py
git commit -m "feat(broker): issue relay credentials only to licensed installations"
```

---

### Task 3: Operator admin CLI

**Files:**
- Create: `src/broker/admin.py`
- Modify: `infra/shared-remote/README.md` (new "License keys" section: `docker exec shared-remote-broker python -m broker.admin ...`)
- Test: `tests/test_broker_admin.py`

**Interfaces:**
- Consumes: `LicenseStore`, `normalize_key` (Task 1).
- Produces: `main(argv: list[str] | None = None, *, now: Callable[[], float] = time.time) -> int`; license path from `--path` or env `REMOTE_LICENSE_PATH`, else `/data/licenses.json`.
  - `issue [--days N] [--note TEXT]` → prints key, exit 0.
  - `reset KEY` / `revoke KEY` → prints `ok` exit 0; unknown key → stderr `unknown key`, exit 1.
  - `list` → one line per key: `<digest8> <installation or -> issued=<iso> bound=<iso|-> moved=<iso|-> expires=<iso|-> <revoked|active> <note>`.

- [ ] **Step 1: Write failing tests** (`capsys`; `test_issue_prints_key_once_and_list_never_shows_it`, `test_reset_and_revoke_unknown_key_exit_1`, `test_revoke_visible_to_running_store` — a separate `LicenseStore` on the same path reports `is_licensed` False after `main(["revoke", key])`)
- [ ] **Step 2: Run, expect FAIL** — `uv run pytest tests/test_broker_admin.py -v`
- [ ] **Step 3: Implement with `argparse`**
- [ ] **Step 4: Run, expect PASS**
- [ ] **Step 5: Commit** — `feat(broker): add license key admin command`

---

### Task 4: `viewer_auth` rate limit

**Files:**
- Modify: `src/broker/limits.py`
- Modify: `src/broker/app.py` (`websocket_endpoint`, `viewer_auth` branch, before `forward`)
- Test: `tests/test_broker.py`

**Interfaces:**
- Produces: `BrokerLimits.check_viewer_auth(ip: str, installation_id: str) -> bool` — consumes one token from `f"vauth_ip_{ip}"` (capacity 10, refill 10/60 s) and `f"vauth_inst_{installation_id}"` (capacity 30, refill 30/60 s); returns `False` if either is empty, without consuming the other.

On `False`: send `format_error_reply(command.id, ErrorCode.QUOTA_EXCEEDED, "Authentication quota exceeded")` and return (socket closes in `finally`). Nothing is forwarded to the PC.

- [ ] **Step 1: Write failing tests** — `test_viewer_auth_eleventh_attempt_per_ip_is_refused_without_reaching_host` (host socket receives exactly 10 routed `viewer_auth` frames), `test_viewer_auth_installation_budget_spans_ips` (unit test on `BrokerLimits` with injected clock: 30 different IPs pass, 31st fails, refill after 2 s allows one)
- [ ] **Step 2: Run, expect FAIL** — `uv run pytest tests/test_broker.py -k viewer_auth -v`
- [ ] **Step 3: Implement**
- [ ] **Step 4: Run, expect PASS**
- [ ] **Step 5: Commit** — `fix(broker): rate-limit viewer authentication attempts`

---

### Task 5: Coturn per-credential quota and wider relay range

**Files:**
- Modify: `infra/shared-remote/turnserver.production.conf.template` (`max-port=50159`, add `user-quota=4` after `total-quota`)
- Modify: `infra/shared-remote/turnserver.test.conf` (`max-port=50159`, `user-quota=4`)
- Modify: `infra/shared-remote/README.md:94,103,104,179` (49160–50159)
- Test: `tests/test_remote_deployment.py`

- [ ] **Step 1: Write failing test** — `test_turn_configs_cap_allocations_per_credential_and_share_port_range`: both configs contain `user-quota=4`, `min-port=49160`, `max-port=50159`; README contains `49160-50159` and no `49200`.
- [ ] **Step 2: Run, expect FAIL** — `uv run pytest tests/test_remote_deployment.py -v`
- [ ] **Step 3: Edit configs and README**
- [ ] **Step 4: Run, expect PASS**
- [ ] **Step 5: Commit** — `fix(infra): cap TURN allocations per credential and widen relay ports`

---

### Task 6: PC-side activation and status

**Files:**
- Modify: `src/server/remote_identity.py` (`RemoteIdentityStore`)
- Modify: `src/server/remote_client.py` (`RemoteHostClient._reconnect` host_auth handling)
- Test: `tests/test_remote_identity.py`, `tests/test_remote_client.py`

**Interfaces:**
- Consumes: endpoints from Task 2.
- Produces:
  - `class LicenseError(RemoteIdentityError)` with attribute `code: Literal["invalid_license", "license_in_use", "unavailable"]` and `retry_after: int | None`.
  - `RemoteIdentityStore.activate_license(key: str) -> bool` — `normalize_key` locally first (raise `LicenseError("invalid_license")` without a request); POST with stored identity; returns `relay_licensed`. 403 → `invalid_license`; 409 → `license_in_use` with `retry_after`; network/other → `unavailable`. Never stores the key.
  - `RemoteIdentityStore.deactivate_license() -> bool` — DELETE; returns `relay_licensed` (False).
  - `RemoteHostClient.relay_licensed: bool | None` — `None` until first auth; set from `host_auth` result (missing key → `False`); `_notify()` after it changes.

`_http_json` currently raises on non-2xx via `urllib`; catch `HTTPError`, read its JSON body (same 16 KiB cap) to map 403/409.

A moved-away PC learns it on its next reconnect: its installation was deleted, so `host_auth` returns `not_paired`. Add `RemoteIdentityStore.discard_identity() -> None` (deletes the local identity file; no network). In `_reconnect`, a `not_paired` host_auth reply calls `discard_identity()`, notifies once with `"This PC's relay license moved to another PC. Restart EmuCtrl to reconnect; remote phones must pair again."`, and stops the reconnect loop instead of retrying. The next launch registers a fresh, unlicensed identity through the existing `load_or_register`. `RemoteHostClient` receives the identity store via a new keyword `identity_store=None` (wired in `src/main.py:build_remote_client`).

- [ ] **Step 1: Write failing tests** — fake `request` callable: `test_activate_license_maps_403_409_and_network_errors`, `test_activate_rejects_malformed_key_without_request`, `test_activation_never_persists_key` (identity file bytes unchanged), `test_discard_identity_removes_file`; in `test_remote_client.py`: `test_host_auth_sets_relay_licensed`, `test_not_paired_host_auth_discards_identity_and_stops_reconnecting` (connector called once, `discard_identity` called once, one notification)
- [ ] **Step 2: Run, expect FAIL** — `uv run pytest tests/test_remote_identity.py tests/test_remote_client.py -v`
- [ ] **Step 3: Implement**
- [ ] **Step 4: Run, expect PASS**
- [ ] **Step 5: Commit** — `feat(remote): activate relay license keys from the PC`

---

### Task 7: Launcher license controls

**Files:**
- Modify: `src/gui/launcher.py` (devices/remote section near `_remote_link_label`, ~line 173)
- Modify: `src/main.py` (`build_remote_client` passes `store` + client to the launcher callbacks)
- Test: `tests/test_launcher_widget.py`

**Interfaces:**
- Consumes: `activate_license`, `deactivate_license`, `LicenseError`, `RemoteHostClient.relay_licensed` (Task 6).
- Produces: `LauncherWindow(..., license_actions=None)` where `license_actions` has `activate(key: str) -> bool` and `deactivate() -> bool`; `LauncherWindow.set_relay_licensed(value: bool | None)`.

UI: a "Relay license" row — `QLineEdit` (placeholder `EMU-XXXXX-XXXXX-XXXXX-XXXXX`), **Activate** button, status label, **Deactivate license** button (visible only when active). Run calls off the Qt thread exactly like the existing remote pairing request (`_remote_pairing_ready` pattern), result delivered via a new `pyqtSignal`. Copy:
- status `None` → hidden row; `True` → "Relay: active on this PC"; `False` → "Relay: not activated — remote works only when a direct connection is possible".
- `invalid_license` → "That license key is not valid."
- `license_in_use` → "This key was moved recently. Try again in N days." (N = ceil(retry_after / 86400))
- `unavailable` → "Could not reach the remote service. Try again later."
- Helper text under the field (the PC cannot know beforehand whether the key is bound elsewhere, so no dialog): "Using a key that is active on another PC moves it here and disconnects that PC and its paired phones."
Clear the key field after success.

- [ ] **Step 1: Write failing tests** — `test_license_row_hidden_until_status_known`, `test_activate_success_shows_active_and_clears_field` (fake actions, `qtbot.waitUntil`), `test_license_in_use_shows_days`, `test_license_activation_failure_keeps_launcher_usable` (actions raise `LicenseError("unavailable")`; Pair device button still enabled), `test_deactivate_hides_button_and_shows_not_activated`
- [ ] **Step 2: Run, expect FAIL** — `uv run pytest tests/test_launcher_widget.py -k license -v`
- [ ] **Step 3: Implement launcher row and `main.py` wiring**
- [ ] **Step 4: Run full suite, expect PASS** — `uv run pytest tests/ -v`
- [ ] **Step 5: Commit** — `feat(gui): add relay license activation to the launcher`

---

## Deployment checklist (after Task 7, on the VPS)

- [ ] Open UDP/TCP 49160–50159 in the provider firewall and host nftables.
- [ ] Redeploy compose; confirm `/data/licenses.json` is created on first `admin issue`.
- [ ] `admin issue --note self` → activate on your PC → launcher shows "Relay: active".
- [ ] Force relay (test-only relay forcing) from the phone and stream 720 for 10 minutes; record route, bitrate, loss, freezes in `docs/testing/shared-remote-validation.md`.
- [ ] Activate the same key on a second PC: first PC's launcher reports the move; a third attempt within 7 days shows the days message.
