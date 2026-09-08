import React from "react";
import { fireEvent, render, waitFor } from "@testing-library/react-native";
import * as SC from "@wc/core";
import { Account } from "./Account";

jest.mock("@wc/core", () => ({
  ...jest.requireActual("@wc/core"),
  TIER_ORDER: ["1440", "480"],
  useServer: jest.fn(),
}));

const navigation = { navigate: jest.fn(), replace: jest.fn() };

function mockServer(overrides: Record<string, unknown> = {}) {
  (SC.useServer as jest.Mock).mockReturnValue({
    identity: { id: "u1", email: "owner@example.com", displayName: "Owner", initials: "O" },
    base: "https://relay.example",
    hostReachability: { state: "reachable", route: "relay", host: "relay.example", rttMs: 31 },
    preferences: { quality: "auto", showHudOnConnect: false, haptics: true, hideRailWhilePlaying: true },
    updatePreferences: jest.fn().mockResolvedValue(undefined),
    clearAuth: jest.fn().mockResolvedValue(undefined),
    ...overrides,
  });
}

beforeEach(() => {
  jest.clearAllMocks();
  mockServer();
});

test("renders only signed-in identity and real host state", async () => {
  mockServer({
    identity: { id: "u1", email: "owner@example.com", displayName: "Owner", initials: "O" },
    base: "https://relay.example",
    hostReachability: { state: "reachable", route: "relay", host: "relay.example", rttMs: 31 },
  });
  const view = await render(<Account navigation={navigation} />);
  expect(view.getByText("Owner")).toBeTruthy();
  expect(view.getByText("owner@example.com")).toBeTruthy();
  expect(view.getByText("relay.example")).toBeTruthy();
  expect(view.queryByText(/Kai Hoang|DESKTOP-7K2N|NVENC/)).toBeNull();
});

test("sign out clears only this device and returns to Login", async () => {
  const clearAuth = jest.fn().mockResolvedValue(undefined);
  const replace = jest.fn();
  mockServer({ clearAuth });
  const view = await render(<Account navigation={{ replace }} />);
  await fireEvent.press(view.getByText("Sign out on this device"));
  await waitFor(() => expect(clearAuth).toHaveBeenCalled());
  expect(replace).toHaveBeenCalledWith("Login");
});

test("stream defaults persist only the preference selected by each row", async () => {
  const updatePreferences = jest.fn().mockResolvedValue(undefined);
  mockServer({ updatePreferences });
  const view = await render(<Account navigation={navigation} />);
  await fireEvent.press(view.getByLabelText("Stream quality"));
  await fireEvent.press(view.getByLabelText("Show HUD on connect"));
  await fireEvent.press(view.getByLabelText("Touch haptics"));
  await fireEvent.press(view.getByLabelText("Hide rail while playing"));
  await waitFor(() => expect(updatePreferences).toHaveBeenCalledWith({ quality: "1440" }));
  expect(updatePreferences).toHaveBeenCalledWith({ showHudOnConnect: true });
  expect(updatePreferences).toHaveBeenCalledWith({ haptics: false });
  expect(updatePreferences).toHaveBeenCalledWith({ hideRailWhilePlaying: false });
});

test("quality cycling follows the core tier order", async () => {
  const updatePreferences = jest.fn().mockResolvedValue(undefined);
  mockServer({
    preferences: { quality: "480", showHudOnConnect: false, haptics: true, hideRailWhilePlaying: true },
    updatePreferences,
  });
  const view = await render(<Account navigation={navigation} />);
  await fireEvent.press(view.getByLabelText("Stream quality"));
  await waitFor(() => expect(updatePreferences).toHaveBeenCalledWith({ quality: "auto" }));
});

test("omits host and route rows when reachability has no real values", async () => {
  mockServer({ base: null, hostReachability: null });
  const view = await render(<Account navigation={navigation} />);
  expect(view.queryByText("Current host")).toBeNull();
  expect(view.queryByText("Connection route")).toBeNull();
  expect(view.queryByText("Host unavailable")).toBeNull();
  expect(view.queryByText("LAN")).toBeNull();
});
