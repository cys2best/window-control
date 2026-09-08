import React from "react";
import { render } from "@testing-library/react-native";
import { StatsOverlay } from "./StatsOverlay";
test("renders typed telemetry readings", async () => {
  const view = await render(<StatsOverlay telemetry={{ decodeMs: 4.1, networkMs: 18, inputMs: 11, jitterMs: 6, bitrateMbps: 8.4, droppedFrames: 2, rttMs: 18, loss: .01, transport: "LAN" }} />);
  expect(view.getByText("4.1 ms")).toBeTruthy(); expect(view.getByText("8.4 Mb/s")).toBeTruthy(); expect(view.getByText("2")).toBeTruthy();
});
