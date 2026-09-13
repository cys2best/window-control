import React from "react";
import { render } from "@testing-library/react-native";
import { StyleSheet } from "react-native";
import { theme } from "../theme/tokens";
import { StatsOverlay } from "./StatsOverlay";

test("renders typed telemetry readings", async () => {
  const view = await render(<StatsOverlay telemetry={{ decodeMs: 4.1, networkMs: 18, inputMs: 11, jitterMs: 6, bitrateMbps: 8.4, droppedFrames: 2, rttMs: 18, loss: .01, transport: "LAN" }} />);
  expect(view.getByText("4.1 ms")).toBeTruthy(); expect(view.getByText("8.4 Mb/s")).toBeTruthy(); expect(view.getByText("2")).toBeTruthy();
});

test("keeps the headroom neutral until network health is measured, then alerts only on new dropped frames", async () => {
  const base = { decodeMs: 4.1, networkMs: 18, inputMs: 11, jitterMs: 6, bitrateMbps: 8.4, transport: "LAN" as const };
  const view = await render(<StatsOverlay telemetry={{ ...base, droppedFrames: 2, rttMs: null, loss: null }} />);
  const headroom = () => StyleSheet.flatten(view.getByTestId("diagnostic-hud-headroom").props.style);

  expect(headroom()).toEqual(expect.objectContaining({ backgroundColor: theme.color.textDim }));
  await view.rerender(<StatsOverlay telemetry={{ ...base, droppedFrames: 2, rttMs: 18, loss: .01 }} />);
  expect(headroom()).toEqual(expect.objectContaining({ backgroundColor: theme.color.telemetry }));
  await view.rerender(<StatsOverlay telemetry={{ ...base, droppedFrames: 3, rttMs: 18, loss: .01 }} />);
  expect(headroom()).toEqual(expect.objectContaining({ backgroundColor: theme.color.live }));
});

test("anchors the HUD in the top-left stream gutter", async () => {
  const view = await render(<StatsOverlay telemetry={{ decodeMs: null, networkMs: null, inputMs: null, jitterMs: null, bitrateMbps: null, droppedFrames: null, rttMs: null, loss: null, transport: "LAN" }} />);
  expect(StyleSheet.flatten(view.getByTestId("diagnostic-hud").props.style)).toEqual(expect.objectContaining({ top: 0, left: 0, width: 68 }));
});
