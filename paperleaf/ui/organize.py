"""Organize Pages: a grid of page thumbnails to reorder, rotate, delete,
duplicate, insert, extract and split pages (like Acrobat's Organize Pages)."""
from __future__ import annotations

from PySide6.QtCore import QPoint, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QColor, QIcon, QKeySequence, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView, QHBoxLayout, QLabel, QListView, QListWidget, QListWidgetItem, QMenu, QPushButton,
    QSizePolicy, QSlider, QToolBar, QToolButton, QVBoxLayout, QWidget,
)

import pymupdf

from paperleaf.ui.icons import icon
from paperleaf.viewer.view import pix_to_pixmap

MIN_THUMB, MAX_THUMB, DEFAULT_THUMB = 90, 360, 160
DROP_COLOR = QColor(0, 120, 215)


def _icon(pm: QPixmap) -> QIcon:
    ic = QIcon(pm)
    ic.addPixmap(pm, QIcon.Mode.Selected)  # no blue tint over selected pages
    return ic


class PageGrid(QListWidget):
    """Thumbnails; drag to reorder, drop files to insert them."""

    pagesMoved = Signal(list, int)        # pages, insert-before index
    filesDropped = Signal(list, int)      # paths, insert-before index
    pageActivated = Signal(int)
    deleteRequested = Signal()

    def __init__(self, doc, parent=None):
        super().__init__(parent)
        self.doc = doc
        self.thumb = DEFAULT_THUMB
        self.setViewMode(QListView.ViewMode.IconMode)
        self.setFlow(QListView.Flow.LeftToRight)
        self.setWrapping(True)
        self.setResizeMode(QListView.ResizeMode.Adjust)
        self.setMovement(QListView.Movement.Snap)
        self.setSpacing(12)
        self.setUniformItemSizes(True)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DragDrop)
        self.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.setAcceptDrops(True)
        self.setDropIndicatorShown(False)
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.setFrameShape(QListWidget.Shape.NoFrame)
        self._drop_at = None
        self._rendered: dict[int, tuple] = {}
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(20)
        self._timer.timeout.connect(self._render_visible)
        self.verticalScrollBar().valueChanged.connect(lambda _v: self._timer.start())
        self.itemDoubleClicked.connect(lambda it: self.pageActivated.emit(self.row(it)))
        self.populate()

    # ------------------------------------------------------------ items
    def _box(self) -> QSize:
        return QSize(self.thumb, int(self.thumb * 1.3))

    def _thumb_size(self, pno: int) -> QSize:
        r = self.doc.page_rects[pno]
        box = self._box()
        s = min(box.width() / r.width, box.height() / r.height)
        return QSize(max(1, int(r.width * s)), max(1, int(r.height * s)))

    def _placeholder(self, pno: int) -> QIcon:
        size = self._thumb_size(pno)
        pm = QPixmap(size)
        pm.fill(QColor("white"))
        p = QPainter(pm)
        p.setPen(QColor(0, 0, 0, 50))
        p.drawRect(0, 0, size.width() - 1, size.height() - 1)
        p.end()
        return _icon(pm)

    def populate(self, keep_selection: list[int] | None = None):
        self.clear()
        self._rendered.clear()
        box = self._box()
        self.setIconSize(box)
        self.setGridSize(QSize(box.width() + 24, box.height() + 36))
        for pno in range(self.doc.page_count):
            it = QListWidgetItem(self._placeholder(pno), self.doc.page_label(pno))
            it.setTextAlignment(Qt.AlignmentFlag.AlignHCenter)
            it.setFlags(it.flags() | Qt.ItemFlag.ItemIsDragEnabled)
            self.addItem(it)
        for p in keep_selection or []:
            if 0 <= p < self.count():
                self.item(p).setSelected(True)
        if keep_selection:
            p = keep_selection[0]
            if 0 <= p < self.count():
                self.setCurrentRow(p, self.selectionModel().SelectionFlag.NoUpdate)
                self.scrollToItem(self.item(p))
        self._timer.start()

    def set_thumb_size(self, px: int):
        sel = self.selected_pages()
        self.thumb = max(MIN_THUMB, min(MAX_THUMB, px))
        self.populate(sel)

    def refresh_pages(self, pages):
        for p in (list(self._rendered) if pages is None else pages):
            self._rendered.pop(p, None)
        self._timer.start()

    def selected_pages(self) -> list[int]:
        return sorted(self.row(i) for i in self.selectedItems())

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
        dpr = self.devicePixelRatioF()
        budget = 10
        for pno in range(self.count()):
            it = self.item(pno)
            rect = self.visualItemRect(it)
            if rect.bottom() < vp.top() or rect.top() > vp.bottom() or pno in self._rendered:
                continue
            size = self._thumb_size(pno)
            r = self.doc.page_rects[pno]
            s = size.width() * dpr / r.width
            try:
                pm = pix_to_pixmap(self.doc.render(pno, pymupdf.Matrix(s, s)))
                pm.setDevicePixelRatio(dpr)
                it.setIcon(_icon(pm))
            except Exception:
                pass
            self._rendered[pno] = True
            budget -= 1
            if budget == 0:
                self._timer.start()
                return

    # -------------------------------------------------------- drag & drop
    def _drop_index(self, pos: QPoint) -> int:
        best, best_d = self.count(), None
        for i in range(self.count()):
            r = self.visualItemRect(self.item(i))
            if r.top() - 8 <= pos.y() <= r.bottom() + 8:
                d = abs(pos.x() - r.center().x())
                if best_d is None or d < best_d:
                    best_d = d
                    best = i if pos.x() < r.center().x() else i + 1
        if best_d is None and self.count():
            last = self.visualItemRect(self.item(self.count() - 1))
            best = 0 if pos.y() < self.visualItemRect(self.item(0)).top() else (
                self.count() if pos.y() > last.bottom() else best)
        return best

    def dragEnterEvent(self, e):
        if e.source() is self or e.mimeData().hasUrls():
            e.setDropAction(Qt.DropAction.MoveAction if e.source() is self else Qt.DropAction.CopyAction)
            e.accept()
        else:
            e.ignore()

    def dragMoveEvent(self, e):
        if e.source() is self or e.mimeData().hasUrls():
            self._drop_at = self._drop_index(e.position().toPoint())
            self.viewport().update()
            e.setDropAction(Qt.DropAction.MoveAction if e.source() is self else Qt.DropAction.CopyAction)
            e.accept()
        else:
            e.ignore()

    def dragLeaveEvent(self, e):
        self._drop_at = None
        self.viewport().update()
        super().dragLeaveEvent(e)

    def dropEvent(self, e):
        at = self._drop_index(e.position().toPoint())
        self._drop_at = None
        self.viewport().update()
        if e.source() is self:
            pages = self.selected_pages()
            e.setDropAction(Qt.DropAction.IgnoreAction)  # we reorder the document ourselves
            e.accept()
            if pages:
                self.pagesMoved.emit(pages, at)
            return
        paths = [u.toLocalFile() for u in e.mimeData().urls() if u.isLocalFile()]
        e.accept()
        if paths:
            self.filesDropped.emit(paths, at)

    def paintEvent(self, e):
        super().paintEvent(e)
        sel = self.selectedItems()
        if sel:
            p = QPainter(self.viewport())
            p.setPen(QPen(DROP_COLOR, 2))
            for it in sel:
                r = self.visualItemRect(it)
                size = self._thumb_size(self.row(it))
                x = r.center().x() - size.width() / 2
                p.drawRect(int(x) - 3, r.top() + 1, size.width() + 5, size.height() + 4)
            p.end()
        if self._drop_at is None or not self.count():
            return
        i = self._drop_at
        if i < self.count():
            r = self.visualItemRect(self.item(i))
            x = r.left() - self.spacing() / 2
        else:
            r = self.visualItemRect(self.item(self.count() - 1))
            x = r.right() + self.spacing() / 2
        p = QPainter(self.viewport())
        p.setPen(QPen(DROP_COLOR, 3))
        p.drawLine(int(x), r.top(), int(x), r.bottom())
        p.end()

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            self.deleteRequested.emit()
            return
        if e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and self.currentRow() >= 0:
            self.pageActivated.emit(self.currentRow())
            return
        super().keyPressEvent(e)


class OrganizePages(QWidget):
    """The Organize Pages view of one document."""

    command = Signal(str, list)   # command name, selected pages (see MainWindow.page_command)
    closed = Signal()
    pageActivated = Signal(int)

    COMMANDS = [  # (command, label, icon, needs a selection)
        ("rotate_ccw", "Rotate Counterclockwise", "rotate_ccw", True),
        ("rotate_cw", "Rotate Clockwise", "rotate", True),
        ("delete", "Delete Pages", "delete", True),
        ("duplicate", "Duplicate Pages", "copy", True),
        None,
        ("insert_file", "Insert from File…", "insert", False),
        ("insert_blank", "Insert Blank Page", "page", False),
        ("replace", "Replace Pages…", "replace", True),
        None,
        ("extract", "Extract Pages…", "extract", False),
        ("split", "Split Document…", "split", False),
        ("crop", "Crop Pages…", "crop", False),
    ]

    def __init__(self, doc, parent=None):
        super().__init__(parent)
        self.doc = doc
        self._structure = doc.structure
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self.bar = QToolBar("Organize Pages")
        self.bar.setObjectName("OrganizeBar")
        self.bar.setIconSize(QSize(20, 20))
        self.bar.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        title = QLabel("  Organize Pages  ")
        title.setStyleSheet("font-weight: 600;")
        self.bar.addWidget(title)
        self.actions_by_name: dict[str, QAction] = {}
        for d in self.COMMANDS:
            if d is None:
                self.bar.addSeparator()
                continue
            name, label, ic, _needs = d
            a = QAction(icon(ic), label, self)
            a.setToolTip(label.replace("…", ""))
            a.triggered.connect(lambda _c=False, n=name: self._run(n))
            self.bar.addAction(a)
            self.actions_by_name[name] = a
        self.count_label = QLabel()
        self.count_label.setEnabled(False)
        self.bar.addWidget(self.count_label)
        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.bar.addWidget(spacer)
        small = QToolButton()
        small.setIcon(icon("zoom_out"))
        small.setAutoRaise(True)
        small.setToolTip("Smaller thumbnails")
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(MIN_THUMB, MAX_THUMB)
        self.slider.setValue(DEFAULT_THUMB)
        self.slider.setFixedWidth(120)
        self.slider.setToolTip("Thumbnail size")
        big = QToolButton()
        big.setIcon(icon("zoom_in"))
        big.setAutoRaise(True)
        big.setToolTip("Larger thumbnails")
        small.clicked.connect(lambda: self.slider.setValue(self.slider.value() - 30))
        big.clicked.connect(lambda: self.slider.setValue(self.slider.value() + 30))
        self.bar.addWidget(small)
        self.bar.addWidget(self.slider)
        self.bar.addWidget(big)
        close = QPushButton("Close")
        close.setToolTip("Back to the document (Esc)")
        close.clicked.connect(self.closed.emit)
        self.bar.addWidget(close)
        lay.addWidget(self.bar)
        self.grid = PageGrid(doc)
        lay.addWidget(self.grid, 1)
        hint = QLabel("  Drag pages to move them, or drag files here to insert them. Double-click a page to open it.")
        hint.setEnabled(False)
        row = QHBoxLayout()
        row.setContentsMargins(4, 2, 4, 4)
        row.addWidget(hint)
        lay.addLayout(row)
        self._size_timer = QTimer(self)
        self._size_timer.setSingleShot(True)
        self._size_timer.setInterval(150)
        self._size_timer.timeout.connect(lambda: self.grid.set_thumb_size(self.slider.value()))
        self.slider.valueChanged.connect(lambda _v: self._size_timer.start())
        self.grid.itemSelectionChanged.connect(self._sync)
        self.grid.pagesMoved.connect(lambda pages, at: self.command.emit(f"move:{at}", pages))
        self.grid.filesDropped.connect(self._files_dropped)
        self.grid.pageActivated.connect(self.pageActivated.emit)
        self.grid.deleteRequested.connect(lambda: self._run("delete"))
        self.grid.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.grid.customContextMenuRequested.connect(self._menu)
        doc.listeners.append(self._doc_changed)
        self.pending_files: list = []
        self._sync()

    def _files_dropped(self, paths, at):
        self.pending_files = paths
        self.command.emit(f"insert_paths:{at}", [])

    def _run(self, name):
        self.command.emit(name, self.grid.selected_pages())

    def select_pages(self, pages):
        self.grid.clearSelection()
        for p in pages:
            if 0 <= p < self.grid.count():
                self.grid.item(p).setSelected(True)
        if pages and 0 <= pages[0] < self.grid.count():
            self.grid.scrollToItem(self.grid.item(pages[0]))

    def _sync(self):
        sel = self.grid.selected_pages()
        for d in self.COMMANDS:
            if d is not None:
                self.actions_by_name[d[0]].setEnabled(bool(sel) or not d[3])
        n = len(sel)
        self.count_label.setText(f"  {n} of {self.doc.page_count} pages selected" if n else
                                 f"  {self.doc.page_count} pages")

    def _menu(self, pos):
        m = QMenu(self)
        for d in self.COMMANDS:
            if d is None:
                m.addSeparator()
            else:
                m.addAction(self.actions_by_name[d[0]])
        m.exec(self.grid.viewport().mapToGlobal(pos))

    def _doc_changed(self, pages):
        if self._structure != self.doc.structure:
            self._structure = self.doc.structure
            sel = [p for p in self.grid.selected_pages() if p < self.doc.page_count]
            self.grid.populate(sel)
        else:
            self.grid.refresh_pages(pages)
        self._sync()

    def keyPressEvent(self, e):
        if e.key() == Qt.Key.Key_Escape:
            self.closed.emit()
            return
        if e.matches(QKeySequence.StandardKey.SelectAll):
            self.grid.selectAll()
            return
        super().keyPressEvent(e)

