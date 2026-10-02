"""Shared desktop palette and bundled font registration."""

from dataclasses import dataclass
from pathlib import Path

from PyQt5.QtGui import QFontDatabase

from config import ASSETS_DIR


# The host widget follows Windows 11 Fluent/Mica rather than the mobile/web
# cyber canvas. Cyan remains the single shared brand accent.
CANVAS = "#1c1c1c"
SURFACE = "#1c1c1c"
SURFACE_RAISED = "#232323"
HAIRLINE = "#333333"
INK = "#e0e0e0"
MUTED = "#a0a0a0"
DIM = "#808080"
CYAN = "#00E5FF"
TANGERINE = "#FF5722"
MINT = "#10B981"
DESTRUCTIVE_HOVER = "#C42B1C"


@dataclass(frozen=True)
class FontFamilies:
    ui: str
    mono: str


def register_fonts() -> FontFamilies:
    ui = "Space Grotesk"
    mono = "JetBrains Mono"
    for filename in (
        "SpaceGrotesk-Regular.ttf", "SpaceGrotesk-SemiBold.ttf",
        "SpaceGrotesk-Bold.ttf", "JetBrainsMono-Regular.ttf",
    ):
        font_id = QFontDatabase.addApplicationFont(
            str(Path(ASSETS_DIR) / "fonts" / filename)
        )
        families = QFontDatabase.applicationFontFamilies(font_id) if font_id >= 0 else []
        if filename.startswith("SpaceGrotesk") and families:
            ui = families[0]
        if filename.startswith("JetBrainsMono") and families:
            mono = families[0]
    return FontFamilies(ui=ui, mono=mono)
