# src/gui/launcher.py
import sys
import subprocess
import time
import html
import threading
from PyQt5.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QLabel, QGroupBox, QFrame, QListWidget, QListWidgetItem
)
from PyQt5.QtCore import Qt, QTimer, pyqtSignal, pyqtSlot
from PyQt5.QtGui import QFont

from config import PORT, VERSION
from server.pairing import PairingStore
from server.tailscale import has_tailscale, detect_local_ip, detect_tailscale_ip
from updater import check_for_update
from gui.theme import (
    CANVAS, CYAN, DIM, HAIRLINE, INK, MINT, MUTED, SURFACE,
    SURFACE_RAISED, TANGERINE, DESTRUCTIVE_HOVER, register_fonts,
)


class LauncherWindow(QMainWindow):
    server_start_requested = pyqtSignal()
    server_stop_requested = pyqtSignal()
    window_selected = pyqtSignal(str)
    remote_pairing_changed = pyqtSignal(object, str)
    _remote_pairing_ready = pyqtSignal(int, object, str)

    def __init__(self, parent=None, on_stop_server=None, pairing=None, remote_pairing=None):
        super().__init__(parent)
        self.setWindowTitle(f"EmuCtrl Host v{VERSION}")
        self.setFixedSize(400, 560)
        self._enable_windows_dark_title_bar()
        self._on_stop_server = on_stop_server
        self._pairing = pairing if pairing is not None else PairingStore()
        self._remote_pairing = remote_pairing
        self._remote_request_id = 0
        self.remote_pairing_changed.connect(self.set_remote_pairing)
        self._remote_pairing_ready.connect(self._on_remote_pairing_ready)
        self._device_ids: list[str] | None = None
        self._active_streams_count = 0
        self._pending_update_version = None
        self._fonts = register_fonts()

        self._setup_ui()
        self._refresh_status()
        self._fit_height()
        # The server thread pairs devices and the code expires on its own, so
        # the group is polled rather than pushed to.
        self._pairing_timer = QTimer(self)
        self._pairing_timer.setInterval(1000)
        self._pairing_timer.timeout.connect(self._refresh_pairing)
        self._pairing_timer.start()
        check_for_update(self._on_update_available)

    def _fit_height(self) -> None:
        """Keep the fixed width but never be shorter than the content: the
        update banner and the save warning appear after construction. Callers
        that just showed or hid a widget schedule this through the event loop,
        because Qt refreshes the affected size hints only once the pending
        layout requests have been delivered."""
        central = self.centralWidget()
        central.layout().activate()
        self.setFixedSize(400, max(560, central.sizeHint().height()))

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

        # 2. Active Streams
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

        # --- Paired devices ---
        devices_group = QGroupBox("Paired Devices")
        devices_layout = QVBoxLayout(devices_group)
        devices_layout.setSpacing(8)
        devices_layout.setContentsMargins(14, 14, 14, 14)

        pair_row = QHBoxLayout()
        pair_row.setSpacing(10)
        self._pair_btn = QPushButton("Pair device")
        self._pair_btn.setFixedHeight(32)
        self._pair_btn.setStyleSheet(self._btn_style(SURFACE, CYAN, INK))
        self._pair_btn.clicked.connect(self._start_pairing)
        pair_row.addWidget(self._pair_btn)
        self._pair_code_label = QLabel("")
        self._pair_code_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self._pair_code_label.setFont(self._mono_font(13, QFont.DemiBold))
        self._pair_code_label.setStyleSheet(f"color: {MINT};")
        pair_row.addWidget(self._pair_code_label, 1)
        devices_layout.addLayout(pair_row)

        self._remote_link_label = QLabel("")
        self._remote_link_label.setWordWrap(True)
        self._remote_link_label.setTextInteractionFlags(Qt.TextBrowserInteraction)
        self._remote_link_label.setOpenExternalLinks(True)
        self._remote_link_label.hide()
        devices_layout.addWidget(self._remote_link_label)
        self._remote_error_label = QLabel("")
        self._remote_error_label.setWordWrap(True)
        self._remote_error_label.setStyleSheet(f"color: {DESTRUCTIVE_HOVER};")
        self._remote_error_label.hide()
        devices_layout.addWidget(self._remote_error_label)

        self._device_list = QListWidget()
        self._device_list.setFixedHeight(64)
        self._device_list.setFont(self._mono_font(10))
        self._device_list.setStyleSheet(
            f"QListWidget {{ background: {SURFACE}; color: {INK};"
            f" border: 1px solid {HAIRLINE}; border-radius: 6px; }}"
        )
        devices_layout.addWidget(self._device_list)

        device_actions = QHBoxLayout()
        device_actions.setSpacing(10)
        self._remove_device_btn = QPushButton("Remove selected")
        self._remove_device_btn.setFixedHeight(32)
        self._remove_device_btn.setStyleSheet(self._btn_style(SURFACE, DESTRUCTIVE_HOVER, INK))
        self._remove_device_btn.clicked.connect(self._remove_selected_device)
        device_actions.addWidget(self._remove_device_btn)
        self._unpair_all_btn = QPushButton("Unpair all")
        self._unpair_all_btn.setFixedHeight(32)
        self._unpair_all_btn.setStyleSheet(self._btn_style(SURFACE, DESTRUCTIVE_HOVER, INK))
        self._unpair_all_btn.clicked.connect(self._unpair_all)
        device_actions.addWidget(self._unpair_all_btn)
        devices_layout.addLayout(device_actions)

        # Shown only when a removal could not be written to disk. A separate
        # label because the code label is rewritten on every refresh tick.
        self._save_warning_label = QLabel("Could not save; device may return after restart")
        self._save_warning_label.setWordWrap(True)
        self._save_warning_label.setStyleSheet(f"color: {DESTRUCTIVE_HOVER};")
        self._save_warning_label.setVisible(False)
        devices_layout.addWidget(self._save_warning_label)

        layout.addWidget(devices_group)

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
        self._refresh_ip()
        self._refresh_pairing()
        self.update_active_streams(0)

    def _refresh_ip(self):
        lan = detect_local_ip()
        ts = detect_tailscale_ip() if has_tailscale() else None
        if ts:
            self._ip_label.setText(f"LAN: {lan}:{PORT}\nTailscale: {ts}:{PORT}")
        else:
            self._ip_label.setText(f"LAN: {lan}:{PORT}\nTailscale: Inactive")

    @pyqtSlot(object, str)
    def set_remote_pairing(self, remote_pairing, error=""):
        """Attach on the Qt thread; workers emit remote_pairing_changed.

        Host lifecycle owns shutdown() of the previous manager. Attachment
        preserves the local pairing window and waits for the next owner click.
        """
        self._remote_request_id += 1
        self._remote_pairing = remote_pairing
        self._remote_error_label.setText(error)
        self._remote_error_label.setVisible(bool(error))
        self._refresh_pairing()
        QTimer.singleShot(0, self._fit_height)

    def _start_pairing(self):
        self._pairing.start_pairing()
        self._remote_request_id += 1
        request_id = self._remote_request_id
        self._remote_error_label.clear()
        self._remote_error_label.hide()
        self._refresh_pairing()
        if self._remote_pairing is not None:
            remote = self._remote_pairing
            window = self._pairing.pairing_window()

            def open_remote():
                from server.remote_pairing import RemotePairingError
                try:
                    invitation = remote.open(window_generation=window.generation)
                except RemotePairingError as error:
                    self._remote_pairing_ready.emit(request_id, None, str(error))
                except Exception:
                    self._remote_pairing_ready.emit(request_id, None, "Remote pairing is unavailable")
                else:
                    self._remote_pairing_ready.emit(request_id, invitation, "")

            threading.Thread(target=open_remote, daemon=True, name="remote-pairing").start()

    def _on_remote_pairing_ready(self, request_id, invitation, error):
        if request_id != self._remote_request_id:
            return
        self._remote_error_label.setText(error)
        self._remote_error_label.setVisible(bool(error))
        self._refresh_pairing()
        QTimer.singleShot(0, self._fit_height)

    def _refresh_pairing(self):
        active = self._pairing.active_code()
        if active is None:
            self._pair_code_label.setText("")
            self._pair_btn.setText("Pair device")
        else:
            code, remaining = active
            self._pair_code_label.setText(
                f"{code[:3]} {code[3:]}  ·  {remaining // 60}:{remaining % 60:02d}"
            )
            self._pair_btn.setText("New code")

        invitation = self._remote_pairing.active_invitation() if self._remote_pairing is not None else None
        if invitation is None:
            self._remote_link_label.clear()
            self._remote_link_label.hide()
        else:
            url = html.escape(invitation.url, quote=True)
            self._remote_link_label.setText(f'<a href="{url}">{url}</a>')
            self._remote_link_label.show()

        devices = self._pairing.list_devices()
        ids = [device.id for device in devices]
        if ids != self._device_ids:
            self._device_ids = ids
            self._device_list.clear()
            for device in devices:
                paired_on = time.strftime("%Y-%m-%d", time.localtime(device.created_at))
                item = QListWidgetItem(f"{device.name}  ·  {paired_on}")
                item.setData(Qt.UserRole, device.id)
                self._device_list.addItem(item)
        self._remove_device_btn.setEnabled(bool(devices))
        self._unpair_all_btn.setEnabled(bool(devices))

    def _remove_selected_device(self):
        item = self._device_list.currentItem()
        if item is None:
            return
        self._pairing.remove_device(item.data(Qt.UserRole))
        self._refresh_pairing()
        self._show_save_warning()

    def _unpair_all(self):
        self._pairing.remove_all()
        self._refresh_pairing()
        self._show_save_warning()

    def _show_save_warning(self):
        self._save_warning_label.setVisible(not self._pairing.last_save_ok)
        QTimer.singleShot(0, self._fit_height)

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
        QTimer.singleShot(0, self._fit_height)

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
