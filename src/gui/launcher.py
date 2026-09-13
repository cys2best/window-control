# src/gui/launcher.py
import sys
import subprocess
from PyQt5.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QLabel, QGroupBox, QDialog, QFrame
)
from PyQt5.QtCore import Qt, QTimer, pyqtSignal
from PyQt5.QtGui import QFont, QFontMetrics

from config import PORT, VERSION, SUPABASE_URL, SUPABASE_ANON_KEY, VPS_SIGNALING_URL
from server.tailscale import has_tailscale, detect_local_ip, detect_tailscale_ip
from updater import check_for_update
from gui.theme import (
    CANVAS, CYAN, DIM, HAIRLINE, INK, MINT, MUTED, SURFACE,
    SURFACE_RAISED, TANGERINE, DESTRUCTIVE_HOVER, register_fonts,
)


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
        self.setWindowTitle(f"EmuCtrl Host v{VERSION}")
        self.setFixedSize(400, 460)
        self._enable_windows_dark_title_bar()
        self._on_stop_server = on_stop_server
        self._active_streams_count = 0
        self._pending_update_version = None
        self._fonts = register_fonts()

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
        title_label = QLabel("EmuCtrl Host")
        title_label.setFont(self._ui_font(18, QFont.Bold))
        title_label.setStyleSheet(f"color: {INK};")
        version_label = QLabel(f"v{VERSION}")
        version_label.setFont(self._mono_font(9.5))
        version_label.setStyleSheet(f"color: {DIM}; padding-top: 4px;")
        title_row.addWidget(title_label)
        title_row.addWidget(version_label)
        title_row.addStretch()
        header_layout.addLayout(title_row)

        status_row = QHBoxLayout()
        status_row.setSpacing(8)

        self._status_dot = QWidget()
        self._status_dot.setFixedSize(10, 10)
        self._status_dot.setStyleSheet(f"background: {MINT}; border-radius: 5px;")
        status_row.addWidget(self._status_dot)

        self._status_label = QLabel(f"Server Running: :{PORT}")
        self._status_label.setFont(self._mono_font(10, QFont.DemiBold))
        self._status_label.setStyleSheet(f"color: {MINT};")
        status_row.addWidget(self._status_label)
        status_row.addStretch()
        header_layout.addLayout(status_row)

        layout.addWidget(header_widget)

        # --- Account ---
        self._account_band = QWidget()
        self._account_band.setFixedHeight(47)
        self._account_band.setStyleSheet(
            f"background: {SURFACE}; border: 1px solid {HAIRLINE}; border-radius: 6px;"
        )
        account_layout = QHBoxLayout(self._account_band)
        account_layout.setContentsMargins(9, 6, 9, 6)
        account_layout.setSpacing(8)

        self._account_avatar = QLabel()
        self._account_avatar.setAlignment(Qt.AlignCenter)
        self._account_avatar.setFixedSize(28, 28)
        self._account_avatar.setStyleSheet(
            f"background: {CYAN}; color: {CANVAS}; border-radius: 14px;"
        )
        self._account_avatar.setFont(self._ui_font(11, QFont.DemiBold))
        account_layout.addWidget(self._account_avatar)

        account_text = QWidget()
        account_text.setFixedHeight(30)
        account_text_layout = QVBoxLayout(account_text)
        account_text_layout.setContentsMargins(0, 0, 0, 0)
        account_text_layout.setSpacing(1)
        self._account_name = QLabel()
        self._account_name.setFixedHeight(15)
        self._account_name.setWordWrap(False)
        self._account_name.setFont(self._ui_font(12, QFont.DemiBold))
        self._account_name.setStyleSheet(f"color: {INK};")
        self._account_email_role = QLabel()
        self._account_email_role.setFixedHeight(14)
        self._account_email_role.setWordWrap(False)
        self._account_email_role.setFont(self._mono_font(9.5))
        self._account_email_role.setStyleSheet(f"color: {MUTED};")
        account_text_layout.addWidget(self._account_name)
        account_text_layout.addWidget(self._account_email_role)
        account_layout.addWidget(account_text, 1)

        self._sign_out_btn = QPushButton("Sign out")
        self._sign_out_btn.setFixedHeight(28)
        self._sign_out_btn.setStyleSheet(
            self._btn_style(SURFACE_RAISED, DESTRUCTIVE_HOVER, INK)
        )
        self._sign_out_btn.clicked.connect(self._sign_out)
        account_layout.addWidget(self._sign_out_btn)

        self._sign_in_btn = QPushButton("Sign in")
        self._sign_in_btn.setFixedHeight(28)
        self._sign_in_btn.setStyleSheet(self._btn_style(SURFACE_RAISED, CYAN, INK))
        self._sign_in_btn.clicked.connect(self._show_sign_in)
        account_layout.addWidget(self._sign_in_btn)
        layout.addWidget(self._account_band)

        # --- Status Card ---
        status_group = QGroupBox("Host Status")
        group_layout = QVBoxLayout(status_group)
        group_layout.setSpacing(12)
        group_layout.setContentsMargins(14, 14, 14, 14)

        # 1. Network
        net_layout = QVBoxLayout()
        net_layout.setSpacing(2)
        net_title = QLabel("Network")
        net_title.setStyleSheet(f"font-size: 11px; font-weight: 600; color: {MUTED};")
        self._ip_label = QLabel("Detecting…")
        self._ip_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self._ip_label.setFont(self._mono_font(10))
        self._ip_label.setStyleSheet(f"color: {INK};")
        net_layout.addWidget(net_title)
        net_layout.addWidget(self._ip_label)
        group_layout.addLayout(net_layout)

        # Divider
        group_layout.addWidget(self._create_divider())

        # 2. VPS Relay
        relay_layout = QVBoxLayout()
        relay_layout.setSpacing(2)
        relay_title = QLabel("VPS Relay")
        relay_title.setStyleSheet(f"font-size: 11px; font-weight: 600; color: {MUTED};")
        self._relay_label = QLabel("Checking…")
        self._relay_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self._relay_label.setFont(self._mono_font(10))
        self._relay_label.setStyleSheet(f"color: {INK};")
        relay_layout.addWidget(relay_title)
        relay_layout.addWidget(self._relay_label)
        group_layout.addLayout(relay_layout)

        # Divider
        group_layout.addWidget(self._create_divider())

        # 3. Active Streams
        streams_layout = QVBoxLayout()
        streams_layout.setSpacing(2)
        streams_title = QLabel("Active Streams")
        streams_title.setStyleSheet(f"font-size: 11px; font-weight: 600; color: {MUTED};")
        self._streams_label = QLabel("Idle")
        self._streams_label.setFont(self._mono_font(10, QFont.DemiBold))
        self._streams_label.setStyleSheet(f"color: {MINT};")
        streams_layout.addWidget(streams_title)
        streams_layout.addWidget(self._streams_label)
        group_layout.addLayout(streams_layout)

        layout.addWidget(status_group)

        # --- Update banner ---
        self._update_banner = QWidget()
        self._update_banner.setStyleSheet(
            f"background: {SURFACE_RAISED}; border: 1px solid {TANGERINE}; border-radius: 6px;"
        )
        banner_layout = QVBoxLayout(self._update_banner)
        banner_layout.setContentsMargins(10, 8, 10, 8)
        banner_layout.setSpacing(6)

        self._update_label = QLabel()
        self._update_label.setFont(self._ui_font(12))
        self._update_label.setStyleSheet(f"color: {TANGERINE}; background: transparent; border: none;")
        self._update_label.setWordWrap(True)
        banner_layout.addWidget(self._update_label)

        self._install_btn = QPushButton("Install Update")
        self._install_btn.setMinimumHeight(32)
        self._install_btn.setStyleSheet(self._btn_style(TANGERINE, INK, CANVAS))
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
        self._minimize_btn.setStyleSheet(self._btn_style(SURFACE_RAISED, HAIRLINE, INK))
        self._minimize_btn.clicked.connect(self.hide)
        actions_layout.addWidget(self._minimize_btn)

        self._stop_btn = QPushButton("Stop Server")
        self._stop_btn.setMinimumHeight(38)
        self._stop_btn.setStyleSheet(self._btn_style(SURFACE_RAISED, TANGERINE, TANGERINE, border=f"1px solid {TANGERINE}"))
        self._stop_btn.clicked.connect(self._handle_stop_server)
        actions_layout.addWidget(self._stop_btn)

        layout.addLayout(actions_layout)

    def _enable_windows_dark_title_bar(self) -> None:
        """Use native dark Windows chrome; other platforms keep their default."""
        if sys.platform != "win32":
            return
        try:
            import ctypes
            enabled = ctypes.c_int(1)
            # DWMWA_USE_IMMERSIVE_DARK_MODE is 20 on current Windows 11.
            ctypes.windll.dwmapi.DwmSetWindowAttribute(
                int(self.winId()), 20, ctypes.byref(enabled), ctypes.sizeof(enabled)
            )
        except Exception:
            pass

    def _create_divider(self) -> QFrame:
        divider = QFrame()
        divider.setFrameShape(QFrame.HLine)
        divider.setFrameShadow(QFrame.Sunken)
        divider.setStyleSheet(f"border: none; background: {HAIRLINE}; min-height: 1px; max-height: 1px;")
        return divider

    def _setup_style(self):
        self.setStyleSheet(f"""
            QMainWindow {{ background: {CANVAS}; }}
            QWidget {{ background: transparent; font-family: '{self._fonts.ui}'; }}
            QGroupBox {{
                font-size: 13px;
                font-weight: 600;
                color: {INK};
                border: 1px solid {HAIRLINE};
                border-radius: 6px;
                margin-top: 10px;
                padding-top: 12px;
                background: {SURFACE_RAISED};
            }}
            QGroupBox::title {{
                subcontrol-origin: margin;
                left: 12px;
                padding: 0 6px;
                color: {MUTED};
            }}
        """)

    def _ui_font(self, point_size: float, weight: int = QFont.Normal) -> QFont:
        font = QFont(self._fonts.ui)
        font.setPointSizeF(point_size)
        font.setWeight(weight)
        return font

    def _mono_font(self, point_size: float, weight: int = QFont.Normal) -> QFont:
        font = QFont(self._fonts.mono)
        font.setPointSizeF(point_size)
        font.setWeight(weight)
        return font

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
            QPushButton:disabled {{ background: {SURFACE}; color: {DIM}; border: none; }}
        """

    def _refresh_status(self):
        self._refresh_account()
        self._refresh_ip()
        self._refresh_relay()
        self.update_active_streams(0)

    def _refresh_account(self):
        if not SUPABASE_URL:
            self._set_account_state("EC", "Auth disabled (LAN mode)", "", signed_in=False)
            return
        try:
            from gui.supabase_login import load_cached_session
            session = load_cached_session()
            if session:
                user = session.get("user", {})
                metadata = user.get("user_metadata") or {}
                email = user.get("email") or user.get("id") or "Signed in"
                name = metadata.get("display_name") or email
                role = metadata.get("role") or user.get("role")
                detail = f"{email} · {role}" if role else email
                self._set_account_state(self._initials(name), name, detail, signed_in=True)
            else:
                self._set_account_state("?", "Not signed in", "", signed_in=False)
        except Exception:
            self._set_account_state("?", "Not signed in", "", signed_in=False)

    def _set_account_state(self, avatar: str, name: str, detail: str, *, signed_in: bool) -> None:
        self._account_avatar.setText(avatar)
        self._account_full_name = name
        self._account_full_email_role = detail
        self._account_name.setText(name)
        self._account_email_role.setText(detail)
        # The first refresh runs before Qt has assigned the label's final
        # width. Re-elide on the next event-loop turn to avoid overlapping
        # account text in the compact 47px strip.
        QTimer.singleShot(0, self._elide_account_detail)
        self._sign_out_btn.setVisible(signed_in)
        self._sign_in_btn.setVisible(not signed_in and bool(SUPABASE_URL))

    def _initials(self, name: str) -> str:
        words = [word for word in name.split() if word]
        if len(words) >= 2:
            return (words[0][0] + words[-1][0]).upper()
        return name[:2].upper()

    def _elide_account_detail(self) -> None:
        available = max(0, self._account_email_role.width())
        self._account_email_role.setText(
            QFontMetrics(self._account_email_role.font()).elidedText(
                self._account_full_email_role, Qt.ElideRight, available
            )
        )
        name_width = max(0, self._account_name.width())
        self._account_name.setText(
            QFontMetrics(self._account_name.font()).elidedText(
                self._account_full_name, Qt.ElideRight, name_width
            )
        )

    def _sign_out(self) -> None:
        from gui.supabase_login import clear_cached_session
        clear_cached_session()
        self._refresh_account()

    def _show_sign_in(self) -> None:
        if not SUPABASE_URL:
            return
        from gui.supabase_login import LoginDialog
        dialog = LoginDialog(SUPABASE_URL, SUPABASE_ANON_KEY, self)
        if dialog.exec_() == QDialog.Accepted:
            self._refresh_account()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "_account_email_role"):
            self._elide_account_detail()

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
            self._relay_label.setStyleSheet(f"color: {MINT};")
        else:
            self._relay_label.setText("Offline (disabled)")
            self._relay_label.setStyleSheet(f"color: {MUTED};")

    def update_active_streams(self, count: int):
        self._active_streams_count = count
        if count <= 0:
            self._streams_label.setText("Idle")
            self._streams_label.setStyleSheet(f"color: {MUTED};")
        elif count == 1:
            self._streams_label.setText("1 client streaming")
            self._streams_label.setStyleSheet(f"color: {MINT};")
        else:
            self._streams_label.setText(f"{count} clients streaming")
            self._streams_label.setStyleSheet(f"color: {MINT};")

    def _handle_stop_server(self):
        if self._on_stop_server is not None:
            self._on_stop_server()
        self._status_dot.setStyleSheet(f"background: {TANGERINE}; border-radius: 5px;")
        self._status_label.setText("Server Stopped")
        self._status_label.setStyleSheet(f"color: {TANGERINE};")
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
