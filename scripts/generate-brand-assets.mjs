#!/usr/bin/env node
// Generates every platform's icon/favicon/tray asset from the single
// canonical brand mark at assets/brand/emuctrl-mark.svg. Nothing downstream
// of this script should ever be hand-edited or redrawn by eye -- re-run
// `npm run generate:brand` after touching the SVG.
import { readFile, writeFile, rename, unlink } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import path from "node:path";
import sharp from "sharp";
import pngToIco from "png-to-ico";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(__dirname, "..");
const SVG_PATH = path.join(ROOT, "assets/brand/emuctrl-mark.svg");

const BG = "#06070b";

// [outputPath, canvasSize, backed]
// backed=true  -> solid #06070b square, mark composited at 72% size.
// backed=false -> transparent canvas, mark composited at 64% size.
const OUTPUTS = [
  ["apps/web/public/icon-192.png", 192, true],
  ["apps/web/public/icon-512.png", 512, true],
  ["apps/mobile/assets/icon.png", 1024, true],
  ["apps/mobile/assets/android-icon-foreground.png", 432, false],
  ["apps/mobile/assets/favicon.png", 48, true],
  ["apps/mobile/assets/splash-icon.png", 512, false],
  ["src/assets/tray_icon.png", 64, false],
];

// Extra platform layers derived from the same mark but not a plain
// [path, size, backed] triple.
const ANDROID_BACKGROUND_PATH = "apps/mobile/assets/android-icon-background.png";
const ANDROID_MONOCHROME_PATH = "apps/mobile/assets/android-icon-monochrome.png";
const ANDROID_LAYER_SIZE = 432;

const ICO_SIZES = [16, 32, 48, 256];
const FAVICON_ICO_PATH = "apps/web/public/favicon.ico";
const APP_ICO_PATH = "src/assets/icon.ico";

async function writeFileAtomic(relPath, buffer) {
  const absPath = path.join(ROOT, relPath);
  const tmpPath = `${absPath}.tmp-${process.pid}`;
  await writeFile(tmpPath, buffer);
  await rename(tmpPath, absPath);
}

/** Renders the mark alone (no canvas) as a square PNG buffer of `size`px. */
async function renderMark(svgBuffer, size) {
  return sharp(svgBuffer, { density: 384 })
    .resize(size, size, { fit: "contain" })
    .png()
    .toBuffer();
}

/** Composites a rendered mark onto a canvas of `canvasSize`, centered. */
async function composite(markBuffer, markSize, canvasSize, background, opaque) {
  const offset = Math.round((canvasSize - markSize) / 2);
  let image = sharp({
    create: {
      width: canvasSize,
      height: canvasSize,
      channels: 4,
      background,
    },
  }).composite([{ input: markBuffer, left: offset, top: offset }]);
  // Backed icons are meant to be fully opaque squares (app-store/PWA icon
  // rules reject/flag a stray alpha channel) -- flatten it away instead of
  // just leaving it at alpha=255 everywhere.
  if (opaque) {
    image = image.flatten({ background }).removeAlpha();
  }
  return image.png().toBuffer();
}

async function buildIconBuffer(svgBuffer, canvasSize, backed) {
  const scale = backed ? 0.72 : 0.64;
  const markSize = Math.round(canvasSize * scale);
  const markBuffer = await renderMark(svgBuffer, markSize);
  const background = backed
    ? BG
    : { r: 0, g: 0, b: 0, alpha: 0 };
  return composite(markBuffer, markSize, canvasSize, background, backed);
}

async function buildMonochromeBuffer(svgBuffer, canvasSize) {
  // Same silhouette/placement as the transparent foreground, but every
  // non-transparent mark pixel is forced to solid white with its original
  // alpha kept -- Android's themed (Material You) icon layer wants a
  // single-color shape, not the mark's real palette. `tint()` preserves
  // per-pixel luminance (so the hairline frame and the fill rectangles
  // would come out different shades of gray); rewriting RGB to pure white
  // while keeping the source alpha channel is what "tinting" actually
  // means here.
  const markSize = Math.round(canvasSize * 0.64);
  const markBuffer = await renderMark(svgBuffer, markSize);
  const { data, info } = await sharp(markBuffer)
    .ensureAlpha()
    .raw()
    .toBuffer({ resolveWithObject: true });
  for (let i = 0; i < data.length; i += info.channels) {
    data[i] = 255; // R
    data[i + 1] = 255; // G
    data[i + 2] = 255; // B
    // data[i + 3] (alpha) untouched
  }
  const whiteMark = await sharp(data, {
    raw: { width: info.width, height: info.height, channels: info.channels },
  })
    .png()
    .toBuffer();
  return composite(whiteMark, markSize, canvasSize, { r: 0, g: 0, b: 0, alpha: 0 });
}

async function buildIco(svgBuffer, outputRelPath) {
  const buffers = await Promise.all(
    ICO_SIZES.map((size) => buildIconBuffer(svgBuffer, size, true))
  );
  const icoBuffer = await pngToIco(buffers);
  await writeFileAtomic(outputRelPath, icoBuffer);
  console.log(`wrote ${outputRelPath} (${ICO_SIZES.join("/")}px)`);
}

async function main() {
  const svgBuffer = await readFile(SVG_PATH);

  for (const [relPath, size, backed] of OUTPUTS) {
    const buffer = await buildIconBuffer(svgBuffer, size, backed);
    await writeFileAtomic(relPath, buffer);
    console.log(`wrote ${relPath} (${size}x${size}, ${backed ? "backed" : "transparent"})`);
  }

  const backgroundBuffer = await sharp({
    create: {
      width: ANDROID_LAYER_SIZE,
      height: ANDROID_LAYER_SIZE,
      channels: 4,
      background: BG,
    },
  })
    .png()
    .toBuffer();
  await writeFileAtomic(ANDROID_BACKGROUND_PATH, backgroundBuffer);
  console.log(`wrote ${ANDROID_BACKGROUND_PATH} (${ANDROID_LAYER_SIZE}x${ANDROID_LAYER_SIZE}, solid)`);

  const monochromeBuffer = await buildMonochromeBuffer(svgBuffer, ANDROID_LAYER_SIZE);
  await writeFileAtomic(ANDROID_MONOCHROME_PATH, monochromeBuffer);
  console.log(`wrote ${ANDROID_MONOCHROME_PATH} (${ANDROID_LAYER_SIZE}x${ANDROID_LAYER_SIZE}, monochrome)`);

  await buildIco(svgBuffer, FAVICON_ICO_PATH);
  await buildIco(svgBuffer, APP_ICO_PATH);
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
