// On a portrait phone the web app draws the stream screen rotated 90°
// clockwise (CSS), viewport-height wide and viewport-width tall, so the
// device can stay rotation-locked. The browser reports touches in screen
// axes; these map them back into the stream box.

/** Screen point → stream-box point; `leftInset` is the box's own left offset (the rail). */
export function rotatedPoint(pageX: number, pageY: number, viewportWidth: number, leftInset: number) {
  return { x: pageY - leftInset, y: viewportWidth - pageX };
}

/** Screen drag → stream-box drag. */
export function rotatedDelta(dx: number, dy: number) {
  return { dx: dy, dy: 0 - dx }; // 0 - dx, not -dx: no -0 for a still axis
}
