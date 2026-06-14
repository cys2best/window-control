# src/gui/launcher.py
import sys
import subprocess
import io
from PyQt5.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QStackedWidget,
    QPushButton, QLabel, QScrollArea, QSizePolicy, QFrame, QApplication
)
from PyQt5.QtCore import Qt, pyqtSignal, QTimer, QSize
from PyQt5.QtGui import QPixmap, QImage, QWheelEvent
import qrcode

from config import PORT, QUALITY_MAP, DEFAULT_QUALITY, VERSION, GITHUB_REPO
from server.tailscale import get_best_ip, has_tailscale
from server.stream import CaptureState
from server.window_manager import list_windows
from gui.service_status import get_service_status
from updater import check_for_update

_BG = "#0f0f1a"
_CARD_BG = "#1a1a2e"
_TOOLBAR_BG = "#16213e"
_BTN = "#0f3460"
_ACCENT = "#e94560"
_TEXT = "#eaeaea"
_MUTED = "#94a3b8"
_GREEN = "#22c55e"
_RED = "#ef4444"


def _style_btn(b: QPushButton, bg=_BTN, fg=_TEXT, hover=_ACCENT, px=8, py=6, radius=6, bold=False):
    weight = "bold" if bold else "normal"
    b.setStyleSheet(f"""
        QPushButton {{
            background:{bg}; color:{fg}; border:none;
            border-radius:{radius}px; padding:{py}px {px}px; font-weight:{weight};
        }}
        QPushButton:hover {{ background:{hover}; }}
        QPushButton:disabled {{ background:#2a2a3e; color:#555; }}
    """)


class LauncherWindow(QMainWindow):
    server_start_requested = pyqtSignal()
    server_stop_requested = pyqtSignal()
    quality_changed = pyqtSignal(int)
    window_selected = pyqtSignal(int, str)

    def __init__(self, state: CaptureState, parent=None):
        super().__init__(parent)
        self._state = state
        self._server_running = False
        self._windows = []       # list of (hwnd, title)
        self._current_idx = 0
        self._quality_keys = list(QUALITY_MAP.keys())
        self._quality_idx = self._quality_keys.index(DEFAULT_QUALITY)

        self.setWindowTitle(f"WindowControl v{VERSION}")
        self.setStyleSheet(f"background:{_BG}; color:{_TEXT};")
        self.resize(480, 700)

        self._stack = QStackedWidget()
        self.setCentralWidget(self._stack)

        self._screen_picker = self._build_picker_screen()
        self._screen_stream = self._build_stream_screen()
        self._stack.addWidget(self._screen_picker)  # index 0
        self._stack.addWidget(self._screen_stream)  # index 1

        self._refresh_windows()
        self._refresh_ip()
        check_for_update(self._on_update_available)

        self._svc_timer = QTimer()
        self._svc_timer.timeout.connect(self._refresh_service_status)
        self._svc_timer.start(5000)
        self._refresh_service_status()

    # ══════════════════════════════════════════════════════════ SCREEN 0: PICKER

    def _build_picker_screen(self) -> QWidget:
        screen = QWidget()
        screen.setStyleSheet(f"background:{_BG};")
        layout = QVBoxLayout(screen)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Header
        header = QWidget()
        header.setStyleSheet(f"background:{_TOOLBAR_BG};")
        h_layout = QHBoxLayout(header)
        h_layout.setContentsMargins(16, 12, 16, 12)
        title = QLabel(f"WindowControl  v{VERSION}")
        title.setStyleSheet(f"color:{_TEXT}; font-size:16px; font-weight:bold;")
        h_layout.addWidget(title, 1)

        self._svc_dot_picker = QLabel("●")
        self._svc_dot_picker.setStyleSheet(f"color:{_MUTED}; font-size:18px;")
        self._svc_dot_picker.setToolTip("Lock screen service")
        h_layout.addWidget(self._svc_dot_picker)

        layout.addWidget(header)

        # IP bar
        self._ip_bar = QLabel("IP: …")
        self._ip_bar.setStyleSheet(
            f"background:{_CARD_BG}; color:{_MUTED}; font-size:11px;"
            "padding:4px 16px;"
        )
        self._ip_bar.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(self._ip_bar)

        # Scrollable window card list
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setStyleSheet("border:none; background:transparent;")

        self._cards_widget = QWidget()
        self._cards_widget.setStyleSheet(f"background:{_BG};")
        self._cards_layout = QVBoxLayout(self._cards_widget)
        self._cards_layout.setContentsMargins(12, 12, 12, 12)
        self._cards_layout.setSpacing(8)
        self._cards_layout.addStretch()
        scroll.setWidget(self._cards_widget)
        layout.addWidget(scroll, 1)

        # Bottom toolbar
        bottom = QWidget()
        bottom.setStyleSheet(f"background:{_TOOLBAR_BG};")
        b_layout = QHBoxLayout(bottom)
        b_layout.setContentsMargins(12, 8, 12, 8)
        b_layout.setSpacing(8)

        refresh_btn = QPushButton("↺  Refresh")
        _style_btn(refresh_btn)
        refresh_btn.clicked.connect(self._refresh_windows)
        b_layout.addWidget(refresh_btn)

        install_btn = QPushButton("Install Service")
        _style_btn(install_btn, bg="#1a3a1a", hover="#22c55e")
        install_btn.clicked.connect(self._on_install_service)
        b_layout.addWidget(install_btn)

        pw_btn = QPushButton("🔑 Auto-Unlock PW")
        _style_btn(pw_btn)
        pw_btn.clicked.connect(self._on_set_unlock_password)
        b_layout.addWidget(pw_btn)

        layout.addWidget(bottom)
        return screen

    def _rebuild_cards(self):
        # Remove all except trailing stretch
        while self._cards_layout.count() > 1:
            item = self._cards_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        for idx, (hwnd, title) in enumerate(self._windows):
            card = QPushButton(title)
            card.setStyleSheet(f"""
                QPushButton {{
                    background:{_CARD_BG}; color:{_TEXT};
                    border:none; border-radius:8px;
                    padding:14px 16px; text-align:left;
                    font-size:14px;
                }}
                QPushButton:hover {{
                    background:{_BTN}; border-left:3px solid {_ACCENT};
                }}
            """)
            card.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            card.setFixedHeight(56)
            card.clicked.connect(lambda _, i=idx: self._open_stream(i))
            self._cards_layout.insertWidget(self._cards_layout.count() - 1, card)

    # ══════════════════════════════════════════════════════════ SCREEN 1: STREAM

    def _build_stream_screen(self) -> QWidget:
        screen = QWidget()
        screen.setStyleSheet(f"background:#000;")
        layout = QVBoxLayout(screen)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Top bar
        topbar = QWidget()
        topbar.setStyleSheet(f"background:{_TOOLBAR_BG};")
        topbar.setFixedHeight(48)
        top_layout = QHBoxLayout(topbar)
        top_layout.setContentsMargins(8, 4, 8, 4)
        top_layout.setSpacing(6)

        back_btn = QPushButton("◀  Windows")
        _style_btn(back_btn, bold=True)
        back_btn.clicked.connect(self._go_picker)
        top_layout.addWidget(back_btn)

        self._stream_title = QLabel("—")
        self._stream_title.setStyleSheet(f"color:{_TEXT}; font-size:13px; font-weight:bold;")
        self._stream_title.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self._stream_title.setAlignment(Qt.AlignCenter)
        top_layout.addWidget(self._stream_title, 1)

        # Prev/Next window
        prev_btn = QPushButton("▲")
        _style_btn(prev_btn)
        prev_btn.setFixedWidth(36)
        prev_btn.setToolTip("Previous window")
        prev_btn.clicked.connect(self._prev_window)
        top_layout.addWidget(prev_btn)

        next_btn = QPushButton("▼")
        _style_btn(next_btn)
        next_btn.setFixedWidth(36)
        next_btn.setToolTip("Next window")
        next_btn.clicked.connect(self._next_window)
        top_layout.addWidget(next_btn)

        # Start/Stop
        self._start_stop_btn = QPushButton("▶")
        _style_btn(self._start_stop_btn, bg=_GREEN, hover="#16a34a")
        self._start_stop_btn.setFixedWidth(40)
        self._start_stop_btn.setToolTip("Start server")
        self._start_stop_btn.clicked.connect(self._on_start_stop)
        top_layout.addWidget(self._start_stop_btn)

        # Quality
        self._quality_btn = QPushButton(DEFAULT_QUALITY[:2].upper())
        _style_btn(self._quality_btn)
        self._quality_btn.setFixedWidth(40)
        self._quality_btn.setToolTip("Cycle stream quality")
        self._quality_btn.clicked.connect(self._cycle_quality)
        top_layout.addWidget(self._quality_btn)

        # QR
        qr_btn = QPushButton("QR")
        _style_btn(qr_btn)
        qr_btn.setFixedWidth(36)
        qr_btn.clicked.connect(self._show_qr_popup)
        top_layout.addWidget(qr_btn)

        # Service dot
        self._svc_dot_stream = QLabel("●")
        self._svc_dot_stream.setStyleSheet(f"color:{_MUTED}; font-size:18px; padding:0 4px;")
        self._svc_dot_stream.setToolTip("Lock screen service")
        top_layout.addWidget(self._svc_dot_stream)

        layout.addWidget(topbar)

        # Stream area (fullscreen horizontal)
        self._stream_area = QLabel("Select a window and start the server\nto begin streaming.")
        self._stream_area.setAlignment(Qt.AlignCenter)
        self._stream_area.setStyleSheet(f"color:{_MUTED}; font-size:15px; background:#000;")
        self._stream_area.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        layout.addWidget(self._stream_area, 1)

        # Bottom status bar
        self._status_bar = QLabel("")
        self._status_bar.setStyleSheet(
            f"background:{_TOOLBAR_BG}; color:{_MUTED}; font-size:11px; padding:3px 12px;"
        )
        self._status_bar.setAlignment(Qt.AlignLeft)
        layout.addWidget(self._status_bar)

        return screen

    # ══════════════════════════════════════════════════════════ NAVIGATION

    def _open_stream(self, idx: int):
        self._current_idx = idx
        hwnd, title = self._windows[idx]
        self._stream_title.setText(title)
        self._status_bar.setText(f"Window: {title}")
        self.window_selected.emit(hwnd, title)
        self._stack.setCurrentIndex(1)

    def _go_picker(self):
        self._stack.setCurrentIndex(0)

    # ══════════════════════════════════════════════════════════ WINDOW SWITCHING

    def _refresh_windows(self):
        self._windows = [(w.hwnd, w.title) for w in list_windows()]
        self._rebuild_cards()

    def _prev_window(self):
        if not self._windows:
            return
        self._current_idx = (self._current_idx - 1) % len(self._windows)
        self._open_stream(self._current_idx)

    def _next_window(self):
        if not self._windows:
            return
        self._current_idx = (self._current_idx + 1) % len(self._windows)
        self._open_stream(self._current_idx)

    def wheelEvent(self, event: QWheelEvent):
        if self._stack.currentIndex() == 1:
            if event.angleDelta().y() > 0:
                self._prev_window()
            else:
                self._next_window()

    # ══════════════════════════════════════════════════════════ SERVER

    def _on_start_stop(self):
        if not self._server_running:
            self._server_running = True
            self._start_stop_btn.setText("⏹")
            _style_btn(self._start_stop_btn, bg=_RED, hover="#b91c1c")
            self._start_stop_btn.setToolTip("Stop server")
            self._status_bar.setText("Server running…")
            self.server_start_requested.emit()
        else:
            self._server_running = False
            self._start_stop_btn.setText("▶")
            _style_btn(self._start_stop_btn, bg=_GREEN, hover="#16a34a")
            self._start_stop_btn.setToolTip("Start server")
            self._status_bar.setText("Server stopped")
            self.server_stop_requested.emit()

    def set_server_running(self, running: bool):
        self._server_running = running
        if running:
            self._start_stop_btn.setText("⏹")
            _style_btn(self._start_stop_btn, bg=_RED, hover="#b91c1c")
        else:
            self._start_stop_btn.setText("▶")
            _style_btn(self._start_stop_btn, bg=_GREEN, hover="#16a34a")

    # ══════════════════════════════════════════════════════════ QUALITY

    def _cycle_quality(self):
        self._quality_idx = (self._quality_idx + 1) % len(self._quality_keys)
        key = self._quality_keys[self._quality_idx]
        self._quality_btn.setText(key[:2].upper())
        self._quality_btn.setToolTip(f"Quality: {key}")
        self.quality_changed.emit(QUALITY_MAP[key])

    # ══════════════════════════════════════════════════════════ IP / QR

    def _refresh_ip(self):
        ip = get_best_ip()
        ts = has_tailscale()
        label = f"{'Tailscale' if ts else 'LAN'}: {ip}:{PORT}"
        self._ip_bar.setText(label)

    def _show_qr_popup(self):
        ip = get_best_ip()
        url = f"http://{ip}:{PORT}"
        from PyQt5.QtWidgets import QDialog, QVBoxLayout
        dlg = QDialog(self)
        dlg.setWindowTitle("Scan to connect")
        dlg.setStyleSheet(f"background:{_BG}; color:{_TEXT};")
        lay = QVBoxLayout(dlg)
        qr_img = qrcode.make(url)
        buf = io.BytesIO()
        qr_img.save(buf, format="PNG")
        buf.seek(0)
        img = QImage.fromData(buf.read())
        pix = QPixmap.fromImage(img).scaled(240, 240, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        lbl = QLabel()
        lbl.setPixmap(pix)
        lbl.setAlignment(Qt.AlignCenter)
        lay.addWidget(lbl)
        url_lbl = QLabel(url)
        url_lbl.setAlignment(Qt.AlignCenter)
        url_lbl.setStyleSheet(f"color:{_TEXT}; font-size:12px;")
        url_lbl.setTextInteractionFlags(Qt.TextSelectableByMouse)
        lay.addWidget(url_lbl)
        dlg.exec_()

    # ══════════════════════════════════════════════════════════ SERVICE

    def _refresh_service_status(self):
        status = get_service_status()
        color = {
            "running": _GREEN,
            "stopped": _RED,
        }.get(status, _MUTED)
        tooltip = {
            "running": "Lock screen service: Running",
            "stopped": "Lock screen service: Stopped",
        }.get(status, "Lock screen service: Not installed")
        style = f"color:{color}; font-size:18px;"
        self._svc_dot_picker.setStyleSheet(style)
        self._svc_dot_picker.setToolTip(tooltip)
        self._svc_dot_stream.setStyleSheet(f"{style} padding:0 4px;")
        self._svc_dot_stream.setToolTip(tooltip)

    def _on_install_service(self):
        self._run_elevated(sys.executable, "--install")
        QTimer.singleShot(4000, self._refresh_service_status)

    def _run_elevated(self, exe: str, arg: str):
        if sys.platform == "win32":
            import ctypes
            ctypes.windll.shell32.ShellExecuteW(None, "runas", exe, arg, None, 1)
        else:
            subprocess.Popen([exe, arg])

    # ══════════════════════════════════════════════════════════ AUTO-UNLOCK

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

    # ══════════════════════════════════════════════════════════ UPDATE

    def _on_update_available(self, latest: str):
        from PyQt5.QtWidgets import QMessageBox
        import webbrowser
        msg = QMessageBox(self)
        msg.setWindowTitle("Update available")
        msg.setText(f"v{latest} is available. Open download page?")
        msg.setStandardButtons(QMessageBox.Ok | QMessageBox.Cancel)
        if msg.exec_() == QMessageBox.Ok:
            webbrowser.open(f"https://github.com/{GITHUB_REPO}/releases/latest")
