"""Shared desktop palette and bundled font registration."""

from dataclasses import dataclass
from pathlib import Path

from PyQt5.QtGui import QFontDatabase

from config import ASSETS_DIR


CANVAS = "#06070b"
SURFACE = "#090a0f"
SURFACE_RAISED = "#13161f"
HAIRLINE = "#222738"
INK = "#E6EAF2"
MUTED = "#7A8496"
DIM = "#5C6679"
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
