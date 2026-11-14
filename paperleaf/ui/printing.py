"""Print dialog (Acrobat-style options) and the print routine."""
from __future__ import annotations

import pymupdf
from PySide6.QtCore import QRectF, QSize, Qt
from PySide6.QtGui import QColor, QImage, QPageLayout, QPainter, QPixmap
from PySide6.QtPrintSupport import QPageSetupDialog, QPrinter, QPrinterInfo
from PySide6.QtWidgets import (
    QApplication, QButtonGroup, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout,
    QGroupBox, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QProgressDialog, QPushButton,
    QRadioButton, QSpinBox, QVBoxLayout, QWidget,
)

from paperleaf.core.document import PdfDocument
from paperleaf.core.pages import parse_ranges  # noqa: F401 (also used by tests)
from paperleaf.viewer.view import pix_to_qimage

MAX_PRINT_DPI = 300


class _Preview(QLabel):
    def __init__(self):
        super().__init__()
        self.setMinimumSize(QSize(230, 300))
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setStyleSheet("background: palette(mid); border-radius: 4px;")


class PrintDialog(QDialog):
    def __init__(self, doc: PdfDocument, current_page: int, parent=None, printer_name: str | None = None):
        super().__init__(parent)
        self.doc = doc
        self.current_page = current_page
        self.setWindowTitle("Print")
        self.printer = QPrinter(QPrinter.PrinterMode.HighResolution)
        if printer_name:
            self.printer.setPrinterName(printer_name)
        self._preview_index = 0

        root = QHBoxLayout(self)
        left = QVBoxLayout()
        root.addLayout(left, 3)

        # printer
        pbox = QGroupBox("Printer")
        pf = QFormLayout(pbox)
        self.printer_combo = QComboBox()
        names = QPrinterInfo.availablePrinterNames()
        default = QPrinterInfo.defaultPrinterName()
        self.printer_combo.addItems(names)
        sel = printer_name or default
        if sel in names:
            self.printer_combo.setCurrentText(sel)
        prow = QHBoxLayout()
        prow.addWidget(self.printer_combo, 1)
        setup = QPushButton("Page Setup…")
        setup.clicked.connect(self._page_setup)
        prow.addWidget(setup)
        pf.addRow("Name:", prow)
        self.copies = QSpinBox()
        self.copies.setRange(1, 999)
        self.collate = QCheckBox("Collate")
        self.collate.setChecked(True)
        crow = QHBoxLayout()
        crow.addWidget(self.copies)
        crow.addWidget(self.collate)
        crow.addStretch(1)
        pf.addRow("Copies:", crow)
        self.gray = QCheckBox("Print in grayscale (black and white)")
        pf.addRow("", self.gray)
        left.addWidget(pbox)

        # pages
        gbox = QGroupBox("Pages to Print")
        gl = QVBoxLayout(gbox)
        self.r_all = QRadioButton(f"All ({doc.page_count} pages)")
        self.r_cur = QRadioButton(f"Current page ({doc.page_label(current_page)})")
        self.r_range = QRadioButton("Pages")
        self.range_edit = QLineEdit(f"1-{doc.page_count}")
        self.range_edit.setPlaceholderText("e.g. 1-3, 5, 8")
        self.range_edit.textEdited.connect(lambda _t: self.r_range.setChecked(True))
        self.r_all.setChecked(True)
        grp = QButtonGroup(self)
        for b in (self.r_all, self.r_cur, self.r_range):
            grp.addButton(b)
            b.toggled.connect(self._refresh_preview)
        gl.addWidget(self.r_all)
        gl.addWidget(self.r_cur)
        rr = QHBoxLayout()
        rr.addWidget(self.r_range)
        rr.addWidget(self.range_edit, 1)
        gl.addLayout(rr)
        sub = QHBoxLayout()
        sub.addWidget(QLabel("Subset:"))
        self.subset = QComboBox()
        self.subset.addItems(["All pages in range", "Odd pages only", "Even pages only"])
        sub.addWidget(self.subset, 1)
        self.reverse = QCheckBox("Reverse pages")
        sub.addWidget(self.reverse)
        gl.addLayout(sub)
        left.addWidget(gbox)

        # sizing
        sbox = QGroupBox("Page Sizing && Handling")
        sl = QVBoxLayout(sbox)
        self.s_fit = QRadioButton("Fit to printable area")
        self.s_actual = QRadioButton("Actual size")
        self.s_shrink = QRadioButton("Shrink oversized pages")
        self.s_shrink.setChecked(True)
        for b in (self.s_fit, self.s_actual, self.s_shrink):
            sl.addWidget(b)
            b.toggled.connect(self._refresh_preview)
        self.auto_rotate = QCheckBox("Auto portrait/landscape")
        self.auto_rotate.setChecked(True)
        self.auto_rotate.toggled.connect(self._refresh_preview)
        sl.addWidget(self.auto_rotate)
        self.annots = QCheckBox("Print comments and form fields")
        self.annots.setChecked(True)
        sl.addWidget(self.annots)
        left.addWidget(sbox)
        left.addStretch(1)

        # preview
        right = QVBoxLayout()
        root.addLayout(right, 2)
        self.preview = _Preview()
        right.addWidget(self.preview, 1)
        nav = QHBoxLayout()
        self.prev_btn = QPushButton("<")
        self.next_btn = QPushButton(">")
        self.prev_lbl = QLabel()
        self.prev_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.prev_btn.clicked.connect(lambda: self._step_preview(-1))
        self.next_btn.clicked.connect(lambda: self._step_preview(1))
        nav.addWidget(self.prev_btn)
        nav.addWidget(self.prev_lbl, 1)
        nav.addWidget(self.next_btn)
        right.addLayout(nav)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        self.print_btn = bb.addButton("Print", QDialogButtonBox.ButtonRole.AcceptRole)
        self.print_btn.setDefault(True)
        bb.accepted.connect(self._accept)
        bb.rejected.connect(self.reject)
        right.addWidget(bb)
        self.range_edit.editingFinished.connect(self._refresh_preview)
        self.subset.currentIndexChanged.connect(self._refresh_preview)
        self.reverse.toggled.connect(self._refresh_preview)
        self.gray.toggled.connect(self._refresh_preview)
        self.printer_combo.currentTextChanged.connect(self._printer_changed)
        self._printer_changed(self.printer_combo.currentText())
        if not names:
            self.print_btn.setEnabled(False)
            self.prev_lbl.setText("No printers are installed.")

    # ----------------------------------------------------------------- options
    def pages(self) -> list[int] | None:
        n = self.doc.page_count
        if self.r_all.isChecked():
            pages = list(range(n))
        elif self.r_cur.isChecked():
            pages = [self.current_page]
        else:
            pages = parse_ranges(self.range_edit.text(), n)
            if pages is None:
                return None
        if self.subset.currentIndex() == 1:
            pages = [p for p in pages if p % 2 == 0]
        elif self.subset.currentIndex() == 2:
            pages = [p for p in pages if p % 2 == 1]
        if self.reverse.isChecked():
            pages.reverse()
        return pages

    def options(self) -> dict:
        sizing = "fit" if self.s_fit.isChecked() else "actual" if self.s_actual.isChecked() else "shrink"
        return {"sizing": sizing, "auto_rotate": self.auto_rotate.isChecked(),
                "gray": self.gray.isChecked(), "annots": self.annots.isChecked()}

    def _printer_changed(self, name: str):
        if name:
            self.printer.setPrinterName(name)
        self._refresh_preview()

    def _page_setup(self):
        dlg = QPageSetupDialog(self.printer, self)
        dlg.exec()
        self._refresh_preview()

    def _step_preview(self, d: int):
        self._preview_index += d
        self._refresh_preview()

    def _refresh_preview(self, *_):
        pages = self.pages()
        if not pages:
            self.preview.setText("Invalid page range")
            self.preview.setPixmap(QPixmap())
            return
        self._preview_index = max(0, min(len(pages) - 1, self._preview_index))
        pno = pages[self._preview_index]
        self.prev_lbl.setText(f"Sheet {self._preview_index + 1} of {len(pages)}")
        self.prev_btn.setEnabled(self._preview_index > 0)
        self.next_btn.setEnabled(self._preview_index < len(pages) - 1)
        layout = self.printer.pageLayout()
        paper = layout.fullRectPoints()
        paint = layout.paintRectPoints()
        box = self.preview.size() - QSize(20, 20)
        img = render_sheet(self.doc, pno, paper.width(), paper.height(),
                           QRectF(paint), box, self.options(), preview=True)
        self.preview.setPixmap(QPixmap.fromImage(img))

    def _accept(self):
        if self.pages() is None:
            QMessageBox.warning(self, "Print", "Please enter a valid page range, e.g. 1-3, 5.")
            return
        self.printer.setCopyCount(self.copies.value())
        self.printer.setCollateCopies(self.collate.isChecked())
        self.printer.setColorMode(QPrinter.ColorMode.GrayScale if self.gray.isChecked()
                                  else QPrinter.ColorMode.Color)
        self.accept()


def _page_transform(doc, pno, paper_w, paper_h, paint: QRectF, opts):
    """Return (rotate, scale, x, y) placing the page on the sheet, in points."""
    r = doc.page_rects[pno]
    pw, ph = r.width, r.height
    rotate = 0
    if opts.get("auto_rotate") and (pw > ph) != (paper_w > paper_h) and abs(pw - ph) > 1:
        rotate = 90
        pw, ph = ph, pw
    sizing = opts.get("sizing", "shrink")
    fit = min(paint.width() / pw, paint.height() / ph)
    if sizing == "fit":
        scale = fit
    elif sizing == "actual":
        scale = 1.0
    else:
        scale = min(1.0, fit)
    x = paint.left() + (paint.width() - pw * scale) / 2
    y = paint.top() + (paint.height() - ph * scale) / 2
    return rotate, scale, x, y, pw, ph


def render_sheet(doc, pno, paper_w, paper_h, paint: QRectF, box: QSize, opts, preview=False) -> QImage:
    """Small image of how a page sits on the paper (for the preview)."""
    s = min(box.width() / paper_w, box.height() / paper_h)
    img = QImage(max(1, int(paper_w * s)), max(1, int(paper_h * s)), QImage.Format.Format_RGB32)
    img.fill(QColor("white"))
    rotate, scale, x, y, pw, ph = _page_transform(doc, pno, paper_w, paper_h, paint, opts)
    z = scale * s
    pix = doc.render(pno, pymupdf.Matrix(z, z).prerotate(rotate), annots=opts.get("annots", True),
                     gray=opts.get("gray", False))
    p = QPainter(img)
    p.drawImage(QRectF(x * s, y * s, pw * z, ph * z), pix_to_qimage(pix))
    p.setPen(QColor(0, 0, 0, 80))
    p.drawRect(0, 0, img.width() - 1, img.height() - 1)
    p.end()
    return img


def print_document(doc: PdfDocument, printer: QPrinter, pages: list[int], opts: dict, parent=None) -> bool:
    if not doc.can_print():
        QMessageBox.warning(parent, "Print", "This document does not allow printing.")
        return False
    printer.setDocName(doc.name)
    printer.setFullPage(True)
    layout = printer.pageLayout()
    layout.setMode(QPageLayout.Mode.FullPageMode)
    printer.setPageLayout(layout)
    paper = layout.fullRectPoints()
    paint = QRectF(layout.paintRectPoints())
    res = printer.resolution()
    dpi = min(res, MAX_PRINT_DPI)
    painter = QPainter()
    if not painter.begin(printer):
        QMessageBox.warning(parent, "Print", "Could not start printing on this printer.")
        return False
    progress = QProgressDialog("Printing…", "Cancel", 0, len(pages), parent)
    progress.setWindowModality(Qt.WindowModality.WindowModal)
    progress.setMinimumDuration(400)
    ok = True
    try:
        # painter units are device pixels; convert points -> pixels
        k = res / 72.0
        for i, pno in enumerate(pages):
            if progress.wasCanceled():
                ok = False
                break
            progress.setValue(i)
            QApplication.processEvents()
            if i > 0:
                printer.newPage()
            rotate, scale, x, y, pw, ph = _page_transform(doc, pno, paper.width(), paper.height(), paint, opts)
            z = scale * dpi / 72.0
            pix = doc.render(pno, pymupdf.Matrix(z, z).prerotate(rotate),
                             annots=opts.get("annots", True), gray=opts.get("gray", False))
            target = QRectF(x * k, y * k, pw * scale * k, ph * scale * k)
            painter.drawImage(target, pix_to_qimage(pix))
        progress.setValue(len(pages))
    finally:
        painter.end()
    return ok


def run_print(doc: PdfDocument, current_page: int, parent=None, printer_name: str | None = None,
              silent: bool = False) -> bool:
    """Show the print dialog (or print silently with defaults) and print."""
    if not doc.can_print():
        QMessageBox.warning(parent, "Print", "This document does not allow printing.")
        return False
    dlg = PrintDialog(doc, current_page, parent, printer_name)
    if silent:
        dlg._accept()
        return print_document(doc, dlg.printer, dlg.pages() or [], dlg.options(), parent)
    if dlg.exec() != QDialog.DialogCode.Accepted:
        return False
    return print_document(doc, dlg.printer, dlg.pages(), dlg.options(), parent)
