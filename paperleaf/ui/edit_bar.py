"""The Edit PDF toolbar (second row while editing text and images)."""
from __future__ import annotations

from PySide6.QtCore import QSize
from PySide6.QtWidgets import QLabel, QPushButton, QSizePolicy, QToolBar, QWidget

HINT = "  Click text to change it. Click an image to move, resize, replace or delete it."


class EditToolbar(QToolBar):
    def __init__(self, actions: dict, parent=None):
        super().__init__("Edit PDF", parent)
        self.setObjectName("EditToolbar")
        self.setMovable(False)
        self.setIconSize(QSize(20, 20))
        title = QLabel("  Edit PDF  ")
        title.setStyleSheet("font-weight: 600;")
        self.addWidget(title)
        self.addAction(actions["edit_add_text"])
        self.addAction(actions["edit_add_image"])
        self.addSeparator()
        self.addAction(actions["edit_replace_image"])
        self.addAction(actions["edit_delete_image"])
        self.addSeparator()
        self.addAction(actions["ocr"])
        self.hint = QLabel(HINT)
        self.hint.setEnabled(False)
        self.addWidget(self.hint)
        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.addWidget(spacer)
        close = QPushButton("Close")
        close.setToolTip("Close the Edit PDF tools")
        close.clicked.connect(actions["edit_pdf"].trigger)
        self.addWidget(close)

    def set_mode(self, mode: str):
        self.hint.setText({"add_text": "  Click where the new text goes. Ctrl+Enter or click elsewhere to finish.",
                           "add_image": "  Click where the image goes."}.get(mode, HINT))
