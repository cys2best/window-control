# Performance Overhaul Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Raise streaming quality to 1080/1440p with network-adaptive 480↔1440 tiers, cut video latency by dropping the ffmpeg re-encode, and make instance switching near-instant via a server-side mediamtx mux path.

**Architecture:** scrcpy emits H.264 on-device → ffmpeg muxes with `-c:v copy` (no re-encode) → mediamtx → WHEP. Quality is a per-session tier (`max_size`/`bit_rate`/`max_fps`) restartable at runtime, driven by client WebRTC getStats hysteresis. Instance switch uses one mediamtx `active` path whose source is repointed server-side, so the browser PeerConnection never renegotiates.

**Tech Stack:** Python 3.12, FastAPI/uvicorn, scrcpy-server 3.x, mediamtx, ffmpeg (imageio-ffmpeg), vanilla JS WebRTC (WHEP), pytest (`uv run pytest`).

**Spec:** docs/superpowers/specs/2026-08-13-performance-overhaul-design.md

## Global Constraints

- Python commands use `uv`: `uv run pytest`, `uv run python` — never `python -m`.
- Commits: do NOT add `Co-Authored-By` lines (project CLAUDE.md).
- Tests insert `src/` into `sys.path` via `sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))` and import from `server.*` / `config`.
- Prefer pure-function unit tests (no real adb/ffmpeg/mediamtx processes) — mirror existing `test_mediamtx_manager.py` / `test_scrcpy_session.py` style.
- Windows-only runtime paths (`C:\ProgramData\WindowControl`) must stay guarded by `sys.platform`; dev runs on darwin.
- scrcpy-server invoked as `com.genymobile.scrcpy.Server 3.1`; keep existing arg names.

---

## Task 0: Spike — can mediamtx repoint a path source at runtime?

**Goal:** Decide Section 3 mechanism (3a mux path vs 3b PC-reuse) before building it. Throwaway probe, no kept code.

**Files:**
- Scratch only (no repo changes committed unless spike promotes to a config approach).

- [ ] **Step 1: Read mediamtx path source options**

Check the bundled mediamtx version and its config surface:
```bash
src/assets/mediamtx/mediamtx.exe --version   # (on Windows target); on dev, check docs
```
Look specifically for: `runOnDemand`, `source` with `rtsp://` pointing at another local path, and whether `api: yes` exposes a runtime path add/edit endpoint (`/v3/config/paths/add`, `/v3/config/paths/patch`).

- [ ] **Step 2: Draft an `active` path fed by another path**

Two candidate mechanisms to evaluate:
- (A) Static: an `active` path whose `source: rtsp://localhost:8554/instanceN` — repoint by patching config + reload.
- (B) API: enable `api: yes`, then `PATCH /v3/config/paths/patch/active` with a new `source` at runtime, no full mediamtx restart.

- [ ] **Step 3: Test one repoint end-to-end**

Start mediamtx with two instance paths + one `active` path sourced from `instance0`. Connect a WHEP client to `active`. Repoint `active`→`instance1` (via chosen mechanism). Observe: does the WHEP client keep its PeerConnection and switch video within ~1s, without re-negotiation?

- [ ] **Step 4: Record the verdict**

Write findings into the spec's Section 3 as a short "Spike result" note (commit that doc edit):
- If (A) or (B) works → Section 3 = **3a mux path**, note exact mechanism + fields.
- If neither works cleanly → Section 3 = **3b fallback** (PC reuse + ICE restart); mark Tasks 8–10 as superseded by the 3b variant described in Task 11.

- [ ] **Step 5: Commit the verdict doc edit**

```bash
git add docs/superpowers/specs/2026-08-13-performance-overhaul-design.md
git commit -m "docs: record mediamtx repoint spike result for fast switch"
```

---

## Task 1: Quality tier table in config

**Files:**
- Modify: `src/config.py`
- Test: `tests/test_config.py`

**Interfaces:**
- Produces: `QUALITY_TIERS: dict[str, dict]` mapping tier name → `{"max_size": int, "bit_rate": str, "max_fps": int}`; `DEFAULT_TIER: str = "720"`; `TIER_ORDER: list[str] = ["480","720","1080","1440"]`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_config.py — add
def test_quality_tiers_shape():
    from config import QUALITY_TIERS, TIER_ORDER, DEFAULT_TIER
    assert TIER_ORDER == ["480", "720", "1080", "1440"]
    assert DEFAULT_TIER == "720"
    for t in TIER_ORDER:
        tier = QUALITY_TIERS[t]
        assert isinstance(tier["max_size"], int)
        assert tier["bit_rate"].endswith("M")
        assert tier["max_fps"] in (30, 60)

def test_quality_tiers_monotonic():
    from config import QUALITY_TIERS, TIER_ORDER
    sizes = [QUALITY_TIERS[t]["max_size"] for t in TIER_ORDER]
    assert sizes == sorted(sizes)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_config.py::test_quality_tiers_shape -v`
Expected: FAIL with `ImportError: cannot import name 'QUALITY_TIERS'`.

- [ ] **Step 3: Add the tier table**

```python
# src/config.py — add near QUALITY_MAP
TIER_ORDER = ["480", "720", "1080", "1440"]
DEFAULT_TIER = "720"
QUALITY_TIERS = {
    "480":  {"max_size": 480,  "bit_rate": "2M",  "max_fps": 30},
    "720":  {"max_size": 720,  "bit_rate": "4M",  "max_fps": 30},
    "1080": {"max_size": 1080, "bit_rate": "8M",  "max_fps": 60},
    "1440": {"max_size": 1440, "bit_rate": "12M", "max_fps": 60},
}
assert DEFAULT_TIER in QUALITY_TIERS
assert set(TIER_ORDER) == set(QUALITY_TIERS)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_config.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/config.py tests/test_config.py
git commit -m "feat: add quality tier table (480/720/1080/1440) to config"
```

---

## Task 2: ScrcpySession accepts a tier and builds scrcpy args from it

**Files:**
- Modify: `src/server/scrcpy_session.py` (`_start_server` signature + arg string; `ScrcpySession.__init__`)
- Test: `tests/test_scrcpy_session.py`

**Interfaces:**
- Consumes: `QUALITY_TIERS`, `DEFAULT_TIER` from Task 1.
- Produces:
  - `ScrcpySession.__init__(..., tier: str = DEFAULT_TIER)` storing `self.tier`.
  - `build_scrcpy_args(tier: str, scid: int) -> list[str]` — module-level pure function returning the `app_process` arg tokens (the part after `com.genymobile.scrcpy.Server 3.1`), including `max_size=`, `bit_rate=`, `max_fps=`, `video_encoder_options=i-frame-interval=1`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_scrcpy_session.py — add
def test_build_scrcpy_args_uses_tier():
    from server.scrcpy_session import build_scrcpy_args
    from config import QUALITY_TIERS
    args = build_scrcpy_args("1080", scid=0x1a)
    joined = " ".join(args)
    assert f"max_size={QUALITY_TIERS['1080']['max_size']}" in joined
    assert f"bit_rate={QUALITY_TIERS['1080']['bit_rate']}" in joined
    assert f"max_fps={QUALITY_TIERS['1080']['max_fps']}" in joined
    assert "i-frame-interval=1" in joined
    assert "scid=1a" in joined

def test_session_defaults_to_default_tier():
    from server.scrcpy_session import ScrcpySession
    from config import DEFAULT_TIER
    s = ScrcpySession("emulator-5554", 0, "rtsp://localhost:8554/instance0", 720, 1280)
    assert s.tier == DEFAULT_TIER
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_scrcpy_session.py::test_build_scrcpy_args_uses_tier -v`
Expected: FAIL with `ImportError: cannot import name 'build_scrcpy_args'`.

- [ ] **Step 3: Implement `build_scrcpy_args` + thread `tier` through**

```python
# src/server/scrcpy_session.py — add module-level fn
from config import ASSETS_DIR, QUALITY_TIERS, DEFAULT_TIER  # extend existing import

def build_scrcpy_args(tier: str, scid: int) -> list[str]:
    t = QUALITY_TIERS.get(tier, QUALITY_TIERS[DEFAULT_TIER])
    return [
        "tunnel_forward=true", "video_codec=h264",
        f"max_size={t['max_size']}",
        f"bit_rate={t['bit_rate']}",
        f"max_fps={t['max_fps']}",
        "send_device_meta=true", "send_frame_meta=true",
        "control=true", "audio=false",
        "video_encoder_options=i-frame-interval=1",
        f"scid={scid:x}",
    ]
```

In `ScrcpySession.__init__`, add `tier: str = DEFAULT_TIER` param and `self.tier = tier`.

Change `_start_server(adb, serial, port, scid)` to `_start_server(adb, serial, port, scid, tier)` and build the `app_process` command from `build_scrcpy_args(tier, scid)`:

```python
subprocess.Popen(
    [
        adb, "-s", serial, "shell",
        "CLASSPATH=/data/local/tmp/scrcpy-server.jar"
        " app_process / com.genymobile.scrcpy.Server 3.1 "
        + " ".join(build_scrcpy_args(tier, scid)),
    ],
    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **nw,
)
```

Update the `_start_server(...)` call in `ScrcpySession.start()` to pass `self.tier`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_scrcpy_session.py -v`
Expected: PASS (including the two pre-existing tests).

- [ ] **Step 5: Commit**

```bash
git add src/server/scrcpy_session.py tests/test_scrcpy_session.py
git commit -m "feat: drive scrcpy resolution/bitrate/fps from quality tier"
```

---

## Task 3: Drop the ffmpeg re-encode (passthrough copy)

**Files:**
- Modify: `src/server/scrcpy_session.py` (`_stream_loop` ffmpeg args, ~lines 345-361)
- Test: `tests/test_scrcpy_session.py`

**Interfaces:**
- Produces: `build_ffmpeg_args(ffmpeg_exe: str, rtsp_url: str) -> list[str]` — module-level pure function returning the full ffmpeg argv for a copy-mux (no libx264).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_scrcpy_session.py — add
def test_ffmpeg_args_are_copy_not_reencode():
    from server.scrcpy_session import build_ffmpeg_args
    args = build_ffmpeg_args("ffmpeg", "rtsp://localhost:8554/instance0")
    assert "-c:v" in args
    assert args[args.index("-c:v") + 1] == "copy"
    assert "libx264" not in args
    assert "-f" in args and "rtsp" in args
    assert "-rtsp_transport" in args and "tcp" in args
    assert args[-1] == "rtsp://localhost:8554/instance0"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_scrcpy_session.py::test_ffmpeg_args_are_copy_not_reencode -v`
Expected: FAIL with `ImportError: cannot import name 'build_ffmpeg_args'`.

- [ ] **Step 3: Implement copy-mux args + use them**

```python
# src/server/scrcpy_session.py — add module-level fn
def build_ffmpeg_args(ffmpeg_exe: str, rtsp_url: str) -> list[str]:
    return [
        ffmpeg_exe,
        "-loglevel", "warning",
        "-fflags", "+genpts",
        "-f", "h264",
        "-i", "pipe:0",
        "-c:v", "copy",
        "-f", "rtsp",
        "-rtsp_transport", "tcp",
        rtsp_url,
    ]
```

In `_stream_loop`, replace the inline `subprocess.Popen([...])` ffmpeg argv with:

```python
ffmpeg_proc = subprocess.Popen(
    build_ffmpeg_args(ffmpeg_exe, self.rtsp_url),
    stdin=subprocess.PIPE,
    stdout=subprocess.DEVNULL,
    stderr=subprocess.PIPE,
    **_no_window_flags(),
)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_scrcpy_session.py -v`
Expected: PASS.

- [ ] **Step 5: Manual smoke note (not automated)**

On a Windows target with a live LDPlayer instance, confirm the stream still appears in the browser (copy-mux produces valid RTSP). If ffmpeg logs `non-monotonic DTS` or refuses copy, capture stderr — fallback is `-bsf:v h264_mp4toannexb` or re-adding `-use_wallclock_as_timestamps 1`. Document outcome in the commit body.

- [ ] **Step 6: Commit**

```bash
git add src/server/scrcpy_session.py tests/test_scrcpy_session.py
git commit -m "perf: pipe scrcpy H.264 to mediamtx with -c:v copy (drop re-encode)"
```

---

## Task 4: Runtime tier change on a live session

**Files:**
- Modify: `src/server/scrcpy_session.py` (add `set_tier`)
- Test: `tests/test_scrcpy_session.py`

**Interfaces:**
- Consumes: `build_scrcpy_args` (Task 2), session start/stop.
- Produces: `ScrcpySession.set_tier(tier: str) -> bool` — updates `self.tier`; if running, restarts capture at the new tier; returns True on accepted tier. No-op restart if tier unchanged.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_scrcpy_session.py — add
def test_set_tier_updates_tier_when_not_running():
    from server.scrcpy_session import ScrcpySession
    s = ScrcpySession("emulator-5554", 0, "rtsp://localhost:8554/instance0", 720, 1280)
    assert s.set_tier("1080") is True
    assert s.tier == "1080"

def test_set_tier_rejects_unknown():
    from server.scrcpy_session import ScrcpySession
    s = ScrcpySession("emulator-5554", 0, "rtsp://localhost:8554/instance0", 720, 1280)
    assert s.set_tier("9000") is False
    assert s.tier == "720"

def test_set_tier_same_is_noop_true():
    from server.scrcpy_session import ScrcpySession
    s = ScrcpySession("emulator-5554", 0, "rtsp://localhost:8554/instance0", 720, 1280)
    assert s.set_tier("720") is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_scrcpy_session.py::test_set_tier_updates_tier_when_not_running -v`
Expected: FAIL with `AttributeError: 'ScrcpySession' object has no attribute 'set_tier'`.

- [ ] **Step 3: Implement `set_tier`**

```python
# src/server/scrcpy_session.py — method on ScrcpySession
def set_tier(self, tier: str) -> bool:
    from config import QUALITY_TIERS
    if tier not in QUALITY_TIERS:
        return False
    if tier == self.tier:
        return True
    self.tier = tier
    with self._lock:
        was_running = self._running and self._ffmpeg_proc is not None
    if was_running:
        self.stop()
        self.start()  # start() reads self.tier
    return True
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_scrcpy_session.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/server/scrcpy_session.py tests/test_scrcpy_session.py
git commit -m "feat: ScrcpySession.set_tier restarts capture at new quality tier"
```

---

## Task 5: InstanceManager exposes tier control per instance

**Files:**
- Modify: `src/server/instance_manager.py` (`Instance` stores tier; add `set_tier`)
- Test: `tests/test_instance_manager.py` (create)

**Interfaces:**
- Consumes: `ScrcpySession.set_tier` (Task 4), `DEFAULT_TIER` (Task 1).
- Produces:
  - `Instance.tier: str` (defaults to `DEFAULT_TIER`).
  - `InstanceManager.set_tier(serial: str, tier: str) -> bool` — routes to that instance's session, updates `Instance.tier`, returns False if serial unknown or tier invalid.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_instance_manager.py — create
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from server.instance_manager import InstanceManager
from server.mediamtx_manager import MediamtxManager


def test_set_tier_unknown_serial_false():
    im = InstanceManager(MediamtxManager())
    assert im.set_tier("emulator-9999", "1080") is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_instance_manager.py::test_set_tier_unknown_serial_false -v`
Expected: FAIL with `AttributeError: 'InstanceManager' object has no attribute 'set_tier'`.

- [ ] **Step 3: Implement**

In `Instance.__init__`, add `self.tier = DEFAULT_TIER` (import `DEFAULT_TIER` from `config`). Add to `InstanceManager`:

```python
def set_tier(self, serial: str, tier: str) -> bool:
    with self._lock:
        inst = self._instances.get(serial)
    if inst is None:
        return False
    ok = inst.session.set_tier(tier)
    if ok:
        inst.tier = tier
    return ok
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_instance_manager.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/server/instance_manager.py tests/test_instance_manager.py
git commit -m "feat: InstanceManager.set_tier routes tier change to instance session"
```

---

## Task 6: `/instances/{id}/quality` endpoint

**Files:**
- Modify: `src/server/app.py` (add POST route)
- Test: `tests/test_app.py`

**Interfaces:**
- Consumes: `InstanceManager.set_tier` (Task 5), `TIER_ORDER` (Task 1).
- Produces: `POST /instances/{instance_id}/quality` with JSON body `{"tier": "1080"}` → `{"ok": true, "tier": "1080"}`; 400 on invalid tier, 404 on unknown instance.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_app.py — add (follow existing TestClient fixture pattern in this file)
def test_quality_endpoint_rejects_bad_tier(client):
    r = client.post("/instances/emulator-5554/quality", json={"tier": "9000"})
    assert r.status_code == 400
```

(If `test_app.py` has no `client` fixture, build the app with a stub InstanceManager as the other tests in that file do; match the existing construction.)

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_app.py::test_quality_endpoint_rejects_bad_tier -v`
Expected: FAIL (404 route missing / 405).

- [ ] **Step 3: Implement the route**

```python
# src/server/app.py — inside create_app, near /select
from config import TIER_ORDER  # extend imports

class QualityTierRequest(BaseModel):
    tier: str

@app.post("/instances/{instance_id}/quality")
async def set_instance_quality(instance_id: str, req: QualityTierRequest):
    if req.tier not in TIER_ORDER:
        raise HTTPException(status_code=400, detail="Invalid tier")
    ok = instance_manager.set_tier(instance_id, req.tier)
    if not ok:
        raise HTTPException(status_code=404, detail="Instance not found")
    return {"ok": True, "tier": req.tier}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_app.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/server/app.py tests/test_app.py
git commit -m "feat: POST /instances/{id}/quality to change stream tier at runtime"
```

---

## Task 7: Client adaptive-quality controller (getStats hysteresis)

**Files:**
- Modify: `src/client/app.js` (add adaptive loop; hook into WebRTC lifecycle)
- Test: none automated (browser WebRTC). Manual verification step included.

**Interfaces:**
- Consumes: `_pc` (RTCPeerConnection), `_activeWindowId`, active serial; `POST /instances/{serial}/quality`.
- Produces: `startAdaptiveQuality(serial)`, `stopAdaptiveQuality()`; module state `_currentTier`, `_tierManualUntil`.

- [ ] **Step 1: Add the controller**

```javascript
// app.js — adaptive quality
const _TIER_ORDER = ["480", "720", "1080", "1440"];
let _currentTier = "720";
let _tierManualUntil = 0;      // ms epoch; manual override wins until then
let _adaptiveTimer = null;
let _goodStreak = 0;           // consecutive good samples (for step-up debounce)
let _lastTierChange = 0;
let _adaptiveSerial = null;

function _stepTier(dir) {
  const i = _TIER_ORDER.indexOf(_currentTier);
  const j = Math.max(0, Math.min(_TIER_ORDER.length - 1, i + dir));
  return _TIER_ORDER[j];
}

async function _applyTier(tier) {
  if (tier === _currentTier || !_adaptiveSerial) return;
  _currentTier = tier;
  _lastTierChange = Date.now();
  try {
    await fetch(`/instances/${_adaptiveSerial}/quality`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ tier }),
    });
  } catch (_) {}
}

async function _sampleAndAdapt() {
  if (!_pc || Date.now() < _tierManualUntil) return;
  if (Date.now() - _lastTierChange < 10000) return;  // cooldown
  let loss = 0, rtt = 0, seen = false;
  const stats = await _pc.getStats();
  stats.forEach(r => {
    if (r.type === 'inbound-rtp' && r.kind === 'video') {
      const recv = r.packetsReceived || 0, lost = r.packetsLost || 0;
      if (recv + lost > 0) loss = lost / (recv + lost);
      seen = true;
    }
    if (r.type === 'candidate-pair' && r.state === 'succeeded' && r.currentRoundTripTime != null) {
      rtt = r.currentRoundTripTime * 1000;  // → ms
    }
  });
  if (!seen) return;
  if (loss > 0.03 || rtt > 250) {
    _goodStreak = 0;
    await _applyTier(_stepTier(-1));
  } else if (loss < 0.01 && rtt < 120) {
    _goodStreak += 1;
    if (_goodStreak >= 3) {  // ~15s at 5s cadence
      _goodStreak = 0;
      await _applyTier(_stepTier(+1));
    }
  } else {
    _goodStreak = 0;
  }
}

function startAdaptiveQuality(serial) {
  _adaptiveSerial = serial;
  _goodStreak = 0;
  stopAdaptiveQuality();
  _adaptiveTimer = setInterval(_sampleAndAdapt, 5000);
}

function stopAdaptiveQuality() {
  if (_adaptiveTimer) { clearInterval(_adaptiveTimer); _adaptiveTimer = null; }
}
```

- [ ] **Step 2: Wire into WebRTC lifecycle**

In `initWebRTC`, after `_webrtcActive = true` inside `_pc.ontrack`, call `startAdaptiveQuality(<serial>)`. Thread the serial into `initWebRTC` (add a `serial` param; `selectWindow` already knows it — pass it through from `windows_panel.js` `initWebRTC(id, data.whep_url, data.stun_url)` → add serial). In `_fallbackToMJPEG`, call `stopAdaptiveQuality()`.

- [ ] **Step 3: Manual verification**

Load PWA on a device, start a stream, throttle the network (e.g. iOS Network Link Conditioner "3G"). Within ~15s confirm a `POST /instances/.../quality` with a lower tier fires (server log `[instance] set_tier`), and the stream keeps playing (no PC teardown). Remove throttle → tier steps back up after ~15s stable.

- [ ] **Step 4: Commit**

```bash
git add src/client/app.js src/client/windows_panel.js
git commit -m "feat: client-side adaptive quality via WebRTC getStats hysteresis"
```

---

## Task 8: mediamtx `active` mux path in generated config

> **Gated by Task 0.** Implement only if the spike chose 3a. If the spike chose 3b, skip Tasks 8–10 and do Task 11 instead.

**Files:**
- Modify: `src/server/mediamtx_manager.py` (`_generate_config` adds `active` path; new `active_source` param)
- Test: `tests/test_mediamtx_manager.py`

**Interfaces:**
- Produces: `_generate_config(instance_names, tailscale_ip=None, active_source=None)` — when `active_source` (an instance name) is given, emits an extra path `active` with `source: rtsp://localhost:{MEDIAMTX_PORT}/{active_source}` and `sourceOnDemand: no`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_mediamtx_manager.py — add
def test_generate_config_active_path():
    from config import MEDIAMTX_PORT
    cfg = _generate_config(["instance0", "instance1"], active_source="instance1")
    assert "active:" in cfg
    assert f"rtsp://localhost:{MEDIAMTX_PORT}/instance1" in cfg

def test_generate_config_no_active_when_none():
    cfg = _generate_config(["instance0"])
    assert "\n  active:" not in cfg
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_mediamtx_manager.py::test_generate_config_active_path -v`
Expected: FAIL (`active:` absent / unexpected kwarg).

- [ ] **Step 3: Implement**

Add `active_source: str | None = None` to `_generate_config`. Build the paths block so instance paths render as before, then append the `active` path when `active_source`:

```python
active_block = ""
if active_source:
    active_block = (
        "  active:\n"
        f"    source: rtsp://localhost:{MEDIAMTX_PORT}/{active_source}\n"
        "    sourceOnDemand: no\n"
    )
# ...
paths:
{paths_config}
{active_block}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_mediamtx_manager.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/server/mediamtx_manager.py tests/test_mediamtx_manager.py
git commit -m "feat: mediamtx 'active' mux path sourced from selected instance"
```

---

## Task 9: Repoint `active` source on select (mechanism from Task 0)

**Files:**
- Modify: `src/server/mediamtx_manager.py` (add `set_active_source`)
- Modify: `src/server/instance_manager.py` (`select` calls repoint)
- Test: `tests/test_mediamtx_manager.py`

**Interfaces:**
- Consumes: Task 0 verdict (config-reload vs API patch), `_generate_config` `active_source` (Task 8).
- Produces: `MediamtxManager.set_active_source(instance_name: str) -> None` — repoints the `active` path to that instance using the spike-chosen mechanism (rewrite config + reload, or `PATCH /v3/config/paths/patch/active`). Idempotent.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_mediamtx_manager.py — add
def test_set_active_source_records_current():
    m = MediamtxManager()
    m.set_active_source("instance0")   # no process running → should not raise
    assert m._active_source == "instance0"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_mediamtx_manager.py::test_set_active_source_records_current -v`
Expected: FAIL (`AttributeError`).

- [ ] **Step 3: Implement**

Store `self._active_source: str | None = None` in `__init__`. Implement `set_active_source` per Task 0's mechanism. Reference implementation for the **API-patch** path (only if spike chose it; otherwise config-rewrite-and-restart):

```python
def set_active_source(self, instance_name: str) -> None:
    self._active_source = instance_name
    if not self.running:
        return
    # API mechanism (api: yes): patch the 'active' path source live.
    import urllib.request, json
    from config import MEDIAMTX_PORT
    body = json.dumps({
        "source": f"rtsp://localhost:{MEDIAMTX_PORT}/{instance_name}",
        "sourceOnDemand": False,
    }).encode()
    req = urllib.request.Request(
        "http://127.0.0.1:9997/v3/config/paths/patch/active",
        data=body, method="PATCH",
        headers={"Content-Type": "application/json"},
    )
    try:
        urllib.request.urlopen(req, timeout=3).read()
    except Exception:
        _log(f"[mediamtx] set_active_source failed for {instance_name}")
```

(If spike chose config-rewrite: regenerate the yml with new `active_source` and restart mediamtx; accept the ~1s blip — still no browser re-ICE. Enable `api: yes` + `apiAddress: :9997` in `_generate_config` if using the API path.)

In `InstanceManager.select`, after setting `_active_serial`, call `self._mediamtx.set_active_source(instance_name(serial))`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_mediamtx_manager.py tests/test_instance_manager.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/server/mediamtx_manager.py src/server/instance_manager.py tests/test_mediamtx_manager.py
git commit -m "feat: repoint mediamtx 'active' path on instance select (no browser re-ICE)"
```

---

## Task 10: Browser connects once to `active`; switch stops tearing down PC

**Files:**
- Modify: `src/server/app.py` (`select` responses return the `active` WHEP url)
- Modify: `src/client/windows_panel.js` (`selectWindow` no longer re-inits WebRTC per switch after first connect)
- Modify: `src/client/app.js` (`initWebRTC` connects to `active`; guard against needless re-negotiation)
- Test: `tests/test_app.py`

**Interfaces:**
- Consumes: mux path from Tasks 8–9.
- Produces: select endpoints return `whep_url` pointing at `.../active/whep`. Client keeps one PC to `active`; switching only POSTs select (server repoints) — WebRTC untouched after first connect.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_app.py — add
def test_select_returns_active_whep(client):
    r = client.post("/instances/emulator-5554/select")
    # 200 path: whep_url ends with /active/whep
    if r.status_code == 200:
        assert r.json()["whep_url"].endswith("/active/whep")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_app.py::test_select_returns_active_whep -v`
Expected: FAIL (url still `.../{inst.name}/whep`).

- [ ] **Step 3: Implement**

In both select handlers, build `whep_url = f"http://{host}:{WHEP_PORT}/active/whep"`. In `windows_panel.js` `selectWindow`, POST select as now, but call `initWebRTC` **only if no live PC** (`if (!_webrtcActive) initWebRTC(...)`); otherwise the repoint alone switches video. In `app.js`, keep `_adaptiveSerial`/serial current on each select so adaptive quality targets the newly-selected instance.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_app.py -v`
Expected: PASS.

- [ ] **Step 5: Manual verification**

On device with ≥2 instances: connect, then hit prev/next repeatedly. Video should switch in well under a second with no reconnect spinner and no ICE churn in console.

- [ ] **Step 6: Commit**

```bash
git add src/server/app.py src/client/windows_panel.js src/client/app.js
git commit -m "feat: single WHEP PC to 'active' path — near-instant instance switch"
```

---

## Task 11: (FALLBACK 3b) PC-reuse + ICE-restart switch

> **Only if Task 0 chose 3b.** Supersedes Tasks 8–10.

**Files:**
- Modify: `src/client/app.js` (`switchInstance(whepUrl)` reuses `_pc`, renegotiates)
- Modify: `src/client/windows_panel.js` (`selectWindow` calls `switchInstance` not full `initWebRTC`)
- Test: none automated; manual.

**Interfaces:**
- Produces: `switchInstance(whepUrl)` — reuses the existing `_pc`, creates a new offer with `iceRestart: true` only when candidates are stale, POSTs to the new path's WHEP, applies answer. Falls back to full `initWebRTC` if `_pc` is null/closed.

- [ ] **Step 1: Implement `switchInstance`**

```javascript
// app.js — 3b fallback
async function switchInstance(whepUrl) {
  if (!_pc || _pc.connectionState === 'closed') { return initWebRTC(_activeWindowId, whepUrl); }
  _whepUrl = whepUrl;
  const offer = await _pc.createOffer({ iceRestart: false });
  await _pc.setLocalDescription(offer);
  await waitForIceGatheringComplete(_pc, 800);
  const r = await fetch(whepUrl, {
    method: 'POST',
    headers: { 'Content-Type': 'application/sdp' },
    body: _pc.localDescription.sdp,
  });
  if (!r.ok) return initWebRTC(_activeWindowId, whepUrl);
  await _pc.setRemoteDescription({ type: 'answer', sdp: await r.text() });
}
```

- [ ] **Step 2: Wire into select**

In `windows_panel.js` `selectWindow`: if a PC is live, call `switchInstance(data.whep_url)`; else `initWebRTC(...)`.

- [ ] **Step 3: Manual verification**

Prev/next should switch in ~300-500ms (vs the old 1-3s), reusing the PC (no full ICE gather).

- [ ] **Step 4: Commit**

```bash
git add src/client/app.js src/client/windows_panel.js
git commit -m "feat: reuse WebRTC PC on switch (3b) — sub-second instance change"
```

---

## Task 12: Update docs + README

**Files:**
- Modify: `README.md` (quality tiers + adaptive note)
- Modify: `docs/TROUBLESHOOTING.md` (copy-mux note; if scrcpy emits bad H.264, the `h264_mp4toannexb` bitstream filter fix)

- [ ] **Step 1: Document tiers + adaptivity in README**

Add a short "Streaming quality" section: tiers 480/720/1080/1440, auto-adapts by network, manual override via UI.

- [ ] **Step 2: Add copy-mux troubleshooting note**

Note that ffmpeg now uses `-c:v copy`; if a device's scrcpy build produces a stream mediamtx rejects, add `-bsf:v h264_mp4toannexb` in `build_ffmpeg_args`.

- [ ] **Step 3: Commit**

```bash
git add README.md docs/TROUBLESHOOTING.md
git commit -m "docs: streaming quality tiers, adaptivity, copy-mux troubleshooting"
```

---

## Self-Review Notes

- **Spec coverage:** Section 1 → Tasks 1-4; Section 2 → Tasks 5-7; Section 3 → Task 0 (spike) + Tasks 8-10 (3a) or Task 11 (3b). Docs → Task 12. All covered.
- **Type consistency:** `build_scrcpy_args`, `build_ffmpeg_args`, `set_tier`, `set_active_source`, `_active_source`, `startAdaptiveQuality`/`stopAdaptiveQuality`, `switchInstance` used consistently across tasks.
- **Ordering:** Task 0 gates 8-11. Tasks 1-7 are independent of the switch mechanism and can ship first (immediate quality + latency win). 8-10 (or 11) ship second.
