"""Form fields in DocumentView: filling them in place, Tab order, field
highlighting, and Prepare Form (add, select, move, resize, delete fields)."""
from __future__ import annotations

import pymupdf
from PySide6.QtCore import QEvent, QItemSelectionModel, QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QBrush, QColor, QFont, QFontMetricsF, QPainter, QPen
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QComboBox, QFrame, QLineEdit, QListWidget, QListWidgetItem, QPlainTextEdit,
)

from paperleaf.core import forms as F

FIELD_TOOLS = {"field_text": F.TEXT, "field_check": F.CHECK, "field_radio": F.RADIO,
               "field_combo": F.COMBO, "field_list": F.LIST, "field_sig": F.SIGNATURE}
FIELD_TOOL_STAY = {"field_radio"}   # keeps adding buttons to the same radio group

HIGHLIGHT = QColor(214, 223, 255)           # Acrobat-like field tint (multiplied, so text stays dark)
REQUIRED = QColor(220, 0, 0)
FOCUS = QColor(0, 120, 215)
PREP_FILL = QColor(224, 236, 250)
PREP_BORDER = QColor(0, 120, 215, 170)
HANDLE_PX = 4.5
RESIZE_CURSORS = [Qt.CursorShape.SizeFDiagCursor, Qt.CursorShape.SizeVerCursor, Qt.CursorShape.SizeBDiagCursor,
                  Qt.CursorShape.SizeHorCursor, Qt.CursorShape.SizeFDiagCursor, Qt.CursorShape.SizeVerCursor,
                  Qt.CursorShape.SizeBDiagCursor, Qt.CursorShape.SizeHorCursor]
UI_FONT = "Arial"


def _qcolor(rgb) -> QColor:
    rgb = list(rgb or (0, 0, 0)) + [0, 0, 0]
    return QColor.fromRgbF(*[max(0.0, min(1.0, float(v))) for v in rgb[:3]])


# ======================================================================= editors
class _Editor:
    """Shared behaviour of the in-place field editors."""

    def _setup(self, ctl, f: F.Field):
        self._ctl = ctl
        self.field = f
        self._done = False

    def finish(self, commit=True):
        if self._done:
            return
        self._done = True
        self._ctl._editor_done(self, commit)

    def _nav_key(self, e) -> bool:
        """Tab / Shift+Tab move to the next / previous field."""
        if e.type() == QEvent.Type.KeyPress and e.key() in (Qt.Key.Key_Tab, Qt.Key.Key_Backtab) \
                and not e.modifiers() & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.AltModifier):
            back = e.key() == Qt.Key.Key_Backtab or bool(e.modifiers() & Qt.KeyboardModifier.ShiftModifier)
            self._ctl._tab_from_editor(self, back)
            return True
        return False

    def _focus_lost(self, e):
        if e.reason() in (Qt.FocusReason.PopupFocusReason, Qt.FocusReason.ActiveWindowFocusReason):
            return  # a context menu, a dropdown list or another window: keep editing
        QTimer.singleShot(0, self._maybe_finish)

    def _maybe_finish(self):
        """Focus moved away: finish, unless it is still inside this editor (or its list)."""
        if self._done or getattr(self, "_popup", False):
            return
        fw = QApplication.focusWidget()
        if fw is not None and (fw is self or self.isAncestorOf(fw)):
            return
        self.finish(True)


class LineEditor(QLineEdit, _Editor):
    def __init__(self, parent, ctl, f):
        super().__init__(parent)
        self._setup(ctl, f)
        self.setFrame(False)

    def value(self):
        return self.text()

    def event(self, e):
        if self._nav_key(e):
            return True
        return super().event(e)

    def keyPressEvent(self, e):
        if e.key() == Qt.Key.Key_Escape:
            self.finish(False)
            return
        if e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.finish(True)
            return
        super().keyPressEvent(e)

    def focusOutEvent(self, e):
        super().focusOutEvent(e)
        self._focus_lost(e)


class TextEditor(QPlainTextEdit, _Editor):
    """Multi-line text field: Enter adds a line, Ctrl+Enter finishes."""

    def __init__(self, parent, ctl, f):
        super().__init__(parent)
        self._setup(ctl, f)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.document().setDocumentMargin(1)

    def value(self):
        return self.toPlainText()

    def event(self, e):
        if self._nav_key(e):
            return True
        return super().event(e)

    def keyPressEvent(self, e):
        if e.key() == Qt.Key.Key_Escape:
            self.finish(False)
            return
        if e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and e.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self.finish(True)
            return
        max_len = self.field.maxlen
        if max_len and e.text() and e.text().isprintable() and len(self.toPlainText()) >= max_len \
                and not self.textCursor().hasSelection():
            return
        super().keyPressEvent(e)

    def focusOutEvent(self, e):
        super().focusOutEvent(e)
        self._focus_lost(e)


class ComboEditor(QComboBox, _Editor):
    def __init__(self, parent, ctl, f):
        super().__init__(parent)
        self._setup(ctl, f)
        self._popup = False
        self.setEditable(f.editable)
        self.setFrame(False)
        for exp, disp in f.options:
            self.addItem(disp, exp)
        cur = f.value or ""
        i = self.findData(cur)
        if i >= 0:
            self.setCurrentIndex(i)
        elif f.editable:
            self.setEditText(cur)
        else:
            self.setCurrentIndex(-1)
        self.activated.connect(self._activated)
        if f.editable:
            self.lineEdit().returnPressed.connect(lambda: self.finish(True))
            self.lineEdit().installEventFilter(self)

    def value(self):
        if self.isEditable():
            text = self.currentText()
            i = self.findText(text)
            return self.itemData(i) if i >= 0 else text
        return self.currentData() if self.currentIndex() >= 0 else ""

    def showPopup(self):
        self._popup = True
        super().showPopup()

    def hidePopup(self):
        super().hidePopup()
        QTimer.singleShot(0, self._popup_closed)

    def _popup_closed(self):
        self._popup = False

    def _activated(self, _i):
        if self._popup:  # picked from the list: done
            self.finish(True)

    def eventFilter(self, obj, e):
        if e.type() == QEvent.Type.KeyPress:
            if self._nav_key(e):
                return True
            if e.key() == Qt.Key.Key_Escape:
                self.finish(False)
                return True
        if e.type() == QEvent.Type.FocusOut:
            self._focus_lost(e)
        return super().eventFilter(obj, e)

    def event(self, e):
        if self._nav_key(e):
            return True
        return super().event(e)

    def keyPressEvent(self, e):
        if e.key() == Qt.Key.Key_Escape:
            self.finish(False)
            return
        if e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.finish(True)
            return
        super().keyPressEvent(e)

    def focusOutEvent(self, e):
        super().focusOutEvent(e)
        if self.isEditable() and self.lineEdit().hasFocus():
            return
        self._focus_lost(e)


class ListEditor(QListWidget, _Editor):
    def __init__(self, parent, ctl, f):
        super().__init__(parent)
        self._setup(ctl, f)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection if f.multiselect
                              else QAbstractItemView.SelectionMode.SingleSelection)
        sel = set(f.values)
        first = None
        for exp, disp in f.options:
            it = QListWidgetItem(disp)
            it.setData(Qt.ItemDataRole.UserRole, exp)
            self.addItem(it)
            if exp in sel:
                it.setSelected(True)
                first = it if first is None else first
        if first is not None:
            self.setCurrentItem(first, QItemSelectionModel.SelectionFlag.NoUpdate)
            self.scrollToItem(first)

    def value(self):
        return [self.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.count())
                if self.item(i).isSelected()]

    def event(self, e):
        if self._nav_key(e):
            return True
        return super().event(e)

    def keyPressEvent(self, e):
        if e.key() == Qt.Key.Key_Escape:
            self.finish(False)
            return
        if e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.finish(True)
            return
        super().keyPressEvent(e)

    def focusOutEvent(self, e):
        super().focusOutEvent(e)
        self._focus_lost(e)


# =================================================================== controller
class FormController:
    def __init__(self, view):
        self.view = view
        self.doc = view.doc
        self.highlight = True
        self.prepare = False
        self.editor = None
        self.focus = None        # (pno, xref) of the focused field when no editor is open
        self.sel: list = []      # Prepare Form: selected [(pno, xref)]
        self.drag = None
        self.make = None
        self.radio_group = None  # Prepare Form: radio group the radio tool adds to
        self.doc.listeners.append(self._doc_changed)

    # ------------------------------------------------------------- helpers
    def _scale(self) -> float:
        from paperleaf.viewer.view import PT
        return self.view.zoom * PT

    def _rot(self, pno):
        return self.doc.rotation_matrix(pno)

    def _view_to_text(self, pno: int, pos: QPointF) -> pymupdf.Point:
        return self.view.content_to_page(pno, pos + self.view._offset()) * ~self._rot(pno)

    def _bounds(self, pno) -> pymupdf.Rect:
        return (self.doc.page_rects[pno] * ~self._rot(pno)).normalize()

    def _clamp_rect(self, pno, r: pymupdf.Rect) -> pymupdf.Rect:
        b = self._bounds(pno)
        dx = max(0, b.x0 - r.x0) - max(0, r.x1 - b.x1)
        dy = max(0, b.y0 - r.y0) - max(0, r.y1 - b.y1)
        return pymupdf.Rect(r) + (dx, dy, dx, dy)

    def _vrect(self, f: F.Field) -> QRectF:
        return self.view.text_rect_to_view(f.pno, f.rect)

    def has_fields(self) -> bool:
        return F.has_fields(self.doc)

    def active(self) -> bool:
        return self.prepare or bool(self.doc.is_pdf and self.has_fields() and F.all_fields(self.doc))

    def _tool(self):
        return self.view.effective_tool()

    def field_at(self, pos: QPointF) -> F.Field | None:
        if not self.doc.is_pdf or not self.has_fields():
            return None
        pno = self.view._page_at(pos + self.view._offset())
        if pno is None:
            return None
        tp = self._view_to_text(pno, pos)
        return F.field_at(self.doc, pno, tp.x, tp.y, tol=1.0 / self._scale(), include_hidden=self.prepare)

    def get(self, key) -> F.Field | None:
        return F.find(self.doc, *key) if key else None

    def _msg(self, text):
        self.view.message.emit(text)

    def _fill_problem(self, f: F.Field) -> str:
        if not F.can_fill(self.doc):
            return "This document does not allow filling in form fields."
        if f.readonly:
            return "This field is read-only."
        return ""

    def _doc_changed(self, _pages):
        if self.focus and self.get(self.focus) is None:
            self.focus = None
        if self.sel:
            alive = [k for k in self.sel if self.get(k) is not None]
            if alive != self.sel:
                self.sel = alive
                self.view.fieldSelected.emit(self.selected())

    # -------------------------------------------------------------- modes
    def set_prepare(self, on: bool):
        if on == self.prepare:
            return
        self.commit()
        self.prepare = on
        self.focus = None
        self.radio_group = None
        if not on:
            self.select([])
        self.view.viewport().update()

    def set_highlight(self, on: bool):
        self.highlight = on
        self.view.viewport().update()

    def tool_changed(self):
        self.make = None
        self.drag = None
        self.radio_group = None

    def cancel(self):
        """Finish editing (keeps what was typed) and stop any drag."""
        self.commit()
        self.make = None
        self.drag = None

    def commit(self):
        if self.editor is not None:
            self.editor.finish(True)

    # ------------------------------------------------------------ editors
    def _font(self, f: F.Field, auto_lines=False) -> QFont:
        font = QFont(UI_FONT)
        fs = f.fontsize
        if not fs:
            fs = 12.0 if auto_lines else max(4.0, min(f.rect.height * 0.62, 14.0))
        font.setPixelSize(max(5, round(fs * self._scale())))
        return font

    def open_editor(self, f: F.Field, click: QPointF | None = None, popup: bool = True) -> bool:
        problem = self._fill_problem(f)
        if problem:
            self._msg(problem)
            return False
        self.commit()
        vp = self.view.viewport()
        if f.kind == F.TEXT:
            ed = TextEditor(vp, self, f) if f.multiline else LineEditor(vp, self, f)
        elif f.kind == F.COMBO:
            ed = ComboEditor(vp, self, f)
        elif f.kind == F.LIST:
            ed = ListEditor(vp, self, f)
        else:
            return False
        self.editor = ed
        self.focus = (f.pno, f.xref)
        fg = _qcolor(f.text_color)
        ed.setStyleSheet(f"background: rgba(255,255,255,250); color: {fg.name()}; "
                         f"border: 1px solid {FOCUS.name()};")
        ed.setFont(self._font(f, auto_lines=f.kind in (F.LIST,) or f.multiline))
        if isinstance(ed, LineEditor):
            ed.setText(f.value or "")
            if f.maxlen:
                ed.setMaxLength(f.maxlen)
            if f.password:
                ed.setEchoMode(QLineEdit.EchoMode.Password)
            ed.setAlignment({1: Qt.AlignmentFlag.AlignHCenter, 2: Qt.AlignmentFlag.AlignRight}.get(
                f.align, Qt.AlignmentFlag.AlignLeft) | Qt.AlignmentFlag.AlignVCenter)
        elif isinstance(ed, TextEditor):
            ed.setPlainText(f.value or "")
        self._place(ed)
        ed.show()
        ed.setFocus(Qt.FocusReason.OtherFocusReason)
        if isinstance(ed, LineEditor):
            if click is not None:
                ed.setCursorPosition(ed.cursorPositionAt(ed.mapFrom(vp, click.toPoint())))
            else:
                ed.selectAll()
        elif isinstance(ed, TextEditor):
            cur = ed.cursorForPosition(ed.viewport().mapFrom(vp, click.toPoint())) if click is not None else None
            if cur is None:
                cur = ed.textCursor()
                cur.movePosition(cur.MoveOperation.End)
            ed.setTextCursor(cur)
        elif isinstance(ed, ComboEditor) and popup and not f.editable:
            QTimer.singleShot(0, lambda e=ed: e is self.editor and e.showPopup())
        self.view.viewport().update()
        return True

    def _place(self, ed):
        f = ed.field
        r = self._vrect(f)
        if isinstance(ed, ComboEditor):
            r.setHeight(max(r.height(), QFontMetricsF(ed.font()).height() + 4))
        ed.setGeometry(r.toAlignedRect())
        if isinstance(ed, LineEditor) and f.comb:
            cell = r.width() / f.maxlen
            font = ed.font()
            avg = QFontMetricsF(font).averageCharWidth()
            font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, max(0.0, cell - avg))
            ed.setFont(font)
            ed.setTextMargins(int(max(0.0, (cell - avg) / 2)), 0, 0, 0)
            ed.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)

    def _editor_done(self, ed, commit: bool):
        if ed is not self.editor:
            return
        value = ed.value() if commit else None
        self.editor = None
        fw = QApplication.focusWidget()
        had_focus = fw is None or fw is ed or ed.isAncestorOf(fw)
        ed.hide()
        ed.deleteLater()
        if had_focus:  # don't take the focus back from, say, the find box
            self.view.setFocus(Qt.FocusReason.OtherFocusReason)
        f = ed.field
        if commit:
            try:
                fresh = self.get((f.pno, f.xref)) or f
                F.set_value(self.doc, fresh, value)
            except Exception as exc:
                self._msg(f"Could not fill in the field: {exc}")
        self.view.viewport().update()

    def view_changed(self):
        """Scrolled or zoomed: keep the editor over its field."""
        ed = self.editor
        if ed is None:
            return
        if self.view._rects[ed.field.pno].isNull():
            ed.finish(True)
            return
        ed.setFont(self._font(ed.field, auto_lines=ed.field.kind == F.LIST or ed.field.multiline))
        self._place(ed)

    # ----------------------------------------------------------------- tab
    def _tab_stops(self) -> list[F.Field]:
        out, groups = [], {}
        for f in F.all_fields(self.doc):
            if not f.interactive:
                continue
            if f.kind == F.RADIO:
                if f.name in groups:
                    if f.is_on:  # one stop per group: the chosen button
                        out[groups[f.name]] = f
                    continue
                groups[f.name] = len(out)
            out.append(f)
        return out

    def tab(self, back: bool = False, origin=None) -> bool:
        stops = self._tab_stops()
        if not stops:
            return False
        cur = origin or (self.editor.field if self.editor is not None else self.get(self.focus))
        idx = None
        if cur is not None:
            for i, f in enumerate(stops):
                if (f.pno, f.xref) == (cur.pno, cur.xref) or (f.kind == F.RADIO and f.name == cur.name
                                                             and cur.kind == F.RADIO):
                    idx = i
                    break
        if idx is None:
            page = self.view.current_page()
            if back:
                before = [i for i, f in enumerate(stops) if f.pno <= page]
                nxt = before[-1] if before else len(stops) - 1
            else:
                nxt = next((i for i, f in enumerate(stops) if f.pno >= page), 0)
        else:
            nxt = (idx + (-1 if back else 1)) % len(stops)
        self.activate(stops[nxt])
        return True

    def _tab_from_editor(self, ed, back: bool):
        f = ed.field
        ed.finish(True)
        self.tab(back, origin=self.get((f.pno, f.xref)) or f)

    def activate(self, f: F.Field):
        """Give a field the keyboard focus (opening its editor if it has one)."""
        self.commit()
        self.view._ensure_visible(f.pno, tuple(f.rect))
        if f.kind in (F.TEXT, F.COMBO, F.LIST) and not self._fill_problem(f):
            self.open_editor(f, popup=False)
        else:
            self.focus = (f.pno, f.xref)
        self.view.viewport().update()

    # -------------------------------------------------------------- clicks
    def _click_field(self, f: F.Field, pos: QPointF):
        if f.kind == F.BUTTON:
            self.focus = (f.pno, f.xref)
            self.view.formAction.emit(dict(f.action or {"type": ""}))
            return
        if f.kind == F.SIGNATURE:
            self.focus = (f.pno, f.xref)
            if f.signed:
                self._msg("This field has a digital signature.")
            else:
                self.view.signatureFieldClicked.emit(f.pno, f.xref)
            return
        problem = self._fill_problem(f)
        if problem:
            self._msg(problem)
            return
        if f.kind in (F.CHECK, F.RADIO):
            self.commit()
            self.focus = (f.pno, f.xref)
            try:
                F.toggle(self.doc, f)
            except Exception as exc:
                self._msg(f"Could not fill in the field: {exc}")
            self.view.viewport().update()
            return
        self.open_editor(f, click=pos)

    def press(self, e) -> bool:
        if e.button() != Qt.MouseButton.LeftButton:
            return False
        if self.prepare:
            return self._prep_press(e)
        if self._tool() not in ("select", "hand"):
            return False
        pos = e.position()
        f = self.field_at(pos)
        if f is None:
            self.commit()
            if self.focus:
                self.focus = None
                self.view.viewport().update()
            return False
        if self.editor is not None and (self.editor.field.pno, self.editor.field.xref) == (f.pno, f.xref):
            return True
        self.commit()
        f = self.field_at(pos) or f  # committing may have re-read the fields
        self._click_field(f, pos)
        return True

    def move(self, e) -> bool:
        if not self.prepare:
            return False
        pos = e.position()
        if self.drag:
            self.drag["cur"] = pos
            if (pos - self.drag["start"]).manhattanLength() > 3:
                self.drag["moved"] = True
            self.view.viewport().update()
            return True
        if self.make:
            self.make["cur"] = pos
            self.view.viewport().update()
            return True
        return False

    def release(self, e) -> bool:
        if not self.prepare:
            return False
        if self.drag:
            d, self.drag = self.drag, None
            if d["moved"]:
                self._finish_drag(d)
            self.view.viewport().update()
            return True
        if self.make:
            m, self.make = self.make, None
            m["cur"] = e.position()
            self._finish_make(m)
            self.view.viewport().update()
            return True
        return False

    def double_click(self, e) -> bool:
        f = self.field_at(e.position())
        if f is None:
            return False
        if self.prepare:
            self.select([(f.pno, f.xref)])
            self.view.fieldActivated.emit(f.pno, f.xref)
        return True

    # ---------------------------------------------------------------- keys
    def key(self, e) -> bool:
        k = e.key()
        if self.prepare:
            return self._prep_key(e)
        f = self.get(self.focus)
        if f is None:
            return False
        if k == Qt.Key.Key_Escape:
            self.focus = None
            self.view.viewport().update()
            return True
        if k == Qt.Key.Key_Space and f.kind in (F.CHECK, F.RADIO, F.SIGNATURE, F.BUTTON):
            self._click_field(f, QPointF())
            return True
        if k in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and f.kind in (F.TEXT, F.COMBO, F.LIST):
            self.open_editor(f, popup=False)
            return True
        if f.kind == F.RADIO and k in (Qt.Key.Key_Left, Qt.Key.Key_Right, Qt.Key.Key_Up, Qt.Key.Key_Down):
            buttons = [g for g in F.group(self.doc, f) if not g.hidden]
            i = next((n for n, g in enumerate(buttons) if g.xref == f.xref), 0)
            step = -1 if k in (Qt.Key.Key_Left, Qt.Key.Key_Up) else 1
            nxt = buttons[(i + step) % len(buttons)]
            if not self._fill_problem(nxt):
                F.set_value(self.doc, nxt, nxt.on_state)
            self.focus = (nxt.pno, nxt.xref)
            self.view.viewport().update()
            return True
        return False

    # ============================================================ Prepare Form
    def selected(self) -> list[F.Field]:
        return [f for f in (self.get(k) for k in self.sel) if f is not None]

    def select(self, keys: list):
        keys = list(dict.fromkeys(keys))
        if keys != self.sel:
            self.sel = keys
            self.view.fieldSelected.emit(self.selected())
        self.view.viewport().update()

    def _editable(self) -> bool:
        if F.can_prepare(self.doc):
            return True
        self._msg("This document does not allow adding or changing form fields." if self.doc.is_pdf
                  else "Form fields can only be added to PDF files.")
        return False

    def _handles(self, f: F.Field) -> list[QPointF]:
        r = self._vrect(f)
        cx, cy = r.center().x(), r.center().y()
        return [QPointF(r.left(), r.top()), QPointF(cx, r.top()), QPointF(r.right(), r.top()),
                QPointF(r.right(), cy), QPointF(r.right(), r.bottom()), QPointF(cx, r.bottom()),
                QPointF(r.left(), r.bottom()), QPointF(r.left(), cy)]

    def _handle_at(self, pos: QPointF):
        sel = self.selected()
        if len(sel) != 1:
            return None
        for i, hp in enumerate(self._handles(sel[0])):
            if abs(pos.x() - hp.x()) <= HANDLE_PX + 2 and abs(pos.y() - hp.y()) <= HANDLE_PX + 2:
                return i
        return None

    def cursor_at(self, pos: QPointF):
        tool = self._tool()
        if self.prepare:
            if tool in FIELD_TOOLS:
                return Qt.CursorShape.CrossCursor
            if tool not in ("select", "hand"):
                return None
            h = self._handle_at(pos)
            if h is not None:
                return RESIZE_CURSORS[h]
            if self.field_at(pos) is not None:
                return Qt.CursorShape.SizeAllCursor
            return None
        if tool not in ("select", "hand"):
            return None
        f = self.field_at(pos)
        if f is None:
            return None
        if f.kind == F.BUTTON or f.kind == F.SIGNATURE:
            return Qt.CursorShape.PointingHandCursor
        if f.readonly or not F.can_fill(self.doc):
            return Qt.CursorShape.ArrowCursor
        if f.kind == F.TEXT or (f.kind == F.COMBO and f.editable):
            return Qt.CursorShape.IBeamCursor
        return Qt.CursorShape.PointingHandCursor

    def tooltip_at(self, pos: QPointF) -> str:
        f = self.field_at(pos)
        if f is None:
            return ""
        if self.prepare:
            return f"{f.label}: {f.name}"
        return f.tooltip

    def _prep_press(self, e) -> bool:
        pos = e.position()
        tool = self._tool()
        if tool in FIELD_TOOLS:
            if not self._editable():
                return True
            pno = self.view._page_at(pos + self.view._offset())
            if pno is None:
                return True
            self.make = {"kind": FIELD_TOOLS[tool], "tool": tool, "pno": pno, "start": pos, "cur": pos}
            self.select([])
            return True
        if tool not in ("select", "hand"):
            return False
        h = self._handle_at(pos)
        if h is not None and self._editable():
            f = self.selected()[0]
            self.drag = {"mode": "resize", "handle": h, "start": pos, "cur": pos, "moved": False,
                         "vrect": self._vrect(f), "fields": [f]}
            return True
        f = self.field_at(pos)
        if f is None:
            if self.sel:
                self.select([])
            return False
        key = (f.pno, f.xref)
        add = bool(e.modifiers() & (Qt.KeyboardModifier.ShiftModifier | Qt.KeyboardModifier.ControlModifier))
        if add:
            self.select([k for k in self.sel if k != key] if key in self.sel else self.sel + [key])
        elif key not in self.sel:
            self.select([key])
        if self.sel and F.can_prepare(self.doc):
            self.drag = {"mode": "move", "start": pos, "cur": pos, "moved": False,
                         "fields": [g for g in self.selected() if g.pno == f.pno]}
        return True

    def _drag_rect(self, d) -> QRectF:
        r = QRectF(d["vrect"])
        dx = d["cur"].x() - d["start"].x()
        dy = d["cur"].y() - d["start"].y()
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
        nr = QRectF(QPointF(left, top), QPointF(right, bottom)).normalized()
        nr.setWidth(max(nr.width(), 6))
        nr.setHeight(max(nr.height(), 6))
        return nr

    def _finish_drag(self, d):
        fields = d["fields"]
        if not fields:
            return
        pno = fields[0].pno
        try:
            if d["mode"] == "move":
                a = self._view_to_text(pno, d["start"])
                b = self._view_to_text(pno, d["cur"])
                F.move_fields(self.doc, fields, b.x - a.x, b.y - a.y)
            else:
                vr = self._drag_rect(d)
                p0 = self._view_to_text(pno, vr.topLeft())
                p1 = self._view_to_text(pno, vr.bottomRight())
                F.set_rect(self.doc, fields[0], self._clamp_rect(pno, pymupdf.Rect(p0, p1).normalize()),
                           "Resize field")
        except Exception as exc:
            self._msg(f"Could not change the field: {exc}")

    def _new_rect(self, m) -> pymupdf.Rect:
        pno, kind = m["pno"], m["kind"]
        a = self._view_to_text(pno, m["start"])
        if (m["cur"] - m["start"]).manhattanLength() > 6:
            b = self._view_to_text(pno, m["cur"])
            r = pymupdf.Rect(a, b).normalize()
            if kind in (F.CHECK, F.RADIO):  # keep buttons square
                s = max(8.0, min(r.width, r.height))
                r = pymupdf.Rect(r.x0, r.y0, r.x0 + s, r.y0 + s)
            if r.width < 8 or r.height < 8:
                r = pymupdf.Rect(r.x0, r.y0, r.x0 + max(8, r.width), r.y0 + max(8, r.height))
        else:
            w, h = F.DEFAULT_SIZES[kind]
            ps = a * self._rot(pno)  # size as the page is displayed
            page_r = pymupdf.Rect(ps.x, ps.y - h / 2, ps.x + w, ps.y + h / 2)
            r = (page_r * ~self._rot(pno)).normalize()
        return self._clamp_rect(pno, r)

    def _finish_make(self, m):
        kind, pno = m["kind"], m["pno"]
        rect = self._new_rect(m)
        try:
            if kind == F.RADIO:
                groups = F.radio_groups(self.doc)
                group = self.radio_group if self.radio_group in groups else None
                xref = F.add_field(self.doc, pno, kind, rect, group_name=group)
                f = F.find(self.doc, pno, xref)
                self.radio_group = f.name if f else None
            else:
                xref = F.add_field(self.doc, pno, kind, rect)
        except Exception as exc:
            self._msg(f"Could not add the field: {exc}")
            return
        self.select([(pno, xref)])
        self.view.fieldCreated.emit(m["tool"], pno, xref)

    def delete_selected(self):
        fields = self.selected()
        if not fields or not self._editable():
            return
        self.sel = []
        try:
            F.delete_fields(self.doc, fields)
        except Exception as exc:
            self._msg(f"Could not delete the field: {exc}")
        self.view.fieldSelected.emit([])
        self.view.viewport().update()

    def _prep_key(self, e) -> bool:
        k = e.key()
        if k == Qt.Key.Key_Escape:
            if self.make or self.drag:
                self.make = self.drag = None
                self.view.viewport().update()
                return True
            if self.sel:
                self.select([])
                return True
            return False
        fields = self.selected()
        if not fields:
            return False
        if k in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            self.delete_selected()
            return True
        if k in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and len(fields) == 1:
            self.view.fieldActivated.emit(fields[0].pno, fields[0].xref)
            return True
        if k in (Qt.Key.Key_Left, Qt.Key.Key_Right, Qt.Key.Key_Up, Qt.Key.Key_Down) and self._editable():
            step = 10 if e.modifiers() & Qt.KeyboardModifier.ShiftModifier else 1
            vec = {Qt.Key.Key_Left: (-step, 0), Qt.Key.Key_Right: (step, 0),
                   Qt.Key.Key_Up: (0, -step), Qt.Key.Key_Down: (0, step)}[k]
            pno = fields[0].pno
            a = self._view_to_text(pno, QPointF(0, 0))
            b = self._view_to_text(pno, QPointF(vec[0], vec[1]))
            same = [f for f in fields if f.pno == pno]
            try:
                F.move_fields(self.doc, same, b.x - a.x, b.y - a.y)
            except Exception as exc:
                self._msg(f"Could not move the field: {exc}")
            return True
        return False

    # =============================================================== painting
    def paint(self, p, visible: list[int]):
        if not self.doc.is_pdf or (not self.has_fields() and not self.make):
            return
        if self.prepare:
            self._paint_prepare(p, visible)
            return
        if self.highlight and not self.view.night:
            shown = [(f, self._vrect(f)) for pno in visible for f in F.page_fields(self.doc, pno) if not f.hidden]
            p.save()
            p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Multiply)
            for f, r in shown:
                p.fillRect(r, HIGHLIGHT)
            p.restore()
            p.setPen(QPen(REQUIRED, 1))
            p.setBrush(Qt.BrushStyle.NoBrush)
            for f, r in shown:
                if f.required:
                    p.drawRect(r.adjusted(0.5, 0.5, -0.5, -0.5))
        f = self.get(self.focus) if self.editor is None else None
        if f is not None and f.pno in visible:
            pen = QPen(FOCUS, 1.5, Qt.PenStyle.DotLine)
            p.setPen(pen)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRect(self._vrect(f).adjusted(-2, -2, 2, 2))

    def _paint_prepare(self, p, visible):
        font = QFont(UI_FONT)
        font.setPixelSize(10)
        p.setFont(font)
        dragging = {(f.pno, f.xref) for f in (self.drag or {}).get("fields", [])} if self.drag and \
            self.drag["moved"] else set()
        fm = QFontMetricsF(font)
        shown = [(f, self._vrect(f)) for pno in visible for f in F.page_fields(self.doc, pno)
                 if (f.pno, f.xref) not in dragging]
        p.save()
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Multiply)
        for f, r in shown:
            p.fillRect(r, PREP_FILL)
        p.restore()
        for f, r in shown:
            p.setPen(QPen(PREP_BORDER, 1, Qt.PenStyle.DashLine if f.hidden else Qt.PenStyle.SolidLine))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRect(r.adjusted(0.5, 0.5, -0.5, -0.5))
            if r.width() >= 30 and r.height() >= 10:  # name tag in the top-left corner
                text = fm.elidedText(f.name, Qt.TextElideMode.ElideRight, r.width() - 6)
                tag = QRectF(r.left() + 1, r.top() + 1, fm.horizontalAdvance(text) + 6, fm.height() + 1)
                p.fillRect(tag, QColor(0, 120, 215, 200))
                p.setPen(QColor("white"))
                p.drawText(tag, Qt.AlignmentFlag.AlignCenter, text)
        for f in self.selected():
            if f.pno not in visible or (f.pno, f.xref) in dragging:
                continue
            r = self._vrect(f).adjusted(-1, -1, 1, 1)
            p.setPen(QPen(FOCUS, 1.5))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRect(r)
            if len(self.sel) == 1:
                p.setPen(QPen(FOCUS, 1))
                p.setBrush(QBrush(QColor("white")))
                for hp in self._handles(f):
                    p.drawRect(QRectF(hp.x() - HANDLE_PX, hp.y() - HANDLE_PX, HANDLE_PX * 2, HANDLE_PX * 2))
        d = self.drag
        if d and d["moved"]:
            p.setPen(QPen(FOCUS, 1.5, Qt.PenStyle.DashLine))
            p.setBrush(QBrush(QColor(0, 120, 215, 25)))
            if d["mode"] == "move":
                delta = d["cur"] - d["start"]
                for f in d["fields"]:
                    p.drawRect(self._vrect(f).translated(delta))
            else:
                p.drawRect(self._drag_rect(d))
        m = self.make
        if m and (m["cur"] - m["start"]).manhattanLength() > 6:
            p.setPen(QPen(FOCUS, 1, Qt.PenStyle.DashLine))
            p.setBrush(QBrush(QColor(0, 120, 215, 25)))
            p.drawRect(QRectF(m["start"], m["cur"]).normalized())
