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
    authToken: "dev-tok",
    base: "http://192.168.1.8:8080",
    hostReachability: { state: "reachable", host: "192.168.1.8:8080", rttMs: 31, paired: true },
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

test("shows the real host and no account identity", async () => {
  const view = await render(<Account navigation={navigation} />);
  expect(view.getByText("192.168.1.8:8080")).toBeTruthy();
  expect(view.queryByText("IDENTITY")).toBeNull();
  expect(view.queryByText("Connection route")).toBeNull();
  expect(view.queryByText(/Sign out/)).toBeNull();
});

test("unpairing clears this device's access and returns to Pair", async () => {
  const clearAuth = jest.fn().mockResolvedValue(undefined);
  const replace = jest.fn();
  mockServer({ clearAuth });
  const view = await render(<Account navigation={{ replace }} />);
  await fireEvent.press(view.getByText("Unpair this device"));
  await waitFor(() => expect(clearAuth).toHaveBeenCalled());
  expect(replace).toHaveBeenCalledWith("Pair");
});

test("a device with no token (the PC itself) has nothing to unpair", async () => {
  mockServer({ authToken: null });
  const view = await render(<Account navigation={navigation} />);
  expect(view.queryByText("Unpair this device")).toBeNull();
  expect(view.getByText("Default quality")).toBeTruthy();
});

test("stream defaults persist only the preference selected by each row", async () => {
  const updatePreferences = jest.fn().mockResolvedValue(undefined);
  mockServer({ updatePreferences });
  const view = await render(<Account navigation={navigation} />);
  await fireEvent.press(view.getByLabelText("Stream quality"));
  await fireEvent.press(view.getByLabelText("Show HUD on connect"));
  await fireEvent.press(view.getByLabelText("Touch haptics"));
  await waitFor(() => expect(updatePreferences).toHaveBeenCalledWith({ quality: "1440" }));
  expect(updatePreferences).toHaveBeenCalledWith({ showHudOnConnect: true });
  expect(updatePreferences).toHaveBeenCalledWith({ haptics: false });
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

test("omits the host row when reachability has no real value", async () => {
  mockServer({ base: null, hostReachability: null });
  const view = await render(<Account navigation={navigation} />);
  expect(view.queryByText("Current host")).toBeNull();
  expect(view.queryByText("Host unavailable")).toBeNull();
});
