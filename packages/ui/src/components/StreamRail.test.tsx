import React from "react";
import { fireEvent, render } from "@testing-library/react-native";
import { StreamRail, type StreamRailProps } from "./StreamRail";

function makeRailProps(): StreamRailProps {
  return {
    visible: true,
    telemetry: { rttMs: 18, loss: 0, decodeMs: null, networkMs: 18, inputMs: null, jitterMs: null, bitrateMbps: null, droppedFrames: null, transport: "unknown", route: "unknown" as const, addressFamily: "unknown" as const, relayProtocol: "unknown" as const, sourceWidth: null, sourceHeight: null, decodedWidth: null, decodedHeight: null, decodedFps: null, framesDecoded: null, freezeCount: null, totalFreezeSeconds: null, maxFreezeSeconds: null },
    connected: true, keyboardOn: false, settingsOn: false,
    onDiagnostics: jest.fn(), onKeyboard: jest.fn(), onSystemKey: jest.fn(), onSettings: jest.fn(),
    onExit: jest.fn(), onWake: jest.fn(), tick: jest.fn(),
  };
}

test("rail exposes the locked key order and system commands", async () => {
  const props = makeRailProps();
  const view = await render(<StreamRail {...props} />);
  expect(view.getAllByTestId("rail-key").map((node) => node.props.accessibilityLabel)).toEqual([
    "Virtual keyboard", "Android home", "Recent apps", "Stream settings",
  ]);
  await fireEvent.press(view.getByLabelText("Android home"));
  await fireEvent.press(view.getByLabelText("Recent apps"));
  expect(props.onSystemKey).toHaveBeenCalledWith("Home");
  expect(props.onSystemKey).toHaveBeenCalledWith("AppSwitch");
});

test("rail keys shrink instead of overlapping on a short viewport", async () => {
  const view = await render(<StreamRail {...makeRailProps()} />);
  for (const key of view.getAllByTestId("rail-key")) {
    const style = key.props.style;
    expect(style).toMatchObject({ flexShrink: 1, minHeight: 28 });
  }
});

test("EXIT is separated and invokes exit", async () => {
  const props = makeRailProps();
  const view = await render(<StreamRail {...props} />);
  expect(view.getByTestId("rail-exit").props.accessibilityLabel).toBe("Exit stream");
  await fireEvent.press(view.getByLabelText("Exit stream"));
  expect(props.onExit).toHaveBeenCalledTimes(1);
});
