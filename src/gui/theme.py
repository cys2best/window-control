"""Shared desktop palette and bundled font registration."""

from dataclasses import dataclass
from pathlib import Path

from PyQt5.QtGui import QFontDatabase

from config import ASSETS_DIR


# The host widget follows Windows 11 Fluent/Mica rather than the app canvas
# (EmuCtrl v2). Kiln is the only brand colour crossing into the desktop
# shell; primary actions use the v2 near-white action with dark ink.
CANVAS = "#1c1c1c"
SURFACE = "#1c1c1c"
SURFACE_RAISED = "#232323"
HAIRLINE = "#333333"
INK = "#e0e0e0"
MUTED = "#a0a0a0"
DIM = "#808080"
ACTION = "#faf9f5"
ACTION_HOVER = "#b0aea5"
ACTION_INK = "#101010"
BUTTON = "#2b2b2b"
BUTTON_HOVER = "#333333"
BUTTON_BORDER = "#3a3a3a"
STATUS = "#faf9f5"
KILN = "#e8895c"
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
