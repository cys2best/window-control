import React from "react";
import { render } from "@testing-library/react-native";
import { StreamToolbar } from "./StreamToolbar";

test.each([
  ["connected", "LIVE"], ["connecting", "SYNC"], ["disconnected", "DOWN"],
] as const)("keeps %s stream status readable while the legacy dot is retired", async (net, label) => {
  const screen = await render(<StreamToolbar net={net}
    active={{ settings: false, drawer: false, keyboard: false, stats: false }}
    onSettings={jest.fn()} onSwitch={jest.fn()} onKeyboard={jest.fn()} onStats={jest.fn()} onBack={jest.fn()} />);
  expect(screen.getByText(label)).toBeTruthy();
});
