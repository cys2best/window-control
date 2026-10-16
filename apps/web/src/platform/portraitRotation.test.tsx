import React from "react";
import { act, render } from "@testing-library/react";
import { PortraitRotation } from "./portraitRotation";

function screen(width: number, height: number, touch: boolean) {
  Object.defineProperty(window, "innerWidth", { configurable: true, value: width });
  Object.defineProperty(window, "innerHeight", { configurable: true, value: height });
  window.matchMedia = jest.fn((query: string) => ({
    matches: query === "(pointer: coarse)" ? touch : false,
    media: query, addEventListener: jest.fn(), removeEventListener: jest.fn(),
  })) as any;
}

function renderRotation() {
  const seen: Array<number | undefined> = [];
  const view = render(<PortraitRotation>{(width) => { seen.push(width); return <div data-testid="screen" />; }}</PortraitRotation>);
  const box = () => view.getByTestId("screen").parentElement as HTMLElement;
  return { seen, box, view };
}

test("a portrait touch screen draws the stream rotated, viewport-height wide", () => {
  screen(390, 844, true);
  const { seen, box } = renderRotation();
  expect(seen[seen.length - 1]).toBe(390);
  expect(box().style.width).toBe("844px");
  expect(box().style.height).toBe("390px");
  expect(box().style.transform).toBe("translateX(390px) rotate(90deg)");
  expect(box().style.transformOrigin).toBe("top left");
});

test("desktop and landscape screens are never rotated", () => {
  screen(390, 844, false);
  let { seen, box, view } = renderRotation();
  expect(seen[seen.length - 1]).toBeUndefined();
  expect(box().style.transform).toBe("");
  view.unmount();

  screen(844, 390, true);
  ({ seen, box } = renderRotation());
  expect(seen[seen.length - 1]).toBeUndefined();
  expect(box().style.transform).toBe("");
});

test("turning the phone updates the rotation", () => {
  screen(844, 390, true);
  const { seen } = renderRotation();
  expect(seen[seen.length - 1]).toBeUndefined();
  screen(390, 844, true);
  act(() => { window.dispatchEvent(new Event("resize")); });
  expect(seen[seen.length - 1]).toBe(390);
});
