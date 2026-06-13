from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QListWidget, QListWidgetItem,
    QPushButton, QLabel, QHBoxLayout
)
from PyQt5.QtCore import Qt, pyqtSignal, QTimer
from PyQt5.QtGui import QPixmap, QImage
from server.window_manager import list_windows
from server.preview import capture_preview


class WindowListWidget(QWidget):
    window_selected = pyqtSignal(int, str)  # emits (hwnd, title) when user selects

    def __init__(self, parent=None):
        super().__init__(parent)
        self._setup_ui()
        self._refresh_timer = QTimer()
        self._refresh_timer.timeout.connect(self.refresh)
        self._refresh_timer.start(3000)  # refresh every 3 seconds
        self.refresh()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        header = QHBoxLayout()
        header.addWidget(QLabel("Windows"))
        refresh_btn = QPushButton("↺")
        refresh_btn.setFixedWidth(30)
        refresh_btn.clicked.connect(self.refresh)
        header.addWidget(refresh_btn)
        layout.addLayout(header)

        self._list = QListWidget()
        self._list.itemDoubleClicked.connect(self._on_item_double_clicked)
        layout.addWidget(self._list)

    def refresh(self):
        """Re-enumerate windows and update list."""
        current_hwnd = self._get_selected_hwnd()
        self._list.clear()
        windows = list_windows()
        for w in windows:
            item = QListWidgetItem(w.title)
            item.setData(Qt.UserRole, w.hwnd)
            self._list.addItem(item)
            # restore selection
            if w.hwnd == current_hwnd:
                self._list.setCurrentItem(item)

    def _get_selected_hwnd(self) -> int | None:
        item = self._list.currentItem()
        if item:
            return item.data(Qt.UserRole)
        return None

    def _on_item_double_clicked(self, item: QListWidgetItem):
        hwnd = item.data(Qt.UserRole)
        title = item.text()
        self.window_selected.emit(hwnd, title)

    def get_selected(self) -> tuple[int, str] | None:
        """Return (hwnd, title) of currently selected item, or None."""
        item = self._list.currentItem()
        if item:
            return item.data(Qt.UserRole), item.text()
        return None
