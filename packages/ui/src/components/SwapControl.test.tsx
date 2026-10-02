import React from "react";
import { fireEvent, render } from "@testing-library/react-native";

let mockOnEnd: ((event: { translationY: number }) => void) | undefined;
jest.mock("react-native-gesture-handler", () => {
  const chain: any = {
    runOnJS: () => chain, activeOffsetY: () => chain, failOffsetX: () => chain,
    onEnd: (fn: typeof mockOnEnd) => { mockOnEnd = fn; return chain; },
  };
  return { Gesture: { Pan: () => chain }, GestureDetector: ({ children }: any) => children };
});

import { SwapControl } from "./SwapControl";

test("press opens the drawer and renders the instance counter", async () => {
  const onOpen = jest.fn();
  const view = await render(<SwapControl activeIndex={1} count={4} onOpen={onOpen} onCycle={jest.fn()} onWake={jest.fn()} tick={jest.fn()} />);
  expect(view.getByText("2 / 4")).toBeTruthy();
  await fireEvent.press(view.getByLabelText("Switch instance"));
  expect(onOpen).toHaveBeenCalledTimes(1);
});

test("vertical gestures cycle one bounded instance", async () => {
  const onCycle = jest.fn();
  await render(<SwapControl activeIndex={1} count={4} onOpen={jest.fn()} onCycle={onCycle} onWake={jest.fn()} tick={jest.fn()} />);
  mockOnEnd?.({ translationY: -56 });
  mockOnEnd?.({ translationY: 56 });
  expect(onCycle.mock.calls).toEqual([[1], [-1]]);
});

test("centres in the whole gutter by default and below the HUD when one is shown", async () => {
  const { StyleSheet } = require("react-native");
  const props = { activeIndex: 0, count: 2, onOpen: jest.fn(), onCycle: jest.fn(), onWake: jest.fn(), tick: jest.fn() };
  const view = await render(<SwapControl {...props} />);
  const slot = () => StyleSheet.flatten(view.getByTestId("swap-control").props.style);

  expect(slot()).toEqual(expect.objectContaining({ position: "absolute", left: 0, top: 0, bottom: 0, width: 68, justifyContent: "center" }));

  // On a short landscape phone the HUD reaches past the middle of the
  // screen; the control must take the space under it instead of overlapping.
  await view.rerender(<SwapControl {...props} topInset={204} />);
  expect(slot()).toEqual(expect.objectContaining({ top: 204, bottom: 0, justifyContent: "center" }));
});
