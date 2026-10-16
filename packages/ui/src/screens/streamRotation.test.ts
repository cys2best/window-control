import { rotatedDelta, rotatedPoint } from "./streamRotation";

// Portrait phone 390 wide: the stream box is drawn 90° clockwise, so its
// left edge runs down the screen's top and its top along the screen's right.
test("maps screen points into the clockwise-rotated stream box", () => {
  expect(rotatedPoint(390, 0, 390, 0)).toEqual({ x: 0, y: 0 });
  expect(rotatedPoint(0, 0, 390, 0)).toEqual({ x: 0, y: 390 });
  expect(rotatedPoint(390, 844, 390, 0)).toEqual({ x: 844, y: 0 });
  expect(rotatedPoint(290, 118, 390, 68)).toEqual({ x: 50, y: 100 });
});

test("maps screen drags onto the rotated axes", () => {
  expect(rotatedDelta(0, 30)).toEqual({ dx: 30, dy: 0 });
  expect(rotatedDelta(-30, 0)).toEqual({ dx: 0, dy: 30 });
});
