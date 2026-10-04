import React from "react";
import { Platform } from "react-native";
import { act, cleanup, fireEvent, render, waitFor } from "@testing-library/react-native";
import * as Core from "@wc/core";
import { Pair } from "./Pair";

jest.mock("@wc/core", () => ({
  ...jest.requireActual("@wc/core"),
  useServer: jest.fn(),
  pairDevice: jest.fn(),
  pairRemote: jest.fn(),
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

const trustedOrigin = "https://remote.example";
const invitation = `${trustedOrigin}/pair#invite=abcdefghijklmnopqrstuv`;
const remoteOptions = { trustedOrigin, requestId: () => "00000000-0000-4000-8000-000000000001" };

test("an invitation pairs with a separate six-digit code and no private address", async () => {
  const setTarget = jest.fn().mockResolvedValue({}); mockServer({ setTarget });
  (Core.pairRemote as jest.Mock).mockResolvedValue({ installationId: "a".repeat(32), token: "secret" });
  const screen = await render(<Pair navigation={navigation} route={{ params: { invitation } }} remoteOptions={remoteOptions} />);
  expect(screen.queryByPlaceholderText("Host address")).toBeNull();
  await fireEvent.press(screen.getByText("Pair"));
  expect(Core.pairRemote).not.toHaveBeenCalled();
  await fireEvent.changeText(screen.getByPlaceholderText("Pairing code"), "123456");
  await fireEvent.press(screen.getByText("Pair"));
  await waitFor(() => expect(setTarget).toHaveBeenCalledWith({ kind: "remote", serviceUrl: trustedOrigin, installationId: "a".repeat(32) }, "secret"));
  expect(Core.pairRemote).toHaveBeenCalledWith(trustedOrigin, "abcdefghijklmnopqrstuv", "123456", "iPhone", remoteOptions);
  expect(navigation.replace).toHaveBeenCalledWith("InstanceList");
});

test("wrong-origin invitation never calls pairing transport", async () => {
  const screen = await render(<Pair navigation={navigation} route={{ params: { invitation: invitation.replace("remote.example", "evil.example") } }} remoteOptions={remoteOptions} />);
  await fireEvent.changeText(screen.getByPlaceholderText("Pairing code"), "123456");
  await fireEvent.press(screen.getByText("Pair"));
  expect(Core.pairRemote).not.toHaveBeenCalled(); expect(pairDevice).not.toHaveBeenCalled();
  expect(screen.getByText("This invitation is invalid or belongs to another service.")).toBeTruthy();
});

test.each([
  ["offline", "Your PC is offline. Open the host and try again."],
  ["expired_pairing", "This invitation has expired. Create a new one on your PC."],
  ["not_paired", "That pairing code is incorrect. Check the six digits on your PC."],
])("remote %s feedback is distinct and contains no secrets", async (reason, message) => {
  (Core.pairRemote as jest.Mock).mockRejectedValue(new Error(reason));
  const screen = await render(<Pair navigation={navigation} route={{ params: { invitation } }} remoteOptions={remoteOptions} />);
  await fireEvent.changeText(screen.getByPlaceholderText("Pairing code"), "123456"); await fireEvent.press(screen.getByText("Pair"));
  expect(await screen.findByText(message)).toBeTruthy();
  expect(screen.queryByText(/abcdefghijklmnopqrstuv|secret/)).toBeNull();
});

test.each(["12345", "abcdef", "1234567"])("remote code %s is rejected before transport", async code => {
  const screen = await render(<Pair navigation={navigation} route={{ params: { invitation } }} remoteOptions={remoteOptions} />);
  await fireEvent.changeText(screen.getByPlaceholderText("Pairing code"), code); await fireEvent.press(screen.getByText("Pair"));
  expect(Core.pairRemote).not.toHaveBeenCalled();
});

test("a late pairing cannot replace a newly selected target", async () => {
  let finish!: (value: { installationId: string; token: string }) => void;
  const setTarget = jest.fn(); const a = { kind: "local", base: "http://a" };
  mockServer({ target: a, setTarget });
  (Core.pairRemote as jest.Mock).mockReturnValue(new Promise(resolve => { finish = resolve; }));
  const screen = await render(<Pair navigation={navigation} route={{ params: { invitation } }} remoteOptions={remoteOptions} />);
  await fireEvent.changeText(screen.getByPlaceholderText("Pairing code"), "123456"); await fireEvent.press(screen.getByText("Pair"));
  mockServer({ target: { kind: "local", base: "http://b" }, setTarget });
  await screen.rerender(<Pair navigation={navigation} route={{ params: { invitation } }} remoteOptions={remoteOptions} />);
  await act(async () => { finish({ installationId: "a".repeat(32), token: "secret" }); });
  expect(setTarget).not.toHaveBeenCalled(); expect(navigation.replace).not.toHaveBeenCalled();
});


test("old pairing failure stays silent after the target changes", async () => {
  let reject!: (reason: Error) => void;
  mockServer({ target: { kind: "local", base: "http://a" } });
  (Core.pairRemote as jest.Mock).mockReturnValue(new Promise((_, fail) => { reject = fail; }));
  const screen = await render(<Pair navigation={navigation} route={{ params: { invitation } }} remoteOptions={remoteOptions} />);
  await fireEvent.changeText(screen.getByPlaceholderText("Pairing code"), "123456"); await fireEvent.press(screen.getByText("Pair"));
  mockServer({ target: { kind: "local", base: "http://b" } });
  await screen.rerender(<Pair navigation={navigation} route={{ params: { invitation } }} remoteOptions={remoteOptions} />);
  await act(async () => { reject(new Error("not_paired")); });
  expect(screen.queryByRole("alert")).toBeNull(); expect(navigation.replace).not.toHaveBeenCalled();
});

test("advanced local host remains available from an invitation", async () => {
  const screen = await render(<Pair navigation={navigation} route={{ params: { invitation } }} remoteOptions={remoteOptions} />);
  await fireEvent.press(screen.getByText("Advanced: local host"));
  await fireEvent.changeText(screen.getByPlaceholderText("Host address"), "[fd7a:115c:a1e0::1]");
  await fireEvent.changeText(screen.getByPlaceholderText("Pairing code"), "123456"); await fireEvent.press(screen.getByText("Pair"));
  await waitFor(() => expect(pairDevice).toHaveBeenCalledWith("http://[fd7a:115c:a1e0::1]:8080", "123456", "iPhone"));
  expect(Core.pairRemote).not.toHaveBeenCalled();
});
