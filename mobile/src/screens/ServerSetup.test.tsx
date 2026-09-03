import React from "react";
import { act, render, fireEvent, waitFor, cleanup } from "@testing-library/react-native";
import AsyncStorage from "@react-native-async-storage/async-storage";
import { ServerProvider } from "../api/ServerContext";
import { ServerSetup } from "./ServerSetup";

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

afterEach(cleanup);

test("rejects a malformed URL with an inline error", async () => {
  const nav = { replace: jest.fn() } as any;
  const { getByPlaceholderText, getByText } = await render(
    <ServerProvider><ServerSetup navigation={nav} /></ServerProvider>);
  await act(async () => {
    fireEvent.changeText(getByPlaceholderText(/http:\/\//), "not a url");
  });
  await act(async () => {
    fireEvent.press(getByText("Start streaming"));
  });
  await waitFor(() => getByText(/Enter a full URL/i));
  expect(nav.replace).not.toHaveBeenCalled();
});

test("probes reachability with an unauthenticated request, not the API client", async () => {
  global.fetch = jest.fn(async () => ({ ok: false, status: 500, json: async () => ({}) })) as any;
  const nav = { replace: jest.fn() } as any;
  const { getByPlaceholderText, getByText } = await render(
    <ServerProvider><ServerSetup navigation={nav} /></ServerProvider>);
  await act(async () => {
    fireEvent.changeText(getByPlaceholderText(/http:\/\//), "http://host:8080");
  });
  await act(async () => {
    fireEvent.press(getByText("Start streaming"));
  });
  await waitFor(() => expect((fetch as jest.Mock).mock.calls.length).toBeGreaterThan(0));
  const [calledUrl, init] = (fetch as jest.Mock).mock.calls[0];
  expect(calledUrl).toBe("http://host:8080/auth/config");
  expect(new Headers(init?.headers).get("Authorization")).toBeNull();
});

test("shows an unreachable-server error on probe failure", async () => {
  global.fetch = jest.fn(async () => ({ ok: false, status: 500, json: async () => ({}) })) as any;
  const nav = { replace: jest.fn() } as any;
  const { getByPlaceholderText, getByText } = await render(
    <ServerProvider><ServerSetup navigation={nav} /></ServerProvider>);
  await act(async () => {
    fireEvent.changeText(getByPlaceholderText(/http:\/\//), "http://host:8080");
  });
  await act(async () => {
    fireEvent.press(getByText("Start streaming"));
  });
  await waitFor(() => getByText(/Can't reach server/i));
  expect(nav.replace).not.toHaveBeenCalled();
});

test("navigates to Login on a successful reachability probe", async () => {
  global.fetch = jest.fn(async () => ({ ok: true, status: 200, json: async () => ({}) })) as any;
  const nav = { replace: jest.fn() } as any;
  const { getByPlaceholderText, getByText } = await render(
    <ServerProvider><ServerSetup navigation={nav} /></ServerProvider>);
  await act(async () => {
    fireEvent.changeText(getByPlaceholderText(/http:\/\//), "http://host:8080");
  });
  await act(async () => {
    fireEvent.press(getByText("Start streaming"));
  });
  await waitFor(() => expect(nav.replace).toHaveBeenCalledWith("Login"));
});
