# Unified React/React Native Frontend Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the duplicated web (`src/client/*.js`) and mobile (`mobile/src/*`) frontends with a monorepo of shared `packages/core` (logic) and `packages/ui` (react-native-web components) consumed by a new Next.js `apps/web`, the relocated `apps/mobile` (Expo), and a new pywebview `apps/desktop` shell — single cutover, old `src/client` deleted only in the final task.

**Architecture:** npm workspaces monorepo. `packages/core` holds platform-agnostic TS (API client, Supabase auth, WHEP session state machine, quality tiers, input-channel protocol) seeded from `mobile/src`. `packages/ui` holds shared React components/screens built on `react-native-web`. `apps/web` (Next.js) and `apps/mobile` (Expo) each supply their own platform adapters (WebRTC constructor, secure storage, video rendering) and consume the shared packages. `apps/desktop` wraps `apps/web`'s served output in a `pywebview` window.

**Tech Stack:** TypeScript, React, React Native / Expo, react-native-web, Next.js (app router), npm workspaces, jest, Python `pywebview`.

**Spec:** `docs/superpowers/specs/2026-09-05-react-unified-frontend-design.md`

## Global Constraints

- Workspace tool: npm workspaces only. No pnpm, yarn, or Turborepo.
- Package names use the `@wc/` scope: `@wc/core`, `@wc/ui`.
- Desktop shell: `pywebview` (Python), embedded in the existing PyInstaller/Inno Setup build. No Electron, no Tauri.
- FastAPI backend (`src/server/`) API surface, auth, and WHEP signaling endpoints do not change in this plan.
- `src/client/` stays live and untouched (still served by FastAPI) until Task 10 — this is a single cutover, not incremental rollout. Every earlier task must leave the currently-running app working.
- New `packages/core`, `packages/ui`, and `apps/web` test suites must be wired into a real CI job (`.github/workflows/`) in the same task that introduces them — never left as a documented-but-unrun command (see spec's CI section for why).
- `mobile/`'s existing 67-test jest baseline must stay green after every task that touches it.

---

### Task 1: Monorepo workspace scaffold

**Files:**
- Create: `package.json` (repo root)
- Create: `packages/core/package.json`
- Create: `packages/core/tsconfig.json`
- Create: `packages/core/jest.config.js`
- Create: `packages/core/src/index.ts`
- Create: `packages/ui/package.json`
- Create: `packages/ui/tsconfig.json`
- Create: `packages/ui/jest.config.js`
- Create: `packages/ui/src/index.ts`
- Create: `.github/workflows/frontend-packages.yml`
- Test: `packages/core/src/index.test.ts`, `packages/ui/src/index.test.ts`

**Interfaces:**
- Produces: workspace packages `@wc/core` and `@wc/ui`, each resolvable via npm workspaces (no publish step — apps depend on them as `"@wc/core": "*"` / `"@wc/ui": "*"` and npm symlinks the local workspace).

- [ ] **Step 1: Create the root workspace `package.json`**

```json
{
  "name": "window-control",
  "private": true,
  "workspaces": [
    "packages/*",
    "apps/web",
    "apps/mobile"
  ],
  "scripts": {
    "test:core": "npm test -w packages/core",
    "test:ui": "npm test -w packages/ui"
  }
}
```

- [ ] **Step 2: Scaffold `packages/core`**

`packages/core/package.json`:
```json
{
  "name": "@wc/core",
  "version": "0.0.0",
  "private": true,
  "main": "src/index.ts",
  "types": "src/index.ts",
  "scripts": {
    "test": "jest"
  },
  "devDependencies": {
    "@types/jest": "^29.5.14",
    "jest": "^29.7.0",
    "ts-jest": "^29.2.5",
    "typescript": "~6.0.3"
  }
}
```

`packages/core/tsconfig.json`:
```json
{
  "compilerOptions": {
    "target": "ES2020",
    "module": "ESNext",
    "moduleResolution": "Bundler",
    "strict": true,
    "declaration": true,
    "outDir": "dist",
    "esModuleInterop": true,
    "skipLibCheck": true
  },
  "include": ["src"]
}
```

`packages/core/jest.config.js`:
```js
module.exports = {
  preset: "ts-jest",
  testEnvironment: "node",
};
```

`packages/core/src/index.ts`:
```ts
export const CORE_PACKAGE_READY = true;
```

- [ ] **Step 3: Write the placeholder-package test**

`packages/core/src/index.test.ts`:
```ts
import { CORE_PACKAGE_READY } from "./index";

test("core package resolves", () => {
  expect(CORE_PACKAGE_READY).toBe(true);
});
```

- [ ] **Step 4: Scaffold `packages/ui` the same way**

`packages/ui/package.json`:
```json
{
  "name": "@wc/ui",
  "version": "0.0.0",
  "private": true,
  "main": "src/index.ts",
  "types": "src/index.ts",
  "scripts": {
    "test": "jest"
  },
  "dependencies": {
    "@wc/core": "*"
  },
  "devDependencies": {
    "@types/jest": "^29.5.14",
    "@types/react": "~19.2.2",
    "jest": "^29.7.0",
    "react": "19.2.3",
    "react-native": "0.86.2",
    "react-native-web": "^0.19.13",
    "ts-jest": "^29.2.5",
    "typescript": "~6.0.3"
  }
}
```

`packages/ui/tsconfig.json`: identical to `packages/core/tsconfig.json` but add `"jsx": "react-jsx"`.

`packages/ui/jest.config.js`:
```js
module.exports = {
  preset: "ts-jest",
  testEnvironment: "jsdom",
};
```

`packages/ui/src/index.ts`:
```ts
export const UI_PACKAGE_READY = true;
```

`packages/ui/src/index.test.ts`:
```ts
import { UI_PACKAGE_READY } from "./index";

test("ui package resolves", () => {
  expect(UI_PACKAGE_READY).toBe(true);
});
```

- [ ] **Step 5: Install and run both suites**

Run: `npm install && npm run test:core && npm run test:ui`
Expected: both PASS (1 test each).

- [ ] **Step 6: Wire CI**

`.github/workflows/frontend-packages.yml`:
```yaml
name: frontend-packages
on:
  push:
    branches: [main, "feature/**"]
  pull_request:
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with:
          node-version: "20"
      - run: npm install
      - run: npm run test:core
      - run: npm run test:ui
```

- [ ] **Step 7: Commit**

```bash
git add package.json packages/core packages/ui .github/workflows/frontend-packages.yml
git commit -m "chore(monorepo): scaffold npm workspaces with @wc/core and @wc/ui packages"
```

---

### Task 2: Extract API client, Supabase auth, and server context into `@wc/core`

**Files:**
- Create: `packages/core/src/api/urls.ts` (from `mobile/src/api/urls.ts`)
- Create: `packages/core/src/api/client.ts` (from `mobile/src/api/client.ts`)
- Create: `packages/core/src/api/supabaseAuth.ts` (from `mobile/src/api/supabaseAuth.ts`)
- Create: `packages/core/src/api/storage.ts` (new — `SecureStorageAdapter` interface)
- Create: `packages/core/src/api/ServerContext.tsx` (from `mobile/src/api/ServerContext.tsx`, adapted)
- Create: `packages/core/src/index.ts` (add exports)
- Test: `packages/core/src/api/urls.test.ts`, `client.test.ts`, `supabaseAuth.test.ts`, `ServerContext.test.tsx` (moved from `mobile/src/api/*.test.ts`)
- Modify: `packages/core/package.json` (add `react` dep for `ServerContext.tsx`)

**Interfaces:**
- Consumes: nothing outside this task.
- Produces: `@wc/core` exports `normalizeBase`, `httpUrl`, `wsUrl`, `makeClient`, `ApiError`, `Instance`, `IceServer`, `SelectResp`, `signInWithPassword`, `signUpWithPassword`, `AuthResult`, `SecureStorageAdapter` (type), `ServerProvider`, `useServer`. `ServerProvider` now takes two required props — `plainStorage: SecureStorageAdapter` and `secureStorage: SecureStorageAdapter` — replacing the direct `AsyncStorage`/`SecureStore` calls mobile's original `ServerContext.tsx` made internally.

- [ ] **Step 1: Move `urls.ts` and `client.ts` verbatim**

```bash
mkdir -p packages/core/src/api
git mv mobile/src/api/urls.ts packages/core/src/api/urls.ts
git mv mobile/src/api/urls.test.ts packages/core/src/api/urls.test.ts
git mv mobile/src/api/client.ts packages/core/src/api/client.ts
git mv mobile/src/api/client.test.ts packages/core/src/api/client.test.ts
git mv mobile/src/api/supabaseAuth.ts packages/core/src/api/supabaseAuth.ts
git mv mobile/src/api/supabaseAuth.test.ts packages/core/src/api/supabaseAuth.test.ts
```

These three files have no external (mobile-only) imports — `client.ts` imports only `./urls`, `urls.ts` and `supabaseAuth.ts` import nothing local. No import-path edits needed inside them.

- [ ] **Step 2: Run the moved tests from their new location**

Run: `cd packages/core && npx jest src/api/urls.test.ts src/api/client.test.ts src/api/supabaseAuth.test.ts`
Expected: PASS (all pre-existing assertions, unchanged).

- [ ] **Step 3: Write the `SecureStorageAdapter` interface**

`packages/core/src/api/storage.ts`:
```ts
export type SecureStorageAdapter = {
  getItem(key: string): Promise<string | null>;
  setItem(key: string, value: string): Promise<void>;
  deleteItem(key: string): Promise<void>;
};
```

- [ ] **Step 4: Write the failing test for the adapted `ServerContext`**

`packages/core/src/api/ServerContext.test.tsx` (new — mobile's original had no test file for this module; this is new coverage, not a move):
```tsx
import React from "react";
import { render, waitFor, act } from "@testing-library/react";
import { ServerProvider, useServer } from "./ServerContext";
import type { SecureStorageAdapter } from "./storage";

function makeMemoryStorage(): SecureStorageAdapter {
  const store = new Map<string, string>();
  return {
    getItem: async (k) => store.get(k) ?? null,
    setItem: async (k, v) => { store.set(k, v); },
    deleteItem: async (k) => { store.delete(k); },
  };
}

function Probe() {
  const { ready, base, authToken, setServer } = useServer();
  return (
    <div>
      <span data-testid="ready">{String(ready)}</span>
      <span data-testid="base">{base ?? ""}</span>
      <span data-testid="token">{authToken ?? ""}</span>
      <button onClick={() => setServer("http://host:8000", "tok")}>set</button>
    </div>
  );
}

test("ServerProvider loads persisted base/token and exposes ready", async () => {
  const plain = makeMemoryStorage();
  const secure = makeMemoryStorage();
  await plain.setItem("wc_base", "http://saved:8000");
  await secure.setItem("wc_auth_token", "saved-tok");

  const { getByTestId } = render(
    <ServerProvider plainStorage={plain} secureStorage={secure}>
      <Probe />
    </ServerProvider>
  );

  await waitFor(() => expect(getByTestId("ready").textContent).toBe("true"));
  expect(getByTestId("base").textContent).toBe("http://saved:8000");
  expect(getByTestId("token").textContent).toBe("saved-tok");
});

test("setServer persists base and token via the injected adapters", async () => {
  const plain = makeMemoryStorage();
  const secure = makeMemoryStorage();
  const { getByTestId, getByText } = render(
    <ServerProvider plainStorage={plain} secureStorage={secure}>
      <Probe />
    </ServerProvider>
  );
  await waitFor(() => expect(getByTestId("ready").textContent).toBe("true"));

  await act(async () => { getByText("set").click(); });

  expect(await plain.getItem("wc_base")).toBe("http://host:8000");
  expect(await secure.getItem("wc_auth_token")).toBe("tok");
});
```

- [ ] **Step 5: Run it to verify it fails**

Run: `cd packages/core && npx jest src/api/ServerContext.test.tsx`
Expected: FAIL — `ServerContext` module doesn't exist yet, and `@testing-library/react`/`react` aren't installed in `packages/core` yet.

- [ ] **Step 6: Add the missing dependencies**

`packages/core/package.json` — add to `dependencies`: `"react": "19.2.3"`; to `devDependencies`: `"@testing-library/react": "^16.0.1"`, `"jest-environment-jsdom": "^29.7.0"`. Change `packages/core/jest.config.js`'s `testEnvironment` to `"jsdom"` (matches `packages/ui`'s config; `ServerContext.tsx` is the only DOM-touching file in `core`, and jsdom is a safe default for the whole package).

Run: `npm install`

- [ ] **Step 7: Write `ServerContext.tsx`, adapted from mobile's original**

`packages/core/src/api/ServerContext.tsx` — same state machine as `mobile/src/api/ServerContext.tsx`, with `AsyncStorage`/`SecureStore` calls replaced by the injected adapters:

```tsx
import React, { createContext, useContext, useEffect, useMemo, useState, useCallback } from "react";
import { makeClient } from "./client";
import { normalizeBase } from "./urls";
import type { SecureStorageAdapter } from "./storage";

type ApiClient = ReturnType<typeof makeClient>;

type Ctx = {
  base: string | null;
  authToken: string | null;
  client: ApiClient | null;
  setServer: (base: string, token: string) => Promise<ApiClient>;
  ready: boolean;
  supabaseUrl: string;
  supabaseAnonKey: string;
};
const ServerCtx = createContext<Ctx | null>(null);
const BASE_KEY = "wc_base";
const TOKEN_KEY = "wc_auth_token";

export function ServerProvider({
  children,
  plainStorage,
  secureStorage,
}: {
  children: React.ReactNode;
  plainStorage: SecureStorageAdapter;
  secureStorage: SecureStorageAdapter;
}) {
  const [base, setBaseState] = useState<string | null>(null);
  const [authToken, setAuthTokenState] = useState<string | null>(null);
  const [baseLoaded, setBaseLoaded] = useState(false);
  const [tokenLoaded, setTokenLoaded] = useState(false);

  useEffect(() => {
    plainStorage.getItem(BASE_KEY)
      .then((v) => { if (v) setBaseState(v); })
      .finally(() => setBaseLoaded(true));
    secureStorage.getItem(TOKEN_KEY)
      .then((v) => { if (v) setAuthTokenState(v); })
      .finally(() => setTokenLoaded(true));
  }, [plainStorage, secureStorage]);

  const setServer = useCallback(async (url: string, token: string) => {
    const norm = normalizeBase(url);
    await plainStorage.setItem(BASE_KEY, norm);
    if (token) {
      await secureStorage.setItem(TOKEN_KEY, token);
    } else {
      await secureStorage.deleteItem(TOKEN_KEY);
    }
    setBaseState(norm);
    setAuthTokenState(token || null);
    return makeClient(norm, token || null);
  }, [plainStorage, secureStorage]);

  const client = useMemo(
    () => (base ? makeClient(base, authToken) : null),
    [base, authToken]
  );

  const [supabaseUrl, setSupabaseUrl] = useState("");
  const [supabaseAnonKey, setSupabaseAnonKey] = useState("");

  useEffect(() => {
    if (!base) return;
    fetch(`${base}/auth/config`)
      .then((r) => r.json())
      .then((cfg) => {
        setSupabaseUrl(cfg.supabase_url || "");
        setSupabaseAnonKey(cfg.supabase_anon_key || "");
      })
      .catch(() => {});
  }, [base]);

  const ready = baseLoaded && tokenLoaded;
  return (
    <ServerCtx.Provider value={{ base, authToken, client, setServer, ready, supabaseUrl, supabaseAnonKey }}>
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

- [ ] **Step 8: Run the test to verify it passes**

Run: `cd packages/core && npx jest src/api/ServerContext.test.tsx`
Expected: PASS (both tests).

- [ ] **Step 9: Export everything from `packages/core/src/index.ts`**

```ts
export * from "./api/urls";
export * from "./api/client";
export * from "./api/supabaseAuth";
export * from "./api/storage";
export * from "./api/ServerContext";
```

- [ ] **Step 10: Run the full core suite and commit**

Run: `npm run test:core`
Expected: PASS (all api/* tests + the index placeholder test).

```bash
git add packages/core mobile/src/api
git commit -m "feat(core): extract API client, Supabase auth, and server context with injectable storage"
```

---

### Task 3: Extract WHEP session, input channel, and quality logic into `@wc/core`

**Files:**
- Create: `packages/core/src/webrtc/whep.ts` (from `mobile/src/webrtc/whep.ts`, adapted)
- Create: `packages/core/src/input/inputChannel.ts` (from `mobile/src/input/inputChannel.ts`)
- Create: `packages/core/src/input/coords.ts` (from `mobile/src/input/coords.ts`)
- Create: `packages/core/src/quality/tiers.ts` (from `mobile/src/quality/tiers.ts`)
- Create: `packages/core/src/quality/adaptive.ts` (from `mobile/src/quality/adaptive.ts`)
- Modify: `packages/core/src/index.ts` (add exports)
- Test: `whep.test.ts`, `inputChannel.test.ts`, `coords.test.ts`, `tiers.test.ts` (none exists per the earlier `find` — confirm before moving), `adaptive.test.ts` (moved from `mobile/src/{webrtc,input,quality}/*.test.ts`)

**Interfaces:**
- Consumes: `IceServer` type from Task 2's `@wc/core` `api/client.ts` export.
- Produces: `connectWhep(opts: ConnectWhepOpts): Promise<WhepSession>` where `ConnectWhepOpts` now requires `RTCImpl` (no default) — the caller supplies its platform's `RTCPeerConnection` constructor. `createInputSender`, `InputSender`, `normalizeCoords`, `TIER_ORDER`, `stepTier`, `shouldDowngrade`, `nextBadStreak`, `makeAdaptive`.

- [ ] **Step 1: Move the platform-agnostic files verbatim**

```bash
mkdir -p packages/core/src/webrtc packages/core/src/input packages/core/src/quality
git mv mobile/src/input/inputChannel.ts packages/core/src/input/inputChannel.ts
git mv mobile/src/input/inputChannel.test.ts packages/core/src/input/inputChannel.test.ts
git mv mobile/src/input/coords.ts packages/core/src/input/coords.ts
git mv mobile/src/input/coords.test.ts packages/core/src/input/coords.test.ts
git mv mobile/src/quality/tiers.ts packages/core/src/quality/tiers.ts
git mv mobile/src/quality/adaptive.ts packages/core/src/quality/adaptive.ts
git mv mobile/src/quality/adaptive.test.ts packages/core/src/quality/adaptive.test.ts
```

`adaptive.ts`'s only local import (`./tiers`) keeps working unchanged — both files land in the same relative position (`quality/adaptive.ts` importing `./tiers`).

- [ ] **Step 2: Run the moved tests from their new location**

Run: `cd packages/core && npx jest src/input/inputChannel.test.ts src/input/coords.test.ts src/quality/adaptive.test.ts`
Expected: PASS.

- [ ] **Step 3: Move `whep.ts` and remove its hardcoded `react-native-webrtc` import**

```bash
git mv mobile/src/webrtc/whep.ts packages/core/src/webrtc/whep.ts
git mv mobile/src/webrtc/whep.test.ts packages/core/src/webrtc/whep.test.ts
```

Edit `packages/core/src/webrtc/whep.ts`: `whep.ts` already accepts an injectable `RTCImpl` in `ConnectWhepOpts`, but it defaults to a hardcoded `react-native-webrtc` import — that import has to go, since `@wc/core` must not depend on a React Native native module (it breaks in a Next.js/browser bundle). Replace:

```ts
import {
  RTCPeerConnection as RN_RTC,
  type MediaStream,
  type RTCPeerConnection,
} from "react-native-webrtc";
import { createInputSender } from "../input/inputChannel";
import type { InputSender } from "../input/inputChannel";
import type { IceServer } from "../api/client";
```

with:

```ts
import { createInputSender } from "../input/inputChannel";
import type { InputSender } from "../input/inputChannel";
import type { IceServer } from "../api/client";

// Platform-supplied constructor/types — mobile passes react-native-webrtc's
// RTCPeerConnection, web passes the browser global. @wc/core has no native
// or DOM dependency of its own.
type MediaStream = any;
type RTCPeerConnection = any;
```

and change `ConnectWhepOpts` so `RTCImpl` is required, and `connectWhep`'s first line drops the fallback:

```ts
type ConnectWhepOpts = {
  whepUrl: string;
  whepToken: string;
  iceServers: IceServer[];
  onStream: (stream: MediaStream) => void;
  onInputRtt: (ms: number) => void;
  onState: (state: "connecting" | "connected" | "disconnected") => void;
  RTCImpl: any;
  fetchImpl?: typeof fetch;
  timeoutMs?: number;
};

export function connectWhep(opts: ConnectWhepOpts): Promise<WhepSession> {
  const RTC = opts.RTCImpl;
  const doFetch = opts.fetchImpl || fetch;
```

(Everything else in the file — `waitForIceGatheringComplete`, `whepError`, `WhepSession`, the whole `connectWhep` body from `deleteResource` onward — is unchanged.)

- [ ] **Step 4: Update `whep.test.ts` for the now-required `RTCImpl`**

Read `packages/core/src/webrtc/whep.test.ts` after the move (it was moved from `mobile/src/webrtc/whep.test.ts` in Step 3). If any test constructs `ConnectWhepOpts` without an explicit `RTCImpl` (relying on the old default), add a fake `RTCImpl` to that call — a minimal mock `RTCPeerConnection` class matching whatever shape the existing test fakes already use elsewhere in the same file for `pc`.

- [ ] **Step 5: Run the whep suite to verify it passes**

Run: `cd packages/core && npx jest src/webrtc/whep.test.ts`
Expected: PASS.

- [ ] **Step 6: Write the missing `tiers.test.ts`**

`mobile/src/quality/tiers.ts` had no companion test file (confirmed absent from the earlier directory listing). Add one now, testing the exported functions directly from their real bodies (`stepTier` walks `TIER_ORDER`; `shouldDowngrade`/`nextBadStreak` gate on the thresholds already defined in `tiers.ts`):

`packages/core/src/quality/tiers.test.ts`:
```ts
import { TIER_ORDER, DOWNGRADE_STREAK, stepTier, shouldDowngrade, nextBadStreak } from "./tiers";

test("stepTier moves within TIER_ORDER bounds", () => {
  expect(stepTier("720", 1)).toBe("1080");
  expect(stepTier("720", -1)).toBe("480");
  expect(stepTier(TIER_ORDER[TIER_ORDER.length - 1], 1)).toBe(TIER_ORDER[TIER_ORDER.length - 1]);
  expect(stepTier(TIER_ORDER[0], -1)).toBe(TIER_ORDER[0]);
});

test("nextBadStreak increments while congested, resets when not", () => {
  expect(nextBadStreak(0, true)).toBe(1);
  expect(nextBadStreak(DOWNGRADE_STREAK - 1, true)).toBe(DOWNGRADE_STREAK);
  expect(nextBadStreak(2, false)).toBe(0);
});

test("shouldDowngrade is a pure threshold check", () => {
  expect(typeof shouldDowngrade(0.5, 500)).toBe("boolean");
});
```

Run: `cd packages/core && npx jest src/quality/tiers.test.ts` — expected PASS. If `shouldDowngrade`'s actual threshold values make the third assertion's specific inputs not exercise a meaningful boundary, read `tiers.ts`'s real thresholds and tighten the assertion to a concrete `true`/`false` expectation instead of `typeof`.

- [ ] **Step 7: Export everything from `packages/core/src/index.ts`**

Add:
```ts
export * from "./webrtc/whep";
export * from "./input/inputChannel";
export * from "./input/coords";
export * from "./quality/tiers";
export * from "./quality/adaptive";
```

- [ ] **Step 8: Run the full core suite and commit**

Run: `npm run test:core`
Expected: PASS (every test moved/added in Tasks 2-3, plus the index placeholder).

```bash
git add packages/core mobile/src/webrtc mobile/src/input mobile/src/quality
git commit -m "feat(core): extract WHEP session, input channel, and quality logic; decouple whep.ts from react-native-webrtc"
```

---

### Task 4: Relocate mobile app to `apps/mobile` and wire it onto `@wc/core`

**Files:**
- Modify: `mobile/package.json` → move to `apps/mobile/package.json` (add `"@wc/core": "*"` dependency)
- Modify: `apps/mobile/src/screens/Stream.tsx` (import path + `RTCImpl` wiring)
- Modify: `apps/mobile/src/screens/Login.tsx`, `ServerSetup.tsx`, `InstanceList.tsx` (import paths)
- Modify: `apps/mobile/src/navigation/Root.tsx` (wrap with `ServerProvider` + adapters, if not already done elsewhere — check `App.tsx`)
- Modify: `apps/mobile/App.tsx` (wire `ServerProvider` with mobile's storage adapters)
- Create: `apps/mobile/src/platform/storage.ts` (mobile's `SecureStorageAdapter` implementation)
- Test: full existing `apps/mobile` jest suite (relocated, unchanged)

**Interfaces:**
- Consumes: `@wc/core`'s `connectWhep`, `ConnectWhepOpts`, `createInputSender`, `normalizeCoords`, `makeAdaptive`, `ServerProvider`, `useServer`, `SecureStorageAdapter`, `makeClient`, `normalizeBase`, `signInWithPassword`, `signUpWithPassword` (all from Tasks 2-3).
- Produces: `apps/mobile` — same app, now importing shared logic instead of the local copies removed in Tasks 2-3.

- [ ] **Step 1: Relocate the directory**

```bash
mkdir -p apps
git mv mobile apps/mobile
```

- [ ] **Step 2: Add the `@wc/core` dependency**

Edit `apps/mobile/package.json` — add to `dependencies`: `"@wc/core": "*"`.

Run: `npm install`

- [ ] **Step 3: Write mobile's `SecureStorageAdapter` implementation**

`apps/mobile/src/platform/storage.ts`:
```ts
import AsyncStorage from "@react-native-async-storage/async-storage";
import * as SecureStore from "expo-secure-store";
import type { SecureStorageAdapter } from "@wc/core";

export const plainStorage: SecureStorageAdapter = {
  getItem: (key) => AsyncStorage.getItem(key),
  setItem: (key, value) => AsyncStorage.setItem(key, value),
  deleteItem: (key) => AsyncStorage.removeItem(key),
};

export const secureStorage: SecureStorageAdapter = {
  getItem: (key) => SecureStore.getItemAsync(key),
  setItem: (key, value) => SecureStore.setItemAsync(key, value),
  deleteItem: (key) => SecureStore.deleteItemAsync(key),
};
```

- [ ] **Step 4: Find and update the `ServerProvider` mount point**

Run: `grep -rn "ServerProvider" apps/mobile/src apps/mobile/App.tsx`

Wherever `ServerProvider` is currently imported from `"./api/ServerContext"` (or a relative path into `src/api`), change the import to `"@wc/core"` and pass the two adapters:

```tsx
import { ServerProvider } from "@wc/core";
import { plainStorage, secureStorage } from "./src/platform/storage";
// ...
<ServerProvider plainStorage={plainStorage} secureStorage={secureStorage}>
  {/* existing children unchanged */}
</ServerProvider>
```

- [ ] **Step 5: Update `Login.tsx`, `ServerSetup.tsx`, `InstanceList.tsx` import paths**

In each of `apps/mobile/src/screens/Login.tsx`, `ServerSetup.tsx`, `InstanceList.tsx`: replace `import { useServer } from "../api/ServerContext";` with `import { useServer } from "@wc/core";`. `Login.tsx` also replace `import { signInWithPassword, signUpWithPassword } from "../api/supabaseAuth";` with `import { signInWithPassword, signUpWithPassword } from "@wc/core";`. `ServerSetup.tsx` also replace `import { normalizeBase } from "../api/urls";` with `import { normalizeBase } from "@wc/core";`. `InstanceList.tsx` also replace `import type { Instance } from "../api/client";` with `import type { Instance } from "@wc/core";`.

- [ ] **Step 6: Update `Stream.tsx` — the one screen needing an explicit `RTCImpl`**

In `apps/mobile/src/screens/Stream.tsx`:

Replace:
```tsx
import { RTCView } from "react-native-webrtc";
```
with:
```tsx
import { RTCView, RTCPeerConnection } from "react-native-webrtc";
```

Replace:
```tsx
import { connectWhep, WhepSession } from "../webrtc/whep";
import { normalizeCoords } from "../input/coords";
import { makeAdaptive } from "../quality/adaptive";
```
with:
```tsx
import { connectWhep, WhepSession, normalizeCoords, makeAdaptive } from "@wc/core";
```

Replace:
```tsx
import { useServer } from "../api/ServerContext";
```
with:
```tsx
import { useServer } from "@wc/core";
```

In the `connectWhep({...})` call inside `start()`, add `RTCImpl: RTCPeerConnection,` to the options object (alongside the existing `whepUrl`, `whepToken`, `iceServers`, etc.) — this is the platform adapter Task 3 made mandatory.

- [ ] **Step 7: Run the full relocated mobile suite**

Run: `cd apps/mobile && npx jest`
Expected: PASS — same 67 tests as the pre-move baseline recorded in `HANDOFF.md`'s 2026-09-04 02:10 entry. If any test imports the now-removed local `src/{api,webrtc,input,quality}` modules directly, update that test's import to `@wc/core` the same way.

- [ ] **Step 8: Delete the now-empty local duplicate directories**

```bash
rmdir apps/mobile/src/webrtc apps/mobile/src/input apps/mobile/src/quality 2>/dev/null || true
git rm -r apps/mobile/src/api/ServerContext.tsx apps/mobile/src/api/urls.ts apps/mobile/src/api/client.ts apps/mobile/src/api/supabaseAuth.ts 2>/dev/null || true
```

(These were already `git mv`'d out in Tasks 2-3; this step only removes stragglers if `git mv` left an empty directory or a stub file behind.)

- [ ] **Step 9: Commit**

```bash
git add apps
git commit -m "refactor(mobile): relocate to apps/mobile and consume @wc/core instead of local duplicates"
```

---

### Task 5: Extract shared theme and primitive components into `@wc/ui`

**Files:**
- Create: `packages/ui/src/theme/tokens.ts` (from `apps/mobile/src/theme/tokens.ts`)
- Create: `packages/ui/src/components/Button.tsx`, `IconButton.tsx`, `NetChip.tsx`, `NetDot.tsx`, `StatsOverlay.tsx`, `ErrorOverlay.tsx`, `BottomNav.tsx`, `InstanceRow.tsx`, `StreamToolbar.tsx`, `SwitchDrawer.tsx`, `SettingsModal.tsx` (from `apps/mobile/src/components/*`)
- Modify: `packages/ui/src/index.ts` (add exports)
- Test: each moved component's `.test.tsx` (e.g. `NetDot.test.tsx`, `SettingsModal.test.tsx`)

**Interfaces:**
- Consumes: `Instance` type from `@wc/core` (Task 2), for `InstanceRow`.
- Produces: `@wc/ui` exports `theme`, `Button`, `IconButton`, `NetChip`, `NetDot`, `StatsOverlay`, `ErrorOverlay`, `BottomNav`, `InstanceRow`, `StreamToolbar`, `SwitchDrawer`, `SettingsModal`.

- [ ] **Step 1: Move the theme and every listed component + its test**

```bash
mkdir -p packages/ui/src/theme packages/ui/src/components
git mv apps/mobile/src/theme/tokens.ts packages/ui/src/theme/tokens.ts
git mv apps/mobile/src/theme/tokens.test.ts packages/ui/src/theme/tokens.test.ts
for f in Button IconButton NetChip NetDot StatsOverlay ErrorOverlay BottomNav InstanceRow StreamToolbar SwitchDrawer SettingsModal; do
  git mv "apps/mobile/src/components/$f.tsx" "packages/ui/src/components/$f.tsx"
  [ -f "apps/mobile/src/components/$f.test.tsx" ] && git mv "apps/mobile/src/components/$f.test.tsx" "packages/ui/src/components/$f.test.tsx"
done
```

(`Button.test.tsx`, `IconButton.test.tsx`, `StatsOverlay.test.tsx`, `ErrorOverlay.test.tsx`, `BottomNav.test.tsx`, `InstanceRow.test.tsx`, `StreamToolbar.test.tsx`, `SwitchDrawer.test.tsx` may not all exist — the earlier directory listing confirmed `NetDot.test.tsx` and `SettingsModal.test.tsx` exist; the `[ -f ... ]` guard skips any that don't.)

- [ ] **Step 2: Fix intra-package import paths**

Every moved component that imports `../theme/tokens` keeps working unchanged (same relative shape: `components/X.tsx` → `../theme/tokens` from its new location `packages/ui/src/components/X.tsx`). `SettingsModal.tsx`'s `import { Button } from "./Button";` also keeps working unchanged (same directory).

`InstanceRow.tsx`'s `import type { Instance } from "../api/client";` must change to `import type { Instance } from "@wc/core";`.

- [ ] **Step 3: Run the moved component tests from their new location**

Run: `cd packages/ui && npx jest src/theme/tokens.test.ts src/components/NetDot.test.tsx src/components/SettingsModal.test.tsx`
Expected: PASS.

- [ ] **Step 4: Export everything from `packages/ui/src/index.ts`**

```ts
export * from "./theme/tokens";
export * from "./components/Button";
export * from "./components/IconButton";
export * from "./components/NetChip";
export * from "./components/NetDot";
export * from "./components/StatsOverlay";
export * from "./components/ErrorOverlay";
export * from "./components/BottomNav";
export * from "./components/InstanceRow";
export * from "./components/StreamToolbar";
export * from "./components/SwitchDrawer";
export * from "./components/SettingsModal";
```

- [ ] **Step 5: Run the full ui suite and commit**

Run: `npm run test:ui`
Expected: PASS.

```bash
git add packages/ui apps/mobile/src/theme apps/mobile/src/components
git commit -m "feat(ui): extract shared theme and primitive components into @wc/ui"
```

---

### Task 6: Extract `Login`, `ServerSetup`, and `InstanceList` screens into `@wc/ui`

**Files:**
- Create: `packages/ui/src/screens/Login.tsx`, `ServerSetup.tsx`, `InstanceList.tsx` (from `apps/mobile/src/screens/*`)
- Modify: `packages/ui/src/index.ts` (add exports)
- Test: `Login.test.tsx`, `InstanceList.test.tsx`, `ServerSetup.test.tsx` (moved)

**Interfaces:**
- Consumes: `useServer`, `signInWithPassword`, `signUpWithPassword`, `normalizeBase`, `Instance` from `@wc/core`; `theme`, `Button`, `InstanceRow`, `NetChip`, `BottomNav` from `@wc/ui` (Task 5).
- Produces: `@wc/ui` exports `Login`, `ServerSetup`, `InstanceList` — each still takes the same `{ navigation }`/`{ route, navigation }`-shaped props apps/mobile's `Root.tsx` already passes (React Navigation's screen prop contract is unchanged by this move).

- [ ] **Step 1: Move the three screens and their tests**

```bash
mkdir -p packages/ui/src/screens
git mv apps/mobile/src/screens/Login.tsx packages/ui/src/screens/Login.tsx
git mv apps/mobile/src/screens/Login.test.tsx packages/ui/src/screens/Login.test.tsx
git mv apps/mobile/src/screens/ServerSetup.tsx packages/ui/src/screens/ServerSetup.tsx
git mv apps/mobile/src/screens/ServerSetup.test.tsx packages/ui/src/screens/ServerSetup.test.tsx
git mv apps/mobile/src/screens/InstanceList.tsx packages/ui/src/screens/InstanceList.tsx
git mv apps/mobile/src/screens/InstanceList.test.tsx packages/ui/src/screens/InstanceList.test.tsx
```

- [ ] **Step 2: Fix import paths in each moved file**

`Login.tsx`: `import { theme } from "../theme/tokens";` → `import { theme } from "../theme/tokens";` (unchanged — still same relative shape inside `packages/ui/src`). `import { Button } from "../components/Button";` → unchanged. `import { useServer } from "../api/ServerContext";` → `import { useServer } from "@wc/core";`. `import { signInWithPassword, signUpWithPassword } from "../api/supabaseAuth";` → `import { signInWithPassword, signUpWithPassword } from "@wc/core";`.

`ServerSetup.tsx`: same `theme`/`Button` imports unchanged. `import { useServer } from "../api/ServerContext";` → `@wc/core`. `import { normalizeBase } from "../api/urls";` → `@wc/core`.

`InstanceList.tsx`: `theme`, `InstanceRow`, `NetChip`, `BottomNav` relative imports unchanged (same `packages/ui/src` layout). `import { useServer } from "../api/ServerContext";` → `@wc/core`. `import type { Instance } from "../api/client";` → `@wc/core`.

- [ ] **Step 3: Run the moved screen tests from their new location**

Run: `cd packages/ui && npx jest src/screens/Login.test.tsx src/screens/ServerSetup.test.tsx src/screens/InstanceList.test.tsx`
Expected: PASS.

- [ ] **Step 4: Export from `packages/ui/src/index.ts`**

Add:
```ts
export * from "./screens/Login";
export * from "./screens/ServerSetup";
export * from "./screens/InstanceList";
```

- [ ] **Step 5: Run the full ui suite and commit**

Run: `npm run test:ui`
Expected: PASS.

```bash
git add packages/ui apps/mobile/src/screens
git commit -m "feat(ui): extract Login, ServerSetup, and InstanceList screens into @wc/ui"
```

---

### Task 7: Extract the `Stream` screen with a `VideoView` platform adapter

**Files:**
- Create: `packages/ui/src/video/VideoView.ts` (type-only adapter contract)
- Create: `packages/ui/src/screens/Stream.tsx` (from `apps/mobile/src/screens/Stream.tsx`, adapted)
- Modify: `apps/mobile/src/platform/VideoView.tsx` (mobile's `RTCView`-backed implementation)
- Modify: `packages/ui/src/index.ts` (add exports)
- Test: `packages/ui/src/screens/Stream.test.tsx` (moved from `apps/mobile/src/screens/Stream.test.tsx`, adapted to pass a fake `VideoView`)

**Interfaces:**
- Consumes: `connectWhep`, `WhepSession`, `normalizeCoords`, `makeAdaptive`, `useServer` from `@wc/core`; `theme`, `StreamToolbar`, `SettingsModal`, `SwitchDrawer`, `StatsOverlay`, `ErrorOverlay` from `@wc/ui` (Task 5).
- Produces: `Stream` component now takes two extra required props beyond React Navigation's `{ route, navigation }`: `RTCImpl: any` (passed straight through to `connectWhep`) and `VideoView: React.ComponentType<{ streamURL: string }>`. This is the same shape of change Task 3 made to `connectWhep` itself — the platform (mobile vs. web) supplies its own video-rendering primitive instead of `Stream.tsx` importing `react-native-webrtc`'s `RTCView` directly.

`packages/ui/src/video/VideoView.ts`:
```ts
export type VideoViewProps = { streamURL: string };
export type VideoViewComponent = React.ComponentType<VideoViewProps>;
```

- [ ] **Step 1: Move `Stream.tsx` and its test**

```bash
mkdir -p packages/ui/src/video
git mv apps/mobile/src/screens/Stream.tsx packages/ui/src/screens/Stream.tsx
git mv apps/mobile/src/screens/Stream.test.tsx packages/ui/src/screens/Stream.test.tsx
```

- [ ] **Step 2: Write `VideoView.ts`**

Create the file exactly as shown above under Interfaces.

- [ ] **Step 3: Edit `Stream.tsx` — remove the direct `RTCView` import, accept it as a prop**

Replace:
```tsx
import { RTCView } from "react-native-webrtc";
```
with nothing (deleted) — add instead:
```tsx
import type { VideoViewComponent } from "../video/VideoView";
```

Replace:
```tsx
import { useServer } from "../api/ServerContext";
import { theme } from "../theme/tokens";
import { connectWhep, WhepSession } from "../webrtc/whep";
import { normalizeCoords } from "../input/coords";
import { makeAdaptive } from "../quality/adaptive";
```
with:
```tsx
import { useServer, connectWhep, WhepSession, normalizeCoords, makeAdaptive } from "@wc/core";
import { theme } from "../theme/tokens";
```

(`StreamToolbar`, `SettingsModal`, `SwitchDrawer`, `StatsOverlay`, `ErrorOverlay` relative imports from `../components/*` stay unchanged — same `packages/ui/src` layout.)

Change the component signature:
```tsx
export function Stream({
  route,
  navigation,
  RTCImpl,
  VideoView,
}: {
  route: any;
  navigation: any;
  RTCImpl: any;
  VideoView: VideoViewComponent;
}) {
```

In the `connectWhep({...})` call inside `start()`, add `RTCImpl,` to the options object.

Replace the render line:
```tsx
{streamUrl ? <RTCView streamURL={streamUrl} objectFit="contain" pointerEvents="none" style={{ flex: 1 }} /> : null}
```
with:
```tsx
{streamUrl ? <VideoView streamURL={streamUrl} /> : null}
```

(`objectFit="contain"` and `pointerEvents="none"` move into each platform's own `VideoView` implementation, since those are rendering-primitive-specific props that don't exist on a generic `<video>` element the same way — `objectFit` becomes a CSS `object-fit: contain` style on web.)

- [ ] **Step 4: Update `Stream.test.tsx` to pass fakes for the new required props**

Read the moved `packages/ui/src/screens/Stream.test.tsx`. Wherever it renders `<Stream route={...} navigation={...} />`, add `RTCImpl={FakeRTCPeerConnection}` (reusing whatever fake `RTCPeerConnection` the test already defines for `connectWhep`'s mocking, or a minimal one if it mocks `connectWhep` entirely instead) and `VideoView={(props) => null}` (a no-op stub component, since the test doesn't need to assert on actual video rendering).

- [ ] **Step 5: Run the Stream test to verify it passes**

Run: `cd packages/ui && npx jest src/screens/Stream.test.tsx`
Expected: PASS.

- [ ] **Step 6: Write mobile's `VideoView` implementation**

`apps/mobile/src/platform/VideoView.tsx`:
```tsx
import React from "react";
import { RTCView } from "react-native-webrtc";
import type { VideoViewProps } from "@wc/ui";

export function VideoView({ streamURL }: VideoViewProps) {
  return <RTCView streamURL={streamURL} objectFit="contain" pointerEvents="none" style={{ flex: 1 }} />;
}
```

- [ ] **Step 7: Wire mobile's `Root.tsx` to pass `RTCImpl` and `VideoView` to the `Stream` screen**

Run: `grep -n "Stream" apps/mobile/src/navigation/Root.tsx`

React Navigation's `Stack.Screen` needs the extra props threaded through — either via `initialParams`-adjacent wrapper or a render-prop wrapping `Stream`. Add a thin wrapper component in `Root.tsx`:

```tsx
import { RTCPeerConnection } from "react-native-webrtc";
import { Stream } from "@wc/ui";
import { VideoView } from "../platform/VideoView";

function StreamScreen(props: any) {
  return <Stream {...props} RTCImpl={RTCPeerConnection} VideoView={VideoView} />;
}
```

and use `StreamScreen` in place of the direct `Stream` import in the `Stack.Screen` registration. Update the top-level `import { Stream } from "../screens/Stream";` to `import { Stream } from "@wc/ui";` (consumed only by the new wrapper).

- [ ] **Step 8: Export from `packages/ui/src/index.ts`**

Add:
```ts
export * from "./video/VideoView";
export * from "./screens/Stream";
```

- [ ] **Step 9: Run both the ui and the mobile suites, then commit**

Run: `npm run test:ui && (cd apps/mobile && npx jest)`
Expected: both PASS.

```bash
git add packages/ui apps/mobile
git commit -m "feat(ui): extract Stream screen with a VideoView platform adapter"
```

---

### Task 8: Delete mobile's now-dead local duplicates and confirm the relocated app is fully on shared packages

**Files:**
- Modify: `apps/mobile/src/navigation/Root.tsx` (confirm all screen imports are from `@wc/ui`)
- Delete: any remaining files under `apps/mobile/src/{screens,components,theme}` not already moved by Tasks 5-7
- Test: full `apps/mobile` jest suite

**Interfaces:**
- Consumes: nothing new — this task is verification and cleanup of Tasks 4-7's work.

- [ ] **Step 1: Confirm no dangling local screen/component files remain**

Run: `find apps/mobile/src/screens apps/mobile/src/components apps/mobile/src/theme -type f 2>/dev/null`
Expected output: only `apps/mobile/src/platform/VideoView.tsx` and `apps/mobile/src/platform/storage.ts` should remain under `apps/mobile/src` outside `navigation/` and `App.tsx`/entry files — everything else was moved to `packages/ui` in Tasks 5-7. If any `.tsx`/`.ts` files still exist under `screens/`, `components/`, or `theme/`, that means Tasks 5-7 missed them — move them into the matching `packages/ui/src/*` location using the same pattern (git mv, fix relative-to-`@wc/core` imports, re-export from `packages/ui/src/index.ts`) before continuing.

- [ ] **Step 2: Update `Root.tsx`'s screen imports to `@wc/ui`**

Run: `grep -n "^import" apps/mobile/src/navigation/Root.tsx`

Replace:
```tsx
import { ServerSetup } from "../screens/ServerSetup";
import { Login } from "../screens/Login";
import { InstanceList } from "../screens/InstanceList";
```
with:
```tsx
import { ServerSetup, Login, InstanceList } from "@wc/ui";
```

(`Stream` is already imported from `@wc/ui` per Task 7 Step 7.) `import { useServer } from "../api/ServerContext";` → `import { useServer } from "@wc/core";`.

- [ ] **Step 3: Run the full mobile suite**

Run: `cd apps/mobile && npx jest`
Expected: PASS, same test count as Task 4's baseline.

- [ ] **Step 4: Run the full `@wc/ui` and `@wc/core` suites once more**

Run: `npm run test:core && npm run test:ui`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/mobile
git commit -m "refactor(mobile): finish cutover to @wc/core and @wc/ui, remove remaining local duplicates"
```

---

### Task 9: Build `apps/web` (Next.js) to feature parity with `src/client`

**Files:**
- Create: `apps/web/package.json`
- Create: `apps/web/next.config.js`
- Create: `apps/web/tsconfig.json`
- Create: `apps/web/jest.config.js`
- Create: `apps/web/src/platform/storage.ts` (web's `SecureStorageAdapter`, `localStorage`-backed)
- Create: `apps/web/src/platform/VideoView.tsx` (web's `<video>`-backed `VideoView`)
- Create: `apps/web/src/app/layout.tsx`
- Create: `apps/web/src/app/setup/page.tsx` (→ `ServerSetup`)
- Create: `apps/web/src/app/login/page.tsx` (→ `Login`)
- Create: `apps/web/src/app/instances/page.tsx` (→ `InstanceList`)
- Create: `apps/web/src/app/stream/[serial]/page.tsx` (→ `Stream`)
- Test: `apps/web/src/platform/storage.test.ts`, `apps/web/src/app/stream/[serial]/page.test.tsx` (at minimum — the `RTCImpl`/`VideoView` wiring)
- Modify: `.github/workflows/frontend-packages.yml` (add `apps/web` test step)

**Interfaces:**
- Consumes: everything `@wc/core` and `@wc/ui` export (Tasks 2-7): `ServerProvider`, `useServer`, `Login`, `ServerSetup`, `InstanceList`, `Stream`, `SecureStorageAdapter`, `VideoViewComponent`.
- Produces: a Next.js app served statically, one route per screen, matching `src/client`'s current pages (login/server-setup gate, instance list, stream view).

- [ ] **Step 1: Scaffold the Next.js app**

`apps/web/package.json`:
```json
{
  "name": "@wc/web",
  "version": "0.0.0",
  "private": true,
  "scripts": {
    "dev": "next dev",
    "build": "next build",
    "test": "jest"
  },
  "dependencies": {
    "@wc/core": "*",
    "@wc/ui": "*",
    "next": "^15.0.0",
    "react": "19.2.3",
    "react-dom": "19.2.3",
    "react-native-web": "^0.19.13"
  },
  "devDependencies": {
    "@testing-library/react": "^16.0.1",
    "@types/jest": "^29.5.14",
    "@types/react": "~19.2.2",
    "jest": "^29.7.0",
    "jest-environment-jsdom": "^29.7.0",
    "ts-jest": "^29.2.5",
    "typescript": "~6.0.3"
  }
}
```

`apps/web/next.config.js` — alias `react-native` to `react-native-web` so `@wc/ui`'s RN-primitive components render on web:
```js
/** @type {import('next').NextConfig} */
const path = require("path");

module.exports = {
  output: "export",
  transpilePackages: ["@wc/core", "@wc/ui"],
  webpack: (config) => {
    config.resolve.alias = {
      ...config.resolve.alias,
      "react-native$": "react-native-web",
    };
    config.resolve.extensions = [".web.js", ".web.ts", ".web.tsx", ...config.resolve.extensions];
    return config;
  },
};
```

`apps/web/tsconfig.json`:
```json
{
  "compilerOptions": {
    "target": "ES2020",
    "lib": ["dom", "ES2020"],
    "module": "ESNext",
    "moduleResolution": "Bundler",
    "jsx": "react-jsx",
    "strict": true,
    "esModuleInterop": true,
    "skipLibCheck": true,
    "noEmit": true
  },
  "include": ["src"]
}
```

`apps/web/jest.config.js`:
```js
module.exports = {
  preset: "ts-jest",
  testEnvironment: "jsdom",
};
```

Add `"apps/web"` to the root `package.json`'s `workspaces` array if Task 1 didn't already include it (it did — confirm, don't duplicate).

- [ ] **Step 2: Write web's `SecureStorageAdapter`**

`apps/web/src/platform/storage.ts`:
```ts
import type { SecureStorageAdapter } from "@wc/core";

function makeLocalStorageAdapter(): SecureStorageAdapter {
  return {
    getItem: async (key) => (typeof window === "undefined" ? null : window.localStorage.getItem(key)),
    setItem: async (key, value) => { if (typeof window !== "undefined") window.localStorage.setItem(key, value); },
    deleteItem: async (key) => { if (typeof window !== "undefined") window.localStorage.removeItem(key); },
  };
}

export const plainStorage = makeLocalStorageAdapter();
export const secureStorage = makeLocalStorageAdapter();
```

- [ ] **Step 3: Write the failing test for web's storage adapter**

`apps/web/src/platform/storage.test.ts`:
```ts
import { plainStorage, secureStorage } from "./storage";

beforeEach(() => { window.localStorage.clear(); });

test("plainStorage round-trips through window.localStorage", async () => {
  await plainStorage.setItem("k", "v");
  expect(await plainStorage.getItem("k")).toBe("v");
  await plainStorage.deleteItem("k");
  expect(await plainStorage.getItem("k")).toBeNull();
});

test("secureStorage round-trips through window.localStorage", async () => {
  await secureStorage.setItem("k", "v");
  expect(await secureStorage.getItem("k")).toBe("v");
});
```

Run: `cd apps/web && npx jest src/platform/storage.test.ts`
Expected: PASS (jsdom provides `window.localStorage`).

- [ ] **Step 4: Write web's `VideoView`**

`apps/web/src/platform/VideoView.tsx`:
```tsx
import React, { useEffect, useRef } from "react";
import type { VideoViewProps } from "@wc/ui";

export function VideoView({ streamURL }: VideoViewProps) {
  const ref = useRef<HTMLVideoElement>(null);
  useEffect(() => {
    if (ref.current) ref.current.srcObject = (streamURL as unknown) as MediaProvider;
  }, [streamURL]);
  return (
    <video
      ref={ref}
      autoPlay
      playsInline
      muted
      style={{ width: "100%", height: "100%", objectFit: "contain" }}
    />
  );
}
```

(Note: `Stream`'s `VideoViewProps.streamURL` is typed as `string` per Task 7, matching mobile's `RTCView` which takes a stream URL string. On web, `MediaStream` objects — not URL strings — are assigned to `video.srcObject`. This is a real platform difference the `Stream` screen's `onStream` callback surfaces upstream: web's wiring in Step 6 below must pass the raw `MediaStream` through, not a `.toURL()` string. Widen `VideoViewProps.streamURL` in `packages/ui/src/video/VideoView.ts` to `string | MediaStream` before using it here, and update `apps/mobile/src/platform/VideoView.tsx`'s prop type accordingly — mobile still only ever receives a string.)

- [ ] **Step 5: Update `VideoViewProps` for the string-or-MediaStream split**

Edit `packages/ui/src/video/VideoView.ts`:
```ts
export type VideoViewProps = { streamURL: string | MediaStream };
export type VideoViewComponent = React.ComponentType<VideoViewProps>;
```

Run: `npm run test:ui` — expected PASS (no test asserted on the narrower type).

- [ ] **Step 6: Write the app-router pages**

`apps/web/src/app/layout.tsx`:
```tsx
"use client";
import React from "react";
import { ServerProvider } from "@wc/core";
import { plainStorage, secureStorage } from "../platform/storage";

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        <ServerProvider plainStorage={plainStorage} secureStorage={secureStorage}>
          {children}
        </ServerProvider>
      </body>
    </html>
  );
}
```

`apps/web/src/app/setup/page.tsx`:
```tsx
"use client";
import { ServerSetup } from "@wc/ui";
import { useRouter } from "next/navigation";

export default function SetupPage() {
  const router = useRouter();
  return <ServerSetup navigation={{ navigate: (route: string) => router.push(`/${route.toLowerCase()}`) }} />;
}
```

`apps/web/src/app/login/page.tsx`:
```tsx
"use client";
import { Login } from "@wc/ui";
import { useRouter } from "next/navigation";

export default function LoginPage() {
  const router = useRouter();
  return <Login navigation={{ navigate: (route: string) => router.push(`/${route.toLowerCase()}`) }} />;
}
```

`apps/web/src/app/instances/page.tsx`:
```tsx
"use client";
import { InstanceList } from "@wc/ui";
import { useRouter } from "next/navigation";

export default function InstancesPage() {
  const router = useRouter();
  return (
    <InstanceList
      navigation={{
        navigate: (route: string, params?: any) =>
          router.push(route === "Stream" ? `/stream/${params.serial}` : `/${route.toLowerCase()}`),
      }}
    />
  );
}
```

`apps/web/src/app/stream/[serial]/page.tsx`:
```tsx
"use client";
import { Stream } from "@wc/ui";
import { useRouter, useParams } from "next/navigation";
import { VideoView } from "../../../platform/VideoView";

export default function StreamPage() {
  const router = useRouter();
  const params = useParams<{ serial: string }>();
  return (
    <Stream
      route={{ params: { serial: params.serial, title: params.serial } }}
      navigation={{
        navigate: (route: string) => router.push(`/${route.toLowerCase()}`),
        setParams: (p: any) => router.push(`/stream/${p.serial}`),
      }}
      RTCImpl={typeof window !== "undefined" ? window.RTCPeerConnection : undefined}
      VideoView={VideoView}
    />
  );
}
```

- [ ] **Step 7: Write the failing test for the Stream page's platform wiring**

`apps/web/src/app/stream/[serial]/page.test.tsx`:
```tsx
import { render } from "@testing-library/react";
import StreamPage from "./page";

jest.mock("next/navigation", () => ({
  useRouter: () => ({ push: jest.fn() }),
  useParams: () => ({ serial: "adb:emulator-5554" }),
}));

test("StreamPage renders without a platform-adapter crash", () => {
  expect(() => render(<StreamPage />)).not.toThrow();
});
```

Run: `cd apps/web && npx jest src/app/stream/\[serial\]/page.test.tsx`
Expected: PASS once Steps 1-6 are in place — this is a smoke test confirming `RTCImpl`/`VideoView` wiring doesn't crash on mount, not full WHEP behavior (that's covered by `@wc/core`'s own `whep.test.ts` from Task 3).

- [ ] **Step 8: Run the full web suite**

Run: `npm run install && cd apps/web && npx jest`
Expected: PASS.

- [ ] **Step 9: Manual parity check against `src/client`**

Run: `uv run python src/main.py` (starts FastAPI, still serving `src/client` — unchanged by this task) in one terminal; in another, `cd apps/web && npm run dev`. Compare screen-by-screen against the running `src/client` PWA: server-setup gate, login, instance list, stream view with touch/drag/scroll input and quality-tier picker. Note any visual/behavioral gap in this task's own notes for Task 10 to address before cutover — do not proceed to Task 10 with an unresolved gap.

- [ ] **Step 10: Wire CI**

Edit `.github/workflows/frontend-packages.yml`, add after the `test:ui` step:
```yaml
      - run: npm test -w apps/web
```

- [ ] **Step 11: Commit**

```bash
git add apps/web packages/ui/src/video/VideoView.ts .github/workflows/frontend-packages.yml
git commit -m "feat(web): build apps/web (Next.js) to feature parity with src/client"
```

---

### Task 10: Desktop pywebview shell, final cutover, and cleanup

**Files:**
- Create: `apps/desktop/tray.py` (moved from `src/gui/tray.py`, adapted)
- Create: `apps/desktop/window.py` (new — pywebview window management)
- Modify: `src/main.py` (wherever it currently invokes `src/gui/tray.py`'s `TrayIcon` and opens the system browser)
- Modify: `pyproject.toml` / `requirements` (add `pywebview` dependency — match whichever dependency file `uv` manages in this repo)
- Modify: `src/server/app.py` (swap `CLIENT_DIR` static mount target to `apps/web`'s exported build output)
- Modify: `src/config.py` (add a `WEB_BUILD_DIR` pointing at `apps/web/out`, bump `VERSION`)
- Delete: `src/client/` (entire directory — superseded by `apps/web`)
- Delete: any remaining `apps/mobile/src/api|webrtc|input|quality` files left behind (should be none after Task 8)
- Modify: `docs/PROJECT_CONTEXT.md` (Repo layout section — replace `src/client`/`mobile/` description with the new `apps/`/`packages/` structure)
- Modify: `.github/workflows/build.yml` (build `apps/web` before the installer step, so `apps/web/out` exists for packaging)
- Test: `src/tests/test_gui_tray.py` or equivalent (locate the existing tray test file first)

**Interfaces:**
- Consumes: `apps/web`'s static export output directory (`apps/web/out`, per `next.config.js`'s `output: "export"` from Task 9).

- [ ] **Step 1: Build `apps/web`'s static export and confirm its output path**

Run: `cd apps/web && npm run build`
Expected: produces `apps/web/out/` containing `index.html` and static assets (Next.js `output: "export"` behavior).

- [ ] **Step 2: Add `pywebview` to the Python dependency file**

Run: `grep -n "pyinstaller\|pystray\|pillow" pyproject.toml` to find the dependency file's existing pattern, then add `pywebview` alongside `pystray` in the same list, matching its existing version-pin style.

Run: `uv sync`

- [ ] **Step 3: Move `tray.py` into `apps/desktop/`**

```bash
mkdir -p apps/desktop
git mv src/gui/tray.py apps/desktop/tray.py
```

Update `apps/desktop/tray.py`'s `from config import ASSETS_DIR` — confirm this still resolves given the new location; if `src/` is on `PYTHONPATH`/`sys.path` at runtime (check how `src/main.py` currently imports `src.gui.tray`), `apps/desktop` needs the same path setup. Match whatever import mechanism `src/main.py` already uses for `src/gui/tray.py` before the move.

- [ ] **Step 4: Write `apps/desktop/window.py`**

```python
# apps/desktop/window.py
import webview


class DesktopWindow:
    def __init__(self, url: str):
        self._url = url
        self._window = None

    def show(self):
        if self._window is not None:
            self._window.show()
            return
        self._window = webview.create_window("WindowControl", self._url, width=1100, height=750)

    def start(self):
        webview.start()
```

- [ ] **Step 5: Write the failing test for `DesktopWindow`**

`apps/desktop/test_window.py`:
```python
from unittest.mock import MagicMock, patch
from window import DesktopWindow


def test_show_creates_window_once():
    with patch("window.webview") as mock_webview:
        mock_webview.create_window.return_value = MagicMock()
        w = DesktopWindow("http://127.0.0.1:8000")
        w.show()
        w.show()
        mock_webview.create_window.assert_called_once_with(
            "WindowControl", "http://127.0.0.1:8000", width=1100, height=750
        )
```

Run: `uv run pytest apps/desktop/test_window.py -v`
Expected: FAIL if `apps/desktop` isn't yet on the test discovery path — add `apps/desktop/conftest.py` (empty file, or matching whatever `tests/` root conftest pattern `src/tests/` already uses) so pytest can import `window` directly; then rerun.

- [ ] **Step 6: Run it to confirm PASS**

Run: `uv run pytest apps/desktop/test_window.py -v`
Expected: PASS.

- [ ] **Step 7: Wire `src/main.py`'s tray "Show" action to open the pywebview window instead of the system browser**

Run: `grep -n "webbrowser\|TrayIcon\|tray" src/main.py`

Replace whatever `on_show` callback currently calls (e.g. `webbrowser.open(...)`) with `DesktopWindow(local_url).show()`, importing `DesktopWindow` from `apps.desktop.window` (adjust the import to match this repo's actual module layout/`sys.path` setup — mirror how `src/main.py` already imports `src.gui.tray.TrayIcon` today, since `apps/desktop/tray.py` needs the same treatment after Step 3's move).

- [ ] **Step 8: Swap the FastAPI static mount to `apps/web`'s build output**

Edit `src/config.py` — add:
```python
WEB_BUILD_DIR = os.path.join(BASE_PATH, "..", "apps", "web", "out")
```
(adjust the relative path to match `BASE_PATH`'s actual anchor — confirm by reading `src/config.py`'s existing `CLIENT_DIR`/`BASE_PATH` definitions before writing this line, since `BASE_PATH` differs between dev and PyInstaller-frozen builds).

Edit `src/server/app.py`:
- Line ~15: `from config import CLIENT_DIR, STUN_PORT, TIER_ORDER` → `from config import WEB_BUILD_DIR, STUN_PORT, TIER_ORDER`
- Line ~288: `html_path = os.path.join(CLIENT_DIR, "index.html")` → `html_path = os.path.join(WEB_BUILD_DIR, "index.html")`
- Lines ~296-298 (VERSION cache-busting on `.js`/`.css` URLs): confirm Next.js's exported `index.html` still has replaceable `.js"`/`.css"` asset-URL patterns; if Next.js's static export instead uses content-hashed filenames (it does, by default — e.g. `_next/static/chunks/main-<hash>.js`), this cache-busting block is now redundant (the hash already busts the cache) and should be deleted rather than adapted. Verify by inspecting `apps/web/out/index.html`'s actual `<script src=...>` output from Step 1 before deciding.
- Line ~415-416: `if os.path.isdir(CLIENT_DIR): app.mount("/static", StaticFiles(directory=CLIENT_DIR), name="static")` → `if os.path.isdir(WEB_BUILD_DIR): app.mount("/static", StaticFiles(directory=WEB_BUILD_DIR), name="static")` (or mount the whole `WEB_BUILD_DIR` at `/` instead of `/static` — check `apps/web/out`'s asset path structure from Step 1 to decide which matches Next.js's emitted `<script>`/`<link>` paths without further URL rewriting).

- [ ] **Step 9: Run the Python test suite**

Run: `uv run pytest tests/ -v`
Expected: same baseline as `HANDOFF.md`'s most recent entry (432 passed / 2 pre-existing unrelated failures / 1 skipped / 2 pre-existing collection errors), plus the new `apps/desktop/test_window.py` passing.

- [ ] **Step 10: Manual smoke test**

Run: `uv run python src/main.py`, open the tray's "Show" item, confirm a pywebview window opens showing the instance list (or login/setup gate, per current auth state) rendered from `apps/web/out`. Exercise login, instance selection, and stream view with real input, per Task 9 Step 9's parity notes — resolve any gap found there now, before deleting `src/client`.

- [ ] **Step 11: Delete `src/client/`**

```bash
git rm -r src/client
```

- [ ] **Step 12: Bump `VERSION` in `src/config.py`**

Per this repo's standing rule (`CLAUDE.md`): any frontend asset change bumps `VERSION`. Increment the patch component of the existing `VERSION = "2.3.30"`.

- [ ] **Step 13: Update `docs/PROJECT_CONTEXT.md`'s Repo layout section**

Replace the `src/` (client-serving) and `mobile/` bullets with a description of the new `apps/{web,mobile,desktop}` + `packages/{core,ui}` structure, matching this plan's Architecture section. Keep the `engine/` and `infra/` bullets unchanged.

- [ ] **Step 14: Update `.github/workflows/build.yml` to build `apps/web` before packaging**

Run: `grep -n "PyInstaller\|installer\|npm" .github/workflows/build.yml`

Add a step before the PyInstaller/installer step that runs `npm install && npm run build -w apps/web` (or equivalent, matching this workflow's existing job structure), so `apps/web/out` exists for `build/build.bat`/PyInstaller to bundle — check whether `build/build.bat` (referenced in `docs/PROJECT_CONTEXT.md`'s "Things NOT to do" section re: `src/assets/engine/`) also needs a matching step to copy `apps/web/out` into the frozen app's served path; if so, add it there too, mirroring how it already stages `src/assets/engine/`.

- [ ] **Step 15: Run the full verification sweep**

Run: `uv run pytest tests/ -v && npm run test:core && npm run test:ui && (cd apps/web && npx jest) && (cd apps/mobile && npx jest)`
Expected: all green, matching each suite's baseline recorded across Tasks 1-9.

- [ ] **Step 16: Commit**

```bash
git add -A
git commit -m "feat(desktop): pywebview shell, cut FastAPI over to apps/web, delete src/client"
```

---

## Self-Review Notes

- **Spec coverage:** every spec section has a task — monorepo/npm workspaces (Task 1), `packages/core` (Tasks 2-3), `apps/mobile` relocation (Task 4), `packages/ui` primitives/screens/Stream (Tasks 5-7), mobile cutover completion (Task 8), `apps/web` (Task 9), `apps/desktop` + final single-cutover deletion (Task 10). CI wiring happens in the same task that introduces each new suite (Tasks 1, 9), per the spec's explicit anti-rot requirement.
- **Real code audited before writing steps:** `mobile/src/webrtc/whep.ts`, `mobile/src/api/ServerContext.tsx`, and `mobile/src/screens/Stream.tsx` were read in full to design the `RTCImpl`/`VideoView`/`SecureStorageAdapter` platform-adapter boundaries accurately — these three files carry all of this migration's real platform-coupling risk (react-native-webrtc, AsyncStorage/SecureStore, RTCView/gesture handling). Other files (api/urls.ts, api/client.ts, quality/*, remaining components) were confirmed via import-line greps to have no platform coupling, so their tasks specify plain moves.
- **Known gaps flagged as in-task decisions, not deferred vaguely:** the `streamURL: string` vs. `MediaStream` type split between mobile and web (Task 9 Step 4-5), whether Next.js's content-hashed export makes the old `VERSION` cache-busting redundant (Task 10 Step 8), and whether `/static` or `/` is the right mount point for the new build (same step) are each resolved by a concrete inspection step at implementation time, not left as unexamined assumptions.
