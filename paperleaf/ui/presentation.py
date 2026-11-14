"""Full-screen presentation mode (Acrobat's Ctrl+L)."""
from __future__ import annotations

import pymupdf
from PySide6.QtCore import QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QGuiApplication, QPainter
from PySide6.QtWidgets import QWidget

from paperleaf.core.document import PdfDocument
from paperleaf.viewer.view import pix_to_pixmap


class PresentationWindow(QWidget):
    closed = Signal(int)  # page the user ended on

    def __init__(self, doc: PdfDocument, page: int, night: bool = False, screen=None):
        super().__init__(None, Qt.WindowType.Window | Qt.WindowType.FramelessWindowHint)
        self.doc = doc
        self.page = page
        self.night = night
        self._cache: dict = {}
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setCursor(Qt.CursorShape.BlankCursor)
        self.setMouseTracking(True)
        self._hide_cursor = QTimer(self)
        self._hide_cursor.setSingleShot(True)
        self._hide_cursor.setInterval(1500)
        self._hide_cursor.timeout.connect(lambda: self.setCursor(Qt.CursorShape.BlankCursor))
        if screen is not None:
            self.setScreen(screen)
            self.setGeometry(screen.geometry())

    def start(self):
        self.showFullScreen()
        self.activateWindow()
        self.setFocus()

    def _pixmap(self, pno: int):
        dpr = self.devicePixelRatioF()
        key = (pno, self.width(), self.height(), dpr)
        if key not in self._cache:
            r = self.doc.page_rects[pno]
            s = min(self.width() / r.width, self.height() / r.height) * dpr
            pm = pix_to_pixmap(self.doc.render(pno, pymupdf.Matrix(s, s), night=self.night))
            pm.setDevicePixelRatio(dpr)
            if len(self._cache) > 6:
                self._cache.pop(next(iter(self._cache)))
            self._cache[key] = pm
        return self._cache[key]

    def paintEvent(self, _e):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor("black"))
        pm = self._pixmap(self.page)
        w = pm.width() / pm.devicePixelRatio()
        h = pm.height() / pm.devicePixelRatio()
        p.drawPixmap(QRectF((self.width() - w) / 2, (self.height() - h) / 2, w, h), pm, QRectF(pm.rect()))
        p.end()
        # pre-render the next page
        if self.page + 1 < self.doc.page_count:
            QTimer.singleShot(0, lambda: self._pixmap(min(self.page + 1, self.doc.page_count - 1)))

    def go(self, pno: int):
        pno = max(0, min(self.doc.page_count - 1, pno))
        if pno != self.page:
            self.page = pno
            self.update()

    def keyPressEvent(self, e):
        k = e.key()
        if k in (Qt.Key.Key_Escape, Qt.Key.Key_L) and (k == Qt.Key.Key_Escape or
                                                        e.modifiers() & Qt.KeyboardModifier.ControlModifier):
            self.close()
        elif k in (Qt.Key.Key_Right, Qt.Key.Key_Down, Qt.Key.Key_PageDown, Qt.Key.Key_Space,
                   Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_N):
            self.go(self.page + 1)
        elif k in (Qt.Key.Key_Left, Qt.Key.Key_Up, Qt.Key.Key_PageUp, Qt.Key.Key_Backspace, Qt.Key.Key_P):
            self.go(self.page - 1)
        elif k == Qt.Key.Key_Home:
            self.go(0)
        elif k == Qt.Key.Key_End:
            self.go(self.doc.page_count - 1)
        else:
            super().keyPressEvent(e)

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self.go(self.page + 1)
        elif e.button() == Qt.MouseButton.RightButton:
            self.go(self.page - 1)

    def mouseMoveEvent(self, e):
        self.setCursor(Qt.CursorShape.ArrowCursor)
        self._hide_cursor.start()

    def wheelEvent(self, e):
        self.go(self.page + (1 if e.angleDelta().y() < 0 else -1))

    def closeEvent(self, e):
        self.closed.emit(self.page)
        super().closeEvent(e)


def screen_for(widget):
    return widget.screen() if widget is not None else QGuiApplication.primaryScreen()
