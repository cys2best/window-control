# src/gui/launcher.py
import sys
import subprocess
from PyQt5.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QLabel, QGroupBox, QDialog, QFrame
)
from PyQt5.QtCore import Qt, pyqtSignal

from config import PORT, VERSION, SUPABASE_URL, SUPABASE_ANON_KEY, VPS_SIGNALING_URL
from server.tailscale import has_tailscale, detect_local_ip, detect_tailscale_ip
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
    server_start_requested = pyqtSignal()
    server_stop_requested = pyqtSignal()
    window_selected = pyqtSignal(str)

    def __init__(self, parent=None, on_stop_server=None):
        super().__init__(parent)
        self.setWindowTitle(f"WindowControl Host v{VERSION}")
        self.resize(400, 460)
        self.setMinimumWidth(380)
        self._on_stop_server = on_stop_server
        self._active_streams_count = 0
        self._pending_update_version = None

        self._setup_ui()
        self._refresh_status()
        check_for_update(self._on_update_available)

    def closeEvent(self, event):
        """Minimize to tray on window close."""
        event.ignore()
        self.hide()

    def _setup_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setSpacing(14)
        layout.setContentsMargins(20, 20, 20, 20)

        self._setup_style()

        # --- Header ---
        header_widget = QWidget()
        header_layout = QVBoxLayout(header_widget)
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(4)

        title_row = QHBoxLayout()
        title_label = QLabel("WindowControl Host")
        title_label.setStyleSheet("font-size: 18px; font-weight: 700; color: #e8eaed;")
        version_label = QLabel(f"v{VERSION}")
        version_label.setStyleSheet("font-size: 12px; color: #8a8f98; padding-top: 4px;")
        title_row.addWidget(title_label)
        title_row.addWidget(version_label)
        title_row.addStretch()
        header_layout.addLayout(title_row)

        status_row = QHBoxLayout()
        status_row.setSpacing(8)

        self._status_dot = QWidget()
        self._status_dot.setFixedSize(10, 10)
        self._status_dot.setStyleSheet("background: #22c55e; border-radius: 5px;")
        status_row.addWidget(self._status_dot)

        self._status_label = QLabel(f"Server Running: :{PORT}")
        self._status_label.setStyleSheet("font-size: 13px; font-weight: 600; color: #22c55e;")
        status_row.addWidget(self._status_label)
        status_row.addStretch()
        header_layout.addLayout(status_row)

        layout.addWidget(header_widget)

        # --- Status Card ---
        status_group = QGroupBox("Host Status")
        group_layout = QVBoxLayout(status_group)
        group_layout.setSpacing(12)
        group_layout.setContentsMargins(14, 14, 14, 14)

        # 1. Account
        acc_layout = QVBoxLayout()
        acc_layout.setSpacing(2)
        acc_title = QLabel("Account")
        acc_title.setStyleSheet("font-size: 11px; font-weight: 600; color: #8a8f98; text-transform: uppercase;")
        self._account_label = QLabel("Detecting…")
        self._account_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self._account_label.setStyleSheet("font-size: 13px; color: #e8eaed;")
        acc_layout.addWidget(acc_title)
        acc_layout.addWidget(self._account_label)
        group_layout.addLayout(acc_layout)

        # Divider
        group_layout.addWidget(self._create_divider())

        # 2. Network
        net_layout = QVBoxLayout()
        net_layout.setSpacing(2)
        net_title = QLabel("Network")
        net_title.setStyleSheet("font-size: 11px; font-weight: 600; color: #8a8f98; text-transform: uppercase;")
        self._ip_label = QLabel("Detecting…")
        self._ip_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self._ip_label.setStyleSheet("font-size: 13px; color: #e8eaed;")
        net_layout.addWidget(net_title)
        net_layout.addWidget(self._ip_label)
        group_layout.addLayout(net_layout)

        # Divider
        group_layout.addWidget(self._create_divider())

        # 3. VPS Relay
        relay_layout = QVBoxLayout()
        relay_layout.setSpacing(2)
        relay_title = QLabel("VPS Relay")
        relay_title.setStyleSheet("font-size: 11px; font-weight: 600; color: #8a8f98; text-transform: uppercase;")
        self._relay_label = QLabel("Checking…")
        self._relay_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self._relay_label.setStyleSheet("font-size: 13px; color: #e8eaed;")
        relay_layout.addWidget(relay_title)
        relay_layout.addWidget(self._relay_label)
        group_layout.addLayout(relay_layout)

        # Divider
        group_layout.addWidget(self._create_divider())

        # 4. Active Streams
        streams_layout = QVBoxLayout()
        streams_layout.setSpacing(2)
        streams_title = QLabel("Active Streams")
        streams_title.setStyleSheet("font-size: 11px; font-weight: 600; color: #8a8f98; text-transform: uppercase;")
        self._streams_label = QLabel("Idle")
        self._streams_label.setStyleSheet("font-size: 13px; color: #6fd7d1; font-weight: 600;")
        streams_layout.addWidget(streams_title)
        streams_layout.addWidget(self._streams_label)
        group_layout.addLayout(streams_layout)

        layout.addWidget(status_group)

        # --- Update banner ---
        self._update_banner = QWidget()
        self._update_banner.setStyleSheet(
            "background: rgba(234,179,8,0.12); border: 1px solid rgba(234,179,8,0.4); border-radius: 6px;"
        )
        banner_layout = QVBoxLayout(self._update_banner)
        banner_layout.setContentsMargins(10, 8, 10, 8)
        banner_layout.setSpacing(6)

        self._update_label = QLabel()
        self._update_label.setStyleSheet("color:#eab308; font-size:13px; background:transparent; border:none;")
        self._update_label.setWordWrap(True)
        banner_layout.addWidget(self._update_label)

        self._install_btn = QPushButton("Install Update")
        self._install_btn.setMinimumHeight(32)
        self._install_btn.setStyleSheet(self._btn_style("#d97706", "#b45309", "#ffffff"))
        self._install_btn.clicked.connect(self._on_install_update)
        banner_layout.addWidget(self._install_btn)

        self._update_banner.hide()
        layout.addWidget(self._update_banner)

        layout.addStretch()

        # --- Actions ---
        actions_layout = QHBoxLayout()
        actions_layout.setSpacing(10)

        self._minimize_btn = QPushButton("Minimize to Tray")
        self._minimize_btn.setMinimumHeight(38)
        self._minimize_btn.setStyleSheet(self._btn_style("rgba(255,255,255,0.08)", "rgba(255,255,255,0.14)", "#e8eaed"))
        self._minimize_btn.clicked.connect(self.hide)
        actions_layout.addWidget(self._minimize_btn)

        self._stop_btn = QPushButton("Stop Server")
        self._stop_btn.setMinimumHeight(38)
        self._stop_btn.setStyleSheet(self._btn_style("rgba(239,68,68,0.16)", "rgba(239,68,68,0.28)", "#ef4444", border="1px solid rgba(239,68,68,0.4)"))
        self._stop_btn.clicked.connect(self._handle_stop_server)
        actions_layout.addWidget(self._stop_btn)

        layout.addLayout(actions_layout)

    def _create_divider(self) -> QFrame:
        divider = QFrame()
        divider.setFrameShape(QFrame.HLine)
        divider.setFrameShadow(QFrame.Sunken)
        divider.setStyleSheet("border: none; background: rgba(255,255,255,0.06); min-height: 1px; max-height: 1px;")
        return divider

    def _setup_style(self):
        self.setStyleSheet("""
            QMainWindow { background: #12141a; }
            QWidget { background: transparent; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; }
            QGroupBox {
                font-size: 13px;
                font-weight: 600;
                color: #e8eaed;
                border: 1px solid rgba(255,255,255,0.09);
                border-radius: 6px;
                margin-top: 10px;
                padding-top: 12px;
                background: #1b1e26;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 12px;
                padding: 0 6px;
                color: #8a8f98;
            }
        """)

    def _btn_style(self, bg: str, hover: str, text_color: str, border: str = "none") -> str:
        return f"""
            QPushButton {{
                background: {bg};
                color: {text_color};
                border: {border};
                border-radius: 6px;
                font-size: 13px;
                font-weight: 600;
                padding: 8px 14px;
            }}
            QPushButton:hover {{ background: {hover}; }}
            QPushButton:pressed {{ background: {hover}; }}
            QPushButton:disabled {{ background: rgba(255,255,255,0.04); color: #4a4e58; border: none; }}
        """

    def _refresh_status(self):
        self._refresh_account()
        self._refresh_ip()
        self._refresh_relay()
        self.update_active_streams(0)

    def _refresh_account(self):
        if not SUPABASE_URL:
            self._account_label.setText("Auth disabled (LAN mode)")
            return
        try:
            from gui.supabase_login import load_cached_session
            session = load_cached_session()
            if session:
                user = session.get("user", {})
                email = user.get("email") or user.get("id") or "Signed in"
                self._account_label.setText(email)
            else:
                self._account_label.setText("Not signed in")
        except Exception:
            self._account_label.setText("Not signed in")

    def _refresh_ip(self):
        lan = detect_local_ip()
        ts = detect_tailscale_ip() if has_tailscale() else None
        if ts:
            self._ip_label.setText(f"LAN: {lan}:{PORT}\nTailscale: {ts}:{PORT}")
        else:
            self._ip_label.setText(f"LAN: {lan}:{PORT}\nTailscale: Inactive")

    def _refresh_relay(self):
        if VPS_SIGNALING_URL:
            self._relay_label.setText("Connected")
            self._relay_label.setStyleSheet("color: #22c55e; font-size: 13px; font-weight: 500;")
        else:
            self._relay_label.setText("Offline (disabled)")
            self._relay_label.setStyleSheet("color: #8a8f98; font-size: 13px; font-weight: 500;")

    def update_active_streams(self, count: int):
        self._active_streams_count = count
        if count <= 0:
            self._streams_label.setText("Idle")
            self._streams_label.setStyleSheet("font-size: 13px; color: #8a8f98; font-weight: 500;")
        elif count == 1:
            self._streams_label.setText("1 client streaming")
            self._streams_label.setStyleSheet("font-size: 13px; color: #22c55e; font-weight: 600;")
        else:
            self._streams_label.setText(f"{count} clients streaming")
            self._streams_label.setStyleSheet("font-size: 13px; color: #22c55e; font-weight: 600;")

    def _handle_stop_server(self):
        if self._on_stop_server is not None:
            self._on_stop_server()
        self._status_dot.setStyleSheet("background: #ef4444; border-radius: 5px;")
        self._status_label.setText("Server Stopped")
        self._status_label.setStyleSheet("font-size: 13px; font-weight: 600; color: #ef4444;")
        self._stop_btn.setEnabled(False)

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
