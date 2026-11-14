"""DocumentView: the scrolling page canvas.

Pages are rendered lazily in tiles on the GUI thread, a few milliseconds at a
time, so the UI stays responsive. A small low-resolution "preview" of each page
is drawn as a placeholder while sharp tiles are being produced.
"""
from __future__ import annotations

import itertools
import math
import time
from collections import OrderedDict

import pymupdf
from PySide6.QtCore import QEvent, QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QCursor, QGuiApplication, QImage, QPainter, QPalette, QPen, QPixmap
from PySide6.QtWidgets import QAbstractScrollArea, QApplication, QMenu, QToolTip

from paperleaf.core.document import PdfDocument
from paperleaf.viewer.annot_tools import ANNOT_TOOLS, TEXT_TOOLS, AnnotController
from paperleaf.viewer.form_tools import FormController
from paperleaf.viewer.edit_tools import EditController

PT = 96.0 / 72.0          # logical pixels per PDF point at 100% zoom
GAP = 10.0                # space between pages
MARGIN = 12.0             # space around the page area
TILE = 512                # tile size in device pixels
PREVIEW_PX = 700          # long side of placeholder previews, device px
ZOOM_MIN, ZOOM_MAX = 0.05, 64.0
ZOOM_STEPS = [0.0833, 0.125, 0.25, 0.3333, 0.5, 0.6667, 0.75, 1.0, 1.25, 1.5, 2.0,
              3.0, 4.0, 6.0, 8.0, 12.0, 16.0, 24.0, 32.0, 64.0]

LAYOUTS = ("single", "single_continuous", "two", "two_continuous")


class Tool:
    SELECT, HAND, MARQUEE, SNAPSHOT = "select", "hand", "marquee", "snapshot"


def pix_to_pixmap(pix: pymupdf.Pixmap) -> QPixmap:
    if pix.n == 1:
        fmt = QImage.Format.Format_Grayscale8
    elif pix.n == 4:
        fmt = QImage.Format.Format_RGBA8888
    else:
        fmt = QImage.Format.Format_RGB888
    img = QImage(pix.samples, pix.width, pix.height, pix.stride, fmt)
    return QPixmap.fromImage(img.copy())


def pix_to_qimage(pix: pymupdf.Pixmap) -> QImage:
    fmt = {1: QImage.Format.Format_Grayscale8, 4: QImage.Format.Format_RGBA8888}.get(
        pix.n, QImage.Format.Format_RGB888)
    return QImage(pix.samples, pix.width, pix.height, pix.stride, fmt).copy()


class _PixCache(OrderedDict):
    """LRU cache of QPixmaps bounded by total bytes."""

    def __init__(self, max_bytes: int):
        super().__init__()
        self.max_bytes = max_bytes
        self.bytes = 0

    def get(self, key):
        item = super().get(key)
        if item is not None:
            self.move_to_end(key)
        return item

    def put(self, key, value, nbytes):
        if key in self:
            self.bytes -= self[key][-1]
            del self[key]
        self[key] = (*value, nbytes)
        self.bytes += nbytes
        while self.bytes > self.max_bytes and len(self) > 1:
            _, old = self.popitem(last=False)
            self.bytes -= old[-1]

    def clear(self):
        super().clear()
        self.bytes = 0

    def drop_owner(self, owner):
        for key in [k for k in self.keys() if k[0] == owner]:
            self.bytes -= self.pop(key)[-1]

    def drop_owner_pages(self, owner, pages):
        pages = set(pages)
        for key in [k for k in self.keys() if k[0] == owner and k[1] in pages]:
            self.bytes -= self.pop(key)[-1]


# Shared by all open documents so memory stays bounded however many tabs are open.
_TILES = _PixCache(256 * 1024 * 1024)
_PREVIEWS = _PixCache(64 * 1024 * 1024)
_view_ids = itertools.count(1)


class DocumentView(QAbstractScrollArea):
    currentPageChanged = Signal(int)
    zoomChanged = Signal(float)
    linkActivated = Signal(dict)
    selectionChanged = Signal(bool)
    toolChanged = Signal(str)
    layoutModeChanged = Signal()
    historyChanged = Signal()
    message = Signal(str)
    annotSelected = Signal(object)              # AnnotRecord | None
    annotActivated = Signal(int, int)           # page, xref (double-click: edit)
    annotCreated = Signal(str, int, int)        # tool, page, xref
    markupRequested = Signal(str)               # tool: make markup from the text selection
    searchProgress = Signal(int, bool)          # total hits, finished
    searchPageHits = Signal(int, list)          # page, [(start, end), ...]
    currentHitChanged = Signal(int, int)        # ordinal (0-based), total
    fieldSelected = Signal(list)                # Prepare Form: selected [Field]
    fieldActivated = Signal(int, int)           # Prepare Form: page, xref (double-click: properties)
    fieldCreated = Signal(str, int, int)        # tool, page, xref
    signatureFieldClicked = Signal(int, int)    # page, xref
    formAction = Signal(dict)                   # push button action (never JavaScript)
    pagesChanged = Signal()                     # pages were added, removed, moved, rotated or resized

    def __init__(self, doc: PdfDocument, parent=None):
        super().__init__(parent)
        self.doc = doc
        self.zoom = 1.0
        self.zoom_mode: str | None = "fit_width"
        self.rotation = 0
        self.layout_mode = "single_continuous"
        self.cover = False
        self.night = False
        self.tool = Tool.SELECT
        self._temp_hand = False
        self._rects: list[QRectF] = [QRectF() for _ in range(doc.page_count)]
        self._content_w = self._content_h = 0.0
        self._current = 0
        self._key = next(_view_ids)
        self._tiles = _TILES
        self._previews = _PREVIEWS
        self._queue: list = []
        self._render_timer = QTimer(self)
        self._render_timer.setSingleShot(True)
        self._render_timer.setInterval(0)
        self._render_timer.timeout.connect(self._process_queue)
        self._laid_out = False
        self._pending_state = None
        self._wheel_acc = 0
        # selection: ((page, idx), (page, idx)) anchor/head
        self._sel_anchor = None
        self._sel_head = None
        self._selecting = False
        # interaction
        self._press_pos = None
        self._press_link = None
        self._panning = False
        self._pan_last = None
        self._rubber = None            # QRectF in content coords
        self._rubber_origin = None
        self._last_dbl_time = 0.0
        self._autoscroll = QTimer(self)
        self._autoscroll.setInterval(30)
        self._autoscroll.timeout.connect(self._do_autoscroll)
        self._last_mouse = QPointF()
        # history
        self._back: list = []
        self._fwd: list = []
        # search
        self._hits: dict[int, list] = {}
        self._hit_cur = None
        self._search_rx = None
        self._search_page = 0
        self._search_done = True
        self._search_jump = False
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(0)
        self._search_timer.timeout.connect(self._search_step)
        self.context_menu_builder = None
        self._pick = None              # pick_area() callback
        # page "generations": bumped when a page's content changes so cached
        # tiles of the old content are never shown again
        self._gen: dict[int, int] = {}
        self._gen_all = 0
        self._sync_pages: set | None = set()
        self._structure = doc.structure
        self.annots = AnnotController(self)
        self.forms = FormController(self)
        self.editing = EditController(self)
        doc.listeners.append(self._doc_changed)

        self.setFrameShape(QAbstractScrollArea.Shape.NoFrame)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMouseTracking(True)
        self.viewport().setMouseTracking(True)
        self.viewport().setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent)
        self.verticalScrollBar().valueChanged.connect(self._on_scrolled)
        self.horizontalScrollBar().valueChanged.connect(self._on_scrolled)

    # ------------------------------------------------------------------ layout
    @property
    def continuous(self) -> bool:
        return self.layout_mode.endswith("continuous")

    @property
    def two_up(self) -> bool:
        return self.layout_mode.startswith("two")

    def _rows(self) -> list[list[int]]:
        n = self.doc.page_count
        if not self.two_up:
            return [[i] for i in range(n)]
        rows = []
        start = 0
        if self.cover and n:
            rows.append([0])
            start = 1
        for i in range(start, n, 2):
            rows.append([i, i + 1] if i + 1 < n else [i])
        return rows

    def _row_of(self, pno: int, rows=None) -> int:
        rows = rows or self._rows()
        for ri, row in enumerate(rows):
            if pno in row:
                return ri
        return 0

    def _unit_size(self, pno: int) -> tuple[float, float]:
        r = self.doc.page_rects[pno]
        w, h = r.width * PT, r.height * PT
        if self.rotation in (90, 270):
            w, h = h, w
        return w, h

    def _fit_zoom(self, mode: str) -> float:
        vw = max(50, self.viewport().width())
        vh = max(50, self.viewport().height())
        rows = self._rows()
        # like Acrobat, fit to the page(s) being viewed, not the widest page in the file
        cand = [rows[self._row_of(self._current, rows)]] if rows else []
        if not cand:
            return 1.0
        if self.two_up:
            maxw = max(self._unit_size(p)[0] for row in cand for p in row)
            unit_w = maxw * 2
            avail_w = vw - 2 * MARGIN - GAP
        else:
            unit_w = max(self._unit_size(row[0])[0] for row in cand)
            avail_w = vw - 2 * MARGIN
        zw = avail_w / unit_w
        if mode == "fit_width":
            return max(ZOOM_MIN, min(ZOOM_MAX, zw))
        row = rows[self._row_of(self._current, rows)]
        unit_h = max(self._unit_size(p)[1] for p in row)
        zh = (vh - 2 * MARGIN) / unit_h
        return max(ZOOM_MIN, min(ZOOM_MAX, min(zw, zh)))

    def _relayout(self):
        if self.zoom_mode in ("fit_width", "fit_page"):
            self.zoom = self._fit_zoom(self.zoom_mode)
        vw = self.viewport().width()
        vh = self.viewport().height()
        z = self.zoom
        rows = self._rows()
        n = self.doc.page_count
        rects = [QRectF() for _ in range(n)]
        sizes = {}
        if self.continuous:
            shown = rows
        else:
            shown = [rows[self._row_of(self._current, rows)]] if rows else []
        for row in shown:
            for p in row:
                w, h = self._unit_size(p)
                sizes[p] = (w * z, h * z)
        if self.two_up:
            half = max((sizes[p][0] for p in sizes), default=0)
            need_w = half * 2 + GAP + 2 * MARGIN
        else:
            need_w = max((sizes[p][0] for p in sizes), default=0) + 2 * MARGIN
        cw = max(float(vw), need_w)
        center = cw / 2
        y = MARGIN
        for ri, row in enumerate(shown):
            rh = max(sizes[p][1] for p in row)
            if self.two_up:
                if len(row) == 2:
                    a, b = row
                    rects[a] = QRectF(center - GAP / 2 - sizes[a][0], y + (rh - sizes[a][1]) / 2, *sizes[a])
                    rects[b] = QRectF(center + GAP / 2, y + (rh - sizes[b][1]) / 2, *sizes[b])
                else:
                    p = row[0]
                    on_right = self.cover and p == 0
                    x = center + GAP / 2 if on_right else center - GAP / 2 - sizes[p][0]
                    rects[p] = QRectF(x, y + (rh - sizes[p][1]) / 2, *sizes[p])
            else:
                p = row[0]
                rects[p] = QRectF(center - sizes[p][0] / 2, y, *sizes[p])
            y += rh + GAP
        ch = y - GAP + MARGIN
        if ch < vh:
            dy = (vh - ch) / 2
            for r in rects:
                if not r.isNull():
                    r.translate(0, dy)
            ch = float(vh)
        self._rects = rects
        self._content_w, self._content_h = cw, ch
        hb, vb = self.horizontalScrollBar(), self.verticalScrollBar()
        for bar, total, page in ((hb, cw, vw), (vb, ch, vh)):
            bar.blockSignals(True)
            bar.setRange(0, max(0, int(math.ceil(total - page))))
            bar.setPageStep(max(1, page))
            bar.setSingleStep(48)
            bar.blockSignals(False)
        self._laid_out = True
        self.forms.view_changed()
        self.editing.view_changed()
        self.viewport().update()

    # -------------------------------------------------------- coordinate maps
    def _offset(self) -> QPointF:
        return QPointF(self.horizontalScrollBar().value(), self.verticalScrollBar().value())

    def _view_matrix(self) -> pymupdf.Matrix:
        s = self.zoom * PT
        return pymupdf.Matrix(s, s).prerotate(self.rotation)

    def page_to_content(self, pno: int, x: float, y: float) -> QPointF:
        """Page-space point -> content coordinates."""
        m = self._view_matrix()
        bbox = self.doc.page_rects[pno] * m
        p = pymupdf.Point(x, y) * m
        r = self._rects[pno]
        return QPointF(r.left() + p.x - bbox.x0, r.top() + p.y - bbox.y0)

    def content_to_page(self, pno: int, pt: QPointF) -> pymupdf.Point:
        m = self._view_matrix()
        bbox = self.doc.page_rects[pno] * m
        r = self._rects[pno]
        return pymupdf.Point(pt.x() - r.left() + bbox.x0, pt.y() - r.top() + bbox.y0) * ~m

    def page_rect_to_view(self, pno: int, rect: pymupdf.Rect) -> QRectF:
        m = self._view_matrix()
        bbox = self.doc.page_rects[pno] * m
        rr = rect * m
        r = self._rects[pno]
        off = self._offset()
        return QRectF(r.left() + rr.x0 - bbox.x0 - off.x(), r.top() + rr.y0 - bbox.y0 - off.y(),
                      rr.width, rr.height)

    def text_rect_to_view(self, pno: int, rect) -> QRectF:
        return self.page_rect_to_view(pno, pymupdf.Rect(rect) * self.doc.rotation_matrix(pno))

    def _page_at(self, content_pt: QPointF, nearest=False):
        best, best_d = None, None
        for i, r in enumerate(self._rects):
            if r.isNull():
                continue
            if r.contains(content_pt):
                return i
            if nearest:
                dx = max(r.left() - content_pt.x(), 0, content_pt.x() - r.right())
                dy = max(r.top() - content_pt.y(), 0, content_pt.y() - r.bottom())
                d = dy * 2 + dx
                if best_d is None or d < best_d:
                    best, best_d = i, d
        return best

    def _visible_pages(self) -> list[int]:
        off = self._offset()
        vis = QRectF(off.x(), off.y(), self.viewport().width(), self.viewport().height())
        return [i for i, r in enumerate(self._rects) if not r.isNull() and r.intersects(vis)]

    # ------------------------------------------------------------- navigation
    def current_page(self) -> int:
        return self._current

    def _on_scrolled(self):
        self.annots.view_changed()
        self.forms.view_changed()
        self.editing.view_changed()
        if self.continuous:
            off = self._offset()
            vh = self.viewport().height()
            vis = QRectF(off.x(), off.y(), self.viewport().width(), vh)
            best, best_a = self._current, -1.0
            for i in self._visible_pages():
                inter = self._rects[i].intersected(vis)
                a = inter.width() * inter.height()
                if a > best_a + 1:
                    best, best_a = i, a
            if best != self._current:
                self._current = best
                self.currentPageChanged.emit(best)
        self.viewport().update()

    def _scroll_to_content(self, x: float | None, y: float | None):
        if x is not None:
            self.horizontalScrollBar().setValue(int(round(x)))
        if y is not None:
            self.verticalScrollBar().setValue(int(round(y)))

    def go_to_page(self, pno: int, point=None, record=True, top_offset: float = None):
        if self.doc.page_count == 0:
            return
        pno = max(0, min(self.doc.page_count - 1, pno))
        if record:
            self._push_history()
        changed = pno != self._current
        self._current = pno
        if not self.continuous:
            self._relayout()
        r = self._rects[pno]
        if r.isNull():
            self._relayout()
            r = self._rects[pno]
        if point is not None:
            c = self.page_to_content(pno, point.x, point.y)
            self._scroll_to_content(None, c.y() - 10)
            if c.x() > self.horizontalScrollBar().value() + self.viewport().width() - 20:
                self._scroll_to_content(c.x() - 20, None)
        elif top_offset is not None:
            self._scroll_to_content(None, r.top() + top_offset * r.height())
        else:
            self._scroll_to_content(None, r.top() - (GAP if self.continuous else MARGIN))
        if point is None:
            # keep the page centred horizontally (matters when other pages are wider)
            self._scroll_to_content(r.center().x() - self.viewport().width() / 2, None)
        if changed:
            self.currentPageChanged.emit(pno)
        self.viewport().update()

    def next_page(self):
        rows = self._rows()
        ri = self._row_of(self._current, rows)
        if self.continuous and not self.two_up:
            if self._current + 1 < self.doc.page_count:
                self.go_to_page(self._current + 1, record=False)
        elif ri + 1 < len(rows):
            self.go_to_page(rows[ri + 1][0], record=False)

    def prev_page(self):
        rows = self._rows()
        ri = self._row_of(self._current, rows)
        if self.continuous and not self.two_up:
            r = self._rects[self._current]
            # if the page top is scrolled off, go to the top of this page first
            if r.top() < self.verticalScrollBar().value() - 2 * GAP:
                self.go_to_page(self._current, record=False)
            elif self._current > 0:
                self.go_to_page(self._current - 1, record=False)
        elif ri > 0:
            self.go_to_page(rows[ri - 1][0], record=False)

    def first_page(self):
        self.go_to_page(0)

    def last_page(self):
        self.go_to_page(self.doc.page_count - 1)

    # history ---------------------------------------------------------------
    def _location(self):
        r = self._rects[self._current] if self._rects else QRectF()
        frac = 0.0
        if not r.isNull() and r.height() > 0:
            frac = (self.verticalScrollBar().value() - r.top()) / r.height()
        return (self._current, frac)

    def _push_history(self):
        if not self._laid_out:
            return
        loc = self._location()
        if self._back and self._back[-1][0] == loc[0] and abs(self._back[-1][1] - loc[1]) < 0.02:
            return
        self._back.append(loc)
        self._back = self._back[-100:]
        self._fwd.clear()
        self.historyChanged.emit()

    def can_go_back(self):
        return bool(self._back)

    def can_go_forward(self):
        return bool(self._fwd)

    def go_back(self):
        if not self._back:
            return
        self._fwd.append(self._location())
        pno, frac = self._back.pop()
        self.go_to_page(pno, record=False, top_offset=frac)
        self.historyChanged.emit()

    def go_forward(self):
        if not self._fwd:
            return
        self._back.append(self._location())
        pno, frac = self._fwd.pop()
        self.go_to_page(pno, record=False, top_offset=frac)
        self.historyChanged.emit()

    # ------------------------------------------------------------------ zoom
    def _anchor_info(self, anchor: QPointF):
        c = anchor + self._offset()
        pno = self._page_at(c, nearest=True)
        if pno is None:
            return None
        return pno, self.content_to_page(pno, c)

    def _restore_anchor(self, info, anchor: QPointF):
        if info is None:
            return
        pno, pt = info
        if self._rects[pno].isNull():
            return
        c = self.page_to_content(pno, pt.x, pt.y)
        self._scroll_to_content(c.x() - anchor.x(), c.y() - anchor.y())

    def _center(self) -> QPointF:
        return QPointF(self.viewport().width() / 2, self.viewport().height() / 2)

    def set_zoom(self, zoom: float, anchor: QPointF | None = None, mode: str | None = None):
        if anchor is None:
            anchor = self._center()
        info = self._anchor_info(anchor)
        self.zoom_mode = mode
        if mode is None:
            self.zoom = max(ZOOM_MIN, min(ZOOM_MAX, zoom))
        self._relayout()
        if mode == "fit_page" and not self.continuous:
            pass
        elif mode == "fit_page":
            self.go_to_page(self._current, record=False)
        else:
            self._restore_anchor(info, anchor)
        self.zoomChanged.emit(self.zoom)
        self._on_scrolled()

    def zoom_in(self, anchor=None):
        nxt = next((z for z in ZOOM_STEPS if z > self.zoom * 1.001), ZOOM_MAX)
        self.set_zoom(nxt, anchor)

    def zoom_out(self, anchor=None):
        prv = next((z for z in reversed(ZOOM_STEPS) if z < self.zoom * 0.999), ZOOM_MIN)
        self.set_zoom(prv, anchor)

    def fit_width(self):
        self.set_zoom(0, mode="fit_width")

    def fit_page(self):
        self.set_zoom(0, mode="fit_page")

    def actual_size(self):
        self.set_zoom(1.0)

    # --------------------------------------------------- rotation / layout
    def rotate_view(self, delta: int):
        loc = self._location()
        self.rotation = (self.rotation + delta) % 360
        self._relayout()
        self.go_to_page(loc[0], record=False, top_offset=max(0.0, loc[1]))
        self.zoomChanged.emit(self.zoom)

    def set_layout(self, mode: str | None = None, cover: bool | None = None):
        loc = self._location()
        if mode is not None:
            self.layout_mode = mode
        if cover is not None:
            self.cover = cover
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn if self.continuous
                                        else Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._relayout()
        self.go_to_page(loc[0], record=False, top_offset=max(0.0, loc[1]) if self.continuous else None)
        self.zoomChanged.emit(self.zoom)
        self.layoutModeChanged.emit()

    def set_night(self, on: bool):
        self.night = on
        self.viewport().update()

    def set_tool(self, tool: str):
        self.annots.cancel()
        self.editing.cancel()
        self.forms.commit()
        self.forms.tool_changed()
        if tool != Tool.SELECT:
            self.annots.select(None)
        self.tool = tool
        self._update_cursor(self._last_mouse)
        self.toolChanged.emit(tool)

    def pick_area(self, callback, message: str = ""):
        """One-shot: the next box dragged on a page calls ``callback(pno, rect)``
        (unrotated page coordinates). Esc cancels with ``callback(None, None)``."""
        self._pick = callback
        self.viewport().setCursor(Qt.CursorShape.CrossCursor)
        if message:
            self.message.emit(message)

    def _finish_pick(self, rect: QRectF | None):
        cb, self._pick = self._pick, None
        self._update_cursor(self._last_mouse)
        if cb is None:
            return
        if rect is None or rect.width() < 8 or rect.height() < 8:
            cb(None, None)
            return
        pno = self._page_at(rect.center(), nearest=True)
        if pno is None:
            cb(None, None)
            return
        m = ~self.doc.rotation_matrix(pno)
        a = self.content_to_page(pno, rect.topLeft()) * m
        b = self.content_to_page(pno, rect.bottomRight()) * m
        r = pymupdf.Rect(a, b).normalize()
        bounds = (self.doc.page_rects[pno] * m).normalize()
        cb(pno, r & bounds)

    def effective_tool(self) -> int:
        return Tool.HAND if self._temp_hand else self.tool

    # ----------------------------------------------------------- view state
    def state(self) -> dict:
        pno, frac = self._location()
        return {"page": pno, "frac": round(frac, 4), "zoom": round(self.zoom, 4),
                "zoom_mode": self.zoom_mode, "rotation": self.rotation,
                "layout": self.layout_mode, "cover": self.cover}

    def restore_state(self, st: dict):
        """Apply a saved state; deferred until the widget has a size."""
        self._pending_state = dict(st)
        if self._laid_out and self.viewport().width() > 50:
            self._apply_pending()

    def _apply_pending(self):
        st, self._pending_state = self._pending_state, None
        if not st:
            return
        self.rotation = int(st.get("rotation", 0)) % 360
        if st.get("layout") in LAYOUTS:
            self.layout_mode = st["layout"]
        self.cover = bool(st.get("cover", False))
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn if self.continuous
                                        else Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        mode = st.get("zoom_mode")
        self.zoom_mode = mode if mode in ("fit_width", "fit_page") else None
        if self.zoom_mode is None and st.get("zoom"):
            self.zoom = max(ZOOM_MIN, min(ZOOM_MAX, float(st["zoom"])))
        page = int(st.get("page", 0))
        self._current = max(0, min(self.doc.page_count - 1, page))
        self._relayout()
        frac = st.get("frac")
        self.go_to_page(self._current, record=False,
                        top_offset=float(frac) if frac is not None and self.continuous else None)
        self.zoomChanged.emit(self.zoom)
        self.currentPageChanged.emit(self._current)
        self.layoutModeChanged.emit()

    # --------------------------------------------------------------- events
    def resizeEvent(self, event):
        first = not self._laid_out
        loc = self._location() if self._laid_out else None
        super().resizeEvent(event)
        self._relayout()
        if self._pending_state is not None:
            self._apply_pending()
        elif first:
            self.go_to_page(self._current, record=False)
            self.zoomChanged.emit(self.zoom)
        elif loc is not None and self.zoom_mode:
            self.go_to_page(loc[0], record=False, top_offset=max(0.0, loc[1]) if self.continuous else None)
            self.zoomChanged.emit(self.zoom)

    def paintEvent(self, event):
        vp = self.viewport()
        p = QPainter(vp)
        pal = self.palette()
        dark = pal.color(QPalette.ColorRole.Window).lightness() < 128
        bg = QColor(0x26, 0x26, 0x26) if dark else QColor(0xE6, 0xE6, 0xE6)
        p.fillRect(vp.rect(), bg)
        if not self._laid_out:
            return
        off = self._offset()
        vw, vh = vp.width(), vp.height()
        vrect = QRectF(0, 0, vw, vh)
        dpr = vp.devicePixelRatioF()
        s_dev = self.zoom * PT * dpr
        m_dev = pymupdf.Matrix(s_dev, s_dev).prerotate(self.rotation)
        zkey = round(s_dev, 5)
        needed = []
        paper = QColor(0x2A, 0x2A, 0x2A) if self.night else QColor(Qt.GlobalColor.white)
        border = QColor(0, 0, 0, 60) if not dark else QColor(0, 0, 0, 160)
        visible = self._visible_pages()
        cy = vh / 2
        for pno in visible:
            r = self._rects[pno].translated(-off.x(), -off.y())
            # shadow + paper
            p.fillRect(r.adjusted(1, 1, 2, 2), QColor(0, 0, 0, 40))
            p.fillRect(r, paper)
            full = (self.doc.page_rects[pno] * m_dev).irect
            vis = r.intersected(vrect)
            if vis.isEmpty() or full.is_empty:
                continue
            dx0 = (vis.left() - r.left()) * dpr
            dy0 = (vis.top() - r.top()) * dpr
            dx1 = (vis.right() - r.left()) * dpr
            dy1 = (vis.bottom() - r.top()) * dpr
            tiles = []
            missing = False
            for ty in range(int(dy0 // TILE), int(max(dy0, dy1 - 0.01) // TILE) + 1):
                for tx in range(int(dx0 // TILE), int(max(dx0, dx1 - 0.01) // TILE) + 1):
                    key = (self._key, pno, self._page_gen(pno), zkey, self.rotation, self.night, tx, ty)
                    item = self._tiles.get(key)
                    if item is None and self._must_sync(pno):
                        item = self._render_tile(key, pno, tx, ty, m_dev, full)
                    if item is None:
                        missing = True
                        dist = abs((r.top() + (ty + 0.5) * TILE / dpr) - cy)
                        needed.append((dist, "tile", key, pno, tx, ty, m_dev, full))
                    else:
                        tiles.append(item)
            if missing:
                pv = self._previews.get((self._key, pno, self._page_gen(pno), self.rotation, self.night))
                if pv is not None:
                    p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
                    p.drawPixmap(r, pv[0], QRectF(pv[0].rect()))
                    p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)
                else:
                    needed.append((-1, "preview", (self._key, pno, self._page_gen(pno), self.rotation, self.night), pno))
            for pm, px, py, _n in tiles:
                target = QRectF(r.left() + (px - full.x0) / dpr, r.top() + (py - full.y0) / dpr,
                                pm.width() / dpr, pm.height() / dpr)
                p.drawPixmap(target, pm, QRectF(pm.rect()))
            p.setPen(QPen(border, 1))
            p.drawRect(r.adjusted(-0.5, -0.5, 0.5, 0.5))
        self._sync_pages = set()
        self._paint_overlays(p, visible, off)
        self.forms.paint(p, visible)
        self.editing.paint(p, visible)
        self.annots.paint(p)
        # schedule rendering
        needed.sort(key=lambda t: t[0])
        if not needed:
            # idle: prefetch previews of neighbouring pages
            for d in (1, -1, 2, -2, 3):
                q = self._current + d
                pkey = (self._key, q, self._page_gen(q), self.rotation, self.night)
                if 0 <= q < self.doc.page_count and self._previews.get(pkey) is None:
                    needed.append((0, "preview", pkey, q))
                    break
        self._queue = needed
        if needed and not self._render_timer.isActive():
            self._render_timer.start()

    def _paint_overlays(self, p: QPainter, visible: list[int], off: QPointF):
        # search hits
        if self._hits:
            cur_rects = []
            p.setPen(Qt.PenStyle.NoPen)
            for pno in visible:
                hits = self._hits.get(pno)
                if not hits:
                    continue
                pt = self.doc.page_text(pno)
                for i, (s, e) in enumerate(hits):
                    is_cur = self._hit_cur == (pno, i)
                    for tr in pt.rects_for(s, e):
                        vr = self.text_rect_to_view(pno, tr).adjusted(-1, -1, 1, 1)
                        if is_cur:
                            cur_rects.append(vr)
                        else:
                            p.fillRect(vr, QColor(255, 210, 0, 110))
            for vr in cur_rects:
                p.fillRect(vr, QColor(255, 120, 0, 150))
                p.setPen(QPen(QColor(220, 80, 0), 1.5))
                p.drawRect(vr)
        # selection
        if self._sel_anchor and self._sel_head and self._sel_anchor != self._sel_head:
            (ap, ai), (hp, hi) = sorted([self._sel_anchor, self._sel_head])
            color = QColor(51, 136, 255, 90)
            for pno in visible:
                if pno < ap or pno > hp:
                    continue
                pt = self.doc.page_text(pno)
                s = ai if pno == ap else 0
                e = hi if pno == hp else len(pt.text)
                for tr in pt.rects_for(s, e):
                    p.fillRect(self.text_rect_to_view(pno, tr), color)
        # rubber band
        if self._rubber is not None:
            rr = self._rubber.translated(-off.x(), -off.y())
            pen = QPen(QColor(0, 120, 215), 1, Qt.PenStyle.DashLine)
            p.setPen(pen)
            p.fillRect(rr, QColor(0, 120, 215, 40))
            p.drawRect(rr)

    def _process_queue(self):
        start = time.perf_counter()
        did = False
        while self._queue and time.perf_counter() - start < 0.025:
            job = self._queue.pop(0)
            kind = job[1]
            try:
                if kind == "tile":
                    _d, _k, key, pno, tx, ty, m_dev, full = job
                    if self._tiles.get(key) is not None:
                        continue
                    self._render_tile(key, pno, tx, ty, m_dev, full)
                else:
                    _d, _k, key, pno = job
                    if self._previews.get(key) is not None:
                        continue
                    r = self.doc.page_rects[pno]
                    s = PREVIEW_PX / max(r.width, r.height, 1)
                    pix = self.doc.render(pno, pymupdf.Matrix(s, s).prerotate(self.rotation),
                                          night=self.night)
                    pm = pix_to_pixmap(pix)
                    self._previews.put(key, (pm,), pm.width() * pm.height() * 4)
                did = True
            except Exception as exc:  # damaged page: skip it
                self.message.emit(f"Could not render page {job[3] + 1}: {exc}")
        if did:
            self.viewport().update()
        elif self._queue:
            self._render_timer.start()

    def _render_tile(self, key, pno, tx, ty, m_dev, full):
        rect = pymupdf.IRect(full.x0 + tx * TILE, full.y0 + ty * TILE,
                             full.x0 + (tx + 1) * TILE, full.y0 + (ty + 1) * TILE) & full
        if rect.is_empty:
            return None
        clip = pymupdf.Rect(rect) * ~m_dev
        pix = self.doc.render(pno, m_dev, clip, night=self.night)
        pm = pix_to_pixmap(pix)
        item = (pm, pix.x, pix.y)
        self._tiles.put(key, item, pm.width() * pm.height() * 4)
        return (*item, 0)

    def _page_gen(self, pno: int):
        return (self._gen_all, self._gen.get(pno, 0))

    def _must_sync(self, pno: int) -> bool:
        return self._sync_pages is None or pno in self._sync_pages

    def _doc_changed(self, pages):
        """The document was edited: re-render the changed pages right away."""
        if self._structure != self.doc.structure:
            self._pages_changed()
        if pages is None:
            self._gen_all += 1
            self._tiles.drop_owner(self._key)
            self._previews.drop_owner(self._key)
            self._sync_pages = None
        else:
            for p in pages:
                self._gen[p] = self._gen.get(p, 0) + 1
            self._tiles.drop_owner_pages(self._key, pages)
            self._previews.drop_owner_pages(self._key, pages)
            if self._sync_pages is not None:
                self._sync_pages |= set(pages)
        self.viewport().update()

    def _pages_changed(self):
        """Pages were added, removed, moved, rotated or resized."""
        self._structure = self.doc.structure
        n = self.doc.page_count
        self.annots.cancel()
        self.annots.sel = None
        self.forms.commit()
        self.forms.sel = []
        self.forms.focus = None
        self.editing.cancel()
        self.editing.sel = None
        self._sel_anchor = self._sel_head = None
        self._selecting = False
        if self._search_rx is not None or self._hits:
            self.stop_search()
        self._back = [(min(p, n - 1), f) for p, f in self._back if n]
        self._fwd = [(min(p, n - 1), f) for p, f in self._fwd if n]
        before = self._current
        self._current = max(0, min(self._current, n - 1))
        top = self._location()[1] if self._laid_out and before < len(self._rects) else 0.0
        self._relayout()
        if self._laid_out and n:
            self.go_to_page(self._current, record=False, top_offset=max(0.0, top) if self.continuous else None)
        self.currentPageChanged.emit(self._current)
        self.selectionChanged.emit(False)
        self.annotSelected.emit(None)
        self.pagesChanged.emit()

    def invalidate(self):
        """Drop this document's cached renders (e.g. after it changed)."""
        self._doc_changed(None)

    def release(self):
        """Free cached renders when the document is closed."""
        self.annots.cancel()
        self.forms.cancel()
        self.editing.cancel()
        self._render_timer.stop()
        self._search_timer.stop()
        self._queue = []
        self._tiles.drop_owner(self._key)
        self._previews.drop_owner(self._key)

    # ----------------------------------------------------------- mouse input
    def _link_at_content(self, c: QPointF):
        pno = self._page_at(c)
        if pno is None:
            return None, None
        pt = self.content_to_page(pno, c)
        return pno, self.doc.link_at(pno, pt.x, pt.y)

    def _text_hit(self, c: QPointF, nearest=False):
        pno = self._page_at(c, nearest=nearest)
        if pno is None:
            return None
        pg = self.content_to_page(pno, c)
        tp = pg * ~self.doc.rotation_matrix(pno)
        return pno, tp

    def _update_cursor(self, pos: QPointF):
        tool = self.effective_tool()
        if self._panning:
            self.viewport().setCursor(Qt.CursorShape.ClosedHandCursor)
            return
        if self._pick is not None:
            self.viewport().setCursor(Qt.CursorShape.CrossCursor)
            return
        shape = self.editing.cursor_at(pos)
        if shape is None:
            shape = self.annots.cursor_at(pos)
        if shape is None:
            shape = self.forms.cursor_at(pos)
        if shape is not None:
            self.viewport().setCursor(shape)
            return
        if tool == Tool.HAND:
            c = pos + self._offset()
            _pno, link = self._link_at_content(c)
            self.viewport().setCursor(Qt.CursorShape.PointingHandCursor if link else Qt.CursorShape.OpenHandCursor)
            return
        if tool in (Tool.MARQUEE, Tool.SNAPSHOT):
            self.viewport().setCursor(Qt.CursorShape.CrossCursor)
            return
        c = pos + self._offset()
        _pno, link = self._link_at_content(c)
        if link:
            self.viewport().setCursor(Qt.CursorShape.PointingHandCursor)
            return
        hit = self._text_hit(c)
        if hit and self.doc.page_text(hit[0]).hit(hit[1].x, hit[1].y):
            self.viewport().setCursor(Qt.CursorShape.IBeamCursor)
        else:
            self.viewport().setCursor(Qt.CursorShape.ArrowCursor)

    def mousePressEvent(self, e):
        self.setFocus()
        pos = e.position()
        self._last_mouse = pos
        c = pos + self._offset()
        if e.button() == Qt.MouseButton.MiddleButton:
            self._start_pan(pos)
            return
        if e.button() != Qt.MouseButton.LeftButton:
            return super().mousePressEvent(e)
        self._press_pos = pos
        self._press_link = None
        if self._pick is not None:
            self._rubber_origin = c
            self._rubber = QRectF(c, c)
            return
        if self.editing.press(e):
            return
        if self.forms.prepare and self.forms.press(e):
            return
        if self.annots.press(e):
            return
        if not self.forms.prepare and self.forms.press(e):
            return
        tool = self.effective_tool()
        markup = tool in TEXT_TOOLS
        _pno, link = self._link_at_content(c)
        self._press_link = None if markup else link
        if tool == Tool.HAND:
            self._start_pan(pos)
            return
        if tool in (Tool.MARQUEE, Tool.SNAPSHOT):
            self._rubber_origin = c
            self._rubber = QRectF(c, c)
            return
        # select tool
        now = time.monotonic()
        triple = now - self._last_dbl_time < QApplication.doubleClickInterval() / 1000.0
        hit = self._text_hit(c)
        shift = bool(e.modifiers() & Qt.KeyboardModifier.ShiftModifier)
        if hit and triple:
            pno, tp = hit
            pt = self.doc.page_text(pno)
            a, b = pt.line_range(pt.index_at(tp.x, tp.y))
            self._set_selection((pno, a), (pno, b))
            self._last_dbl_time = 0
            return
        if hit and shift and self._sel_anchor:
            pno, tp = hit
            self._sel_head = (pno, self.doc.page_text(pno).index_at(tp.x, tp.y))
            self._selecting = True
            self.viewport().update()
            return
        if hit and self.doc.page_text(hit[0]).hit(hit[1].x, hit[1].y) and (markup or not link):
            pno, tp = hit
            idx = self.doc.page_text(pno).index_at(tp.x, tp.y)
            self._sel_anchor = self._sel_head = (pno, idx)
            self._selecting = True
            self.viewport().update()
            return
        if link:
            return
        self.clear_selection()
        if not markup:
            self._start_pan(pos)

    def mouseDoubleClickEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton and self.editing.double_click(e):
            return
        if e.button() == Qt.MouseButton.LeftButton and self.forms.prepare and self.forms.double_click(e):
            return
        if e.button() == Qt.MouseButton.LeftButton and self.annots.double_click(e):
            return
        if e.button() == Qt.MouseButton.LeftButton and self.effective_tool() in (Tool.SELECT, Tool.HAND) \
                and self.forms.double_click(e):
            return
        if e.button() != Qt.MouseButton.LeftButton or self.effective_tool() not in (Tool.SELECT, *TEXT_TOOLS):
            if self.effective_tool() == Tool.MARQUEE:
                return
            return super().mouseDoubleClickEvent(e)
        c = e.position() + self._offset()
        hit = self._text_hit(c)
        if not hit:
            return
        pno, tp = hit
        pt = self.doc.page_text(pno)
        if not pt.hit(tp.x, tp.y):
            return
        a, b = pt.word_at(pt.index_at(tp.x, tp.y))
        self._set_selection((pno, a), (pno, b))
        self._last_dbl_time = time.monotonic()
        self._selecting = False

    def _start_pan(self, pos):
        self._panning = True
        self._pan_last = pos
        self.viewport().setCursor(Qt.CursorShape.ClosedHandCursor)

    def mouseMoveEvent(self, e):
        pos = e.position()
        self._last_mouse = pos
        if self._panning and self._pan_last is not None:
            d = pos - self._pan_last
            self._pan_last = pos
            self.horizontalScrollBar().setValue(self.horizontalScrollBar().value() - int(round(d.x())))
            self.verticalScrollBar().setValue(self.verticalScrollBar().value() - int(round(d.y())))
            return
        if self.editing.move(e):
            return
        if self.forms.move(e):
            return
        if self.annots.move(e):
            return
        if self._selecting:
            self._extend_selection(pos)
            inside = 0 <= pos.y() <= self.viewport().height()
            if inside:
                self._autoscroll.stop()
            elif not self._autoscroll.isActive():
                self._autoscroll.start()
            return
        if self._rubber is not None:
            c = pos + self._offset()
            self._rubber = QRectF(self._rubber_origin, c).normalized()
            self.viewport().update()
            return
        if not e.buttons():
            self._update_cursor(pos)
            _pno, link = self._link_at_content(pos + self._offset())
            if link and link.get("kind") == pymupdf.LINK_URI:
                QToolTip.showText(e.globalPosition().toPoint(), link.get("uri", ""), self)
            elif link and link.get("kind") == pymupdf.LINK_GOTO:
                QToolTip.showText(e.globalPosition().toPoint(),
                                  f"Go to page {self.doc.page_label(link.get('page', 0))}", self)
            else:
                rec = self.annots.record_at(pos) if self.effective_tool() in (Tool.SELECT, Tool.HAND) else None
                tip = ""
                if rec is not None and (rec.contents or rec.author) and not rec.is_symbol:
                    tip = f"<b>{rec.author or rec.label}</b>" + (f"<br>{rec.contents[:400]}" if rec.contents else "")
                elif rec is None:
                    tip = self.forms.tooltip_at(pos)
                if tip:
                    QToolTip.showText(e.globalPosition().toPoint(), tip, self)
                else:
                    QToolTip.hideText()

    def _extend_selection(self, pos: QPointF):
        hit = self._text_hit(pos + self._offset(), nearest=True)
        if hit:
            pno, tp = hit
            self._sel_head = (pno, self.doc.page_text(pno).index_at(tp.x, tp.y))
            self.viewport().update()

    def _do_autoscroll(self):
        y = self._last_mouse.y()
        vh = self.viewport().height()
        if y < 0:
            delta = y
        elif y > vh:
            delta = y - vh
        else:
            self._autoscroll.stop()
            return
        vb = self.verticalScrollBar()
        vb.setValue(vb.value() + int(max(-80, min(80, delta))))
        self._extend_selection(self._last_mouse)

    def mouseReleaseEvent(self, e):
        pos = e.position()
        moved = self._press_pos is not None and (pos - self._press_pos).manhattanLength() > 4
        if not self._panning and e.button() == Qt.MouseButton.LeftButton and self.editing.release(e):
            return
        if not self._panning and e.button() == Qt.MouseButton.LeftButton and self.forms.release(e):
            return
        if not self._panning and e.button() == Qt.MouseButton.LeftButton and self.annots.release(e):
            return
        if self._panning:
            self._panning = False
            self._pan_last = None
            if self._press_link is not None and not moved and e.button() == Qt.MouseButton.LeftButton:
                link, self._press_link = self._press_link, None
                self.linkActivated.emit(link)
            self._update_cursor(pos)
            return
        if self._selecting:
            self._selecting = False
            self._autoscroll.stop()
            self.selectionChanged.emit(self.has_selection())
            if self.has_selection() and self.effective_tool() in TEXT_TOOLS:
                self.markupRequested.emit(self.effective_tool())
            return
        if self._rubber is not None:
            rect, self._rubber = self._rubber, None
            self.viewport().update()
            if self._pick is not None:
                self._finish_pick(rect)
                return
            if self.effective_tool() == Tool.MARQUEE:
                if rect.width() < 5 or rect.height() < 5:
                    if e.modifiers() & (Qt.KeyboardModifier.AltModifier | Qt.KeyboardModifier.ControlModifier):
                        self.zoom_out(pos)
                    else:
                        self.zoom_in(pos)
                else:
                    self.zoom_to_rect(rect)
            elif self.effective_tool() == Tool.SNAPSHOT and rect.width() > 4 and rect.height() > 4:
                self.snapshot(rect)
            return
        if self._press_link is not None and not moved and e.button() == Qt.MouseButton.LeftButton:
            link, self._press_link = self._press_link, None
            self.linkActivated.emit(link)
        self._press_link = None

    def zoom_to_rect(self, rect: QRectF):
        vw, vh = self.viewport().width(), self.viewport().height()
        center = rect.center()
        pno = self._page_at(center, nearest=True)
        if pno is None:
            return
        pt = self.content_to_page(pno, center)
        factor = min(vw / rect.width(), vh / rect.height())
        self.zoom_mode = None
        self.zoom = max(ZOOM_MIN, min(ZOOM_MAX, self.zoom * factor))
        self._relayout()
        c = self.page_to_content(pno, pt.x, pt.y)
        self._scroll_to_content(c.x() - vw / 2, c.y() - vh / 2)
        self.zoomChanged.emit(self.zoom)

    def snapshot(self, rect: QRectF):
        """Copy the area under ``rect`` (content coords) to the clipboard as an image."""
        best, best_a = None, 0
        for i, r in enumerate(self._rects):
            if r.isNull():
                continue
            inter = r.intersected(rect)
            a = inter.width() * inter.height()
            if a > best_a:
                best, best_a = i, a
        if best is None:
            return
        inter = self._rects[best].intersected(rect)
        p0 = self.content_to_page(best, inter.topLeft())
        p1 = self.content_to_page(best, inter.bottomRight())
        clip = pymupdf.Rect(p0, p1).normalize()
        s = max(self.zoom * PT * self.viewport().devicePixelRatioF(), 150 / 72)
        pix = self.doc.render(best, pymupdf.Matrix(s, s).prerotate(self.rotation), clip)
        QGuiApplication.clipboard().setImage(pix_to_qimage(pix))
        self.message.emit("The selected area has been copied.")

    def wheelEvent(self, e):
        mods = e.modifiers()
        dy = e.angleDelta().y()
        if mods & Qt.KeyboardModifier.ControlModifier:
            self._wheel_acc += dy
            while self._wheel_acc >= 120:
                self._wheel_acc -= 120
                self.zoom_in(e.position())
            while self._wheel_acc <= -120:
                self._wheel_acc += 120
                self.zoom_out(e.position())
            e.accept()
            return
        if not self.continuous and dy:
            vb = self.verticalScrollBar()
            if dy < 0 and vb.value() >= vb.maximum():
                self._wheel_acc -= abs(dy)
                if self._wheel_acc <= -120:
                    self._wheel_acc = 0
                    self.next_page()
                return
            if dy > 0 and vb.value() <= vb.minimum():
                self._wheel_acc += dy
                if self._wheel_acc >= 120:
                    self._wheel_acc = 0
                    before = self._current
                    self.prev_page()
                    if self._current != before:
                        vb.setValue(vb.maximum())
                return
            self._wheel_acc = 0
        super().wheelEvent(e)

    def event(self, e):
        # Tab / Shift+Tab move between form fields (instead of moving keyboard focus)
        if e.type() == QEvent.Type.KeyPress and e.key() in (Qt.Key.Key_Tab, Qt.Key.Key_Backtab) \
                and not e.modifiers() & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.AltModifier) \
                and not self.forms.prepare and self.forms.active():
            back = e.key() == Qt.Key.Key_Backtab or bool(e.modifiers() & Qt.KeyboardModifier.ShiftModifier)
            if self.forms.tab(back):
                return True
        return super().event(e)

    def keyPressEvent(self, e):
        if self._pick is not None and e.key() == Qt.Key.Key_Escape:
            self._rubber = None
            self._finish_pick(None)
            return
        if self.editing.key(e):
            return
        if self.forms.prepare and self.forms.key(e):
            return
        if self.annots.key(e):
            return
        if not self.forms.prepare and self.forms.key(e):
            return
        key = e.key()
        mods = e.modifiers()
        vb, hb = self.verticalScrollBar(), self.horizontalScrollBar()
        if key == Qt.Key.Key_Space and not e.isAutoRepeat() and not mods:
            self._temp_hand = True
            self._update_cursor(self._last_mouse)
            return
        if key == Qt.Key.Key_Escape:
            if self._rubber is not None:
                self._rubber = None
                self.viewport().update()
            elif self.has_selection():
                self.clear_selection()
            else:
                e.ignore()
            return
        if key in (Qt.Key.Key_Left, Qt.Key.Key_Right) and hb.maximum() == 0:
            (self.prev_page if key == Qt.Key.Key_Left else self.next_page)()
            return
        if key == Qt.Key.Key_PageDown or (key == Qt.Key.Key_Down and not self.continuous and vb.value() >= vb.maximum()):
            if not self.continuous and vb.value() >= vb.maximum():
                self.next_page()
            else:
                vb.setValue(vb.value() + vb.pageStep() - 40)
            return
        if key == Qt.Key.Key_PageUp or (key == Qt.Key.Key_Up and not self.continuous and vb.value() <= 0):
            if not self.continuous and vb.value() <= 0:
                self.prev_page()
                vb.setValue(vb.maximum())
            else:
                vb.setValue(vb.value() - vb.pageStep() + 40)
            return
        if key == Qt.Key.Key_Home:
            self.first_page()
            return
        if key == Qt.Key.Key_End:
            self.last_page()
            return
        super().keyPressEvent(e)

    def keyReleaseEvent(self, e):
        if e.key() == Qt.Key.Key_Space and not e.isAutoRepeat():
            self._temp_hand = False
            self._update_cursor(self._last_mouse)
            return
        super().keyReleaseEvent(e)

    def focusOutEvent(self, e):
        self._temp_hand = False
        super().focusOutEvent(e)

    def contextMenuEvent(self, e):
        if self.context_menu_builder is None:
            return
        menu = QMenu(self)
        self.context_menu_builder(menu, e.pos())
        if not menu.isEmpty():
            menu.exec(e.globalPos())

    # -------------------------------------------------------------- selection
    def _set_selection(self, a, b):
        self._sel_anchor, self._sel_head = a, b
        self.viewport().update()
        self.selectionChanged.emit(self.has_selection())

    def has_selection(self) -> bool:
        return bool(self._sel_anchor and self._sel_head and self._sel_anchor != self._sel_head)

    def clear_selection(self):
        if self._sel_anchor or self._sel_head:
            self._sel_anchor = self._sel_head = None
            self.viewport().update()
            self.selectionChanged.emit(False)

    def select_all(self):
        n = self.doc.page_count
        if not n:
            return
        self._set_selection((0, 0), (n - 1, len(self.doc.page_text(n - 1).text)))

    def selected_text(self) -> str:
        if not self.has_selection():
            return ""
        (ap, ai), (hp, hi) = sorted([self._sel_anchor, self._sel_head])
        parts = []
        for pno in range(ap, hp + 1):
            t = self.doc.page_text(pno).text
            s = ai if pno == ap else 0
            e = hi if pno == hp else len(t)
            parts.append(t[s:e])
        return "\n".join(parts)

    def selection_ranges(self) -> list[tuple[int, int, int]]:
        """[(page, start, end), ...] of the current text selection."""
        if not self.has_selection():
            return []
        (ap, ai), (hp, hi) = sorted([self._sel_anchor, self._sel_head])
        out = []
        for pno in range(ap, hp + 1):
            t = self.doc.page_text(pno).text
            s = ai if pno == ap else 0
            e = hi if pno == hp else len(t)
            if e > s:
                out.append((pno, s, e))
        return out

    def copy_selection(self) -> bool:
        if not self.has_selection():
            return False
        if not self.doc.can_copy():
            self.message.emit("This document does not allow copying text.")
            return False
        QGuiApplication.clipboard().setText(self.selected_text())
        return True

    # ----------------------------------------------------------------- search
    def start_search(self, regex):
        self._search_timer.stop()
        self._hits = {}
        self._hit_cur = None
        self._search_rx = regex
        self._search_page = 0
        self._search_done = regex is None
        self._search_jump = regex is not None
        self.viewport().update()
        if regex is None:
            self.searchProgress.emit(0, True)
            self.currentHitChanged.emit(-1, 0)
            return
        self._search_timer.start()

    def stop_search(self):
        self.start_search(None)

    def total_hits(self) -> int:
        return sum(len(v) for v in self._hits.values())

    def search_finished(self) -> bool:
        return self._search_done

    def _search_step(self):
        if self._search_rx is None:
            return
        start = time.perf_counter()
        n = self.doc.page_count
        while self._search_page < n and time.perf_counter() - start < 0.03:
            pno = self._search_page
            self._search_page += 1
            try:
                hits = self.doc.search_page(pno, self._search_rx)
            except Exception:
                hits = []
            if hits:
                self._hits[pno] = hits
                self.searchPageHits.emit(pno, hits)
                if self._search_jump and pno >= self._current:
                    self._search_jump = False
                    self._select_hit((pno, 0))
        self._search_done = self._search_page >= n
        if self._search_done and self._search_jump and self._hits:
            self._search_jump = False
            first = min(self._hits)
            self._select_hit((first, 0))
        self.searchProgress.emit(self.total_hits(), self._search_done)
        if self._hit_cur is not None:
            self.currentHitChanged.emit(self._ordinal(self._hit_cur), self.total_hits())
        self.viewport().update()
        if not self._search_done:
            self._search_timer.start()

    def _ordinal(self, hit) -> int:
        pno, i = hit
        return sum(len(v) for p, v in self._hits.items() if p < pno) + i

    def _select_hit(self, hit):
        self._hit_cur = hit
        pno, i = hit
        s, e = self._hits[pno][i]
        rects = self.doc.page_text(pno).rects_for(s, e)
        if rects:
            self._ensure_visible(pno, rects[0])
        self.currentHitChanged.emit(self._ordinal(hit), self.total_hits())
        self.viewport().update()

    def select_hit_by_ordinal(self, pno: int, i: int):
        if pno in self._hits and 0 <= i < len(self._hits[pno]):
            self._push_history()
            self._select_hit((pno, i))

    def _ensure_visible(self, pno: int, text_rect):
        if not self.continuous and pno not in self._rows()[self._row_of(self._current)]:
            self._current = pno
            self._relayout()
            self.currentPageChanged.emit(pno)
        vr = self.text_rect_to_view(pno, text_rect)
        vw, vh = self.viewport().width(), self.viewport().height()
        hb, vb = self.horizontalScrollBar(), self.verticalScrollBar()
        if vr.top() < 40 or vr.bottom() > vh - 40:
            vb.setValue(int(vb.value() + vr.center().y() - vh / 3))
        if vr.left() < 20 or vr.right() > vw - 20:
            hb.setValue(int(hb.value() + vr.center().x() - vw / 2))

    def search_next(self, backward=False):
        if not self._hits:
            return
        order = sorted(self._hits)
        if self._hit_cur is None:
            target = next((p for p in order if p >= self._current), order[0])
            self._select_hit((target, 0) if not backward else (target, len(self._hits[target]) - 1))
            return
        pno, i = self._hit_cur
        if not backward:
            if i + 1 < len(self._hits[pno]):
                self._select_hit((pno, i + 1))
                return
            later = [p for p in order if p > pno]
            nxt = later[0] if later else order[0]
            self._select_hit((nxt, 0))
        else:
            if i > 0:
                self._select_hit((pno, i - 1))
                return
            earlier = [p for p in order if p < pno]
            prv = earlier[-1] if earlier else order[-1]
            self._select_hit((prv, len(self._hits[prv]) - 1))
