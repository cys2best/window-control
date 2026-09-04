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
