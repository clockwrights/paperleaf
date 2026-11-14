"""Prepare Form: the field properties dialog."""
from __future__ import annotations

from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QGroupBox, QLabel, QLineEdit,
    QPlainTextEdit, QSpinBox, QVBoxLayout,
)

from paperleaf.core import forms as F

FONT_SIZES = [6, 7, 8, 9, 10, 11, 12, 14, 16, 18, 20, 24, 28, 36]


def parse_options(text: str) -> list[tuple[str, str]]:
    """One choice per line; "Shown text | export value" sets a separate export value."""
    out, seen = [], set()
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if "|" in line:
            disp, exp = (p.strip() for p in line.split("|", 1))
            exp = exp or disp
        else:
            disp = exp = line
        if exp in seen:
            continue
        seen.add(exp)
        out.append((exp, disp))
    return out


def format_options(options) -> str:
    return "\n".join(d if e == d else f"{d} | {e}" for e, d in options)


class FieldPropertiesDialog(QDialog):
    def __init__(self, doc, f: F.Field, parent=None):
        super().__init__(parent)
        self.doc, self.field = doc, f
        self.setWindowTitle(f"{f.label} Properties")
        self.setMinimumWidth(420)
        lay = QVBoxLayout(self)

        gen = QGroupBox("General")
        g = QFormLayout(gen)
        self.name = QLineEdit(f.name)
        g.addRow("Group name:" if f.kind == F.RADIO else "Name:", self.name)
        self.tooltip = QLineEdit(f.tooltip)
        self.tooltip.setPlaceholderText("Shown when you point at the field")
        g.addRow("Tooltip:", self.tooltip)
        self.required = QCheckBox("Required")
        self.required.setChecked(f.required)
        self.readonly = QCheckBox("Read only")
        self.readonly.setChecked(f.readonly)
        if f.kind != F.SIGNATURE:
            g.addRow("", self.required)
        g.addRow("", self.readonly)
        lay.addWidget(gen)

        self.font = QComboBox()
        self.font.setEditable(True)
        self.font.addItem("Auto", 0.0)
        for size in FONT_SIZES:
            self.font.addItem(f"{size} pt", float(size))
        i = self.font.findData(float(f.fontsize))
        if i >= 0:
            self.font.setCurrentIndex(i)
        else:
            self.font.setEditText(f"{f.fontsize:g} pt")

        opt = QGroupBox("Options")
        o = QFormLayout(opt)
        self.default = None
        if f.kind == F.TEXT:
            self.multiline = QCheckBox("Multi-line")
            self.multiline.setChecked(f.multiline)
            self.maxlen = QSpinBox()
            self.maxlen.setRange(0, 9999)
            self.maxlen.setSpecialValueText("No limit")
            self.maxlen.setValue(f.maxlen)
            self.comb = QCheckBox("Comb of characters (one box per character)")
            self.comb.setChecked(bool(f.flags & F.FF_COMB))
            self.default = QLineEdit(f.default or "")
            o.addRow("Default value:", self.default)
            o.addRow("Limit of characters:", self.maxlen)
            o.addRow("", self.multiline)
            o.addRow("", self.comb)
            o.addRow("Font size:", self.font)
            self.multiline.toggled.connect(self._sync)
            self.maxlen.valueChanged.connect(self._sync)
        elif f.kind in (F.CHECK, F.RADIO):
            self.export = QLineEdit(f.on_state)
            o.addRow("Choice:" if f.kind == F.RADIO else "Export value:", self.export)
            self.default_on = QCheckBox("Selected by default" if f.kind == F.RADIO else "Checked by default")
            self.default_on.setChecked(bool(f.default) and f.default == f.on_state)
            o.addRow("", self.default_on)
        elif f.kind in (F.COMBO, F.LIST):
            self.options = QPlainTextEdit(format_options(f.options))
            self.options.setPlaceholderText("One choice per line")
            self.options.setMinimumHeight(110)
            o.addRow("Choices:", self.options)
            hint = QLabel("One choice per line. To give a choice a different export value, write "
                          "“Shown text | value”.")
            hint.setWordWrap(True)
            hint.setEnabled(False)
            o.addRow("", hint)
            self.default = QComboBox()
            self.options.textChanged.connect(self._fill_default)
            o.addRow("Default:", self.default)
            if f.kind == F.COMBO:
                self.editable = QCheckBox("Allow typing your own text")
                self.editable.setChecked(f.editable)
                o.addRow("", self.editable)
            else:
                self.multiselect = QCheckBox("Allow choosing more than one")
                self.multiselect.setChecked(f.multiselect)
                o.addRow("", self.multiselect)
            o.addRow("Font size:", self.font)
            self._fill_default()
        if f.kind != F.SIGNATURE:
            lay.addWidget(opt)

        self.error = QLabel()
        self.error.setStyleSheet("color: #c42b1c;")
        self.error.setWordWrap(True)
        self.error.hide()
        lay.addWidget(self.error)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self._ok)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        self._sync()

    def _sync(self):
        if self.field.kind == F.TEXT:
            self.comb.setEnabled(self.maxlen.value() > 0 and not self.multiline.isChecked())

    def _fill_default(self):
        f = self.field
        cur = self.default.currentData() if self.default.count() else (
            f.default[0] if isinstance(f.default, list) and f.default else f.default)
        self.default.clear()
        self.default.addItem("(none)", "")
        for exp, disp in parse_options(self.options.toPlainText()):
            self.default.addItem(disp, exp)
        i = self.default.findData(cur or "")
        self.default.setCurrentIndex(max(0, i))

    def _font_size(self):
        data = self.font.currentData()
        text = self.font.currentText().strip().lower().replace("pt", "").strip()
        if self.font.findText(self.font.currentText()) >= 0 and data is not None:
            return float(data)
        if text in ("", "auto"):
            return 0.0
        try:
            return max(0.0, min(144.0, float(text.replace(",", "."))))
        except ValueError:
            return None

    def properties(self) -> dict:
        """The changed properties, for ``forms.set_properties``."""
        f = self.field
        props = {}
        name = self.name.text().strip()
        if name != f.name:
            props["name"] = name
        if self.tooltip.text() != f.tooltip:
            props["tooltip"] = self.tooltip.text()
        if self.required.isChecked() != f.required and f.kind != F.SIGNATURE:
            props["required"] = self.required.isChecked()
        if self.readonly.isChecked() != f.readonly:
            props["readonly"] = self.readonly.isChecked()
        if f.kind in (F.TEXT, F.COMBO, F.LIST):
            fs = self._font_size()
            if fs is not None and abs(fs - f.fontsize) > 1e-3:
                props["fontsize"] = fs
        if f.kind == F.TEXT:
            if self.multiline.isChecked() != f.multiline:
                props["multiline"] = self.multiline.isChecked()
            if self.maxlen.value() != f.maxlen:
                props["maxlen"] = self.maxlen.value()
            comb = self.comb.isChecked() and self.comb.isEnabled()
            if comb != bool(f.flags & F.FF_COMB):
                props["comb"] = comb
            if self.default.text() != (f.default or ""):
                props["default"] = self.default.text()
        elif f.kind in (F.CHECK, F.RADIO):
            export = self.export.text().strip()
            if export and export != f.on_state:
                props["export"] = export
            on = self.default_on.isChecked()
            was = bool(f.default) and f.default == f.on_state
            if on != was:
                props["default"] = (export or f.on_state) if on else "Off"
        elif f.kind in (F.COMBO, F.LIST):
            opts = parse_options(self.options.toPlainText())
            if opts != f.options:
                props["options"] = opts
            d = self.default.currentData() or ""
            old = f.default if not isinstance(f.default, list) else (f.default[0] if f.default else "")
            if d != (old or ""):
                props["default"] = [d] if f.kind == F.LIST and d else ([] if f.kind == F.LIST else d)
            if f.kind == F.COMBO and self.editable.isChecked() != f.editable:
                props["editable"] = self.editable.isChecked()
            if f.kind == F.LIST and self.multiselect.isChecked() != f.multiselect:
                props["multiselect"] = self.multiselect.isChecked()
        return props

    def _problem(self, props) -> str:
        if "name" in props:
            p = F.name_problem(self.doc, self.field, props["name"])
            if p:
                return p
        if self.field.kind in (F.COMBO, F.LIST) and not parse_options(self.options.toPlainText()) \
                and not (self.field.kind == F.COMBO and self.editable.isChecked()):
            return "Add at least one choice."
        if self.field.kind in (F.TEXT, F.COMBO, F.LIST) and self._font_size() is None:
            return "Enter a font size in points, or choose Auto."
        return ""

    def _ok(self):
        props = self.properties()
        problem = self._problem(props)
        if problem:
            self.error.setText(problem)
            self.error.show()
            return
        if props:
            try:
                F.set_properties(self.doc, self.field, **props)
            except Exception as exc:
                self.error.setText(str(exc))
                self.error.show()
                return
        self.accept()
