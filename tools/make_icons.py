"""Draw Paperleaf's application and document icons and save them as .ico files."""
import io
import os
import sys

from PIL import Image
from PySide6.QtCore import QBuffer, QIODevice, QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QGuiApplication, QImage, QLinearGradient, QPainter, QPainterPath, QPen

SIZES = [16, 20, 24, 32, 40, 48, 64, 96, 128, 256]
OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "resources")


def _page_path(x, y, w, h, fold):
    p = QPainterPath()
    p.moveTo(x, y)
    p.lineTo(x + w - fold, y)
    p.lineTo(x + w, y + fold)
    p.lineTo(x + w, y + h)
    p.lineTo(x, y + h)
    p.closeSubpath()
    return p


def draw_app(s: int) -> QImage:
    img = QImage(s, s, QImage.Format.Format_ARGB32)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    r = s * 0.2
    g = QLinearGradient(0, 0, s, s)
    g.setColorAt(0, QColor("#E5484D"))
    g.setColorAt(1, QColor("#B4232A"))
    p.setBrush(g)
    p.setPen(Qt.PenStyle.NoPen)
    p.drawRoundedRect(QRectF(s * 0.04, s * 0.04, s * 0.92, s * 0.92), r, r)
    # white page
    pw, ph = s * 0.50, s * 0.64
    px, py = (s - pw) / 2, (s - ph) / 2
    fold = pw * 0.30
    p.setBrush(QColor("white"))
    p.drawPath(_page_path(px, py, pw, ph, fold))
    p.setBrush(QColor("#F2C4C6"))
    tri = QPainterPath()
    tri.moveTo(px + pw - fold, py)
    tri.lineTo(px + pw - fold, py + fold)
    tri.lineTo(px + pw, py + fold)
    tri.closeSubpath()
    p.drawPath(tri)
    # text lines
    if s >= 24:
        p.setPen(QPen(QColor("#C9343A"), max(1.0, s * 0.045), Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        lx0, lx1 = px + pw * 0.18, px + pw * 0.82
        for i, frac in enumerate((0.48, 0.62, 0.76)):
            y = py + ph * frac
            p.drawLine(QPointF(lx0, y), QPointF(lx1 if i < 2 else lx0 + (lx1 - lx0) * 0.6, y))
    p.end()
    return img


def draw_doc(s: int) -> QImage:
    img = QImage(s, s, QImage.Format.Format_ARGB32)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    pw, ph = s * 0.72, s * 0.92
    px, py = (s - pw) / 2, (s - ph) / 2
    fold = pw * 0.28
    p.setPen(QPen(QColor("#9AA0A6"), max(1.0, s / 64)))
    p.setBrush(QColor("white"))
    p.drawPath(_page_path(px, py, pw, ph, fold))
    p.setBrush(QColor("#E8EAED"))
    tri = QPainterPath()
    tri.moveTo(px + pw - fold, py)
    tri.lineTo(px + pw - fold, py + fold)
    tri.lineTo(px + pw, py + fold)
    tri.closeSubpath()
    p.drawPath(tri)
    # red label
    bh = ph * 0.30
    by = py + ph * 0.58
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor("#D93A40"))
    p.drawRoundedRect(QRectF(px - s * 0.06, by, pw * 0.9, bh), s * 0.03, s * 0.03)
    if s >= 24:
        f = QFont("Segoe UI")
        f.setBold(True)
        f.setPixelSize(max(6, int(bh * 0.62)))
        p.setFont(f)
        p.setPen(QColor("white"))
        p.drawText(QRectF(px - s * 0.06, by, pw * 0.9, bh), Qt.AlignmentFlag.AlignCenter, "PDF")
    p.end()
    return img


def to_pil(img: QImage) -> Image.Image:
    buf = QBuffer()
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    img.save(buf, "PNG")
    return Image.open(io.BytesIO(bytes(buf.data()))).convert("RGBA")


def save_ico(draw, path):
    imgs = [to_pil(draw(s)) for s in SIZES]
    big = imgs[-1]
    big.save(path, format="ICO", sizes=[(s, s) for s in SIZES], append_images=imgs[:-1])
    big.save(path.replace(".ico", ".png"))


if __name__ == "__main__":
    app = QGuiApplication(sys.argv)
    os.makedirs(OUT, exist_ok=True)
    save_ico(draw_app, os.path.join(OUT, "paperleaf.ico"))
    save_ico(draw_doc, os.path.join(OUT, "pdf_document.ico"))
    print("icons written to", OUT)
