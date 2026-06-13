#!/usr/bin/env python3
"""Generate placeholder icon assets for WindowControl."""
import sys
import os
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from PIL import Image, ImageDraw

ASSETS_DIR = Path(__file__).parent.parent / "src" / "assets"
ASSETS_DIR.mkdir(parents=True, exist_ok=True)


def make_icon_image(size: int) -> Image.Image:
    """Blue rounded square with white 'W' letter."""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    # Blue background
    margin = size // 8
    try:
        draw.rounded_rectangle(
            [margin, margin, size - margin, size - margin],
            radius=size // 6,
            fill=(30, 120, 200, 255),
        )
    except AttributeError:
        # Older Pillow without rounded_rectangle
        draw.rectangle(
            [margin, margin, size - margin, size - margin],
            fill=(30, 120, 200, 255),
        )
    # White 'W' text — use a simple cross shape since no font needed
    cx, cy = size // 2, size // 2
    stroke = max(2, size // 12)
    # Draw W as two V shapes
    pts_left = [
        (cx - size // 3, cy - size // 4),
        (cx - size // 4, cy + size // 4),
        (cx, cy),
        (cx + size // 4, cy + size // 4),
        (cx + size // 3, cy - size // 4),
    ]
    draw.line(pts_left, fill=(255, 255, 255, 255), width=stroke)
    return img


def generate_tray_icon():
    img = make_icon_image(64)
    # Convert to RGB for PNG (pystray uses PIL, RGBA is fine)
    path = ASSETS_DIR / "tray_icon.png"
    img.save(path, format="PNG")
    print(f"Created {path}")


def generate_ico():
    # .ico needs multiple sizes
    sizes = [16, 32, 48, 256]
    images = [make_icon_image(s) for s in sizes]
    path = ASSETS_DIR / "icon.ico"
    images[0].save(
        path,
        format="ICO",
        sizes=[(s, s) for s in sizes],
        append_images=images[1:],
    )
    print(f"Created {path}")


if __name__ == "__main__":
    generate_tray_icon()
    generate_ico()
    print("Assets generated.")
