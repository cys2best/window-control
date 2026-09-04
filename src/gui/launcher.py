# src/gui/launcher.py
import sys
import subprocess
from PyQt5.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout,
    QPushButton, QLabel, QGroupBox, QScrollArea, QDialog
)
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QPixmap, QImage
import qrcode
import io

from config import PORT, VERSION, SUPABASE_URL, SUPABASE_ANON_KEY
from server.tailscale import get_best_ip, has_tailscale
from updater import check_for_update


def maybe_show_login(parent=None) -> bool:
    """Return True if it's OK to proceed to the main window (auth disabled,
    or the user completed sign-in / had a cached session)."""
    if not SUPABASE_URL:
        return True
    from gui.supabase_login import LoginDialog, load_cached_session
    if load_cached_session() is not None:
        return True
    dialog = LoginDialog(SUPABASE_URL, SUPABASE_ANON_KEY, parent)
    return dialog.exec_() == QDialog.Accepted


class LauncherWindow(QMainWindow):
    def __init__(self, parent=None):
        super().__init__(parent)
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
        self._ip_label.setStyleSheet("font-size: 14px; color: #e8eaed; background: transparent; border: none;")
        server_layout.addWidget(self._ip_label)

        self._url_label = QLabel("")
        self._url_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self._url_label.setStyleSheet("font-size: 13px; color: #8a8f98; background: transparent; border: none;")
        server_layout.addWidget(self._url_label)

        # QR stays on a white card regardless of theme -- inverting it
        # risks scan reliability on some phone cameras, and a code is the
        # one element where "matches the dark UI" matters less than
        # "a phone can actually read it".
        self._qr_label = QLabel()
        self._qr_label.setAlignment(Qt.AlignCenter)
        self._qr_label.setFixedHeight(200)
        self._qr_label.setStyleSheet("background: #ffffff; border-radius: 4px;")
        server_layout.addWidget(self._qr_label)

        layout.addWidget(server_group)

        # --- Update banner ---
        self._update_banner = QWidget()
        self._update_banner.setStyleSheet(
            "background: rgba(234,179,8,0.12); border: 1px solid rgba(234,179,8,0.4); border-radius: 4px;"
        )
        banner_layout = QVBoxLayout(self._update_banner)
        banner_layout.setContentsMargins(10, 8, 10, 8)
        banner_layout.setSpacing(6)

        self._update_label = QLabel()
        self._update_label.setStyleSheet("color:#eab308; font-size:13px; background:transparent; border:none;")
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
        self._status_label = QLabel("Server running…")
        self._status_label.setStyleSheet("font-size: 13px; color: #22c55e; background: transparent; border: none;")
        layout.addWidget(self._status_label)


    def _setup_style(self):
        self.setStyleSheet("""
            QMainWindow { background: #12141a; }
            QGroupBox {
                font-size: 14px;
                font-weight: 600;
                color: #e8eaed;
                border: 1px solid rgba(255,255,255,0.09);
                border-radius: 4px;
                margin-top: 8px;
                padding-top: 8px;
                background: #1b1e26;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 12px;
                padding: 0 6px;
                color: #e8eaed;
            }
            QScrollArea { border: none; background: #12141a; }
            QWidget#qt_scrollarea_viewport { background: #12141a; }
        """)

    def _btn_style(self, bg: str, hover: str) -> str:
        return f"""
            QPushButton {{
                background: {bg};
                color: #12141a;
                border: none;
                border-radius: 4px;
                font-size: 15px;
                font-weight: 600;
                padding: 10px;
            }}
            QPushButton:hover {{ background: {hover}; }}
            QPushButton:pressed {{ background: {hover}; }}
            QPushButton:disabled {{ background: #4a4e58; color: #8a8f98; }}
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

    def _run_elevated(self, exe: str, arg: str):
        if sys.platform == "win32":
            import ctypes
            ctypes.windll.shell32.ShellExecuteW(None, "runas", exe, arg, None, 1)
        else:
            subprocess.Popen([exe, arg])
