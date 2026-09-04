import React from "react";
import { render, fireEvent } from "@testing-library/react-native";
import { SettingsModal } from "./SettingsModal";

test("picking a tier fires onPick with the tier string", async () => {
  const onPick = jest.fn();
  const { getByText } = await render(
    <SettingsModal tier="720" onPick={onPick} statsOn={false} onToggleStats={() => {}} onClose={() => {}} />);
  fireEvent.press(getByText("1080p"));
  expect(onPick).toHaveBeenCalledWith("1080");
});
