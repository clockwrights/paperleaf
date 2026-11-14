"""Dialogs for a single annotation: comment text + replies, and properties."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFormLayout, QGroupBox, QLabel,
    QLineEdit, QPlainTextEdit, QSpinBox, QTextBrowser, QVBoxLayout,
)

from paperleaf.core import annotations as an
from paperleaf.ui.annotate_bar import ColorButton, HAS_DASH, HAS_FILL, HAS_FONT, HAS_WIDTH


def _html_escape(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("\n", "<br>")


class CommentDialog(QDialog):
    """Open a sticky note / comment: edit its text, read the thread, write a reply."""

    def __init__(self, doc, rec: an.AnnotRecord, author: str, parent=None, editable=True):
        super().__init__(parent)
        self.doc, self.rec, self.author = doc, rec, author
        self.setWindowTitle(rec.label)
        self.resize(460, 420)
        lay = QVBoxLayout(self)
        head = QLabel(f"<b>{_html_escape(rec.author or 'Unknown author')}</b>"
                      f"&nbsp;&nbsp;<span style='color:gray'>{an.format_date(rec.modified or rec.created)}"
                      f" · page {doc.page_label(rec.pno)}</span>")
        lay.addWidget(head)
        quoted = an.markup_text(doc, rec)
        if quoted:
            q = QLabel(f"<i>“{_html_escape(quoted[:300])}”</i>")
            q.setWordWrap(True)
            lay.addWidget(q)
        self.text = QPlainTextEdit(rec.contents)
        self.text.setPlaceholderText("Add a comment…")
        self.text.setReadOnly(not editable)
        lay.addWidget(self.text, 2)
        reps = an.replies(doc, rec)
        status = an.status_of(doc, rec)
        if reps or status:
            thread = QTextBrowser()
            parts = []
            if status:
                parts.append(f"<p><b>Status:</b> {status}</p>")
            for r in sorted(reps, key=lambda r: r.created or r.modified):
                parts.append(f"<p><b>{_html_escape(r.author or 'Unknown')}</b> "
                             f"<span style='color:gray'>{an.format_date(r.modified or r.created)}</span><br>"
                             f"{_html_escape(r.contents)}</p>")
            thread.setHtml("".join(parts))
            lay.addWidget(QLabel("Replies"))
            lay.addWidget(thread, 2)
        self.reply = QLineEdit()
        self.reply.setPlaceholderText("Write a reply…")
        self.reply.setEnabled(editable)
        lay.addWidget(self.reply)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self._ok)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        self.text.setFocus()

    def _ok(self):
        rec = self.rec
        text = self.text.toPlainText()
        if text != rec.contents and not self.text.isReadOnly():
            if rec.kind == "FreeText":
                an.set_freetext(self.doc, rec.pno, rec.xref, text, None)
            else:
                an.set_contents(self.doc, rec.pno, rec.xref, text)
        if self.reply.text().strip():
            an.reply(self.doc, rec.pno, rec.xref, self.reply.text().strip(), self.author)
        self.accept()


class PropertiesDialog(QDialog):
    def __init__(self, doc, rec: an.AnnotRecord, parent=None):
        super().__init__(parent)
        self.doc, self.rec = doc, rec
        self.setWindowTitle(f"{rec.label} Properties")
        lay = QVBoxLayout(self)
        app = QGroupBox("Appearance")
        f = QFormLayout(app)
        kind = rec.kind
        self.color = ColorButton("Color", allow_none=False)
        self.color.set_color((rec.text_color if kind == "FreeText" else rec.stroke) or (0, 0, 0))
        self.fill = ColorButton("Fill", allow_none=True)
        self.fill.set_color(rec.fill)
        self.width = QDoubleSpinBox()
        self.width.setRange(0, 50)
        self.width.setSingleStep(0.5)
        self.width.setSuffix(" pt")
        self.width.setValue(rec.width)
        self.opacity = QSpinBox()
        self.opacity.setRange(5, 100)
        self.opacity.setSuffix(" %")
        self.opacity.setValue(int(round(rec.opacity * 100)))
        self.fontsize = QDoubleSpinBox()
        self.fontsize.setRange(4, 144)
        self.fontsize.setSuffix(" pt")
        self.fontsize.setValue(rec.fontsize or 12)
        self.dashed = QCheckBox("Dashed")
        self.dashed.setChecked(rec.dashed)
        if kind != "Stamp":
            f.addRow("Color:", self.color)
        if kind in HAS_FILL:
            f.addRow("Fill:", self.fill)
        if kind in HAS_WIDTH:
            f.addRow("Thickness:", self.width)
        f.addRow("Opacity:", self.opacity)
        if kind in HAS_FONT:
            f.addRow("Font size:", self.fontsize)
        if kind in HAS_DASH:
            f.addRow("", self.dashed)
        lay.addWidget(app)
        gen = QGroupBox("General")
        g = QFormLayout(gen)
        self.author = QLineEdit(rec.author)
        g.addRow("Author:", self.author)
        g.addRow("Type:", QLabel(rec.label))
        g.addRow("Modified:", QLabel(an.format_date(rec.modified or rec.created)))
        self.contents = QPlainTextEdit(rec.contents)
        self.contents.setMaximumHeight(110)
        if not rec.is_symbol:
            g.addRow("Comment:", self.contents)
        lay.addWidget(gen)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self._ok)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def _ok(self):
        rec, d = self.rec, self.doc
        changes = {}
        color = self.color._color
        old_color = rec.text_color if rec.kind == "FreeText" else rec.stroke
        if rec.kind != "Stamp" and color != (tuple(old_color) if old_color else None):
            changes["color"] = color
        if rec.kind in HAS_FILL and self.fill._color != (tuple(rec.fill) if rec.fill else None):
            changes["fill"] = self.fill._color
        if rec.kind in HAS_WIDTH and abs(self.width.value() - rec.width) > 1e-3:
            changes["width"] = self.width.value()
        if abs(self.opacity.value() / 100 - rec.opacity) > 0.004:
            changes["opacity"] = self.opacity.value() / 100
        if rec.kind in HAS_FONT and abs(self.fontsize.value() - (rec.fontsize or 12)) > 1e-3:
            changes["fontsize"] = self.fontsize.value()
        if rec.kind in HAS_DASH and self.dashed.isChecked() != rec.dashed:
            changes["dashed"] = self.dashed.isChecked()
        if changes:
            an.set_style(d, rec.pno, rec.xref, **changes)
        if self.author.text() != rec.author:
            an.set_author(d, rec.pno, rec.xref, self.author.text())
        if not rec.is_symbol and self.contents.toPlainText() != rec.contents:
            if rec.kind == "FreeText":
                an.set_freetext(d, rec.pno, rec.xref, self.contents.toPlainText(), None)
            else:
                an.set_contents(d, rec.pno, rec.xref, self.contents.toPlainText())
        self.accept()
