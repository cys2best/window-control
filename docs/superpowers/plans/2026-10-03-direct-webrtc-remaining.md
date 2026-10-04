# Remaining Direct WebRTC and Shared TURN Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Finish authenticated remote streaming on the published feature branch, preserving LAN behavior and proving the approved Windows/iPhone acceptance criteria.

**Architecture:** Continue the existing Python-host WSS bridge and PC-authoritative authorization. Complete the broker's media admission and ephemeral TURN issuance, adapt the private native peer API, and share the existing WebRTC/input lifecycle across local and remote clients. Deployment tests establish service behavior; packaged Windows/iPhone measurements establish media compatibility and quality.

**Tech Stack:** Python/FastAPI/httpx/websockets/PyQt; C++20/libdatachannel 0.21.1 with libnice; TypeScript/React/Next.js/Expo/react-native-webrtc/Jest; Docker/coturn; existing Windows GitHub Actions packaging.

**Spec:** [Approved design](../specs/2026-10-02-direct-webrtc-shared-turn-design.md). Read it alongside this continuation of [the original plan](2026-10-02-direct-webrtc-shared-turn.md), Tasks 6–11.

**Revision:** October 4, 2026. Code examples and executable test bodies follow the format of [the October 1 engine/verifier plan](2026-10-01-engine-and-verifier-cleanup.md). This revises the approved continuation in place; task numbers and workflow tracking are preserved. Examples describe code to add, not tests already run.

## Global Constraints

The following requirements are copied verbatim from the approved design:

- “Remote use must work without a Tailscale account, tailnet policy changes, a personal VPS, or router configuration.”
- “Product assumptions for this design: no app login is introduced; the PC owner authorizes phones through pairing. Initial support is one active remote stream per PC installation.”
- “Only the Python host maintains a public WSS connection. Do not restore a second, independent C++ public signaling client.”
- “The existing local `AccessGate` stays restrictive, with `proxy_headers=False`.”
- “Retain the current six-digit code, 300-second lifetime, single use, and invalidation after five wrong attempts.”
- “For remote pairing, also create an unguessable rendezvous handle containing at least 128 random bits.”
- “The service does not persist viewer tokens or log them.”
- “Use a 30-second overall remote setup deadline; gathering and command dispatch consume that deadline rather than each resetting it.”
- “ICE uses all supported host, server-reflexive, and relay candidates without stripping address families or forcing relay in normal use.”
- “Use coturn secret-derived credentials with a 3,600-second validity, scoped in the username to installation, session, and endpoint.”
- “Service disconnect: retain an already working media peer for a bounded 60-second reconnect grace period, then close if authorization cannot be re-established.”
- “Revocation stops active input and media within five seconds while the PC is connected.”
- “On a controlled path independently sustaining at least 12 Mbps, a landscape 1920×1080 source streams at the 8 Mbps target and at least 30 decoded FPS for ten minutes.”
- “Verify actual dimensions, zero adaptive downgrades, no single freeze over one second, and under 1% total freeze time.”
- “All connectivity/media cases use the actual packaged Windows engine and iPhone client, with Tailscale disabled and no router changes. Unit tests on macOS cannot establish these results.”
- “No new paid resources or production service are created by this design change.”

Project execution rules: prefix shell commands with `rtk`; Python runs through `uv run`. Build native C++ on Windows. Stage explicit task paths; preserve the dirty design document, `patch_app.py`, `patch_test.py`, and `scratch/`. Do not edit plugin workflow state by hand. Use imperative Conventional Commits without task numbers or attribution.

## Review Focus

1. The engine adopts a peer but its response disappears: cleanup must know the attempt ID, work after source outage/reconnect, and preserve local/successor peers. Task 1 tests.
2. The viewer disappears after the host has completed its command: cancellation must reach the retained media session, while an old cancellation cannot retire its successor. Tasks 2–3 tests.
3. An old installation's delayed preview or storage callback completes after switching targets: no image, token deletion, or selected session may affect the new installation. Tasks 4 and 6 tests.
4. Stats counters reset, selected-pair linkage changes, or native stats omit fields: diagnostics and exports must reset baselines and report unknown/inconclusive rather than invented route, FPS, or freeze evidence. Tasks 7 and 11 tests.
5. Concurrent registrations or corrupt persisted broker data occur during restart: identities must not be lost or silently replaced, and unrelated active streams must survive quota rejection. Tasks 2, 9–10 tests.

---

## Starting point and task boundaries

Execution method is already chosen: **subagents**, one implementer followed by an independent reviewer for each delivery unit, then a whole-branch review. Execute sequentially because the interfaces below are shared. The controller self-reviews this plan; do not delegate planning.

Working checkout: `.worktrees/direct-webrtc-shared-turn`; published branch: `feat/direct-webrtc-shared-turn`; current committed baseline: `1c943f4`. Original Tasks 1–5 and continuation Task 1 have implementations and reviews. Continuation Task 1's Windows [run 37114034029](https://github.com/cys2best/window-control/actions/runs/37114034029) at `1c943f4` built the engine, passed 124 native tests, and packaged the installer. This is recorded prior evidence, not final runtime/iPhone acceptance. Do not repeat completed implementation or modify its workflow checkmarks.

Task 2 has uncommitted broker/protocol/test changes from an interrupted implementation worker. `src/server/remote_sessions.py` and its tests remain partial Task 3 work. Preserve these changes and inspect their actual diff before extending them; existing passing cases are not a new RED cycle. Start execution at Task 2: add uncovered regressions, verify, review and commit it before Task 3. The task report, independent review and actual verification output determine completion, not the presence of files.

| New task | Original scope | Responsibility and primary files |
| --- | --- | --- |
| 1 | 6, native corrective work | `engine/src/remote_peer_handler.*`, `peer_registry.*`; `src/server/engine_remote.py`: attempt identity, atomic cancellation fences, fixed loopback adapter |
| 2 | 6 | `src/broker/media.py` (new), `turn_credentials.py`, `registry.py`, `app.py`, `limits.py`; `src/remote_protocol.py`: PC-approved admission and private lifecycle messages |
| 3 | 6 | `src/server/remote_sessions.py`, `remote_client.py`, `remote_dispatch.py`, `src/main.py`: media ownership, authorization, teardown, renewal |
| 4 | 7 | `packages/core/src/remote/{protocol,client,pairing}.ts`, `api/target.ts`, `api/ServerContext.tsx`: typed remote client and installation storage |
| 5 | 8 | `packages/core/src/webrtc/{peer,remote,whep,session}.ts`; `packages/ui/src/screens/Stream.tsx`: shared peer lifecycle and replacement |
| 6 | 9 | Pair/list/drawer UI; web pairing page; mobile invitation routing: pairing and bounded previews |
| 7 | 9 | `packages/core/src/webrtc/telemetry.ts`, UI diagnostics, `webrtc/measurement.ts` (new): selected-route reporting and export |
| 8 | 9 | `src/config.py`, `tests/test_config.py`, `tests/test_scrcpy_server.py`: quality dimension correction |
| 9 | 10, persistence repair | `src/broker/identity_store.py`, `tests/test_broker_identity.py`: durable installation isolation |
| 10 | 10 | `infra/shared-remote/`, integration tests, CI: runnable local stack and concrete production templates |
| 11 | 11 | `scripts/validate_remote_stream.py`, assessor tests: honest release-evidence evaluation |
| 12 | 11 | Windows build/device runbooks and recorded results: actual compatibility, sustained media, final branch review |

Tests live beside TypeScript files and under `tests/` / `engine/test/` as below. Reuse existing files rather than restructuring unrelated code. New `broker/media.py` owns media admission; `registry.py` retains socket/RPC routing.

## Contracts fixed for all remaining tasks

Public JSON command: `{v:1,id:UUID,op,payload}`. Reply: `{v:1,id,ok:true,result}` or `{v:1,id,ok:false,error:{code,message}}`. Keep the existing Python parser's strict field validation, duplicate-key rejection, and limits: 1 MiB UTF-8 frame, 128 KiB SDP, 384 KiB raw preview, 32 pending/viewer, 256 pending/installation, 32 host concurrent commands and outbound queue entries. Authenticate in the first frame within 10 seconds; never place remote secrets in URLs.

| Operation | Exact public payload |
| --- | --- |
| `viewer_auth` | `{installation_id,token}` |
| `pair` | `{handle,code,device_name?}` |
| `instances` | `{}` |
| `select`, `preview`, `keyframe` | `{serial}` |
| `quality` | `{serial,tier}` |
| `negotiate` | `{session_id,generation,offer,timeout_ms}` |
| `close`, `renew` | `{session_id,generation}` |

Public session IDs are UUID strings. Public `generation` is a positive, installation-wide coordinator revision; it is **distinct from private native generation**, which starts at zero. `timeout_ms` is remaining positive setup budget, at most 30,000. The viewer starts one monotonic deadline **before select**, then spends that same budget on gathering, exchange, and adoption. Broker/host subtract their queue time; the viewer rejects late answers. No shared-clock precision is assumed for network transit.

Keep all existing error codes: `not_paired`, `offline`, `expired_pairing`, `invalid_request`, `busy`, `stale_generation`, `unavailable`, `relay_unavailable`, `quota_exceeded`, `timeout`, `canceled`. Public results for pair remain `{installation_id,token}`; viewer auth remains `{authenticated:true,viewer_id}`. Internal device IDs stay private.

Define Python `SessionIceBundle` as a strict model with `ice_servers:list[dict]`, `expires_at:int` (Unix seconds), `renew_after:int` (seconds, 3300), `relay_available:bool`. Define `MediaBundles` with `host:SessionIceBundle` and `viewer:SessionIceBundle`. Even STUN-only selections expire logically at 3600 seconds so renewal follows one lifecycle. Public select/renew result: `{ok:true,id,serial,name,w,h,tier,session_id,generation,ice_servers,expires_at,renew_after,relay_available}`. Omit private engine URLs, capabilities, native generation, and host credentials. Negotiate result: `{session_id,generation,answer}`. Close result: `{closed:true}`.

Private host messages use separate strict models, never expand viewer `Command`:

- `MediaAuthorization`: envelope op `media_authorize`, payload `{routing_id:UUID,session_id:UUID,generation:positiveInt}`. Broker derives installation, host epoch, viewer and device from the **live pending select/renew** identified by `routing_id`; host cannot supply ownership.
- `MediaRelease`: op `media_release`, payload `{session_id:UUID,generation:positiveInt}`; reply `{released:true}` after matching current host/admission, also for an already gone matching session.
- `MediaCancellation`: broker-to-host op `media_cancel`, payload `{installation_id,host_epoch,viewer_id,routing_id?}`; no token, no reply. Absence of `routing_id` retires that viewer's session; presence cancels that request and its retained session. Parse role and epoch before acting.
- Existing `device_invalidated`, host authentication, pairing-open/close contracts remain unchanged.

Renewal revalidates the device and returns a **fresh session**, credentials, and revision; close the old engine peer before admitting its replacement. Broker restart drops transient viewers/admissions. A reconnecting viewer authenticates again and selects a new session; existing media may survive only the original 60-second grace until this replacement or closure. Reconnecting a socket alone never resets that grace or replays old requests.


### Implementation rulings to preserve

Public revision high-water marks are scoped to `(installation_id, authenticated host epoch)`. A new PC process can start at revision 1 only after exact predecessor release or expiration of its original grace; it still uses a fresh session UUID. Do not introduce a durable revision counter. A current authenticated host can release an exact predecessor session/revision after PC cleanup even if that admission was created in an older epoch; an old socket cannot release anything, and an old session/revision cannot remove a successor.

Control-only viewer disconnects send no `media_cancel` unless the viewer requested media or retains media correlation. Media cancellation continues after a routed request has completed. Keep the existing blocked synchronous mutation regression: migrate its `select` path through PC-approved media authority instead of bypassing admission or weakening the mutation fence.

Code below is deliberately more explicit than the skill's default, as requested. Production excerpts are labeled; they belong inside the named existing methods and do not replace their other validation. Test bodies use imports and helpers defined here or explicitly identified in the existing test file. RED/GREEN expectations are prospective. If partial code already passes a new case, retain it and test the uncovered failure next; do not manufacture a failing run.

## Task 1: Make native peer cleanup reliable and finish the engine adapter

**Files:** Modify `engine/src/remote_peer_handler.{h,cpp}`, `engine/src/peer_registry.{h,cpp}`, `engine/test/test_remote_peer_handler.cpp`, `engine/test/test_peer_registry.cpp`, `tests/test_engine_sources.py`; extend `src/server/engine_remote.py`, `tests/test_engine_remote.py`, existing dirty runtime/orchestrator endpoint changes and their tests. Modify `engine/CMakeLists.txt` only if test wiring requires it.

**Interfaces:** Extend the partial frozen endpoint to `RemoteEngineEndpoint(admin_port:int,capability:str,generation:int,owner:EngineInstance)`; retain `RemotePeerAnswer(peer_id:str,answer:str,generation:int)`, with secrets/owner excluded from repr. `owner` retains the actual engine handle: identity comparison detects a replacement and `owner.is_running()` proves whether the old process exited. Produce `EngineRemoteClient(http_client:httpx.AsyncClient|None=None)`, async `negotiate(endpoint,session_id:str,offer:str,ice_servers:list[dict],deadline:float,*,on_attempt:Callable[[str],None]|None=None)->RemotePeerAnswer`, `close(endpoint,peer_id:str)->None`, `aclose()->None`. Before POST, synchronously publish the generated attempt ID through `on_attempt`, so the coordinator can retry cleanup even if both POST and DELETE responses disappear. Endpoint generation is native. Retain `EngineRuntime.remote_endpoint()->RemoteEngineEndpoint|None` and `EngineOrchestrator.remote_endpoint(serial:str)->RemoteEngineEndpoint|None`; each call mints a fresh capability.

**Completed code to verify, not reimplement:** `engine/test/test_remote_peer_handler.cpp` already contains this test (using its existing fixture, `kAuth`, `Body` and `GatheredOffer` helpers):

```cpp
TEST_F(RemotePeerHandlerTest, CancelBeforeAdoptRejectsAttempt) {
    const std::string id = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
    httplib::Client client("127.0.0.1", admin.Port());
    auto deleted = client.Delete("/admin/remote-peers/" + id, kAuth);
    ASSERT_TRUE(deleted);
    EXPECT_EQ(deleted->status, 204);
    auto pending = std::make_shared<PeerSession>(id, std::vector<std::string>{});
    std::vector<std::shared_ptr<PeerSession>> retired;
    EXPECT_FALSE(registry.AdoptPublic(pending, 0, retired));
    EXPECT_EQ(registry.Find(id), nullptr);
    pending->Close();
    auto body = Body(GatheredOffer()); body["peer_id"] = id;
    auto posted = client.Post("/admin/remote-peers", kAuth, body.dump(), "application/json");
    ASSERT_TRUE(posted);
    EXPECT_NE(posted->status, 201);
    EXPECT_FALSE(registry.HasPublicPeer());
}
```

This pins DELETE-before-adoption at the actual HTTP/registry boundary. Preserve `on_attempt` and the required `owner` argument when updating Task 3 fakes. Add the remaining conflicting-live-owner regression during Task 3 teardown verification. The checklist below records the original task definition; its completed status lives in the controller/task reports.

- [ ] **Step 1: Add failing native regression tests.** `CancelBeforeAdoptRejectsAttempt`: DELETE of a known 32-hex attempt before adoption yields 204 and subsequent adoption fails. `DeleteDuringOutageAndAfterGenerationChange`: the exact public peer is gone in both cases, local count unchanged. `OldDeletePreservesSuccessor`: deleting A cannot remove B. `CancellationFenceSaturationFailsClosed`: 4096 live fences cannot evict one to admit another; expiry after 120 monotonic seconds frees capacity. Preserve omitted-`peer_id` POST compatibility.
- [ ] **Step 2: Add adapter assertions to `tests/test_engine_remote.py`.** Extend the existing four-case lost-response/canceled/wrong-generation/oversized test: POST includes a unique `peer_id` on every attempt; final DELETE uses that ID; `live == set()` after unsuccessful adoption. Add tests rejecting redirects, over-budget/expired offers and unexpected response fields; assert the fixed request host is always `127.0.0.1` and no offer/capability appears in repr/errors. Fake owner handles implement `is_running`; endpoint recreation retains owner identity until respawn, and port reuse with a different live owner cannot cause an old capability to be sent to it.
- [ ] **Step 3: Run the focused tests before implementation.** `rtk proxy uv run pytest tests/test_engine_remote.py tests/test_engine_sources.py -v`; expect missing `EngineRemoteClient`/new behavior failures. For native RED without direct Windows-runner access, the controller publishes an explicit test-first checkpoint to a temporary test branch and dispatches the existing Windows workflow; record its actual failing tests separately from Python source guards. Final GREEN runs against the feature branch, with no force-push.
- [ ] **Step 4: Extend the private POST and atomic registry boundary.** POST accepts optional lowercase 32-hex `peer_id`; omitted value still generates one. Add `bool PeerRegistry::CancelPublicAttempt(const std::string& id,std::shared_ptr<PeerSession>& retired)`: return false if a required fence cannot be retained; otherwise record a 120-second fence and detach only that public peer under the registry lock, then close outside locks. `AdoptPublic` checks the same fence atomically with insertion. Bound fences at 4096, return 503 on saturation, never evict a live fence. Authenticated exact DELETE does not depend on source usability/current generation and returns 204 for a fenced missing ID. Keep source-generation checks on POST adoption and local deletion rules intact.
- [ ] **Step 5: Implement the fixed loopback adapter.** Construct only `http://127.0.0.1:<admin_port>/admin/remote-peers`, disable redirects, use bearer capability, generate one UUID-hex per attempt, pass only the remaining budget. Bound the response before JSON parsing and verify exact attempt/native generation/SDP size. On any failed/canceled adoption, supervise exact-ID DELETE independently of caller cancellation. Retain failed cleanup for the coordinator to retry; do not claim cleanup succeeded when HTTP failed. Close fresh-endpoint capabilities after long streams; never send a capability to a different engine process.
- [ ] **Step 6: Verify and commit.** Run focused Python tests plus `tests/test_engine_runtime.py` and `tests/test_engine_orchestrator.py`; expect PASS. Windows: build Release and `rtk proxy ctest --test-dir engine/build -C Release --output-on-failure`; require PASS for native fence and existing WHEP/input tests. Commit explicit paths with `fix(engine): close canceled remote peer attempts reliably`; reviewer checks locking and actual Windows evidence before Task 3 integration.

## Task 2: Add PC-approved broker admission and TURN issuance

**Files:** Create `src/broker/media.py`, `tests/test_broker_media.py`; extend `src/broker/turn_credentials.py`, `app.py`, `registry.py`, `limits.py`, `src/remote_protocol.py`, and `tests/test_turn_credentials.py`, `test_remote_protocol.py`, `test_broker.py`.

**Interfaces:** Define the three private models and ICE models above in `remote_protocol.py`; export strict `parse_media_authorization(raw:str)`, `parse_media_release(raw:str)`, `parse_media_cancellation(raw:str)` returning their models. Produce `MediaAdmission(settings:BrokerSettings,limits:BrokerLimits,registry:Registry,clock:Callable[[],float])`, synchronous `authorize(host:HostConnection,request:MediaAuthorization)->MediaBundles`, `release(host:HostConnection,session_id:str,generation:int)->bool`, `sweep(now:float)->None`. Add settings `stun_urls:list[str]`, `turn_urls:list[str]`, `max_active_streams:int` with positive validation, and `relay_available:bool`; production construction requires explicit capacity. Preserve `issue_turn_credentials(secret,installation_id,session_id,endpoint,now,ttl=3600)->IceBundle`.

**Test code for Steps 1–2:** Add to `tests/test_broker_media.py`. Reuse its existing `admission`, `owner`, `pending`, and `authorization` helpers: `admission` supplies `(media, registry, mutable_now, settings)`; `owner` creates a current host and PC-approved viewer; `pending` records a live routed request; `authorization` creates a fresh session UUID.

```python
@pytest.mark.asyncio
async def test_new_host_releases_predecessor_before_revision_restart(admission):
    from broker.media import MediaError

    media, registry, now, _ = admission
    old_host, old_viewer = owner(registry)
    old = authorization(pending(registry, old_viewer).routing_id, generation=7)
    media.authorize(old_host, old)
    media.admissions["a"].established = True
    media.host_disconnected(old_host)
    registry.remove_host(old_host)
    now[0] += 10
    new_host, new_viewer = owner(registry)
    fresh = authorization(pending(registry, new_viewer).routing_id, generation=1)

    with pytest.raises(MediaError, match="busy"):
        media.authorize(new_host, fresh)
    assert not media.release(old_host, old.payload["session_id"], 7)
    # The current PC reports exact predecessor cleanup, then requests a new session.
    assert media.release(new_host, old.payload["session_id"], 7)
    bundles = media.authorize(new_host, fresh)
    assert fresh.payload["session_id"] != old.payload["session_id"]
    assert media.admissions["a"].generation == 1
    assert bundles.viewer.renew_after == 3300
    assert not media.release(new_host, old.payload["session_id"], 7)
    assert media.admissions["a"].session_id == fresh.payload["session_id"]


@pytest.mark.asyncio
async def test_endpoint_credentials_are_distinct_and_secret_stays_private(admission):
    import base64
    import hashlib
    import hmac

    media, registry, now, settings = admission
    host, viewer = owner(registry)
    request = authorization(pending(registry, viewer).routing_id)
    bundles = media.authorize(host, request)
    for endpoint in ("host", "viewer"):
        bundle = getattr(bundles, endpoint)
        turn = bundle.ice_servers[1]
        expected_username = (
            f"{int(now[0]) + 3600}:a:{request.payload['session_id']}:{endpoint}"
        )
        expected_credential = base64.b64encode(hmac.new(
            settings.turn_shared_secret.encode(), expected_username.encode(), hashlib.sha1
        ).digest()).decode("ascii")
        assert turn["username"] == expected_username
        assert turn["credential"] == expected_credential
        assert bundle.expires_at == int(now[0]) + 3600
    assert bundles.host.ice_servers[1] != bundles.viewer.ice_servers[1]
    assert settings.turn_shared_secret not in bundles.model_dump_json()
```

**Production excerpt for Step 4:** In `MediaAdmission.release`, current socket authentication precedes exact session matching. Retain already-gone idempotence; a mismatched live successor returns false. In `authorize`, reject replay only within the same authenticated epoch and reject reused session IDs; preserve pending/PC approval, quota, expiry and original-grace checks.

```python
# release(host, session_id, generation), before touching live admission:
if self.registry.get_host(host.installation_id) is not host:
    return False
current = self.admissions.get(host.installation_id)
if current is not None:
    if (current.session_id, current.generation) != (session_id, generation):
        return False
    del self.admissions[host.installation_id]
    return True

# authorize(...), after validating current host and pending select/renew:
latest = self.latest.get(host.installation_id)
if latest is not None:
    same_epoch = latest[4] == host.epoch
    if (same_epoch and generation <= latest[1]) or session_id == latest[0]:
        raise MediaError("stale_generation")
```

Verify this plus the existing real-socket completed-session cancellation tests with the Step 3 command. Expected RED: authority/reconnect/privacy assertions fail if uncovered. Expected GREEN: all broker suites pass, no registration-issued ICE, no cancel for control-only disconnect, and B's admission survives A's quota failure.

- [ ] **Step 1: Write failing issuance/admission tests.** In a real broker socket fixture: `assert register_only.turn_credentials is None`; authorization referencing a pending preview, foreign request, stale host epoch, completed request, or unauthorized viewer returns `invalid_request`/`not_paired` with no allocation admitted. First valid select admits one installation slot; renewing/replacing the matching slot keeps total one; exceeding configured aggregate capacity returns `quota_exceeded` and installation B's admission is unchanged. Issuance #13 in one hour is rejected; registration does not consume credential issuance.
- [ ] **Step 2: Write cancellation/expiry assertions.** `test_timeout_after_host_reply_sends_media_cancel`: completed native adoption with lost viewer reply is retired. `test_viewer_disconnect_cancels_completed_session`: no pending RPC is required to send viewer-scoped cancel. `test_old_cancel_and_release_preserve_successor`: old epoch/session/revision cannot remove current admission. Host disconnect retains its slot no longer than 60 seconds; a new epoch cannot inherit authorization. STUN-only bundle asserts `relay_available is False` and STUN URLs are retained.
- [ ] **Step 3: Run RED.** `rtk proxy uv run pytest tests/test_broker_media.py tests/test_turn_credentials.py tests/test_remote_protocol.py tests/test_broker.py -v`; new media behavior fails while existing owner-routing cases remain green.
- [ ] **Step 4: Implement the private authority and routing lifecycle.** Add pending operation metadata before forwarding; derive all ownership from it. Store one admission per installation with session/revision/host epoch/viewer/device and its setup request correlation. Issue separate HMAC-SHA1/base64 host/viewer credentials, expiry 3600, renewal 3300; signing secret remains server-only. Track completion long enough to clean undelivered media answers; send scoped cancellation on command timeout, failed delivery and viewer disconnect. Release failed/unnegotiated admissions after the 30-second setup window; superseding or canceled work cannot resurrect them. Current-host replacement requires fresh PC approval and fencing of the previous slot; use the bounded host grace rather than silently granting another allocation.
- [ ] **Step 5: Verify and commit.** Run the Step 3 command; all cases PASS, including bounds, malformed roles, and existing broker tests. Add a two-installation integration assertion: A's quota failure does not close B's socket or media admission. Commit explicit paths with `feat(broker): authorize bounded media sessions and temporary TURN access`; independent review checks parser roles and lifetime bounds.

## Task 3: Complete the host media coordinator and lifecycle integration

**Files:** Extend dirty `src/server/remote_sessions.py`, `tests/test_remote_sessions.py`; modify `src/server/remote_dispatch.py`, `remote_client.py`, `src/main.py`, `src/server/app.py`, `tests/test_remote_dispatch.py`, `tests/test_remote_client.py`; create `tests/test_remote_lifecycle.py`. Reuse existing runtime/orchestrator accessors from Task 1.

**Interfaces:** `RemoteSessionCoordinator(installation_id:str,actions:InstanceActions,pairing:PairingStore,endpoint:Callable[[str],RemoteEngineEndpoint|None],engine:EngineRemoteClient,*,authority:RemoteHostClient,clock=time.monotonic)`. Async `dispatch(command:Command,device:PairedDevice,*,context:RoutingContext,mutation_guard:MutationGuard|None=None)->dict`, `cancel(installation_id:str,host_epoch:int,viewer_id:str,routing_id:str|None=None)->None`, `sweep(now:float)->None`, `shutdown()->None`; synchronous `disconnected()->None`, `connected()->None`. `RemoteDispatcher.dispatch(command,token,*,trusted_context:RoutingContext|None=None)->Reply` wraps coordinator dictionaries consistently. Host produces async `authorize(routing_id,session_id,generation)->MediaBundles` and `release(session_id,generation)->None` over bounded correlated private requests.

**Fixture migration before the new tests:** In `tests/test_remote_sessions.py`, update the existing `Engine` fake to implement Task 1's actual owner/attempt contract. Keep its block/release controls; use this method body so late cleanup targets the known attempt. The fake owner stays identical across capability refreshes; a respawn test supplies a different owner object.

```python
# In Engine.__init__:
self.owner = SimpleNamespace(is_running=lambda: True)

# Replace Engine.endpoint:
def endpoint(self, serial):
    return RemoteEngineEndpoint(
        1234 if serial == "a" else 1235, "fresh", self.generation, self.owner
    )

# Replace Engine.negotiate:
async def negotiate(self, endpoint, session_id, offer, ice_servers, deadline, *, on_attempt=None):
    peer_id = uuid.uuid4().hex
    if on_attempt is not None:
        on_attempt(peer_id)
    self.started.set()
    if self.block:
        try:
            await self.release.wait()
        except asyncio.CancelledError:
            await self.release.wait()
    self.live.add(peer_id)
    return RemotePeerAnswer(peer_id, "v=0\r\n", endpoint.generation)
```

The `Authority` fake must return valid `MediaBundles`, including both endpoint expiries and `renew_after=3300`; keep host/viewer credentials distinct in renewal tests. Replace its existing authorize body with this valid STUN-only fixture, then extend the renewal case with distinct TURN credentials:

```python
# In Authority.__init__, retain self.released and add:
self.clock = lambda: 1000.0

# In setup(), after creating now and authority:
authority.clock = lambda: now[0]

# Replace Authority.authorize:
async def authorize(self, routing_id, session_id, generation):
    from remote_protocol import MediaBundles, SessionIceBundle
    def bundle():
        return SessionIceBundle(
            ice_servers=[{"urls": ["stun:stun.example:3478"]}],
            expires_at=int(self.clock()) + 3600, renew_after=3300, relay_available=False,
        )
    return MediaBundles(host=bundle(), viewer=bundle())
```

Declare `drain()->None` as an async coordinator method for waiting on retained cleanup tasks in tests and shutdown; it does not mean a failed DELETE is successful.

**Test code for Steps 1–2:** These reuse `setup`, `select`, and `negotiate` defined in that test file after the fixture migration.

```python
@pytest.mark.asyncio
async def test_completed_viewer_cancel_cannot_close_successor():
    sessions, engine, _, _, context, _ = setup()
    try:
        first = await select(sessions, context)
        await negotiate(sessions, context, first)
        await sessions.cancel(context.installation_id, context.host_epoch, context.viewer_id)
        await sessions.drain()
        assert engine.live == set()
        successor_context = context.model_copy(update={"viewer_id": "successor"})
        second = await select(sessions, successor_context)
        await negotiate(sessions, successor_context, second)
        successor_peers = set(engine.live)
        await sessions.cancel(context.installation_id, context.host_epoch, context.viewer_id)
        await sessions.drain()
        assert engine.live == successor_peers
        assert len(engine.live) == 1
    finally:
        await sessions.shutdown()


@pytest.mark.asyncio
async def test_socket_reconnect_without_fresh_approval_does_not_extend_grace():
    sessions, engine, _, _, context, now = setup()
    try:
        await negotiate(sessions, context, await select(sessions, context))
        sessions.disconnected()
        now[0] += 59
        sessions.connected()  # Socket only; no fresh PC-approved select.
        await sessions.sweep(now[0])
        assert len(engine.live) == 1
        sessions.disconnected()
        now[0] += 1
        await sessions.sweep(now[0])
        await sessions.drain()
        assert engine.live == set()
    finally:
        await sessions.shutdown()
```

**Production construction for Step 5:** In `build_remote_client`, retain its existing constructor arguments, then assign these relationships in this order: `dispatcher = RemoteDispatcher(...)`; `host = RemoteHostClient(..., dispatcher=dispatcher)`; `sessions = RemoteSessionCoordinator(..., authority=host)`; `dispatcher.sessions = sessions`. In `remote_client.py`, the one-second sweep must be an application-lifetime task beside the reconnect loop, not a child canceled when the socket closes. In coordinator teardown, release admission only after exact DELETE confirmation or proven owner exit; preserve failed attempts for retry.

**Owner check to replace in Step 4:** In `RemoteSessionCoordinator._fresh_endpoint`, compare retained process identity before permitting cleanup. The current partial code checks only the port and treats a missing endpoint as successful closure; replace that decision with the following body. `_cleanup_peer` may treat None as closed only because this method has proven owner exit. Move `authority.release` out of `_retire`'s unconditional `finally`; failed cleanup must retain admission for retry.

```python
async def _fresh_endpoint(self, session):
    retained = session.endpoint
    if retained is None:
        raise RemotePairingError(ErrorCode.UNAVAILABLE, "Remote peer cleanup is pending")
    fresh = await asyncio.to_thread(self.endpoint, session.serial)
    if fresh is not None and fresh.owner is retained.owner:
        return fresh  # Fresh bearer capability, exact same engine process.
    if not retained.owner.is_running():
        return None  # Proven exit; no old capability reaches a replacement.
    raise RemotePairingError(ErrorCode.UNAVAILABLE, "Remote peer cleanup is pending")
```

Step 3's GREEN also requires `test_reconnect_fences_blocked_synchronous_mutation_and_serializes_replacement[select]` to exercise the real coordinator/authority. Retain its blocked-thread assertion and shared-lock serialization; approving a sanitized host-only select reply is no longer a valid broker fixture. Add a different-still-live-owner port-reuse case asserting the old capability is never sent, failed cleanup fences the successor, and owner exit permits release.

- [ ] **Step 1: Complete the existing coordinator tests with precise assertions.** Switching serial A→B closes A before B can negotiate; `len(engine.live) <= 1` installation-wide. Native generation 0 produces positive public revision. Wrong viewer/device/epoch/revision yields `stale_generation` or `not_paired`, with no new peer. Cancel before answer and cancel after host task completion both leave `engine.live == set()`; a later cancel for A leaves successor B alive.
- [ ] **Step 2: Add real lifecycle integration tests.** A revoked token closes media/input on the once-per-second sweep (`elapsed <= 5`), including while WSS is down. Disconnect at t=0 keeps a working peer at t=59 and closes at t=60; repeated socket reconnects do not extend this timer. Fresh viewer auth plus select closes/replaces the old peer. At t=3300 renew closes old peer, returns a new session and distinct host/viewer credentials; revoked token cannot renew. Dropped authority/engine replies release admission and retry teardown without blocking Qt or registry metadata locks.
- [ ] **Step 3: Run RED.** `rtk proxy uv run pytest tests/test_remote_sessions.py tests/test_engine_remote.py tests/test_remote_dispatch.py tests/test_remote_client.py tests/test_remote_lifecycle.py -v`; record unimplemented integration failures rather than rewriting already green primitive tests.
- [ ] **Step 4: Implement session ownership and authority integration.** Intercept select/negotiate/close/renew with trusted context; create `MutationGuard` for select/quality/renew and invalidate in dispatcher `finally`. Preserve the shared InstanceManager mutation lock. State transitions do not await network I/O. Record `on_attempt` before POST, keep canceled negotiation tasks supervised, exact attempts in retryable cleanup, and session→request/viewer correlations after dispatcher task completion. Fence a successor until previous cleanup is confirmed or the retained `endpoint.owner.is_running()` is false; compare owners by identity before using any fresh capability, regardless of port reuse. Use fresh endpoint capabilities for DELETE and compare native generation before/after negotiation. Reject stale answers before adoption, then clean them.
- [ ] **Step 5: Wire host lifecycle outside the connected socket task.** Correlate media authority replies separately from pairing acknowledgements; process `media_cancel` before general routed-command parsing. Cancel a matching active command and its retained coordinator session. Run the one-second authorization/cleanup sweep throughout reconnect grace. On application shutdown close peers, drain supervised cleanup, close owned HTTP clients, and stop outbound work. `build_remote_client` constructs dispatcher without sessions, then host client, then coordinator with that client as authority, then assigns `dispatcher.sessions`; use the manager's existing orchestrator, never a viewer engine URL. Map coordinator `RemotePairingError` to its actual code, not generic `unavailable`.
- [ ] **Step 6: Verify and commit.** Step 3 passes; preserve Task 5 blocked-thread/canceled-mutation regressions and local HTTP/LAN tests. Run the covering Python suite once after all host edits. Commit explicit paths with `feat(remote): coordinate authorized media ownership and renewal`; independent review must trace lost-answer and disconnected-viewer cleanup end to end.

## Task 4: Add the typed remote client and installation-specific storage

**Files:** Create `packages/core/src/remote/{protocol,client,pairing}.ts` and matching tests, `api/target.ts`, `target.test.ts`; modify `api/{client,pairing,ServerContext}.ts*`, their tests, `src/index.ts`. Add only the native UUID adapter dependency in `apps/mobile/package.json` and lockfile if needed; supply browser/native request factories from application providers.

**Interfaces:** Export `ServerTarget = {kind:"local";base:string}|{kind:"remote";serviceUrl:string;installationId:string}`, `RequestOptions={signal?:AbortSignal;deadline?:number}`, `PreviewSource={uri:string;headers?:{Authorization:string}}`, `RemoteSelection` with exactly the public fields above plus `kind:"remote"`. Keep `SelectResp` as local shape; return `LocalSelection=SelectResp & {kind:"local"}` from local select. Export `Selection=LocalSelection|RemoteSelection`, `ApiClient=LocalApiClient|RemoteApiClient`, and `RemoteClientOptions={requestId:()=>string;trustedOrigin:string;WebSocketImpl?:any;now?:()=>number;allowInsecureLocalhost?:boolean}`. `connectRemoteClient(target,token,onUnauthorized:(()=>void)|undefined,options:RemoteClientOptions)->RemoteApiClient` exposes async `instances():Promise<Instance[]>`, `ping():Promise<number>`, `select(serial:string,opts?:RequestOptions):Promise<RemoteSelection>`, `preview(serial:string,opts?:RequestOptions):Promise<PreviewSource>`, `keyframe(serial:string):Promise<void>`, `setQuality(serial:string,tier:string):Promise<void>`, `negotiate(selection:RemoteSelection,offer:string,opts:RequestOptions):Promise<{answer:string;session_id:string;generation:number}>`, `closeSession(selection:RemoteSelection):Promise<void>`, `renew(selection:RemoteSelection,opts?:RequestOptions):Promise<RemoteSelection>`, plus `dispose():void`. Local client adds the common request options/dispose while retaining its synchronous preview helper.

**Public types for Step 4:** In `packages/core/src/remote/protocol.ts`, keep the selection and answer shapes identical to Python; reuse core `IceServer` and define bounded validators separately from TypeScript types.

```ts
import type { IceServer } from "../api/client";

export type RemoteSelection = {
  kind: "remote"; ok: true; id: string; serial: string; name: string;
  w: number; h: number; tier: string;
  session_id: string; generation: number;
  ice_servers: IceServer[]; expires_at: number; renew_after: 3300;
  relay_available: boolean;
};
export type RemoteAnswer = { answer: string; session_id: string; generation: number };
export type RequestOptions = { signal?: AbortSignal; deadline?: number };
```

**Production code for the storage algorithm:** Export from `packages/core/src/api/target.ts`. Hex-encode every origin code unit, including underscores; this works without a browser-only encoder or native base64 dependency. Normalize/trust-check the service separately before storing or opening a socket. Installation IDs are the broker's lowercase 32-hex identities, distinct from session UUIDs.

```ts
export function remoteDeviceTokenKey(serviceUrl: string, installationId: string): string {
  if (!/^[0-9a-f]{32}$/.test(installationId)) throw new Error("invalid installation");
  const origin = new URL(serviceUrl).origin;
  let encodedOrigin = "";
  for (let i = 0; i < origin.length; i++) {
    encodedOrigin += origin.charCodeAt(i).toString(16).padStart(4, "0");
  }
  return `wc_remote_device_token.${encodedOrigin}.${installationId}`;
}
```

**Test code for Step 2:** In `api/target.test.ts`, import the function above. In `remote/pairing.test.ts`, import `parseRemoteInvite` from `./pairing` and add the second test. `parseRemoteInvite` is synchronous and throws on invalid input.

```ts
import { remoteDeviceTokenKey } from "./target";

test("remote token keys are origin-specific, collision-free and native-storage safe", () => {
  const id = "a".repeat(32);
  const key = remoteDeviceTokenKey("https://relay.example", id);
  expect(key).toMatch(/^[A-Za-z0-9._-]+$/);
  expect(remoteDeviceTokenKey("https://relay.example:443/pair", id)).toBe(key);
  expect(remoteDeviceTokenKey("https://relay.example:8443", id)).not.toBe(key);
  expect(remoteDeviceTokenKey("https://relay.example_3a8443", id)).not.toBe(
    remoteDeviceTokenKey("https://relay.example:8443", id)
  );
  expect(remoteDeviceTokenKey("https://relay.example", "b".repeat(32))).not.toBe(key);
});
```

```ts
test("rejects an invitation outside the configured trust boundary", () => {
  const origin = "https://relay.example";
  expect(parseRemoteInvite(`${origin}/pair#invite=abc`, origin)).toEqual({
    serviceUrl: origin, handle: "abc",
  });
  for (const url of [
    "http://relay.example/pair#invite=abc",
    "https://evil.example/pair#invite=abc",
    "https://user@relay.example/pair#invite=abc",
    `${origin}/other#invite=abc`,
    `${origin}/pair#invite=abc&invite=def`,
  ]) expect(() => parseRemoteInvite(url, origin)).toThrow();
});
```

Step 3's expected RED is missing modules/exports or failed assertions. GREEN also needs first-frame auth, pending limit 32, no replay, and A→B stale unauthorized/storage tests listed above; type declarations alone do not prove the wire validator or provider lifecycle.

- [ ] **Step 1: Write browser/epoch tests.** Assert first sent frame is `viewer_auth`, URL contains no token, epoch change rejects all old promises, pending #33 rejects, malformed/oversized replies reject, and no canceled operation replays. Cancel unknown-session select resets its viewer connection, causing broker viewer-scoped cleanup; cancel known-session negotiate closes that exact session. `offline` retains the stored token; `not_paired` clears only the captured target's token.
- [ ] **Step 2: Write storage/trust tests.** `remoteDeviceTokenKey(serviceUrl,installationId)` matches `/^[A-Za-z0-9._-]+$/` and differs for distinct installation IDs/origins. `parseRemoteInvite(url, trustedOrigin)` rejects non-HTTPS, credentials, wrong origin/path and missing/duplicate fragment handle before any code/token is sent. Legacy `wc_base`, local token keys and stream preferences survive remote pairing and switching. A late unauthorized callback for A cannot delete B's token.
- [ ] **Step 3: Run RED.** `rtk npm run test:core -- --runInBand`; new modules/contracts fail, existing local tests stay relevant.
- [ ] **Step 4: Implement the matching wire and client.** `protocol.ts` mirrors Python payload/result bounds and error codes; `pairRemote(serviceUrl:string,handle:string,code:string,deviceName:string,options:RemoteClientOptions)->Promise<{installationId:string;token:string}>` opens a first-frame pairing socket and disposes it after reply. Use injectable UUID factory, browser `crypto.randomUUID`, and a mobile `expo-crypto` adapter installed with the project's Expo SDK tooling. Require the trusted origin before opening WSS; localhost HTTP only behind an explicit test option. New socket means fresh auth and fresh IDs; never buffer/replay mutations across reconnect. Add local async `preview` wrapping existing synchronous `previewSource`; retain that helper for compatibility.
- [ ] **Step 5: Integrate target storage in `ServerContext.tsx`.** Add `target`, `setTarget(target,token)->Promise<ApiClient>`, and remote-client disposal; keep `setServer(base,token)` as the local compatibility method. Add optional provider prop `remoteOptions:RemoteClientOptions`; no configured origin means local-only operation. Persist remote selection under `wc_remote_target` and tokens under `wc_remote_device_token.<encodedOrigin>.<installationId>`; encode the complete normalized URL origin as fixed-width hexadecimal UTF-16 code units, as shown below, to prevent escape/literal collisions. Selecting local deletes only the active remote-target marker, never the saved remote token. Share `wc_stream_preferences`. All async loads/probes and unauthorized handlers capture a target generation; stale callbacks are discarded. Remote reachability uses authenticated broker operations, not `/pair/status` at a private address.
- [ ] **Step 6: Verify and commit.** Core suites PASS for local/remote, bounds, target-switch races, and storage failure. Commit explicit paths with `feat(core): pair and address remote installations`; review the Python/TypeScript contract together.

## Task 5: Share peer lifecycle and implement remote setup/replacement

**Files:** Create `packages/core/src/webrtc/peer.ts`, `peer.test.ts`, `remote.ts`, `remote.test.ts`; modify `whep.ts`, `session.ts`, their tests, `packages/ui/src/screens/Stream.tsx`, `Stream.test.tsx`, core exports.

**Interfaces:** Export `PeerNegotiator={exchange(offer:string,deadline:number,signal:AbortSignal):Promise<{answer:string;resourceId:string}>;close(resourceId:string):Promise<void>;cancel():Promise<void>}`; `PeerSession={pc:any;input:InputSender;stream?:any;close:()=>Promise<void>}`; `connectPeer(opts:{iceServers:IceServer[];negotiator:PeerNegotiator;deadline:number;signal?:AbortSignal;RTCImpl:any;onStream?;onState?;onInputRtt?})->Promise<PeerSession>`. `connectRemote({selection:RemoteSelection,client:RemoteApiClient,...peerOptions})->Promise<PeerSession>` wraps fixed broker negotiate/close. Extend `EngineSession.kind` to `"local"|"remote"`; `ConnectEngineSessionOpts` accepts `Selection`, remote client, absolute deadline and signal. Preserve the existing `connectWhep` API as a wrapper.

**Transport code for Step 4:** Implement `connectRemote` in `webrtc/remote.ts` as the following adapter body around Task 5's shared `connectPeer`. `peerOptions` contains `deadline`, `signal`, `RTCImpl`, and callbacks; resource IDs are the already-known remote session, never an engine URL. A known selection has exact cleanup even if exchange never yields an answer.

```ts
const negotiator: PeerNegotiator = {
  async exchange(offer, deadline, signal) {
    const reply = await client.negotiate(selection, offer, { deadline, signal });
    if (reply.session_id !== selection.session_id || reply.generation !== selection.generation) {
      throw new Error("stale_generation");
    }
    return { answer: reply.answer, resourceId: selection.session_id };
  },
  async close(resourceId) {
    if (resourceId !== selection.session_id) throw new Error("stale_generation");
    await client.closeSession(selection);
  },
  async cancel() { await client.closeSession(selection); },
};
return connectPeer({ ...peerOptions, iceServers: selection.ice_servers, negotiator });
```

**Test code for Step 1:** Add to `webrtc/peer.test.ts`. Reject an expired absolute deadline before creating any peer or exchanging; error text remains sanitized. Add the existing `fakePc` and `fireReady` bodies from `whep.test.ts` to this test file for subsequent full lifecycle cases; keep the WHEP tests intact.

```ts
import { connectPeer, PeerNegotiator } from "./peer";

test("an exhausted setup deadline cannot start another negotiation budget", async () => {
  const negotiator: PeerNegotiator = {
    exchange: jest.fn(), close: jest.fn(), cancel: jest.fn(),
  };
  const RTCImpl = jest.fn();
  await expect(connectPeer({
    iceServers: [], negotiator, RTCImpl, deadline: performance.now() - 1,
  })).rejects.toThrow("timeout");
  expect(RTCImpl).not.toHaveBeenCalled();
  expect(negotiator.exchange).not.toHaveBeenCalled();
});
```

**Stream setup excerpt for Step 5:** `deadline` uses `performance.now()` in milliseconds throughout core/Stream; Python host translates received remaining milliseconds into its own monotonic seconds. It never receives the browser timestamp. The same AbortController covers select and exchange. Immediately record a returned selection in the generation's cleanup scope so abort between select and `connectRemote` also closes it.

Task 4's default `RemoteClientOptions.now` must also be `performance.now`; tests inject the same clock basis. Compute negotiate `timeout_ms` as `min(30000, floor(deadline-now()))` and reject values <=0 before sending. A native platform without performance support supplies one consistent monotonic clock adapter at the provider, never a wall-clock timestamp mixed with this deadline.

```ts
const controller = new AbortController();
const deadline = performance.now() + 30_000; // Create BEFORE select.
const selected = await client.select(serial, { signal: controller.signal, deadline });
// Store selected in this start generation's cleanup scope before another await.
const peer = await connectRemote({
  client, selection: selected, deadline, signal: controller.signal, RTCImpl,
  onStream, onState, onInputRtt,
});
```

Pin the 12s select + 15s gathering case using fake timers: forwarded `timeout_ms <= 3000`, total elapsed <=30s. Abort while exchange is blocked, then deliver its late answer: exact resource close once, no connected callback, and no successor teardown. Reuse WHEP fixture assertions for bearer/Location behavior. These are required alongside the complete expired-deadline test, not replaced by it. Expected GREEN: core/UI suites pass all local compatibility and remote orphan/replacement cases.

- [ ] **Step 1: Pin shared lifecycle assertions.** Both paths create one receive-video transceiver and the current input channel, use credentialed ICE with policy all, and report connected once after adoption. Delay select 12 s and gathering 15 s: answer/adoption has at most 3 s remaining, total timeout is 30 s. Cancel at each async boundary closes local peer/input and invokes negotiator cancellation; late answers close their resources. Close twice performs one teardown. TURN unavailable plus a reachable direct pair still connects.
- [ ] **Step 2: Pin compatibility and replacement tests.** Local WHEP still sends the capability and follows its existing Location resolution/DELETE contract. Unknown-session canceled selection triggers viewer disconnect cleanup; known-session cancellation issues matching close. Renew at 3300 closes old peer/input/sampler before new session adoption. Aborted predecessor cannot close its successor or emit stale stream callbacks.
- [ ] **Step 3: Run RED.** `rtk npm run test:core -- --runInBand` and `rtk npm run test:ui -- --runInBand`; remote/shared-lifecycle assertions fail before implementation.
- [ ] **Step 4: Extract and connect the shared peer.** Move existing media/input wiring and gathered-offer cleanup into `peer.ts`; inject transport only. Every wait consumes the same deadline and observes cancellation, including ICE connected/adoption. WHEP negotiator owns any late Location resource; remote negotiator owns the selection's session even when no answer arrives. Remote failure reports `relay_unavailable` only when relay service is unavailable and direct setup did not succeed; otherwise preserve the specific protocol/setup error.
- [ ] **Step 5: Wire `Stream` replacement using an AbortController per start generation.** Start deadline before select; abort previous work on target/serial switch, unmount, reconnect, or renewal. Retire remote old peer before replacement per PC single-owner contract. Schedule renewal from the selection's issuance/expiry metadata, reauthorize and negotiate fresh credentials. A remote quality command that changes native generation must perform a controlled fresh selection/peer exchange; preserve within-session adaptive downgrade state for this quality transition, while explicit reconnect/network-route replacement resets sampling and retries the chosen starting tier. Clean up timers, drag state, input health, watchdog and sampler once.
- [ ] **Step 6: Verify and commit.** Core/UI suites PASS, including existing rapid-switch and navigation-render regressions. Commit explicit paths with `feat(stream): share direct and relayed peer lifecycle`; independent review checks deadline, orphan cleanup and renewal races.

## Task 6: Wire invitation pairing and bounded previews in both apps

**Files:** Modify `packages/ui/src/screens/{Pair,InstanceList,Stream}.tsx` and tests, `components/{SwitchDrawer,InstanceRow}.tsx` and tests; create `hooks/useInstancePreview.ts`, `useInstancePreview.test.tsx`; modify `apps/web/src/app/{pair/page,providers}.tsx` and pair tests; create `apps/mobile/src/navigation/invitations.ts`, `invitations.test.ts`, update `Root.tsx`, `App.tsx`, `app.json`, and mobile tests. Update `.github/workflows/frontend-packages.yml`.

**Interfaces:** `parseRemoteInvite` returns `{serviceUrl:string;handle:string}`; Pair accepts `route.params.invitation` and calls Task 4 pairing/target methods. `useInstancePreview(client:ApiClient|null,serial:string)->PreviewSource|null` uses `client.preview` and a shared maximum of two requests across mounted preview rows. `SwitchDrawer` receives the client; each row calls the hook. Configured service origin comes only from `NEXT_PUBLIC_REMOTE_SERVICE_URL` / `EXPO_PUBLIC_REMOTE_SERVICE_URL`; never infer a trusted public service from an arbitrary invitation.

**Test code for Step 2:** In `packages/ui/src/hooks/useInstancePreview.test.tsx`, this uses the existing React Native testing library. The hook captures both client and serial and ignores an old Promise even if a transport cannot honor abort. The production queue is shared across hook instances and releases its slot on cancellation.

```tsx
import { act, renderHook, waitFor } from "@testing-library/react-native";
import type { ApiClient, PreviewSource } from "@wc/core";
import { useInstancePreview } from "./useInstancePreview";

test("a late preview from the previous client is never rendered", async () => {
  let resolveOld!: (source: PreviewSource) => void;
  const old = { preview: jest.fn(() => new Promise<PreviewSource>(resolve => {
    resolveOld = resolve;
  })) } as unknown as ApiClient;
  const current = { preview: jest.fn(() => new Promise<PreviewSource>(() => {})) } as unknown as ApiClient;
  const view = renderHook(({ client }) => useInstancePreview(client, "a"), {
    initialProps: { client: old },
  });
  await waitFor(() => expect(old.preview).toHaveBeenCalledTimes(1));
  view.rerender({ client: current });
  await act(async () => { resolveOld({ uri: "data:image/jpeg;base64,OLD" }); });
  expect(view.result.current).toBeNull();
  view.unmount();
});

test("three mounted rows start only two previews", async () => {
  const client = { preview: jest.fn(() => new Promise<PreviewSource>(() => {})) } as unknown as ApiClient;
  const view = renderHook(() => {
    useInstancePreview(client, "a");
    useInstancePreview(client, "b");
    useInstancePreview(client, "c");
  });
  await waitFor(() => expect(client.preview).toHaveBeenCalledTimes(2));
  view.unmount();
});
```

**Production wiring for Step 4:** In each `InstanceRow`, replace a direct remote preview expression with the hook result; use the existing rendering component/placeholder rather than adding a new layout.

```tsx
const preview = useInstancePreview(client, serial);
// Existing Image branch:
{preview !== null && <Image source={preview} style={existingPreviewStyle} />}
```

`existingPreviewStyle` above means the row's current style expression, not a new exported identifier. In Pair and web/mobile providers, call `parseRemoteInvite(url, configuredOrigin)` before `pairRemote`, and read web fragments only client-side. Mobile `invitations.ts` must export `parseInvitation(url:string,trustedOrigin:string)` returning the same parsed invitation or null; it delegates to core parsing, catches invalid links, and leaves socket creation to Pair. Test `Linking.getInitialURL()` and the warm listener each route one valid invitation, including an initial URL repeated as a warm event. Existing root/provider tests must pin local navigation and token storage.

RED: Step 3 finds missing hook/mobile routing or failed race assertions. GREEN: Step 5's four workspace suites plus web export pass; core JPEG validation rejects invalid base64 and >393216 decoded bytes. No live iPhone claim follows from these tests.

- [ ] **Step 1: Write invitation tests.** Valid `/pair#invite=<handle>` prompts for the separately entered six-digit code and requires no private IP. Cold-start and warm mobile links navigate to Pair exactly once. Wrong-origin link never opens a socket; offline/expired/wrong-code feedback is distinct and contains no handle/token. Existing local address and default-port/IPv6 paths still pass.
- [ ] **Step 2: Write preview race assertions.** Mount three rows: active requests `<= 2`; resolve a preview after switching client A→B and assert A's URI is never rendered. Unmounted/aborted rows release queue capacity. Remote preview validates JPEG mime/base64 and raw size `<= 384*1024`; malformed data shows no image. Local helper remains compatible.
- [ ] **Step 3: Run RED.** Core/UI/web/mobile focused tests through their existing workspace scripts; expect missing invitation/preview behavior failures.
- [ ] **Step 4: Implement invitation and preview wiring.** Keep local entry as an advanced/local option and existing layout/rail fixes. Use Expo Linking initial URL plus warm events and explicit trusted-origin parsing; add the configured HTTPS domain to iOS associated-domain configuration only when provided, with its server association file handled in Task 10. Browser reads the fragment on the client, preserving static export. Queue preview loads with cancellation and captured client identity; never pass a Promise to Image.
- [ ] **Step 5: Verify and commit.** `rtk npm run test:core -- --runInBand`, `rtk npm run test:ui -- --runInBand`, `rtk npm test -w apps/web -- --runInBand`, `rtk npm test -w apps/mobile -- --runInBand`, `rtk npm run build -w apps/web` all PASS. CI runs mobile tests and recognizes both `feature/**` and `feat/**` branches. Commit explicit paths with `feat(ui): pair remote installations and load bounded previews`.

## Task 7: Report selected media routes and export truthful measurements

**Files:** Modify `packages/core/src/webrtc/telemetry.ts`, its tests, UI `components/{StatsOverlay,NetChip}.tsx`, their tests and Stream initialization; create `webrtc/measurement.ts`, `measurement.test.ts`; modify core exports and web/mobile stream diagnostics actions.

**Interfaces:** Extend `StreamTelemetry` with `route:"direct"|"relay"|"unknown"`, `addressFamily:"IPv4"|"IPv6"|"mixed"|"unknown"`, `relayProtocol:"udp"|"tcp"|"tls"|"unknown"`, `sourceWidth/Height`, `decodedWidth/Height`, `decodedFps`, `framesDecoded`, `freezeCount`, `totalFreezeSeconds`, `maxFreezeSeconds` (numeric or null); retain existing bitrate/loss/input metrics. Replace unconditional LAN label with selected route or unknown. `makeMeasurementRecorder(meta:{targetBitrateMbps:number;capacityMbps:number|null;expectedRoute:string},now?:()=>number)` returns `add(sample:StreamTelemetry,adaptiveDowngrades:number):void`, `exportRun():RemoteRunEvidence`; define/export `RemoteRunEvidence` in `measurement.ts` with `capacity_mbps`, `target_bitrate_mbps`, `expected_route`, `samples`.

**Test code for Step 1:** In `webrtc/telemetry.test.ts`, use the existing `makeTelemetrySampler` import. Extend `TelemetrySamplerOptions.transport` to `"local"|"remote"`; this is the signaling context, while the route is measured from selected candidates.

Change `StreamTelemetry.transport` from its hardcoded `"LAN"` type/value to `"direct"|"relay"|"unknown"`, equal to the measured route, and update UI labels/snapshots. Add `TelemetrySamplerOptions.sourceDimensions?:()=>{width:number;height:number}|null` for actual selected engine dimensions; absence yields null source dimensions. Keep decoded dimensions sourced independently from inbound video reports. Stream passes a captured current-selection accessor and resets it on replacement.

```ts
test("the transport-selected pair wins over another succeeded pair", async () => {
  const reports = new Map<string, any>([
    ["transport", { type: "transport", selectedCandidatePairId: "chosen" }],
    ["chosen", { type: "candidate-pair", state: "succeeded", currentRoundTripTime: 0.02,
      localCandidateId: "local", remoteCandidateId: "remote" }],
    ["unused", { type: "candidate-pair", state: "succeeded", currentRoundTripTime: 0.9,
      localCandidateId: "other", remoteCandidateId: "remote" }],
    ["local", { type: "local-candidate", candidateType: "host", address: "192.0.2.1" }],
    ["remote", { type: "remote-candidate", candidateType: "srflx", address: "192.0.2.2" }],
    ["other", { type: "local-candidate", candidateType: "relay", relayProtocol: "tls" }],
  ]);
  const sampler = makeTelemetrySampler({
    pc: { getStats: async () => reports }, transport: "remote", onSample: () => {},
  });
  expect(await sampler.sample()).toMatchObject({
    rttMs: 20, route: "direct", addressFamily: "IPv4", relayProtocol: "unknown",
    decodedFps: null, totalFreezeSeconds: null, maxFreezeSeconds: null,
  });
});
```

**Production excerpt for Step 4:** Candidate selection in `telemetry.ts` starts with the actual transport link. Never overwrite the selected RTT by iterating all succeeded pairs. If there is no linked pair, apply the explicitly selected/unique nominated fallback from Step 1; multiple plausible pairs produce unknown.

```ts
const transports = [...stats.values()].filter((s: any) => s.type === "transport");
const pairIds = [...new Set(transports.map((s: any) => s.selectedCandidatePairId)
  .filter((id: unknown): id is string => typeof id === "string"))];
const linkedPair = pairIds.length === 1 ? stats.get(pairIds[0]) : undefined;
const local = linkedPair ? stats.get(linkedPair.localCandidateId) : undefined;
const remote = linkedPair ? stats.get(linkedPair.remoteCandidateId) : undefined;
const route = !local || !remote ? "unknown"
  : local.candidateType === "relay" || remote.candidateType === "relay" ? "relay" : "direct";
```

Declare/export `RemoteSampleEvidence` in `measurement.ts` with `elapsed_s`, source/decoded dimensions, decoded FPS, target/actual bitrate, cumulative adaptive downgrades, total/max freeze seconds, route/family/relay protocol. Numeric measurements are `number|null`; `elapsed_s` and downgrade count are finite nonnegative numbers. `RemoteRunEvidence.samples` is `RemoteSampleEvidence[]`, not `any[]`. Build exports by selecting those fields, never spreading raw stats or selection objects. Tests assert counter reset yields null interval metrics and no candidate addresses/SDP/credentials appear in serialized export. Missing freeze evidence stays null even when frames arrive; FPS buckets do not prove maximum freeze duration.

RED: the selected-pair assertion exposes the current last-succeeded-pair behavior and missing fields. GREEN: core/UI tests include route ambiguity, counter reset, null export, and all selected-pair families/protocol evidence. The sampler remains diagnostic until Task 11 consumes complete evidence.

- [ ] **Step 1: Write selected-pair tests.** With two succeeded pairs, transport's `selectedCandidatePairId` controls RTT/route/family. Use an explicitly selected or uniquely nominated succeeded pair only when transport linkage is missing; ambiguous pairs yield unknown. Either selected endpoint of type relay means relay. Gathered relay candidates and WSS alone yield no relay claim. Missing relay protocol remains unknown; plain ICE candidate `protocol:"tcp"` cannot distinguish TURN/TCP from TURN/TLS, so require relay-protocol evidence rather than the configured URL.
- [ ] **Step 2: Write counter/export tests.** Reset byte/frame counters produces a new baseline and null interval metrics, never negative bitrate/FPS. Native missing dimensions/freezes remains null. Every export sample records `elapsed_s`, source/decoded dimensions, `decoded_fps`, `target_bitrate_mbps`, actual `bitrate_mbps`, cumulative `adaptive_downgrades`, `total_freeze_s`, `max_freeze_s`, `route`, `address_family`, `relay_protocol`; null stays null. Assert serialized exports contain no token, handle, code, credential, complete SDP or raw candidate address.
- [ ] **Step 3: Run RED.** `rtk npm run test:core -- --runInBand` and `rtk npm run test:ui -- --runInBand`; new telemetry assertions fail.
- [ ] **Step 4: Implement actual measurement boundaries.** Follow selected-pair IDs and candidate records; use inbound video dimensions and decoded-frame deltas. Source dimensions originate from engine selection/updates, not tier labels. Prefer actual native freeze stats or a frame-progress observation with sufficient timing resolution to detect >1-second freezes; one-second FPS buckets alone cannot prove the freeze criterion. If unavailable, export null and preserve inconclusive status. Reset sampler/recorder baselines on peer/route replacement. Offer a JSON export in both clients; browser download and native sharing contain only sanitized measurement fields.
- [ ] **Step 5: Verify and commit.** Core/UI and relevant web/mobile suites PASS for selected-route, unknown stats, privacy and reset tests. Commit explicit paths with `feat(diagnostics): report selected routes and export media evidence`.

## Task 8: Correct scrcpy tier dimensions without changing encoding targets

**Files:** Modify `src/config.py`, `tests/test_config.py`, `tests/test_scrcpy_server.py`; change `src/server/scrcpy_server.py` only if argument plumbing requires it.

**Interfaces:** Keep `QUALITY_TIERS` and `build_scrcpy_args(tier:str,scid:int)->list[str]`; tier denotes the intended short edge of 16:9 content and scrcpy `max_size` limits its long edge.

**Complete regression code for Step 1:** Add to the named existing test files with their current imports (or the imports shown here).

```python
# tests/test_config.py
from config import QUALITY_TIERS

def test_quality_tiers_limit_the_long_edge_and_keep_encoding_targets():
    assert QUALITY_TIERS == {
        "360": {"max_size": 640, "bit_rate": 800_000, "max_fps": 30},
        "480": {"max_size": 854, "bit_rate": 2_000_000, "max_fps": 30},
        "720": {"max_size": 1280, "bit_rate": 4_000_000, "max_fps": 30},
        "1080": {"max_size": 1920, "bit_rate": 8_000_000, "max_fps": 60},
        "1440": {"max_size": 2560, "bit_rate": 12_000_000, "max_fps": 60},
    }

# tests/test_scrcpy_server.py
from server.scrcpy_server import build_scrcpy_args

def test_1080_arguments_allow_full_hd_without_lowering_bitrate_or_fps():
    args = build_scrcpy_args("1080", 1234)
    assert "max_size=1920" in args
    assert "video_bit_rate=8000000" in args
    assert "max_fps=60" in args
```

**Exact production replacement for Step 3:** Replace only `QUALITY_TIERS` in `src/config.py` with the dictionary in the first assertion. Replace its short-edge/max-size comment to explain that scrcpy caps the long edge; do not rename tiers or alter their bitrate/FPS values. Preserve all other config keys. Expected RED: `1080 != 1920` (and other current short-edge values); expected GREEN: both Step 2 files pass with the current runtime tier tests. Actual source/decoded landscape dimensions still need Task 12 evidence.

- [ ] **Step 1: Add failing exact-value tests.** `assert {tier:cfg["max_size"] for tier,cfg in QUALITY_TIERS.items()} == {"360":640,"480":854,"720":1280,"1080":1920,"1440":2560}`. For 1080, assert `max_size=1920`, `video_bit_rate=8000000`, `max_fps=60` in arguments. Snapshot other current bitrates/FPS and assert unchanged; non-16:9 selection still reports actual engine dimensions.
- [ ] **Step 2: Run RED.** `rtk proxy uv run pytest tests/test_config.py tests/test_scrcpy_server.py -v`; long-edge assertions fail on existing configuration.
- [ ] **Step 3: Change only long-edge configuration and its explanatory comment.** Keep defaults, tier order, bitrate, FPS, codec and capture settings. Do not infer landscape full-HD from a tier name.
- [ ] **Step 4: Verify GREEN.** Step 2 command and relevant runtime tier tests PASS. This proves argument selection; live codec dimensions are Task 12.
- [ ] **Step 5: Commit.** Stage explicit paths with `fix(quality): encode full short-edge resolution targets`.

## Task 9: Make broker installation persistence safe under concurrency and corruption

**Files:** Modify `src/broker/identity_store.py`, `src/broker/app.py`, `tests/test_broker_identity.py`, `tests/test_broker.py`.

**Interfaces:** Preserve `InstallationStore.register()->InstallationIdentity`, `authenticate(id,credential)->bool`, `exists(id)->bool`, `revoke(id,credential)->bool`. Add `InstallationStoreError` for unreadable, invalid or unwritable persistence; broker maps it to sanitized service-unavailable responses and does not publish fresh identities after failure. Deployment is one broker worker/process with one store owner.

**Test code for Steps 1–2:** Add to `tests/test_broker_identity.py`. These are direct tests of acknowledged persistence, with no production-only helper mocks.

```python
import json
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
import pytest
from broker.identity_store import InstallationStore, InstallationStoreError

def test_parallel_registrations_preserve_every_acknowledged_identity(tmp_path):
    store = InstallationStore(tmp_path / "identities.json")
    barrier = Barrier(8)
    def register(_):
        barrier.wait(timeout=5)
        return store.register()
    with ThreadPoolExecutor(max_workers=8) as pool:
        identities = list(pool.map(register, range(8)))
    restarted = InstallationStore(store.storage_path)
    assert len(json.loads(store.storage_path.read_text())) == 8
    assert all(restarted.authenticate(i.installation_id, i.credential) for i in identities)


def test_corruption_is_not_silently_replaced_by_registration(tmp_path):
    path = tmp_path / "identities.json"
    corrupt = b'{"truncated":'
    path.write_bytes(corrupt)
    with pytest.raises(InstallationStoreError):
        InstallationStore(path).register()
    assert path.read_bytes() == corrupt


def test_replace_failure_preserves_previous_acknowledged_identity(tmp_path, monkeypatch):
    store = InstallationStore(tmp_path / "identities.json")
    existing = store.register()
    before = store.storage_path.read_bytes()
    def failed_replace(*args):
        raise OSError("injected replacement failure")
    monkeypatch.setattr("broker.identity_store.os.replace", failed_replace)
    with pytest.raises(InstallationStoreError):
        store.register()
    assert store.storage_path.read_bytes() == before
    assert store.authenticate(existing.installation_id, existing.credential)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["identities.json"]
```

For a deterministic lost-update test in addition to the barrier start, block the first thread's `_write_data` using events; start the second registration and assert it cannot enter `_read_data` while the first transaction holds the store lock. Release in `finally`, then assert both acknowledged identities survive. Do not put a rendezvous inside a correctly locked read/write method: that deadlocks the fixed implementation.

**Production excerpt for Step 4:** Add `self._lock = threading.RLock()` in `__init__`. `register`, `authenticate`, `exists`, and `revoke` each hold that same lock across their entire transaction. Existing revoke can call authenticate recursively because it is an RLock. Change `_read_data`'s exception policy:

```python
class InstallationStoreError(RuntimeError):
    pass

try:
    raw = self.storage_path.read_text()
except FileNotFoundError:
    return {}
except OSError:
    raise InstallationStoreError("installation storage unavailable") from None
try:
    data = json.loads(raw)
except (ValueError, TypeError):
    raise InstallationStoreError("installation storage invalid") from None
```

Validate the parsed object: only lowercase 32-hex installation IDs, each value exactly `{"digest": <64 lowercase hex>}`; malformed records raise the same sanitized error. Before `os.replace`, call `file.flush()` and `os.fsync(file.fileno())`; in a failure `finally`, unlink only this invocation's temporary file. Map store errors to HTTP 503 without issuing an identity. RED: corruption/replace/race assertions fail with current silent reset/unlocked writes; GREEN: the Step 3 suites pass every acknowledged-identity and failure injection case.

- [ ] **Step 1: Add failing concurrency tests.** Barrier-controlled simultaneous register/register and register/revoke preserve all acknowledged identities; authentication and credential-checked revoke are atomic under the same store lock. Failed `os.replace` never acknowledges registration and leaves previous data intact.
- [ ] **Step 2: Add corruption assertions.** Invalid JSON, wrong digest schema, permission failure and truncated file raise `InstallationStoreError`; file bytes remain unchanged; no new registration silently replaces old data. Missing file alone permits initial empty storage. Persisted document contains only installation IDs and digest records.
- [ ] **Step 3: Run RED.** `rtk proxy uv run pytest tests/test_broker_identity.py tests/test_broker.py -v`; current lost-update/corrupt-as-empty behavior fails.
- [ ] **Step 4: Implement the minimal durable boundary.** Guard the full read/check/modify/atomic-write with an `RLock`; validate schema on read, distinguish absence from error, remove temporary files on failure, flush/fsync before replacement. Keep constant-time digest checks. Do not introduce multi-worker/distributed coordination; explicitly enforce the single-worker deployment contract.
- [ ] **Step 5: Verify and commit.** Step 3 PASS with deterministic race/error injection. Commit explicit paths with `fix(broker): preserve installation identities across concurrent writes`.

## Task 10: Build the local broker/coturn stack and production deployment templates

**Files:** Create `infra/shared-remote/{compose.test.yml,compose.production.yml,broker.Dockerfile,edge.Dockerfile,edge.conf.template,turnserver.test.conf,turnserver.production.conf.template,README.md}`, `src/broker/main.py`, `scripts/shared_remote_test_env.py`, `tests/integration/test_shared_remote.py`, `tests/test_remote_deployment.py`; update CI and deployment documentation. Generated test secrets/certificates stay outside tracked paths.

**Interfaces:** `src/broker/main.py` exposes ASGI `app`, constructed from explicit `REMOTE_*` environment settings including origins, digest-store path, STUN/TURN URLs, secret-file path, active-stream cap. Test stack: broker HTTPS/WSS 8443, coturn UDP/TCP 3478, TLS 5349, relay range 49160–49200. Production signaling/web edge binds address A:443; TURN/TLS binds separate address B:443. `shared_remote_test_env.py --directory <temp>` writes one-run secret/cert/env files and prints no credentials.

**Configuration code for Steps 1 and 4:** This is the relevant production Compose/TURN template content; merge it into the named files with pinned tested image, mounts and required variables. Address A and B are separate host addresses, not two conflicting listeners on the same address. The preflight script rejects equal addresses before launch. Example TURN config values are template inputs, not checked-in secrets.

```yaml
# compose.production.yml, service excerpts
services:
  edge:
    ports:
      - "${REMOTE_HTTPS_BIND_ADDRESS:?required}:443:443/tcp"
  broker:
    command: ["uvicorn", "broker.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
    environment:
      REMOTE_MAX_ACTIVE_STREAMS: "${REMOTE_MAX_ACTIVE_STREAMS:?required}"
      REMOTE_STORE_PATH: /data/installations.json
      REMOTE_TURN_SECRET_FILE: /run/secrets/turn_secret
    volumes:
      - broker-data:/data
  turn:
    network_mode: host
    # Config binds REMOTE_TURN_BIND_ADDRESS separately from the HTTPS listener.
volumes:
  broker-data:
```

```ini
# turnserver.production.conf.template; expand only named operator inputs
listening-ip=${REMOTE_TURN_BIND_ADDRESS}
relay-ip=${REMOTE_TURN_RELAY_BIND_ADDRESS}
external-ip=${REMOTE_TURN_PUBLIC_ADDRESS}/${REMOTE_TURN_RELAY_BIND_ADDRESS}
listening-port=3478
tls-listening-port=443
realm=${REMOTE_TURN_DOMAIN}
use-auth-secret
static-auth-secret=${REMOTE_TURN_SECRET}
cert=/run/certs/turn-fullchain.pem
pkey=/run/certs/turn-key.pem
min-port=49160
max-port=49200
max-bps=${REMOTE_TURN_ALLOCATION_BPS}
bps-capacity=${REMOTE_TURN_AGGREGATE_BPS}
total-quota=${REMOTE_TURN_TOTAL_ALLOCATIONS}
no-multicast-peers
no-cli
```

Append verified `denied-peer-ip` ranges for private, loopback, link-local and infrastructure addresses in both families, and reject private hostnames resolving to them. The test template has explicit isolated echo exceptions and TLS port 5349, not the production exceptions. Render the secret from a mounted file into a container-private config; do not put it in logs, arguments, git, or printed generated assets. Add UDP/TCP relay firewall rules for the published range. This excerpt is not a complete deployable template by itself.

**Executable test cases for Step 2:** Create this exact parametrization in `tests/integration/test_shared_remote.py`; its `shared_remote_stack` fixture must be implemented in this file and invoke the real Docker stack and coturn clients, not simulated allocations. Required fixture methods: `approved_credentials()->dict` (real host/viewer select through broker); `transfer(credentials,transport,payload)->AllocationResult`; `allocate(credentials,transport)->bool`. Define the result type below in that test module. Generate one-run CA/certs/secrets using the environment script; verify TLS with that CA.

```python
from dataclasses import dataclass
import pytest

@dataclass(frozen=True)
class AllocationResult:
    allocation_ok: bool
    received: bytes

@pytest.mark.parametrize("transport", ["udp", "tcp", "tls"])
def test_approved_turn_allocation_transfers_payload(shared_remote_stack, transport):
    credentials = shared_remote_stack.approved_credentials()
    payload = b"shared-remote-allocation-check"
    result = shared_remote_stack.transfer(credentials, transport, payload)
    assert result.allocation_ok
    assert result.received == payload
    invalid = {**credentials, "credential": "wrong-credential"}
    assert not shared_remote_stack.allocate(invalid, transport)
```

Add fixture methods `restart_broker`, `connect_saved_viewer`, and `deny_permission` with real bounded operations for the restart and denied-destination tests. Expired-credential test uses an actually expired signed username, not sleeping 3600 seconds. Never count a skipped daemon-dependent test as GREEN. Report allocation and payload transfer separately; a port-open check is insufficient. Task 12 alone proves Windows/iPhone TURN/TLS:443 compatibility. Expected RED: missing runnable stack or failed real allocations; GREEN: Step 3 integration/config suites pass all three transports and isolation cases with a recorded image digest, followed by scoped teardown.

- [ ] **Step 1: Write configuration assertions.** Production requires addresses, DNS, certs, secret files, persistent storage, max streams and aggregate bandwidth; missing values prevent launch. Assert two distinct :443 bind addresses, no anonymous TURN/static endpoint secret, denied private/loopback/link-local/multicast/infrastructure destinations, and `max-bps >= 2000000` bytes/s for the validation profile. Reference [coturn's configuration documentation](https://github.com/coturn/coturn/blob/master/README.turnserver) when translating template options; verify against the selected image during integration.
- [ ] **Step 2: Write real allocation/integration tests.** Obtain credentials through PC-approved broker select; fresh credentials allocate and transfer test payload, wrong/expired credentials fail. Run UDP, TCP and TLS with trusted generated test CA; TLS hostname mismatch fails. Denied-destination permission requests fail, aggregate admission rejection leaves B alive, broker restart requires viewer reauth while persisted host identity survives, and TURN-down direct/STUN configuration remains available. Use coturn's bundled test clients with explicit generated credentials and bounded subprocess deadlines. Test-only allowed echo destinations must be an explicit isolated exception; test the production deny template separately.
- [ ] **Step 3: Run RED in an isolated temporary environment.** Generate assets via `rtk proxy uv run python scripts/shared_remote_test_env.py --directory /private/tmp/window-control-shared-remote-test`; start `rtk proxy docker compose --env-file /private/tmp/window-control-shared-remote-test/test.env -f infra/shared-remote/compose.test.yml up -d --build`. Run `rtk proxy uv run pytest tests/test_remote_deployment.py tests/integration/test_shared_remote.py -v`; missing stack/behavior fails.
- [ ] **Step 4: Implement the stack and templates.** Build broker with one worker and persisted digest volume; edge serves the web static export, `/pair`, and proxies fixed broker routes including WSS. Generate the web trusted-origin setting at build time. Serve iOS association metadata from operator-provided app/team/domain inputs; absent values leave universal-link deployment explicitly unconfigured. Pin the actually tested coturn image digest in templates. Configure secret-derived auth, allocation/aggregate limits and denied destinations; reserve separate HTTPS and TURN/TLS listener addresses. Bound issuance at 12/installation/hour, registration 5/IP/hour, pairing 10/IP/minute, commands 120/viewer/minute and previews 2/viewer/second. Bound/expire rate-limit keys as well as individual buckets.
- [ ] **Step 5: Produce the concrete operator proposal.** README lists required DNS/address/cert/domain-association values, capacity and an illustrative two-stream profile: 8 Mbps media per stream gives 16 Mbps relay egress before overhead, approximately 7.2 decimal GB/hour; provision ingress plus egress, both endpoint allocations, headroom and a measured stream cap. Use `monthly_cost = fixed_compute + fixed_addresses + billable_egress_GB * price_per_GB`; no provider quote or hidden production capacity default. Provider, region, DNS, iOS team ID and monthly budget remain explicit operator inputs before paid deployment. Document that cap enforcement bounds new admissions; existing abuse/allocation expiry still requires coturn quotas and operator monitoring.
- [ ] **Step 6: Verify and tear down.** Step 3 tests PASS, record image/version and sanitized logs; CI generates its own secret/CA and runs these integration tests on Linux. Always run matching `docker compose ... down --volumes --remove-orphans` for this test project only, then remove generated test assets. Commit explicit paths with `feat(infra): prepare tested shared signaling and TURN deployment`; do not deploy or purchase infrastructure.

## Task 11: Implement an honest remote-stream evidence assessor

**Files:** Create `scripts/validate_remote_stream.py`, `tests/test_remote_stream_validation.py`, `docs/testing/shared-remote-validation.md`.

**Interfaces:** Preserve the original plan's `evaluate_run(capacity_mbps:float,samples:list[dict],expected_route:str)->dict`, returning `{status:"pass"|"fail"|"inconclusive",reasons:list[str],metrics:dict}`. CLI reads Task 7 `RemoteRunEvidence` JSON, exits 0 for pass, 1 for measured failure, 2 for missing/invalid/inconclusive evidence. `expected_route` values: `direct`, `turn_udp`, `turn_tcp`, `turn_tls`; map these to selected route/relay protocol without inferring them from configured URLs.

**Complete synthetic fixture and boundary tests for Step 1:** Add to `tests/test_remote_stream_validation.py`; this fixture tests the assessor only. It does not represent a captured device run.

```python
import copy
import pytest
from scripts.validate_remote_stream import evaluate_run

def full_hd_fixture():
    return [{
        "elapsed_s": second,
        "source_width": 1920, "source_height": 1080,
        "decoded_width": 1920, "decoded_height": 1080,
        "decoded_fps": 30, "target_bitrate_mbps": 8, "bitrate_mbps": 4,
        "adaptive_downgrades": 0, "total_freeze_s": 0, "max_freeze_s": 0,
        "route": "relay", "address_family": "IPv4", "relay_protocol": "tls",
    } for second in range(601)]

def test_complete_ten_minute_evidence_passes_without_static_scene_bitrate_floor():
    assert evaluate_run(12, full_hd_fixture(), "turn_tls")["status"] == "pass"

@pytest.mark.parametrize("field,value", [
    ("source_width", 1080), ("decoded_height", 608), ("decoded_fps", 0),
    ("target_bitrate_mbps", 1.7), ("adaptive_downgrades", 1),
    ("max_freeze_s", 1.1), ("relay_protocol", "tcp"),
])
def test_measured_acceptance_violation_fails(field, value):
    samples = full_hd_fixture()
    for sample in samples[300:]:
        sample[field] = value
    assert evaluate_run(12, samples, "turn_tls")["status"] == "fail"

def test_exact_freeze_boundaries():
    samples = full_hd_fixture()
    samples[-1]["max_freeze_s"] = 1
    samples[-1]["total_freeze_s"] = 1
    assert evaluate_run(12, samples, "turn_tls")["status"] == "pass"
    samples[-1]["total_freeze_s"] = 6  # 1%, strict less-than criterion.
    assert evaluate_run(12, samples, "turn_tls")["status"] == "fail"

def test_missing_freeze_evidence_and_observation_gap_are_inconclusive():
    samples = full_hd_fixture()
    samples[300]["max_freeze_s"] = None
    assert evaluate_run(12, samples, "turn_tls")["status"] == "inconclusive"
    gapped = copy.deepcopy(full_hd_fixture())
    del gapped[300:304]
    assert evaluate_run(12, gapped, "turn_tls")["status"] == "inconclusive"
```

**Evaluation code for Step 3:** In `scripts/validate_remote_stream.py`, after schema validation (finite values, positive capacity, chronological timestamps, no counter resets), evaluate both per-sample minima and full-window cumulative criteria. For valid complete evidence, the core comparisons are:

```python
duration_s = samples[-1]["elapsed_s"] - samples[0]["elapsed_s"]
full_hd = all((s["source_width"], s["source_height"], s["decoded_width"], s["decoded_height"])
              == (1920, 1080, 1920, 1080) for s in samples)
fps_ok = all(s["decoded_fps"] >= 30 for s in samples)
target_ok = all(s["target_bitrate_mbps"] == 8 for s in samples)
no_downgrades = all(s["adaptive_downgrades"] == 0 for s in samples)
freeze_total_s = samples[-1]["total_freeze_s"] - samples[0]["total_freeze_s"]
freeze_ok = max(s["max_freeze_s"] for s in samples) <= 1 and freeze_total_s / duration_s < 0.01
# Final pass also requires capacity >=12, duration >=600, complete contiguous
# evidence, and actual selected route/protocol matching expected_route.
```

Fix the capture/export contract so the first sample starts after sampler baselines are established, with zero initial cumulative freezes and downgrades. Missing/null initial metrics cannot be silently replaced with fixture values. Reject nonzero initial counters or mark the capture window inconclusive; do not subtract away an earlier downgrade. Cumulative counters must never decrease. Test duplicate/out-of-order time, NaN, impossible negative counters, short run, capacity <12, and wrong route in addition to the code above. The CLI accepts `python scripts/validate_remote_stream.py <evidence.json>` and reads `capacity_mbps`, `expected_route`, `target_bitrate_mbps`, and `samples`; conflicting root/sample targets are invalid. Expected RED: missing assessor or wrong threshold classifications; GREEN: Step 2 plus subprocess exit tests show 0/1/2 for pass/fail/inconclusive.

- [ ] **Step 1: Write failing assessor tests.** A 600-second complete fixture with capacity 12, source/decoded 1920×1080, target 8 Mbps, decoded FPS >=30, zero downgrades, max freeze <=1 s and freeze fraction <0.01 passes. Mutate it to 1080×608, zero FPS, 1.7 Mbps target/downgrade, 1.1-second max freeze, 6/600 freeze fraction, wrong selected route, capacity <12 or duration <600: assert fail. Equality at 1 s passes; equality at 1% fails. Missing required measurements returns inconclusive; duplicate/out-of-order timestamps, NaN and reset cumulative counters cannot pass.
- [ ] **Step 2: Run RED.** `rtk proxy uv run pytest tests/test_remote_stream_validation.py -v`; assessor is missing.
- [ ] **Step 3: Implement schema and evaluation.** Require contiguous full-duration evidence with explicit target bitrate, source and decoded dimensions, decoded FPS, downgrade count, selected route, and freeze maxima/totals; gaps over 2 sample intervals are inconclusive. Actual bitrate is diagnostic rather than an invented constant-motion lower threshold; an 8 Mbps encoder target does not require every static frame interval to consume 8 Mbps. Capacity must be independently measured, not derived from WebRTC receive bitrate. Family/protocol cases require their actual selected evidence. Synthetic fixtures are labeled assessor tests, never device acceptance.
- [ ] **Step 4: Verify GREEN and CLI semantics.** Step 2 PASS; CLI tests verify all three exit codes and sanitized reasons. Runbook defines exported fields, independent capacity-test evidence, fixture/device separation, and the required matrix from Task 12.
- [ ] **Step 5: Commit.** Stage explicit paths with `test(remote): assess sustained full-HD stream evidence`.

## Task 12: Validate the final packaged branch on Windows and iPhone

**Files:** Update `docs/testing/shared-remote-validation.md`, `docs/testing/remote-engine-compatibility.md`, `apps/mobile/docs/device-smoke-test.md`; add sanitized result artifacts under `docs/testing/results/shared-remote/`. Modify installer/build files only for a measured packaging defect. Media fixes, if discovered, get a separate focused TDD task and commit.

**Interfaces:** Consume Task 11 evidence files and all earlier tested interfaces; produce a matrix with exact branch commit, Windows installer/engine artifact ID, mobile build ID, library/backend versions, route/family/protocol, capacity measurement, assessor result, and evidence paths. Every row has `pass`, `fail`, or `untested` with a concrete reason.

**Verification commands for Step 1:** Run from the feature worktree; each command has its own recorded exit status. These are future release checks, not claimed results of writing this plan.

```bash
rtk proxy env UV_CACHE_DIR=/private/tmp/remote-peer-uv-cache uv run pytest tests/ apps/desktop/ -v
rtk npm run test:core -- --runInBand
rtk npm run test:ui -- --runInBand
rtk npm test -w apps/web -- --runInBand
rtk npm test -w apps/mobile -- --runInBand
rtk npm run build -w apps/web
rtk proxy git rev-parse HEAD
rtk proxy gh workflow run build.yml --ref feat/direct-webrtc-shared-turn
```

Expected: all source suites/export pass; Windows workflow's `headSha` equals the recorded final commit, engine tests and installer jobs succeed. Run native tests from the freshly staged Windows artifact, then launch its host without a development vcpkg PATH. Build/installer success does not replace the standalone launch, DPAPI or iPhone tests.

**Concrete result artifact for Steps 2–4:** Create `docs/testing/results/shared-remote/<tested-sha>-matrix.json` with real IDs/evidence when available. This initial example intentionally reports untested, not pass. Repeat the row for direct and every TURN transport/family case.

```json
{
  "branch": "feat/direct-webrtc-shared-turn",
  "tested_sha": null,
  "windows_workflow_run": null,
  "installer_artifact": null,
  "mobile_build_id": null,
  "cases": [{
    "name": "iphone-full-hd-turn-tls-443-ipv4",
    "status": "untested",
    "reason": "Packaged Windows host, iPhone build and controlled network path required",
    "capacity_evidence": null,
    "media_evidence": null,
    "assessor_status": null
  }]
}
```

**Evidence command:** `rtk proxy uv run python scripts/validate_remote_stream.py <captured-evidence.json>` must exit 0 for each required sustained-media row. A fixture file cannot satisfy it. Record separate elapsed measurements for revocation <=5s, original outage grace 60s, renew at3300s and expiration3600s; include actual selected route/address family/relay protocol. If a device/network setup is unavailable, preserve the untested row and explain exactly which setup is missing. Commit measured results/runbooks only, not placeholder pass claims.

- [ ] **Step 1: Verify source and publish the final pull/build branch.** Run `rtk proxy uv run pytest tests/ apps/desktop/ -v`, core/UI/web/mobile workspace suites, and web export build. Require PASS; integration cases use the real stack from Task 10. Stage only reviewed changes; push `feat/direct-webrtc-shared-turn`, manually dispatch the existing Windows installer workflow, record tested SHA. Download/stage the new artifacts and run all native GoogleTests; a previous successful run does not validate later native changes.
- [ ] **Step 2: Verify actual packaged launch and owner identity.** On Windows, launch from a clean directory/machine without vcpkg's PATH to prove the complete runtime DLL closure. Install the packaged host, exercise current-user DPAPI identity save/load and restart, confirm LAN remains usable when remote service is absent. Record failures as packaging/identity defects with a regression before continuing.
- [ ] **Step 3: Run connectivity and isolation cases on the actual iPhone.** Disable Tailscale on both endpoints, make no router changes, pair through owner QR/link+code, reconnect with saved token. Test selected direct, TURN/UDP, endpoint-UDP-blocked TURN/TCP and TURN/TLS:443, IPv4, IPv6 and mixed-family relay reachability. Run two independent PC installations concurrently; wrong/revoked token, cross-installation session, pairing replay, stale revision and arbitrary forwarding all reject. For direct, verify TURN has no continuous media payload rather than rejecting mere allocation checks.
- [ ] **Step 4: Run sustained quality and expiry cases.** Independently measure >=12 Mbps capacity, then export a ten-minute landscape full-HD run for direct and relay transport profiles; assessor must pass actual 1920×1080, >=30 decoded FPS, 8 Mbps target, zero downgrades and freeze limits. Run production-length 3600-second credentials and controlled renewal at 3300; old credentials cannot create a new allocation. Measure revocation <=5 s, service grace 60 s, broker restart, PC offline, TURN unavailable, negotiation cancellation, network switch and LAN during service outage.
- [ ] **Step 5: Investigate only observed native/media failures.** If earlier IPv6 failure recurs, capture RTP/SRTP packet sizes, fragmentation/DF and path MTU on the actual build; implement the smallest measured fix, add its regression, rebuild and repeat affected cases. Never remove IPv6 to make the matrix pass. A missing iPhone/Windows device, usable test service or controlled network path leaves its row untested; keep advancing available source/integration validation but do not label the feature release-ready.
- [ ] **Step 6: Review, record and commit.** Conduct the final independent whole-branch review, fix blocking findings, repeat only affected checks. Commit sanitized runbooks/results with `test(remote): record packaged streaming acceptance results`. Report the branch/build instructions and actual passed/untested criteria. No merge, release tag, production deployment or paid resource creation is included.

## Self-review and execution handoff

Coverage: spec registration/pairing/storage → completed Tasks 2/4/5 plus continuation 4/6/9; PC authorization/isolation → 2/3; native session/ICE/input → 1/3/5; direct/relay and timeout → 2/5/10/12; quality/recovery/diagnostics → 5/7/8/11/12; quotas/expiry/deployment → 2/3/9/10/12; actual Windows/iPhone acceptance → 12. Local gates, WHEP, capture and UI regressions remain explicit verification inputs.

October 4 self-review checks: code examples cover every task; Python and TypeScript/TSX blocks were syntax-checked, JSON and relative links checked, interfaces reconciled with source and spec, all new helper contracts and fixture migrations named, and RED/GREEN expectations distinguished from actual evidence. These checks do not execute future tests or type-check their unimplemented imports. Each delivery unit has named behavioral assertions, a RED/GREEN command and a commit/reviewer boundary; cross-task public/private generation and expiry names agree; all five Review Focus conditions have owning tests. No unreviewed partial implementation or synthetic fixture is treated as acceptance evidence. Provider/domain/device inputs constrain deployment or live validation, not the ability to finish the code tasks.

**Review gate:** This detailed continuation plan is saved before execution as requested. The chosen subagent method is preserved. Wait for the user's plan review before restarting implementation; then give each worker its task, the contracts/global constraints, actual starting diff and prior task's reviewed interfaces.
