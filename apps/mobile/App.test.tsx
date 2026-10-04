import React from "react";
import { act, render } from "@testing-library/react-native";
import { deviceTokenKey } from "@wc/core";
import App from "./App";
import * as Linking from "expo-linking";
import { plainStorage, secureStorage } from "./src/platform/storage";

jest.mock("react-native-gesture-handler", () => ({ GestureHandlerRootView: ({ children }: any) => children }));
jest.mock("expo-font", () => ({ useFonts: () => [true] }));
jest.mock("expo-screen-orientation", () => ({ lockAsync: jest.fn(), OrientationLock: { PORTRAIT_UP: 1 } }));
jest.mock("expo-crypto", () => ({ randomUUID: jest.fn() }));
jest.mock("@expo-google-fonts/space-grotesk", () => ({}));
jest.mock("@expo-google-fonts/jetbrains-mono", () => ({}));
jest.mock("@wc/ui", () => ({ theme: { color: { bg: "black" } } }));
jest.mock("./src/platform/storage", () => ({ plainStorage: { getItem: jest.fn(), setItem: jest.fn(), deleteItem: jest.fn() }, secureStorage: { getItem: jest.fn(), setItem: jest.fn(), deleteItem: jest.fn() } }));
jest.mock("expo-linking", () => ({ getInitialURL: jest.fn(), addEventListener: jest.fn() }));
const mockNavigation = { isReady: jest.fn().mockReturnValue(true), navigate: jest.fn() };
jest.mock("@react-navigation/native", () => ({
  useNavigationContainerRef: () => mockNavigation,
  NavigationContainer: ({ children, onReady }: any) => { const React = require("react"); React.useEffect(onReady, [onReady]); return children; },
}));
jest.mock("./src/navigation/Root", () => ({ RootNavigator: () => {
  const { authToken, base } = require("@wc/core").useServer();
  const { Text } = require("react-native"); const React = require("react");
  return React.createElement(Text, null, `${base}|${authToken ? "InstanceList" : "Pair"}|${authToken ?? ""}`);
} }));

beforeEach(() => {
  jest.clearAllMocks();
  (Linking.getInitialURL as jest.Mock).mockResolvedValue(null);
  (Linking.addEventListener as jest.Mock).mockReturnValue({ remove: jest.fn() });
  (plainStorage.getItem as jest.Mock).mockImplementation(async key => key === "wc_base" ? "http://192.168.1.8:8080" : null);
  (secureStorage.getItem as jest.Mock).mockResolvedValue(null);
  (secureStorage.deleteItem as jest.Mock).mockResolvedValue(undefined);
  (plainStorage.deleteItem as jest.Mock).mockResolvedValue(undefined);
});

test("saved local token is loaded from its host key before navigating to the list", async () => {
  (secureStorage.getItem as jest.Mock).mockImplementation(async key => key === deviceTokenKey("http://192.168.1.8:8080") ? "saved-local-token" : null);
  const view = await render(<App />);
  expect(await view.findByText("http://192.168.1.8:8080|InstanceList|saved-local-token")).toBeTruthy();
  expect(secureStorage.getItem).toHaveBeenCalledWith(deviceTokenKey("http://192.168.1.8:8080"));
  expect(secureStorage.deleteItem).toHaveBeenCalledWith("wc_auth_token");
  expect(mockNavigation.navigate).not.toHaveBeenCalled(); await view.unmount();
});

test("no local token keeps Pair and provider hydration gates the navigator", async () => {
  let finish!: (value: string) => void;
  (plainStorage.getItem as jest.Mock).mockImplementation(key => key === "wc_base" ? new Promise(resolve => { finish = resolve; }) : Promise.resolve(null));
  const view = await render(<App />);
  expect(view.queryByText(/\|Pair/)).toBeNull();
  await act(async () => { finish("http://192.168.1.8:8080"); });
  expect(await view.findByText("http://192.168.1.8:8080|Pair|")).toBeTruthy();
  expect(mockNavigation.navigate).not.toHaveBeenCalled(); await view.unmount();
});
