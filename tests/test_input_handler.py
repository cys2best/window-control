import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from server.input_handler import normalize_to_abs, make_lparam

def test_normalize_center():
    rect = (100, 200, 1380, 920)  # x0,y0,x1,y1 → w=1280, h=720
    ax, ay = normalize_to_abs(0.5, 0.5, rect)
    assert ax == 100 + 640
    assert ay == 200 + 360

def test_normalize_top_left():
    rect = (0, 0, 1280, 720)
    ax, ay = normalize_to_abs(0.0, 0.0, rect)
    assert ax == 0
    assert ay == 0

def test_normalize_bottom_right():
    rect = (0, 0, 1280, 720)
    ax, ay = normalize_to_abs(1.0, 1.0, rect)
    assert ax == 1280
    assert ay == 720

def test_make_lparam():
    lp = make_lparam(100, 200)
    assert lp == (200 << 16) | 100
