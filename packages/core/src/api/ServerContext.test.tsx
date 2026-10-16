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
