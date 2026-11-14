"""Comments panel: every annotation in the document, grouped by page, with replies."""
from __future__ import annotations

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QComboBox, QHBoxLayout, QLabel, QLineEdit, QMenu, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

from paperleaf.core import annotations as an
from paperleaf.ui.icons import icon

LINE_CHARS = 42


def _short(text: str) -> str:
    """One display line: no line breaks, cut to fit the panel (full text is in the tooltip)."""
    text = " ".join(text.split())
    return text if len(text) <= LINE_CHARS else text[:LINE_CHARS - 1] + "…"


KIND_ICONS = {
    "Highlight": "highlight", "Underline": "underline", "StrikeOut": "strikeout", "Squiggly": "squiggly",
    "Text": "note", "FreeText": "textbox", "Ink": "pen", "Line": "line", "Square": "rect",
    "Circle": "ellipse", "Polygon": "polygon", "PolyLine": "polyline", "Stamp": "stamp",
}


class CommentsPanel(QWidget):
    annotClicked = Signal(int, int)         # page, xref
    annotActivated = Signal(int, int)       # double-click: open
    replyRequested = Signal(int, int)
    statusRequested = Signal(int, int, str)
    deleteRequested = Signal(int, int)

    def __init__(self, doc, parent=None):
        super().__init__(parent)
        self.doc = doc
        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        row = QHBoxLayout()
        self.filter = QComboBox()
        self.filter.addItem("All types", None)
        self.filter.currentIndexChanged.connect(lambda _i: self.refresh())
        row.addWidget(self.filter, 1)
        lay.addLayout(row)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search comments")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(lambda _t: self._debounce.start())
        lay.addWidget(self.search)
        self.count = QLabel()
        self.count.setEnabled(False)
        lay.addWidget(self.count)
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setWordWrap(False)
        self.tree.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.tree.setUniformRowHeights(False)
        self.tree.setFrameShape(QTreeWidget.Shape.NoFrame)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._menu)
        self.tree.itemClicked.connect(self._clicked)
        self.tree.itemDoubleClicked.connect(self._activated)
        lay.addWidget(self.tree, 1)
        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(150)
        self._debounce.timeout.connect(self.refresh)
        self._dirty = True
        doc.listeners.append(self._doc_changed)

    def _doc_changed(self, _pages):
        self._dirty = True
        if self.isVisible():
            self._debounce.start()

    def showEvent(self, e):
        super().showEvent(e)
        if self._dirty:
            self.refresh()

    def all_comments(self) -> list[an.AnnotRecord]:
        out = []
        for pno in range(self.doc.page_count):
            for rec in an.page_annots(self.doc, pno):
                if not rec.is_reply and not rec.hidden:
                    out.append(rec)
        return out

    def refresh(self):
        self._dirty = False
        recs = self.all_comments()
        # filter choices follow the kinds present
        kinds = sorted({r.label for r in recs})
        cur = self.filter.currentData()
        self.filter.blockSignals(True)
        self.filter.clear()
        self.filter.addItem("All types", None)
        for k in kinds:
            self.filter.addItem(k, k)
        idx = self.filter.findData(cur)
        self.filter.setCurrentIndex(max(0, idx))
        self.filter.blockSignals(False)
        kind = self.filter.currentData()
        needle = self.search.text().strip().lower()
        expanded_sel = self._selected_key()
        self.tree.clear()
        shown = 0
        pages: dict[int, QTreeWidgetItem] = {}
        bold = QFont()
        bold.setBold(True)
        for rec in sorted(recs, key=lambda r: (r.pno, r.rect.y0, r.rect.x0)):
            if kind and rec.label != kind:
                continue
            body = "" if rec.is_symbol or (rec.kind == "Stamp" and rec.contents == "Image Stamp") else rec.contents
            quoted = an.markup_text(self.doc, rec)
            reps = an.replies(self.doc, rec)
            hay = " ".join([rec.author, body, quoted, rec.label] + [r.contents for r in reps]).lower()
            if needle and needle not in hay:
                continue
            if rec.pno not in pages:
                pi = QTreeWidgetItem([f"Page {self.doc.page_label(rec.pno)}"])
                pi.setFont(0, bold)
                pi.setFlags(Qt.ItemFlag.ItemIsEnabled)
                self.tree.addTopLevelItem(pi)
                pi.setExpanded(True)
                pages[rec.pno] = pi
            status = an.status_of(self.doc, rec)
            date = an.format_date(rec.modified or rec.created)
            head = f"{rec.label} · {rec.author or 'Unknown'}" + (f"  [{status}]" if status else "")
            full = [head, date]
            lines = [_short(head)]
            if quoted:
                lines.append(_short(f"“{quoted}”"))
                full.append(f"“{quoted}”")
            if body:
                lines.append(_short(body))
                full.append(body)
            it = QTreeWidgetItem(["\n".join(lines)])
            it.setIcon(0, icon(KIND_ICONS.get(rec.kind, "comments")))
            it.setData(0, Qt.ItemDataRole.UserRole, (rec.pno, rec.xref))
            it.setToolTip(0, "\n".join(full))
            pages[rec.pno].addChild(it)
            for r in sorted(reps, key=lambda r: r.created or r.modified):
                ri = QTreeWidgetItem([_short(f"{r.author or 'Unknown'}: {r.contents}")])
                ri.setToolTip(0, f"{r.author or 'Unknown'}  {an.format_date(r.modified or r.created)}\n{r.contents}")
                ri.setIcon(0, icon("reply"))
                ri.setData(0, Qt.ItemDataRole.UserRole, (rec.pno, rec.xref))
                it.addChild(ri)
            it.setExpanded(True)
            shown += 1
            if expanded_sel == (rec.pno, rec.xref):
                self.tree.setCurrentItem(it)
        total = len(recs)
        self.count.setText(f"{total} comment{'s' if total != 1 else ''}" +
                           (f" ({shown} shown)" if shown != total else ""))
        if not total:
            hint = QTreeWidgetItem(["No comments yet.\nUse the Comment & Sign toolbar to add some."])
            hint.setFlags(Qt.ItemFlag.NoItemFlags)
            self.tree.addTopLevelItem(hint)

    def _selected_key(self):
        it = self.tree.currentItem()
        return it.data(0, Qt.ItemDataRole.UserRole) if it else None

    def select(self, pno, xref):
        for i in range(self.tree.topLevelItemCount()):
            top = self.tree.topLevelItem(i)
            for j in range(top.childCount()):
                it = top.child(j)
                if it.data(0, Qt.ItemDataRole.UserRole) == (pno, xref):
                    self.tree.blockSignals(True)
                    self.tree.setCurrentItem(it)
                    self.tree.blockSignals(False)
                    self.tree.scrollToItem(it)
                    return

    def _clicked(self, item, _col=0):
        data = item.data(0, Qt.ItemDataRole.UserRole)
        if data:
            self.annotClicked.emit(*data)

    def _activated(self, item, _col=0):
        data = item.data(0, Qt.ItemDataRole.UserRole)
        if data:
            self.annotActivated.emit(*data)

    def _menu(self, pos):
        item = self.tree.itemAt(pos)
        data = item.data(0, Qt.ItemDataRole.UserRole) if item else None
        if not data:
            return
        m = QMenu(self)
        editable = self.doc.can_annotate()
        m.addAction("Open…", lambda: self.annotActivated.emit(*data))
        a = m.addAction(icon("reply"), "Reply…", lambda: self.replyRequested.emit(*data))
        a.setEnabled(editable)
        sm = m.addMenu("Set Status")
        sm.setEnabled(editable)
        for st in an.STATUSES:
            sm.addAction(st, lambda s=st: self.statusRequested.emit(data[0], data[1], s))
        m.addSeparator()
        a = m.addAction(icon("delete"), "Delete", lambda: self.deleteRequested.emit(*data))
        a.setEnabled(editable)
        m.exec(self.tree.viewport().mapToGlobal(pos))
