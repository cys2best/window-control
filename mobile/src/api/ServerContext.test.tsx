import React from "react";
import { render, waitFor } from "@testing-library/react-native";
import { Text } from "react-native";
import AsyncStorage from "@react-native-async-storage/async-storage";
import CookieManager from "@react-native-cookies/cookies";
import { ServerProvider, useServer } from "./ServerContext";
import { discoverServer } from "./discovery";

jest.mock("@react-native-async-storage/async-storage", () =>
  require("@react-native-async-storage/async-storage/jest/async-storage-mock"));

jest.mock("expo-constants", () => ({
  __esModule: true,
  default: { expoConfig: { extra: { publicUrl: "https://tunnel.koeeru.com" } } },
}));

jest.mock("./discovery", () => ({ discoverServer: jest.fn() }));

const mockDiscoverServer = discoverServer as jest.Mock;

function Probe() {
  const { base, ready, discovering, serverFound, needsLogin } = useServer();
  return (
    <Text>
      {ready ? "ready" : "loading"}:{discovering ? "discovering" : "settled"}:
      {base ?? "none"}:{serverFound ? "found" : "notfound"}:{needsLogin ? "needslogin" : "noauth"}
    </Text>
  );
}

beforeEach(async () => {
  mockDiscoverServer.mockReset();
  (CookieManager.clearAll as jest.Mock).mockClear();
  await AsyncStorage.clear();
});

test("runs discovery on mount and stores the resolved base", async () => {
  mockDiscoverServer.mockResolvedValue({ base: "http://192.168.1.5:8080", status: 200 });
  const { getByText } = await render(<ServerProvider><Probe /></ServerProvider>);
  await waitFor(() => getByText(/ready:settled:http:\/\/192\.168\.1\.5:8080:found:noauth/));
  expect(mockDiscoverServer).toHaveBeenCalledWith(
    "https://tunnel.koeeru.com", expect.objectContaining({ cachedBase: null }));
  expect(await AsyncStorage.getItem("wc_base")).toBe("http://192.168.1.5:8080");
});

test("exposes needsLogin when discovery finds a server that requires auth", async () => {
  mockDiscoverServer.mockResolvedValue({ base: "http://192.168.1.5:8080", status: 401 });
  const { getByText } = await render(<ServerProvider><Probe /></ServerProvider>);
  await waitFor(() => getByText(/needslogin/));
});

test("reflects nothing-reachable as serverFound: false", async () => {
  mockDiscoverServer.mockResolvedValue(null);
  const { getByText } = await render(<ServerProvider><Probe /></ServerProvider>);
  await waitFor(() => getByText(/ready:settled:none:notfound/));
});

test("clears cookies when the resolved base points at a different host than the cached one", async () => {
  await AsyncStorage.setItem("wc_base", "http://old-host:8080");
  mockDiscoverServer.mockResolvedValue({ base: "http://new-host:8080", status: 200 });
  const { getByText } = await render(<ServerProvider><Probe /></ServerProvider>);
  await waitFor(() => getByText(/http:\/\/new-host:8080/));
  expect(CookieManager.clearAll).toHaveBeenCalled();
});

test("does not clear cookies when the resolved base matches the cached host", async () => {
  await AsyncStorage.setItem("wc_base", "http://same-host:8080");
  mockDiscoverServer.mockResolvedValue({ base: "http://same-host:8080", status: 200 });
  const { getByText } = await render(<ServerProvider><Probe /></ServerProvider>);
  await waitFor(() => getByText(/http:\/\/same-host:8080/));
  expect(CookieManager.clearAll).not.toHaveBeenCalled();
});

test("does not clear cookies on first-ever launch (no cached host to compare against)", async () => {
  mockDiscoverServer.mockResolvedValue({ base: "http://new-host:8080", status: 200 });
  const { getByText } = await render(<ServerProvider><Probe /></ServerProvider>);
  await waitFor(() => getByText(/http:\/\/new-host:8080/));
  expect(CookieManager.clearAll).not.toHaveBeenCalled();
});
