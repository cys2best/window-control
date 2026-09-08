import React from "react";
import { render } from "@testing-library/react-native";
import { NetChip } from "./NetChip";
import { theme } from "../theme/tokens";

test.each([
  ["lan", "reachable", "LAN · 192.168.1.8:8080", theme.color.telemetry],
  ["relay", "standby", "RELAY · relay.example", theme.color.textMuted],
  ["relay", "reachable", "RELAY · relay.example", theme.color.live],
  ["lan", "unreachable", "LAN · 192.168.1.8:8080", theme.color.live],
  ["relay", "unreachable", "RELAY · relay.example", theme.color.live],
  ["lan", "checking", "LAN · 192.168.1.8:8080", theme.color.textMuted],
] as const)("%s %s communicates its route and actual state", async (route, state, label, color) => {
  const screen = await render(<NetChip route={route} state={state} host={route === "lan" ? "192.168.1.8:8080" : "relay.example"} />);
  expect(screen.getByText(label)).toHaveStyle({ color });
  expect(screen.getByLabelText(`${label}, ${state}`)).toBeTruthy();
});

test("omits a chip when there is no host to report", async () => {
  const screen = await render(<NetChip route="lan" state="checking" host="" />);
  expect(screen.queryByText(/LAN/)).toBeNull();
});
