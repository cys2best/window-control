# src/gui/launcher.py
import sys
import threading
import subprocess
from PyQt5.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QLabel, QComboBox, QGroupBox, QSizePolicy, QScrollArea
)
from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtGui import QPixmap, QImage
import qrcode
import io

from config import PORT, QUALITY_MAP, DEFAULT_QUALITY, VERSION
from server.tailscale import get_best_ip, has_tailscale
from server.stream import CaptureState
from updater import check_for_update


class LauncherWindow(QMainWindow):
    server_start_requested = pyqtSignal()
    server_stop_requested = pyqtSignal()
    quality_changed = pyqtSignal(int)
    window_selected = pyqtSignal(int, str)

    def __init__(self, state: CaptureState, parent=None):
        super().__init__(parent)
        self._state = state
        self._server_running = False
        self.setWindowTitle(f"WindowControl v{VERSION}")
        self.setMinimumWidth(420)
        self.resize(460, 600)
        self._setup_ui()
        self._pending_update_version = None
        self._refresh_ip()
        check_for_update(self._on_update_available)

    def _setup_ui(self):
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setCentralWidget(scroll)

        central = QWidget()
        scroll.setWidget(central)
        layout = QVBoxLayout(central)
        layout.setSpacing(16)
        layout.setContentsMargins(20, 20, 20, 20)

        self._setup_style()

        # --- Server group ---
        server_group = QGroupBox("Server")
        server_layout = QVBoxLayout(server_group)
        server_layout.setSpacing(10)

        self._ip_label = QLabel("IP: detecting…")
        self._ip_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self._ip_label.setStyleSheet("font-size: 14px; color: #333;")
        server_layout.addWidget(self._ip_label)

        self._url_label = QLabel("")
        self._url_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self._url_label.setStyleSheet("font-size: 13px; color: #555;")
        server_layout.addWidget(self._url_label)

        self._qr_label = QLabel()
        self._qr_label.setAlignment(Qt.AlignCenter)
        self._qr_label.setFixedHeight(200)
        server_layout.addWidget(self._qr_label)

        self._start_stop_btn = QPushButton("Start Server")
        self._start_stop_btn.setMinimumHeight(44)
        self._start_stop_btn.setStyleSheet(self._btn_style("#2563eb", "#1d4ed8"))
        self._start_stop_btn.clicked.connect(self._on_start_stop)
        server_layout.addWidget(self._start_stop_btn)

        layout.addWidget(server_group)

        # --- Quality group ---
        quality_group = QGroupBox("Stream Quality")
        quality_layout = QHBoxLayout(quality_group)
        quality_layout.setContentsMargins(12, 12, 12, 12)
        self._quality_combo = QComboBox()
        self._quality_combo.setMinimumHeight(36)
        self._quality_combo.setStyleSheet("font-size: 14px;")
        for label in QUALITY_MAP:
            self._quality_combo.addItem(label.capitalize(), label)
        idx = self._quality_combo.findData(DEFAULT_QUALITY)
        if idx >= 0:
            self._quality_combo.setCurrentIndex(idx)
        self._quality_combo.currentIndexChanged.connect(self._on_quality_changed)
        quality_layout.addWidget(self._quality_combo)
        layout.addWidget(quality_group)

        # --- Update banner ---
        self._update_banner = QWidget()
        self._update_banner.setStyleSheet(
            "background:#fffbe6; border:1px solid #f0c040; border-radius:6px;"
        )
        banner_layout = QVBoxLayout(self._update_banner)
        banner_layout.setContentsMargins(10, 8, 10, 8)
        banner_layout.setSpacing(6)

        self._update_label = QLabel()
        self._update_label.setStyleSheet("color:#7a6000; font-size:13px; background:transparent; border:none;")
        self._update_label.setWordWrap(True)
        banner_layout.addWidget(self._update_label)

        self._install_btn = QPushButton("Install Update")
        self._install_btn.setMinimumHeight(36)
        self._install_btn.setStyleSheet(self._btn_style("#d97706", "#b45309"))
        self._install_btn.clicked.connect(self._on_install_update)
        banner_layout.addWidget(self._install_btn)

        self._update_banner.hide()
        layout.addWidget(self._update_banner)

        # --- Status bar ---
        self._status_label = QLabel("Server stopped")
        self._status_label.setStyleSheet("font-size: 13px; color: #666;")
        layout.addWidget(self._status_label)


    def _setup_style(self):
        self.setStyleSheet("""
            QMainWindow { background: #f8fafc; }
            QGroupBox {
                font-size: 14px;
                font-weight: 600;
                color: #1e293b;
                border: 1px solid #e2e8f0;
                border-radius: 8px;
                margin-top: 8px;
                padding-top: 8px;
                background: white;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 12px;
                padding: 0 6px;
                color: #374151;
            }
            QScrollArea { border: none; background: #f8fafc; }
            QWidget#qt_scrollarea_viewport { background: #f8fafc; }
        """)

    def _btn_style(self, bg: str, hover: str) -> str:
        return f"""
            QPushButton {{
                background: {bg};
                color: white;
                border: none;
                border-radius: 8px;
                font-size: 15px;
                font-weight: 600;
                padding: 10px;
            }}
            QPushButton:hover {{ background: {hover}; }}
            QPushButton:pressed {{ background: {hover}; }}
            QPushButton:disabled {{ background: #94a3b8; }}
        """

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
            190, 190, Qt.KeepAspectRatio, Qt.SmoothTransformation
        )
        self._qr_label.setPixmap(pix)

    def _on_start_stop(self):
        if not self._server_running:
            self._server_running = True
            self._start_stop_btn.setText("Stop Server")
            self._start_stop_btn.setStyleSheet(self._btn_style("#dc2626", "#b91c1c"))
            self._status_label.setText("Server running…")
            self.server_start_requested.emit()
        else:
            self._server_running = False
            self._start_stop_btn.setText("Start Server")
            self._start_stop_btn.setStyleSheet(self._btn_style("#2563eb", "#1d4ed8"))
            self._status_label.setText("Server stopped")
            self.server_stop_requested.emit()

    def _on_quality_changed(self, _idx: int):
        key = self._quality_combo.currentData()
        value = QUALITY_MAP[key]
        self.quality_changed.emit(value)

    def _on_update_available(self, latest: str):
        self._pending_update_version = latest
        self._update_label.setText(f"Update available: v{latest}")
        self._install_btn.setText("Install Update")
        self._install_btn.setEnabled(True)
        self._update_banner.show()

    def _on_install_update(self):
        from updater import download_and_install
        version = self._pending_update_version
        if not version:
            return
        self._install_btn.setEnabled(False)
        self._update_label.setText(f"Downloading v{version}… 0%")

        def _progress(pct):
            self._update_label.setText(f"Downloading v{version}… {pct}%")

        def _error(msg):
            self._update_label.setText(f"Download failed: {msg}")
            self._install_btn.setEnabled(True)

        download_and_install(version, on_progress=_progress, on_error=_error)

    def set_server_running(self, running: bool):
        self._server_running = running
        if running:
            self._start_stop_btn.setText("Stop Server")
            self._start_stop_btn.setStyleSheet(self._btn_style("#dc2626", "#b91c1c"))
            self._status_label.setText("Server running…")
        else:
            self._start_stop_btn.setText("Start Server")
            self._start_stop_btn.setStyleSheet(self._btn_style("#2563eb", "#1d4ed8"))
            self._status_label.setText("Server stopped")

    def _run_elevated(self, exe: str, arg: str):
        if sys.platform == "win32":
            import ctypes
            ctypes.windll.shell32.ShellExecuteW(None, "runas", exe, arg, None, 1)
        else:
            subprocess.Popen([exe, arg])
