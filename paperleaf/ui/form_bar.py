"""The slim "this document has form fields" bar, and the Prepare Form toolbar."""
from __future__ import annotations

from PySide6.QtCore import QSize, Signal
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QPushButton, QSizePolicy, QToolBar, QToolButton, QVBoxLayout, QWidget,
)

from paperleaf.core import forms as F
from paperleaf.ui.icons import icon

# (tool, label, icon) in toolbar order
FIELD_TOOL_DEFS = [
    ("field_text", "Add Text Field", "field_text"),
    ("field_check", "Add Check Box", "field_check"),
    ("field_radio", "Add Radio Button", "field_radio"),
    ("field_combo", "Add Dropdown", "field_combo"),
    ("field_list", "Add List Box", "field_list"),
    ("field_sig", "Add Signature Field", "field_sig"),
]
FIELD_TOOL_LABELS = {t: label for t, label, _i in FIELD_TOOL_DEFS}

XFA_NOTE = ("This is an XFA form, which Paperleaf only partly supports. Paperleaf fills its standard fields; "
            "when you change a value it removes the XFA part, so other apps show your entries.")
XFA_ONLY_NOTE = ("This form uses XFA, which Paperleaf can't display or fill. Open it in Adobe Acrobat or "
                 "Reader to fill it in.")
JS_NOTE = ("Some fields are calculated by JavaScript. Paperleaf never runs PDF JavaScript, so those "
           "fields won't update by themselves.")
NO_FILL_NOTE = "This document does not allow filling in form fields."


class FormBar(QFrame):
    """Shown above the page when a document has form fields (like Acrobat's)."""

    highlightToggled = Signal(bool)
    clearRequested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("FormBar")
        self._dismissed = False
        outer = QVBoxLayout(self)
        outer.setContentsMargins(10, 4, 6, 4)
        outer.setSpacing(2)
        row = QHBoxLayout()
        row.setSpacing(8)
        ic = QLabel()
        ic.setPixmap(icon("form").pixmap(QSize(18, 18)))
        row.addWidget(ic)
        self.text = QLabel()
        row.addWidget(self.text)
        row.addStretch(1)
        self.highlight_btn = QPushButton("Highlight fields")
        self.highlight_btn.setCheckable(True)
        self.highlight_btn.setToolTip("Tint the form fields so they are easy to find")
        self.highlight_btn.toggled.connect(self.highlightToggled.emit)
        row.addWidget(self.highlight_btn)
        self.clear_btn = QPushButton("Clear form")
        self.clear_btn.setToolTip("Set every field back to its default value")
        self.clear_btn.clicked.connect(self.clearRequested.emit)
        row.addWidget(self.clear_btn)
        close = QToolButton()
        close.setIcon(icon("close"))
        close.setAutoRaise(True)
        close.setIconSize(QSize(14, 14))
        close.setToolTip("Hide this bar")
        close.clicked.connect(self.dismiss)
        row.addWidget(close)
        outer.addLayout(row)
        self.note = QLabel()
        self.note.setWordWrap(True)
        self.note.setEnabled(False)
        outer.addWidget(self.note)
        self.hide()

    def dismiss(self):
        self._dismissed = True
        self.hide()

    def set_highlight(self, on: bool):
        self.highlight_btn.blockSignals(True)
        self.highlight_btn.setChecked(on)
        self.highlight_btn.blockSignals(False)

    def refresh(self, doc, prepare: bool = False):
        info = F.form_info(doc) if doc.is_pdf else F.FormInfo()
        if not info.fillable or self._dismissed or prepare:
            self.hide()
            return
        fill = F.can_fill(doc)
        notes = []
        if info.xfa_only:
            self.text.setText("This document contains an XFA form.")
            notes.append(XFA_ONLY_NOTE)
        else:
            self.text.setText("This document contains fillable form fields.")
            if info.xfa:
                notes.append(XFA_NOTE)
            if not fill:
                notes.append(NO_FILL_NOTE)
            if info.calculated:
                notes.append(JS_NOTE)
        self.note.setText(" ".join(notes))
        self.note.setVisible(bool(notes))
        self.highlight_btn.setVisible(not info.xfa_only)
        self.clear_btn.setVisible(not info.xfa_only)
        self.clear_btn.setEnabled(fill)
        self.show()


class PrepareFormToolbar(QToolBar):
    """Second toolbar row while Prepare Form is on. ``tool_actions`` are the
    window's checkable tool actions (shared with the menus)."""

    def __init__(self, tool_actions: dict, actions: dict, parent=None):
        super().__init__("Prepare Form", parent)
        self.setObjectName("PrepareFormToolbar")
        self.setMovable(False)
        self.setIconSize(QSize(20, 20))
        title = QLabel("  Prepare Form  ")
        title.setStyleSheet("font-weight: 600;")
        self.addWidget(title)
        self.addAction(tool_actions["select"])
        self.addSeparator()
        for tool, _label, _ic in FIELD_TOOL_DEFS:
            self.addAction(tool_actions[tool])
        self.addSeparator()
        self.addAction(actions["field_props"])
        self.addAction(actions["delete_field"])
        self.addSeparator()
        self.hint = QLabel()
        self.hint.setEnabled(False)
        self.addWidget(self.hint)
        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.addWidget(spacer)
        close = QPushButton("Close")
        close.setToolTip("Close Prepare Form")
        close.clicked.connect(lambda: actions["prepare_form"].trigger())
        self.addWidget(close)
        self.set_selection([])

    def set_selection(self, fields: list):
        if not fields:
            self.hint.setText("  Choose a field type, then click or drag on the page.")
        elif len(fields) == 1:
            f = fields[0]
            self.hint.setText(f"  Selected: {f.label} “{f.name}”")
        else:
            self.hint.setText(f"  {len(fields)} fields selected")
