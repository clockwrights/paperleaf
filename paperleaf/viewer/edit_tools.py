"""Edit PDF in DocumentView: click a line of text to change it, select an
image to move, resize, replace or delete it, add new text and images."""
from __future__ import annotations

import pymupdf
from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal, QObject
from PySide6.QtGui import QBrush, QColor, QFont, QPen
from PySide6.QtWidgets import QApplication, QLineEdit, QPlainTextEdit

from paperleaf.core import edit as E

LINE_COLOR = QColor(0, 120, 215, 90)
HOVER_COLOR = QColor(0, 120, 215, 200)
IMAGE_COLOR = QColor(0, 120, 215, 170)
HANDLE_PX = 4.5
RESIZE_CURSORS = [Qt.CursorShape.SizeFDiagCursor, Qt.CursorShape.SizeVerCursor, Qt.CursorShape.SizeBDiagCursor,
                  Qt.CursorShape.SizeHorCursor, Qt.CursorShape.SizeFDiagCursor, Qt.CursorShape.SizeVerCursor,
                  Qt.CursorShape.SizeBDiagCursor, Qt.CursorShape.SizeHorCursor]

ADD_TEXT, ADD_IMAGE = "add_text", "add_image"


def _font_family(line: E.TextLine) -> str:
    name = line.font.lower()
    if line.flags & E.MONO or any(k in name for k in ("courier", "mono", "consol")):
        return "Courier New"
    if line.flags & E.SERIF or any(k in name for k in ("times", "serif", "roman", "georgia")):
        return "Times New Roman"
    return "Arial"


class _Edit:
    def _setup(self, ctl, target):
        self._ctl = ctl
        self.target = target       # TextLine, or (pno, point, size) for new text
        self._done = False

    def finish(self, commit=True):
        if not self._done:
            self._done = True
            self._ctl._editor_done(self, commit)

    def _lost(self, e):
        if e.reason() in (Qt.FocusReason.PopupFocusReason, Qt.FocusReason.ActiveWindowFocusReason):
            return
        QTimer.singleShot(0, lambda: None if self._done else self.finish(True))


class LineEdit(QLineEdit, _Edit):
    def __init__(self, parent, ctl, target):
        super().__init__(parent)
        self._setup(ctl, target)
        self.setFrame(False)

    def value(self):
        return self.text()

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.finish(True)
            return
        if e.key() == Qt.Key.Key_Escape:
            self.finish(False)
            return
        super().keyPressEvent(e)

    def focusOutEvent(self, e):
        super().focusOutEvent(e)
        self._lost(e)


class BlockEdit(QPlainTextEdit, _Edit):
    """New text: Enter starts a new line, Ctrl+Enter or clicking elsewhere finishes."""

    def __init__(self, parent, ctl, target):
        super().__init__(parent)
        self._setup(ctl, target)
        self.setFrameShape(QPlainTextEdit.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.document().setDocumentMargin(1)
        self.textChanged.connect(self._grow)

    def value(self):
        return self.toPlainText()

    def _grow(self):
        fm = self.fontMetrics()
        lines = self.toPlainText().split("\n")
        w = max([fm.horizontalAdvance(t) for t in lines] + [fm.horizontalAdvance("M") * 8]) + 12
        h = fm.lineSpacing() * max(1, len(lines)) + 6
        self.resize(max(self.width(), w), max(self.height(), h))

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and e.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self.finish(True)
            return
        if e.key() == Qt.Key.Key_Escape:
            self.finish(False)
            return
        super().keyPressEvent(e)

    def focusOutEvent(self, e):
        super().focusOutEvent(e)
        self._lost(e)


class EditController(QObject):
    selectionChanged = Signal(object)   # ImageBox | None
    modeChanged = Signal(str)            # "", ADD_TEXT, ADD_IMAGE

    def __init__(self, view):
        super().__init__(view)
        self.view = view
        self.doc = view.doc
        self.active = False
        self.mode = ""
        self.image_path = ""      # for ADD_IMAGE
        self.text_size = 12.0
        self.text_color = (0, 0, 0)
        self.editor = None
        self.sel: E.ImageBox | None = None
        self.hover = None         # TextLine under the mouse
        self.drag = None
        self._expect = None      # (pno, rect): select the image found there after the next change
        self.doc.listeners.append(self._doc_changed)

    # ------------------------------------------------------------- helpers
    def _scale(self) -> float:
        from paperleaf.viewer.view import PT
        return self.view.zoom * PT

    def _rot(self, pno):
        return self.doc.rotation_matrix(pno)

    def _page_at(self, pos: QPointF):
        return self.view._page_at(pos + self.view._offset())

    def _to_page(self, pno: int, pos: QPointF) -> pymupdf.Point:
        """Viewport position -> unrotated page coordinates."""
        return self.view.content_to_page(pno, pos + self.view._offset()) * ~self._rot(pno)

    def _vrect(self, pno, rect) -> QRectF:
        return self.view.text_rect_to_view(pno, rect)

    def _msg(self, text):
        self.view.message.emit(text)

    def _allowed(self) -> bool:
        return self.doc.is_pdf and self.doc.can_modify()

    def _working(self) -> bool:
        return self.active and self.view.effective_tool() == "select"

    def line_at(self, pos: QPointF):
        pno = self._page_at(pos)
        if pno is None:
            return None
        p = self._to_page(pno, pos)
        return E.line_at(self.doc, pno, p.x, p.y)

    def image_at(self, pos: QPointF):
        pno = self._page_at(pos)
        if pno is None:
            return None
        p = self._to_page(pno, pos)
        return E.image_at(self.doc, pno, p.x, p.y)

    def _doc_changed(self, _pages):
        self.hover = None
        want, self._expect = self._expect, None
        if want is None and self.sel is not None:
            want = (self.sel.pno, self.sel.rect)
        if want is None:
            return
        # find the image again: an edit puts it in a new object
        pno, rect = want
        imgs = E.images(self.doc, pno) if pno < self.doc.page_count else []
        near = [i for i in imgs if all(abs(a - b) < 1.5 for a, b in zip(i.rect, rect))]
        self.sel = None
        self._select(near[0] if near else None)

    # ---------------------------------------------------------- mode
    def set_active(self, on: bool):
        if on == self.active:
            return
        self.commit()
        self.active = on
        self.set_mode("")
        self._select(None)
        self.hover = None
        self.view.viewport().update()

    def set_mode(self, mode: str, path: str = ""):
        self.mode = mode
        self.image_path = path if mode == ADD_IMAGE else ""
        self.modeChanged.emit(mode)
        self.view._update_cursor(self.view._last_mouse)

    def _select(self, img):
        if img is self.sel:
            return
        self.sel = img
        self.selectionChanged.emit(img)
        self.view.viewport().update()

    def cancel(self):
        self.commit()
        self.drag = None

    def commit(self):
        if self.editor is not None:
            self.editor.finish(True)

    # ----------------------------------------------------------- editors
    def _open_line(self, line: E.TextLine, click: QPointF | None = None):
        if not line.editable:
            self._msg("Only straight, left-to-right text can be edited.")
            return
        self.commit()
        ed = LineEdit(self.view.viewport(), self, line)
        font = QFont(_font_family(line))
        font.setBold(line.bold)
        font.setItalic(line.italic)
        font.setPixelSize(max(6, round(line.size * self._scale())))
        ed.setFont(font)
        c = QColor.fromRgbF(*line.color)
        ed.setStyleSheet(f"background: rgba(255,255,255,250); color: {c.name()}; border: 1px solid #0078d7;")
        ed.setText(line.text)
        self.editor = ed
        self._place(ed)
        ed.show()
        ed.setFocus(Qt.FocusReason.OtherFocusReason)
        if click is not None:
            ed.setCursorPosition(ed.cursorPositionAt(ed.mapFrom(self.view.viewport(), click.toPoint())))
        else:
            ed.selectAll()
        self.view.viewport().update()

    def _open_new(self, pno: int, pos: QPointF):
        self.commit()
        p = self._to_page(pno, pos)
        size = self.text_size
        target = (pno, pymupdf.Point(p.x, p.y + size * 0.8), size)
        ed = BlockEdit(self.view.viewport(), self, target)
        font = QFont("Arial")
        font.setPixelSize(max(6, round(size * self._scale())))
        ed.setFont(font)
        c = QColor.fromRgbF(*self.text_color)
        ed.setStyleSheet(f"background: rgba(255,255,255,235); color: {c.name()}; border: 1px dashed #0078d7;")
        self.editor = ed
        self._place(ed)
        ed.show()
        ed.setFocus(Qt.FocusReason.OtherFocusReason)

    def _place(self, ed):
        t = ed.target
        if isinstance(t, E.TextLine):
            r = self._vrect(t.pno, t.rect)
            fm = ed.fontMetrics()
            w = max(r.width(), fm.horizontalAdvance(ed.text()) + 24, 60)
            h = max(r.height(), fm.height() + 4)
            ed.setGeometry(QRectF(r.left() - 3, r.center().y() - h / 2, w + 6, h).toAlignedRect())
        else:
            pno, point, size = t
            top_left = self.view.text_rect_to_view(pno, pymupdf.Rect(point.x, point.y - size * 0.8,
                                                                     point.x + 1, point.y)).topLeft()
            fm = ed.fontMetrics()
            w = max(ed.width() if ed.isVisible() else 0, fm.horizontalAdvance("M") * 8 + 12)
            h = max(ed.height() if ed.isVisible() else 0, fm.lineSpacing() + 6)
            ed.setGeometry(int(top_left.x()) - 2, int(top_left.y()) - 2, int(w), int(h))

    def _editor_done(self, ed, commit: bool):
        if ed is not self.editor:
            return
        value = ed.value() if commit else None
        self.editor = None
        fw = QApplication.focusWidget()
        had_focus = fw is None or fw is ed or ed.isAncestorOf(fw)
        ed.hide()
        ed.deleteLater()
        if had_focus:
            self.view.setFocus(Qt.FocusReason.OtherFocusReason)
        if not commit:
            self.view.viewport().update()
            return
        t = ed.target
        try:
            if isinstance(t, E.TextLine):
                E.replace_line(self.doc, t, value)
            else:
                pno, point, size = t
                E.add_text(self.doc, pno, point, value, size=size, color=self.text_color)
                if value.strip():
                    self.set_mode("")
        except Exception as exc:
            self._msg(f"Could not change the text: {exc}")
        self.view.viewport().update()

    def view_changed(self):
        ed = self.editor
        if ed is None:
            return
        t = ed.target
        pno = t.pno if isinstance(t, E.TextLine) else t[0]
        if pno >= len(self.view._rects) or self.view._rects[pno].isNull():
            ed.finish(True)
            return
        size = t.size if isinstance(t, E.TextLine) else t[2]
        f = ed.font()
        f.setPixelSize(max(6, round(size * self._scale())))
        ed.setFont(f)
        self._place(ed)

    # ------------------------------------------------------------ handles
    def _handles(self, img) -> list[QPointF]:
        r = self._vrect(img.pno, img.rect)
        cx, cy = r.center().x(), r.center().y()
        return [QPointF(r.left(), r.top()), QPointF(cx, r.top()), QPointF(r.right(), r.top()),
                QPointF(r.right(), cy), QPointF(r.right(), r.bottom()), QPointF(cx, r.bottom()),
                QPointF(r.left(), r.bottom()), QPointF(r.left(), cy)]

    def _handle_at(self, pos):
        if self.sel is None:
            return None
        for i, hp in enumerate(self._handles(self.sel)):
            if abs(pos.x() - hp.x()) <= HANDLE_PX + 2 and abs(pos.y() - hp.y()) <= HANDLE_PX + 2:
                return i
        return None

    def _drag_rect(self, d) -> QRectF:
        r = QRectF(d["vrect"])
        dx = d["cur"].x() - d["start"].x()
        dy = d["cur"].y() - d["start"].y()
        if d["mode"] == "move":
            return r.translated(dx, dy)
        h = d["handle"]
        left, top, right, bottom = r.left(), r.top(), r.right(), r.bottom()
        if h in (0, 6, 7):
            left += dx
        if h in (2, 3, 4):
            right += dx
        if h in (0, 1, 2):
            top += dy
        if h in (4, 5, 6):
            bottom += dy
        if h in (0, 2, 4, 6) and r.width() > 0 and r.height() > 0:
            # corners keep the proportions (hold Shift to stretch)
            if not QApplication.keyboardModifiers() & Qt.KeyboardModifier.ShiftModifier:
                ratio = r.width() / r.height()
                w = abs(right - left)
                hh = w / ratio
                if h in (0, 2):
                    top = bottom - hh
                else:
                    bottom = top + hh
        nr = QRectF(QPointF(left, top), QPointF(right, bottom)).normalized()
        nr.setWidth(max(nr.width(), 8))
        nr.setHeight(max(nr.height(), 8))
        return nr

    def _finish_drag(self, d):
        img = d["image"]
        vr = self._drag_rect(d)
        a = self._to_page(img.pno, vr.topLeft())
        b = self._to_page(img.pno, vr.bottomRight())
        rect = pymupdf.Rect(a, b).normalize()
        self._expect = (img.pno, rect)
        try:
            E.move_image(self.doc, img, rect, "Move image" if d["mode"] == "move" else "Resize image")
        except Exception as exc:
            self._msg(f"Could not change the image: {exc}")

    # ------------------------------------------------------------- events
    def press(self, e) -> bool:
        if not self._working():
            return False
        pos = e.position()
        if not self._allowed():
            self._msg("This document does not allow changes.")
            return True
        pno = self._page_at(pos)
        if self.mode == ADD_TEXT:
            if pno is not None:
                self._open_new(pno, pos)
            return True
        if self.mode == ADD_IMAGE:
            if pno is not None:
                self._add_image_at(pno, pos)
            return True
        h = self._handle_at(pos)
        if h is not None:
            self.drag = {"mode": "resize", "handle": h, "start": pos, "cur": pos, "moved": False,
                         "vrect": self._vrect(self.sel.pno, self.sel.rect), "image": self.sel}
            return True
        line = self.line_at(pos)
        img = self.image_at(pos)
        if line is not None and (img is None or line.rect.height < img.rect.height):
            self._select(None)
            self._open_line(line, pos)
            return True
        if img is not None:
            self._select(img)
            self.drag = {"mode": "move", "start": pos, "cur": pos, "moved": False,
                         "vrect": self._vrect(img.pno, img.rect), "image": img}
            return True
        self.commit()
        self._select(None)
        return True

    def move(self, e) -> bool:
        if not self.active:
            return False
        pos = e.position()
        d = self.drag
        if d is not None:
            d["cur"] = pos
            if (pos - d["start"]).manhattanLength() > 3:
                d["moved"] = True
            self.view.viewport().update()
            return True
        if self._working() and not e.buttons() and not self.mode:
            line = self.line_at(pos)
            if line is not self.hover:
                self.hover = line
                self.view.viewport().update()
        return False

    def release(self, e) -> bool:
        d, self.drag = self.drag, None
        if d is None:
            return False
        if d["moved"]:
            self._finish_drag(d)
        self.view.viewport().update()
        return True

    def double_click(self, e) -> bool:
        return self._working()

    def key(self, e) -> bool:
        if not self.active:
            return False
        k = e.key()
        if k == Qt.Key.Key_Escape:
            if self.drag:
                self.drag = None
                self.view.viewport().update()
                return True
            if self.mode:
                self.set_mode("")
                return True
            if self.sel is not None:
                self._select(None)
                return True
            return False
        if self.sel is None or not self._allowed():
            return False
        if k in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            self.delete_selected()
            return True
        if k in (Qt.Key.Key_Left, Qt.Key.Key_Right, Qt.Key.Key_Up, Qt.Key.Key_Down):
            step = 10 if e.modifiers() & Qt.KeyboardModifier.ShiftModifier else 1
            vec = {Qt.Key.Key_Left: (-step, 0), Qt.Key.Key_Right: (step, 0),
                   Qt.Key.Key_Up: (0, -step), Qt.Key.Key_Down: (0, step)}[k]
            img = self.sel
            a = self._to_page(img.pno, QPointF(0, 0))
            b = self._to_page(img.pno, QPointF(*vec))
            rect = img.rect + (b.x - a.x, b.y - a.y, b.x - a.x, b.y - a.y)
            self._expect = (img.pno, rect)
            try:
                E.move_image(self.doc, img, rect)
            except Exception as exc:
                self._msg(f"Could not move the image: {exc}")
            return True
        return False

    def cursor_at(self, pos):
        if not self._working():
            return None
        if self.mode == ADD_TEXT:
            return Qt.CursorShape.IBeamCursor
        if self.mode == ADD_IMAGE:
            return Qt.CursorShape.CrossCursor
        h = self._handle_at(pos)
        if h is not None:
            return RESIZE_CURSORS[h]
        line = self.line_at(pos)
        img = self.image_at(pos)
        if line is not None and (img is None or line.rect.height < img.rect.height):
            return Qt.CursorShape.IBeamCursor
        if img is not None:
            return Qt.CursorShape.SizeAllCursor
        return Qt.CursorShape.ArrowCursor

    # ------------------------------------------------------------ commands
    def delete_selected(self):
        img = self.sel
        if img is None:
            return
        self._select(None)
        try:
            E.delete_image(self.doc, img)
        except Exception as exc:
            self._msg(f"Could not delete the image: {exc}")

    def replace_selected(self, path: str):
        img = self.sel
        if img is None:
            return
        self._expect = (img.pno, img.rect)
        try:
            E.replace_image(self.doc, img, path)
        except Exception as exc:
            self._msg(f"Could not replace the image: {exc}")

    def _add_image_at(self, pno: int, pos: QPointF):
        path = self.image_path
        self.set_mode("")
        try:
            w, h = E.image_size_for(path)
        except Exception as exc:
            self._msg(f"Could not open the image: {exc}")
            return
        p = self._to_page(pno, pos)
        bounds = (self.doc.page_rects[pno] * ~self._rot(pno)).normalize()
        x0 = max(bounds.x0, min(p.x - w / 2, bounds.x1 - w))
        y0 = max(bounds.y0, min(p.y - h / 2, bounds.y1 - h))
        rect = pymupdf.Rect(x0, y0, x0 + w, y0 + h)
        self._expect = (pno, rect)
        try:
            E.add_image(self.doc, pno, rect, path)
        except Exception as exc:
            self._expect = None
            self._msg(f"Could not add the image: {exc}")

    # ------------------------------------------------------------ painting
    def paint(self, p, visible: list[int]):
        if not self.active or not self.doc.is_pdf:
            return
        p.save()
        p.setBrush(Qt.BrushStyle.NoBrush)
        editing = self.editor.target if self.editor is not None else None
        pen = QPen(LINE_COLOR, 1, Qt.PenStyle.DotLine)
        for pno in visible:
            p.setPen(pen)
            for ln in E.text_lines(self.doc, pno):
                if ln is editing:
                    continue
                p.drawRect(self._vrect(pno, ln.rect).adjusted(-1, -1, 1, 1))
            p.setPen(QPen(IMAGE_COLOR, 1, Qt.PenStyle.DashLine))
            for img in E.images(self.doc, pno):
                p.drawRect(self._vrect(pno, img.rect))
        if self.hover is not None and self.hover.pno in visible and self.hover is not editing:
            p.setPen(QPen(HOVER_COLOR, 1.5))
            p.drawRect(self._vrect(self.hover.pno, self.hover.rect).adjusted(-2, -2, 2, 2))
        img = self.sel
        d = self.drag
        if img is not None and img.pno in visible:
            r = self._vrect(img.pno, img.rect)
            p.setPen(QPen(HOVER_COLOR, 1.5))
            p.drawRect(r)
            if not (d and d["moved"]):
                p.setPen(QPen(HOVER_COLOR, 1))
                p.setBrush(QBrush(QColor("white")))
                for hp in self._handles(img):
                    p.drawRect(QRectF(hp.x() - HANDLE_PX, hp.y() - HANDLE_PX, HANDLE_PX * 2, HANDLE_PX * 2))
        if d and d["moved"]:
            p.setPen(QPen(HOVER_COLOR, 1.5, Qt.PenStyle.DashLine))
            p.setBrush(QBrush(QColor(0, 120, 215, 25)))
            p.drawRect(self._drag_rect(d))
        p.restore()
