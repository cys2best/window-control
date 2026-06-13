# src/gui/launcher.py
import threading
from PyQt5.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QLabel, QComboBox, QGroupBox, QSizePolicy
)
from PyQt5.QtCore import Qt, pyqtSignal, QThread, pyqtSlot
from PyQt5.QtGui import QPixmap, QImage
import qrcode
import io

from config import PORT, QUALITY_MAP, DEFAULT_QUALITY
from server.tailscale import get_best_ip, has_tailscale
from server.stream import CaptureState
from gui.window_list import WindowListWidget


class LauncherWindow(QMainWindow):
    server_start_requested = pyqtSignal()
    server_stop_requested = pyqtSignal()
    quality_changed = pyqtSignal(int)  # emits QUALITY_MAP value (int)
    window_selected = pyqtSignal(int, str)  # hwnd, title

    def __init__(self, state: CaptureState, parent=None):
        super().__init__(parent)
        self._state = state
        self._server_running = False
        self.setWindowTitle("WindowControl")
        self.setMinimumWidth(320)
        self._setup_ui()
        self._refresh_ip()

    def _setup_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setSpacing(12)
        layout.setContentsMargins(16, 16, 16, 16)

        # --- Server status group ---
        server_group = QGroupBox("Server")
        server_layout = QVBoxLayout(server_group)

        self._ip_label = QLabel("IP: detecting…")
        self._ip_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        server_layout.addWidget(self._ip_label)

        self._url_label = QLabel("")
        self._url_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        server_layout.addWidget(self._url_label)

        self._qr_label = QLabel()
        self._qr_label.setAlignment(Qt.AlignCenter)
        self._qr_label.setFixedHeight(180)
        server_layout.addWidget(self._qr_label)

        self._start_stop_btn = QPushButton("Start Server")
        self._start_stop_btn.clicked.connect(self._on_start_stop)
        server_layout.addWidget(self._start_stop_btn)

        layout.addWidget(server_group)

        # --- Quality group ---
        quality_group = QGroupBox("Stream Quality")
        quality_layout = QHBoxLayout(quality_group)
        self._quality_combo = QComboBox()
        for label in QUALITY_MAP:
            self._quality_combo.addItem(label.capitalize(), label)
        # Set default
        idx = self._quality_combo.findData(DEFAULT_QUALITY)
        if idx >= 0:
            self._quality_combo.setCurrentIndex(idx)
        self._quality_combo.currentIndexChanged.connect(self._on_quality_changed)
        quality_layout.addWidget(self._quality_combo)
        layout.addWidget(quality_group)

        # --- Window list group ---
        windows_group = QGroupBox("Select Window")
        windows_layout = QVBoxLayout(windows_group)
        self._window_list = WindowListWidget()
        self._window_list.window_selected.connect(self._on_window_selected)
        windows_layout.addWidget(self._window_list)
        layout.addWidget(windows_group)

        # --- Status bar ---
        self._status_label = QLabel("Server stopped")
        layout.addWidget(self._status_label)

    def _refresh_ip(self):
        ip = get_best_ip()
        ts = has_tailscale()
        label = f"{'Tailscale' if ts else 'LAN'}: {ip}"
        self._ip_label.setText(f"IP: {label}")
        url = f"http://{ip}:{PORT}"
        self._url_label.setText(f"URL: {url}")
        self._update_qr(url)

    def _update_qr(self, url: str):
        qr = qrcode.make(url)
        buf = io.BytesIO()
        qr.save(buf, format="PNG")
        buf.seek(0)
        data = buf.read()
        img = QImage.fromData(data)
        pix = QPixmap.fromImage(img).scaled(
            160, 160, Qt.KeepAspectRatio, Qt.SmoothTransformation
        )
        self._qr_label.setPixmap(pix)

    def _on_start_stop(self):
        if not self._server_running:
            self._server_running = True
            self._start_stop_btn.setText("Stop Server")
            self._status_label.setText("Server running…")
            self.server_start_requested.emit()
        else:
            self._server_running = False
            self._start_stop_btn.setText("Start Server")
            self._status_label.setText("Server stopped")
            self.server_stop_requested.emit()

    def _on_quality_changed(self, _index: int):
        key = self._quality_combo.currentData()
        value = QUALITY_MAP[key]
        self.quality_changed.emit(value)

    def _on_window_selected(self, hwnd: int, title: str):
        self._status_label.setText(f"Streaming: {title}")
        self.window_selected.emit(hwnd, title)

    def set_server_running(self, running: bool):
        """Called externally to sync button state."""
        self._server_running = running
        if running:
            self._start_stop_btn.setText("Stop Server")
            self._status_label.setText("Server running…")
        else:
            self._start_stop_btn.setText("Start Server")
            self._status_label.setText("Server stopped")
