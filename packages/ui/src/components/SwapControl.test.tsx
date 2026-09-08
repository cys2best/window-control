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
