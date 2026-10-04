import React from "react";
import { render } from "@testing-library/react-native";
import { StyleSheet } from "react-native";
import { theme } from "../theme/tokens";
import { StatsOverlay } from "./StatsOverlay";

test("renders typed telemetry readings", async () => {
  const view = await render(<StatsOverlay telemetry={{ decodeMs: 4.1, networkMs: 18, inputMs: 11, jitterMs: 6, bitrateMbps: 8.4, droppedFrames: 2, rttMs: 18, loss: .01, transport: "unknown", route: "unknown" as const, addressFamily: "unknown" as const, relayProtocol: "unknown" as const, sourceWidth: null, sourceHeight: null, decodedWidth: null, decodedHeight: null, decodedFps: null, framesDecoded: null, freezeCount: null, totalFreezeSeconds: null, maxFreezeSeconds: null }} />);
  expect(view.getByText("4.1 ms")).toBeTruthy(); expect(view.getByText("8.4 Mb/s")).toBeTruthy(); expect(view.getByText("2")).toBeTruthy();
});

test("keeps the headroom neutral until network health is measured, then alerts only on new dropped frames", async () => {
  const base = { decodeMs: 4.1, networkMs: 18, inputMs: 11, jitterMs: 6, bitrateMbps: 8.4, transport: "unknown" as const, route: "unknown" as const, addressFamily: "unknown" as const, relayProtocol: "unknown" as const, sourceWidth: null, sourceHeight: null, decodedWidth: null, decodedHeight: null, decodedFps: null, framesDecoded: null, freezeCount: null, totalFreezeSeconds: null, maxFreezeSeconds: null };
  const view = await render(<StatsOverlay telemetry={{ ...base, droppedFrames: 2, rttMs: null, loss: null }} />);
  const headroom = () => StyleSheet.flatten(view.getByTestId("diagnostic-hud-headroom").props.style);

  expect(headroom()).toEqual(expect.objectContaining({ backgroundColor: theme.color.textDim }));
  await view.rerender(<StatsOverlay telemetry={{ ...base, droppedFrames: 2, rttMs: 18, loss: .01 }} />);
  expect(headroom()).toEqual(expect.objectContaining({ backgroundColor: theme.color.telemetry }));
  await view.rerender(<StatsOverlay telemetry={{ ...base, droppedFrames: 3, rttMs: 18, loss: .01 }} />);
  expect(headroom()).toEqual(expect.objectContaining({ backgroundColor: theme.color.live }));
});

test("anchors the HUD in the top-left stream gutter", async () => {
  const view = await render(<StatsOverlay telemetry={{ decodeMs: null, networkMs: null, inputMs: null, jitterMs: null, bitrateMbps: null, droppedFrames: null, rttMs: null, loss: null, transport: "unknown", route: "unknown" as const, addressFamily: "unknown" as const, relayProtocol: "unknown" as const, sourceWidth: null, sourceHeight: null, decodedWidth: null, decodedHeight: null, decodedFps: null, framesDecoded: null, freezeCount: null, totalFreezeSeconds: null, maxFreezeSeconds: null }} />);
  expect(StyleSheet.flatten(view.getByTestId("diagnostic-hud").props.style)).toEqual(expect.objectContaining({ top: 0, left: 0, width: 68 }));
});

test("reports its height so the gutter can lay out around it", async () => {
  const { fireEvent } = require("@testing-library/react-native");
  const onHeight = jest.fn();
  const view = await render(<StatsOverlay onHeight={onHeight} telemetry={{ decodeMs: null, networkMs: null, inputMs: null, jitterMs: null, bitrateMbps: null, droppedFrames: null, rttMs: null, loss: null, transport: "unknown", route: "unknown" as const, addressFamily: "unknown" as const, relayProtocol: "unknown" as const, sourceWidth: null, sourceHeight: null, decodedWidth: null, decodedHeight: null, decodedFps: null, framesDecoded: null, freezeCount: null, totalFreezeSeconds: null, maxFreezeSeconds: null }} />);

  await fireEvent(view.getByTestId("diagnostic-hud"), "layout", { nativeEvent: { layout: { x: 0, y: 0, width: 68, height: 204 } } });

  expect(onHeight).toHaveBeenCalledWith(204);
});

test("shows selected route and independent media evidence with unknown gaps", async () => {
  const telemetry = { decodeMs: null, networkMs: null, inputMs: null, jitterMs: null, bitrateMbps: null, droppedFrames: null, rttMs: null, loss: null, transport: "relay", route: "relay", addressFamily: "IPv6", relayProtocol: "tls", sourceWidth: 1600, sourceHeight: 900, decodedWidth: 1280, decodedHeight: 720, decodedFps: 30, framesDecoded: 100, freezeCount: null, totalFreezeSeconds: null, maxFreezeSeconds: null } as any;
  const view = await render(<StatsOverlay telemetry={telemetry} />);
  expect(view.getByText("Relay")).toBeTruthy();
  expect(view.getByText("IPv6 / tls")).toBeTruthy();
  expect(view.getByText("1600×900")).toBeTruthy();
  expect(view.getByText("1280×720")).toBeTruthy();
  expect(view.getByText("30 fps")).toBeTruthy();
  expect(view.getByText("MAX FREEZE")).toBeTruthy();
});
