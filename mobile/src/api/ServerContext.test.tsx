import React from "react";
import { act, render, waitFor } from "@testing-library/react-native";
import { Text } from "react-native";
import AsyncStorage from "@react-native-async-storage/async-storage";
import * as SecureStore from "expo-secure-store";
import { ServerProvider, useServer } from "./ServerContext";

jest.mock("@react-native-async-storage/async-storage", () =>
  require("@react-native-async-storage/async-storage/jest/async-storage-mock"));

jest.mock("expo-secure-store", () => {
  const store = new Map<string, string>();
  return {
    getItemAsync: jest.fn(async (key: string) => store.get(key) ?? null),
    setItemAsync: jest.fn(async (key: string, value: string) => {
      store.set(key, value);
    }),
    deleteItemAsync: jest.fn(async (key: string) => {
      store.delete(key);
    }),
  };
});

function Probe() {
  const ctx = useServer();
  return <Text>{ctx.ready ? `ready:${ctx.base ?? "none"}` : "loading"}</Text>;
}

// Exposes the live useServer() context on a mutable ref outside React so
// tests can call setServer() and read state without renderHook (not
// exported by this project's @testing-library/react-native version).
function capture(ref: { current: ReturnType<typeof useServer> | null }) {
  function Capture() {
    ref.current = useServer();
    return null;
  }
  return Capture;
}

test("loads persisted base and setServer persists", async () => {
  await AsyncStorage.setItem("wc_base", "http://h:8080");
  const { getByText } = await render(<ServerProvider><Probe /></ServerProvider>);
  await waitFor(() => getByText("ready:http://h:8080"));
});

test("stores base in AsyncStorage and token only in SecureStore", async () => {
  const ref: { current: ReturnType<typeof useServer> | null } = { current: null };
  const Capture = capture(ref);
  await render(<ServerProvider><Capture /></ServerProvider>);
  await waitFor(() => expect(ref.current?.ready).toBe(true));
  await act(async () => { await ref.current!.setServer("http://host:8080", "s3cret"); });
  expect(await AsyncStorage.getItem("wc_base")).toBe("http://host:8080");
  expect(await AsyncStorage.getItem("wc_auth_token")).toBeNull();
  expect(await SecureStore.getItemAsync("wc_auth_token")).toBe("s3cret");
});

test("empty token deletes the SecureStore item", async () => {
  const ref: { current: ReturnType<typeof useServer> | null } = { current: null };
  const Capture = capture(ref);
  await render(<ServerProvider><Capture /></ServerProvider>);
  await waitFor(() => expect(ref.current?.ready).toBe(true));
  await act(async () => { await ref.current!.setServer("http://host:8080", "s3cret"); });
  await act(async () => { await ref.current!.setServer("http://host:8080", ""); });
  expect(await SecureStore.getItemAsync("wc_auth_token")).toBeNull();
});

test("setServer returns the newly constructed client", async () => {
  const ref: { current: ReturnType<typeof useServer> | null } = { current: null };
  const Capture = capture(ref);
  await render(<ServerProvider><Capture /></ServerProvider>);
  await waitFor(() => expect(ref.current?.ready).toBe(true));
  let client: any;
  await act(async () => { client = await ref.current!.setServer("http://host:8080", "s3cret"); });
  expect(client).toBeTruthy();
  expect(typeof client.instances).toBe("function");
});

test("ready only becomes true after both AsyncStorage and SecureStore load", async () => {
  await AsyncStorage.setItem("wc_base", "http://h:8080");
  const ref: { current: ReturnType<typeof useServer> | null } = { current: null };
  const Capture = capture(ref);
  await render(<ServerProvider><Capture /></ServerProvider>);
  await waitFor(() => expect(ref.current?.ready).toBe(true));
  expect(ref.current?.base).toBe("http://h:8080");
});
