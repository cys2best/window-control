import React from "react";
import { render, waitFor, act } from "@testing-library/react";
import { ServerProvider, useServer } from "./ServerContext";
import { deviceTokenKey } from "./pairing";
import type { SecureStorageAdapter } from "./storage";
import { remoteDeviceTokenKey, type ServerTarget } from "./target";
import { FakeSocket, options as remoteOptions, installationId } from "../remote/testUtils";

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

test("remote setTarget returns the provider-owned client and local switch preserves the remote token", async () => {
  FakeSocket.sockets = [];
  const plain = makeMemoryStorage(), secure = makeMemoryStorage();
  const remote: ServerTarget = { kind: "remote", serviceUrl: "https://relay.example", installationId };
  let context: ReturnType<typeof useServer> | undefined;
  function Capture() { context = useServer(); return <span>{context.target?.kind}</span>; }
  render(<ServerProvider plainStorage={plain} secureStorage={secure} remoteOptions={remoteOptions()}><Capture /></ServerProvider>);
  await waitFor(() => expect(context?.ready).toBe(true));
  let client: unknown;
  await act(async () => { client = await context!.setTarget(remote, "remote-token"); });
  expect(client).toBe(context!.client);
  expect(FakeSocket.sockets).toHaveLength(1);
  expect(await plain.getItem("wc_remote_target")).toBe(JSON.stringify(remote));
  expect(await secure.getItem(remoteDeviceTokenKey(remote.serviceUrl, installationId))).toBe("remote-token");
  await act(async () => { await context!.setServer("http://host:8000", "local-token"); });
  expect(await plain.getItem("wc_remote_target")).toBeNull();
  expect(await secure.getItem(remoteDeviceTokenKey(remote.serviceUrl, installationId))).toBe("remote-token");
  expect(FakeSocket.sockets[0].readyState).toBe(3);
});

test("late unauthorized from A cannot delete B's saved token", async () => {
  FakeSocket.sockets = [];
  const plain = makeMemoryStorage(), secure = makeMemoryStorage();
  let context: ReturnType<typeof useServer> | undefined;
  function Capture() { context = useServer(); return null; }
  render(<ServerProvider plainStorage={plain} secureStorage={secure} remoteOptions={remoteOptions()}><Capture /></ServerProvider>);
  await waitFor(() => expect(context?.ready).toBe(true));
  const a: ServerTarget = { kind: "remote", serviceUrl: "https://relay.example", installationId };
  const b: ServerTarget = { kind: "remote", serviceUrl: "https://relay.example", installationId: "b".repeat(32) };
  await act(async () => { await context!.setTarget(a, "A"); });
  const old = FakeSocket.sockets[0]; old.open(); old.reply({ authenticated: true, viewer_id: "v" });
  await act(async () => { await context!.setTarget(b, "B"); });
  old.error("not_paired"); await flush();
  expect(context!.target).toEqual(b);
  expect(context!.authToken).toBe("B");
  expect(await secure.getItem(remoteDeviceTokenKey(b.serviceUrl, b.installationId))).toBe("B");
});

test("ordered marker writes leave the newest target on disk", async () => {
  FakeSocket.sockets = [];
  let release!: () => void;
  let hold = false;
  const stored = new Map<string, string>();
  const plain: SecureStorageAdapter = {
    getItem: async k => stored.get(k) ?? null,
    setItem: async (k, v) => { if (k === "wc_remote_target" && hold) { hold = false; await new Promise<void>(r => { release = r; }); } stored.set(k, v); },
    deleteItem: async k => { stored.delete(k); },
  };
  const secure = makeMemoryStorage();
  let context: ReturnType<typeof useServer> | undefined;
  function Capture() { context = useServer(); return null; }
  render(<ServerProvider plainStorage={plain} secureStorage={secure} remoteOptions={remoteOptions()}><Capture /></ServerProvider>);
  await waitFor(() => expect(context?.ready).toBe(true));
  const a: ServerTarget = { kind: "remote", serviceUrl: "https://relay.example", installationId };
  const b: ServerTarget = { kind: "remote", serviceUrl: "https://relay.example", installationId: "b".repeat(32) };
  hold = true;
  let first!: Promise<unknown>;
  act(() => { first = context!.setTarget(a, "A"); });
  await waitFor(() => expect(release).toBeDefined());
  let second!: Promise<unknown>;
  act(() => { second = context!.setTarget(b, "B"); });
  release(); await act(async () => {
    await expect(first).rejects.toThrow("target changed");
    await second;
  });
  expect(stored.get("wc_remote_target")).toBe(JSON.stringify(b));
  expect(context!.target).toEqual(b);
});

test("loads a saved remote target with its own token and probes over its authenticated socket", async () => {
  FakeSocket.sockets = [];
  const plain = makeMemoryStorage(), secure = makeMemoryStorage();
  const remote: ServerTarget = { kind: "remote", serviceUrl: "https://relay.example", installationId };
  await plain.setItem("wc_base", "http://old:8000");
  await plain.setItem("wc_remote_target", JSON.stringify(remote));
  await secure.setItem(remoteDeviceTokenKey(remote.serviceUrl, installationId), "saved-remote");
  let context: ReturnType<typeof useServer> | undefined;
  function Capture() { context = useServer(); return null; }
  render(<ServerProvider plainStorage={plain} secureStorage={secure} remoteOptions={remoteOptions()}><Capture /></ServerProvider>);
  await waitFor(() => expect(context?.ready).toBe(true));
  expect(context!.target).toEqual(remote);
  expect(context!.authToken).toBe("saved-remote");
  expect(FakeSocket.sockets).toHaveLength(1);
  const socket = FakeSocket.sockets[0]; socket.open();
  expect(socket.sent[0]).toMatchObject({ op: "viewer_auth", payload: { token: "saved-remote" } });
});

test("a delayed local probe cannot update reachability after switching remote", async () => {
  FakeSocket.sockets = [];
  let answer!: (value: any) => void;
  global.fetch = jest.fn(() => new Promise(resolve => { answer = resolve; })) as any;
  const plain = makeMemoryStorage(), secure = makeMemoryStorage();
  await plain.setItem("wc_base", "http://old:8000");
  let context: ReturnType<typeof useServer> | undefined;
  function Capture() { context = useServer(); return null; }
  render(<ServerProvider plainStorage={plain} secureStorage={secure} remoteOptions={remoteOptions()}><Capture /></ServerProvider>);
  await waitFor(() => expect(answer).toBeDefined());
  const remote: ServerTarget = { kind: "remote", serviceUrl: "https://relay.example", installationId };
  await act(async () => { await context!.setTarget(remote, "token"); });
  await act(async () => { answer({ ok: true, json: async () => ({ paired: false }) }); });
  expect(context!.target).toEqual(remote);
  expect(context!.paired).toBe(true);
  expect(context!.hostReachability.host).toBe("relay.example");
});

test("missing remote credential restores the saved local token", async () => {
  const plain = makeMemoryStorage(), secure = makeMemoryStorage();
  const remote: ServerTarget = { kind: "remote", serviceUrl: "https://relay.example", installationId };
  await plain.setItem("wc_base", "http://old:8000");
  await plain.setItem("wc_remote_target", JSON.stringify(remote));
  await secure.setItem(deviceTokenKey("http://old:8000"), "local-token");
  let context: ReturnType<typeof useServer> | undefined;
  function Capture() { context = useServer(); return null; }
  render(<ServerProvider plainStorage={plain} secureStorage={secure} remoteOptions={remoteOptions()}><Capture /></ServerProvider>);
  await waitFor(() => expect(context?.ready).toBe(true));
  expect(context!.target).toEqual({ kind: "local", base: "http://old:8000" });
  expect(context!.authToken).toBe("local-token");
});

test("an old same-installation token delete cannot erase a newer pairing", async () => {
  FakeSocket.sockets = [];
  const plain = makeMemoryStorage(), saved = new Map<string, string>();
  let hold = false, release!: () => void;
  const secure: SecureStorageAdapter = {
    getItem: async k => saved.get(k) ?? null,
    setItem: async (k, v) => { saved.set(k, v); },
    deleteItem: async k => {
      if (hold && k.startsWith("wc_remote_device_token.")) {
        hold = false; await new Promise<void>(resolve => { release = resolve; });
      }
      saved.delete(k);
    },
  };
  let context: ReturnType<typeof useServer> | undefined;
  function Capture() { context = useServer(); return null; }
  render(<ServerProvider plainStorage={plain} secureStorage={secure} remoteOptions={remoteOptions()}><Capture /></ServerProvider>);
  await waitFor(() => expect(context?.ready).toBe(true));
  const remote: ServerTarget = { kind: "remote", serviceUrl: "https://relay.example", installationId };
  await act(async () => { await context!.setTarget(remote, "old"); });
  const socket = FakeSocket.sockets[0];
  act(() => { socket.open(); socket.reply({ authenticated: true, viewer_id: "v" }); });
  await waitFor(() => expect(socket.sent.length).toBe(2));
  hold = true;
  act(() => { socket.error("not_paired"); });
  await waitFor(() => expect(release).toBeDefined());
  let next!: Promise<unknown>;
  act(() => { next = context!.setTarget(remote, "new"); });
  release(); await act(async () => { await next; });
  expect(saved.get(remoteDeviceTokenKey(remote.serviceUrl, installationId))).toBe("new");
  expect(context!.authToken).toBe("new");
});

test("a target switch during initial storage reads still loads saved preferences", async () => {
  FakeSocket.sockets = [];
  let release!: (value: string) => void;
  const plain: SecureStorageAdapter = {
    getItem: async k => k === "wc_stream_preferences" ? new Promise<string>(resolve => { release = resolve; }) : null,
    setItem: async () => {}, deleteItem: async () => {},
  };
  const secure = makeMemoryStorage();
  let context: ReturnType<typeof useServer> | undefined;
  function Capture() { context = useServer(); return null; }
  render(<ServerProvider plainStorage={plain} secureStorage={secure} remoteOptions={remoteOptions()}><Capture /></ServerProvider>);
  await waitFor(() => expect(release).toBeDefined());
  const remote: ServerTarget = { kind: "remote", serviceUrl: "https://relay.example", installationId };
  await act(async () => { await context!.setTarget(remote, "token"); });
  act(() => { release(JSON.stringify({ quality: "720", showHudOnConnect: true, haptics: false, hideRailWhilePlaying: false })); });
  await waitFor(() => expect(context!.preferences.quality).toBe("720"));
  expect(context!.target).toEqual(remote);
});

test("failed remote token storage leaves no saved remote target marker", async () => {
  FakeSocket.sockets = [];
  const plain = makeMemoryStorage();
  const secure: SecureStorageAdapter = {
    getItem: async () => null,
    setItem: async k => { if (k.startsWith("wc_remote_device_token.")) throw new Error("storage unavailable"); },
    deleteItem: async () => {},
  };
  let context: ReturnType<typeof useServer> | undefined;
  function Capture() { context = useServer(); return null; }
  render(<ServerProvider plainStorage={plain} secureStorage={secure} remoteOptions={remoteOptions()}><Capture /></ServerProvider>);
  await waitFor(() => expect(context?.ready).toBe(true));
  const priorTarget = context!.target, priorClient = context!.client;
  const remote: ServerTarget = { kind: "remote", serviceUrl: "https://relay.example", installationId };
  await act(async () => { await expect(context!.setTarget(remote, "token")).rejects.toThrow("storage unavailable"); });
  expect(await plain.getItem("wc_remote_target")).toBeNull();
  expect(context!.target).toEqual(priorTarget);
  expect(context!.client).toBe(priorClient);
  expect(FakeSocket.sockets).toHaveLength(0);
});

test("failed replacement keeps the old remote client and its revocation handler", async () => {
  FakeSocket.sockets = [];
  const plain = makeMemoryStorage(), saved = new Map<string, string>();
  const secure: SecureStorageAdapter = {
    getItem: async k => saved.get(k) ?? null,
    setItem: async (k, v) => { if (k.endsWith("." + "b".repeat(32))) throw new Error("storage unavailable"); saved.set(k, v); },
    deleteItem: async k => { saved.delete(k); },
  };
  let context: ReturnType<typeof useServer> | undefined;
  function Capture() { context = useServer(); return null; }
  render(<ServerProvider plainStorage={plain} secureStorage={secure} remoteOptions={remoteOptions()}><Capture /></ServerProvider>);
  await waitFor(() => expect(context?.ready).toBe(true));
  const a: ServerTarget = { kind: "remote", serviceUrl: "https://relay.example", installationId };
  const b: ServerTarget = { kind: "remote", serviceUrl: "https://relay.example", installationId: "b".repeat(32) };
  await act(async () => { await context!.setTarget(a, "A"); });
  const oldClient = context!.client, socket = FakeSocket.sockets[0];
  await act(async () => { await expect(context!.setTarget(b, "B")).rejects.toThrow("storage unavailable"); });
  expect(context!.client).toBe(oldClient);
  expect(context!.target).toEqual(a);
  expect(FakeSocket.sockets).toHaveLength(1);
  act(() => { socket.open(); socket.reply({ authenticated: true, viewer_id: "v" }); });
  await waitFor(() => expect(socket.sent.length).toBe(2));
  act(() => { socket.error("not_paired"); });
  await waitFor(async () => expect(await secure.getItem(remoteDeviceTokenKey(a.serviceUrl, a.installationId))).toBeNull());
  expect(context!.paired).toBe(false);
});

test("overlapping failed replacements retain the active client's revocation authority", async () => {
  FakeSocket.sockets = [];
  const plain = makeMemoryStorage(), saved = new Map<string, string>();
  const rejectors = new Map<string, (error: Error) => void>();
  const secure: SecureStorageAdapter = {
    getItem: async k => saved.get(k) ?? null,
    setItem: (k, v) => k.endsWith("." + "a".repeat(32)) ? Promise.resolve(saved.set(k, v)).then(() => {})
      : new Promise<void>((_, reject) => { rejectors.set(k, reject); }),
    deleteItem: async k => { saved.delete(k); },
  };
  let context: ReturnType<typeof useServer> | undefined;
  function Capture() { context = useServer(); return null; }
  render(<ServerProvider plainStorage={plain} secureStorage={secure} remoteOptions={remoteOptions()}><Capture /></ServerProvider>);
  await waitFor(() => expect(context?.ready).toBe(true));
  const a: ServerTarget = { kind: "remote", serviceUrl: "https://relay.example", installationId };
  const b: ServerTarget = { kind: "remote", serviceUrl: "https://relay.example", installationId: "b".repeat(32) };
  const c: ServerTarget = { kind: "remote", serviceUrl: "https://relay.example", installationId: "c".repeat(32) };
  await act(async () => { await context!.setTarget(a, "A"); });
  const oldClient = context!.client, socket = FakeSocket.sockets[0];
  let bResult!: Promise<unknown>, cResult!: Promise<unknown>;
  act(() => { bResult = context!.setTarget(b, "B").catch(e => e); cResult = context!.setTarget(c, "C").catch(e => e); });
  await waitFor(() => expect(rejectors.size).toBe(2));
  await act(async () => { rejectors.get(remoteDeviceTokenKey(b.serviceUrl, b.installationId))!(new Error("B failed")); await bResult; });
  await act(async () => { rejectors.get(remoteDeviceTokenKey(c.serviceUrl, c.installationId))!(new Error("C failed")); await cResult; });
  expect(context!.target).toEqual(a); expect(context!.client).toBe(oldClient);
  act(() => { socket.open(); socket.reply({ authenticated: true, viewer_id: "v" }); });
  await waitFor(() => expect(socket.sent.length).toBe(2));
  act(() => { socket.error("not_paired"); });
  await waitFor(async () => expect(await secure.getItem(remoteDeviceTokenKey(a.serviceUrl, a.installationId))).toBeNull());
  expect(context!.authToken).toBeNull(); expect(context!.paired).toBe(false);
});

test("an obsolete delayed switch cannot commit after a newer switch fails", async () => {
  FakeSocket.sockets = [];
  const plain = makeMemoryStorage(), saved = new Map<string, string>();
  let releaseB!: () => void;
  const secure: SecureStorageAdapter = {
    getItem: async k => saved.get(k) ?? null,
    setItem: async (k, v) => {
      if (k.endsWith("." + "b".repeat(32))) await new Promise<void>(resolve => { releaseB = resolve; });
      if (k.endsWith("." + "c".repeat(32))) throw new Error("C failed");
      saved.set(k, v);
    },
    deleteItem: async k => { saved.delete(k); },
  };
  let context: ReturnType<typeof useServer> | undefined;
  function Capture() { context = useServer(); return null; }
  render(<ServerProvider plainStorage={plain} secureStorage={secure} remoteOptions={remoteOptions()}><Capture /></ServerProvider>);
  await waitFor(() => expect(context?.ready).toBe(true));
  const a: ServerTarget = { kind: "remote", serviceUrl: "https://relay.example", installationId };
  const b: ServerTarget = { kind: "remote", serviceUrl: "https://relay.example", installationId: "b".repeat(32) };
  const c: ServerTarget = { kind: "remote", serviceUrl: "https://relay.example", installationId: "c".repeat(32) };
  await act(async () => { await context!.setTarget(a, "A"); });
  const oldClient = context!.client;
  let bResult!: Promise<unknown>;
  act(() => { bResult = context!.setTarget(b, "B").catch(e => e); });
  await waitFor(() => expect(releaseB).toBeDefined());
  await act(async () => { await expect(context!.setTarget(c, "C")).rejects.toThrow("C failed"); });
  await act(async () => { releaseB(); expect(await bResult).toMatchObject({ message: "target changed" }); });
  expect(context!.target).toEqual(a); expect(context!.client).toBe(oldClient);
  expect(await plain.getItem("wc_remote_target")).toBe(JSON.stringify(a));
  expect(FakeSocket.sockets).toHaveLength(1);
});

test("old revocation during a same-installation replacement preserves the new token on disk", async () => {
  FakeSocket.sockets = [];
  const marker = new Map<string, string>(), saved = new Map<string, string>();
  let hold = false, release!: () => void;
  const plain: SecureStorageAdapter = {
    getItem: async k => marker.get(k) ?? null,
    setItem: async (k, v) => { if (k === "wc_remote_target" && hold) { hold = false; await new Promise<void>(resolve => { release = resolve; }); } marker.set(k, v); },
    deleteItem: async k => { marker.delete(k); },
  };
  const secure: SecureStorageAdapter = {
    getItem: async k => saved.get(k) ?? null,
    setItem: async (k, v) => { saved.set(k, v); },
    deleteItem: async k => { saved.delete(k); },
  };
  let context: ReturnType<typeof useServer> | undefined;
  function Capture() { context = useServer(); return null; }
  render(<ServerProvider plainStorage={plain} secureStorage={secure} remoteOptions={remoteOptions()}><Capture /></ServerProvider>);
  await waitFor(() => expect(context?.ready).toBe(true));
  const remote: ServerTarget = { kind: "remote", serviceUrl: "https://relay.example", installationId };
  await act(async () => { await context!.setTarget(remote, "old"); });
  const socket = FakeSocket.sockets[0];
  act(() => { socket.open(); socket.reply({ authenticated: true, viewer_id: "v" }); });
  await waitFor(() => expect(socket.sent.length).toBe(2));
  hold = true;
  let next!: Promise<unknown>;
  act(() => { next = context!.setTarget(remote, "new"); });
  await waitFor(() => expect(release).toBeDefined());
  act(() => { socket.error("not_paired"); });
  await act(async () => { release(); await next; });
  expect(saved.get(remoteDeviceTokenKey(remote.serviceUrl, installationId))).toBe("new");
  expect(context!.authToken).toBe("new");
});

test("failed switch during initial storage reads still completes saved-target loading", async () => {
  FakeSocket.sockets = [];
  let releaseBase!: (value: string) => void;
  let holdBase = true;
  const plain: SecureStorageAdapter = {
    getItem: async k => {
      if (k !== "wc_base") return null;
      if (holdBase) { holdBase = false; return new Promise<string>(resolve => { releaseBase = resolve; }); }
      return "http://saved:8000";
    },
    setItem: async () => {}, deleteItem: async () => {},
  };
  const secure: SecureStorageAdapter = {
    getItem: async k => k === deviceTokenKey("http://saved:8000") ? "local-token" : null,
    setItem: async () => { throw new Error("storage unavailable"); },
    deleteItem: async () => {},
  };
  let context: ReturnType<typeof useServer> | undefined;
  function Capture() { context = useServer(); return null; }
  render(<ServerProvider plainStorage={plain} secureStorage={secure} remoteOptions={remoteOptions()}><Capture /></ServerProvider>);
  await waitFor(() => expect(releaseBase).toBeDefined());
  const remote: ServerTarget = { kind: "remote", serviceUrl: "https://relay.example", installationId };
  await act(async () => { await expect(context!.setTarget(remote, "token")).rejects.toThrow("storage unavailable"); });
  act(() => { releaseBase("http://saved:8000"); });
  await waitFor(() => expect(context!.ready).toBe(true));
  expect(context!.target).toEqual({ kind: "local", base: "http://saved:8000" });
  expect(context!.authToken).toBe("local-token");
});
