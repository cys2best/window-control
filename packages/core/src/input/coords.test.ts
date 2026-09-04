import { normalizeCoords } from "./coords";

test("center of a matched-aspect rect maps to (0.5,0.5)", () => {
  const r = normalizeCoords({ x: 100, y: 100 }, { width: 200, height: 200 }, { w: 100, h: 100 });
  expect(r.x).toBeCloseTo(0.5);
  expect(r.y).toBeCloseTo(0.5);
});

test("letterboxed portrait video in a wide rect ignores the side bars", () => {
  // rect 400x200, content 100x200 => scale 1 => contentW=100, offsetX=150
  const left = normalizeCoords({ x: 150, y: 100 }, { width: 400, height: 200 }, { w: 100, h: 200 });
  expect(left.x).toBeCloseTo(0);   // left edge of the content
  const right = normalizeCoords({ x: 250, y: 100 }, { width: 400, height: 200 }, { w: 100, h: 200 });
  expect(right.x).toBeCloseTo(1);
});

test("coords clamp to [0,1]", () => {
  const r = normalizeCoords({ x: -50, y: 9999 }, { width: 200, height: 200 }, { w: 100, h: 100 });
  expect(r.x).toBe(0);
  expect(r.y).toBe(1);
});
