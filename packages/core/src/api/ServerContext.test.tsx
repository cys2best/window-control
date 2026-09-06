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
  const { ready, base, authToken, setServer, clearAuth } = useServer();
  return (
    <div>
      <span data-testid="ready">{String(ready)}</span>
      <span data-testid="base">{base ?? ""}</span>
      <span data-testid="token">{authToken ?? ""}</span>
      <button onClick={() => setServer("http://host:8000", "tok")}>set</button>
      <button onClick={() => clearAuth()}>clear</button>
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

test("ServerProvider falls back to EXPO_PUBLIC_API_URL when plainStorage has no wc_base", async () => {
  const originalEnv = process.env;
  try {
    process.env = { ...originalEnv, EXPO_PUBLIC_API_URL: "https://api.example.com" };
    const plain = makeMemoryStorage();
    const secure = makeMemoryStorage();

    const { getByTestId } = render(
      <ServerProvider plainStorage={plain} secureStorage={secure}>
        <Probe />
      </ServerProvider>
    );

    await waitFor(() => expect(getByTestId("ready").textContent).toBe("true"));
    expect(getByTestId("base").textContent).toBe("https://api.example.com");
  } finally {
    process.env = originalEnv;
  }
});

test("ServerProvider falls back to NEXT_PUBLIC_API_URL when EXPO_PUBLIC_API_URL is unset", async () => {
  const originalEnv = process.env;
  try {
    process.env = { ...originalEnv, EXPO_PUBLIC_API_URL: undefined, NEXT_PUBLIC_API_URL: "https://next.example.com" };
    const plain = makeMemoryStorage();
    const secure = makeMemoryStorage();

    const { getByTestId } = render(
      <ServerProvider plainStorage={plain} secureStorage={secure}>
        <Probe />
      </ServerProvider>
    );

    await waitFor(() => expect(getByTestId("ready").textContent).toBe("true"));
    expect(getByTestId("base").textContent).toBe("https://next.example.com");
  } finally {
    process.env = originalEnv;
  }
});

test("ServerProvider prefers persisted wc_base over environment fallback", async () => {
  const originalEnv = process.env;
  try {
    process.env = { ...originalEnv, EXPO_PUBLIC_API_URL: "https://fallback.example.com" };
    const plain = makeMemoryStorage();
    const secure = makeMemoryStorage();
    await plain.setItem("wc_base", "https://persisted.example.com");

    const { getByTestId } = render(
      <ServerProvider plainStorage={plain} secureStorage={secure}>
        <Probe />
      </ServerProvider>
    );

    await waitFor(() => expect(getByTestId("ready").textContent).toBe("true"));
    expect(getByTestId("base").textContent).toBe("https://persisted.example.com");
  } finally {
    process.env = originalEnv;
  }
});

test("ServerProvider trims trailing slashes from environment fallback", async () => {
  const originalEnv = process.env;
  try {
    process.env = { ...originalEnv, NEXT_PUBLIC_API_URL: "https://trailing.example.com///" };
    const plain = makeMemoryStorage();
    const secure = makeMemoryStorage();

    const { getByTestId } = render(
      <ServerProvider plainStorage={plain} secureStorage={secure}>
        <Probe />
      </ServerProvider>
    );

    await waitFor(() => expect(getByTestId("ready").textContent).toBe("true"));
    expect(getByTestId("base").textContent).toBe("https://trailing.example.com");
  } finally {
    process.env = originalEnv;
  }
});

test("ServerProvider purges expired JWT token on load and leaves authToken null", async () => {
  const plain = makeMemoryStorage();
  const secure = makeMemoryStorage();
  const expiredPayload = Buffer.from(JSON.stringify({ exp: Math.floor(Date.now() / 1000) - 60 })).toString("base64url");
  const expiredToken = `eyJhbGciOiJIUzI1NiJ9.${expiredPayload}.signature`;
  await secure.setItem("wc_auth_token", expiredToken);

  const { getByTestId } = render(
    <ServerProvider plainStorage={plain} secureStorage={secure}>
      <Probe />
    </ServerProvider>
  );

  await waitFor(() => expect(getByTestId("ready").textContent).toBe("true"));
  expect(getByTestId("token").textContent).toBe("");
  expect(await secure.getItem("wc_auth_token")).toBeNull();
});

test("clearAuth clears token from secure storage and resets authToken state", async () => {
  const plain = makeMemoryStorage();
  const secure = makeMemoryStorage();
  await secure.setItem("wc_auth_token", "active-tok");

  const { getByTestId, getByText } = render(
    <ServerProvider plainStorage={plain} secureStorage={secure}>
      <Probe />
    </ServerProvider>
  );

  await waitFor(() => expect(getByTestId("ready").textContent).toBe("true"));
  expect(getByTestId("token").textContent).toBe("active-tok");

  await act(async () => { getByText("clear").click(); });

  expect(getByTestId("token").textContent).toBe("");
  expect(await secure.getItem("wc_auth_token")).toBeNull();
});



