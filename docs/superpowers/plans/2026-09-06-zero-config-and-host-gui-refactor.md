# Zero-Config Discovery & Host GUI Refactor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Eliminate manual server IP entry across mobile/web clients by leveraging the existing VPS HTTP tunnel and restoring WebRTC dual-transport ICE discovery, while redesigning the Windows host application into a lean ~400px Minimal Host Monitor widget and retiring pywebview.

**Architecture:** The client connects to the VPS HTTP tunnel for initial REST API requests (`/instances`, `/select`) and initiates streaming via the VPS signaling server. WebRTC ICE candidate discovery automatically resolves direct peer-to-peer LAN IP when local and coturn TURN relay when remote. The Windows host GUI drops pywebview, DesktopWindow, and QR pairing, adopting an Option B minimal card layout with tray minimization.

**Tech Stack:** Python 3.11+ / FastAPI / PyQt5 / pystray (Desktop Host); TypeScript / React Native (`packages/core`, `packages/ui`); Expo (`apps/mobile`); Next.js (`apps/web`); Node.js ws (`infra/vps/`).

**Spec:** `docs/superpowers/specs/2026-09-06-zero-config-and-host-gui-refactor-design.md`

## Global Constraints

- Never call `webview.start()` or embed a browser window in the Python host process; `pywebview` is retired.
- Maintain single-responsibility module boundaries: WebRTC signaling and session orchestration live in `@wc/core/src/webrtc/`, shared UI in `packages/ui/`, platform shells in `apps/`.
- No AI attribution footer or Co-Authored-By trailers in git commits.
- Follow existing commit message convention: `<type>(optional-scope): imperative description`.

---

### Task 1: Retire `pywebview` and DesktopWindow from Desktop Shell

**Files:**
- Delete: `apps/desktop/window.py`
- Delete: `apps/desktop/webview_main.py`
- Delete: `apps/desktop/test_window.py`
- Modify: `src/main.py:270-285`
- Modify: `pyproject.toml`
- Test: `apps/desktop/test_tray.py`

**Interfaces:**
- Consumes: Existing `TrayIcon` from `apps/desktop/tray.py`.
- Produces: Cleaned `src/main.py` entry point that runs server and launcher without pywebview subprocess.

- [ ] **Step 1: Verify existing desktop tests pass before removal**

Run: `uv run pytest apps/desktop/ -v`
Expected: PASS (8 passed in test_tray.py and test_window.py)

- [ ] **Step 2: Delete pywebview wrapper and child entrypoint files**

```bash
rm -f apps/desktop/window.py apps/desktop/webview_main.py apps/desktop/test_window.py
```

- [ ] **Step 3: Remove pywebview dependency from pyproject.toml and update main.py**

In `pyproject.toml`, remove `"pywebview>=5.0"` from dependencies.

In `src/main.py`, remove:
```python
    from apps.desktop.window import DesktopWindow  # or window import
    desktop_window = DesktopWindow(...)
    launcher = LauncherWindow(on_open_app=desktop_window.show)
```
and replace with:
```python
    launcher = LauncherWindow()
```
Also remove `--webview-window` argument parsing in `src/main.py:305-316`.

- [ ] **Step 4: Run uv sync and desktop test suite**

Run: `uv sync && uv run pytest apps/desktop/ -v`
Expected: PASS (test_tray.py passes, no test_window.py)

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml uv.lock src/main.py apps/desktop/
git commit -m "refactor(desktop): retire pywebview and DesktopWindow child process"
```

---

### Task 2: Redesign `LauncherWindow` to Option B Minimal Host Monitor

**Files:**
- Modify: `src/gui/launcher.py`
- Create: `tests/test_launcher_widget.py`
- Test: `tests/test_launcher_widget.py`

**Interfaces:**
- Consumes: `PORT`, `VERSION`, `SUPABASE_URL` from `config.py`; `get_best_ip`, `has_tailscale` from `server.tailscale`.
- Produces: `LauncherWindow()` without `on_open_app`, rendering Option B Minimal Host Monitor layout.

- [ ] **Step 1: Write unit test for LauncherWindow Option B components**

Create `tests/test_launcher_widget.py`:
```python
import pytest
from unittest.mock import MagicMock, patch

def test_launcher_window_constructs_without_open_app():
    # Test that LauncherWindow does not require on_open_app and does not have _open_app_btn or _qr_label
    with patch("gui.launcher.check_for_update"):
        from gui.launcher import LauncherWindow
        window = LauncherWindow()
        assert not hasattr(window, "_open_app_btn")
        assert not hasattr(window, "_qr_label")
        assert hasattr(window, "_status_label")
        assert hasattr(window, "_ip_label")
        assert window.windowTitle().startswith("WindowControl Host")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_launcher_widget.py -v`
Expected: FAIL (Window title or removed attributes differ)

- [ ] **Step 3: Implement Option B layout in `src/gui/launcher.py`**

Refactor `LauncherWindow._setup_ui()`:
- Title: `f"WindowControl Host v{VERSION}"`
- Resize: width 400, height 460
- Header: Title, status indicator dot, `:8080` port.
- Status Card:
  - Account: Supabase logged-in user or "Auth disabled (LAN mode)"
  - Network: Local LAN IP and Tailscale IP
  - VPS Relay: Status indicator for `VPS_SIGNALING_URL`
  - Active streams count
- Actions:
  - "Minimize to Tray" button (`self.hide`)
  - "Stop Server" button
- Remove `self._qr_label`, `qrcode` generation, and `self._open_app_btn`.
- Override `closeEvent(event)`: `event.ignore(); self.hide()` so closing minimizes to tray.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_launcher_widget.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/gui/launcher.py tests/test_launcher_widget.py
git commit -m "feat(gui): redesign launcher to Option B minimal host monitor widget"
```

---

### Task 3: Implement WebRTC Public Signaling Client in `@wc/core`

**Files:**
- Create: `packages/core/src/webrtc/signaling.ts`
- Create: `packages/core/src/webrtc/signaling.test.ts`
- Modify: `packages/core/src/index.ts`
- Test: `packages/core/src/webrtc/signaling.test.ts`

**Interfaces:**
- Consumes: WebSocket implementation, Supabase access token.
- Produces: `connectSignalingViewer(opts: SignalingViewerOpts): Promise<SignalingExchange>`.

- [ ] **Step 1: Write unit test for signaling client handshake**

Create `packages/core/src/webrtc/signaling.test.ts`:
```typescript
import { connectSignalingViewer } from "./signaling";

class FakeWebSocket {
  url: string;
  sent: string[] = [];
  listeners: Record<string, ((e: any) => void)[]> = {};
  readyState = 0; // CONNECTING
  constructor(url: string) { this.url = url; }
  addEventListener(type: string, cb: (e: any) => void) {
    this.listeners[type] = this.listeners[type] || [];
    this.listeners[type].push(cb);
  }
  send(data: string) { this.sent.push(data); }
  close() { this.readyState = 3; }
  simulateOpen() {
    this.readyState = 1;
    (this.listeners["open"] || []).forEach((cb) => cb({}));
  }
  simulateMessage(data: string) {
    (this.listeners["message"] || []).forEach((cb) => cb({ data }));
  }
}

test("connectSignalingViewer sends offer SDP and receives answer", async () => {
  let createdWs: FakeWebSocket | null = null;
  const promise = connectSignalingViewer({
    signalingUrl: "wss://relay.example.com/ws",
    sessionId: "user123.instance1",
    token: "tokenABC",
    offerSdp: "v=0
o=offer...",
    WebSocketImpl: (url: string) => {
      createdWs = new FakeWebSocket(url);
      return createdWs;
    },
    timeoutMs: 1000,
  });

  expect(createdWs).not.toBeNull();
  expect(createdWs!.url).toContain("session=user123.instance1");
  expect(createdWs!.url).toContain("role=viewer");
  expect(createdWs!.url).toContain("token=tokenABC");

  createdWs!.simulateOpen();
  expect(createdWs!.sent).toEqual(["v=0
o=offer..."]);

  createdWs!.simulateMessage("v=0
o=answer...");
  const answer = await promise;
  expect(answer.answerSdp).toBe("v=0
o=answer...");
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm run test:core -- signaling.test.ts`
Expected: FAIL (Cannot find module `./signaling`)

- [ ] **Step 3: Implement `packages/core/src/webrtc/signaling.ts`**

```typescript
export type SignalingViewerOpts = {
  signalingUrl: string;
  sessionId: string;
  token: string;
  offerSdp: string;
  WebSocketImpl?: any;
  timeoutMs?: number;
};

export type SignalingResult = {
  answerSdp: string;
  close: () => void;
};

export function connectSignalingViewer(opts: SignalingViewerOpts): Promise<SignalingResult> {
  const WS = opts.WebSocketImpl || WebSocket;
  const timeoutMs = opts.timeoutMs ?? 8000;
  const url = new URL(opts.signalingUrl);
  url.searchParams.set("session", opts.sessionId);
  url.searchParams.set("role", "viewer");
  if (opts.token) url.searchParams.set("token", opts.token);

  const ws = new WS(url.toString());

  return new Promise((resolve, reject) => {
    let resolved = false;
    let timer: any = null;

    const cleanup = () => {
      if (timer) clearTimeout(timer);
    };

    if (timeoutMs > 0) {
      timer = setTimeout(() => {
        if (!resolved) {
          resolved = true;
          ws.close();
          reject(new Error("Signaling timeout"));
        }
      }, timeoutMs);
    }

    const listen = (type: string, fn: (e: any) => void) => {
      if (typeof ws.addEventListener === "function") ws.addEventListener(type, fn);
      else ws[`on${type}`] = fn;
    };

    listen("open", () => {
      ws.send(opts.offerSdp);
    });

    listen("message", (event: any) => {
      if (resolved) return;
      resolved = true;
      cleanup();
      resolve({
        answerSdp: event.data,
        close: () => { try { ws.close(); } catch {} },
      });
    });

    listen("error", () => {
      if (resolved) return;
      resolved = true;
      cleanup();
      reject(new Error("Signaling WebSocket error"));
    });

    listen("close", (e: any) => {
      if (resolved) return;
      resolved = true;
      cleanup();
      reject(new Error(`Signaling closed: ${e?.reason || "unknown"}`));
    });
  });
}
```
Export `connectSignalingViewer` in `packages/core/src/index.ts`.

- [ ] **Step 4: Run test to verify it passes**

Run: `npm run test:core -- signaling.test.ts`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add packages/core/src/webrtc/signaling.ts packages/core/src/webrtc/signaling.test.ts packages/core/src/index.ts
git commit -m "feat(core): add public signaling WebSocket client"
```

---

### Task 4: Implement Dual-Transport WebRTC Session Manager in `@wc/core`

**Files:**
- Create: `packages/core/src/webrtc/session.ts`
- Create: `packages/core/src/webrtc/session.test.ts`
- Modify: `packages/core/src/index.ts`
- Test: `packages/core/src/webrtc/session.test.ts`

**Interfaces:**
- Consumes: `connectWhep` from `whep.ts`, `connectSignalingViewer` from `signaling.ts`, `SelectResp` from `client.ts`.
- Produces: `connectEngineSession(opts: ConnectEngineSessionOpts): Promise<EngineSession>`.

- [ ] **Step 1: Write unit test for dual-transport racing and fallback**

Create `packages/core/src/webrtc/session.test.ts`:
```typescript
import { connectEngineSession } from "./session";

test("connectEngineSession adopts the faster transport and closes the other", async () => {
  let localClosed = false;
  let publicClosed = false;

  const session = await connectEngineSession({
    selection: {
      whep_url: "http://192.168.1.10:8080/whep",
      whep_token: "tokLocal",
      signaling_url: "wss://relay.example.com/ws",
      public_session: "user.inst1",
      ice_servers: [],
    } as any,
    authToken: "jwt",
    startLocalImpl: async () => {
      // Local wins fast
      return {
        kind: "local",
        stream: {} as any,
        input: { close: () => {} } as any,
        close: async () => { localClosed = true; },
      };
    },
    startPublicImpl: async () => {
      await new Promise((r) => setTimeout(r, 50));
      return {
        kind: "public",
        stream: {} as any,
        input: { close: () => {} } as any,
        close: async () => { publicClosed = true; },
      };
    },
  });

  expect(session.kind).toBe("local");
  // The slower public attempt should be closed
  await new Promise((r) => setTimeout(r, 80));
  expect(publicClosed).toBe(true);
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm run test:core -- session.test.ts`
Expected: FAIL (Cannot find module `./session`)

- [ ] **Step 3: Implement `packages/core/src/webrtc/session.ts`**

Implement `connectEngineSession`:
- Rework the race pattern from `src/client/engine_session.js`:
  - Launch `startLocal` if `selection.whep_url` is present.
  - Launch `startPublic` if `selection.signaling_url` and `selection.public_session` are present.
  - Race `Promise.race([localPromise, publicPromise])`.
  - When winner resolves, cancel/close remaining attempts.
  - If one fails, wait for the other; only reject if all configured transports fail.
- Export `EngineSession` and `connectEngineSession` in `packages/core/src/index.ts`.

- [ ] **Step 4: Run test to verify it passes**

Run: `npm run test:core -- session.test.ts`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add packages/core/src/webrtc/session.ts packages/core/src/webrtc/session.test.ts packages/core/src/index.ts
git commit -m "feat(core): implement dual-transport WebRTC session manager"
```

---

### Task 5: Update `Stream.tsx` to Use Dual-Transport Session

**Files:**
- Modify: `packages/ui/src/screens/Stream.tsx:61-125`
- Modify: `packages/ui/src/screens/Stream.test.tsx`
- Test: `packages/ui/src/screens/Stream.test.tsx`

**Interfaces:**
- Consumes: `connectEngineSession` from `@wc/core`.
- Produces: Stream component streaming over direct LAN or public relay seamlessly.

- [ ] **Step 1: Update Stream tests for dual-transport session adoption**

In `packages/ui/src/screens/Stream.test.tsx`, mock `connectEngineSession` instead of `connectWhep` and verify stream connection passes `signaling_url` and `public_session`.

- [ ] **Step 2: Run test to verify it fails**

Run: `npm run test:ui -- Stream.test.tsx`
Expected: FAIL

- [ ] **Step 3: Update `packages/ui/src/screens/Stream.tsx`**

Replace `connectWhep` call in `start()`:
```typescript
import { useServer, connectEngineSession, normalizeCoords, makeAdaptive } from "@wc/core";
```
In `start()`:
```typescript
const s = await connectEngineSession({
  selection: sel,
  authToken,
  RTCImpl,
  onStream: (stream) => { if (gen === startGen.current) nextStream = stream; },
  onInputRtt: (ms) => { if (gen === startGen.current) setRtt(ms); },
  onState: (st) => {
    if (gen !== startGen.current) return;
    setNet(st);
    if (st === "disconnected" && gen === startGen.current) {
      releaseActiveDrag();
      start();
    }
  },
});
```

- [ ] **Step 4: Run test to verify it passes**

Run: `npm run test:ui -- Stream.test.tsx`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add packages/ui/src/screens/Stream.tsx packages/ui/src/screens/Stream.test.tsx
git commit -m "feat(ui): switch Stream screen to dual-transport engine session"
```

---

### Task 6: Remove `ServerSetup` and Update Mobile Navigation Flow

**Files:**
- Delete: `packages/ui/src/screens/ServerSetup.tsx`
- Delete: `packages/ui/src/screens/ServerSetup.test.tsx`
- Modify: `packages/ui/src/index.ts`
- Modify: `packages/core/src/api/ServerContext.tsx`
- Modify: `apps/mobile/src/navigation/Root.tsx`
- Test: `npm run test:ui`, `npm run test:core`

**Interfaces:**
- Consumes: Default API tunnel URL from environment (`EXPO_PUBLIC_API_URL`).
- Produces: Streamlined mobile navigation without manual server IP entry.

- [ ] **Step 1: Remove ServerSetup files and update exports**

```bash
rm -f packages/ui/src/screens/ServerSetup.tsx packages/ui/src/screens/ServerSetup.test.tsx
```
In `packages/ui/src/index.ts`, remove export of `ServerSetup`.

- [ ] **Step 2: Update ServerContext default URL handling**

In `packages/core/src/api/ServerContext.tsx`:
Set default `base` state from `process.env.EXPO_PUBLIC_API_URL || process.env.NEXT_PUBLIC_API_URL || ""` if not already saved in `plainStorage`.

- [ ] **Step 3: Update `apps/mobile/src/navigation/Root.tsx`**

Remove `ServerSetup` import and screen:
```typescript
export function RootNavigator() {
  const { authToken } = useServer();
  const initialRoute = !authToken ? "Login" : "InstanceList";
  return (
    <Stack.Navigator screenOptions={{ headerShown: false }} initialRouteName={initialRoute}>
      <Stack.Screen name="Login" component={Login} />
      <Stack.Screen name="InstanceList" component={InstanceList} />
      <Stack.Screen name="Stream" component={StreamScreen} />
    </Stack.Navigator>
  );
}
```

- [ ] **Step 4: Run packages test suites**

Run: `npm run test:core && npm run test:ui`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add packages/ui/ packages/core/ apps/mobile/
git commit -m "feat(mobile,ui): remove ServerSetup screen and streamline navigation"
```

---

### Task 7: Update Web Client Routing and Root Page

**Files:**
- Delete: `apps/web/src/app/setup/page.tsx`
- Modify: `apps/web/src/app/page.tsx`
- Test: `apps/web/test/` or `npm test -w apps/web`

**Interfaces:**
- Consumes: `useServer()` authToken state.
- Produces: Web client root page redirecting directly to `/login` or `/instances`.

- [ ] **Step 1: Delete web setup route and update root page**

```bash
rm -rf apps/web/src/app/setup
```

In `apps/web/src/app/page.tsx`:
```typescript
export default function RootPage() {
  const router = useRouter();
  const { ready, authToken } = useServer();
  useEffect(() => {
    if (!ready) return;
    if (!authToken) router.replace("/login");
    else router.replace("/instances");
  }, [ready, authToken, router]);
  return null;
}
```

- [ ] **Step 2: Run web tests and export build check**

Run: `npm test -w apps/web && npm run build -w apps/web`
Expected: PASS, build static export succeeds in `apps/web/out`.

- [ ] **Step 3: Commit**

```bash
git add apps/web/
git commit -m "feat(web): remove /setup page and route directly to login/instances"
```

---

### Task 8: End-to-End Monorepo Verification & Integration

**Files:**
- Modify: `CHANGELOG.md`
- Test: `uv run pytest tests/ apps/desktop/ -v`
- Test: `npm run test:core && npm run test:ui && npm test -w apps/web`

**Interfaces:**
- Consumes: All updated desktop, core, ui, and app packages.
- Produces: Verified passing test suites and updated changelog.

- [ ] **Step 1: Run Python desktop test suite**

Run: `uv run pytest tests/ apps/desktop/ -v`
Expected: PASS

- [ ] **Step 2: Run all TypeScript workspace test suites**

Run: `npm run test:core && npm run test:ui && npm test -w apps/web`
Expected: PASS

- [ ] **Step 3: Update CHANGELOG.md**

Record:
- Removal of manual Server base URL screen and addition of zero-config WebRTC dual-transport streaming.
- Windows host desktop redesign into Option B Minimal Host Monitor Widget with pywebview retirement.

- [ ] **Step 4: Commit**

```bash
git add CHANGELOG.md
git commit -m "docs: update changelog for zero-config discovery and host gui refactor"
```
