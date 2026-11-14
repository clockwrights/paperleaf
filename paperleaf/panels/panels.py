"""Side panels: page thumbnails, bookmarks (outline), attachments, search results."""
from __future__ import annotations

import os
from collections import OrderedDict

import pymupdf
from PySide6.QtCore import QPoint, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView, QFileDialog, QHBoxLayout, QLabel, QListView, QListWidget,
    QListWidgetItem, QMessageBox, QPushButton, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

from paperleaf.core.document import PdfDocument, human_size
from paperleaf.viewer.view import pix_to_pixmap

THUMB_W = 132
MAX_RENDERED_THUMBS = 240  # older ones fall back to a shared placeholder


class ThumbnailPanel(QListWidget):
    pageClicked = Signal(int)

    def __init__(self, doc: PdfDocument, parent=None):
        super().__init__(parent)
        self.doc = doc
        self.rotation = 0
        self.night = False
        self.setViewMode(QListView.ViewMode.IconMode)
        self.setFlow(QListView.Flow.LeftToRight)
        self.setWrapping(True)
        self.setResizeMode(QListView.ResizeMode.Adjust)
        self.setMovement(QListView.Movement.Static)
        self.setUniformItemSizes(False)
        self.setSpacing(8)
        self.setIconSize(QSize(THUMB_W, int(THUMB_W * 1.42)))
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.setFrameShape(QListWidget.Shape.NoFrame)
        self._rendered: OrderedDict = OrderedDict()  # rendered thumbnails, oldest first
        self._placeholders: dict = {}
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(30)
        self._timer.timeout.connect(self._render_visible)
        self._populate()
        self.verticalScrollBar().valueChanged.connect(lambda _v: self._timer.start())
        self.itemClicked.connect(lambda it: self.pageClicked.emit(self.row(it)))
        self.itemActivated.connect(lambda it: self.pageClicked.emit(self.row(it)))

    def _thumb_size(self, pno: int) -> QSize:
        r = self.doc.page_rects[pno]
        w, h = r.width, r.height
        if self.rotation in (90, 270):
            w, h = h, w
        if w >= h:
            return QSize(THUMB_W, max(1, int(THUMB_W * h / w)))
        hh = int(THUMB_W * 1.42)
        return QSize(max(1, int(hh * w / h)), hh)

    def _placeholder(self, size: QSize) -> QIcon:
        key = (size.width(), size.height(), self.night)
        cached = self._placeholders.get(key)
        if cached is not None:
            return cached
        pm = QPixmap(size)
        pm.fill(QColor(255, 255, 255) if not self.night else QColor(0x2A, 0x2A, 0x2A))
        p = QPainter(pm)
        p.setPen(QColor(0, 0, 0, 50))
        p.drawRect(0, 0, size.width() - 1, size.height() - 1)
        p.end()
        ic = QIcon(pm)
        ic.addPixmap(pm, QIcon.Mode.Selected)
        self._placeholders[key] = ic
        return ic

    def _populate(self):
        self.clear()
        self._rendered.clear()
        self._placeholders = {}
        box = QSize(THUMB_W, int(THUMB_W * 1.42))
        for pno in range(self.doc.page_count):
            it = QListWidgetItem(self._placeholder(self._thumb_size(pno)), self.doc.page_label(pno))
            it.setTextAlignment(Qt.AlignmentFlag.AlignHCenter)
            it.setSizeHint(QSize(box.width() + 16, box.height() + 30))
            self.addItem(it)
        self._timer.start()

    def set_view_options(self, rotation: int, night: bool):
        if rotation != self.rotation or night != self.night:
            self.rotation, self.night = rotation, night
            cur = self.currentRow()
            self._populate()
            self.set_current(max(0, cur))

    def showEvent(self, e):
        super().showEvent(e)
        self._timer.start()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._timer.start()

    def _render_visible(self):
        if not self.isVisible():
            return
        vp = self.viewport().rect()
        first = -1
        for y in (10, 40, 80, 140):
            first = self.indexAt(QPoint(vp.width() // 2, y)).row()
            if first >= 0:
                break
        first = max(0, first - 2)
        dpr = self.devicePixelRatioF()
        budget = 12
        pno = first
        n = self.doc.page_count
        while pno < n and budget > 0:
            it = self.item(pno)
            rect = self.visualItemRect(it)
            if rect.top() > vp.bottom():
                break
            if rect.bottom() < vp.top():
                pno += 1
                continue
            if pno not in self._rendered:
                size = self._thumb_size(pno)
                r = self.doc.page_rects[pno]
                rw = r.height if self.rotation in (90, 270) else r.width
                s = size.width() * dpr / rw
                try:
                    pix = self.doc.render(pno, pymupdf.Matrix(s, s).prerotate(self.rotation), night=self.night)
                    pm = pix_to_pixmap(pix)
                    pm.setDevicePixelRatio(dpr)
                    ic = QIcon(pm)
                    ic.addPixmap(pm, QIcon.Mode.Selected)
                    it.setIcon(ic)
                except Exception:
                    pass
                self._rendered[pno] = True
                budget -= 1
                while len(self._rendered) > MAX_RENDERED_THUMBS:
                    old, _ = self._rendered.popitem(last=False)
                    self.item(old).setIcon(self._placeholder(self._thumb_size(old)))
            else:
                self._rendered.move_to_end(pno)
            pno += 1
        if budget == 0:
            self._timer.start()

    def refresh_pages(self, pages):
        """Re-render thumbnails whose pages changed (``None`` = all)."""
        for pno in (list(self._rendered) if pages is None else pages):
            if pno in self._rendered:
                del self._rendered[pno]
        self._timer.start()

    def set_current(self, pno: int):
        if 0 <= pno < self.count():
            self.blockSignals(True)
            self.setCurrentRow(pno)
            self.blockSignals(False)
            self.scrollToItem(self.item(pno), QAbstractItemView.ScrollHint.EnsureVisible)


class OutlinePanel(QTreeWidget):
    destinationClicked = Signal(int, object)  # page index, pymupdf.Point | None

    def __init__(self, doc: PdfDocument, parent=None):
        super().__init__(parent)
        self.doc = doc
        self.setHeaderHidden(True)
        self.setFrameShape(QTreeWidget.Shape.NoFrame)
        self.setWordWrap(True)
        self.itemClicked.connect(self._clicked)
        self.itemActivated.connect(self._clicked)
        self.toc = doc.toc()
        stack: list = []
        for entry in self.toc:
            level, title, page = entry[0], entry[1], entry[2]
            dest = entry[3] if len(entry) > 3 else {}
            it = QTreeWidgetItem([title.strip() or "(untitled)"])
            it.setToolTip(0, title)
            point = None
            if isinstance(dest, dict) and dest.get("kind") == pymupdf.LINK_GOTO:
                to = dest.get("to")
                if to is not None:
                    point = pymupdf.Point(to)
            it.setData(0, Qt.ItemDataRole.UserRole, (page - 1, point))
            while len(stack) >= level:
                stack.pop()
            if stack:
                stack[-1].addChild(it)
            else:
                self.addTopLevelItem(it)
            stack.append(it)
            if isinstance(dest, dict) and not dest.get("collapse", True):
                it.setExpanded(True)
        if self.topLevelItemCount() <= 12:
            for i in range(self.topLevelItemCount()):
                self.topLevelItem(i).setExpanded(True)

    def is_empty(self) -> bool:
        return not self.toc

    def _clicked(self, item, _col=0):
        data = item.data(0, Qt.ItemDataRole.UserRole)
        if data and data[0] >= 0:
            self.destinationClicked.emit(data[0], data[1])


class AttachmentsPanel(QWidget):
    def __init__(self, doc: PdfDocument, parent=None):
        super().__init__(parent)
        self.doc = doc
        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        self.list = QListWidget()
        self.list.setFrameShape(QListWidget.Shape.NoFrame)
        self.items = doc.attachments()
        for a in self.items:
            it = QListWidgetItem(f"{a['filename']}\n{human_size(a['size'])}")
            it.setToolTip(a.get("desc") or a["filename"])
            self.list.addItem(it)
        lay.addWidget(self.list)
        row = QHBoxLayout()
        self.save_btn = QPushButton("Save…")
        self.save_btn.clicked.connect(self.save_selected)
        self.save_all_btn = QPushButton("Save all…")
        self.save_all_btn.clicked.connect(self.save_all)
        row.addWidget(self.save_btn)
        row.addWidget(self.save_all_btn)
        lay.addLayout(row)
        if not self.items:
            self.list.addItem("This document has no attachments.")
            self.list.setEnabled(False)
            self.save_btn.setEnabled(False)
            self.save_all_btn.setEnabled(False)
        self.list.itemDoubleClicked.connect(lambda _it: self.save_selected())

    def save_selected(self):
        row = self.list.currentRow()
        if row < 0 or row >= len(self.items):
            return
        a = self.items[row]
        path, _ = QFileDialog.getSaveFileName(self, "Save attachment", a["filename"])
        if path:
            self._write(a, path)

    def save_all(self):
        folder = QFileDialog.getExistingDirectory(self, "Save attachments to folder")
        if not folder:
            return
        for a in self.items:
            self._write(a, os.path.join(folder, os.path.basename(a["filename"])))

    def _write(self, a, path):
        try:
            with open(path, "wb") as f:
                f.write(self.doc.attachment_bytes(a["name"]))
        except OSError as exc:
            QMessageBox.warning(self, "Save attachment", f"Could not save the file:\n{exc}")


class SearchResultsPanel(QWidget):
    hitClicked = Signal(int, int)  # page, index within page

    def __init__(self, doc: PdfDocument, parent=None):
        super().__init__(parent)
        self.doc = doc
        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        self.summary = QLabel("Use Find (Ctrl+F) to search this document.")
        self.summary.setWordWrap(True)
        lay.addWidget(self.summary)
        self.list = QListWidget()
        self.list.setFrameShape(QListWidget.Shape.NoFrame)
        self.list.setWordWrap(True)
        self.list.setUniformItemSizes(False)
        lay.addWidget(self.list)
        self.list.itemClicked.connect(self._clicked)
        self.list.itemActivated.connect(self._clicked)

    def reset(self, query: str | None):
        self.list.clear()
        self.summary.setText(f"Searching for “{query}”…" if query else "Use Find (Ctrl+F) to search this document.")

    def add_hits(self, pno: int, hits: list):
        text = self.doc.page_text(pno).text
        for i, (s, e) in enumerate(hits):
            if self.list.count() >= 5000:
                return
            a = max(0, s - 40)
            b = min(len(text), e + 40)
            before = text[a:s].replace("\n", " ")
            match = text[s:e].replace("\n", " ")
            after = text[e:b].replace("\n", " ")
            it = QListWidgetItem(f"Page {self.doc.page_label(pno)}:  …{before}[{match}]{after}…")
            it.setData(Qt.ItemDataRole.UserRole, (pno, i))
            self.list.addItem(it)

    def finished(self, total: int, done: bool):
        if done:
            self.summary.setText(f"{total} result{'s' if total != 1 else ''} found.")
        else:
            self.summary.setText(f"Searching… {total} found so far")

    def _clicked(self, item):
        data = item.data(Qt.ItemDataRole.UserRole)
        if data:
            self.hitClicked.emit(*data)
