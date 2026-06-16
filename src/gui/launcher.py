# src/gui/launcher.py
import sys
import threading
import subprocess
from PyQt5.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QLabel, QComboBox, QGroupBox, QSizePolicy, QScrollArea
)
from PyQt5.QtCore import Qt, pyqtSignal, QThread, pyqtSlot, QTimer
from PyQt5.QtGui import QPixmap, QImage
import qrcode
import io

from config import PORT, QUALITY_MAP, DEFAULT_QUALITY, VERSION, GITHUB_REPO
from server.tailscale import get_best_ip, has_tailscale
from server.stream import CaptureState
from updater import check_for_update


class LauncherWindow(QMainWindow):
    server_start_requested = pyqtSignal()
    server_stop_requested = pyqtSignal()
    quality_changed = pyqtSignal(int)
    window_selected = pyqtSignal(int, str)
    _lock_signal = pyqtSignal()
    _unlock_signal = pyqtSignal()

    def __init__(self, state: CaptureState, parent=None):
        super().__init__(parent)
        self._state = state
        self._server_running = False
        self.setWindowTitle(f"WindowControl v{VERSION}")
        self.setMinimumWidth(420)
        self.resize(460, 600)
        self._setup_ui()
        self._refresh_ip()
        check_for_update(self._on_update_available)
        self._disable_lock_on_disconnect()
        QTimer.singleShot(1500, self._auto_install_service)
        # Refresh service status every 10s
        self._svc_refresh_timer = QTimer()
        self._svc_refresh_timer.timeout.connect(self._refresh_service_status_label)
        self._svc_refresh_timer.start(10000)
        # Wire cross-thread lock/unlock signals
        self._lock_signal.connect(self._on_lock_ui)
        self._unlock_signal.connect(self._on_unlock_ui)

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

        # --- Auto-Unlock group ---
        self._setup_unlock_group(layout)

        # --- Lock screen service status (compact, no manual buttons) ---
        self._service_status_label = QLabel()
        self._service_status_label.setStyleSheet("font-size: 12px; color: #666; padding: 4px 0;")
        self._service_status_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(self._service_status_label)

        # --- Update banner ---
        self._update_label = QLabel()
        self._update_label.setOpenExternalLinks(True)
        self._update_label.setStyleSheet(
            "background:#fffbe6; color:#7a6000; border:1px solid #f0c040;"
            "border-radius:6px; padding:8px; font-size:13px;"
        )
        self._update_label.setWordWrap(True)
        self._update_label.hide()
        layout.addWidget(self._update_label)

        # --- Status bar ---
        self._status_label = QLabel("Server stopped")
        self._status_label.setStyleSheet("font-size: 13px; color: #666;")
        layout.addWidget(self._status_label)

        self._refresh_service_status_label()

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
        url = f"https://github.com/{GITHUB_REPO}/releases/latest"
        self._update_label.setText(
            f'Update available: v{latest} — '
            f'<a href="{url}">Download</a>'
        )
        self._update_label.show()

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

    def _setup_unlock_group(self, layout):
        unlock_group = QGroupBox("Auto-Unlock")
        unlock_layout = QVBoxLayout(unlock_group)
        unlock_layout.setSpacing(8)
        unlock_layout.setContentsMargins(12, 12, 12, 12)

        info = QLabel("Store your Windows password to auto-unlock after lock screen.")
        info.setWordWrap(True)
        info.setStyleSheet("font-size: 13px; color: #64748b;")
        unlock_layout.addWidget(info)

        pw_row = QHBoxLayout()
        self._set_pw_btn = QPushButton("Set Unlock Password")
        self._set_pw_btn.setMinimumHeight(38)
        self._set_pw_btn.setStyleSheet(self._btn_style("#0f766e", "#0d9488"))
        self._set_pw_btn.clicked.connect(self._on_set_unlock_password)
        self._clear_pw_btn = QPushButton("Clear")
        self._clear_pw_btn.setMinimumHeight(38)
        self._clear_pw_btn.setMaximumWidth(80)
        self._clear_pw_btn.setStyleSheet(self._btn_style("#64748b", "#475569"))
        self._clear_pw_btn.clicked.connect(self._on_clear_unlock_password)
        pw_row.addWidget(self._set_pw_btn)
        pw_row.addWidget(self._clear_pw_btn)
        unlock_layout.addLayout(pw_row)
        layout.addWidget(unlock_group)

    def _on_set_unlock_password(self):
        from PyQt5.QtWidgets import QInputDialog, QLineEdit
        pw, ok = QInputDialog.getText(
            self, "Set Unlock Password",
            "Enter your Windows password for auto-unlock:",
            QLineEdit.Password
        )
        if ok and pw:
            from service.auto_unlock import store_password
            store_password(pw)
            self._status_label.setText("Unlock password saved.")

    def _on_clear_unlock_password(self):
        from service.auto_unlock import delete_password
        delete_password()
        self._status_label.setText("Unlock password cleared.")

    @staticmethod
    def _disable_lock_on_disconnect():
        """Prevent Windows from locking session on RDP disconnect."""
        if sys.platform != "win32":
            return
        try:
            import winreg
            # Disable screen saver lock
            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Control Panel\Desktop",
                0, winreg.KEY_SET_VALUE
            )
            winreg.SetValueEx(key, "ScreenSaverIsSecure", 0, winreg.REG_SZ, "0")
            winreg.SetValueEx(key, "ScreenSaveActive", 0, winreg.REG_SZ, "0")
            winreg.CloseKey(key)
        except Exception:
            pass

    def _auto_install_service(self):
        """Install lock screen service automatically if not already installed/running."""
        if sys.platform != "win32":
            return
        from gui.service_status import get_service_status
        status = get_service_status()
        if status == "not_installed":
            self._service_status_label.setText("Lock screen service: installing…")
            self._run_elevated(sys.executable, "--install")
            QTimer.singleShot(5000, self._refresh_service_status_label)

    def _refresh_service_status_label(self):
        if sys.platform != "win32":
            self._service_status_label.setText("Lock screen service: not available (Windows only)")
            return
        from gui.service_status import get_service_status
        status = get_service_status()
        if status == "running":
            self._service_status_label.setText("● Lock screen service: running")
            self._service_status_label.setStyleSheet("font-size: 12px; color: #16a34a; padding: 4px 0;")
        elif status == "stopped":
            self._service_status_label.setText("● Lock screen service: stopped — restart app to retry")
            self._service_status_label.setStyleSheet("font-size: 12px; color: #dc2626; padding: 4px 0;")
        else:
            self._service_status_label.setText("○ Lock screen service: not installed")
            self._service_status_label.setStyleSheet("font-size: 12px; color: #94a3b8; padding: 4px 0;")

    def on_service_lock(self):
        """Called from pipe thread — emit to Qt main thread."""
        self._lock_signal.emit()

    def on_service_unlock(self):
        """Called from pipe thread — emit to Qt main thread."""
        self._unlock_signal.emit()

    def _on_lock_ui(self):
        self._status_label.setText("Screen locked")
        self._status_label.setStyleSheet("font-size: 13px; color: #dc2626;")

    def _on_unlock_ui(self):
        self._status_label.setText("Screen unlocked — server running…")
        self._status_label.setStyleSheet("font-size: 13px; color: #16a34a;")

    def _run_elevated(self, exe: str, arg: str):
        if sys.platform == "win32":
            import ctypes
            ctypes.windll.shell32.ShellExecuteW(None, "runas", exe, arg, None, 1)
        else:
            subprocess.Popen([exe, arg])
