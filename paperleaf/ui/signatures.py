"""Create a signature by drawing, typing or importing an image."""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import (
    QButtonGroup, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QHBoxLayout, QLabel, QLineEdit,
    QMessageBox, QPushButton, QRadioButton, QTabWidget, QVBoxLayout, QWidget,
)

from paperleaf.ui import stamps

INK_COLORS = {"Black": QColor(20, 20, 20), "Blue": QColor(20, 50, 160)}


class DrawCanvas(QWidget):
    def __init__(self):
        super().__init__()
        self.setMinimumSize(QSize(520, 200))
        self.paths: list[QPainterPath] = []
        self.color = INK_COLORS["Black"]
        self._last = None
        self._mid = None
        self.setCursor(Qt.CursorShape.CrossCursor)

    def clear(self):
        self.paths = []
        self.update()

    def is_empty(self) -> bool:
        return not self.paths

    def mousePressEvent(self, e):
        p = e.position()
        path = QPainterPath(p)
        path.lineTo(p + QPointF(0.1, 0.1))
        self.paths.append(path)
        self._last = p
        self._mid = p
        self.update()

    def mouseMoveEvent(self, e):
        if self._last is None:
            return
        p = e.position()
        mid = (self._last + p) / 2
        self.paths[-1].quadTo(self._last, mid)  # smooth curve through the midpoints
        self._last = p
        self.update()

    def mouseReleaseEvent(self, e):
        if self._last is not None:
            self.paths[-1].lineTo(e.position())
        self._last = None
        self.update()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(self.rect(), QColor("white"))
        p.setPen(QPen(QColor(0, 0, 0, 60), 1, Qt.PenStyle.DashLine))
        y = self.height() * 0.75
        p.drawLine(QPointF(20, y), QPointF(self.width() - 20, y))
        if not self.paths:
            p.setPen(QColor(0, 0, 0, 90))
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "Sign here with your mouse, pen or finger")
        p.setPen(self._pen(1.0))
        for path in self.paths:
            p.drawPath(path)

    def _pen(self, scale):
        pen = QPen(self.color, 2.6 * scale)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        return pen

    def image(self) -> QImage:
        scale = 3
        img = QImage(self.width() * scale, self.height() * scale, QImage.Format.Format_ARGB32_Premultiplied)
        img.fill(Qt.GlobalColor.transparent)
        p = QPainter(img)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.scale(scale, scale)
        p.setPen(self._pen(1.0))
        for path in self.paths:
            p.drawPath(path)
        p.end()
        return stamps.crop_to_content(img, margin=8)


class SignatureDialog(QDialog):
    def __init__(self, name: str = "", parent=None):
        super().__init__(parent)
        self.setWindowTitle("Add Signature")
        self.result_path = None
        self._image_sig = None
        lay = QVBoxLayout(self)
        self.tabs = QTabWidget()
        lay.addWidget(self.tabs)

        # draw
        draw = QWidget()
        dl = QVBoxLayout(draw)
        self.canvas = DrawCanvas()
        dl.addWidget(self.canvas)
        row = QHBoxLayout()
        self.ink_group = QButtonGroup(self)
        for i, label in enumerate(INK_COLORS):
            rb = QRadioButton(label)
            rb.setChecked(i == 0)
            rb.toggled.connect(lambda on, lb=label: on and self._set_ink(lb))
            self.ink_group.addButton(rb)
            row.addWidget(rb)
        row.addStretch(1)
        clear = QPushButton("Clear")
        clear.clicked.connect(self.canvas.clear)
        row.addWidget(clear)
        dl.addLayout(row)
        self.tabs.addTab(draw, "Draw")

        # type
        typ = QWidget()
        tl = QVBoxLayout(typ)
        self.name_edit = QLineEdit(name)
        self.name_edit.setPlaceholderText("Type your name")
        tl.addWidget(self.name_edit)
        frow = QHBoxLayout()
        frow.addWidget(QLabel("Style:"))
        self.font_combo = QComboBox()
        for fam in stamps.available_script_fonts():
            self.font_combo.addItem(fam, fam)
            self.font_combo.setItemData(self.font_combo.count() - 1, QFont(fam, 14), Qt.ItemDataRole.FontRole)
        frow.addWidget(self.font_combo, 1)
        self.type_ink = QComboBox()
        self.type_ink.addItems(list(INK_COLORS))
        frow.addWidget(self.type_ink)
        tl.addLayout(frow)
        self.type_preview = QLabel()
        self.type_preview.setMinimumHeight(140)
        self.type_preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.type_preview.setStyleSheet("background: white; border: 1px solid #ccc;")
        tl.addWidget(self.type_preview, 1)
        self.name_edit.textChanged.connect(self._update_typed)
        self.font_combo.currentIndexChanged.connect(self._update_typed)
        self.type_ink.currentIndexChanged.connect(self._update_typed)
        self.tabs.addTab(typ, "Type")

        # image
        imgw = QWidget()
        il = QVBoxLayout(imgw)
        pick = QPushButton("Choose an image of your signature…")
        pick.clicked.connect(self._pick_image)
        il.addWidget(pick)
        hint = QLabel("A photo or scan works: the white paper background is removed automatically.")
        hint.setWordWrap(True)
        hint.setEnabled(False)
        il.addWidget(hint)
        self.image_preview = QLabel()
        self.image_preview.setMinimumHeight(140)
        self.image_preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.image_preview.setStyleSheet("background: white; border: 1px solid #ccc;")
        il.addWidget(self.image_preview, 1)
        self.tabs.addTab(imgw, "Image")

        note = QLabel("Your signature is saved on this computer only (in %APPDATA%\\Paperleaf\\signatures) "
                      "so you can reuse it. It is placed as an image; this is not a certificate-based "
                      "digital signature.")
        note.setWordWrap(True)
        note.setEnabled(False)
        lay.addWidget(note)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        save = bb.addButton("Save and Use", QDialogButtonBox.ButtonRole.AcceptRole)
        save.setDefault(True)
        bb.accepted.connect(self._accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        self._update_typed()

    def _set_ink(self, label):
        self.canvas.color = INK_COLORS[label]
        self.canvas.update()

    def _typed_image(self):
        name = self.name_edit.text().strip()
        if not name:
            return None
        return stamps.typed_signature(name, self.font_combo.currentData() or "Segoe Script",
                                      INK_COLORS[self.type_ink.currentText()])

    def _update_typed(self):
        img = self._typed_image()
        if img is None:
            self.type_preview.setPixmap(QPixmap())
            self.type_preview.setText("Type your name above")
            return
        pm = QPixmap.fromImage(img).scaled(self.type_preview.width() - 20, 120, Qt.AspectRatioMode.KeepAspectRatio,
                                           Qt.TransformationMode.SmoothTransformation)
        self.type_preview.setPixmap(pm)

    def _pick_image(self):
        path, _ = QFileDialog.getOpenFileName(self, "Choose signature image", "",
                                              "Images (*.png *.jpg *.jpeg *.bmp *.gif *.tif *.tiff)")
        if not path:
            return
        img = QImage(path)
        if img.isNull():
            QMessageBox.warning(self, "Signature", "That image could not be read.")
            return
        if img.width() > 900:
            img = img.scaledToWidth(900, Qt.TransformationMode.SmoothTransformation)
        if not img.hasAlphaChannel():
            img = stamps.remove_white(img)
        self._image_sig = stamps.crop_to_content(img)
        pm = QPixmap.fromImage(self._image_sig).scaled(self.image_preview.width() - 20, 140,
                                                        Qt.AspectRatioMode.KeepAspectRatio,
                                                        Qt.TransformationMode.SmoothTransformation)
        self.image_preview.setPixmap(pm)

    def _accept(self):
        tab = self.tabs.currentIndex()
        if tab == 0:
            if self.canvas.is_empty():
                QMessageBox.information(self, "Signature", "Draw your signature first.")
                return
            img = self.canvas.image()
        elif tab == 1:
            img = self._typed_image()
            if img is None:
                QMessageBox.information(self, "Signature", "Type your name first.")
                return
        else:
            img = self._image_sig
            if img is None:
                QMessageBox.information(self, "Signature", "Choose an image first.")
                return
        self.result_path = stamps.save_image(img, stamps.signatures_dir(), "sig")
        self.accept()
