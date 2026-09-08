from gui import theme


def test_desktop_palette_matches_shared_tokens():
    assert theme.CANVAS == "#06070b"
    assert theme.SURFACE == "#090a0f"
    assert theme.SURFACE_RAISED == "#13161f"
    assert theme.HAIRLINE == "#222738"
    assert theme.INK == "#E6EAF2"
    assert theme.MUTED == "#7A8496"
    assert theme.DIM == "#5C6679"
    assert theme.CYAN == "#00E5FF"
    assert theme.TANGERINE == "#FF5722"
    assert theme.MINT == "#10B981"
