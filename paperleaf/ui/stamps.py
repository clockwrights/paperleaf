"""Stamp and signature images (drawn with Qt, stored as PNG)."""
from __future__ import annotations

import datetime as _dt
import os
import time

from PySide6.QtCore import QBuffer, QIODevice, QRect, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QFontDatabase, QFontMetricsF, QImage, QPainter, QPen

from paperleaf.core.settings import config_dir

GREEN, RED, BLUE = QColor(0x1E, 0x8E, 0x3E), QColor(0xC6, 0x28, 0x28), QColor(0x1F, 0x5F, 0xBF)

# (label, color, dynamic) - dynamic stamps add "by <author>, <date time>"
STAMPS = [
    ("Approved", GREEN, False), ("Approved", GREEN, True), ("Completed", GREEN, False),
    ("Final", GREEN, False), ("Reviewed", BLUE, True), ("Received", BLUE, True),
    ("Draft", BLUE, False), ("For Comment", BLUE, False), ("For Public Release", BLUE, False),
    ("Not Approved", RED, False), ("Rejected", RED, False), ("Confidential", RED, False),
    ("Void", RED, False), ("Not For Public Release", RED, False),
]

SCRIPT_FONTS = ["Segoe Script", "Ink Free", "Lucida Handwriting", "Bradley Hand ITC", "Freestyle Script",
                "Brush Script MT", "Mistral", "Segoe Print", "Kunstler Script", "Vladimir Script"]

PX_PER_PT = 4.0  # render resolution of stamps


def to_png(img: QImage) -> bytes:
    buf = QBuffer()
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    img.save(buf, "PNG")
    return bytes(buf.data())


def render_stamp(label: str, color: QColor, sub: str | None = None) -> QImage:
    """An Acrobat-like rubber stamp: rounded border, bold capitals, optional second line."""
    title_font = QFont("Segoe UI")
    title_font.setBold(True)
    title_font.setPixelSize(int(22 * PX_PER_PT))
    title_font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 1.5 * PX_PER_PT)
    sub_font = QFont("Segoe UI")
    sub_font.setPixelSize(int(8.5 * PX_PER_PT))
    fm = QFontMetricsF(title_font)
    sfm = QFontMetricsF(sub_font)
    text = label.upper()
    pad = 8 * PX_PER_PT
    tw = fm.horizontalAdvance(text)
    sw = sfm.horizontalAdvance(sub) if sub else 0
    w = int(max(tw, sw) + 2 * pad)
    h = int(fm.height() + (sfm.height() + 2 * PX_PER_PT if sub else 0) + 1.2 * pad)
    img = QImage(w, h, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
    bw = 2.2 * PX_PER_PT
    fill = QColor(color)
    fill.setAlpha(28)
    p.setBrush(fill)
    p.setPen(QPen(color, bw))
    p.drawRoundedRect(QRectF(bw / 2, bw / 2, w - bw, h - bw), 5 * PX_PER_PT, 5 * PX_PER_PT)
    p.setPen(color)
    p.setFont(title_font)
    top = 0.6 * pad
    p.drawText(QRectF(0, top, w, fm.height()), Qt.AlignmentFlag.AlignCenter, text)
    if sub:
        p.setFont(sub_font)
        p.drawText(QRectF(0, top + fm.height() + PX_PER_PT, w, sfm.height()), Qt.AlignmentFlag.AlignCenter, sub)
    p.end()
    return img


def dynamic_line(author: str) -> str:
    now = _dt.datetime.now()
    who = f"By {author} " if author else ""
    return f"{who}at {now.strftime('%I:%M %p').lstrip('0')}, {now.strftime('%b %d, %Y')}"


def size_in_points(img: QImage, px_per_pt: float = PX_PER_PT) -> tuple[float, float]:
    return img.width() / px_per_pt, img.height() / px_per_pt


# ---------------------------------------------------------------- signatures
def _dir(name: str) -> str:
    path = os.path.join(config_dir(), name)
    os.makedirs(path, exist_ok=True)
    return path


def signatures_dir() -> str:
    return _dir("signatures")


def custom_stamps_dir() -> str:
    return _dir("stamps")


def list_images(folder: str) -> list[str]:
    try:
        files = [os.path.join(folder, f) for f in os.listdir(folder) if f.lower().endswith(".png")]
    except OSError:
        return []
    return sorted(files, key=os.path.getmtime)


def save_image(img: QImage, folder: str, prefix: str) -> str:
    path = os.path.join(folder, f"{prefix}_{int(time.time() * 1000)}.png")
    img.save(path, "PNG")
    return path


def crop_to_content(img: QImage, margin: int = 6) -> QImage:
    img = img.convertToFormat(QImage.Format.Format_ARGB32)
    w, h = img.width(), img.height()
    bpl = img.bytesPerLine()
    buf = bytes(img.constBits())  # BGRA byte order on little-endian
    x0, y0, x1, y1 = w, h, -1, -1
    for y in range(h):
        alphas = buf[y * bpl + 3: y * bpl + w * 4: 4]
        if alphas.strip(b"\x00"):
            if y < y0:
                y0 = y
            y1 = y
            first = len(alphas) - len(alphas.lstrip(b"\x00"))
            last = len(alphas.rstrip(b"\x00")) - 1
            x0 = min(x0, first)
            x1 = max(x1, last)
    if x1 < 0:
        return img
    r = QRect(max(0, x0 - margin), max(0, y0 - margin),
              min(w, x1 + margin + 1) - max(0, x0 - margin), min(h, y1 + margin + 1) - max(0, y0 - margin))
    return img.copy(r)


def remove_white(img: QImage, threshold: int = 225) -> QImage:
    """Make a scanned/photographed signature's paper background transparent."""
    img = img.convertToFormat(QImage.Format.Format_ARGB32)
    out = QImage(img.size(), QImage.Format.Format_ARGB32)
    for y in range(img.height()):
        for x in range(img.width()):
            c = img.pixelColor(x, y)
            lum = (c.red() * 299 + c.green() * 587 + c.blue() * 114) // 1000
            if lum >= threshold or c.alpha() == 0:
                out.setPixelColor(x, y, QColor(0, 0, 0, 0))
            else:
                a = int(255 * min(1.0, (threshold - lum) / max(1, threshold - 60)))
                c.setAlpha(max(a, 40))
                out.setPixelColor(x, y, c)
    return out


def typed_signature(name: str, family: str, color: QColor) -> QImage:
    font = QFont(family)
    font.setPixelSize(140)
    fm = QFontMetricsF(font)
    w = int(fm.horizontalAdvance(name) + 80)
    h = int(fm.height() + 60)
    img = QImage(w, h, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
    p.setFont(font)
    p.setPen(color)
    p.drawText(QRectF(0, 0, w, h), Qt.AlignmentFlag.AlignCenter, name)
    p.end()
    return crop_to_content(img)


def available_script_fonts() -> list[str]:
    fams = set(QFontDatabase.families())
    found = [f for f in SCRIPT_FONTS if f in fams]
    return found or ["Segoe UI"]
