export type TargetColor = "black" | "white";

export const WHITE_MIN = 160;
export const BLACK_MAX = 96;

// Mean Rec.601 luma of the central half of the image (each dimension), so
// letterboxing or a status overlay at the edges does not count.
export function centerLuminance(rgba: ArrayLike<number>, width: number, height: number): number {
  const x0 = Math.floor(width / 4), x1 = Math.ceil((width * 3) / 4);
  const y0 = Math.floor(height / 4), y1 = Math.ceil((height * 3) / 4);
  let sum = 0;
  let count = 0;
  for (let y = y0; y < y1; y++) {
    for (let x = x0; x < x1; x++) {
      const i = (y * width + x) * 4;
      sum += 0.299 * rgba[i] + 0.587 * rgba[i + 1] + 0.114 * rgba[i + 2];
      count++;
    }
  }
  return count === 0 ? 0 : sum / count;
}

export function classify(lum: number): TargetColor | null {
  if (lum >= WHITE_MIN) return "white";
  if (lum <= BLACK_MAX) return "black";
  return null;
}

export function opposite(color: TargetColor): TargetColor {
  return color === "white" ? "black" : "white";
}
