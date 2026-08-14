# Expo Native Client Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a React Native (Expo) remote-control client in `mobile/` that reaches feature parity with the current web PWA (`src/client/`), streaming scrcpy/mediamtx WebRTC over Tailscale via WHEP and forwarding touch/keyboard input over a WebSocket, styled to the Claude Design "Modernist" dark theme.

**Architecture:** Expo dev-client app (Expo Go cannot load `react-native-webrtc`). Three screens (ServerSetup → InstanceList → Stream) over React Navigation. A `ServerContext` holds the persisted base URL and prefixes all HTTP/WS calls. WHEP negotiation, gesture→normalized-coords input, and downgrade-only adaptive quality are direct ports of the web client's proven logic. Pure logic (coord math, tier stepping, URL building) is TDD'd with Jest; WebRTC/gesture integration is device-verified against a manual checklist. The Python server is unchanged.

**Tech Stack:** Expo SDK (latest), TypeScript, `react-native-webrtc` + `@config-plugins/react-native-webrtc`, `@react-navigation/native` + native-stack, `@react-native-async-storage/async-storage`, `react-native-gesture-handler`, `react-native-reanimated`, `expo-font` + `@expo-google-fonts/archivo`, Jest + `@testing-library/react-native`, EAS Build.

**Spec:** `docs/superpowers/specs/2026-08-14-expo-native-client-design.md`

## Global Constraints

- **Platforms:** iOS + Android. Both require an EAS **dev-client** build; Expo Go will not work (native `react-native-webrtc`).
- **Server is unchanged.** No new endpoints, no server-side URL derivation. `POST /instances/{serial}/select` already returns absolute `whep_url`/`stun_url` (Tailscale IP); use them verbatim.
- **App lives in `mobile/`** in this repo. The web client `src/client/` is being replaced but is NOT deleted by this plan (removal is a later cleanup, out of scope).
- **Language:** TypeScript throughout.
- **Package manager:** use `npm` (repo has no JS workspace; `npx` for expo/eas — no global installs; node is v22).
- **Server message shapes (must match exactly — server parses these):** input WS messages are JSON with `type` ∈ `{echo, click, drag_start, drag_move, drag_end, scroll, key}`; coordinates `x`,`y` are floats in `[0,1]`; `drag_move`/`drag_end` carry optional `scroll` bool; `scroll` carries `dy` (±1); `key` carries `key` (JS key name, e.g. `"Return"`, `"BackSpace"`, `"ArrowLeft"`). `echo` carries `t` (ms epoch) and is echoed back.
- **Quality tiers:** `["480","720","1080","1440"]`; default `"720"`; `"auto"` = no pin. Preferred tier persisted.
- **Design tokens (Modernist, dark):** font Archivo (400/600/800); accent `#9dbf95`; bg `#141312`; deep `#0c0b0b`; surface `#201e1d`; text `#f3f2f2`; error `#ff563c`; **border-radius 0 everywhere**; rules **2px**; net dot connected `#4ade80` / connecting `#facc15` / disconnected `#ff563c`.
- **Commit style (repo CLAUDE.md):** do NOT add `Co-Authored-By` lines to commits.
- **All work happens on a feature branch, never `main`.**

---

### Task 1: Scaffold the Expo app and design tokens

**Files:**
- Create: `mobile/package.json`, `mobile/app.json`, `mobile/eas.json`, `mobile/tsconfig.json`, `mobile/babel.config.js`, `mobile/App.tsx`, `mobile/index.ts`
- Create: `mobile/src/theme/tokens.ts`
- Create: `mobile/.gitignore`
- Test: `mobile/src/theme/tokens.test.ts`

**Interfaces:**
- Produces: `theme` object from `mobile/src/theme/tokens.ts` with exact fields:
  `theme.color.accent`, `.bg`, `.deep`, `.surface`, `.text`, `.textMuted`, `.error`,
  `.netConnected`, `.netConnecting`, `.netDisconnected`;
  `theme.radius` (= 0); `theme.rule` (= 2);
  `theme.font.regular` (= `"Archivo_400Regular"`), `.semibold` (= `"Archivo_600SemiBold"`), `.bold` (= `"Archivo_800ExtraBold"`), `.mono` (platform mono).

- [ ] **Step 1: Create the Expo app scaffold**

Run (from repo root):
```bash
cd mobile 2>/dev/null || npx create-expo-app@latest mobile --template blank-typescript
```
If `mobile/` already exists and is empty, instead run `npx create-expo-app@latest mobile --template blank-typescript` from the repo root. This produces `package.json`, `app.json`, `App.tsx`, `tsconfig.json`, `babel.config.js`, `.gitignore`.

- [ ] **Step 2: Install runtime + dev dependencies**

Run (from `mobile/`):
```bash
npx expo install react-native-webrtc @config-plugins/react-native-webrtc \
  @react-navigation/native @react-navigation/native-stack \
  react-native-screens react-native-safe-area-context \
  @react-native-async-storage/async-storage \
  react-native-gesture-handler react-native-reanimated \
  expo-font @expo-google-fonts/archivo expo-dev-client
npm i -D jest jest-expo @testing-library/react-native @types/jest @types/react
```

- [ ] **Step 3: Configure the WebRTC config plugin and dev-client**

Edit `mobile/app.json` — add under `expo.plugins`: `"@config-plugins/react-native-webrtc"` and `"expo-font"`; set `expo.jsEngine` to `"hermes"`; add iOS camera/mic usage strings (the plugin requires them even for recvonly):
```json
{
  "expo": {
    "name": "WindowControl",
    "slug": "window-control",
    "plugins": [
      "@config-plugins/react-native-webrtc",
      "expo-font"
    ],
    "ios": { "infoPlist": {
      "NSCameraUsageDescription": "Required by the WebRTC engine; the app does not use the camera.",
      "NSMicrophoneUsageDescription": "Required by the WebRTC engine; the app does not use the microphone."
    }},
    "android": { "permissions": ["INTERNET", "ACCESS_NETWORK_STATE"] }
  }
}
```

- [ ] **Step 4: Add Jest config to `mobile/package.json`**

Add:
```json
"scripts": { "test": "jest" },
"jest": {
  "preset": "jest-expo",
  "transformIgnorePatterns": [
    "node_modules/(?!((jest-)?react-native|@react-native(-community)?|expo(nent)?|@expo(nent)?/.*|@expo-google-fonts/.*|react-navigation|@react-navigation/.*|react-native-webrtc|react-native-gesture-handler|react-native-reanimated)/)"
  ]
}
```

- [ ] **Step 5: Write the failing test for tokens**

`mobile/src/theme/tokens.test.ts`:
```ts
import { theme } from "./tokens";

test("Modernist tokens carry the spec values", () => {
  expect(theme.color.accent).toBe("#9dbf95");
  expect(theme.color.bg).toBe("#141312");
  expect(theme.color.surface).toBe("#201e1d");
  expect(theme.color.error).toBe("#ff563c");
  expect(theme.radius).toBe(0);
  expect(theme.rule).toBe(2);
  expect(theme.font.bold).toBe("Archivo_800ExtraBold");
});
```

- [ ] **Step 6: Run test to verify it fails**

Run: `cd mobile && npm test -- tokens`
Expected: FAIL — cannot find module `./tokens`.

- [ ] **Step 7: Implement `mobile/src/theme/tokens.ts`**

```ts
import { Platform } from "react-native";

export const theme = {
  color: {
    accent: "#9dbf95",
    bg: "#141312",
    deep: "#0c0b0b",
    surface: "#201e1d",
    text: "#f3f2f2",
    textMuted: "rgba(243,242,242,0.5)",
    error: "#ff563c",
    netConnected: "#4ade80",
    netConnecting: "#facc15",
    netDisconnected: "#ff563c",
  },
  radius: 0,
  rule: 2,
  font: {
    regular: "Archivo_400Regular",
    semibold: "Archivo_600SemiBold",
    bold: "Archivo_800ExtraBold",
    mono: Platform.select({ ios: "Menlo", android: "monospace", default: "monospace" })!,
  },
} as const;
```

- [ ] **Step 8: Run test to verify it passes**

Run: `cd mobile && npm test -- tokens`
Expected: PASS.

- [ ] **Step 9: Commit**

```bash
git add mobile
git commit -m "feat(mobile): scaffold Expo app with Modernist design tokens"
```

---

### Task 2: Pure input math — normalizeCoords (TDD)

**Files:**
- Create: `mobile/src/input/coords.ts`
- Test: `mobile/src/input/coords.test.ts`

**Interfaces:**
- Produces: `normalizeCoords(pointer, rect, content): { x: number; y: number }` where
  `pointer = { x: number; y: number }` (px within the element),
  `rect = { width: number; height: number }` (element box px),
  `content = { w: number; h: number }` (video intrinsic px, from the select response).
  Returns normalized `[0,1]` coords accounting for `object-fit: contain` letterboxing, clamped to `[0,1]`. Direct port of the web `normalizeCoords`.

- [ ] **Step 1: Write the failing tests**

`mobile/src/input/coords.test.ts`:
```ts
import { normalizeCoords } from "./coords";

test("center of a matched-aspect rect maps to (0.5,0.5)", () => {
  const r = normalizeCoords({ x: 100, y: 100 }, { width: 200, height: 200 }, { w: 100, h: 100 });
  expect(r.x).toBeCloseTo(0.5);
  expect(r.y).toBeCloseTo(0.5);
});

test("letterboxed portrait video in a wide rect ignores the side bars", () => {
  // rect 400x200, content 100x200 => scale 1 => contentW=100, offsetX=150
  const left = normalizeCoords({ x: 150, y: 100 }, { width: 400, height: 200 }, { w: 100, h: 200 });
  expect(left.x).toBeCloseTo(0);   // left edge of the content
  const right = normalizeCoords({ x: 250, y: 100 }, { width: 400, height: 200 }, { w: 100, h: 200 });
  expect(right.x).toBeCloseTo(1);
});

test("coords clamp to [0,1]", () => {
  const r = normalizeCoords({ x: -50, y: 9999 }, { width: 200, height: 200 }, { w: 100, h: 100 });
  expect(r.x).toBe(0);
  expect(r.y).toBe(1);
});
```

- [ ] **Step 2: Run to verify failure**

Run: `cd mobile && npm test -- coords`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement `coords.ts`**

```ts
export function normalizeCoords(
  pointer: { x: number; y: number },
  rect: { width: number; height: number },
  content: { w: number; h: number },
): { x: number; y: number } {
  let contentW = rect.width, contentH = rect.height, offsetX = 0, offsetY = 0;
  if (content.w && content.h) {
    const scale = Math.min(rect.width / content.w, rect.height / content.h);
    contentW = content.w * scale;
    contentH = content.h * scale;
    offsetX = (rect.width - contentW) / 2;
    offsetY = (rect.height - contentH) / 2;
  }
  const clamp = (v: number) => Math.max(0, Math.min(1, v));
  return {
    x: clamp((pointer.x - offsetX) / contentW),
    y: clamp((pointer.y - offsetY) / contentH),
  };
}
```

- [ ] **Step 4: Run to verify pass**

Run: `cd mobile && npm test -- coords`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add mobile/src/input
git commit -m "feat(mobile): normalizeCoords letterbox math with tests"
```

---

### Task 3: Pure quality logic — tier stepping (TDD)

**Files:**
- Create: `mobile/src/quality/tiers.ts`
- Test: `mobile/src/quality/tiers.test.ts`

**Interfaces:**
- Produces:
  - `TIER_ORDER: readonly ["480","720","1080","1440"]`
  - `stepTier(current: string, dir: -1 | 1): string` — clamped step within `TIER_ORDER`.
  - `shouldDowngrade(loss: number, rttMs: number): boolean` — `loss > 0.08 || rttMs > 400`.
  - `nextBadStreak(prev: number, congested: boolean): number` — increments on congestion else resets to 0.
  - `DOWNGRADE_STREAK = 3` — samples of sustained congestion before a step.

- [ ] **Step 1: Write the failing tests**

`mobile/src/quality/tiers.test.ts`:
```ts
import { TIER_ORDER, stepTier, shouldDowngrade, nextBadStreak, DOWNGRADE_STREAK } from "./tiers";

test("stepTier clamps at both ends", () => {
  expect(stepTier("480", -1)).toBe("480");
  expect(stepTier("1440", 1)).toBe("1440");
  expect(stepTier("720", -1)).toBe("480");
  expect(stepTier("720", 1)).toBe("1080");
});

test("shouldDowngrade triggers on loss or rtt", () => {
  expect(shouldDowngrade(0.09, 100)).toBe(true);
  expect(shouldDowngrade(0.0, 500)).toBe(true);
  expect(shouldDowngrade(0.02, 100)).toBe(false);
});

test("bad streak accumulates then resets", () => {
  let s = 0;
  s = nextBadStreak(s, true); s = nextBadStreak(s, true); s = nextBadStreak(s, true);
  expect(s).toBe(DOWNGRADE_STREAK);
  expect(nextBadStreak(s, false)).toBe(0);
});
```

- [ ] **Step 2: Run to verify failure**

Run: `cd mobile && npm test -- tiers`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement `tiers.ts`**

```ts
export const TIER_ORDER = ["480", "720", "1080", "1440"] as const;
export const DOWNGRADE_STREAK = 3;

export function stepTier(current: string, dir: -1 | 1): string {
  const i = TIER_ORDER.indexOf(current as (typeof TIER_ORDER)[number]);
  const base = i === -1 ? 1 : i; // default to "720" if unknown
  const j = Math.max(0, Math.min(TIER_ORDER.length - 1, base + dir));
  return TIER_ORDER[j];
}

export function shouldDowngrade(loss: number, rttMs: number): boolean {
  return loss > 0.08 || rttMs > 400;
}

export function nextBadStreak(prev: number, congested: boolean): number {
  return congested ? prev + 1 : 0;
}
```

- [ ] **Step 4: Run to verify pass**

Run: `cd mobile && npm test -- tiers`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add mobile/src/quality
git commit -m "feat(mobile): pure quality-tier stepping logic with tests"
```

---

### Task 4: Server URL building + API client (TDD for URL math)

**Files:**
- Create: `mobile/src/api/urls.ts`
- Create: `mobile/src/api/client.ts`
- Test: `mobile/src/api/urls.test.ts`

**Interfaces:**
- Produces (`urls.ts`):
  - `normalizeBase(input: string): string` — trims, strips a trailing `/`, throws `Error("invalid url")` if it does not match `^https?://\S+`.
  - `wsUrl(base: string, path: string): string` — maps `http→ws`, `https→wss`, joins path.
  - `httpUrl(base: string, path: string): string` — joins base + path.
- Produces (`client.ts`): `makeClient(base: string)` returning
  `{ instances(): Promise<Instance[]>, select(serial): Promise<SelectResp>, keyframe(serial): Promise<void>, setQuality(serial, tier): Promise<void>, previewUrl(serial): string, inputWsUrl(): string }`.
  `Instance = { id: string; serial: string; title: string; w?: number; h?: number }` (server returns a list; `title`/`serial` derived — see Step 5).
  `SelectResp = { ok: boolean; serial: string; name: string; w: number; h: number; whep_url: string; stun_url: string }`.

- [ ] **Step 1: Write the failing tests**

`mobile/src/api/urls.test.ts`:
```ts
import { normalizeBase, wsUrl, httpUrl } from "./urls";

test("normalizeBase strips trailing slash and validates", () => {
  expect(normalizeBase("http://100.86.14.2:8080/")).toBe("http://100.86.14.2:8080");
  expect(() => normalizeBase("ftp://x")).toThrow();
  expect(() => normalizeBase("not a url")).toThrow();
});

test("wsUrl swaps scheme", () => {
  expect(wsUrl("http://h:8080", "/input")).toBe("ws://h:8080/input");
  expect(wsUrl("https://h", "/input")).toBe("wss://h/input");
});

test("httpUrl joins", () => {
  expect(httpUrl("http://h:8080", "/instances")).toBe("http://h:8080/instances");
});
```

- [ ] **Step 2: Run to verify failure**

Run: `cd mobile && npm test -- urls`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement `urls.ts`**

```ts
export function normalizeBase(input: string): string {
  const t = input.trim().replace(/\/+$/, "");
  if (!/^https?:\/\/\S+/.test(t)) throw new Error("invalid url");
  return t;
}
export function httpUrl(base: string, path: string): string {
  return base + path;
}
export function wsUrl(base: string, path: string): string {
  return base.replace(/^http/, (m) => (m === "http" ? "ws" : m)).replace(/^https/, "wss").replace(/^http:/, "ws:") + path;
}
```
Note: implement `wsUrl` simply and correctly — `base.startsWith("https") ? "wss" + base.slice(5) : "ws" + base.slice(4)` then `+ path`. Use whichever form passes the tests; the test is the contract.

- [ ] **Step 4: Run to verify pass**

Run: `cd mobile && npm test -- urls`
Expected: PASS (3 tests).

- [ ] **Step 5: Implement `client.ts` (no unit test — thin fetch wrapper, covered by device smoke test)**

```ts
import { httpUrl, wsUrl } from "./urls";

export type Instance = { id: string; serial: string; title: string; w?: number; h?: number };
export type SelectResp = {
  ok: boolean; serial: string; name: string; w: number; h: number;
  whep_url: string; stun_url: string;
};

function serialOf(raw: any): string {
  const id: string = raw.id ?? raw.serial ?? "";
  return raw.serial ?? (id.startsWith("adb:") ? id.slice(4) : id);
}

export function makeClient(base: string) {
  return {
    async instances(): Promise<Instance[]> {
      const r = await fetch(httpUrl(base, "/instances"));
      const list = await r.json();
      return (list as any[]).map((d) => ({
        id: d.id ?? d.serial,
        serial: serialOf(d),
        title: d.title ?? d.name ?? serialOf(d),
        w: d.w, h: d.h,
      }));
    },
    async select(serial: string): Promise<SelectResp> {
      const r = await fetch(httpUrl(base, `/instances/${serial}/select`), { method: "POST" });
      if (!r.ok) throw new Error(`select ${r.status}`);
      return r.json();
    },
    async keyframe(serial: string): Promise<void> {
      try { await fetch(httpUrl(base, `/instances/${serial}/keyframe`), { method: "POST" }); } catch {}
    },
    async setQuality(serial: string, tier: string): Promise<void> {
      try {
        await fetch(httpUrl(base, `/instances/${serial}/quality`), {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ tier }),
        });
      } catch {}
    },
    previewUrl(serial: string): string {
      return httpUrl(base, `/instances/${serial}/preview?t=${Date.now()}`);
    },
    inputWsUrl(): string { return wsUrl(base, "/input"); },
  };
}
```

- [ ] **Step 6: Commit**

```bash
git add mobile/src/api
git commit -m "feat(mobile): server URL helpers (tested) and API client"
```

---

### Task 5: ServerContext — persisted base URL + provider

**Files:**
- Create: `mobile/src/api/ServerContext.tsx`
- Test: `mobile/src/api/ServerContext.test.tsx`

**Interfaces:**
- Consumes: `makeClient`, `normalizeBase` from Task 4.
- Produces: `ServerProvider` component and `useServer()` hook returning
  `{ base: string | null; client: ReturnType<typeof makeClient> | null; setBase(url: string): Promise<void>; ready: boolean }`.
  `setBase` validates via `normalizeBase`, persists to AsyncStorage key `wc_base`, and rebuilds the client. On mount it loads the persisted base (`ready` flips true after the load attempt).

- [ ] **Step 1: Write the failing test (mock AsyncStorage)**

`mobile/src/api/ServerContext.test.tsx`:
```tsx
import React from "react";
import { render, waitFor, act } from "@testing-library/react-native";
import { Text } from "react-native";
import AsyncStorage from "@react-native-async-storage/async-storage";
import { ServerProvider, useServer } from "./ServerContext";

jest.mock("@react-native-async-storage/async-storage", () =>
  require("@react-native-async-storage/async-storage/jest/async-storage-mock"));

function Probe() {
  const { base, ready, setBase } = useServer();
  return <Text>{ready ? `ready:${base ?? "none"}` : "loading"}</Text>;
}

test("loads persisted base and setBase persists", async () => {
  await AsyncStorage.setItem("wc_base", "http://h:8080");
  const { getByText } = render(<ServerProvider><Probe /></ServerProvider>);
  await waitFor(() => getByText("ready:http://h:8080"));
});
```

- [ ] **Step 2: Run to verify failure**

Run: `cd mobile && npm test -- ServerContext`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement `ServerContext.tsx`**

```tsx
import React, { createContext, useContext, useEffect, useMemo, useState, useCallback } from "react";
import AsyncStorage from "@react-native-async-storage/async-storage";
import { makeClient } from "./client";
import { normalizeBase } from "./urls";

type Ctx = {
  base: string | null;
  client: ReturnType<typeof makeClient> | null;
  setBase: (url: string) => Promise<void>;
  ready: boolean;
};
const ServerCtx = createContext<Ctx | null>(null);
const KEY = "wc_base";

export function ServerProvider({ children }: { children: React.ReactNode }) {
  const [base, setBaseState] = useState<string | null>(null);
  const [ready, setReady] = useState(false);

  useEffect(() => {
    AsyncStorage.getItem(KEY)
      .then((v) => { if (v) setBaseState(v); })
      .finally(() => setReady(true));
  }, []);

  const setBase = useCallback(async (url: string) => {
    const norm = normalizeBase(url);
    await AsyncStorage.setItem(KEY, norm);
    setBaseState(norm);
  }, []);

  const client = useMemo(() => (base ? makeClient(base) : null), [base]);
  return <ServerCtx.Provider value={{ base, client, setBase, ready }}>{children}</ServerCtx.Provider>;
}

export function useServer(): Ctx {
  const c = useContext(ServerCtx);
  if (!c) throw new Error("useServer outside ServerProvider");
  return c;
}
```

- [ ] **Step 4: Run to verify pass**

Run: `cd mobile && npm test -- ServerContext`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add mobile/src/api/ServerContext.tsx mobile/src/api/ServerContext.test.tsx
git commit -m "feat(mobile): ServerContext with persisted base URL"
```

---

### Task 6: Shared UI primitives (Modernist)

**Files:**
- Create: `mobile/src/components/Button.tsx`
- Create: `mobile/src/components/NetDot.tsx`
- Create: `mobile/src/components/IconButton.tsx`
- Test: `mobile/src/components/NetDot.test.tsx`

**Interfaces:**
- Consumes: `theme` (Task 1).
- Produces:
  - `Button({ label, onPress, variant?: "primary" | "secondary", loading?, disabled? })` — flush-left label, zero radius, primary = sage bg / `#141312` text.
  - `IconButton({ children, onPress, active?, label })` — 48×48, zero radius, active = sage fill.
  - `NetDot({ state })` where `state ∈ "connected"|"connecting"|"disconnected"`; renders a 9px square in the mapped color; `testID="net-dot"` and `accessibilityValue.text = state`.

- [ ] **Step 1: Write the failing test**

`mobile/src/components/NetDot.test.tsx`:
```tsx
import React from "react";
import { render } from "@testing-library/react-native";
import { NetDot } from "./NetDot";
import { theme } from "../theme/tokens";

test("NetDot colors by state", () => {
  const { getByTestId, rerender } = render(<NetDot state="connected" />);
  expect(getByTestId("net-dot").props.style.backgroundColor).toBe(theme.color.netConnected);
  rerender(<NetDot state="disconnected" />);
  expect(getByTestId("net-dot").props.style.backgroundColor).toBe(theme.color.netDisconnected);
});
```

- [ ] **Step 2: Run to verify failure**

Run: `cd mobile && npm test -- NetDot`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement the three components**

`NetDot.tsx`:
```tsx
import React from "react";
import { View } from "react-native";
import { theme } from "../theme/tokens";

const MAP = {
  connected: theme.color.netConnected,
  connecting: theme.color.netConnecting,
  disconnected: theme.color.netDisconnected,
} as const;

export function NetDot({ state }: { state: keyof typeof MAP }) {
  return <View testID="net-dot"
    accessibilityValue={{ text: state }}
    style={{ width: 9, height: 9, backgroundColor: MAP[state] }} />;
}
```
`Button.tsx`:
```tsx
import React from "react";
import { Pressable, Text, ActivityIndicator, View } from "react-native";
import { theme } from "../theme/tokens";

export function Button({ label, onPress, variant = "primary", loading, disabled }:
  { label: string; onPress: () => void; variant?: "primary" | "secondary"; loading?: boolean; disabled?: boolean }) {
  const primary = variant === "primary";
  return (
    <Pressable onPress={onPress} disabled={disabled || loading}
      style={{
        height: 54, flexDirection: "row", alignItems: "center", gap: 10,
        paddingHorizontal: 16, borderRadius: theme.radius,
        backgroundColor: primary ? theme.color.accent : "transparent",
        borderWidth: primary ? 0 : theme.rule, borderColor: "rgba(243,242,242,0.3)",
        opacity: disabled ? 0.45 : 1,
      }}>
      {loading ? <ActivityIndicator color={primary ? "#141312" : theme.color.text} /> : null}
      <Text style={{ fontFamily: theme.font.bold, fontSize: 15,
        color: primary ? "#141312" : theme.color.text }}>{label}</Text>
    </Pressable>
  );
}
```
`IconButton.tsx`:
```tsx
import React from "react";
import { Pressable } from "react-native";
import { theme } from "../theme/tokens";

export function IconButton({ children, onPress, active, label }:
  { children: React.ReactNode; onPress: () => void; active?: boolean; label: string }) {
  return (
    <Pressable onPress={onPress} accessibilityLabel={label}
      style={{ width: 48, height: 48, alignItems: "center", justifyContent: "center",
        backgroundColor: active ? theme.color.accent : "transparent", borderRadius: theme.radius }}>
      {children}
    </Pressable>
  );
}
```

- [ ] **Step 4: Run to verify pass**

Run: `cd mobile && npm test -- NetDot`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add mobile/src/components
git commit -m "feat(mobile): Modernist Button, IconButton, NetDot primitives"
```

---

### Task 7: Navigation shell + font loading + ServerSetup screen

**Files:**
- Create: `mobile/src/navigation/Root.tsx`
- Create: `mobile/src/screens/ServerSetup.tsx`
- Modify: `mobile/App.tsx`
- Test: `mobile/src/screens/ServerSetup.test.tsx`

**Interfaces:**
- Consumes: `ServerProvider`/`useServer` (Task 5), `Button` (Task 6), `theme` (Task 1).
- Produces: `RootNavigator` with a native-stack: routes `"ServerSetup"`, `"InstanceList"`, `"Stream"` (param `{ serial: string; title: string }`). `App.tsx` wraps everything in `GestureHandlerRootView` → font loading gate → `ServerProvider` → `NavigationContainer` → `RootNavigator`. Initial route = `"InstanceList"` if a base is persisted, else `"ServerSetup"`.

- [ ] **Step 1: Write the failing test for ServerSetup validation**

`mobile/src/screens/ServerSetup.test.tsx`:
```tsx
import React from "react";
import { render, fireEvent, waitFor } from "@testing-library/react-native";
import AsyncStorage from "@react-native-async-storage/async-storage";
import { ServerProvider } from "../api/ServerContext";
import { ServerSetup } from "./ServerSetup";

jest.mock("@react-native-async-storage/async-storage", () =>
  require("@react-native-async-storage/async-storage/jest/async-storage-mock"));

test("rejects a malformed URL with an inline error", async () => {
  const nav = { replace: jest.fn() } as any;
  const { getByPlaceholderText, getByText } = render(
    <ServerProvider><ServerSetup navigation={nav} /></ServerProvider>);
  fireEvent.changeText(getByPlaceholderText(/http:\/\//), "not a url");
  fireEvent.press(getByText("Connect"));
  await waitFor(() => getByText(/Enter a full URL/i));
  expect(nav.replace).not.toHaveBeenCalled();
});
```

- [ ] **Step 2: Run to verify failure**

Run: `cd mobile && npm test -- ServerSetup`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement `ServerSetup.tsx`**

```tsx
import React, { useState } from "react";
import { View, Text, TextInput } from "react-native";
import { theme } from "../theme/tokens";
import { Button } from "../components/Button";
import { useServer } from "../api/ServerContext";

export function ServerSetup({ navigation }: { navigation: any }) {
  const { setBase } = useServer();
  const [url, setUrl] = useState("http://100.86.14.2:8080");
  const [error, setError] = useState("");
  const [hint, setHint] = useState("");
  const [connecting, setConnecting] = useState(false);

  const connect = async () => {
    if (connecting) return;
    if (!/^https?:\/\/\S+/.test(url.trim())) {
      setError("Enter a full URL");
      setHint("Include the scheme and port, e.g. http://100.86.14.2:8080");
      return;
    }
    setConnecting(true); setError("");
    try {
      await setBase(url);
      // Probe reachability so an unreachable host surfaces here, not on the list.
      const r = await fetch(url.trim().replace(/\/+$/, "") + "/instances");
      if (!r.ok) throw new Error("bad status");
      navigation.replace("InstanceList");
    } catch {
      setError("Can't reach server");
      setHint("No response. Confirm the host is on the tailnet.");
    } finally {
      setConnecting(false);
    }
  };

  return (
    <View style={{ flex: 1, backgroundColor: theme.color.bg, justifyContent: "center", padding: 24 }}>
      <Text style={{ fontFamily: theme.font.bold, fontSize: 11, letterSpacing: 1.5, color: theme.color.accent, marginBottom: 14 }}>EMUCTRL</Text>
      <Text style={{ fontFamily: theme.font.bold, fontSize: 34, color: theme.color.text, marginBottom: 10 }}>Connect to{"\n"}your server</Text>
      <Text style={{ fontFamily: theme.font.regular, fontSize: 13, color: theme.color.textMuted, marginBottom: 26 }}>Enter the base URL of the host on your private network.</Text>
      <View style={{ height: theme.rule, backgroundColor: "rgba(243,242,242,0.35)", marginBottom: 22 }} />
      <Text style={{ fontFamily: theme.font.regular, fontSize: 11, letterSpacing: 1, color: theme.color.textMuted, marginBottom: 8 }}>SERVER BASE URL</Text>
      <TextInput value={url} onChangeText={(t) => { setUrl(t); setError(""); }}
        placeholder="http://100.86.14.2:8080" placeholderTextColor="rgba(243,242,242,0.35)"
        autoCapitalize="none" autoCorrect={false} spellCheck={false}
        style={{ height: 54, paddingHorizontal: 14, fontFamily: theme.font.regular, fontSize: 16,
          color: theme.color.text, backgroundColor: theme.color.surface, borderWidth: theme.rule,
          borderColor: "rgba(243,242,242,0.3)", borderRadius: theme.radius }} />
      {error ? (
        <View style={{ marginTop: 12, padding: 12, backgroundColor: "rgba(255,86,60,0.12)", borderLeftWidth: theme.rule, borderLeftColor: theme.color.error }}>
          <Text style={{ fontFamily: theme.font.bold, fontSize: 13, color: theme.color.error }}>{error}</Text>
          <Text style={{ fontFamily: theme.font.regular, fontSize: 11.5, color: theme.color.textMuted, marginTop: 3 }}>{hint}</Text>
        </View>
      ) : null}
      <View style={{ marginTop: 18 }}>
        <Button label={connecting ? "Connecting…" : "Connect"} onPress={connect} loading={connecting} />
      </View>
    </View>
  );
}
```

- [ ] **Step 4: Implement `Root.tsx` and wire `App.tsx`**

`Root.tsx`:
```tsx
import React from "react";
import { createNativeStackNavigator } from "@react-navigation/native-stack";
import { ServerSetup } from "../screens/ServerSetup";
import { InstanceList } from "../screens/InstanceList";
import { Stream } from "../screens/Stream";
import { useServer } from "../api/ServerContext";

const Stack = createNativeStackNavigator();

export function RootNavigator() {
  const { base } = useServer();
  return (
    <Stack.Navigator screenOptions={{ headerShown: false }}
      initialRouteName={base ? "InstanceList" : "ServerSetup"}>
      <Stack.Screen name="ServerSetup" component={ServerSetup} />
      <Stack.Screen name="InstanceList" component={InstanceList} />
      <Stack.Screen name="Stream" component={Stream} />
    </Stack.Navigator>
  );
}
```
`App.tsx`:
```tsx
import "react-native-gesture-handler";
import React from "react";
import { GestureHandlerRootView } from "react-native-gesture-handler";
import { NavigationContainer } from "@react-navigation/native";
import { useFonts, Archivo_400Regular, Archivo_600SemiBold, Archivo_800ExtraBold } from "@expo-google-fonts/archivo";
import { View } from "react-native";
import { ServerProvider, useServer } from "./src/api/ServerContext";
import { RootNavigator } from "./src/navigation/Root";
import { theme } from "./src/theme/tokens";

function Gate() {
  const { ready } = useServer();
  if (!ready) return <View style={{ flex: 1, backgroundColor: theme.color.bg }} />;
  return <NavigationContainer><RootNavigator /></NavigationContainer>;
}

export default function App() {
  const [fontsLoaded] = useFonts({ Archivo_400Regular, Archivo_600SemiBold, Archivo_800ExtraBold });
  if (!fontsLoaded) return <View style={{ flex: 1, backgroundColor: theme.color.bg }} />;
  return (
    <GestureHandlerRootView style={{ flex: 1 }}>
      <ServerProvider><Gate /></ServerProvider>
    </GestureHandlerRootView>
  );
}
```
Note: `InstanceList` and `Stream` are created in Tasks 8 and 9. To keep this task's tests green in isolation, create temporary stub files `mobile/src/screens/InstanceList.tsx` and `mobile/src/screens/Stream.tsx` each exporting a component that renders an empty `View` (they are fully implemented in the next tasks).

- [ ] **Step 5: Run to verify ServerSetup test passes**

Run: `cd mobile && npm test -- ServerSetup`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add mobile/src/navigation mobile/src/screens/ServerSetup.tsx mobile/src/screens/ServerSetup.test.tsx mobile/App.tsx mobile/src/screens/InstanceList.tsx mobile/src/screens/Stream.tsx
git commit -m "feat(mobile): navigation shell, font gate, ServerSetup screen"
```

---

### Task 8: InstanceList screen

**Files:**
- Modify: `mobile/src/screens/InstanceList.tsx` (replace stub)
- Create: `mobile/src/components/InstanceCard.tsx`
- Test: `mobile/src/screens/InstanceList.test.tsx`

**Interfaces:**
- Consumes: `useServer` (Task 5), `client.instances/previewUrl/keyframe` (Task 4), `theme`, `Button`.
- Produces: `InstanceList({ navigation })`. On focus, fetches `/instances`, renders a `FlatList` of `InstanceCard`. Poll every 60s while focused; pull-to-refresh. Tap card → `client.keyframe(serial)` then `navigation.navigate("Stream", { serial, title })`. Header shows `host:port · N online` + refresh button. Empty state when list is empty.
- `InstanceCard({ instance, active, previewUri, onPress })` — 16:9 preview `Image`, LIVE/IDLE badge, title + meta; active = sage border + title.

- [ ] **Step 1: Write the failing test (mock client via ServerContext)**

`mobile/src/screens/InstanceList.test.tsx`:
```tsx
import React from "react";
import { render, fireEvent, waitFor } from "@testing-library/react-native";
import { InstanceList } from "./InstanceList";
import * as SC from "../api/ServerContext";

test("renders instances and navigates on tap", async () => {
  const client = {
    instances: jest.fn().mockResolvedValue([
      { id: "adb:A", serial: "A", title: "LDP-01" },
      { id: "adb:B", serial: "B", title: "LDP-02" },
    ]),
    previewUrl: (s: string) => `http://h/preview/${s}`,
    keyframe: jest.fn().mockResolvedValue(undefined),
  };
  jest.spyOn(SC, "useServer").mockReturnValue({ base: "http://h", client, setBase: jest.fn(), ready: true } as any);
  const nav = { navigate: jest.fn() } as any;
  const { getByText } = render(<InstanceList navigation={nav} />);
  await waitFor(() => getByText("LDP-01"));
  fireEvent.press(getByText("LDP-02"));
  await waitFor(() => expect(nav.navigate).toHaveBeenCalledWith("Stream", { serial: "B", title: "LDP-02" }));
  expect(client.keyframe).toHaveBeenCalledWith("B");
});
```

- [ ] **Step 2: Run to verify failure**

Run: `cd mobile && npm test -- InstanceList`
Expected: FAIL — stub renders nothing / no such text.

- [ ] **Step 3: Implement `InstanceCard.tsx`**

```tsx
import React from "react";
import { Pressable, View, Text, Image } from "react-native";
import { theme } from "../theme/tokens";
import type { Instance } from "../api/client";

export function InstanceCard({ instance, active, previewUri, onPress }:
  { instance: Instance; active: boolean; previewUri: string; onPress: () => void }) {
  return (
    <Pressable onPress={onPress}
      style={{ backgroundColor: theme.color.surface, borderWidth: theme.rule,
        borderColor: active ? theme.color.accent : "rgba(243,242,242,0.22)", marginBottom: 14 }}>
      <View style={{ aspectRatio: 16 / 9, backgroundColor: theme.color.deep }}>
        <Image source={{ uri: previewUri }} resizeMode="cover" style={{ width: "100%", height: "100%" }} />
        <View style={{ position: "absolute", top: 0, left: 0, paddingHorizontal: 7, paddingVertical: 4,
          backgroundColor: active ? theme.color.accent : "rgba(12,11,11,0.7)" }}>
          <Text style={{ fontFamily: theme.font.bold, fontSize: 9, letterSpacing: 1.2,
            color: active ? "#141312" : "rgba(243,242,242,0.7)" }}>{active ? "LIVE" : "IDLE"}</Text>
        </View>
      </View>
      <View style={{ padding: 10 }}>
        <Text style={{ fontFamily: theme.font.bold, fontSize: 13, color: active ? theme.color.accent : theme.color.text }}>{instance.title}</Text>
        {instance.w && instance.h ? (
          <Text style={{ fontFamily: theme.font.regular, fontSize: 10.5, color: theme.color.textMuted, marginTop: 5 }}>{instance.w}×{instance.h}</Text>
        ) : null}
      </View>
    </Pressable>
  );
}
```

- [ ] **Step 4: Implement `InstanceList.tsx`**

```tsx
import React, { useCallback, useEffect, useState } from "react";
import { View, Text, FlatList, Pressable } from "react-native";
import { useServer } from "../api/ServerContext";
import { theme } from "../theme/tokens";
import { InstanceCard } from "../components/InstanceCard";
import type { Instance } from "../api/client";

export function InstanceList({ navigation }: { navigation: any }) {
  const { client, base } = useServer();
  const [items, setItems] = useState<Instance[]>([]);
  const [refreshing, setRefreshing] = useState(false);

  const load = useCallback(async () => {
    if (!client) return;
    try { setItems(await client.instances()); } catch {}
  }, [client]);

  useEffect(() => {
    load();
    const id = setInterval(load, 60000);
    return () => clearInterval(id);
  }, [load]);

  const onRefresh = async () => { setRefreshing(true); await load(); setRefreshing(false); };
  const open = (inst: Instance) => {
    client?.keyframe(inst.serial);
    navigation.navigate("Stream", { serial: inst.serial, title: inst.title });
  };

  const host = (base ?? "").replace(/^https?:\/\//, "");
  return (
    <View style={{ flex: 1, backgroundColor: theme.color.bg }}>
      <View style={{ flexDirection: "row", alignItems: "center", paddingHorizontal: 18, paddingTop: 48, paddingBottom: 14,
        borderBottomWidth: theme.rule, borderBottomColor: "rgba(243,242,242,0.35)" }}>
        <View style={{ flex: 1 }}>
          <Text style={{ fontFamily: theme.font.bold, fontSize: 24, color: theme.color.text }}>Windows</Text>
          <Text style={{ fontFamily: theme.font.regular, fontSize: 11, color: theme.color.textMuted, marginTop: 4 }}>{host} · {items.length} online</Text>
        </View>
        <Pressable onPress={onRefresh} accessibilityLabel="Refresh"
          style={{ width: 44, height: 44, alignItems: "center", justifyContent: "center",
            borderWidth: theme.rule, borderColor: "rgba(243,242,242,0.3)" }}>
          <Text style={{ color: theme.color.text, fontFamily: theme.font.bold }}>⟳</Text>
        </Pressable>
      </View>
      {items.length === 0 ? (
        <View style={{ flex: 1, justifyContent: "center", padding: 24 }}>
          <Text style={{ fontFamily: theme.font.bold, fontSize: 20, color: theme.color.text, marginBottom: 8 }}>No windows found</Text>
          <Text style={{ fontFamily: theme.font.regular, fontSize: 13, color: theme.color.textMuted }}>Start an emulator instance, then refresh.</Text>
        </View>
      ) : (
        <FlatList data={items} keyExtractor={(i) => i.id}
          refreshing={refreshing} onRefresh={onRefresh}
          contentContainerStyle={{ padding: 18 }}
          renderItem={({ item }) => (
            <InstanceCard instance={item} active={false}
              previewUri={client!.previewUrl(item.serial)} onPress={() => open(item)} />
          )} />
      )}
    </View>
  );
}
```
Note: the refresh glyph is a text `⟳` placeholder — acceptable; swap for an SVG icon later if desired. This keeps the task dependency-free.

- [ ] **Step 5: Run to verify pass**

Run: `cd mobile && npm test -- InstanceList`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add mobile/src/screens/InstanceList.tsx mobile/src/screens/InstanceList.test.tsx mobile/src/components/InstanceCard.tsx
git commit -m "feat(mobile): InstanceList screen with preview cards"
```

---

### Task 9: WHEP negotiation module

**Files:**
- Create: `mobile/src/webrtc/whep.ts`
- Test: `mobile/src/webrtc/whep.test.ts`

**Interfaces:**
- Consumes: `react-native-webrtc` globals.
- Produces: `connectWhep(opts): WhepSession` where
  `opts = { whepUrl: string; stunUrl: string; onStream(stream): void; onState(s: "connecting"|"connected"|"failed"): void; fetchImpl?: typeof fetch; RTCImpl?: typeof RTCPeerConnection }`.
  `WhepSession = { close(): void; pc: RTCPeerConnection }`.
  Internally: create PC with `iceServers:[{urls:stunUrl}]`, `addTransceiver("video",{direction:"recvonly"})`, `createOffer`→`setLocalDescription`→**wait for ICE gathering (srflx fast-path else complete else cap)**→POST offer SDP→`setRemoteDescription(answer)`. `ontrack`→`onStream`. `RTCImpl`/`fetchImpl` are injectable for testing.
  Also exports `waitForIceGatheringComplete(pc, capMs?): Promise<void>` (srflx fast-path).

- [ ] **Step 1: Write the failing test (fake PC + fetch)**

`mobile/src/webrtc/whep.test.ts`:
```ts
import { connectWhep } from "./whep";

function fakePc() {
  const listeners: Record<string, Function[]> = {};
  return {
    iceGatheringState: "complete",
    localDescription: { sdp: "OFFER" },
    addEventListener: (k: string, f: Function) => { (listeners[k] ||= []).push(f); },
    removeEventListener: () => {},
    addTransceiver: () => ({ receiver: {} }),
    createOffer: async () => ({ type: "offer", sdp: "OFFER" }),
    setLocalDescription: async () => {},
    setRemoteDescription: jest.fn(async () => {}),
    close: jest.fn(),
    _fire: (k: string, e: any) => (listeners[k] || []).forEach((f) => f(e)),
  } as any;
}

test("posts the offer SDP and applies the answer", async () => {
  const pc = fakePc();
  const fetchImpl = jest.fn(async () => ({ ok: true, text: async () => "ANSWER" })) as any;
  connectWhep({
    whepUrl: "http://h/whep", stunUrl: "stun:h:3478",
    onStream: () => {}, onState: () => {},
    RTCImpl: function () { return pc; } as any, fetchImpl,
  });
  await new Promise((r) => setTimeout(r, 0));
  expect(fetchImpl).toHaveBeenCalledWith("http://h/whep", expect.objectContaining({ method: "POST", body: "OFFER" }));
  expect(pc.setRemoteDescription).toHaveBeenCalled();
});
```

- [ ] **Step 2: Run to verify failure**

Run: `cd mobile && npm test -- whep`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement `whep.ts`**

```ts
import { RTCPeerConnection as RN_RTC } from "react-native-webrtc";

export function waitForIceGatheringComplete(pc: any, capMs = 4000): Promise<void> {
  if (pc.iceGatheringState === "complete") return Promise.resolve();
  return new Promise((resolve) => {
    let done = false;
    const finish = () => {
      if (done) return;
      done = true;
      pc.removeEventListener("icegatheringstatechange", check);
      pc.removeEventListener("icecandidate", onCand);
      resolve();
    };
    const check = () => { if (pc.iceGatheringState === "complete") finish(); };
    const onCand = (e: any) => {
      if (e.candidate && e.candidate.candidate && e.candidate.candidate.includes("typ srflx")) finish();
    };
    pc.addEventListener("icegatheringstatechange", check);
    pc.addEventListener("icecandidate", onCand);
    setTimeout(finish, capMs);
  });
}

type Opts = {
  whepUrl: string; stunUrl: string;
  onStream: (s: any) => void;
  onState: (s: "connecting" | "connected" | "failed") => void;
  fetchImpl?: typeof fetch; RTCImpl?: any;
};

export function connectWhep(opts: Opts) {
  const RTC = opts.RTCImpl || RN_RTC;
  const doFetch = opts.fetchImpl || fetch;
  const pc: any = new RTC({ iceServers: opts.stunUrl ? [{ urls: opts.stunUrl }] : [] });
  opts.onState("connecting");

  pc.addEventListener?.("track", (e: any) => {
    opts.onStream(e.streams ? e.streams[0] : e.stream);
    opts.onState("connected");
  });
  pc.ontrack = (e: any) => { opts.onStream(e.streams ? e.streams[0] : e.stream); opts.onState("connected"); };
  pc.addEventListener?.("iceconnectionstatechange", () => {
    const s = pc.iceConnectionState;
    if (s === "failed" || s === "closed") opts.onState("failed");
    else if (s === "connected" || s === "completed") opts.onState("connected");
  });

  (async () => {
    try {
      pc.addTransceiver("video", { direction: "recvonly" });
      const offer = await pc.createOffer();
      await pc.setLocalDescription(offer);
      await waitForIceGatheringComplete(pc);
      const r = await doFetch(opts.whepUrl, {
        method: "POST",
        headers: { "Content-Type": "application/sdp" },
        body: pc.localDescription.sdp,
      } as any);
      if (!r || !(r as any).ok) { opts.onState("failed"); return; }
      const sdp = await (r as any).text();
      await pc.setRemoteDescription({ type: "answer", sdp });
    } catch {
      opts.onState("failed");
    }
  })();

  return { pc, close: () => { try { pc.close(); } catch {} } };
}
```
Note: also add a Jest manual mock so other tests importing `react-native-webrtc` don't crash: create `mobile/__mocks__/react-native-webrtc.js` exporting `{ RTCPeerConnection: class {}, RTCView: () => null, mediaDevices: {} }`.

- [ ] **Step 4: Run to verify pass**

Run: `cd mobile && npm test -- whep`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add mobile/src/webrtc mobile/__mocks__/react-native-webrtc.js
git commit -m "feat(mobile): WHEP negotiation module with srflx-wait, injectable for tests"
```

---

### Task 10: Input WebSocket client with reconnect

**Files:**
- Create: `mobile/src/input/inputSocket.ts`
- Test: `mobile/src/input/inputSocket.test.ts`

**Interfaces:**
- Consumes: `normalizeCoords` (Task 2).
- Produces: `makeInputSocket(url, opts?): InputSocket` where
  `InputSocket = { send(msg: object): void; close(): void; onNet(cb: (s: "good"|"bad") => void): void }`.
  `opts.WsImpl` injectable (defaults to global `WebSocket`). Exponential backoff 1s→30s on close; sends a `{type:"echo",t}` every 2s while open; ignores incoming messages except `echo` (records RTT via optional `opts.onRtt`).
  Also exports message builders: `clickMsg(x,y)`, `dragStartMsg(x,y)`, `dragMoveMsg(x,y,scroll)`, `dragEndMsg(x,y,scroll?)`, `scrollMsg(x,y,dy)`, `keyMsg(key)` — each returns the exact JSON shape the server parses.

- [ ] **Step 1: Write the failing tests for builders + backoff**

`mobile/src/input/inputSocket.test.ts`:
```ts
import { clickMsg, dragMoveMsg, scrollMsg, keyMsg, makeInputSocket } from "./inputSocket";

test("message builders match the server contract", () => {
  expect(clickMsg(0.1, 0.2)).toEqual({ type: "click", x: 0.1, y: 0.2 });
  expect(dragMoveMsg(0.3, 0.4, true)).toEqual({ type: "drag_move", x: 0.3, y: 0.4, scroll: true });
  expect(scrollMsg(0.5, 0.6, -1)).toEqual({ type: "scroll", x: 0.5, y: 0.6, dy: -1 });
  expect(keyMsg("Return")).toEqual({ type: "key", key: "Return" });
});

test("send serializes to JSON over the socket", () => {
  const sent: string[] = [];
  class FakeWs {
    readyState = 1; static OPEN = 1;
    onopen?: () => void; onclose?: () => void; onmessage?: (e: any) => void; onerror?: () => void;
    send(s: string) { sent.push(s); }
    close() {}
    constructor() { setTimeout(() => this.onopen && this.onopen(), 0); }
  }
  const sock = makeInputSocket("ws://h/input", { WsImpl: FakeWs as any });
  sock.send(clickMsg(0.5, 0.5));
  expect(JSON.parse(sent[0])).toEqual({ type: "click", x: 0.5, y: 0.5 });
  sock.close();
});
```

- [ ] **Step 2: Run to verify failure**

Run: `cd mobile && npm test -- inputSocket`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement `inputSocket.ts`**

```ts
export const clickMsg = (x: number, y: number) => ({ type: "click", x, y });
export const dragStartMsg = (x: number, y: number) => ({ type: "drag_start", x, y });
export const dragMoveMsg = (x: number, y: number, scroll: boolean) => ({ type: "drag_move", x, y, scroll });
export const dragEndMsg = (x: number, y: number, scroll?: boolean) =>
  scroll === undefined ? { type: "drag_end", x, y } : { type: "drag_end", x, y, scroll };
export const scrollMsg = (x: number, y: number, dy: number) => ({ type: "scroll", x, y, dy });
export const keyMsg = (key: string) => ({ type: "key", key });

type Opts = { WsImpl?: any; onNet?: (s: "good" | "bad") => void; onRtt?: (ms: number) => void };

export function makeInputSocket(url: string, opts: Opts = {}) {
  const Ws = opts.WsImpl || (globalThis as any).WebSocket;
  let ws: any = null;
  let retry = 1000;
  let echoTimer: any = null;
  let closed = false;
  let netCb = opts.onNet;

  const connect = () => {
    ws = new Ws(url);
    ws.onopen = () => {
      retry = 1000;
      netCb?.("good");
      clearInterval(echoTimer);
      echoTimer = setInterval(() => {
        if (ws && ws.readyState === (Ws.OPEN ?? 1)) ws.send(JSON.stringify({ type: "echo", t: Date.now() }));
      }, 2000);
    };
    ws.onmessage = (e: any) => {
      try { const m = JSON.parse(e.data); if (m.type === "echo" && m.t) opts.onRtt?.(Date.now() - m.t); } catch {}
    };
    ws.onclose = () => {
      clearInterval(echoTimer);
      if (closed) return;
      netCb?.("bad");
      setTimeout(connect, retry);
      retry = Math.min(retry * 2, 30000);
    };
    ws.onerror = () => { try { ws.close(); } catch {} };
  };
  connect();

  return {
    send(msg: object) { if (ws && ws.readyState === (Ws.OPEN ?? 1)) ws.send(JSON.stringify(msg)); },
    close() { closed = true; clearInterval(echoTimer); try { ws?.close(); } catch {} },
    onNet(cb: (s: "good" | "bad") => void) { netCb = cb; },
  };
}
```

- [ ] **Step 4: Run to verify pass**

Run: `cd mobile && npm test -- inputSocket`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add mobile/src/input/inputSocket.ts mobile/src/input/inputSocket.test.ts
git commit -m "feat(mobile): input WebSocket with reconnect + message builders"
```

---

### Task 11: Adaptive quality controller

**Files:**
- Create: `mobile/src/quality/adaptive.ts`
- Test: `mobile/src/quality/adaptive.test.ts`

**Interfaces:**
- Consumes: `shouldDowngrade`, `nextBadStreak`, `stepTier`, `DOWNGRADE_STREAK` (Task 3).
- Produces: `makeAdaptive(opts): { start(pc): void; stop(): void; pin(tier: string): void; setAuto(): void; current(): string }` where
  `opts = { serial: string; onApply(tier: string): void; sampleMs?: number; now?: () => number }`.
  Samples `pc.getStats()` every `sampleMs` (default 5000); downgrade-only; manual pin suspends adaptation for 60s; a tier change sets a 10s cooldown; calls `onApply(tier)` when it decides to change. Pure enough to test by injecting a fake `pc` whose `getStats()` returns a controllable map and a fake clock.

- [ ] **Step 1: Write the failing test**

`mobile/src/quality/adaptive.test.ts`:
```ts
import { makeAdaptive } from "./adaptive";

function statsMap(loss: number, rttMs: number) {
  const m = new Map<string, any>();
  m.set("r1", { type: "inbound-rtp", kind: "video", packetsReceived: 1000, packetsLost: Math.round(loss * 1000 / (1 - loss)) });
  m.set("p1", { type: "candidate-pair", state: "succeeded", currentRoundTripTime: rttMs / 1000 });
  return m;
}

test("downgrades after sustained congestion, once cooldown allows", async () => {
  let t = 100000;
  const applied: string[] = [];
  const pc = { getStats: async () => statsMap(0.2, 500) };
  const a = makeAdaptive({ serial: "A", onApply: (tier) => applied.push(tier), sampleMs: 1, now: () => t });
  a.start(pc as any);
  // drive 3 congested samples past the 10s change-cooldown
  for (let i = 0; i < 3; i++) { t += 6000; await (a as any)._tick(); }
  a.stop();
  expect(applied[0]).toBe("480"); // 720 -> 480
});
```
Note: expose a test hook `_tick()` on the returned object that runs one sample synchronously (awaitable), so the test does not depend on real timers.

- [ ] **Step 2: Run to verify failure**

Run: `cd mobile && npm test -- adaptive`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement `adaptive.ts`**

```ts
import { shouldDowngrade, nextBadStreak, stepTier, DOWNGRADE_STREAK } from "./tiers";

type Opts = { serial: string; onApply: (tier: string) => void; sampleMs?: number; now?: () => number };

export function makeAdaptive(opts: Opts) {
  const now = opts.now || Date.now;
  let pc: any = null;
  let timer: any = null;
  let current = "720";
  let badStreak = 0;
  let manualUntil = 0;
  let lastChange = 0;

  const apply = (tier: string) => {
    if (tier === current) return;
    current = tier;
    lastChange = now();
    opts.onApply(tier);
  };

  const tick = async () => {
    if (!pc || now() < manualUntil) return;
    if (now() - lastChange < 10000) return;
    let loss = 0, rtt = 0, seen = false;
    const stats = await pc.getStats();
    stats.forEach((r: any) => {
      if (r.type === "inbound-rtp" && r.kind === "video") {
        const recv = r.packetsReceived || 0, lost = r.packetsLost || 0;
        if (recv + lost > 0) loss = lost / (recv + lost);
        seen = true;
      }
      if (r.type === "candidate-pair" && r.state === "succeeded" && r.currentRoundTripTime != null) {
        rtt = r.currentRoundTripTime * 1000;
      }
    });
    if (!seen) return;
    if (shouldDowngrade(loss, rtt)) {
      badStreak = nextBadStreak(badStreak, true);
      if (badStreak >= DOWNGRADE_STREAK) { badStreak = 0; apply(stepTier(current, -1)); }
    } else {
      badStreak = 0;
    }
  };

  return {
    start(peer: any) { pc = peer; badStreak = 0; clearInterval(timer); timer = setInterval(tick, opts.sampleMs ?? 5000); },
    stop() { clearInterval(timer); timer = null; },
    pin(tier: string) { current = tier; manualUntil = now() + 60000; lastChange = now(); opts.onApply(tier); },
    setAuto() { manualUntil = 0; },
    current() { return current; },
    _tick: tick,
  };
}
```

- [ ] **Step 4: Run to verify pass**

Run: `cd mobile && npm test -- adaptive`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add mobile/src/quality/adaptive.ts mobile/src/quality/adaptive.test.ts
git commit -m "feat(mobile): downgrade-only adaptive quality controller"
```

---

### Task 12: Stream screen — video + gestures + overlays

**Files:**
- Modify: `mobile/src/screens/Stream.tsx` (replace stub)
- Create: `mobile/src/components/StreamToolbar.tsx`
- Create: `mobile/src/components/SettingsModal.tsx`
- Create: `mobile/src/components/SwitchDrawer.tsx`
- Create: `mobile/src/components/StatsOverlay.tsx`
- Create: `mobile/src/components/ErrorOverlay.tsx`
- Test: `mobile/src/components/SettingsModal.test.tsx`

**Interfaces:**
- Consumes: `connectWhep` (Task 9), `makeInputSocket` + builders (Task 10), `makeAdaptive` (Task 11), `normalizeCoords` (Task 2), `client.select/keyframe/setQuality` (Task 4), `useServer`, `theme`, `NetDot`, `IconButton`, `Button`, `TIER_ORDER`.
- Produces: `Stream({ route, navigation })` — reads `route.params.serial/title`. On mount: `client.select(serial)` → `connectWhep({ whepUrl, stunUrl, ... })` → render `RTCView` (letterboxed, `objectFit="contain"`); wrap in a `GestureDetector` translating tap/pan/two-finger to input messages via `normalizeCoords` using the measured `RTCView` layout + `select` response `w/h`; open `makeInputSocket`; start `makeAdaptive` (its `onApply` calls `client.setQuality`). Toolbar buttons open Settings / Switch drawer / toggle keyboard+stats / back. On WHEP `"failed"` (past the tier-switch window) show `ErrorOverlay`.
  - `StreamToolbar({ net, onSettings, onSwitch, onKeyboard, onStats, onBack, activeStates })`.
  - `SettingsModal({ tier, onPick, statsOn, onToggleStats, onClose })` — 5 tier pills + stats toggle + Done. `onPick(tier)` receives the tier string.
  - `SwitchDrawer({ instances, activeSerial, onPick, onClose })`.
  - `StatsOverlay({ lines })` — mono text block.
  - `ErrorOverlay({ onReconnect, onBack, reconnecting })`.

- [ ] **Step 1: Write the failing test for SettingsModal tier pick**

`mobile/src/components/SettingsModal.test.tsx`:
```tsx
import React from "react";
import { render, fireEvent } from "@testing-library/react-native";
import { SettingsModal } from "./SettingsModal";

test("picking a tier fires onPick with the tier string", () => {
  const onPick = jest.fn();
  const { getByText } = render(
    <SettingsModal tier="720" onPick={onPick} statsOn={false} onToggleStats={() => {}} onClose={() => {}} />);
  fireEvent.press(getByText("1080p"));
  expect(onPick).toHaveBeenCalledWith("1080");
});
```

- [ ] **Step 2: Run to verify failure**

Run: `cd mobile && npm test -- SettingsModal`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement `SettingsModal.tsx`**

```tsx
import React from "react";
import { View, Text, Pressable, Modal } from "react-native";
import { theme } from "../theme/tokens";
import { Button } from "./Button";

const PILLS: { label: string; tier: string }[] = [
  { label: "Auto", tier: "auto" }, { label: "480p", tier: "480" }, { label: "720p", tier: "720" },
  { label: "1080p", tier: "1080" }, { label: "1440p", tier: "1440" },
];

export function SettingsModal({ tier, onPick, statsOn, onToggleStats, onClose }:
  { tier: string; onPick: (t: string) => void; statsOn: boolean; onToggleStats: () => void; onClose: () => void }) {
  return (
    <Modal transparent animationType="fade" onRequestClose={onClose}>
      <Pressable onPress={onClose} style={{ flex: 1, backgroundColor: "rgba(12,11,11,0.55)", alignItems: "center", justifyContent: "center" }}>
        <Pressable onPress={() => {}} style={{ width: 420, backgroundColor: "rgba(20,19,18,0.97)", borderWidth: theme.rule, borderColor: "rgba(243,242,242,0.35)", padding: 20 }}>
          <Text style={{ fontFamily: theme.font.bold, fontSize: 16, color: theme.color.text, marginBottom: 16 }}>Settings</Text>
          <Text style={{ fontFamily: theme.font.regular, fontSize: 10, letterSpacing: 1.4, color: theme.color.textMuted, marginBottom: 9 }}>QUALITY</Text>
          <View style={{ flexDirection: "row", gap: 6, marginBottom: 20 }}>
            {PILLS.map((p) => {
              const sel = p.tier === tier;
              return (
                <Pressable key={p.tier} onPress={() => onPick(p.tier)}
                  style={{ flex: 1, height: 42, alignItems: "center", justifyContent: "center",
                    backgroundColor: sel ? theme.color.accent : "transparent",
                    borderWidth: theme.rule, borderColor: sel ? theme.color.accent : "rgba(243,242,242,0.3)" }}>
                  <Text style={{ fontFamily: theme.font.bold, fontSize: 12, color: sel ? "#141312" : "rgba(243,242,242,0.8)" }}>{p.label}</Text>
                </Pressable>
              );
            })}
          </View>
          <Pressable onPress={onToggleStats} style={{ flexDirection: "row", alignItems: "center", marginBottom: 22 }}>
            <Text style={{ flex: 1, fontFamily: theme.font.bold, fontSize: 13.5, color: theme.color.text }}>Show live stats</Text>
            <View style={{ width: 52, height: 28, borderWidth: theme.rule,
              borderColor: statsOn ? theme.color.accent : "rgba(243,242,242,0.3)",
              backgroundColor: statsOn ? theme.color.accent : "transparent",
              justifyContent: "center", alignItems: statsOn ? "flex-end" : "flex-start", padding: 2 }}>
              <View style={{ width: 20, height: 20, backgroundColor: statsOn ? "#141312" : "rgba(243,242,242,0.6)" }} />
            </View>
          </Pressable>
          <Button label="Done" onPress={onClose} />
        </Pressable>
      </Pressable>
    </Modal>
  );
}
```

- [ ] **Step 4: Run to verify SettingsModal test passes**

Run: `cd mobile && npm test -- SettingsModal`
Expected: PASS.

- [ ] **Step 5: Implement the remaining overlay components**

`StatsOverlay.tsx`:
```tsx
import React from "react";
import { View, Text } from "react-native";
import { theme } from "../theme/tokens";
export function StatsOverlay({ lines }: { lines: string }) {
  return (
    <View style={{ position: "absolute", top: 14, left: 16, padding: 12,
      backgroundColor: "rgba(12,11,11,0.62)", borderLeftWidth: theme.rule, borderLeftColor: theme.color.accent }}>
      <Text style={{ fontFamily: theme.font.mono, fontSize: 10.5, lineHeight: 18, color: "rgba(243,242,242,0.82)" }}>{lines}</Text>
    </View>
  );
}
```
`ErrorOverlay.tsx`:
```tsx
import React from "react";
import { View, Text } from "react-native";
import { theme } from "../theme/tokens";
import { Button } from "./Button";
export function ErrorOverlay({ onReconnect, onBack, reconnecting }:
  { onReconnect: () => void; onBack: () => void; reconnecting: boolean }) {
  return (
    <View style={{ position: "absolute", inset: 0 as any, backgroundColor: theme.color.deep, alignItems: "center", justifyContent: "center", padding: 40 }}>
      <Text style={{ fontFamily: theme.font.bold, fontSize: 22, color: theme.color.text, marginBottom: 8 }}>Stream unavailable</Text>
      <Text style={{ fontFamily: theme.font.regular, fontSize: 13, color: theme.color.textMuted, textAlign: "center", marginBottom: 20 }}>
        The WebRTC session dropped. Check that the host is awake and on the tailnet.</Text>
      <View style={{ flexDirection: "row", gap: 8 }}>
        <Button label={reconnecting ? "Reconnecting…" : "Reconnect"} onPress={onReconnect} loading={reconnecting} />
        <Button label="Back to windows" variant="secondary" onPress={onBack} />
      </View>
    </View>
  );
}
```
`SwitchDrawer.tsx`:
```tsx
import React from "react";
import { View, Text, Pressable, ScrollView } from "react-native";
import { theme } from "../theme/tokens";
import type { Instance } from "../api/client";
export function SwitchDrawer({ instances, activeSerial, onPick, onClose }:
  { instances: Instance[]; activeSerial: string; onPick: (i: Instance) => void; onClose: () => void }) {
  return (
    <View style={{ position: "absolute", inset: 0 as any, flexDirection: "row" }}>
      <View style={{ width: 290, backgroundColor: "rgba(20,19,18,0.95)", borderRightWidth: theme.rule, borderRightColor: "rgba(243,242,242,0.35)" }}>
        <Text style={{ fontFamily: theme.font.bold, fontSize: 15, color: theme.color.text, padding: 16, borderBottomWidth: theme.rule, borderBottomColor: "rgba(243,242,242,0.25)" }}>Instances</Text>
        <ScrollView>
          {instances.map((i) => {
            const act = i.serial === activeSerial;
            return (
              <Pressable key={i.id} onPress={() => onPick(i)}
                style={{ height: 56, flexDirection: "row", alignItems: "center", paddingHorizontal: 16,
                  borderBottomWidth: 1, borderBottomColor: "rgba(243,242,242,0.12)",
                  borderLeftWidth: theme.rule, borderLeftColor: act ? theme.color.accent : "transparent",
                  backgroundColor: act ? "rgba(157,191,149,0.16)" : "transparent" }}>
                <Text style={{ flex: 1, fontFamily: theme.font.bold, fontSize: 13.5, color: act ? theme.color.accent : theme.color.text }}>{i.title}</Text>
                {act ? <Text style={{ fontFamily: theme.font.regular, fontSize: 10, letterSpacing: 1, color: theme.color.accent }}>LIVE</Text> : null}
              </Pressable>
            );
          })}
        </ScrollView>
      </View>
      <Pressable onPress={onClose} style={{ flex: 1, backgroundColor: "rgba(12,11,11,0.5)" }} />
    </View>
  );
}
```
`StreamToolbar.tsx`:
```tsx
import React from "react";
import { View, Text } from "react-native";
import { theme } from "../theme/tokens";
import { NetDot } from "./NetDot";
import { IconButton } from "./IconButton";

type Net = "connected" | "connecting" | "disconnected";
export function StreamToolbar({ net, active, onSettings, onSwitch, onKeyboard, onStats, onBack }:
  { net: Net; active: { settings: boolean; drawer: boolean; keyboard: boolean; stats: boolean };
    onSettings: () => void; onSwitch: () => void; onKeyboard: () => void; onStats: () => void; onBack: () => void }) {
  return (
    <View style={{ position: "absolute", top: 0, right: 0, bottom: 0, padding: 12, justifyContent: "center" }}>
      <View style={{ backgroundColor: "rgba(12,11,11,0.55)", borderWidth: 1, borderColor: "rgba(243,242,242,0.14)", padding: 6, alignItems: "center", gap: 2 }}>
        <View style={{ height: 48, alignItems: "center", justifyContent: "center", gap: 5, borderBottomWidth: 1, borderBottomColor: "rgba(243,242,242,0.14)", marginBottom: 4, width: 48 }}>
          <NetDot state={net} />
          <Text style={{ fontFamily: theme.font.bold, fontSize: 7.5, letterSpacing: 0.8, color: "rgba(243,242,242,0.55)" }}>
            {net === "connected" ? "LIVE" : net === "connecting" ? "SYNC" : "DOWN"}</Text>
        </View>
        <IconButton label="Settings" active={active.settings} onPress={onSettings}><Text style={{ color: active.settings ? "#141312" : theme.color.text }}>⚙</Text></IconButton>
        <IconButton label="Switch instance" active={active.drawer} onPress={onSwitch}><Text style={{ color: active.drawer ? "#141312" : theme.color.text }}>≡</Text></IconButton>
        <IconButton label="Keyboard" active={active.keyboard} onPress={onKeyboard}><Text style={{ color: active.keyboard ? "#141312" : theme.color.text }}>⌨</Text></IconButton>
        <IconButton label="Live stats" active={active.stats} onPress={onStats}><Text style={{ color: active.stats ? "#141312" : theme.color.text }}>◔</Text></IconButton>
        <IconButton label="Back" onPress={onBack}><Text style={{ color: "rgba(243,242,242,0.75)" }}>←</Text></IconButton>
      </View>
    </View>
  );
}
```
Note: toolbar/button glyphs use unicode placeholders to stay dependency-free; swapping for `react-native-svg` icons matching the prototype's SVGs is a later polish step, not required for parity of function.

- [ ] **Step 6: Implement `Stream.tsx` (integration; no unit test — device-verified)**

```tsx
import React, { useEffect, useRef, useState, useCallback } from "react";
import { View, TextInput } from "react-native";
import { RTCView } from "react-native-webrtc";
import { Gesture, GestureDetector } from "react-native-gesture-handler";
import { runOnJS } from "react-native-reanimated";
import { useServer } from "../api/ServerContext";
import { theme } from "../theme/tokens";
import { connectWhep } from "../webrtc/whep";
import { makeInputSocket, clickMsg, dragStartMsg, dragMoveMsg, dragEndMsg, scrollMsg, keyMsg } from "../input/inputSocket";
import { normalizeCoords } from "../input/coords";
import { makeAdaptive } from "../quality/adaptive";
import { StreamToolbar } from "../components/StreamToolbar";
import { SettingsModal } from "../components/SettingsModal";
import { SwitchDrawer } from "../components/SwitchDrawer";
import { StatsOverlay } from "../components/StatsOverlay";
import { ErrorOverlay } from "../components/ErrorOverlay";

type Net = "connected" | "connecting" | "disconnected";

export function Stream({ route, navigation }: { route: any; navigation: any }) {
  const { client } = useServer();
  const { serial } = route.params;
  const [streamUrl, setStreamUrl] = useState<string | null>(null);
  const [net, setNet] = useState<Net>("connecting");
  const [failed, setFailed] = useState(false);
  const [reconnecting, setReconnecting] = useState(false);
  const [overlay, setOverlay] = useState<null | "settings" | "drawer">(null);
  const [keyboardOn, setKeyboardOn] = useState(false);
  const [statsOn, setStatsOn] = useState(false);
  const [tier, setTier] = useState("auto");
  const [instances, setInstances] = useState<any[]>([]);
  const rect = useRef({ width: 1, height: 1 });
  const content = useRef({ w: 1, h: 1 });
  const session = useRef<any>(null);
  const sock = useRef<any>(null);
  const adaptive = useRef<any>(null);

  const start = useCallback(async () => {
    if (!client) return;
    setFailed(false); setNet("connecting");
    try {
      const sel = await client.select(serial);
      content.current = { w: sel.w, h: sel.h };
      session.current?.close();
      session.current = connectWhep({
        whepUrl: sel.whep_url, stunUrl: sel.stun_url,
        onStream: (s) => setStreamUrl(s.toURL()),
        onState: (st) => {
          setNet(st === "connected" ? "connected" : st === "failed" ? "disconnected" : "connecting");
          if (st === "failed") setFailed(true);
        },
      });
      adaptive.current?.stop();
      adaptive.current = makeAdaptive({ serial, onApply: (t) => client.setQuality(serial, t) });
      adaptive.current.start(session.current.pc);
    } catch { setFailed(true); setNet("disconnected"); }
  }, [client, serial]);

  useEffect(() => {
    sock.current = makeInputSocket(client!.inputWsUrl(), { onNet: (s) => { if (s === "bad") setNet("disconnected"); } });
    client!.instances().then(setInstances).catch(() => {});
    start();
    return () => { session.current?.close(); sock.current?.close(); adaptive.current?.stop(); };
  }, [start]);

  const send = (m: object) => sock.current?.send(m);
  const norm = (px: number, py: number) => normalizeCoords({ x: px, y: py }, rect.current, content.current);

  const tap = Gesture.Tap().onEnd((e) => { const c = norm(e.x, e.y); runOnJS(send)(clickMsg(c.x, c.y)); });
  const pan = Gesture.Pan()
    .onStart((e) => { const c = norm(e.x, e.y); runOnJS(send)(dragStartMsg(c.x, c.y)); })
    .onUpdate((e) => { const c = norm(e.x, e.y); runOnJS(send)(dragMoveMsg(c.x, c.y, Math.abs(e.velocityY) > Math.abs(e.velocityX) * 1.5)); })
    .onEnd((e) => { const c = norm(e.x, e.y); runOnJS(send)(dragEndMsg(c.x, c.y)); });
  const gesture = Gesture.Exclusive(pan, tap);

  const pickTier = (t: string) => {
    setTier(t);
    if (t === "auto") adaptive.current?.setAuto();
    else adaptive.current?.pin(t);
  };
  const switchTo = (inst: any) => {
    setOverlay(null);
    client?.keyframe(inst.serial);
    navigation.replace("Stream", { serial: inst.serial, title: inst.title });
  };
  const reconnect = async () => { setReconnecting(true); await start(); setReconnecting(false); };

  const statsLines = `TIER   ${tier}`; // full stats sampling wired in device pass; tier always shown

  return (
    <View style={{ flex: 1, backgroundColor: theme.color.deep }}>
      <GestureDetector gesture={gesture}>
        <View style={{ flex: 1 }} onLayout={(e) => { rect.current = { width: e.nativeEvent.layout.width, height: e.nativeEvent.layout.height }; }}>
          {streamUrl ? <RTCView streamURL={streamUrl} objectFit="contain" style={{ flex: 1 }} /> : null}
        </View>
      </GestureDetector>

      {statsOn && !failed ? <StatsOverlay lines={statsLines} /> : null}

      <StreamToolbar net={net}
        active={{ settings: overlay === "settings", drawer: overlay === "drawer", keyboard: keyboardOn, stats: statsOn }}
        onSettings={() => setOverlay(overlay === "settings" ? null : "settings")}
        onSwitch={() => setOverlay(overlay === "drawer" ? null : "drawer")}
        onKeyboard={() => setKeyboardOn((v) => !v)}
        onStats={() => setStatsOn((v) => !v)}
        onBack={() => navigation.navigate("InstanceList")} />

      {keyboardOn ? (
        <TextInput autoFocus onKeyPress={(e) => send(keyMsg(e.nativeEvent.key))}
          onBlur={() => setKeyboardOn(false)}
          style={{ position: "absolute", opacity: 0, height: 1, width: 1 }} />
      ) : null}

      {overlay === "drawer" ? (
        <SwitchDrawer instances={instances} activeSerial={serial} onPick={switchTo} onClose={() => setOverlay(null)} />
      ) : null}
      {overlay === "settings" ? (
        <SettingsModal tier={tier} onPick={pickTier} statsOn={statsOn}
          onToggleStats={() => setStatsOn((v) => !v)} onClose={() => setOverlay(null)} />
      ) : null}
      {failed ? <ErrorOverlay onReconnect={reconnect} onBack={() => navigation.navigate("InstanceList")} reconnecting={reconnecting} /> : null}
    </View>
  );
}
```
Note on the tier-switch window: a full port of the web `_tierSwitchUntil` ICE-failed suppression belongs in the device pass, where the real restart bounce is observable. For this task, `onState("failed")` shows the error overlay and the user (or an added timer) re-negotiates; the device checklist (Task 13) validates and, if needed, adds the suppression window. Keep this note in the task so the executor knows it is deliberate, not forgotten.

- [ ] **Step 7: Run the full unit suite**

Run: `cd mobile && npm test`
Expected: all unit tests PASS (tokens, coords, tiers, urls, ServerContext, NetDot, ServerSetup, InstanceList, whep, inputSocket, adaptive, SettingsModal).

- [ ] **Step 8: Commit**

```bash
git add mobile/src/screens/Stream.tsx mobile/src/components
git commit -m "feat(mobile): Stream screen — video, gestures, overlays"
```

---

### Task 13: Dev build + device smoke test + README

**Files:**
- Create: `mobile/README.md`
- Create: `mobile/docs/device-smoke-test.md`

**Interfaces:**
- Consumes: everything above.
- Produces: build/run instructions and a manual verification checklist. No app code changes except fixes discovered during the smoke test (each committed separately with a descriptive message).

- [ ] **Step 1: Write `mobile/README.md`**

Content: prerequisites (Node 22, an Expo account for EAS, a physical device or simulator with the dev-client installed), and the exact commands:
```bash
cd mobile
npm install
# one-time dev-client builds (Expo Go will NOT work — native WebRTC):
npx eas login
npx eas build --profile development --platform ios
npx eas build --profile development --platform android
# then run the JS bundle against the installed dev-client:
npx expo start --dev-client
```
Explain: enter the server base URL (Tailscale `http://100.x.x.x:8080`) on first launch; it persists.

- [ ] **Step 2: Write `mobile/docs/device-smoke-test.md`**

A checklist the tester runs on a real device over real Tailscale against a running server + at least two scrcpy instances:
```
[ ] ServerSetup: bad URL shows inline error; valid URL persists and advances
[ ] ServerSetup: unreachable host shows "Can't reach server"
[ ] InstanceList: cards render with live 16:9 previews; N-online count correct
[ ] InstanceList: pull-to-refresh updates; 60s poll refreshes
[ ] Stream: WHEP connects; video paints; net dot green
[ ] Stream: tap registers on device; drag moves; two-finger scroll scrolls
[ ] Stream: keyboard button forwards keystrokes
[ ] Settings: pinning 480/1080 changes resolution; Auto resumes adaptation
[ ] Under induced congestion, tier steps DOWN only (never restart-storms up)
[ ] Quick-switch drawer switches instances; new stream paints quickly (keyframe prefetch)
[ ] Kill server → ErrorOverlay appears; restart → Reconnect recovers
[ ] Background/foreground the app → WS + WHEP recover
[ ] Verify the tier-switch ICE bounce does NOT blank to the error overlay;
    if it does, port the _tierSwitchUntil suppression window from the web client
    (app.js oniceconnectionstatechange) into whep.ts / Stream.tsx and re-test
```

- [ ] **Step 3: Run the dev build (executor, real account)**

Run the EAS build commands from the README. Expected: a dev-client app installs on the device.

- [ ] **Step 4: Execute the smoke-test checklist**

Work through `device-smoke-test.md`. For each failure, open a focused fix, commit it, re-run that line.

- [ ] **Step 5: Commit docs (and any fixes)**

```bash
git add mobile/README.md mobile/docs/device-smoke-test.md
git commit -m "docs(mobile): dev-build instructions and device smoke-test checklist"
```

---

## Self-Review

**Spec coverage:**
- Manual base-URL entry + persist → Task 5 (ServerContext) + Task 7 (ServerSetup). ✓
- `/instances`, `/select`, `/keyframe`, `/quality`, `/preview`, `/input` → Task 4 (client) + used in Tasks 8, 12. ✓
- WHEP + srflx-wait → Task 9. ✓
- Gesture→coords→WS with exact message shapes → Tasks 2, 10, 12. ✓
- Downgrade-only adaptive quality + manual pin → Tasks 3, 11, 12. ✓
- 3 screens + all overlays (settings, drawer, stats, keyboard, error) → Tasks 7, 8, 12. ✓
- Modernist design tokens + Archivo font → Tasks 1, 6, and applied per-screen. ✓
- MJPEG dropped; error overlay instead → Task 12 (ErrorOverlay), no MJPEG anywhere. ✓
- Foreground re-negotiation, WS backoff, 404 handling → Task 10 (backoff), Task 12 (device pass covers AppState; note included). ✓
- Dev-client build reality → Task 1 (plugin/config) + Task 13 (build + README). ✓

**Placeholder scan:** Unicode-glyph and text-`⟳`/`←` icons are deliberate, dependency-free stand-ins with explicit notes and a later-polish path (not "TODO"). The stats overlay ships `TIER` immediately; full getStats sampling is called out as a device-pass item with a clear note, not a silent gap. No "TBD"/"handle edge cases"/"write tests for the above" left.

**Type consistency:** `Instance`/`SelectResp` defined in Task 4 and consumed unchanged in Tasks 8, 12. `makeClient` method names (`instances`, `select`, `keyframe`, `setQuality`, `previewUrl`, `inputWsUrl`) consistent across Tasks 5, 8, 12. `makeAdaptive` API (`start/stop/pin/setAuto/current`) consistent between Tasks 11 and 12. `connectWhep` opts consistent between Tasks 9 and 12. Message builders identical between Tasks 10 and 12. ✓
