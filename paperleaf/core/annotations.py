"""Annotation model and editing operations.

All coordinates here are *unrotated* page coordinates (same as text), which is
what PyMuPDF's annotation API uses. Every change runs inside
``PdfDocument.edit()`` so it is one undoable step.
"""
from __future__ import annotations

import datetime as _dt
import math
import re
from dataclasses import dataclass, field

import pymupdf

from paperleaf.core.document import PdfDocument

MARKUP = {"Highlight", "Underline", "StrikeOut", "Squiggly"}
VERTEX = {"Ink", "Line", "Polygon", "PolyLine"}
BOXED = {"Square", "Circle", "FreeText", "Stamp", "Text", "FileAttachment", "Caret"}
SKIP = {"Popup", "Link", "Widget"}

KIND_LABELS = {
    "Highlight": "Highlight", "Underline": "Underline", "StrikeOut": "Strikethrough",
    "Squiggly": "Squiggly underline", "Text": "Sticky note", "FreeText": "Text box",
    "Ink": "Drawing", "Line": "Line", "Square": "Rectangle", "Circle": "Oval",
    "Polygon": "Polygon", "PolyLine": "Connected lines", "Stamp": "Stamp",
    "FileAttachment": "Attachment", "Caret": "Inserted text", "Redact": "Redaction",
}

STATUSES = ("None", "Accepted", "Rejected", "Cancelled", "Completed")

ZADB = {"check": "4", "cross": "8", "dot": "l"}  # ZapfDingbats glyphs


@dataclass
class Style:
    color: tuple | None = (1.0, 0.0, 0.0)
    fill: tuple | None = None
    width: float = 1.5
    opacity: float = 1.0
    fontsize: float = 12.0
    dashed: bool = False


@dataclass
class AnnotRecord:
    xref: int
    pno: int
    kind: str
    rect: pymupdf.Rect
    vertices: list = field(default_factory=list)
    stroke: tuple | None = None
    fill: tuple | None = None
    opacity: float = 1.0
    width: float = 1.0
    dashed: bool = False
    author: str = ""
    contents: str = ""
    subject: str = ""
    created: str = ""
    modified: str = ""
    irt: int = 0
    hidden: bool = False
    locked: bool = False
    state: str = ""
    fontsize: float = 0.0
    text_color: tuple | None = None
    line_ends: tuple = (0, 0)
    callout: bool = False

    @property
    def label(self) -> str:
        if self.kind == "Stamp" and self.subject:
            return self.subject
        if self.is_symbol:
            return {"4": "Checkmark", "8": "Cross", "l": "Dot"}[self.contents]
        return KIND_LABELS.get(self.kind, self.kind)

    @property
    def movable(self) -> bool:
        # redaction marks sit exactly on the content they remove: mark again instead of moving them
        return self.kind not in MARKUP and self.kind != "Redact" and not self.locked

    @property
    def resizable(self) -> bool:
        return self.movable and self.kind not in {"Text", "Line", "FileAttachment", "Caret"}

    @property
    def is_reply(self) -> bool:
        return bool(self.irt)

    @property
    def is_symbol(self) -> bool:
        """A Fill & Sign checkmark / cross / dot (ZapfDingbats text box)."""
        return self.kind == "FreeText" and self.contents in ZADB.values() and self.subject == "Fill & Sign"


def _nums(s: str) -> list[float]:
    return [float(v) for v in re.findall(r"-?\d*\.?\d+(?:[eE][-+]?\d+)?", s or "")]


def _parse_da(da: str):
    """'0 0 1 rg /Helv 18 Tf' -> (fontsize, (r, g, b))."""
    size = 0.0
    color = None
    m = re.search(r"/\S+\s+([\d.]+)\s+Tf", da or "")
    if m:
        size = float(m.group(1))
    m = re.search(r"([\d.]+)\s+([\d.]+)\s+([\d.]+)\s+rg", da or "")
    if m:
        color = tuple(float(v) for v in m.groups())
    else:
        m = re.search(r"([\d.]+)\s+g\b", da or "")
        if m:
            g = float(m.group(1))
            color = (g, g, g)
    return size, color


def _record(doc: PdfDocument, annot: pymupdf.Annot, pno: int) -> AnnotRecord:
    kind = annot.type[1]
    colors = annot.colors or {}
    border = annot.border or {}
    info = annot.info or {}
    flags = annot.flags or 0
    stroke = tuple(colors.get("stroke") or ()) or None
    fill = tuple(colors.get("fill") or ()) or None
    width = border.get("width", 1)
    rec = AnnotRecord(
        xref=annot.xref, pno=pno, kind=kind, rect=pymupdf.Rect(annot.rect),
        vertices=annot.vertices or [], stroke=stroke, fill=fill,
        opacity=annot.opacity if annot.opacity is not None and annot.opacity >= 0 else 1.0,
        width=width if width and width > 0 else (1.0 if kind not in ("FreeText",) else 0.0),
        dashed=bool(border.get("dashes")),
        author=info.get("title", "") or "", contents=info.get("content", "") or "",
        subject=info.get("subject", "") or "", created=info.get("creationDate", "") or "",
        modified=info.get("modDate", "") or "", irt=annot.irt_xref or 0,
        hidden=bool(flags & pymupdf.PDF_ANNOT_IS_HIDDEN),
        locked=bool(flags & pymupdf.PDF_ANNOT_IS_LOCKED),
        line_ends=tuple(annot.line_ends or (0, 0)),
    )
    if kind == "FreeText":
        try:
            rec.fontsize, rec.text_color = _parse_da(doc.doc.xref_get_key(annot.xref, "DA")[1])
            rec.callout = doc.doc.xref_get_key(annot.xref, "CL")[0] == "array"
        except Exception:
            pass
    if kind == "Text" and rec.irt:
        try:
            st = doc.doc.xref_get_key(annot.xref, "State")
            if st[0] == "string":
                rec.state = st[1]
        except Exception:
            pass
    return rec


def page_annots(doc: PdfDocument, pno: int) -> list[AnnotRecord]:
    """All annotations on a page (including hidden replies), in z-order."""
    def load():
        out = []
        if not doc.is_pdf:
            return out
        try:
            for a in doc.page(pno).annots():
                if a.type[1] in SKIP:
                    continue
                out.append(_record(doc, a, pno))
        except Exception:
            pass
        return out
    return doc._annots.get_or(pno, load)


def find(doc: PdfDocument, pno: int, xref: int) -> AnnotRecord | None:
    return next((r for r in page_annots(doc, pno) if r.xref == xref), None)


def replies(doc: PdfDocument, rec: AnnotRecord) -> list[AnnotRecord]:
    return [r for r in page_annots(doc, rec.pno) if r.irt == rec.xref and not r.state]


def status_of(doc: PdfDocument, rec: AnnotRecord) -> str:
    states = [r for r in page_annots(doc, rec.pno) if r.irt == rec.xref and r.state]
    if not states:
        return ""
    st = max(states, key=lambda r: r.modified or r.created)
    return "" if st.state == "None" else st.state


def markup_text(doc: PdfDocument, rec: AnnotRecord) -> str:
    """Text covered by a highlight/underline/... annotation."""
    if rec.kind not in MARKUP or not rec.vertices:
        return ""
    pt = doc.page_text(rec.pno)
    quads = []
    v = rec.vertices
    for i in range(0, len(v) - 3, 4):
        quads.append(pymupdf.Quad(v[i], v[i + 1], v[i + 2], v[i + 3]).rect)
    out = []
    for ch, box in zip(pt.text, pt.boxes):
        if box is None:
            if out and out[-1] != " ":
                out.append(" ")
            continue
        cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
        if any(q.x0 - 1 <= cx <= q.x1 + 1 and q.y0 - 1 <= cy <= q.y1 + 1 for q in quads):
            out.append(ch)
        elif out and out[-1] != " ":
            out.append(" ")
    return " ".join("".join(out).split())


# --------------------------------------------------------------- hit testing
def _seg_dist(px, py, ax, ay, bx, by) -> float:
    dx, dy = bx - ax, by - ay
    if dx == dy == 0:
        return math.hypot(px - ax, py - ay)
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def _in_polygon(x, y, pts) -> bool:
    inside = False
    n = len(pts)
    for i in range(n):
        x1, y1 = pts[i]
        x2, y2 = pts[(i + 1) % n]
        if (y1 > y) != (y2 > y) and x < (x2 - x1) * (y - y1) / ((y2 - y1) or 1e-9) + x1:
            inside = not inside
    return inside


def hit(rec: AnnotRecord, x: float, y: float, tol: float) -> bool:
    if rec.hidden:
        return False
    r = rec.rect
    if not (r.x0 - tol <= x <= r.x1 + tol and r.y0 - tol <= y <= r.y1 + tol):
        return False
    k = rec.kind
    reach = tol + rec.width / 2
    if k in MARKUP:
        v = rec.vertices
        for i in range(0, len(v) - 3, 4):
            q = pymupdf.Quad(v[i], v[i + 1], v[i + 2], v[i + 3]).rect
            if q.x0 - 1 <= x <= q.x1 + 1 and q.y0 - 1 <= y <= q.y1 + 1:
                return True
        return False
    if k == "Ink":
        for stroke in rec.vertices:
            if len(stroke) == 1 and math.hypot(x - stroke[0][0], y - stroke[0][1]) <= reach:
                return True
            for a, b in zip(stroke, stroke[1:]):
                if _seg_dist(x, y, a[0], a[1], b[0], b[1]) <= reach:
                    return True
        return False
    if k in ("Line", "PolyLine", "Polygon"):
        pts = rec.vertices
        segs = list(zip(pts, pts[1:]))
        if k == "Polygon" and len(pts) > 2:
            segs.append((pts[-1], pts[0]))
            if rec.fill and _in_polygon(x, y, pts):
                return True
        return any(_seg_dist(x, y, a[0], a[1], b[0], b[1]) <= reach + 2 for a, b in segs)
    if k in ("Square", "Circle") and not rec.fill:
        inner = pymupdf.Rect(r.x0 + reach + 3, r.y0 + reach + 3, r.x1 - reach - 3, r.y1 - reach - 3)
        return inner.is_empty or not inner.contains(pymupdf.Point(x, y))
    return True


def annot_at(doc: PdfDocument, pno: int, x: float, y: float, tol: float) -> AnnotRecord | None:
    for rec in reversed(page_annots(doc, pno)):
        if hit(rec, x, y, tol):
            return rec
    return None


# ------------------------------------------------------------------ helpers
def _now() -> str:
    return pymupdf.get_pdf_now()


def _info(annot: pymupdf.Annot, author: str, subject: str | None = None, contents: str | None = None):
    now = _now()
    kw = {"title": author or "", "creationDate": now, "modDate": now}
    if subject is not None:
        kw["subject"] = subject
    if contents is not None:
        kw["content"] = contents
    annot.set_info(**kw)


def _touch(annot: pymupdf.Annot):
    annot.set_info(modDate=_now())


def _style_border(annot: pymupdf.Annot, style: Style, clouds: int = 0):
    annot.set_border(width=style.width, dashes=[3, 3] if style.dashed else None, clouds=clouds or -1)


def _coord_map(doc: PdfDocument, annot: pymupdf.Annot):
    """(to_pdf) mapping unrotated MuPDF coordinates -> raw PDF user space."""
    raw = _nums(doc.doc.xref_get_key(annot.xref, "Rect")[1])
    r = annot.rect
    llx, ury = min(raw[0], raw[2]), max(raw[1], raw[3])
    k = llx - r.x0
    c = r.y0 + ury
    return lambda x, y: (x + k, c - y)


def _fmt(v: float) -> str:
    return f"{v:.3f}".rstrip("0").rstrip(".") or "0"


def _set_vertices(doc: PdfDocument, annot: pymupdf.Annot, transform):
    """Rewrite an Ink/Line/Polygon/PolyLine's points in place (keeps xref and replies)."""
    to_pdf = _coord_map(doc, annot)
    kind = annot.type[1]
    verts = annot.vertices

    def conv(p):
        x, y = transform(p[0], p[1])
        return to_pdf(x, y)
    if kind == "Ink":
        arr = "[" + " ".join("[" + " ".join(f"{_fmt(a)} {_fmt(b)}" for a, b in map(conv, s)) + "]"
                             for s in verts) + "]"
        doc.doc.xref_set_key(annot.xref, "InkList", arr)
    else:
        key = "L" if kind == "Line" else "Vertices"
        arr = "[" + " ".join(f"{_fmt(a)} {_fmt(b)}" for a, b in map(conv, verts)) + "]"
        doc.doc.xref_set_key(annot.xref, key, arr)


def _shift_callout(doc: PdfDocument, annot: pymupdf.Annot, dx: float, dy: float, transform=None):
    key = doc.doc.xref_get_key(annot.xref, "CL")
    if key[0] != "array":
        return
    vals = _nums(key[1])
    # PDF space: y is flipped
    out = []
    for i in range(0, len(vals) - 1, 2):
        out += [vals[i] + dx, vals[i + 1] - dy]
    doc.doc.xref_set_key(annot.xref, "CL", "[" + " ".join(_fmt(v) for v in out) + "]")


def _load(doc: PdfDocument, pno: int, xref: int) -> pymupdf.Annot:
    a = doc.page(pno).load_annot(xref)
    if a is None:
        raise KeyError(f"annotation {xref} not found on page {pno + 1}")
    return a


# ------------------------------------------------------------------ creation
def add_markup(doc, pno, kind: str, rects: list, style: Style, author: str) -> int:
    quads = [pymupdf.Rect(r).quad for r in rects]
    label = {"highlight": "Highlight", "underline": "Underline", "strikeout": "Strikethrough",
             "squiggly": "Squiggly underline"}[kind]
    with doc.edit(f"Add {label.lower()}", pno):
        page = doc.page(pno)
        fn = {"highlight": page.add_highlight_annot, "underline": page.add_underline_annot,
              "strikeout": page.add_strikeout_annot, "squiggly": page.add_squiggly_annot}[kind]
        a = fn(quads=quads)
        a.set_colors(stroke=style.color)
        a.set_opacity(style.opacity)
        _info(a, author)
        a.update()
        return a.xref


def add_markup_ranges(doc, ranges: list, kind: str, style: Style, author: str) -> list[tuple[int, int]]:
    """Markup over text selection ranges [(page, start, end)], possibly on several pages, as one step."""
    label = {"highlight": "Highlight", "underline": "Underline", "strikeout": "Strikethrough",
             "squiggly": "Squiggly underline"}[kind]
    out = []
    pages = {r[0] for r in ranges}
    with doc.edit(f"Add {label.lower()}", pages):
        for pno, s, e in ranges:
            rects = doc.page_text(pno).rects_for(s, e)
            if not rects:
                continue
            page = doc.page(pno)
            fn = {"highlight": page.add_highlight_annot, "underline": page.add_underline_annot,
                  "strikeout": page.add_strikeout_annot, "squiggly": page.add_squiggly_annot}[kind]
            a = fn(quads=[pymupdf.Rect(r).quad for r in rects])
            a.set_colors(stroke=style.color)
            a.set_opacity(style.opacity)
            _info(a, author)
            a.update()
            out.append((pno, a.xref))
    return out


def add_note(doc, pno, point, text: str, style: Style, author: str, icon: str = "Comment") -> int:
    with doc.edit("Add sticky note", pno):
        a = doc.page(pno).add_text_annot(pymupdf.Point(point), text, icon=icon)
        a.set_colors(stroke=style.color)
        a.set_opacity(style.opacity)
        _info(a, author, contents=text)
        a.update()
        return a.xref


def add_freetext(doc, pno, rect, text: str, style: Style, author: str, *, border: bool = True,
                 callout=None, fontname: str = "Helv", subject: str | None = None,
                 label: str = "Add text box") -> int:
    page = doc.page(pno)
    with doc.edit(label, pno):
        kw = dict(fontsize=style.fontsize, fontname=fontname, text_color=style.color,
                  fill_color=style.fill, rotate=page.rotation, opacity=style.opacity)
        if border:  # plain-text boxes draw their border in the text color
            kw.update(border_width=max(0.5, style.width))
        if callout:
            kw.update(callout=callout, line_end=pymupdf.PDF_ANNOT_LE_OPEN_ARROW)
        a = page.add_freetext_annot(pymupdf.Rect(rect), text, **kw)
        _info(a, author, subject=subject, contents=text)
        a.update()
        return a.xref


def add_symbol(doc, pno, center, kind: str, style: Style, author: str) -> int:
    s = style.fontsize * 1.15
    r = pymupdf.Rect(center[0] - s / 2, center[1] - s / 2, center[0] + s / 2, center[1] + s / 2)
    st = Style(color=style.color, fill=None, width=0, opacity=style.opacity, fontsize=style.fontsize)
    names = {"check": "checkmark", "cross": "cross mark", "dot": "dot"}
    return add_freetext(doc, pno, r, ZADB[kind], st, author, border=False, fontname="ZaDb",
                        subject="Fill & Sign", label=f"Add {names[kind]}")


def text_width(text: str, fontsize: float) -> float:
    return max((pymupdf.get_text_length(line, "helv", fontsize) for line in text.split("\n")), default=0)


def fit_text_rect(origin, text: str, fontsize: float, min_w: float = 30) -> pymupdf.Rect:
    """Rect at ``origin`` (top-left) big enough for ``text``."""
    lines = max(1, text.count("\n") + 1)
    w = max(min_w, text_width(text, fontsize) + fontsize * 0.9)
    h = lines * fontsize * 1.25 + fontsize * 0.45
    return pymupdf.Rect(origin[0], origin[1], origin[0] + w, origin[1] + h)


def add_ink(doc, pno, strokes: list, style: Style, author: str, label="Add drawing") -> int:
    with doc.edit(label, pno):
        a = doc.page(pno).add_ink_annot([[tuple(p) for p in s] for s in strokes])
        a.set_colors(stroke=style.color)
        a.set_border(width=style.width)
        a.set_opacity(style.opacity)
        _info(a, author)
        a.update()
        return a.xref


def add_shape(doc, pno, kind: str, pts: list, style: Style, author: str) -> int:
    labels = {"rect": "rectangle", "ellipse": "oval", "line": "line", "arrow": "arrow",
              "polygon": "polygon", "cloud": "cloud", "polyline": "connected lines"}
    with doc.edit(f"Add {labels[kind]}", pno):
        page = doc.page(pno)
        if kind == "rect":
            a = page.add_rect_annot(pymupdf.Rect(pts[0], pts[1]).normalize())
        elif kind == "ellipse":
            a = page.add_circle_annot(pymupdf.Rect(pts[0], pts[1]).normalize())
        elif kind in ("line", "arrow"):
            a = page.add_line_annot(pymupdf.Point(pts[0]), pymupdf.Point(pts[1]))
        elif kind == "polyline":
            a = page.add_polyline_annot([pymupdf.Point(p) for p in pts])
        else:
            a = page.add_polygon_annot([pymupdf.Point(p) for p in pts])
        a.set_colors(stroke=style.color, fill=style.fill if kind in ("rect", "ellipse", "polygon", "cloud") else None)
        _style_border(a, style, clouds=2 if kind == "cloud" else 0)
        if kind == "arrow":
            a.set_line_ends(pymupdf.PDF_ANNOT_LE_NONE, pymupdf.PDF_ANNOT_LE_OPEN_ARROW)
        a.set_opacity(style.opacity)
        _info(a, author)
        a.update()
        return a.xref


def add_image_stamp(doc, pno, rect, png: bytes, author: str, subject: str = "Stamp",
                    label: str = "Add stamp") -> int:
    with doc.edit(label, pno):
        a = doc.page(pno).add_stamp_annot(pymupdf.Rect(rect), stamp=png)
        _info(a, author, subject=subject, contents="")
        a.update()
        return a.xref


# -------------------------------------------------------------- modification
def move(doc, pno, xref, dx: float, dy: float) -> int:
    with doc.edit("Move", pno):
        a = _load(doc, pno, xref)
        kind = a.type[1]
        if kind in VERTEX:
            _set_vertices(doc, a, lambda x, y: (x + dx, y + dy))
        elif kind not in MARKUP:
            if kind == "FreeText":
                _shift_callout(doc, a, dx, dy)
            a.set_rect(pymupdf.Rect(a.rect) + (dx, dy, dx, dy))
        _touch(a)
        a.update()
    return xref


def resize(doc, pno, xref, new_rect) -> int:
    new = pymupdf.Rect(new_rect).normalize()
    with doc.edit("Resize", pno):
        a = _load(doc, pno, xref)
        kind = a.type[1]
        if kind in VERTEX:
            old = pymupdf.Rect(a.rect)
            pts = [p for s in a.vertices for p in s] if kind == "Ink" else list(a.vertices)
            # map the points' own box (the annot rect is padded) onto the new box
            inner_old = pymupdf.Rect(min(p[0] for p in pts), min(p[1] for p in pts),
                                     max(p[0] for p in pts), max(p[1] for p in pts))
            inner_new = pymupdf.Rect(new.x0 + inner_old.x0 - old.x0, new.y0 + inner_old.y0 - old.y0,
                                     new.x1 - (old.x1 - inner_old.x1), new.y1 - (old.y1 - inner_old.y1))
            sx = inner_new.width / inner_old.width if inner_old.width > 1e-3 else 1
            sy = inner_new.height / inner_old.height if inner_old.height > 1e-3 else 1
            _set_vertices(doc, a, lambda x, y: (inner_new.x0 + (x - inner_old.x0) * sx,
                                                inner_new.y0 + (y - inner_old.y0) * sy))
        elif kind not in MARKUP:
            a.set_rect(new)
        _touch(a)
        a.update()
    return xref


def set_line_points(doc, pno, xref, p1, p2) -> int:
    with doc.edit("Move line end", pno):
        a = _load(doc, pno, xref)
        to_pdf = _coord_map(doc, a)
        (x1, y1), (x2, y2) = to_pdf(*p1), to_pdf(*p2)
        doc.doc.xref_set_key(xref, "L", f"[{_fmt(x1)} {_fmt(y1)} {_fmt(x2)} {_fmt(y2)}]")
        _touch(a)
        a.update()
    return xref


def set_style(doc, pno, xref, *, color=..., fill=..., width=None, opacity=None, fontsize=None,
              dashed=None) -> int:
    with doc.edit("Change appearance", pno):
        a = _load(doc, pno, xref)
        kind = a.type[1]
        rec = _record(doc, a, pno)
        if opacity is not None:
            a.set_opacity(opacity)
        if kind == "FreeText":
            tc = rec.text_color if color is ... else color
            fc = rec.fill if fill is ... else fill
            fs = fontsize or rec.fontsize or 12
            bw = rec.width if width is None else width
            if width is not None:
                a.set_border(width=width)
            a.update(fontsize=fs, text_color=tc, fill_color=fc)
            _touch(a)
            return xref
        kw = {}
        if color is not ...:
            kw["stroke"] = color
        if fill is not ... and kind in ("Square", "Circle", "Polygon"):
            kw["fill"] = fill
        if kw:
            if "stroke" not in kw:
                kw["stroke"] = rec.stroke
            if "fill" not in kw and kind in ("Square", "Circle", "Polygon"):
                kw["fill"] = rec.fill
            a.set_colors(**kw)
        if (width is not None or dashed is not None) and kind not in MARKUP and kind not in ("Text", "Stamp"):
            b = a.border or {}
            w = width if width is not None else b.get("width", 1)
            d = ([3, 3] if dashed else None) if dashed is not None else (b.get("dashes") or None)
            a.set_border(width=w, dashes=d, clouds=b.get("clouds") or -1)
        _touch(a)
        a.update()
    return xref


def set_contents(doc, pno, xref, text: str) -> int:
    with doc.edit("Edit text", pno):
        a = _load(doc, pno, xref)
        a.set_info(content=text, modDate=_now())
        if a.type[1] == "FreeText":
            rec = _record(doc, a, pno)
            a.update(fontsize=rec.fontsize or 12, text_color=rec.text_color, fill_color=rec.fill)
        else:
            a.update()
    return xref


def set_freetext(doc, pno, xref, text: str, rect) -> int:
    """Replace a text box's text and size in one step."""
    with doc.edit("Edit text", pno):
        a = _load(doc, pno, xref)
        rec = _record(doc, a, pno)
        if rect is not None:
            a.set_rect(pymupdf.Rect(rect))
        a.set_info(content=text, modDate=_now())
        a.update(fontsize=rec.fontsize or 12, text_color=rec.text_color, fill_color=rec.fill)
    return xref


def set_author(doc, pno, xref, author: str):
    with doc.edit("Change author", pno):
        a = _load(doc, pno, xref)
        a.set_info(title=author, modDate=_now())
        a.update()


def delete(doc, pno, xrefs) -> None:
    xrefs = [xrefs] if isinstance(xrefs, int) else list(xrefs)
    label = "Delete comment" if len(xrefs) == 1 else f"Delete {len(xrefs)} comments"
    with doc.edit(label, pno):
        page = doc.page(pno)
        for x in xrefs:
            a = page.load_annot(x)
            if a is not None:
                page.delete_annot(a)  # also removes its popup and replies


def reply(doc, pno, parent_xref, text: str, author: str) -> int:
    with doc.edit("Reply", pno):
        page = doc.page(pno)
        parent = _load(doc, pno, parent_xref)
        r = parent.rect
        a = page.add_text_annot(pymupdf.Point(r.x0, r.y0), text, icon="Comment")
        a.set_irt_xref(parent_xref)
        a.set_flags(pymupdf.PDF_ANNOT_IS_HIDDEN | pymupdf.PDF_ANNOT_IS_NO_VIEW)
        _info(a, author, contents=text)
        a.update()
        return a.xref


def set_status(doc, pno, parent_xref, status: str, author: str) -> int:
    with doc.edit(f"Set status: {status}", pno):
        page = doc.page(pno)
        parent = _load(doc, pno, parent_xref)
        r = parent.rect
        text = f"{status} set by {author}" if status != "None" else f"None set by {author}"
        a = page.add_text_annot(pymupdf.Point(r.x0, r.y0), text, icon="Comment")
        a.set_irt_xref(parent_xref)
        a.set_flags(pymupdf.PDF_ANNOT_IS_HIDDEN | pymupdf.PDF_ANNOT_IS_NO_VIEW)
        doc.doc.xref_set_key(a.xref, "State", pymupdf.get_pdf_str(status))
        doc.doc.xref_set_key(a.xref, "StateModel", pymupdf.get_pdf_str("Review"))
        _info(a, author, contents=text)
        a.update()
        return a.xref


def format_date(pdf_date: str) -> str:
    v = (pdf_date or "")[2:] if (pdf_date or "").startswith("D:") else (pdf_date or "")
    m = re.match(r"(\d{4})(\d{2})(\d{2})(\d{2})?(\d{2})?", v)
    if not m:
        return ""
    y, mo, d, h, mi = m.groups()
    try:
        dt = _dt.datetime(int(y), int(mo), int(d), int(h or 0), int(mi or 0))
    except ValueError:
        return ""
    return dt.strftime("%Y-%m-%d %H:%M")
