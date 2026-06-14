import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from unittest.mock import patch, MagicMock
from server.input_handler import _abs_coords, _to_send_input_coords


def test_abs_coords_center():
    rect = (100, 200, 1380, 920)  # w=1280, h=720
    with patch('server.input_handler.win32gui') as mock_gui:
        mock_gui.GetWindowRect.return_value = rect
        ax, ay = _abs_coords(0, 0.5, 0.5)
    assert ax == 100 + 640
    assert ay == 200 + 360


def test_abs_coords_top_left():
    rect = (0, 0, 1280, 720)
    with patch('server.input_handler.win32gui') as mock_gui:
        mock_gui.GetWindowRect.return_value = rect
        ax, ay = _abs_coords(0, 0.0, 0.0)
    assert ax == 0
    assert ay == 0


def test_abs_coords_bottom_right():
    rect = (0, 0, 1280, 720)
    with patch('server.input_handler.win32gui') as mock_gui:
        mock_gui.GetWindowRect.return_value = rect
        ax, ay = _abs_coords(0, 1.0, 1.0)
    assert ax == 1280
    assert ay == 720


def test_to_send_input_coords_full_screen():
    with patch('server.input_handler._screen_size', return_value=(1920, 1080)):
        sx, sy = _to_send_input_coords(1919, 1079)
    assert sx == 65535
    assert sy == 65535


def test_to_send_input_coords_origin():
    with patch('server.input_handler._screen_size', return_value=(1920, 1080)):
        sx, sy = _to_send_input_coords(0, 0)
    assert sx == 0
    assert sy == 0
