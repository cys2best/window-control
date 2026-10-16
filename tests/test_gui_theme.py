from gui import theme


def test_desktop_palette_matches_v2_widget():
    # Fluent/Mica neutrals stay; kiln is the only brand colour crossing into
    # the desktop shell, and primary actions use the v2 near-white action.
    assert theme.CANVAS == "#1c1c1c"
    assert theme.SURFACE == "#1c1c1c"
    assert theme.SURFACE_RAISED == "#232323"
    assert theme.HAIRLINE == "#333333"
    assert theme.INK == "#e0e0e0"
    assert theme.MUTED == "#a0a0a0"
    assert theme.DIM == "#808080"
    assert (theme.ACTION, theme.ACTION_HOVER, theme.ACTION_INK) == ("#faf9f5", "#b0aea5", "#101010")
    assert (theme.BUTTON, theme.BUTTON_HOVER, theme.BUTTON_BORDER) == ("#2b2b2b", "#333333", "#3a3a3a")
    assert theme.STATUS == "#faf9f5"
    assert theme.KILN == "#e8895c"
    assert not hasattr(theme, "CYAN") and not hasattr(theme, "MINT") and not hasattr(theme, "TANGERINE")
