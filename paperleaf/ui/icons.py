"""Toolbar icons drawn from the Windows "Segoe Fluent Icons" font.

Icons repaint with the current palette, so they follow light/dark mode.
"""
from __future__ import annotations

from PySide6.QtCore import QRect, QRectF, QSize, Qt
from PySide6.QtGui import QFont, QFontDatabase, QIcon, QIconEngine, QPainter, QPalette, QPixmap
from PySide6.QtWidgets import QApplication

GLYPHS = {
    "open": 0xE838, "print": 0xE749, "save": 0xE74E, "zoom_in": 0xE8A3, "zoom_out": 0xE71F,
    "search": 0xE721, "up": 0xE70E, "down": 0xE70D, "left": 0xE76B, "right": 0xE76C,
    "back": 0xE72B, "forward": 0xE72A, "rotate": 0xE7AD, "rotate_ccw": 0xE7A7, "copy": 0xE8C8,
    "settings": 0xE713, "menu": 0xE700, "sidebar": 0xE8A0, "fullscreen": 0xE740,
    "exit_fullscreen": 0xE73F, "presentation": 0xE8AE, "select": 0xE8B0, "text": 0xE933,
    "hand": 0xE7C2, "marquee": 0xE71E, "snapshot": 0xE722, "night": 0xE7A1, "moon": 0xE708,
    "sun": 0xE706, "info": 0xE946, "page": 0xE7C3, "two_page": 0xE736, "thumbnails": 0xE8A9,
    "bookmarks": 0xE8A4, "attach": 0xE723, "close": 0xE711, "fit_page": 0xE9A6,
    "fit_width": 0xE799, "read": 0xE736, "help": 0xE9CE, "folder": 0xE8B7, "home": 0xE80F,
    "first": 0xE892, "last": 0xE893, "pin": 0xE718, "check": 0xE73E, "doc": 0xE8A5,
}

_FONT_FAMILY = None


def _family() -> str:
    global _FONT_FAMILY
    if _FONT_FAMILY is None:
        fams = QFontDatabase.families()
        _FONT_FAMILY = next((f for f in ("Segoe Fluent Icons", "Segoe MDL2 Assets") if f in fams),
                            "Segoe UI Symbol")
    return _FONT_FAMILY


class GlyphIconEngine(QIconEngine):
    def __init__(self, codepoint: int, color=None):
        super().__init__()
        self.codepoint = codepoint
        self.color = color

    def clone(self):
        return GlyphIconEngine(self.codepoint, self.color)

    def paint(self, painter: QPainter, rect: QRect, mode: QIcon.Mode, state: QIcon.State):
        pal = QApplication.palette()
        if self.color is not None:
            color = self.color
        elif mode == QIcon.Mode.Disabled:
            color = pal.color(QPalette.ColorGroup.Disabled, QPalette.ColorRole.WindowText)
        else:
            color = pal.color(QPalette.ColorRole.WindowText)
        font = QFont(_family())
        font.setPixelSize(max(8, int(min(rect.width(), rect.height()) * 0.82)))
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        painter.setFont(font)
        painter.setPen(color)
        painter.drawText(QRectF(rect), Qt.AlignmentFlag.AlignCenter, chr(self.codepoint))
        painter.restore()

    def pixmap(self, size: QSize, mode: QIcon.Mode, state: QIcon.State) -> QPixmap:
        pm = QPixmap(size)
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        self.paint(p, QRect(0, 0, size.width(), size.height()), mode, state)
        p.end()
        return pm

    def scaledPixmap(self, size, mode, state, scale):
        pm = self.pixmap(QSize(int(size.width() * scale), int(size.height() * scale)), mode, state)
        pm.setDevicePixelRatio(scale)
        return pm


_cache: dict = {}


def icon(name: str) -> QIcon:
    if name not in _cache:
        _cache[name] = QIcon(GlyphIconEngine(GLYPHS[name]))
    return _cache[name]


# ------------------------------------------------------------ annotation icons
GLYPHS.update({
    "highlight": 0xE7E6, "underline": 0xE8DC, "note": 0xE8BD, "comments": 0xE90A, "eraser": 0xED60,
    "pen": 0xED63, "sign": 0xEE56, "checkmark": 0xE8FB, "crossmark": 0xE711, "date": 0xE787,
    "undo": 0xE7A7, "redo": 0xE7A6, "delete": 0xE74D, "reply": 0xE97A, "color": 0xE790,
    "comment_mode": 0xE90A,
    # page tools (Segoe MDL2 / Fluent)
    "insert": 0xE710, "replace": 0xE8AB, "extract": 0xE72D, "split": 0xE8C6, "crop": 0xE7A8,
    "organize": 0xE8A9, "combine": 0xE8F1, "watermark": 0xE7E8, "header": 0xE8D2,
    "lock": 0xE72E, "unlock": 0xE785, "export": 0xEDE1, "compress": 0xE8B7, "clean": 0xE894,
    # advanced tools
    "edit": 0xE70F, "font": 0xE8D2, "image": 0xEB9F, "ocr": 0xE8FE, "speak": 0xE767, "play": 0xE768,
    "pause": 0xE769, "stop": 0xE71A, "compare": 0xE8FD, "certificate": 0xEB95,
})


class PaintIconEngine(QIconEngine):
    """Icon drawn by a function(painter, rect, color); follows the palette like glyph icons."""

    def __init__(self, fn):
        super().__init__()
        self.fn = fn

    def clone(self):
        return PaintIconEngine(self.fn)

    def paint(self, painter, rect, mode, state):
        pal = QApplication.palette()
        color = pal.color(QPalette.ColorGroup.Disabled if mode == QIcon.Mode.Disabled else QPalette.ColorGroup.Active,
                          QPalette.ColorRole.WindowText)
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.fn(painter, QRectF(rect), color)
        painter.restore()

    def pixmap(self, size, mode, state):
        pm = QPixmap(size)
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        self.paint(p, QRect(0, 0, size.width(), size.height()), mode, state)
        p.end()
        return pm

    def scaledPixmap(self, size, mode, state, scale):
        pm = self.pixmap(QSize(int(size.width() * scale), int(size.height() * scale)), mode, state)
        pm.setDevicePixelRatio(scale)
        return pm


def _pen(color, w):
    from PySide6.QtGui import QPen
    pen = QPen(color, w)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    return pen


def _letter(p, r, color, text, size=0.62, dy=0.0):
    f = QFont("Segoe UI")
    f.setPixelSize(max(6, int(r.height() * size)))
    p.setFont(f)
    p.setPen(color)
    p.drawText(r.adjusted(0, -r.height() * 0.08 + dy * r.height(), 0, 0), Qt.AlignmentFlag.AlignCenter, text)


def _draw(name, p, r, color):
    from PySide6.QtCore import QPointF
    from PySide6.QtGui import QPainterPath, QPolygonF
    s = min(r.width(), r.height())
    m = s * 0.16
    b = QRectF(r.center().x() - s / 2 + m, r.center().y() - s / 2 + m, s - 2 * m, s - 2 * m)
    w = max(1.0, s / 14)
    p.setPen(_pen(color, w))
    p.setBrush(Qt.BrushStyle.NoBrush)
    if name == "rect":
        p.drawRect(b.adjusted(0, s * 0.08, 0, -s * 0.08))
    elif name == "ellipse":
        p.drawEllipse(b.adjusted(0, s * 0.06, 0, -s * 0.06))
    elif name == "line":
        p.drawLine(b.bottomLeft(), b.topRight())
    elif name == "arrow":
        p.drawLine(b.bottomLeft(), b.topRight())
        t = b.topRight()
        p.drawLine(t, QPointF(t.x() - s * 0.3, t.y()))
        p.drawLine(t, QPointF(t.x(), t.y() + s * 0.3))
    elif name == "polygon":
        poly = QPolygonF([QPointF(b.left() + b.width() * 0.5, b.top()), QPointF(b.right(), b.top() + b.height() * 0.4),
                          QPointF(b.right() - b.width() * 0.15, b.bottom()), QPointF(b.left() + b.width() * 0.15, b.bottom()),
                          QPointF(b.left(), b.top() + b.height() * 0.4)])
        p.drawPolygon(poly)
    elif name == "polyline":
        path = QPainterPath(b.bottomLeft())
        path.lineTo(b.left() + b.width() * 0.35, b.top() + b.height() * 0.3)
        path.lineTo(b.left() + b.width() * 0.65, b.bottom() - b.height() * 0.25)
        path.lineTo(b.topRight())
        p.drawPath(path)
    elif name == "cloud":
        path = QPainterPath()
        cr = b.adjusted(0, s * 0.12, 0, -s * 0.12)
        n = 4
        for i in range(n):
            seg = QRectF(cr.left() + i * cr.width() / n, cr.top() - s * 0.08, cr.width() / n, s * 0.16)
            path.arcMoveTo(seg, 180)
            path.arcTo(seg, 180, -180)
            seg2 = QRectF(cr.left() + i * cr.width() / n, cr.bottom() - s * 0.08, cr.width() / n, s * 0.16)
            path.arcMoveTo(seg2, 0)
            path.arcTo(seg2, 0, -180)
        for side in (cr.left(), cr.right()):
            seg = QRectF(side - s * 0.08, cr.top(), s * 0.16, cr.height())
            path.arcMoveTo(seg, 90)
            path.arcTo(seg, 90, 180 if side == cr.left() else -180)
        p.drawPath(path)
    elif name == "strikeout":
        _letter(p, r, color, "S")
        p.setPen(_pen(color, w))
        p.drawLine(QPointF(b.left(), r.center().y()), QPointF(b.right(), r.center().y()))
    elif name == "squiggly":
        _letter(p, r, color, "ab", size=0.5, dy=-0.08)
        p.setPen(_pen(color, w))
        path = QPainterPath(QPointF(b.left(), b.bottom() - s * 0.04))
        steps = 6
        for i in range(1, steps + 1):
            x = b.left() + b.width() * i / steps
            y = b.bottom() - s * (0.12 if i % 2 else 0.02)
            path.lineTo(x, y)
        p.drawPath(path)
    elif name == "textbox":
        p.drawRect(b)
        _letter(p, r, color, "T", size=0.5)
    elif name == "callout":
        box = QRectF(b.left() + b.width() * 0.3, b.top(), b.width() * 0.7, b.height() * 0.6)
        p.drawRect(box)
        p.drawLine(QPointF(box.left(), box.center().y()), b.bottomLeft())
    elif name == "filltext":
        _letter(p, r, color, "Ab", size=0.55)
        p.setPen(_pen(color, w))
        p.drawLine(QPointF(b.right(), b.top() + s * 0.05), QPointF(b.right(), b.bottom() - s * 0.05))
    elif name == "dot":
        p.setBrush(color)
        p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(r.center(), s * 0.18, s * 0.18)
    elif name == "stamp":
        top = QRectF(r.center().x() - s * 0.12, b.top(), s * 0.24, s * 0.32)
        p.drawRoundedRect(top, s * 0.08, s * 0.08)
        p.drawLine(QPointF(r.center().x(), top.bottom()), QPointF(r.center().x(), top.bottom() + s * 0.1))
        base = QRectF(b.left(), top.bottom() + s * 0.1, b.width(), s * 0.18)
        p.drawRoundedRect(base, s * 0.04, s * 0.04)
        p.drawLine(QPointF(b.left() + s * 0.05, b.bottom()), QPointF(b.right() - s * 0.05, b.bottom()))
    elif name == "field_text":
        box = b.adjusted(0, s * 0.2, 0, -s * 0.2)
        p.drawRect(box)
        x = box.left() + box.width() * 0.28
        p.drawLine(QPointF(x, box.top() + s * 0.08), QPointF(x, box.bottom() - s * 0.08))
    elif name == "field_check":
        box = b.adjusted(s * 0.06, s * 0.06, -s * 0.06, -s * 0.06)
        p.drawRect(box)
        path = QPainterPath(QPointF(box.left() + box.width() * 0.2, box.top() + box.height() * 0.55))
        path.lineTo(box.left() + box.width() * 0.42, box.bottom() - box.height() * 0.2)
        path.lineTo(box.right() - box.width() * 0.18, box.top() + box.height() * 0.22)
        p.drawPath(path)
    elif name == "field_radio":
        p.drawEllipse(r.center(), s * 0.32, s * 0.32)
        p.setBrush(color)
        p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(r.center(), s * 0.14, s * 0.14)
    elif name == "field_combo":
        box = b.adjusted(0, s * 0.2, 0, -s * 0.2)
        p.drawRect(box)
        split = box.right() - box.height()
        p.drawLine(QPointF(split, box.top()), QPointF(split, box.bottom()))
        c = QPointF((split + box.right()) / 2, box.center().y())
        p.setBrush(color)
        p.setPen(Qt.PenStyle.NoPen)
        p.drawPolygon(QPolygonF([QPointF(c.x() - s * 0.08, c.y() - s * 0.04), QPointF(c.x() + s * 0.08, c.y() - s * 0.04),
                                 QPointF(c.x(), c.y() + s * 0.06)]))
    elif name == "field_list":
        p.drawRect(b)
        for i in range(3):
            y = b.top() + b.height() * (0.27 + i * 0.23)
            p.drawLine(QPointF(b.left() + s * 0.12, y), QPointF(b.right() - s * 0.12, y))
    elif name == "field_sig":
        box = b.adjusted(0, s * 0.18, 0, -s * 0.18)
        p.drawRect(box)
        path = QPainterPath(QPointF(box.left() + s * 0.1, box.center().y() + s * 0.05))
        path.cubicTo(QPointF(box.left() + s * 0.25, box.top()), QPointF(box.left() + s * 0.3, box.bottom()),
                     QPointF(box.left() + s * 0.45, box.center().y()))
        path.lineTo(box.right() - s * 0.1, box.center().y())
        p.drawPath(path)
    elif name == "redact":
        for i in range(3):
            y = b.top() + b.height() * (0.18 + i * 0.32)
            if i == 1:
                p.fillRect(QRectF(b.left(), y - s * 0.09, b.width(), s * 0.18), color)
            else:
                p.drawLine(QPointF(b.left(), y), QPointF(b.right(), y))
    elif name == "form":
        page = QRectF(b.left() + s * 0.08, b.top(), b.width() - s * 0.16, b.height())
        p.drawRect(page)
        for i in range(3):
            y = page.top() + page.height() * (0.22 + i * 0.28)
            p.drawLine(QPointF(page.left() + s * 0.08, y), QPointF(page.left() + s * 0.2, y))
            p.drawRect(QRectF(page.left() + s * 0.26, y - s * 0.06, page.width() - s * 0.36, s * 0.12))


PAINTED = {"rect", "ellipse", "line", "arrow", "polygon", "polyline", "cloud", "strikeout", "squiggly",
           "textbox", "callout", "filltext", "dot", "stamp", "field_text", "field_check", "field_radio",
           "field_combo", "field_list", "field_sig", "form", "redact"}


def painted_icon(name: str) -> QIcon:
    key = "painted:" + name
    if key not in _cache:
        _cache[key] = QIcon(PaintIconEngine(lambda p, r, c, n=name: _draw(n, p, r, c)))
    return _cache[key]


def color_swatch(rgb, size=16) -> QIcon:
    """Small square showing a color (or a crossed box for 'none')."""
    from PySide6.QtGui import QColor, QPen
    pm = QPixmap(size * 2, size * 2)
    pm.setDevicePixelRatio(2)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    r = QRectF(1.5, 1.5, size - 3, size - 3)
    if rgb:
        p.setBrush(QColor.fromRgbF(*rgb[:3]))
        p.setPen(QPen(QColor(0, 0, 0, 90), 1))
        p.drawRoundedRect(r, 2, 2)
    else:
        p.setBrush(QColor("white"))
        p.setPen(QPen(QColor(0, 0, 0, 120), 1))
        p.drawRoundedRect(r, 2, 2)
        p.setPen(QPen(QColor(200, 0, 0), 1.5))
        p.drawLine(r.bottomLeft(), r.topRight())
    p.end()
    return QIcon(pm)


_orig_icon = icon


def icon(name: str) -> QIcon:  # noqa: F811  (extends the glyph-only version)
    if name in PAINTED:
        return painted_icon(name)
    return _orig_icon(name)
