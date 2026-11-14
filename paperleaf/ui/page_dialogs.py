"""Dialogs for page tools: insert, extract, split, combine, rotate, delete, crop,
page size, header & footer, page numbers, Bates numbers and watermarks."""
from __future__ import annotations

import os

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView, QButtonGroup, QCheckBox, QColorDialog, QComboBox, QDialog, QDialogButtonBox,
    QDoubleSpinBox, QFileDialog, QFormLayout, QGridLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit,
    QListWidget, QListWidgetItem, QMessageBox, QPlainTextEdit, QPushButton, QRadioButton, QSpinBox,
    QTabWidget, QToolButton, QVBoxLayout, QWidget,
)

from paperleaf.core import pages as P
from paperleaf.ui.icons import color_swatch, icon

UNITS = {"in": 72.0, "mm": 72.0 / 25.4, "pt": 1.0}


def _error(parent, title, text):
    QMessageBox.warning(parent, title, text)


class PageRangeBox(QGroupBox):
    """All / Current page / Pages: [1-3, 5] (+ optional Even / Odd)."""

    def __init__(self, page_count: int, current: int = 0, selected=None, subset: bool = True,
                 title: str = "Pages", current_label: str = "Current page", parent=None):
        super().__init__(title, parent)
        self.page_count, self.current = page_count, current
        lay = QGridLayout(self)
        self.r_all = QRadioButton(f"All ({page_count})")
        self.r_cur = QRadioButton(f"{current_label} ({current + 1})")
        self.r_range = QRadioButton("Pages:")
        self.edit = QLineEdit()
        self.edit.setPlaceholderText(f"e.g. 1-3, 5, 8-{page_count}")
        self.group = QButtonGroup(self)
        for i, b in enumerate((self.r_all, self.r_cur, self.r_range)):
            self.group.addButton(b, i)
        lay.addWidget(self.r_all, 0, 0, 1, 2)
        lay.addWidget(self.r_cur, 1, 0, 1, 2)
        lay.addWidget(self.r_range, 2, 0)
        lay.addWidget(self.edit, 2, 1)
        self.subset = QComboBox()
        self.subset.addItem("All pages in range", "all")
        self.subset.addItem("Even pages only", "even")
        self.subset.addItem("Odd pages only", "odd")
        if subset:
            lay.addWidget(QLabel("Include:"), 3, 0)
            lay.addWidget(self.subset, 3, 1)
        self.edit.textEdited.connect(lambda _t: self.r_range.setChecked(True))
        if selected:
            self.edit.setText(P.format_ranges(selected))
            self.r_range.setChecked(True)
        else:
            self.r_all.setChecked(True)

    def pages(self) -> list[int] | None:
        if self.r_all.isChecked():
            pages = list(range(self.page_count))
        elif self.r_cur.isChecked():
            pages = [self.current]
        else:
            pages = P.parse_ranges(self.edit.text(), self.page_count)
            if pages is None:
                return None
        mode = self.subset.currentData()
        if mode == "even":
            pages = [p for p in pages if (p + 1) % 2 == 0]
        elif mode == "odd":
            pages = [p for p in pages if (p + 1) % 2 == 1]
        return sorted(set(pages)) or None


class _Dialog(QDialog):
    def __init__(self, title: str, parent=None, ok: str = "OK"):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.lay = QVBoxLayout(self)
        self.bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        self.ok_btn = self.bb.addButton(ok, QDialogButtonBox.ButtonRole.AcceptRole)
        self.ok_btn.setDefault(True)
        self.bb.accepted.connect(self._ok)
        self.bb.rejected.connect(self.reject)

    def finish_layout(self):
        self.lay.addWidget(self.bb)

    def _ok(self):
        if self.validate():
            self.accept()

    def validate(self) -> bool:
        return True


class RotateDialog(_Dialog):
    def __init__(self, page_count, current, selected=None, parent=None):
        super().__init__("Rotate Pages", parent)
        f = QFormLayout()
        self.direction = QComboBox()
        self.direction.addItem("Clockwise 90°", 90)
        self.direction.addItem("Counterclockwise 90°", 270)
        self.direction.addItem("180°", 180)
        f.addRow("Direction:", self.direction)
        self.lay.addLayout(f)
        self.range = PageRangeBox(page_count, current, selected)
        self.lay.addWidget(self.range)
        self.finish_layout()

    def validate(self):
        if self.range.pages() is None:
            _error(self, "Rotate Pages", "Enter the pages to rotate, like 1-3, 5.")
            return False
        return True


class DeleteDialog(_Dialog):
    def __init__(self, page_count, current, selected=None, parent=None):
        super().__init__("Delete Pages", parent, ok="Delete")
        self.range = PageRangeBox(page_count, current, selected)
        self.lay.addWidget(self.range)
        self.finish_layout()

    def validate(self):
        pages = self.range.pages()
        if pages is None:
            _error(self, "Delete Pages", "Enter the pages to delete, like 1-3, 5.")
            return False
        if len(pages) >= self.range.page_count:
            _error(self, "Delete Pages", "You can't delete all the pages of a document.")
            return False
        return True


class InsertDialog(_Dialog):
    """Where to insert: before/after a page, first, last. ``blank`` adds size and count."""

    def __init__(self, page_count, current, title="Insert Pages", blank=False, sizes_from=None, parent=None):
        super().__init__(title, parent, ok="Insert")
        self.page_count = page_count
        box = QGroupBox("Location")
        g = QGridLayout(box)
        self.where = QComboBox()
        self.where.addItem("After", "after")
        self.where.addItem("Before", "before")
        self.page = QSpinBox()
        self.page.setRange(1, max(1, page_count))
        self.page.setValue(current + 1)
        self.r_page = QRadioButton("Page:")
        self.r_first = QRadioButton("First page")
        self.r_last = QRadioButton("Last page")
        self.r_page.setChecked(True)
        g.addWidget(self.where, 0, 0)
        g.addWidget(self.r_page, 0, 1)
        g.addWidget(self.page, 0, 2)
        g.addWidget(self.r_first, 1, 1)
        g.addWidget(self.r_last, 2, 1)
        self.lay.addWidget(box)
        self.blank = blank
        if blank:
            f = QFormLayout()
            self.size = QComboBox()
            if sizes_from:
                w, h = sizes_from
                self.size.addItem(f"Same as the current page ({w / 72:.2f} × {h / 72:.2f} in)", (w, h))
            for name, wh in P.PAPER_SIZES.items():
                self.size.addItem(f"{name} portrait", wh)
                self.size.addItem(f"{name} landscape", (wh[1], wh[0]))
            self.count = QSpinBox()
            self.count.setRange(1, 500)
            f.addRow("Page size:", self.size)
            f.addRow("Number of pages:", self.count)
            self.lay.addLayout(f)
        self.finish_layout()

    def position(self) -> int:
        if self.r_first.isChecked():
            return 0
        if self.r_last.isChecked():
            return self.page_count
        p = self.page.value() - 1
        return p + 1 if self.where.currentData() == "after" else p


class ExtractDialog(_Dialog):
    def __init__(self, page_count, current, selected=None, allow_delete=True, parent=None):
        super().__init__("Extract Pages", parent, ok="Extract")
        self.range = PageRangeBox(page_count, current, selected)
        self.lay.addWidget(self.range)
        self.delete = QCheckBox("Delete pages after extracting")
        self.delete.setEnabled(allow_delete)
        self.separate = QCheckBox("Extract pages as separate files")
        self.lay.addWidget(self.delete)
        self.lay.addWidget(self.separate)
        self.finish_layout()

    def validate(self):
        pages = self.range.pages()
        if pages is None:
            _error(self, "Extract Pages", "Enter the pages to extract, like 1-3, 5.")
            return False
        if self.delete.isChecked() and len(pages) >= self.range.page_count:
            _error(self, "Extract Pages", "You can't delete all the pages of a document. "
                                          "Untick “Delete pages after extracting”.")
            return False
        return True


class SplitDialog(_Dialog):
    def __init__(self, doc, parent=None):
        super().__init__("Split Document", parent, ok="Split")
        self.doc = doc
        box = QGroupBox("Split by")
        g = QGridLayout(box)
        self.r_pages = QRadioButton("Number of pages:")
        self.n_pages = QSpinBox()
        self.n_pages.setRange(1, max(1, doc.page_count))
        self.n_pages.setValue(min(2, max(1, doc.page_count)))
        self.r_size = QRadioButton("File size:")
        self.size = QDoubleSpinBox()
        self.size.setRange(0.1, 2000)
        self.size.setValue(10)
        self.size.setSuffix(" MB")
        self.r_bm = QRadioButton("Top-level bookmarks")
        self.r_ranges = QRadioButton("Page ranges:")
        self.ranges = QLineEdit()
        self.ranges.setPlaceholderText("e.g. 1-3; 4-10; 11-")
        has_bm = any(e[0] == 1 for e in doc.toc())
        self.r_bm.setEnabled(has_bm)
        if not has_bm:
            self.r_bm.setToolTip("This document has no bookmarks.")
        self.r_pages.setChecked(True)
        g.addWidget(self.r_pages, 0, 0)
        g.addWidget(self.n_pages, 0, 1)
        g.addWidget(self.r_size, 1, 0)
        g.addWidget(self.size, 1, 1)
        g.addWidget(self.r_bm, 2, 0, 1, 2)
        g.addWidget(self.r_ranges, 3, 0)
        g.addWidget(self.ranges, 3, 1)
        self.lay.addWidget(box)
        out = QGroupBox("Output")
        f = QFormLayout(out)
        row = QHBoxLayout()
        self.folder = QLineEdit(os.path.dirname(doc.path))
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._browse)
        row.addWidget(self.folder, 1)
        row.addWidget(browse)
        f.addRow("Folder:", row)
        self.base = QLineEdit(os.path.splitext(doc.name)[0])
        f.addRow("File names start with:", self.base)
        self.lay.addWidget(out)
        self.preview = QLabel()
        self.preview.setEnabled(False)
        self.lay.addWidget(self.preview)
        for w in (self.r_pages, self.r_size, self.r_bm, self.r_ranges):
            w.toggled.connect(self._update)
        self.n_pages.valueChanged.connect(self._update)
        self.ranges.textEdited.connect(lambda _t: (self.r_ranges.setChecked(True), self._update()))
        self.finish_layout()
        self._update()

    def _browse(self):
        d = QFileDialog.getExistingDirectory(self, "Choose a folder", self.folder.text())
        if d:
            self.folder.setText(d)

    def mode(self):
        if self.r_size.isChecked():
            return "size", self.size.value()
        if self.r_bm.isChecked():
            return "bookmarks", None
        if self.r_ranges.isChecked():
            return "ranges", self.ranges.text()
        return "pages", self.n_pages.value()

    def _update(self):
        mode, value = self.mode()
        if mode == "size":
            self.preview.setText("The number of files depends on the page sizes.")
            return
        try:
            n = len(P.split_groups(self.doc, mode, value))
            self.preview.setText(f"This makes {n} file{'s' if n != 1 else ''}.")
        except ValueError as exc:
            self.preview.setText(str(exc))

    def validate(self):
        mode, value = self.mode()
        if not os.path.isdir(self.folder.text()):
            _error(self, "Split Document", "Choose a folder for the new files.")
            return False
        if not self.base.text().strip():
            _error(self, "Split Document", "Enter a start for the file names.")
            return False
        if mode != "size":
            try:
                P.split_groups(self.doc, mode, value)
            except ValueError as exc:
                _error(self, "Split Document", str(exc))
                return False
        return True


class CombineDialog(_Dialog):
    """A reorderable list of files to combine into one PDF."""

    def __init__(self, paths=(), parent=None, start_dir=""):
        super().__init__("Combine Files", parent, ok="Combine")
        self.resize(620, 460)
        self.start_dir = start_dir
        self.passwords: dict = {}
        row = QHBoxLayout()
        self.list = QListWidget()
        self.list.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.list.setAlternatingRowColors(True)
        row.addWidget(self.list, 1)
        col = QVBoxLayout()
        add = QPushButton("Add Files…")
        add.clicked.connect(self._add_dialog)
        self.up = QPushButton("Move Up")
        self.up.clicked.connect(lambda: self._move(-1))
        self.down = QPushButton("Move Down")
        self.down.clicked.connect(lambda: self._move(1))
        self.remove = QPushButton("Remove")
        self.remove.clicked.connect(self._remove)
        for b in (add, self.up, self.down, self.remove):
            col.addWidget(b)
        col.addStretch(1)
        row.addLayout(col)
        self.lay.addLayout(row)
        hint = QLabel("Drag files here, or drag them in the list to change the order. Images become pages.")
        hint.setEnabled(False)
        hint.setWordWrap(True)
        self.lay.addWidget(hint)
        self.bookmarks = QCheckBox("Add a bookmark for each file")
        self.bookmarks.setChecked(True)
        self.lay.addWidget(self.bookmarks)
        self.setAcceptDrops(True)
        self.finish_layout()
        self.add_paths(paths)

    def add_paths(self, paths):
        for p in paths:
            if not os.path.isfile(p):
                continue
            it = QListWidgetItem(icon("doc"), os.path.basename(p))
            it.setToolTip(p)
            it.setData(Qt.ItemDataRole.UserRole, os.path.abspath(p))
            self.list.addItem(it)

    def _add_dialog(self):
        paths, _ = QFileDialog.getOpenFileNames(self, "Add Files", self.start_dir, P.COMBINE_FILTER)
        self.add_paths(paths)

    def _move(self, d):
        rows = sorted({self.list.row(i) for i in self.list.selectedItems()}, reverse=d > 0)
        for r in rows:
            nr = r + d
            if 0 <= nr < self.list.count():
                it = self.list.takeItem(r)
                self.list.insertItem(nr, it)
                it.setSelected(True)

    def _remove(self):
        for it in self.list.selectedItems():
            self.list.takeItem(self.list.row(it))

    def paths(self) -> list[str]:
        return [self.list.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.list.count())]

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        self.add_paths([u.toLocalFile() for u in e.mimeData().urls() if u.isLocalFile()])

    def validate(self):
        if self.list.count() < 1:
            _error(self, "Combine Files", "Add the files to combine.")
            return False
        return True


class MarginEdit(QWidget):
    """Left / top / right / bottom with a unit choice; values in points."""

    changed = Signal()

    def __init__(self, values=(0, 0, 0, 0), unit="in", parent=None):
        super().__init__(parent)
        g = QGridLayout(self)
        g.setContentsMargins(0, 0, 0, 0)
        self.unit = QComboBox()
        for u in UNITS:
            self.unit.addItem(u, u)
        self.unit.setCurrentIndex(self.unit.findData(unit))
        self.spins = []
        for i, label in enumerate(("Left:", "Top:", "Right:", "Bottom:")):
            s = QDoubleSpinBox()
            s.setDecimals(2)
            s.setRange(0, 10000)
            s.valueChanged.connect(self.changed.emit)
            g.addWidget(QLabel(label), i // 2, (i % 2) * 2)
            g.addWidget(s, i // 2, (i % 2) * 2 + 1)
            self.spins.append(s)
        g.addWidget(QLabel("Units:"), 2, 0)
        g.addWidget(self.unit, 2, 1)
        self._unit = unit
        self.unit.currentIndexChanged.connect(self._unit_changed)
        self.set_points(values)

    def _unit_changed(self):
        pts = [s.value() * UNITS[self._unit] for s in self.spins]
        self._unit = self.unit.currentData()
        self.set_points(pts)

    def set_points(self, pts):
        f = UNITS[self.unit.currentData()]
        for s, v in zip(self.spins, pts):
            s.blockSignals(True)
            s.setValue(v / f)
            s.blockSignals(False)
        self.changed.emit()

    def points(self) -> tuple:
        f = UNITS[self.unit.currentData()]
        return tuple(s.value() * f for s in self.spins)


class CropDialog(_Dialog):
    def __init__(self, doc, current, selected=None, parent=None):
        super().__init__("Crop Pages", parent, ok="Crop")
        self.doc, self.current = doc, current
        box = QGroupBox("Margins to remove")
        bl = QVBoxLayout(box)
        self.margins = MarginEdit()
        bl.addWidget(self.margins)
        auto = QPushButton("Remove White Margins")
        auto.setToolTip("Find the empty border around the content of the current page")
        auto.clicked.connect(lambda: self.margins.set_points(P.white_margins(doc, current)))
        bl.addWidget(auto, 0, Qt.AlignmentFlag.AlignLeft)
        self.lay.addWidget(box)
        r = doc.page_rects[current]
        self.size_label = QLabel()
        self.size_label.setEnabled(False)
        self.lay.addWidget(self.size_label)
        self.margins.changed.connect(lambda: self._show_size(r))
        self.range = PageRangeBox(doc.page_count, current, selected)
        self.lay.addWidget(self.range)
        self.finish_layout()
        self._show_size(r)

    def _show_size(self, r):
        left, top, right, bottom = self.margins.points()
        w, h = r.width - left - right, r.height - top - bottom
        self.size_label.setText(f"Current page after cropping: {max(0, w) / 72:.2f} × {max(0, h) / 72:.2f} in")

    def validate(self):
        if self.range.pages() is None:
            _error(self, "Crop Pages", "Enter the pages to crop, like 1-3, 5.")
            return False
        left, top, right, bottom = self.margins.points()
        if not any((left, top, right, bottom)):
            _error(self, "Crop Pages", "Enter the margins to remove.")
            return False
        for p in self.range.pages():
            r = self.doc.page_rects[p]
            if r.width - left - right < 9 or r.height - top - bottom < 9:
                _error(self, "Crop Pages", f"The margins are too large for page {p + 1}.")
                return False
        return True


class PageSizeDialog(_Dialog):
    def __init__(self, doc, current, selected=None, parent=None):
        super().__init__("Change Page Size", parent)
        f = QFormLayout()
        self.size = QComboBox()
        for name, wh in P.PAPER_SIZES.items():
            self.size.addItem(f"{name} ({wh[0] / 72:.2f} × {wh[1] / 72:.2f} in)", wh)
        self.size.addItem("Custom", None)
        self.size.setCurrentIndex(self.size.findText("Letter", Qt.MatchFlag.MatchStartsWith))
        self.orient = QComboBox()
        self.orient.addItem("Portrait", "portrait")
        self.orient.addItem("Landscape", "landscape")
        r = doc.page_rects[current]
        self.w = QDoubleSpinBox()
        self.h = QDoubleSpinBox()
        for s, v in ((self.w, r.width), (self.h, r.height)):
            s.setRange(0.5, 200)
            s.setDecimals(2)
            s.setSuffix(" in")
            s.setValue(v / 72)
        f.addRow("Size:", self.size)
        f.addRow("Orientation:", self.orient)
        f.addRow("Width:", self.w)
        f.addRow("Height:", self.h)
        self.lay.addLayout(f)
        note = QLabel("The content stays centred and is not scaled.")
        note.setEnabled(False)
        self.lay.addWidget(note)
        self.range = PageRangeBox(doc.page_count, current, selected)
        self.lay.addWidget(self.range)
        self.size.currentIndexChanged.connect(self._sync)
        self.orient.currentIndexChanged.connect(self._sync)
        self.finish_layout()
        self._sync()

    def _sync(self):
        wh = self.size.currentData()
        custom = wh is None
        self.w.setEnabled(custom)
        self.h.setEnabled(custom)
        if not custom:
            w, h = wh
            if self.orient.currentData() == "landscape":
                w, h = h, w
            self.w.setValue(w / 72)
            self.h.setValue(h / 72)

    def page_size(self) -> tuple:
        return self.w.value() * 72, self.h.value() * 72

    def validate(self):
        if self.range.pages() is None:
            _error(self, "Change Page Size", "Enter the pages to change, like 1-3, 5.")
            return False
        return True


class ColorPick(QToolButton):
    def __init__(self, rgb=(0, 0, 0), parent=None):
        super().__init__(parent)
        self.rgb = tuple(rgb)
        self.setIcon(color_swatch(self.rgb, 18))
        self.setToolTip("Color")
        self.clicked.connect(self._pick)

    def _pick(self):
        c = QColorDialog.getColor(QColor.fromRgbF(*self.rgb), self, "Choose color")
        if c.isValid():
            self.rgb = (round(c.redF(), 3), round(c.greenF(), 3), round(c.blueF(), 3))
            self.setIcon(color_swatch(self.rgb, 18))


class _FontRow(QWidget):
    def __init__(self, size=10.0, rgb=(0, 0, 0), parent=None):
        super().__init__(parent)
        h = QHBoxLayout(self)
        h.setContentsMargins(0, 0, 0, 0)
        self.size = QDoubleSpinBox()
        self.size.setRange(4, 400)
        self.size.setValue(size)
        self.size.setSuffix(" pt")
        self.color = ColorPick(rgb)
        h.addWidget(self.size)
        h.addWidget(self.color)
        h.addStretch(1)


POSITIONS = [("Bottom center", "bottom-center"), ("Bottom right", "bottom-right"), ("Bottom left", "bottom-left"),
             ("Top center", "top-center"), ("Top right", "top-right"), ("Top left", "top-left")]


class HeaderFooterDialog(_Dialog):
    def __init__(self, doc, current, parent=None):
        super().__init__("Add Header & Footer", parent)
        self.doc = doc
        self.resize(640, 460)
        grid = QGridLayout()
        self.boxes = {}
        for col, title in enumerate(("Left", "Center", "Right")):
            grid.addWidget(QLabel(f"<b>{title}</b>"), 0, col + 1, Qt.AlignmentFlag.AlignHCenter)
        for row, part in enumerate(("Header", "Footer")):
            grid.addWidget(QLabel(f"{part}:"), row + 1, 0)
            for col in range(3):
                e = QLineEdit()
                e.installEventFilter(self)
                grid.addWidget(e, row + 1, col + 1)
                self.boxes[(part, col)] = e
        self._last = self.boxes[("Footer", 1)]
        self.lay.addLayout(grid)
        ins = QHBoxLayout()
        ins.addWidget(QLabel("Insert:"))
        for label, token in (("Page number", "<<page>>"), ("Number of pages", "<<pages>>"),
                             ("Date", "<<date>>"), ("File name", "<<file>>"), ("Title", "<<title>>")):
            b = QPushButton(label)
            b.setAutoDefault(False)
            b.clicked.connect(lambda _c=False, t=token: self._last.insert(t))
            ins.addWidget(b)
        ins.addStretch(1)
        self.lay.addLayout(ins)
        f = QFormLayout()
        self.font = _FontRow(10)
        f.addRow("Font size:", self.font)
        self.margins = MarginEdit((72, 36, 72, 36))
        f.addRow("Margins:", self.margins)
        self.start = QSpinBox()
        self.start.setRange(-99999, 999999)
        self.start.setValue(1)
        f.addRow("First page number:", self.start)
        self.lay.addLayout(f)
        self.range = PageRangeBox(doc.page_count, current)
        self.lay.addWidget(self.range)
        self.finish_layout()

    def eventFilter(self, obj, e):
        if e.type() == e.Type.FocusIn and isinstance(obj, QLineEdit):
            self._last = obj
        return super().eventFilter(obj, e)

    def spec(self) -> P.HeaderFooter:
        return P.HeaderFooter(
            header=tuple(self.boxes[("Header", c)].text() for c in range(3)),
            footer=tuple(self.boxes[("Footer", c)].text() for c in range(3)),
            style=P.TextStyle(self.font.size.value(), self.font.color.rgb),
            margins=self.margins.points(), start=self.start.value())

    def validate(self):
        if not any(b.text().strip() for b in self.boxes.values()):
            _error(self, "Add Header & Footer", "Type the text for at least one box.")
            return False
        if self.range.pages() is None:
            _error(self, "Add Header & Footer", "Enter the pages, like 1-3, 5.")
            return False
        return True


class PageNumbersDialog(_Dialog):
    FORMATS = [("1", "<<page>>"), ("Page 1", "Page <<page>>"), ("Page 1 of n", "Page <<page>> of <<pages>>"),
               ("1 / n", "<<page>> / <<pages>>"), ("- 1 -", "- <<page>> -")]

    def __init__(self, doc, current, parent=None):
        super().__init__("Add Page Numbers", parent)
        f = QFormLayout()
        self.position = QComboBox()
        for label, key in POSITIONS:
            self.position.addItem(label, key)
        self.fmt = QComboBox()
        for label, key in self.FORMATS:
            self.fmt.addItem(label, key)
        self.start = QSpinBox()
        self.start.setRange(-99999, 999999)
        self.start.setValue(1)
        self.font = _FontRow(10)
        f.addRow("Position:", self.position)
        f.addRow("Format:", self.fmt)
        f.addRow("Start at:", self.start)
        f.addRow("Font size:", self.font)
        self.lay.addLayout(f)
        self.range = PageRangeBox(doc.page_count, current)
        self.lay.addWidget(self.range)
        self.finish_layout()

    def validate(self):
        if self.range.pages() is None:
            _error(self, "Add Page Numbers", "Enter the pages, like 1-3, 5.")
            return False
        return True


class BatesDialog(_Dialog):
    def __init__(self, doc, current, next_number=1, parent=None):
        super().__init__("Add Bates Numbering", parent)
        f = QFormLayout()
        self.prefix = QLineEdit()
        self.suffix = QLineEdit()
        self.start = QSpinBox()
        self.start.setRange(0, 999999999)
        self.start.setValue(next_number)
        self.digits = QSpinBox()
        self.digits.setRange(1, 15)
        self.digits.setValue(6)
        self.position = QComboBox()
        for label, key in POSITIONS:
            self.position.addItem(label, key)
        self.position.setCurrentIndex(1)
        self.font = _FontRow(10)
        self.example = QLabel()
        self.example.setEnabled(False)
        f.addRow("Prefix:", self.prefix)
        f.addRow("Suffix:", self.suffix)
        f.addRow("Start number:", self.start)
        f.addRow("Number of digits:", self.digits)
        f.addRow("Position:", self.position)
        f.addRow("Font size:", self.font)
        f.addRow("Example:", self.example)
        self.lay.addLayout(f)
        self.range = PageRangeBox(doc.page_count, current, subset=False)
        self.lay.addWidget(self.range)
        for w in (self.prefix, self.suffix):
            w.textChanged.connect(self._example)
        self.start.valueChanged.connect(self._example)
        self.digits.valueChanged.connect(self._example)
        self.finish_layout()
        self._example()

    def _example(self):
        self.example.setText(f"{self.prefix.text()}{self.start.value():0{self.digits.value()}d}{self.suffix.text()}")

    def validate(self):
        if self.range.pages() is None:
            _error(self, "Add Bates Numbering", "Enter the pages, like 1-3, 5.")
            return False
        return True


class WatermarkDialog(_Dialog):
    def __init__(self, doc, current, parent=None):
        super().__init__("Add Watermark", parent)
        tabs = QTabWidget()
        tw = QWidget()
        tf = QFormLayout(tw)
        self.text = QPlainTextEdit("CONFIDENTIAL")
        self.text.setMaximumHeight(70)
        self.font = _FontRow(60, (0.8, 0.1, 0.1))
        tf.addRow("Text:", self.text)
        tf.addRow("Font size:", self.font)
        tabs.addTab(tw, "Text")
        iw = QWidget()
        il = QFormLayout(iw)
        row = QHBoxLayout()
        self.image = QLineEdit()
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._browse)
        row.addWidget(self.image, 1)
        row.addWidget(browse)
        il.addRow("Image:", row)
        self.scale = QSpinBox()
        self.scale.setRange(5, 100)
        self.scale.setValue(50)
        self.scale.setSuffix(" % of page width")
        il.addRow("Size:", self.scale)
        tabs.addTab(iw, "Image")
        self.tabs = tabs
        self.lay.addWidget(tabs)
        f = QFormLayout()
        self.angle = QSpinBox()
        self.angle.setRange(-180, 180)
        self.angle.setValue(45)
        self.angle.setSuffix("°")
        self.opacity = QSpinBox()
        self.opacity.setRange(5, 100)
        self.opacity.setValue(30)
        self.opacity.setSuffix(" %")
        self.behind = QCheckBox("Behind the page content")
        f.addRow("Rotation (text):", self.angle)
        f.addRow("Opacity:", self.opacity)
        f.addRow("", self.behind)
        self.lay.addLayout(f)
        self.range = PageRangeBox(doc.page_count, current)
        self.lay.addWidget(self.range)
        self.finish_layout()

    def _browse(self):
        p, _ = QFileDialog.getOpenFileName(self, "Choose an image", "", P.IMAGE_FILTER)
        if p:
            self.image.setText(p)

    def watermark(self) -> P.Watermark:
        style = P.TextStyle(self.font.size.value(), self.font.color.rgb, self.opacity.value() / 100)
        if self.tabs.currentIndex() == 0:
            return P.Watermark(text=self.text.toPlainText().strip(), style=style, angle=self.angle.value(),
                               behind=self.behind.isChecked())
        return P.Watermark(image=self.image.text(), style=style, scale=self.scale.value() / 100,
                           behind=self.behind.isChecked())

    def validate(self):
        if self.tabs.currentIndex() == 0 and not self.text.toPlainText().strip():
            _error(self, "Add Watermark", "Type the watermark text.")
            return False
        if self.tabs.currentIndex() == 1 and not os.path.isfile(self.image.text()):
            _error(self, "Add Watermark", "Choose an image file.")
            return False
        if self.range.pages() is None:
            _error(self, "Add Watermark", "Enter the pages, like 1-3, 5.")
            return False
        return True


class ReplaceDialog(_Dialog):
    """Replace pages of this document with pages of another file."""

    def __init__(self, page_count, selected, src_name, src_count, parent=None):
        super().__init__("Replace Pages", parent, ok="Replace")
        self.page_count, self.src_count = page_count, src_count
        f = QFormLayout()
        self.target = QLineEdit(P.format_ranges(selected) if selected else "1")
        self.target.setToolTip("Consecutive pages of this document, like 3-5")
        first = 1
        self.source = QLineEdit(f"{first}-{min(src_count, max(1, len(selected or [1])))}"
                                if src_count > 1 and len(selected or []) > 1 else "1")
        f.addRow("Replace pages:", self.target)
        f.addRow(f"With pages of “{src_name}” ({src_count}):", self.source)
        self.lay.addLayout(f)
        self.finish_layout()

    def ranges(self):
        t = P.parse_ranges(self.target.text(), self.page_count)
        s = P.parse_ranges(self.source.text(), self.src_count)
        return t, s

    def validate(self):
        t, s = self.ranges()
        if t is None or s is None:
            _error(self, "Replace Pages", "Enter page ranges like 3-5.")
            return False
        if t != list(range(t[0], t[0] + len(t))):
            _error(self, "Replace Pages", "The pages to replace must follow each other, like 3-5.")
            return False
        return True
