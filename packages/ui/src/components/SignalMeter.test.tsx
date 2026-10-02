import React from "react";
import { fireEvent, render } from "@testing-library/react-native";
import { SignalMeter } from "./SignalMeter";

const telemetry = {
  rttMs: 18, loss: 0, decodeMs: null, networkMs: 18, inputMs: null,
  jitterMs: null, bitrateMbps: null, droppedFrames: null, transport: "LAN" as const,
};

test("renders real RTT and four signal bars in its accessible rail head", async () => {
  const onPress = jest.fn();
  const screen = await render(<SignalMeter telemetry={telemetry} connected onPress={onPress} />);

  expect(screen.getByLabelText("Network diagnostics")).toHaveStyle({ width: 68 });
  expect(screen.getByText("18ms")).toBeTruthy();
  expect(screen.getAllByTestId("signal-bar")).toHaveLength(4);
  fireEvent.press(screen.getByLabelText("Network diagnostics"));
  expect(onPress).toHaveBeenCalledTimes(1);
});

test("shows no invented RTT when telemetry is unavailable", async () => {
  const screen = await render(<SignalMeter telemetry={{ ...telemetry, rttMs: null, loss: null }} connected={false} onPress={() => {}} />);

  expect(screen.getByText("—")).toBeTruthy();
  expect(screen.getAllByTestId("signal-bar")[0]).toHaveStyle({ backgroundColor: "#FF5722" });
  screen.unmount();
});
