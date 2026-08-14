export function normalizeCoords(
  pointer: { x: number; y: number },
  rect: { width: number; height: number },
  content: { w: number; h: number },
): { x: number; y: number } {
  let contentW = rect.width, contentH = rect.height, offsetX = 0, offsetY = 0;
  if (content.w && content.h) {
    const scale = Math.min(rect.width / content.w, rect.height / content.h);
    contentW = content.w * scale;
    contentH = content.h * scale;
    offsetX = (rect.width - contentW) / 2;
    offsetY = (rect.height - contentH) / 2;
  }
  const clamp = (v: number) => Math.max(0, Math.min(1, v));
  return {
    x: clamp((pointer.x - offsetX) / contentW),
    y: clamp((pointer.y - offsetY) / contentH),
  };
}
