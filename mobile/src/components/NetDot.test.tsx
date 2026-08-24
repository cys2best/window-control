import React from "react";
import { render } from "@testing-library/react-native";
import { NetDot } from "./NetDot";
import { theme } from "../theme/tokens";

test("NetDot colors by state", async () => {
  const { getByTestId, rerender } = await render(<NetDot state="connected" />);
  expect(getByTestId("net-dot").props.style.backgroundColor).toBe(theme.net.connected.dot);
  await rerender(<NetDot state="disconnected" />);
  expect(getByTestId("net-dot").props.style.backgroundColor).toBe(theme.net.disconnected.dot);
});
