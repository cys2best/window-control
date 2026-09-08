import React from "react";
import { FlatList, StyleSheet, Text, useWindowDimensions } from "react-native";
import { act, render, fireEvent, waitFor } from "@testing-library/react-native";
import { InstanceList } from "./InstanceList";
import * as SC from "@wc/core";

jest.mock("@wc/core", () => ({ ...jest.requireActual("@wc/core"), useServer: jest.fn() }));
jest.mock("@react-native-async-storage/async-storage", () => require("@react-native-async-storage/async-storage/jest/async-storage-mock"));
jest.mock("react-native/Libraries/Utilities/useWindowDimensions", () => ({ __esModule: true, default: jest.fn() }));

const first = { id: "adb:A", serial: "A", title: "LDP-01", active: false };
const second = { id: "adb:B", serial: "B", title: "LDP-02", active: true };
let client: any;
let nav: any;
let clearAuth: jest.Mock;

beforeEach(() => {
  client = {
    instances: jest.fn().mockResolvedValue([first, second]),
    ping: jest.fn().mockResolvedValue(23),
    previewSource: (serial: string) => ({ uri: `http://h/preview/${serial}`, headers: { Authorization: "Bearer token" } }),
    keyframe: jest.fn().mockResolvedValue(undefined),
  };
  nav = { navigate: jest.fn(), replace: jest.fn() };
  clearAuth = jest.fn().mockResolvedValue(undefined);
  (useWindowDimensions as jest.Mock).mockReturnValue({ width: 390, height: 844, scale: 1, fontScale: 1 });
  (SC.useServer as jest.Mock).mockReturnValue({ base: "http://different-base", client, clearAuth,
    hostReachability: { route: "lan", state: "reachable", host: "actual-host:8080", rttMs: 99 } });
});
afterEach(() => { jest.restoreAllMocks(); jest.clearAllMocks(); jest.useRealTimers(); });

test("renders instances and requests a keyframe before navigating on tap", async () => {
  const screen = await render(<InstanceList navigation={nav} />);
  await fireEvent.press(await screen.findByText("LDP-02"));
  expect(nav.navigate).toHaveBeenCalledWith("Stream", { serial: "B", title: "LDP-02" });
  expect(client.keyframe).toHaveBeenCalledWith("B");
  expect(screen.getByText("LIVE")).toBeTruthy();
});

test("host card displays measured ping as its largest value and the current reachability host", async () => {
  const screen = await render(<InstanceList navigation={nav} />);
  const rtt = await screen.findByText("23 ms");
  expect(StyleSheet.flatten(rtt.props.style)).toMatchObject({ fontSize: 18, fontFamily: "JetBrainsMono_400Regular" });
  expect(screen.getByText("actual-host:8080")).toBeTruthy();
  expect(screen.getByLabelText("LAN · actual-host:8080, reachable")).toBeTruthy();
  expect(StyleSheet.flatten(rtt.props.style)).toMatchObject({ fontSize: 18 });
  expect(screen.queryByText(/GPU|different-base|99 ms/)).toBeNull();
});

test.each([true, false])("Resume opens active instance or first fallback (active=%s)", async (active) => {
  client.instances.mockResolvedValue([first, { ...second, active }]);
  const screen = await render(<InstanceList navigation={nav} />);
  await screen.findByText("LDP-02");
  await fireEvent.press(screen.getByRole("button", { name: "Resume" }));
  expect(nav.navigate).toHaveBeenCalledWith("Stream", active ? { serial: "B", title: "LDP-02" } : { serial: "A", title: "LDP-01" });
});

test("Health scrolls to the host card, Instances to the grid, and Account opens the Account route", async () => {
  const scroll = jest.spyOn(FlatList.prototype, "scrollToOffset").mockImplementation(() => {});
  const screen = await render(<InstanceList navigation={nav} />);
  await screen.findByText("LDP-01");
  await fireEvent(screen.getByTestId("instances-heading"), "layout", { nativeEvent: { layout: { x: 0, y: 160, width: 342, height: 30 } } });
  await fireEvent.press(screen.getByRole("button", { name: "Instances" }));
  expect(scroll).toHaveBeenLastCalledWith({ offset: 160, animated: true });
  await fireEvent.press(screen.getByRole("button", { name: "Health" }));
  expect(scroll).toHaveBeenLastCalledWith({ offset: 0, animated: true });
  await fireEvent.press(screen.getByRole("button", { name: "Account" }));
  expect(nav.navigate).toHaveBeenCalledWith("Account");
});

test("empty dashboard retains health scrolling and Resume has no destination", async () => {
  client.instances.mockResolvedValue([]);
  const scroll = jest.spyOn(FlatList.prototype, "scrollToOffset").mockImplementation(() => {});
  const screen = await render(<InstanceList navigation={nav} />);
  await screen.findByText("No windows found");
  await fireEvent.press(screen.getByRole("button", { name: "Resume" }));
  expect(nav.navigate).not.toHaveBeenCalled();
  await fireEvent.press(screen.getByRole("button", { name: "Health" }));
  expect(scroll).toHaveBeenCalledWith({ offset: 0, animated: true });
});

test.each([[390, 1], [583, 1], [584, 2], [1024, 3]])("width %i renders %i grid columns", async (width, columns) => {
  (useWindowDimensions as jest.Mock).mockReturnValue({ width, height: 900, scale: 1, fontScale: 1 });
  const screen = await render(<InstanceList navigation={nav} />);
  await screen.findByText("LDP-01");
  expect(screen.getByLabelText(`Instance grid, ${columns} columns`)).toBeTruthy();
});

test("grid remounts safely when the column count changes", async () => {
  const screen = await render(<InstanceList navigation={nav} />);
  await screen.findByText("LDP-01");
  (useWindowDimensions as jest.Mock).mockReturnValue({ width: 1024, height: 900, scale: 1, fontScale: 1 });
  await screen.rerender(<InstanceList navigation={nav} />);
  expect(screen.getByLabelText("Instance grid, 3 columns")).toBeTruthy();
});

test("refreshes measured RTT with instances every minute and clears unavailable ping", async () => {
  jest.useFakeTimers();
  const screen = await render(<InstanceList navigation={nav} />);
  await screen.findByText("23 ms");
  client.ping.mockResolvedValueOnce(41);
  await act(async () => { jest.advanceTimersByTime(60000); });
  expect(screen.getByText("41 ms")).toBeTruthy();
  expect(client.instances).toHaveBeenCalledTimes(2);
  client.ping.mockRejectedValueOnce(new Error("offline"));
  await act(async () => { jest.advanceTimersByTime(60000); });
  expect(screen.queryByText(/\d+ ms/)).toBeNull();
  expect(screen.getByText("LDP-01")).toBeTruthy();
  await screen.unmount();
  await act(async () => { jest.advanceTimersByTime(60000); });
  expect(client.ping).toHaveBeenCalledTimes(3);
});

test("failed instance refresh shows the unreachable empty state without fabricated latency", async () => {
  client.instances.mockRejectedValue(new Error("offline"));
  client.ping.mockRejectedValue(new Error("offline"));
  const screen = await render(<InstanceList navigation={nav} />);
  expect(await screen.findByText("Can't reach the server")).toBeTruthy();
  expect(screen.queryByText(/\d+ ms/)).toBeNull();
});

test("redirects to Login on 401 response and clears auth", async () => {
  client.instances.mockRejectedValue(Object.assign(new Error("401"), { status: 401 }));
  await render(<InstanceList navigation={nav} />);
  await waitFor(() => expect(clearAuth).toHaveBeenCalled());
  expect(nav.replace).toHaveBeenCalledWith("Login");
});
