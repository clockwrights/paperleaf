"""Creating, selecting, moving, resizing and editing annotations in DocumentView."""
from __future__ import annotations

import math

import pymupdf
from PySide6.QtCore import QDate, QLocale, QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QBrush, QColor, QFont, QFontMetricsF, QPainterPath, QPen, QTextOption
from PySide6.QtWidgets import QFrame, QPlainTextEdit

from paperleaf.core import annotations as an

MARKUP_TOOLS = {"highlight", "underline", "strikeout", "squiggly"}
DRAG_SHAPES = {"rect", "ellipse", "line", "arrow"}
POLY_TOOLS = {"polygon", "cloud", "polyline"}
SYMBOL_TOOLS = {"check", "cross", "dot"}
CREATE_TOOLS = ({"note", "freetext", "callout", "filltext", "pen", "highlighter", "eraser", "stamp",
                 "signature", "date"} | DRAG_SHAPES | POLY_TOOLS | SYMBOL_TOOLS)
REDACT = "redact"   # mark for redaction: select text, or drag a box elsewhere
TEXT_TOOLS = MARKUP_TOOLS | {REDACT}   # tools that act on a text selection
ANNOT_TOOLS = CREATE_TOOLS | MARKUP_TOOLS | {REDACT}
STAY_ACTIVE = MARKUP_TOOLS | DRAG_SHAPES | POLY_TOOLS | SYMBOL_TOOLS | {"pen", "highlighter", "eraser", REDACT}

SEL_COLOR = QColor(0, 120, 215)
HANDLE_PX = 4.5
RESIZE_CURSORS = [Qt.CursorShape.SizeFDiagCursor, Qt.CursorShape.SizeVerCursor, Qt.CursorShape.SizeBDiagCursor,
                  Qt.CursorShape.SizeHorCursor, Qt.CursorShape.SizeFDiagCursor, Qt.CursorShape.SizeVerCursor,
                  Qt.CursorShape.SizeBDiagCursor, Qt.CursorShape.SizeHorCursor]


def qcolor(rgb, alpha: float = 1.0) -> QColor:
    if not rgb:
        return QColor(0, 0, 0, 0)
    c = QColor.fromRgbF(*[max(0.0, min(1.0, v)) for v in rgb[:3]])
    c.setAlphaF(max(0.0, min(1.0, alpha)))
    return c


class InlineEditor(QPlainTextEdit):
    """Text box editor placed directly over the page."""

    def __init__(self, parent, on_commit, on_cancel):
        super().__init__(parent)
        self._on_commit = on_commit
        self._on_cancel = on_cancel
        self._done = False
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setWordWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
        self.document().setDocumentMargin(2)

    def finish(self, commit=True):
        if self._done:
            return
        self._done = True
        (self._on_commit if commit else self._on_cancel)(self.toPlainText())

    def keyPressEvent(self, e):
        if e.key() == Qt.Key.Key_Escape:
            self.finish(commit=bool(self.toPlainText().strip()))
            return
        if e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and e.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self.finish(True)
            return
        super().keyPressEvent(e)

    def focusOutEvent(self, e):
        super().focusOutEvent(e)
        QTimer.singleShot(0, lambda: self.finish(True))


class AnnotController:
    def __init__(self, view):
        self.view = view
        self.doc = view.doc
        self.sel = None          # (pno, xref)
        self.drag = None         # move / resize / line-end drag
        self.make = None         # creation in progress
        self.editor = None
        self.editor_ctx = None
        self.env = None          # set by the window: author(), style(tool), stamp(), signature()
        self.doc.listeners.append(self._doc_changed)

    # ------------------------------------------------------------- helpers
    def _scale(self) -> float:
        from paperleaf.viewer.view import PT
        return self.view.zoom * PT

    def _tol(self) -> float:
        return 5.0 / self._scale()

    def _rot(self, pno):
        return self.doc.rotation_matrix(pno)

    def _view_to_text(self, pno: int, pos: QPointF) -> pymupdf.Point:
        pg = self.view.content_to_page(pno, pos + self.view._offset())
        return pg * ~self._rot(pno)

    def _text_to_view(self, pno: int, x: float, y: float) -> QPointF:
        p = pymupdf.Point(x, y) * self._rot(pno)
        return self.view.page_to_content(pno, p.x, p.y) - self.view._offset()

    def _hit_page(self, pos: QPointF):
        pno = self.view._page_at(pos + self.view._offset())
        if pno is None:
            return None
        return pno, self._view_to_text(pno, pos)

    def _bounds(self, pno) -> pymupdf.Rect:
        return (self.doc.page_rects[pno] * ~self._rot(pno)).normalize()

    def _clamp(self, pno, p: pymupdf.Point) -> pymupdf.Point:
        b = self._bounds(pno)
        return pymupdf.Point(min(max(p.x, b.x0), b.x1), min(max(p.y, b.y0), b.y1))

    def _clamp_rect(self, pno, r: pymupdf.Rect) -> pymupdf.Rect:
        b = self._bounds(pno)
        dx = max(0, b.x0 - r.x0) - max(0, r.x1 - b.x1)
        dy = max(0, b.y0 - r.y0) - max(0, r.y1 - b.y1)
        return pymupdf.Rect(r) + (dx, dy, dx, dy)

    def _page_rect_to_text(self, pno, r: pymupdf.Rect) -> pymupdf.Rect:
        return (pymupdf.Rect(r) * ~self._rot(pno)).normalize()

    def record(self):
        if not self.sel:
            return None
        return an.find(self.doc, *self.sel)

    def record_at(self, pos: QPointF):
        if not self.doc.is_pdf:
            return None
        hit = self._hit_page(pos)
        if not hit:
            return None
        return an.annot_at(self.doc, hit[0], hit[1].x, hit[1].y, self._tol())

    def select(self, pno=None, xref=None):
        new = (pno, xref) if xref else None
        if new != self.sel:
            self.sel = new
            self.view.annotSelected.emit(self.record())
        self.view.viewport().update()

    def _editable(self) -> bool:
        if self.doc.can_annotate():
            return True
        self.view.message.emit("This document does not allow comments or changes." if self.doc.is_pdf
                               else "Comments can only be added to PDF files.")
        return False

    def _env(self, name, *args, default=None):
        fn = getattr(self.env, name, None) if self.env else None
        return fn(*args) if fn else default

    def _style(self, tool) -> an.Style:
        return self._env("style", tool, default=an.Style())

    def _author(self) -> str:
        return self._env("author", default="") or ""

    def _doc_changed(self, _pages):
        if self.sel and an.find(self.doc, *self.sel) is None:
            self.sel = None
            self.view.annotSelected.emit(None)

    def cancel(self):
        if self.editor is not None:
            self.editor.finish(True)
        self.make = None
        self.drag = None
        self.view.viewport().update()

    # ------------------------------------------------------------- handles
    def _handles(self, rec) -> list[QPointF]:
        if rec is None or not rec.movable:
            return []
        if rec.kind == "Line" and len(rec.vertices) >= 2:
            return [self._text_to_view(rec.pno, *rec.vertices[0]), self._text_to_view(rec.pno, *rec.vertices[1])]
        if not rec.resizable:
            return []
        r = self.view.text_rect_to_view(rec.pno, rec.rect)
        cx, cy = r.center().x(), r.center().y()
        return [QPointF(r.left(), r.top()), QPointF(cx, r.top()), QPointF(r.right(), r.top()),
                QPointF(r.right(), cy), QPointF(r.right(), r.bottom()), QPointF(cx, r.bottom()),
                QPointF(r.left(), r.bottom()), QPointF(r.left(), cy)]

    def _handle_at(self, pos: QPointF, rec):
        for i, hp in enumerate(self._handles(rec)):
            if abs(pos.x() - hp.x()) <= HANDLE_PX + 2 and abs(pos.y() - hp.y()) <= HANDLE_PX + 2:
                return i
        return None

    def _on_text(self, pos: QPointF) -> bool:
        hit = self._hit_page(pos)
        return bool(hit) and self.doc.page_text(hit[0]).hit(hit[1].x, hit[1].y)

    def cursor_at(self, pos: QPointF):
        tool = self.view.effective_tool()
        if tool in CREATE_TOOLS:
            return Qt.CursorShape.CrossCursor
        if tool == REDACT:
            return Qt.CursorShape.IBeamCursor if self._on_text(pos) else Qt.CursorShape.CrossCursor
        if tool not in ("select", "hand") or not self.doc.is_pdf:
            return None
        rec = self.record()
        if rec is not None and self.doc.can_annotate():
            h = self._handle_at(pos, rec)
            if h is not None:
                return Qt.CursorShape.SizeAllCursor if rec.kind == "Line" else RESIZE_CURSORS[h]
        hit = self.record_at(pos)
        if hit is not None:
            if hit.movable and self.doc.can_annotate():
                return Qt.CursorShape.SizeAllCursor
            return Qt.CursorShape.PointingHandCursor
        return None

    # --------------------------------------------------------------- events
    def press(self, e) -> bool:
        if e.button() != Qt.MouseButton.LeftButton:
            return False
        pos = e.position()
        tool = self.view.effective_tool()
        if self.editor is not None:
            self.editor.finish(True)
        if self.make and self.make["tool"] in POLY_TOOLS:
            self._poly_click(pos)
            return True
        rec = self.record()
        if rec is not None and tool in ("select", "hand") and self.doc.can_annotate():
            h = self._handle_at(pos, rec)
            if h is not None:
                self.drag = {"mode": "line" if rec.kind == "Line" else "resize", "handle": h, "rec": rec,
                             "start": pos, "cur": pos, "moved": False,
                             "vrect": self.view.text_rect_to_view(rec.pno, rec.rect)}
                return True
        if tool in ("select", "hand"):
            hit = self.record_at(pos)
            if hit is None:
                if self.sel:
                    self.select(None)
                return False
            self.select(hit.pno, hit.xref)
            if hit.movable and self.doc.can_annotate():
                self.drag = {"mode": "move", "rec": hit, "start": pos, "cur": pos, "moved": False,
                             "vrect": self.view.text_rect_to_view(hit.pno, hit.rect)}
            return True
        if tool == REDACT:
            if self.sel:
                self.select(None)
            if not self._editable():
                return True
            if self._on_text(pos):
                return False  # the view selects the text; releasing marks it
            hit = self._hit_page(pos)
            if hit is None:
                return True
            self.make = {"tool": REDACT, "pno": hit[0], "start": hit[1], "cur": hit[1], "pts": [hit[1]],
                         "start_pos": pos, "shift": False}
            return True
        if tool in MARKUP_TOOLS:
            if self.sel:
                self.select(None)
            return not self.doc.can_annotate() and not self._editable()
        if tool not in CREATE_TOOLS:
            return False
        if not self._editable():
            return True
        hit = self._hit_page(pos)
        if hit is None:
            return True
        pno, tp = hit
        self.select(None)
        if tool == "eraser":
            self.make = {"tool": tool, "pno": pno, "marked": {}}
            self._erase_at(pos)
            return True
        self.make = {"tool": tool, "pno": pno, "start": tp, "cur": tp, "pts": [tp], "start_pos": pos,
                     "shift": False}
        if tool in POLY_TOOLS:
            self.make["pts"] = [tp]
        self.view.viewport().update()
        return True

    def move(self, e) -> bool:
        pos = e.position()
        if self.drag:
            self.drag["cur"] = pos
            if (pos - self.drag["start"]).manhattanLength() > 3:
                self.drag["moved"] = True
            self.view.viewport().update()
            return True
        if self.make:
            m = self.make
            if m["tool"] == "eraser":
                if e.buttons() & Qt.MouseButton.LeftButton:
                    self._erase_at(pos)
                return True
            tp = self._clamp(m["pno"], self._view_to_text(m["pno"], pos))
            m["shift"] = bool(e.modifiers() & Qt.KeyboardModifier.ShiftModifier)
            if m["tool"] in ("pen", "highlighter"):
                last = m["pts"][-1]
                if math.hypot(tp.x - last.x, tp.y - last.y) * self._scale() >= 1.5:
                    m["pts"].append(tp)
            m["cur"] = tp
            self.view.viewport().update()
            return True
        return False

    def release(self, e) -> bool:
        if self.drag:
            d, self.drag = self.drag, None
            if d["moved"]:
                self._finish_drag(d)
            self.view.viewport().update()
            return True
        if self.make and self.make["tool"] not in POLY_TOOLS:
            m, self.make = self.make, None
            m["shift"] = bool(e.modifiers() & Qt.KeyboardModifier.ShiftModifier)
            m["end_pos"] = e.position()
            self._finish_create(m)
            self.view.viewport().update()
            return True
        return bool(self.make)

    def double_click(self, e) -> bool:
        tool = self.view.effective_tool()
        if self.make and self.make["tool"] in POLY_TOOLS:
            self._finish_poly()
            return True
        if tool not in ("select", "hand"):
            return tool in CREATE_TOOLS
        rec = self.record_at(e.position())
        if rec is None:
            return False
        self.select(rec.pno, rec.xref)
        if rec.kind == "FreeText" and self.doc.can_annotate() and not rec.is_symbol:
            self.edit_text(rec)
        else:
            self.view.annotActivated.emit(rec.pno, rec.xref)
        return True

    def key(self, e) -> bool:
        k = e.key()
        if k == Qt.Key.Key_Escape:
            if self.make:
                self.make = None
                self.view.viewport().update()
                return True
            if self.sel:
                self.select(None)
                return True
            return False
        if self.make and self.make["tool"] in POLY_TOOLS and k in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self._finish_poly()
            return True
        rec = self.record()
        if rec is None or not self.doc.can_annotate():
            return False
        if k in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            self.delete_selected()
            return True
        if k in (Qt.Key.Key_Left, Qt.Key.Key_Right, Qt.Key.Key_Up, Qt.Key.Key_Down) and rec.movable:
            step = (10 if e.modifiers() & Qt.KeyboardModifier.ShiftModifier else 1)
            vec = {Qt.Key.Key_Left: (-step, 0), Qt.Key.Key_Right: (step, 0),
                   Qt.Key.Key_Up: (0, -step), Qt.Key.Key_Down: (0, step)}[k]
            # nudge in screen directions
            a = self._view_to_text(rec.pno, QPointF(0, 0))
            b = self._view_to_text(rec.pno, QPointF(vec[0] / self._scale(), vec[1] / self._scale()))
            an.move(self.doc, rec.pno, rec.xref, b.x - a.x, b.y - a.y)
            return True
        return False

    def delete_selected(self):
        rec = self.record()
        if rec is None or not self.doc.can_annotate():
            return
        self.sel = None
        an.delete(self.doc, rec.pno, rec.xref)
        self.view.annotSelected.emit(None)

    # ------------------------------------------------------------- finishing
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
        if d["rec"].kind == "Stamp" and h in (0, 2, 4, 6) and r.width() > 0 and r.height() > 0:
            s = max(nr.width() / r.width(), nr.height() / r.height())
            w, hh = r.width() * s, r.height() * s
            ax = r.right() if h in (0, 6) else r.left()
            ay = r.bottom() if h in (0, 2) else r.top()
            x0 = ax - w if h in (0, 6) else ax
            y0 = ay - hh if h in (0, 2) else ay
            nr = QRectF(x0, y0, w, hh)
        if nr.width() < 8:
            nr.setWidth(8)
        if nr.height() < 8:
            nr.setHeight(8)
        return nr

    def _finish_drag(self, d):
        rec = d["rec"]
        pno = rec.pno
        try:
            if d["mode"] == "move":
                a = self._view_to_text(pno, d["start"])
                b = self._view_to_text(pno, d["cur"])
                an.move(self.doc, pno, rec.xref, b.x - a.x, b.y - a.y)
            elif d["mode"] == "resize":
                vr = self._drag_rect(d)
                p0 = self._view_to_text(pno, vr.topLeft())
                p1 = self._view_to_text(pno, vr.bottomRight())
                an.resize(self.doc, pno, rec.xref, pymupdf.Rect(p0, p1).normalize())
            elif d["mode"] == "line":
                v = list(rec.vertices[:2])
                v[d["handle"]] = tuple(self._clamp(pno, self._view_to_text(pno, d["cur"])))
                an.set_line_points(self.doc, pno, rec.xref, v[0], v[1])
        except Exception as exc:
            self.view.message.emit(f"Could not change the comment: {exc}")
        self.select(pno, rec.xref)

    def _constrain(self, m, pts):
        a, b = pts
        if not m.get("shift"):
            return a, b
        dx, dy = b.x - a.x, b.y - a.y
        if m["tool"] in ("line", "arrow"):
            ang = round(math.atan2(dy, dx) / (math.pi / 4)) * (math.pi / 4)
            ln = math.hypot(dx, dy)
            return a, pymupdf.Point(a.x + ln * math.cos(ang), a.y + ln * math.sin(ang))
        s = max(abs(dx), abs(dy))
        return a, pymupdf.Point(a.x + math.copysign(s, dx or 1), a.y + math.copysign(s, dy or 1))

    def _finish_create(self, m):
        tool, pno = m["tool"], m["pno"]
        if tool == "eraser":
            try:
                for epno, xrefs in m["marked"].items():
                    an.delete(self.doc, epno, list(xrefs))
            except Exception as exc:
                self.view.message.emit(f"Could not erase: {exc}")
            return
        st = self._style(tool)
        author = self._author()
        start, cur = m["start"], m["cur"]
        dragged = (m["end_pos"] - m["start_pos"]).manhattanLength() > 6 if "start_pos" in m else False
        if tool == REDACT:
            if dragged:
                from paperleaf.core import protect
                try:
                    protect.mark_rects(self.doc, pno, [pymupdf.Rect(start, cur).normalize()],
                                       self._env("redact_style"), author)
                except Exception as exc:
                    self.view.message.emit(f"Could not mark the area: {exc}")
            return
        rot = self._rot(pno)
        page_start = start * rot  # page space follows the page's own orientation
        xref = None
        try:
            if tool == "note":
                xref = an.add_note(self.doc, pno, (start.x - 10, start.y - 10), "", st, author)
                self._created(tool, pno, xref)
                self.view.annotActivated.emit(pno, xref)
                return
            if tool in ("freetext", "filltext", "callout"):
                self._begin_new_text(m, st, dragged)
                return
            if tool in ("pen", "highlighter"):
                pts = m["pts"] if len(m["pts"]) > 1 else [m["pts"][0], m["pts"][0] + (0.5, 0.5)]
                xref = an.add_ink(self.doc, pno, [[(p.x, p.y) for p in pts]], st, author,
                                  label="Add highlighter mark" if tool == "highlighter" else "Add drawing")
            elif tool in DRAG_SHAPES:
                if not dragged:
                    return
                a, b = self._constrain(m, (start, cur))
                xref = an.add_shape(self.doc, pno, tool, [(a.x, a.y), (b.x, b.y)], st, author)
            elif tool in ("stamp", "signature"):
                item = self._env("stamp" if tool == "stamp" else "signature")
                if not item:
                    self.view.message.emit("Choose a stamp first." if tool == "stamp" else
                                           "Create a signature first (Sign ▸ Add signature).")
                    return
                png, w, h, subject = item
                if dragged:
                    box = pymupdf.Rect(page_start, cur * rot).normalize()
                    s = min(box.width / w, box.height / h)
                    w, h = w * s, h * s
                    r = pymupdf.Rect(box.x0, box.y0, box.x0 + w, box.y0 + h)
                else:
                    r = pymupdf.Rect(page_start.x - w / 2, page_start.y - h / 2,
                                     page_start.x + w / 2, page_start.y + h / 2)
                r = self._clamp_rect(pno, self._page_rect_to_text(pno, r))
                xref = an.add_image_stamp(self.doc, pno, r, png, author, subject=subject,
                                          label="Add signature" if tool == "signature" else "Add stamp")
            elif tool in SYMBOL_TOOLS:
                xref = an.add_symbol(self.doc, pno, (start.x, start.y), tool, st, author)
            elif tool == "date":
                text = QLocale.system().toString(QDate.currentDate(), QLocale.FormatType.ShortFormat)
                fs = st.fontsize
                r = an.fit_text_rect((page_start.x, page_start.y - fs * 0.75), text, fs)
                r = self._clamp_rect(pno, self._page_rect_to_text(pno, r))
                xref = an.add_freetext(self.doc, pno, r, text, st, author, border=False,
                                       subject="Fill & Sign", label="Add date")
        except Exception as exc:
            self.view.message.emit(f"Could not add the comment: {exc}")
            return
        if xref:
            self._created(tool, pno, xref)

    def _created(self, tool, pno, xref):
        if tool not in STAY_ACTIVE:
            self.select(pno, xref)
        self.view.annotCreated.emit(tool, pno, xref)

    # polygons -----------------------------------------------------------------
    def _poly_click(self, pos):
        m = self.make
        tp = self._clamp(m["pno"], self._view_to_text(m["pno"], pos))
        first = self._text_to_view(m["pno"], m["pts"][0].x, m["pts"][0].y)
        if len(m["pts"]) >= 3 and (pos - first).manhattanLength() < 10 and m["tool"] != "polyline":
            self._finish_poly()
            return
        m["pts"].append(tp)
        m["cur"] = tp
        self.view.viewport().update()

    def _finish_poly(self):
        m, self.make = self.make, None
        pts = m["pts"]
        # a double-click adds the same point twice
        clean = [pts[0]]
        for p in pts[1:]:
            if math.hypot(p.x - clean[-1].x, p.y - clean[-1].y) * self._scale() > 3:
                clean.append(p)
        need = 2 if m["tool"] == "polyline" else 3
        if len(clean) >= need:
            st = self._style(m["tool"])
            try:
                xref = an.add_shape(self.doc, m["pno"], m["tool"], [(p.x, p.y) for p in clean], st, self._author())
                self._created(m["tool"], m["pno"], xref)
            except Exception as exc:
                self.view.message.emit(f"Could not add the shape: {exc}")
        self.view.viewport().update()

    # eraser ------------------------------------------------------------------
    def _erase_at(self, pos):
        hit = self._hit_page(pos)
        if not hit:
            return
        pno, tp = hit
        for rec in an.page_annots(self.doc, pno):
            if rec.kind == "Ink" and an.hit(rec, tp.x, tp.y, 6 / self._scale()):
                self.make["marked"].setdefault(pno, set()).add(rec.xref)
        self.view.viewport().update()

    # text boxes ----------------------------------------------------------------
    def _begin_new_text(self, m, st, dragged):
        pno, tool = m["pno"], m["tool"]
        rot = self._rot(pno)
        ps, pc = m["start"] * rot, m["cur"] * rot
        fs = st.fontsize
        callout = None
        if tool == "callout":
            box_tl = pc if dragged else ps + (60, -60)
            r = pymupdf.Rect(box_tl.x, box_tl.y, box_tl.x + 170, box_tl.y + fs * 1.25 + fs * 0.45)
            user_rect = False
            callout_page = ps
        elif dragged:
            r = pymupdf.Rect(ps, pc).normalize()
            user_rect = True
        else:
            width = 200 if tool == "freetext" else fs * 6
            r = pymupdf.Rect(ps.x, ps.y - fs * 0.75, ps.x + width, ps.y - fs * 0.75 + fs * 1.7)
            user_rect = False
        r = self._clamp_rect(pno, r)
        if tool == "callout":
            callout = (callout_page, pymupdf.Point(r.x0, (r.y0 + r.y1) / 2))
        self._open_editor(pno, None, r, st, tool, user_rect, callout, "")

    def edit_text(self, rec):
        st = an.Style(color=rec.text_color or (0, 0, 0), fill=rec.fill, width=rec.width,
                      opacity=rec.opacity, fontsize=rec.fontsize or 12)
        page_rect = (rec.rect * self._rot(rec.pno)).normalize()
        self._open_editor(rec.pno, rec.xref, page_rect, st, "freetext", True, None, rec.contents)

    def _open_editor(self, pno, xref, page_rect, st, tool, user_rect, callout, text):
        """``page_rect`` is in page space (upright as the page is displayed)."""
        if self.editor is not None:
            self.editor.finish(True)
        vr = self.view.page_rect_to_view(pno, page_rect)
        ed = InlineEditor(self.view.viewport(), self._commit_editor, self._cancel_editor)
        font = QFont("Arial")
        font.setPixelSize(max(6, round(st.fontsize * self._scale())))
        ed.setFont(font)
        bg = qcolor(st.fill, 1) if st.fill else QColor(255, 255, 255, 235)
        fg = qcolor(st.color or (0, 0, 0))
        ed.setStyleSheet(f"QPlainTextEdit {{ background: {bg.name(QColor.NameFormat.HexArgb)}; "
                         f"color: {fg.name()}; border: 1px dashed {SEL_COLOR.name()}; }}")
        ed.setPlainText(text)
        ed.setGeometry(vr.toRect().adjusted(0, 0, 2, 2))
        ed.setMinimumSize(40, font.pixelSize() + 10)
        ed.textChanged.connect(lambda e=ed: self._grow_editor(e))
        self.editor = ed
        self.editor_ctx = dict(pno=pno, xref=xref, page_rect=page_rect, style=st, tool=tool,
                               user_rect=user_rect, callout=callout, original=text)
        ed.show()
        ed.setFocus()
        cur = ed.textCursor()
        cur.movePosition(cur.MoveOperation.End)
        ed.setTextCursor(cur)
        self._grow_editor(ed)
        self.view.viewport().update()

    def _grow_editor(self, ed):
        ctx = self.editor_ctx
        if ctx is None:
            return
        fm = ed.fontMetrics()
        # QPlainTextEdit's layout reports its height in lines, not pixels
        lines = max(1.0, ed.document().documentLayout().documentSize().height())
        doc_h = lines * fm.lineSpacing() + 2 * ed.document().documentMargin()
        if not ctx["user_rect"]:
            longest = max((fm.horizontalAdvance(line) for line in (ed.toPlainText() or " ").split("\n")), default=0)
            w = max(ed.minimumWidth(), int(longest + fm.averageCharWidth() * 2 + 8), ed.width())
            ed.resize(w, ed.height())
        h = max(int(doc_h + 6), ed.height())
        if h != ed.height():
            ed.resize(ed.width(), h)

    def _needed_rect(self, ctx, text) -> pymupdf.Rect:
        """Rect (page space) big enough for ``text`` at the box's font size."""
        st = ctx["style"]
        r = pymupdf.Rect(ctx["page_rect"])
        fs = st.fontsize
        font = QFont("Arial")
        font.setPixelSize(max(1, round(fs * 10)))  # measure at 10x for precision
        fm = QFontMetricsF(font)
        pad = fs * 0.9
        if ctx["user_rect"]:
            avail = max(10.0, r.width - pad) * 10
            br = fm.boundingRect(QRectF(0, 0, avail, 1e7), int(Qt.TextFlag.TextWordWrap), text or " ")
            h = br.height() / 10 + fs * 0.6
            return pymupdf.Rect(r.x0, r.y0, r.x1, r.y0 + max(r.height, h))
        lines = (text or " ").split("\n")
        w = max(an.text_width(line, fs) for line in lines) + pad
        h = len(lines) * fs * 1.25 + fs * 0.45
        return pymupdf.Rect(r.x0, r.y0, r.x0 + max(w, fs * 2), r.y0 + h)

    def _close_editor(self):
        ed, self.editor = self.editor, None
        ctx, self.editor_ctx = self.editor_ctx, None
        if ed is not None:
            ed.hide()
            ed.deleteLater()
        self.view.setFocus()
        self.view.viewport().update()
        return ctx

    def _cancel_editor(self, _text):
        self._close_editor()

    def _commit_editor(self, text):
        ctx = self._close_editor()
        if ctx is None:
            return
        text = text.rstrip()
        pno, xref = ctx["pno"], ctx["xref"]
        rect = self._clamp_rect(pno, self._page_rect_to_text(pno, self._needed_rect(ctx, text)))
        try:
            if xref is None:
                if not text:
                    return
                tool = ctx["tool"]
                callout = None
                if ctx["callout"]:
                    inv = ~self._rot(pno)
                    callout = tuple(p * inv for p in ctx["callout"])
                    # re-attach the line to the (possibly resized) box
                    callout = (callout[0], pymupdf.Point(rect.x0, (rect.y0 + rect.y1) / 2))
                labels = {"freetext": "Add text box", "callout": "Add callout", "filltext": "Add text"}
                xref = an.add_freetext(self.doc, pno, rect, text, ctx["style"], self._author(),
                                       border=tool in ("freetext", "callout"), callout=callout,
                                       subject="Fill & Sign" if tool == "filltext" else None,
                                       label=labels.get(tool, "Add text"))
                self._created(tool, pno, xref)
            else:
                if text == ctx["original"]:
                    return
                if not text:
                    an.delete(self.doc, pno, xref)
                    return
                an.set_freetext(self.doc, pno, xref, text, rect)
                self.select(pno, xref)
        except Exception as exc:
            self.view.message.emit(f"Could not save the text: {exc}")

    def view_changed(self):
        """Scroll/zoom: finish editing so the editor never drifts off its box."""
        if self.editor is not None:
            self.editor.finish(True)

    # ----------------------------------------------------------------- paint
    def paint(self, p):
        rec = self.record()
        if rec is not None and not (self.drag and self.drag["mode"] in ("move", "resize")):
            self._paint_selection(p, rec)
        if self.drag and self.drag["moved"]:
            self._paint_drag(p, self.drag)
        if self.make:
            self._paint_make(p, self.make)

    def _paint_selection(self, p, rec):
        r = self.view.text_rect_to_view(rec.pno, rec.rect).adjusted(-2, -2, 2, 2)
        pen = QPen(SEL_COLOR, 1, Qt.PenStyle.DashLine)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        if rec.kind != "Line":
            p.drawRect(r)
        p.setPen(QPen(SEL_COLOR, 1))
        p.setBrush(QBrush(QColor("white")))
        for hp in self._handles(rec):
            p.drawRect(QRectF(hp.x() - HANDLE_PX, hp.y() - HANDLE_PX, HANDLE_PX * 2, HANDLE_PX * 2))

    def _paint_drag(self, p, d):
        pen = QPen(SEL_COLOR, 1.5, Qt.PenStyle.DashLine)
        p.setPen(pen)
        p.setBrush(QBrush(QColor(0, 120, 215, 25)))
        if d["mode"] == "move":
            delta = d["cur"] - d["start"]
            p.drawRect(d["vrect"].translated(delta))
        elif d["mode"] == "resize":
            p.drawRect(self._drag_rect(d))
        else:
            rec = d["rec"]
            pts = [self._text_to_view(rec.pno, *v) for v in rec.vertices[:2]]
            pts[d["handle"]] = d["cur"]
            p.drawLine(pts[0], pts[1])

    def _paint_make(self, p, m):
        tool = m["tool"]
        if tool == "eraser":
            p.setPen(QPen(QColor(220, 0, 0), 1.5, Qt.PenStyle.DashLine))
            p.setBrush(QBrush(QColor(220, 0, 0, 40)))
            for pno, xrefs in m["marked"].items():
                for x in xrefs:
                    rec = an.find(self.doc, pno, x)
                    if rec:
                        p.drawRect(self.view.text_rect_to_view(pno, rec.rect))
            return
        pno = m["pno"]
        st = self._style(tool)
        scale = self._scale()
        color = qcolor(st.color or (0, 0, 0), st.opacity)
        pen = QPen(color, max(1.0, st.width * scale))
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        if st.dashed:
            pen.setStyle(Qt.PenStyle.DashLine)
        p.setPen(pen)
        p.setBrush(QBrush(qcolor(st.fill, st.opacity)) if st.fill and tool in ("rect", "ellipse", "polygon", "cloud")
                   else Qt.BrushStyle.NoBrush)
        v = lambda pt: self._text_to_view(pno, pt.x, pt.y)  # noqa: E731
        if tool in ("pen", "highlighter"):
            path = QPainterPath(v(m["pts"][0]))
            for pt in m["pts"][1:]:
                path.lineTo(v(pt))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawPath(path)
        elif tool in DRAG_SHAPES:
            a, b = self._constrain(m, (m["start"], m["cur"]))
            if tool == "rect":
                p.drawRect(QRectF(v(a), v(b)).normalized())
            elif tool == "ellipse":
                p.drawEllipse(QRectF(v(a), v(b)).normalized())
            else:
                p.drawLine(v(a), v(b))
                if tool == "arrow":
                    va, vb = v(a), v(b)
                    ang = math.atan2(vb.y() - va.y(), vb.x() - va.x())
                    size = max(8.0, st.width * scale * 4)
                    for s in (-1, 1):
                        p.drawLine(vb, QPointF(vb.x() - size * math.cos(ang + s * 0.45),
                                               vb.y() - size * math.sin(ang + s * 0.45)))
        elif tool in POLY_TOOLS:
            pts = [v(pt) for pt in m["pts"]] + [v(m["cur"])]
            path = QPainterPath(pts[0])
            for q in pts[1:]:
                path.lineTo(q)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawPath(path)
            p.setPen(QPen(SEL_COLOR, 1))
            for q in pts[:-1]:
                p.drawEllipse(q, 3, 3)
        elif tool == REDACT:
            p.setPen(QPen(QColor(220, 0, 0), 1.5, Qt.PenStyle.DashLine))
            p.setBrush(QBrush(QColor(0, 0, 0, 60)))
            p.drawRect(QRectF(v(m["start"]), v(m["cur"])).normalized())
        elif tool in ("freetext", "filltext", "callout", "stamp", "signature"):
            if (m["cur"].x, m["cur"].y) != (m["start"].x, m["start"].y):
                p.setPen(QPen(SEL_COLOR, 1, Qt.PenStyle.DashLine))
                p.setBrush(Qt.BrushStyle.NoBrush)
                if tool == "callout":
                    p.drawLine(v(m["start"]), v(m["cur"]))
                else:
                    p.drawRect(QRectF(v(m["start"]), v(m["cur"])).normalized())
