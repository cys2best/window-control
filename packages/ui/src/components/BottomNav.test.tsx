import React from "react";
import { Platform, StyleSheet, useWindowDimensions } from "react-native";
import { SafeAreaInsetsContext } from "react-native-safe-area-context";
import { fireEvent, render } from "@testing-library/react-native";
import { BottomNav } from "./BottomNav";

jest.mock("react-native/Libraries/Utilities/useWindowDimensions", () => ({ __esModule: true, default: jest.fn() }));
const originalOS = Platform.OS;

beforeEach(() => {
  (useWindowDimensions as jest.Mock).mockReturnValue({ width: 390, height: 844, scale: 1, fontScale: 1 });
});
afterEach(() => { Platform.OS = originalOS; jest.clearAllMocks(); });

test("capsule exposes only Instances, Resume, and Health with working actions", async () => {
  const onInstances = jest.fn();
  const onResume = jest.fn();
  const onHealth = jest.fn();
  const screen = await render(<BottomNav onInstances={onInstances} onResume={onResume} onHealth={onHealth} />);
  expect(screen.getAllByRole("button")).toHaveLength(3);
  await fireEvent.press(screen.getByRole("button", { name: "Instances" }));
  await fireEvent.press(screen.getByRole("button", { name: "Resume" }));
  await fireEvent.press(screen.getByRole("button", { name: "Health" }));
  expect(onInstances).toHaveBeenCalledTimes(1);
  expect(onResume).toHaveBeenCalledTimes(1);
  expect(onHealth).toHaveBeenCalledTimes(1);
});

test("native capsule clears the safe bottom inset by 34px", async () => {
  Platform.OS = "ios";
  const screen = await render(
    <SafeAreaInsetsContext.Provider value={{ top: 47, bottom: 34, left: 0, right: 0 }}>
      <BottomNav onInstances={() => {}} onResume={() => {}} onHealth={() => {}} />
    </SafeAreaInsetsContext.Provider>,
  );
  expect(StyleSheet.flatten(screen.getByTestId("navigation-capsule").props.style)).toMatchObject({ height: 60, bottom: 68, backgroundColor: "rgba(19,22,31,.82)" });
  expect(StyleSheet.flatten(screen.getByRole("button", { name: "Resume" }).props.style)).toMatchObject({ width: 66, height: 48, backgroundColor: "#00E5FF" });
});

test.each([767, 768])("web width %i uses the appropriate utility position", async (width) => {
  Platform.OS = "web";
  (useWindowDimensions as jest.Mock).mockReturnValue({ width, height: 900, scale: 1, fontScale: 1 });
  const screen = await render(<BottomNav onInstances={() => {}} onResume={() => {}} onHealth={() => {}} />);
  const style = StyleSheet.flatten(screen.getByTestId("navigation-capsule").props.style);
  if (width === 767) {
    expect(style.bottom).toBe("calc(34px + env(safe-area-inset-bottom, 0px))");
    expect(style.top).toBeUndefined();
  } else {
    expect(style.top).toBe(24);
    expect(style.bottom).toBeUndefined();
  }
});
