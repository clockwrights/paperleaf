"""A slim bar above the page with a message and one button (signatures, scanned pages)."""
from __future__ import annotations

from PySide6.QtCore import QSize, Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QToolButton

from paperleaf.ui.icons import icon

COLORS = {"valid": "#107c10", "unknown": "#b07000", "invalid": "#c42b1c", "info": ""}


class NoticeBar(QFrame):
    clicked = Signal()

    def __init__(self, icon_name: str, button: str, tip: str = "", parent=None):
        super().__init__(parent)
        self.setObjectName("FormBar")   # same look as the form bar
        self._dismissed = False
        row = QHBoxLayout(self)
        row.setContentsMargins(10, 4, 6, 4)
        row.setSpacing(8)
        self.ic = QLabel()
        self.ic.setPixmap(icon(icon_name).pixmap(QSize(18, 18)))
        row.addWidget(self.ic)
        self.text = QLabel()
        self.text.setWordWrap(True)
        row.addWidget(self.text, 1)
        self.button = QPushButton(button)
        if tip:
            self.button.setToolTip(tip)
        self.button.clicked.connect(self.clicked.emit)
        row.addWidget(self.button)
        close = QToolButton()
        close.setIcon(icon("close"))
        close.setAutoRaise(True)
        close.setIconSize(QSize(14, 14))
        close.setToolTip("Hide this bar")
        close.clicked.connect(self.dismiss)
        row.addWidget(close)
        self.hide()

    def dismiss(self):
        self._dismissed = True
        self.hide()

    def show_message(self, text: str, level: str = "info"):
        if self._dismissed:
            return
        color = COLORS.get(level, "")
        self.text.setStyleSheet(f"color: {color};" if color else "")
        self.text.setText(text)
        self.show()
