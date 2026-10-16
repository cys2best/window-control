import React from "react";
import { render } from "@testing-library/react-native";
import * as NetChipModule from "./NetChip";
import { NetChip } from "./NetChip";
import { theme } from "../theme/tokens";

test.each([
  ["reachable", theme.color.telemetry],
  ["unreachable", theme.color.live],
  ["checking", theme.color.textMuted],
] as const)("%s shows the host with its actual state", async (state, color) => {
  const screen = await render(<NetChip state={state} host="192.168.1.8:8080" />);
  expect(screen.getByText("LAN · 192.168.1.8:8080")).toHaveStyle({ color });
  expect(screen.getByLabelText(`LAN · 192.168.1.8:8080, ${state}`)).toBeTruthy();
});

test("omits a chip when there is no host to report", async () => {
  const screen = await render(<NetChip state="checking" host="" />);
  expect(screen.queryByText(/LAN/)).toBeNull();
});

test("there is no relay chip", () => {
  expect((NetChipModule as any).RelayIdleChip).toBeUndefined();
});
