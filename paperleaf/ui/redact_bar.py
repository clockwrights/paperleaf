"""The Redact toolbar (second row while redacting)."""
from __future__ import annotations

from PySide6.QtCore import QSize
from PySide6.QtWidgets import QLabel, QPushButton, QSizePolicy, QToolBar, QWidget


class RedactToolbar(QToolBar):
    def __init__(self, redact_action, actions: dict, parent=None):
        super().__init__("Redact", parent)
        self.setObjectName("RedactToolbar")
        self.setMovable(False)
        self.setIconSize(QSize(20, 20))
        title = QLabel("  Redact  ")
        title.setStyleSheet("font-weight: 600;")
        self.addWidget(title)
        self.addAction(actions["tool_select"])
        self.addAction(redact_action)
        self.addAction(actions["find_redact"])
        self.addSeparator()
        self.apply_btn = QPushButton("Apply Redactions")
        self.apply_btn.setToolTip("Remove the marked content")
        self.apply_btn.clicked.connect(actions["apply_redactions"].trigger)
        self.addWidget(self.apply_btn)
        self.addAction(actions["redact_props"])
        self.addSeparator()
        self.addAction(actions["sanitize"])
        self.count = QLabel()
        self.count.setEnabled(False)
        self.addWidget(self.count)
        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.addWidget(spacer)
        close = QPushButton("Close")
        close.setToolTip("Close the Redact tools")
        close.clicked.connect(actions["redact_mode"].trigger)
        self.addWidget(close)
        self.set_count(0)

    def set_count(self, n: int):
        self.apply_btn.setEnabled(n > 0)
        self.count.setText(f"  {n} area{'s' if n != 1 else ''} marked for redaction" if n else
                           "  Select text or drag a box to mark it for redaction.")
