import React from "react";
import { Platform } from "react-native";
import { act, cleanup, fireEvent, render, waitFor } from "@testing-library/react-native";
import * as Core from "@wc/core";
import { Pair } from "./Pair";

jest.mock("@wc/core", () => ({
  ...jest.requireActual("@wc/core"),
  useServer: jest.fn(),
  pairDevice: jest.fn(),
}));

const setServer = jest.fn();
const navigation = { replace: jest.fn() };
const pairDevice = Core.pairDevice as jest.Mock;

function mockServer(overrides: Record<string, unknown> = {}) {
  (Core.useServer as jest.Mock).mockReturnValue({
    base: null,
    setServer,
    hostReachability: { state: "checking", host: "", rttMs: null, paired: null },
    ...overrides,
  });
}

beforeEach(() => {
  jest.clearAllMocks();
  setServer.mockResolvedValue({});
  pairDevice.mockResolvedValue({ token: "dev-tok" });
  mockServer();
});

afterEach(() => {
  jest.restoreAllMocks();
  cleanup();
});

test("pairs with the entered host and code, stores the token, and opens the instance list", async () => {
  const screen = await render(<Pair navigation={navigation} />);
  await fireEvent.changeText(screen.getByPlaceholderText("Host address"), "http://100.101.102.103:8080");
  await fireEvent.changeText(screen.getByPlaceholderText("Pairing code"), "123456");
  await fireEvent.press(screen.getByText("Pair"));

  await waitFor(() => expect(navigation.replace).toHaveBeenCalledWith("InstanceList"));
  expect(pairDevice).toHaveBeenCalledWith("http://100.101.102.103:8080", "123456", "iPhone");
  expect(setServer).toHaveBeenCalledWith("http://100.101.102.103:8080", "dev-tok");
});

test("a bare host gets http:// and a spaced code is sent as digits", async () => {
  const screen = await render(<Pair navigation={navigation} />);
  await fireEvent.changeText(screen.getByPlaceholderText("Host address"), " 192.168.1.8:8080/ ");
  await fireEvent.changeText(screen.getByPlaceholderText("Pairing code"), " 123 456 ");
  await fireEvent.press(screen.getByText("Pair"));

  await waitFor(() => expect(pairDevice).toHaveBeenCalledWith("http://192.168.1.8:8080", "123456", "iPhone"));
});

test("the host field starts with the saved host", async () => {
  mockServer({ base: "http://192.168.1.8:8080" });
  const screen = await render(<Pair navigation={navigation} />);
  expect(screen.getByPlaceholderText("Host address").props.value).toBe("http://192.168.1.8:8080");
});

test("a missing host or code is reported without calling the host", async () => {
  const screen = await render(<Pair navigation={navigation} />);
  await fireEvent.press(screen.getByText("Pair"));
  expect(await screen.findByText("Enter your host address")).toBeTruthy();

  await fireEvent.changeText(screen.getByPlaceholderText("Host address"), "192.168.1.8:8080");
  await fireEvent.changeText(screen.getByPlaceholderText("Pairing code"), "   ");
  await fireEvent.press(screen.getByText("Pair"));
  expect(await screen.findByText("Enter the pairing code shown on your PC")).toBeTruthy();
  expect(pairDevice).not.toHaveBeenCalled();
});

test("a rejected code shows the host's message and stays on the screen", async () => {
  pairDevice.mockResolvedValue({ error: "That code is wrong or has expired." });
  const screen = await render(<Pair navigation={navigation} />);
  await fireEvent.changeText(screen.getByPlaceholderText("Host address"), "192.168.1.8:8080");
  await fireEvent.changeText(screen.getByPlaceholderText("Pairing code"), "000000");
  await fireEvent.press(screen.getByText("Pair"));

  expect(await screen.findByText("That code is wrong or has expired.")).toBeTruthy();
  expect(setServer).not.toHaveBeenCalled();
  expect(navigation.replace).not.toHaveBeenCalled();
});

test("a second press while pairing does not send a second request", async () => {
  let finish!: (value: { token: string }) => void;
  pairDevice.mockReturnValue(new Promise((resolve) => { finish = resolve; }));
  const screen = await render(<Pair navigation={navigation} />);
  await fireEvent.changeText(screen.getByPlaceholderText("Host address"), "192.168.1.8:8080");
  await fireEvent.changeText(screen.getByPlaceholderText("Pairing code"), "123456");
  await fireEvent.press(screen.getByText("Pair"));
  await fireEvent.press(await screen.findByText("Please wait…"));
  expect(pairDevice).toHaveBeenCalledTimes(1);

  await act(async () => { finish({ token: "dev-tok" }); });
  await waitFor(() => expect(navigation.replace).toHaveBeenCalledWith("InstanceList"));
});

test("on web there is no host field and the page's own host is used", async () => {
  jest.replaceProperty(Platform, "OS", "web");
  mockServer({
    base: "http://192.168.1.8:8080",
    hostReachability: { state: "reachable", host: "192.168.1.8:8080", rttMs: 12, paired: false },
  });
  const screen = await render(<Pair navigation={navigation} />);
  expect(screen.queryByPlaceholderText("Host address")).toBeNull();
  expect(screen.getByText("LAN · 192.168.1.8:8080")).toBeTruthy();

  await fireEvent.changeText(screen.getByPlaceholderText("Pairing code"), "123456");
  await fireEvent.press(screen.getByText("Pair"));

  await waitFor(() => expect(pairDevice).toHaveBeenCalledWith("http://192.168.1.8:8080", "123456", "Browser"));
});

test.each([
  ["192.168.1.8", "http://192.168.1.8:8080"],
  ["192.168.1.8:9000", "http://192.168.1.8:9000"],
  ["http://192.168.1.8", "http://192.168.1.8:8080"],
  ["[fd7a:115c:a1e0::1]", "http://[fd7a:115c:a1e0::1]:8080"],
  ["[fd7a:115c:a1e0::1]:9000", "http://[fd7a:115c:a1e0::1]:9000"],
])("host %s is sent as %s", async (typed, sent) => {
  const screen = await render(<Pair navigation={navigation} />);
  await fireEvent.changeText(screen.getByPlaceholderText("Host address"), typed);
  await fireEvent.changeText(screen.getByPlaceholderText("Pairing code"), "123456");
  await fireEvent.press(screen.getByText("Pair"));

  await waitFor(() => expect(pairDevice).toHaveBeenCalledWith(sent, "123456", "iPhone"));
});

test("an invalid host is reported and nothing is sent", async () => {
  const screen = await render(<Pair navigation={navigation} />);
  await fireEvent.changeText(screen.getByPlaceholderText("Host address"), "https://");
  await fireEvent.changeText(screen.getByPlaceholderText("Pairing code"), "123456");
  await fireEvent.press(screen.getByText("Pair"));

  expect(await screen.findByText("That host address isn't valid")).toBeTruthy();
  expect(pairDevice).not.toHaveBeenCalled();
});

test("on web the page's own origin gets no port added", async () => {
  jest.replaceProperty(Platform, "OS", "web");
  mockServer({ base: "https://host.example" });
  const screen = await render(<Pair navigation={navigation} />);
  await fireEvent.changeText(screen.getByPlaceholderText("Pairing code"), "123456");
  await fireEvent.press(screen.getByText("Pair"));

  await waitFor(() => expect(pairDevice).toHaveBeenCalledWith("https://host.example", "123456", "Browser"));
});
