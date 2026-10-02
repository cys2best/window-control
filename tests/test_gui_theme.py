from gui import theme


def test_desktop_palette_matches_shared_tokens():
    assert theme.CANVAS == "#1c1c1c"
    assert theme.SURFACE == "#1c1c1c"
    assert theme.SURFACE_RAISED == "#232323"
    assert theme.HAIRLINE == "#333333"
    assert theme.INK == "#e0e0e0"
    assert theme.MUTED == "#a0a0a0"
    assert theme.DIM == "#808080"
    assert theme.CYAN == "#00E5FF"
    assert theme.TANGERINE == "#FF5722"
    assert theme.MINT == "#10B981"
