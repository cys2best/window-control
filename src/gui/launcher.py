# src/gui/launcher.py
import sys
import subprocess
from PyQt5.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QLabel, QComboBox, QFrame, QSizePolicy,
    QScrollArea, QApplication
)
from PyQt5.QtCore import Qt, pyqtSignal, QTimer, QSize
from PyQt5.QtGui import QPixmap, QImage, QFont, QWheelEvent
import qrcode
import io

from config import PORT, QUALITY_MAP, DEFAULT_QUALITY, VERSION, GITHUB_REPO
from server.tailscale import get_best_ip, has_tailscale
from server.stream import CaptureState
from server.window_manager import list_windows
from gui.service_status import get_service_status
from updater import check_for_update


_PANEL_BG = "#1a1a2e"
_TOOLBAR_BG = "#16213e"
_BTN_BG = "#0f3460"
_BTN_HOVER = "#e94560"
_TEXT = "#eaeaea"
_ACCENT = "#e94560"
_GREEN = "#22c55e"
_RED = "#ef4444"
_GRAY = "#94a3b8"

_TOOLBAR_W = 64
_LEFT_PANEL_W = 220


def _btn(text: str, tooltip: str = "", size: int = 13) -> QPushButton:
    b = QPushButton(text)
    b.setToolTip(tooltip)
    f = b.font()
    f.setPointSize(size)
    b.setFont(f)
    b.setStyleSheet(f"""
        QPushButton {{
            background: {_BTN_BG}; color: {_TEXT};
            border: none; border-radius: 6px; padding: 6px;
        }}
        QPushButton:hover {{ background: {_BTN_HOVER}; }}
        QPushButton:disabled {{ background: #2a2a3e; color: #666; }}
    """)
    return b


class LauncherWindow(QMainWindow):
    server_start_requested = pyqtSignal()
    server_stop_requested = pyqtSignal()
    quality_changed = pyqtSignal(int)
    window_selected = pyqtSignal(int, str)

    def __init__(self, state: CaptureState, parent=None):
        super().__init__(parent)
        self._state = state
        self._server_running = False
        self._windows = []          # list of (hwnd, title)
        self._current_idx = 0
        self._left_visible = False

        self.setWindowTitle(f"WindowControl v{VERSION}")
        self.setMinimumSize(480, 400)
        self.resize(900, 600)
        self.setStyleSheet(f"background: {_PANEL_BG}; color: {_TEXT};")

        self._setup_ui()
        self._refresh_ip()
        self._refresh_windows()
        self._refresh_service_status()
        check_for_update(self._on_update_available)

        self._svc_timer = QTimer()
        self._svc_timer.timeout.connect(self._refresh_service_status)
        self._svc_timer.start(5000)

    # ------------------------------------------------------------------ layout

    def _setup_ui(self):
        root = QWidget()
        self.setCentralWidget(root)
        root_layout = QHBoxLayout(root)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        # LEFT PANEL (window list, hidden by default)
        self._left_panel = self._build_left_panel()
        self._left_panel.setFixedWidth(_LEFT_PANEL_W)
        self._left_panel.hide()
        root_layout.addWidget(self._left_panel)

        # CENTER (stream view + info)
        center = self._build_center()
        root_layout.addWidget(center, 1)

        # RIGHT TOOLBAR
        toolbar = self._build_toolbar()
        toolbar.setFixedWidth(_TOOLBAR_W)
        root_layout.addWidget(toolbar)

    def _build_left_panel(self) -> QWidget:
        panel = QWidget()
        panel.setStyleSheet(f"background: {_TOOLBAR_BG};")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        title = QLabel("Windows")
        title.setStyleSheet(f"color: {_TEXT}; font-weight: bold; font-size: 13px;")
        layout.addWidget(title)

        refresh_btn = _btn("↺ Refresh", "Refresh window list", 11)
        refresh_btn.clicked.connect(self._refresh_windows)
        layout.addWidget(refresh_btn)

        self._win_list_layout = QVBoxLayout()
        self._win_list_layout.setSpacing(4)
        scroll_widget = QWidget()
        scroll_widget.setLayout(self._win_list_layout)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(scroll_widget)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setStyleSheet("border: none;")
        layout.addWidget(scroll, 1)

        return panel

    def _build_center(self) -> QWidget:
        center = QWidget()
        center.setStyleSheet(f"background: #000;")
        layout = QVBoxLayout(center)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Stream placeholder
        self._stream_label = QLabel("Select a window →\nthen Start Server")
        self._stream_label.setAlignment(Qt.AlignCenter)
        self._stream_label.setStyleSheet(f"color: {_GRAY}; font-size: 16px;")
        self._stream_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        layout.addWidget(self._stream_label, 1)

        # Bottom bar: current window name + IP
        bar = QWidget()
        bar.setStyleSheet(f"background: {_TOOLBAR_BG};")
        bar_layout = QHBoxLayout(bar)
        bar_layout.setContentsMargins(12, 6, 12, 6)
        self._window_name_label = QLabel("No window selected")
        self._window_name_label.setStyleSheet(f"color: {_TEXT}; font-size: 12px;")
        bar_layout.addWidget(self._window_name_label, 1)
        self._ip_label = QLabel("IP: …")
        self._ip_label.setStyleSheet(f"color: {_GRAY}; font-size: 11px;")
        self._ip_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        bar_layout.addWidget(self._ip_label)
        layout.addWidget(bar)

        return center

    def _build_toolbar(self) -> QWidget:
        toolbar = QWidget()
        toolbar.setStyleSheet(f"background: {_TOOLBAR_BG};")
        layout = QVBoxLayout(toolbar)
        layout.setContentsMargins(6, 8, 6, 8)
        layout.setSpacing(8)
        layout.setAlignment(Qt.AlignTop)

        # Toggle left panel
        self._toggle_btn = _btn("◀", "Show/hide window list", 14)
        self._toggle_btn.setFixedSize(52, 44)
        self._toggle_btn.clicked.connect(self._toggle_left_panel)
        layout.addWidget(self._toggle_btn)

        self._divider(layout)

        # Prev / Next window (scroll equivalent)
        prev_btn = _btn("▲", "Previous window\n(scroll up)", 14)
        prev_btn.setFixedSize(52, 44)
        prev_btn.clicked.connect(self._prev_window)
        layout.addWidget(prev_btn)

        next_btn = _btn("▼", "Next window\n(scroll down)", 14)
        next_btn.setFixedSize(52, 44)
        next_btn.clicked.connect(self._next_window)
        layout.addWidget(next_btn)

        self._divider(layout)

        # Start / Stop server
        self._start_stop_btn = _btn("▶", "Start server", 14)
        self._start_stop_btn.setFixedSize(52, 44)
        self._start_stop_btn.clicked.connect(self._on_start_stop)
        layout.addWidget(self._start_stop_btn)

        self._divider(layout)

        # Quality cycle button
        self._quality_btn = _btn("HD", "Cycle quality", 10)
        self._quality_btn.setFixedSize(52, 36)
        self._quality_btn.clicked.connect(self._cycle_quality)
        self._quality_keys = list(QUALITY_MAP.keys())
        self._quality_idx = self._quality_keys.index(DEFAULT_QUALITY)
        layout.addWidget(self._quality_btn)

        layout.addStretch(1)

        # Service status dot
        self._svc_dot = QLabel("●")
        self._svc_dot.setAlignment(Qt.AlignCenter)
        self._svc_dot.setFixedSize(52, 24)
        self._svc_dot.setToolTip("Lock screen service status")
        self._svc_dot.setStyleSheet(f"color: {_GRAY}; font-size: 16px;")
        layout.addWidget(self._svc_dot)

        # Install / Uninstall service
        install_btn = _btn("↓Svc", "Install lock screen service", 9)
        install_btn.setFixedSize(52, 36)
        install_btn.clicked.connect(self._on_install_service)
        layout.addWidget(install_btn)

        # Set unlock password
        pw_btn = _btn("🔑", "Set auto-unlock password", 13)
        pw_btn.setFixedSize(52, 36)
        pw_btn.clicked.connect(self._on_set_unlock_password)
        layout.addWidget(pw_btn)

        # QR code button
        qr_btn = _btn("QR", "Show QR code", 10)
        qr_btn.setFixedSize(52, 36)
        qr_btn.clicked.connect(self._show_qr_popup)
        layout.addWidget(qr_btn)

        # Status label (bottom)
        self._status_label = QLabel("")
        self._status_label.setAlignment(Qt.AlignCenter)
        self._status_label.setWordWrap(True)
        self._status_label.setStyleSheet(f"color: {_GRAY}; font-size: 9px;")
        self._status_label.setFixedWidth(52)
        layout.addWidget(self._status_label)

        return toolbar

    @staticmethod
    def _divider(layout):
        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        line.setStyleSheet("color: #2a2a4e;")
        layout.addWidget(line)

    # ------------------------------------------------------------------ scroll to switch window

    def wheelEvent(self, event: QWheelEvent):
        delta = event.angleDelta().y()
        if delta > 0:
            self._prev_window()
        elif delta < 0:
            self._next_window()

    def _prev_window(self):
        if not self._windows:
            return
        self._current_idx = (self._current_idx - 1) % len(self._windows)
        self._select_window_by_idx(self._current_idx)

    def _next_window(self):
        if not self._windows:
            return
        self._current_idx = (self._current_idx + 1) % len(self._windows)
        self._select_window_by_idx(self._current_idx)

    def _select_window_by_idx(self, idx: int):
        hwnd, title = self._windows[idx]
        self._window_name_label.setText(title)
        self._status_label.setText(title[:12])
        self.window_selected.emit(hwnd, title)
        self._highlight_window_btn(idx)

    # ------------------------------------------------------------------ left panel

    def _toggle_left_panel(self):
        self._left_visible = not self._left_visible
        self._left_panel.setVisible(self._left_visible)
        self._toggle_btn.setText("▶" if self._left_visible else "◀")

    def _refresh_windows(self):
        self._windows = [(w.hwnd, w.title) for w in list_windows()]
        # Rebuild button list
        for i in reversed(range(self._win_list_layout.count())):
            w = self._win_list_layout.itemAt(i).widget()
            if w:
                w.deleteLater()
        for idx, (hwnd, title) in enumerate(self._windows):
            btn = QPushButton(title[:28])
            btn.setToolTip(title)
            btn.setCheckable(True)
            btn.setStyleSheet(f"""
                QPushButton {{
                    background: {_BTN_BG}; color: {_TEXT};
                    border: none; border-radius: 4px;
                    padding: 6px 8px; text-align: left; font-size: 11px;
                }}
                QPushButton:hover {{ background: {_BTN_HOVER}; }}
                QPushButton:checked {{ background: {_ACCENT}; }}
            """)
            btn.clicked.connect(lambda _, i=idx: self._on_window_btn(i))
            self._win_list_layout.addWidget(btn)
        self._win_list_layout.addStretch()

    def _highlight_window_btn(self, selected_idx: int):
        for i in range(self._win_list_layout.count()):
            w = self._win_list_layout.itemAt(i).widget()
            if isinstance(w, QPushButton):
                w.setChecked(i == selected_idx)

    def _on_window_btn(self, idx: int):
        self._current_idx = idx
        self._select_window_by_idx(idx)

    # ------------------------------------------------------------------ server

    def _on_start_stop(self):
        if not self._server_running:
            self._server_running = True
            self._start_stop_btn.setText("⏹")
            self._start_stop_btn.setToolTip("Stop server")
            self.server_start_requested.emit()
        else:
            self._server_running = False
            self._start_stop_btn.setText("▶")
            self._start_stop_btn.setToolTip("Start server")
            self.server_stop_requested.emit()

    def set_server_running(self, running: bool):
        self._server_running = running
        self._start_stop_btn.setText("⏹" if running else "▶")

    # ------------------------------------------------------------------ quality

    def _cycle_quality(self):
        self._quality_idx = (self._quality_idx + 1) % len(self._quality_keys)
        key = self._quality_keys[self._quality_idx]
        self._quality_btn.setText(key[:2].upper())
        self._quality_btn.setToolTip(f"Quality: {key}")
        self.quality_changed.emit(QUALITY_MAP[key])

    # ------------------------------------------------------------------ IP / QR

    def _refresh_ip(self):
        ip = get_best_ip()
        ts = has_tailscale()
        label = f"{'TS' if ts else 'LAN'}: {ip}:{PORT}"
        self._ip_label.setText(label)
        self._ip_label.setToolTip(f"http://{ip}:{PORT}")

    def _show_qr_popup(self):
        ip = get_best_ip()
        url = f"http://{ip}:{PORT}"
        from PyQt5.QtWidgets import QDialog, QVBoxLayout, QLabel as QL
        dlg = QDialog(self)
        dlg.setWindowTitle("Scan to connect")
        dlg.setStyleSheet(f"background: {_PANEL_BG};")
        lay = QVBoxLayout(dlg)
        qr = qrcode.make(url)
        buf = io.BytesIO()
        qr.save(buf, format="PNG")
        buf.seek(0)
        img = QImage.fromData(buf.read())
        pix = QPixmap.fromImage(img).scaled(240, 240, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        lbl = QL()
        lbl.setPixmap(pix)
        lbl.setAlignment(Qt.AlignCenter)
        lay.addWidget(lbl)
        url_lbl = QL(url)
        url_lbl.setAlignment(Qt.AlignCenter)
        url_lbl.setStyleSheet(f"color: {_TEXT}; font-size: 12px;")
        url_lbl.setTextInteractionFlags(Qt.TextSelectableByMouse)
        lay.addWidget(url_lbl)
        dlg.exec_()

    # ------------------------------------------------------------------ service

    def _refresh_service_status(self):
        status = get_service_status()
        if status == "running":
            self._svc_dot.setStyleSheet(f"color: {_GREEN}; font-size: 16px;")
            self._svc_dot.setToolTip("Lock screen service: Running")
        elif status == "stopped":
            self._svc_dot.setStyleSheet(f"color: {_RED}; font-size: 16px;")
            self._svc_dot.setToolTip("Lock screen service: Stopped")
        else:
            self._svc_dot.setStyleSheet(f"color: {_GRAY}; font-size: 16px;")
            self._svc_dot.setToolTip("Lock screen service: Not installed")

    def _on_install_service(self):
        self._run_elevated(sys.executable, "--install")
        QTimer.singleShot(4000, self._refresh_service_status)

    def _run_elevated(self, exe: str, arg: str):
        if sys.platform == "win32":
            import ctypes
            ctypes.windll.shell32.ShellExecuteW(None, "runas", exe, arg, None, 1)
        else:
            subprocess.Popen([exe, arg])

    # ------------------------------------------------------------------ auto-unlock

    def _on_set_unlock_password(self):
        from PyQt5.QtWidgets import QInputDialog, QLineEdit
        pw, ok = QInputDialog.getText(
            self, "Auto-Unlock Password",
            "Enter Windows password for auto-unlock on lock screen:",
            QLineEdit.Password
        )
        if ok and pw:
            from service.auto_unlock import store_password
            store_password(pw)
            self._status_label.setText("PW saved")

    # ------------------------------------------------------------------ update

    def _on_update_available(self, latest: str):
        url = f"https://github.com/{GITHUB_REPO}/releases/latest"
        from PyQt5.QtWidgets import QMessageBox
        msg = QMessageBox(self)
        msg.setWindowTitle("Update available")
        msg.setText(f"v{latest} is available. Download from GitHub?")
        msg.setStandardButtons(QMessageBox.Ok | QMessageBox.Cancel)
        if msg.exec_() == QMessageBox.Ok:
            import webbrowser
            webbrowser.open(url)
