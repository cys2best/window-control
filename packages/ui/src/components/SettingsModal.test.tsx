import React from "react";
import { render, fireEvent } from "@testing-library/react-native";
import { TIER_ORDER } from "@wc/core";
import { SettingsModal } from "./SettingsModal";

test("quality segments come from TIER_ORDER plus Auto", async () => {
  const view = await render(<SettingsModal preferences={{ quality: "720", showHudOnConnect: false, haptics: true, hideRailWhilePlaying: true }} onPickQuality={jest.fn()} onPreferences={jest.fn()} onClose={jest.fn()} />);
  expect(["Auto", ...TIER_ORDER.map((tier) => `${tier}p`)].every((label) => view.getByText(label))).toBe(true);
});

test("settings persist HUD and haptic preferences", async () => {
  const onPreferences = jest.fn();
  const view = await render(<SettingsModal preferences={{ quality: "auto", showHudOnConnect: false, haptics: true, hideRailWhilePlaying: true }} onPickQuality={jest.fn()} onPreferences={onPreferences} onClose={jest.fn()} />);
  await fireEvent.press(view.getByRole("switch", { name: "Diagnostic HUD" }));
  await fireEvent.press(view.getByRole("switch", { name: "Touch haptics" }));
  expect(onPreferences).toHaveBeenCalledWith({ showHudOnConnect: true });
  expect(onPreferences).toHaveBeenCalledWith({ haptics: false });
});
