import React from "react";
import { render } from "@testing-library/react-native";
import * as Core from "@wc/core";
import { RootNavigator } from "./Root";
const mockNavigator = jest.fn(); const mockPair = jest.fn();
jest.mock("@react-navigation/native-stack", () => ({ createNativeStackNavigator: () => ({
  Navigator: (props: any) => { mockNavigator(props); const React = require("react"); const { Text } = require("react-native"); return React.createElement(Text, null, props.initialRouteName); },
  Screen: () => null,
}) }));
jest.mock("@wc/core", () => ({ ...jest.requireActual("@wc/core"), useServer: jest.fn() }));
jest.mock("@wc/ui", () => ({ Pair: (props: any) => { mockPair(props); return null; }, Account: () => null, InstanceList: () => null, Stream: () => null }));
jest.mock("../platform/VideoView", () => ({ VideoView: () => null }));
jest.mock("expo-haptics", () => ({}));

test.each([["saved-token", "InstanceList"], [null, "Pair"]])("token %s keeps initial local route %s", async (authToken, expected) => {
  (Core.useServer as jest.Mock).mockReturnValue({ authToken });
  const view = await render(<RootNavigator />); expect(view.getByText(expected)).toBeTruthy(); await view.unmount();
});

test("Pair route receives stable configured options and the invitation params", async () => {
  (Core.useServer as jest.Mock).mockReturnValue({ authToken: null });
  const remoteOptions = { trustedOrigin: "https://remote.example", requestId: () => "id" };
  const view = await render(<RootNavigator remoteOptions={remoteOptions} />);
  const screens = mockNavigator.mock.calls[mockNavigator.mock.calls.length - 1][0].children;
  const props = { route: { params: { invitation: "https://remote.example/pair#invite=abcdefghijklmnopqrstuv" } }, navigation: {} };
  const pairView = await render(screens[0].props.children(props));
  expect(mockPair).toHaveBeenLastCalledWith({ ...props, remoteOptions }); await pairView.unmount(); await view.unmount();
});
