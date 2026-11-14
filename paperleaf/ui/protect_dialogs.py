"""Dialogs for Protect: password security, redaction (appearance, find & redact)
and removing hidden information; plus Reduce File Size and Export to Images."""
from __future__ import annotations

import os
import re

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QButtonGroup, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QGridLayout, QGroupBox,
    QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMessageBox, QPushButton, QRadioButton,
    QSpinBox, QVBoxLayout,
)

from paperleaf.core import convert as C
from paperleaf.core import protect as X
from paperleaf.core.document import human_size
from paperleaf.ui.page_dialogs import ColorPick, PageRangeBox


def _warn(parent, title, text):
    QMessageBox.warning(parent, title, text)


class _Base(QDialog):
    def __init__(self, title, parent=None, ok="OK"):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.lay = QVBoxLayout(self)
        self.bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        self.ok_btn = self.bb.addButton(ok, QDialogButtonBox.ButtonRole.AcceptRole)
        self.ok_btn.setDefault(True)
        self.bb.accepted.connect(self._ok)
        self.bb.rejected.connect(self.reject)

    def done_layout(self):
        self.lay.addWidget(self.bb)

    def _ok(self):
        if self.validate():
            self.accept()

    def validate(self) -> bool:
        return True


def _password_row(placeholder: str):
    pw = QLineEdit()
    pw.setEchoMode(QLineEdit.EchoMode.Password)
    pw.setPlaceholderText(placeholder)
    again = QLineEdit()
    again.setEchoMode(QLineEdit.EchoMode.Password)
    again.setPlaceholderText("Type it again")
    return pw, again


class SecurityDialog(_Base):
    """Acrobat's "Password Security - Settings"."""

    def __init__(self, doc, parent=None):
        super().__init__("Password Security", parent)
        self.doc = doc
        self.resize(500, 520)
        f = QFormLayout()
        self.method = QComboBox()
        for label, m in X.METHODS:
            self.method.addItem(label, m)
        f.addRow("Encryption:", self.method)
        self.lay.addLayout(f)

        openbox = QGroupBox("Document open")
        og = QFormLayout(openbox)
        self.need_open = QCheckBox("Require a password to open the document")
        self.open_pw, self.open_again = _password_row("Open password")
        og.addRow(self.need_open)
        og.addRow("Password:", self.open_pw)
        og.addRow("Confirm:", self.open_again)
        self.lay.addWidget(openbox)

        perm = QGroupBox("Permissions")
        pg = QFormLayout(perm)
        self.restrict = QCheckBox("Restrict editing and printing of the document")
        self.owner_pw, self.owner_again = _password_row("Permissions password")
        self.printing = QComboBox()
        for label, key in X.PRINTING:
            self.printing.addItem(label, key)
        self.changes = QComboBox()
        for label, key in X.CHANGES:
            self.changes.addItem(label, key)
        self.copy = QCheckBox("Enable copying of text, images and other content")
        self.access = QCheckBox("Enable text access for screen readers")
        pg.addRow(self.restrict)
        pg.addRow("Permissions password:", self.owner_pw)
        pg.addRow("Confirm:", self.owner_again)
        pg.addRow("Printing allowed:", self.printing)
        pg.addRow("Changes allowed:", self.changes)
        pg.addRow("", self.copy)
        pg.addRow("", self.access)
        self.lay.addWidget(perm)
        note = QLabel("The new security takes effect when you save the document. Keep your passwords "
                      "safe: Paperleaf can't open a file whose password is lost.")
        note.setWordWrap(True)
        note.setEnabled(False)
        self.lay.addWidget(note)
        cur = X.describe_permissions(doc.doc.permissions)
        self.printing.setCurrentIndex(self.printing.findData(cur["printing"] if doc.encrypted else X.PRINT_HIGH))
        self.changes.setCurrentIndex(self.changes.findData(cur["changes"] if doc.encrypted else "none"))
        self.copy.setChecked(cur["copy"] if doc.encrypted else False)
        self.access.setChecked(True)
        self.need_open.setChecked(True)
        for w in (self.need_open, self.restrict):
            w.toggled.connect(self._sync)
        self.done_layout()
        self._sync()

    def _sync(self):
        on = self.need_open.isChecked()
        for w in (self.open_pw, self.open_again):
            w.setEnabled(on)
        r = self.restrict.isChecked()
        for w in (self.owner_pw, self.owner_again, self.printing, self.changes, self.copy, self.access):
            w.setEnabled(r)

    def values(self):
        open_pw = self.open_pw.text() if self.need_open.isChecked() else ""
        restrict = self.restrict.isChecked()
        owner = self.owner_pw.text() if restrict else ""
        perms = X.permissions_value(self.printing.currentData(), self.changes.currentData(),
                                    self.copy.isChecked(), self.access.isChecked()) if restrict else None
        return open_pw, owner, perms, self.method.currentData()

    def validate(self):
        if self.need_open.isChecked() and self.open_pw.text() != self.open_again.text():
            _warn(self, "Password Security", "The open passwords don't match.")
            return False
        if self.restrict.isChecked() and self.owner_pw.text() != self.owner_again.text():
            _warn(self, "Password Security", "The permissions passwords don't match.")
            return False
        open_pw, owner, perms, _m = self.values()
        problem = X.password_problem(open_pw, owner, perms is not None)
        if problem:
            _warn(self, "Password Security", problem)
            return False
        return True


class RedactStyleDialog(_Base):
    def __init__(self, style: X.RedactStyle, parent=None):
        super().__init__("Redaction Properties", parent)
        f = QFormLayout()
        self.fill = ColorPick(style.fill)
        self.text = QLineEdit(style.text)
        self.text.setPlaceholderText("None")
        self.text_color = ColorPick(style.text_color)
        self.size = QSpinBox()
        self.size.setRange(4, 72)
        self.size.setValue(int(style.fontsize))
        f.addRow("Box color:", self.fill)
        f.addRow("Overlay text:", self.text)
        f.addRow("Text color:", self.text_color)
        f.addRow("Font size:", self.size)
        self.lay.addLayout(f)
        self.done_layout()

    def style(self) -> X.RedactStyle:
        return X.RedactStyle(self.fill.rgb, self.text.text().strip(), self.text_color.rgb, float(self.size.value()))


class FindRedactDialog(_Base):
    """Search for words or patterns (email addresses, phone numbers...) and mark the results."""

    def __init__(self, doc, parent=None):
        super().__init__("Find Text & Redact", parent, ok="Mark Checked Results")
        self.doc = doc
        self.resize(620, 560)
        box = QGroupBox("Search for")
        g = QGridLayout(box)
        self.r_text = QRadioButton("Words or phrase:")
        self.query = QLineEdit()
        self.query.setPlaceholderText("Separate several with commas")
        self.whole = QCheckBox("Whole words only")
        self.case = QCheckBox("Match case")
        self.r_patterns = QRadioButton("Patterns:")
        self.grp = QButtonGroup(self)
        self.grp.addButton(self.r_text)
        self.grp.addButton(self.r_patterns)
        self.r_text.setChecked(True)
        g.addWidget(self.r_text, 0, 0)
        g.addWidget(self.query, 0, 1, 1, 2)
        g.addWidget(self.whole, 1, 1)
        g.addWidget(self.case, 1, 2)
        g.addWidget(self.r_patterns, 2, 0)
        self.patterns = {}
        for i, name in enumerate(X.PATTERNS):
            cb = QCheckBox(name)
            cb.toggled.connect(lambda on: on and self.r_patterns.setChecked(True))
            g.addWidget(cb, 2 + i // 2, 1 + i % 2)
            self.patterns[name] = cb
        self.lay.addWidget(box)
        row = QHBoxLayout()
        self.find_btn = QPushButton("Find")
        self.find_btn.clicked.connect(self.search)
        self.count = QLabel()
        row.addWidget(self.find_btn)
        row.addWidget(self.count, 1)
        self.all_btn = QPushButton("Check All")
        self.none_btn = QPushButton("Uncheck All")
        self.all_btn.clicked.connect(lambda: self._check_all(True))
        self.none_btn.clicked.connect(lambda: self._check_all(False))
        row.addWidget(self.all_btn)
        row.addWidget(self.none_btn)
        self.lay.addLayout(row)
        self.results = QListWidget()
        self.lay.addWidget(self.results, 1)
        self.query.returnPressed.connect(self.search)
        self.matches: list = []
        self.done_layout()

    def regexes(self):
        if self.r_text.isChecked():
            out = []
            for part in self.query.text().split(","):
                rx = X.search_regex(part, self.whole.isChecked(), self.case.isChecked())
                if rx is not None:
                    out.append(rx)
            return out
        return [re.compile(X.PATTERNS[n]) for n, cb in self.patterns.items() if cb.isChecked()]

    def search(self):
        rxs = self.regexes()
        self.results.clear()
        if not rxs:
            self.count.setText("Type what to find, or choose a pattern.")
            return
        self.matches = X.find_matches(self.doc, rxs)
        for m in self.matches:
            pno, s, e, text = m
            ctx = self.doc.page_text(pno).text
            before = ctx[max(0, s - 30):s].replace("\n", " ")
            after = ctx[e:e + 30].replace("\n", " ")
            it = QListWidgetItem(f"Page {self.doc.page_label(pno)}:  …{before}[{text}]{after}…")
            it.setFlags(it.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            it.setCheckState(Qt.CheckState.Checked)
            self.results.addItem(it)
        n = len(self.matches)
        self.count.setText(f"{n} result{'s' if n != 1 else ''}" if n else "No results")

    def _check_all(self, on):
        for i in range(self.results.count()):
            self.results.item(i).setCheckState(Qt.CheckState.Checked if on else Qt.CheckState.Unchecked)

    def checked(self) -> list:
        return [m for i, m in enumerate(self.matches)
                if self.results.item(i).checkState() == Qt.CheckState.Checked]

    def validate(self):
        if not self.matches:
            self.search()
        if not self.checked():
            _warn(self, "Find Text & Redact", "There are no checked results to mark.")
            return False
        return True


class SanitizeDialog(_Base):
    """Acrobat's "Remove Hidden Information"."""

    def __init__(self, doc, parent=None):
        super().__init__("Remove Hidden Information", parent, ok="Remove")
        self.resize(560, 460)
        intro = QLabel("Choose what to remove. Items Paperleaf found in this document show how many there are.")
        intro.setWordWrap(True)
        self.lay.addWidget(intro)
        found = X.inspect(doc).counts
        self.boxes = {}
        for key, label, default in X.SANITIZE_ITEMS:
            n = found.get(key, 0)
            cb = QCheckBox(f"{label}  ({n})" if n else f"{label}  (none found)")
            cb.setChecked(default and n > 0)
            cb.setEnabled(n > 0)
            self.lay.addWidget(cb)
            self.boxes[key] = cb
        note = QLabel("You can undo this until you close the document. Save to make it permanent.")
        note.setEnabled(False)
        note.setWordWrap(True)
        self.lay.addWidget(note)
        self.lay.addStretch(1)
        self.done_layout()

    def items(self) -> list[str]:
        return [k for k, cb in self.boxes.items() if cb.isChecked()]

    def validate(self):
        if not self.items():
            _warn(self, "Remove Hidden Information", "Choose at least one item to remove.")
            return False
        return True


class CompressDialog(_Base):
    def __init__(self, doc, parent=None):
        super().__init__("Reduce File Size", parent, ok="Save Smaller Copy…")
        size = len(doc.data) if not doc.modified else None
        info = QLabel(f"Current file size: {human_size(size)}" if size else "Paperleaf saves a smaller copy of the "
                      "document as it is now.")
        self.lay.addWidget(info)
        box = QGroupBox("Quality")
        bl = QVBoxLayout(box)
        self.presets = {}
        for label, key in C.PRESET_LABELS:
            dpi, q = C.PRESETS[key]
            rb = QRadioButton(f"{label}: images at {dpi} dpi, JPEG quality {q}")
            bl.addWidget(rb)
            self.presets[key] = rb
        self.presets["medium"].setChecked(True)
        self.r_custom = QRadioButton("Custom:")
        row = QHBoxLayout()
        row.addWidget(self.r_custom)
        self.dpi = QSpinBox()
        self.dpi.setRange(36, 600)
        self.dpi.setValue(150)
        self.dpi.setSuffix(" dpi")
        self.quality = QSpinBox()
        self.quality.setRange(10, 100)
        self.quality.setValue(72)
        self.quality.setPrefix("quality ")
        row.addWidget(self.dpi)
        row.addWidget(self.quality)
        row.addStretch(1)
        bl.addLayout(row)
        self.dpi.valueChanged.connect(lambda _v: self.r_custom.setChecked(True))
        self.quality.valueChanged.connect(lambda _v: self.r_custom.setChecked(True))
        self.lay.addWidget(box)
        self.fonts = QCheckBox("Keep only the font characters that are used")
        self.fonts.setChecked(True)
        self.gray = QCheckBox("Convert images to grayscale")
        self.meta = QCheckBox("Remove metadata")
        for cb in (self.fonts, self.gray, self.meta):
            self.lay.addWidget(cb)
        self.done_layout()

    def settings(self):
        if self.r_custom.isChecked():
            dpi, q = self.dpi.value(), self.quality.value()
        else:
            key = next(k for k, rb in self.presets.items() if rb.isChecked())
            dpi, q = C.PRESETS[key]
        return dict(dpi=dpi, quality=q, subset_fonts=self.fonts.isChecked(), gray=self.gray.isChecked(),
                    remove_metadata=self.meta.isChecked())


class ExportImagesDialog(_Base):
    def __init__(self, doc, current, parent=None):
        super().__init__("Export to Images", parent, ok="Export…")
        self.doc = doc
        f = QFormLayout()
        self.fmt = QComboBox()
        for label, ext in C.IMAGE_FORMATS:
            self.fmt.addItem(label, ext)
        self.dpi = QComboBox()
        for d in (72, 96, 150, 200, 300, 600):
            self.dpi.addItem(f"{d} dpi", d)
        self.dpi.setCurrentIndex(2)
        self.quality = QSpinBox()
        self.quality.setRange(10, 100)
        self.quality.setValue(90)
        f.addRow("Format:", self.fmt)
        f.addRow("Resolution:", self.dpi)
        f.addRow("JPEG quality:", self.quality)
        self.lay.addLayout(f)
        self.range = PageRangeBox(doc.page_count, current)
        self.lay.addWidget(self.range)
        hint = QLabel("Each page becomes one image file.")
        hint.setEnabled(False)
        self.lay.addWidget(hint)
        self.fmt.currentIndexChanged.connect(lambda _i: self.quality.setEnabled(self.fmt.currentData() == "jpg"))
        self.quality.setEnabled(False)
        self.done_layout()

    def validate(self):
        if self.range.pages() is None:
            _warn(self, "Export to Images", "Enter the pages, like 1-3, 5.")
            return False
        return True


def base_name(path: str) -> str:
    return os.path.splitext(os.path.basename(path))[0]
