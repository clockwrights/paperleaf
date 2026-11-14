"""Edit PDF: change existing text (line by line) and move, resize, replace,
delete or add images and text. No Qt here.

Simple edits work well (change a word, fix a number, move a logo). A line is
edited as a whole: the old line is removed with a text-only redaction and the
new text is written in its place, in the original font when that font has the
needed characters, otherwise in a similar standard font. Paragraphs are not
reflowed. Every change is one undoable step.
"""
from __future__ import annotations

import math
import os
from dataclasses import dataclass, field

import pymupdf

from paperleaf.core.document import PdfDocument

SERIF, MONO, BOLD, ITALIC = 4, 8, 16, 2   # PyMuPDF span flags


@dataclass
class TextLine:
    pno: int
    rect: pymupdf.Rect          # unrotated page coordinates
    text: str
    font: str
    size: float
    color: tuple
    flags: int
    origin: tuple               # baseline start
    direction: tuple = (1.0, 0.0)
    spans: list = field(default_factory=list)

    @property
    def editable(self) -> bool:
        return abs(self.direction[1]) < 0.01 and self.direction[0] > 0  # horizontal, left to right

    @property
    def bold(self) -> bool:
        return bool(self.flags & BOLD) or "bold" in self.font.lower()

    @property
    def italic(self) -> bool:
        return bool(self.flags & ITALIC) or any(k in self.font.lower() for k in ("italic", "oblique"))


@dataclass
class ImageBox:
    pno: int
    xref: int
    rect: pymupdf.Rect          # unrotated page coordinates
    transform: tuple
    width: int
    height: int

    @property
    def angle(self) -> int:
        a, b = self.transform[0], self.transform[1]
        return int(round(math.degrees(math.atan2(b, a)) / 90.0)) * 90 % 360


def _rgb(c: int) -> tuple:
    return ((c >> 16) & 255) / 255, ((c >> 8) & 255) / 255, (c & 255) / 255


def text_lines(doc: PdfDocument, pno: int) -> list[TextLine]:
    key = ("edit-lines", pno)
    if key in doc._forms:
        return doc._forms[key]
    out = []
    page = doc.text_doc.load_page(pno)
    flags = pymupdf.TEXT_PRESERVE_WHITESPACE | pymupdf.TEXT_MEDIABOX_CLIP
    for block in page.get_text("dict", flags=flags).get("blocks", []):
        if block.get("type", 0) != 0:
            continue
        for line in block.get("lines", []):
            # invisible text (like recognized text over a scan) is not edited here
            spans = [s for s in line.get("spans", []) if s.get("text", "") and s.get("alpha", 255) > 0]
            text = "".join(s["text"] for s in spans)
            if not text.strip():
                continue
            main = max(spans, key=lambda s: len(s["text"].strip()))
            out.append(TextLine(pno, pymupdf.Rect(line["bbox"]), text.rstrip(), main["font"], main["size"],
                                _rgb(main.get("color", 0)), main.get("flags", 0), tuple(spans[0]["origin"]),
                                tuple(line.get("dir", (1, 0))), spans))
    doc._forms[key] = out
    return out


def line_at(doc: PdfDocument, pno: int, x: float, y: float, slack: float = 1.5) -> TextLine | None:
    best = None
    for ln in text_lines(doc, pno):
        r = ln.rect
        if r.x0 - slack <= x <= r.x1 + slack and r.y0 - slack <= y <= r.y1 + slack:
            if best is None or r.height < best.rect.height:
                best = ln
    return best


def images(doc: PdfDocument, pno: int) -> list[ImageBox]:
    key = ("edit-images", pno)
    if key in doc._forms:
        return doc._forms[key]
    out = []
    try:
        for info in doc.page(pno).get_image_info(xrefs=True):
            if info.get("xref", 0) <= 0:
                continue  # inline image: can't be moved
            r = pymupdf.Rect(info["bbox"])
            if r.is_empty or r.width < 2 or r.height < 2:
                continue
            out.append(ImageBox(pno, info["xref"], r, tuple(info["transform"]), info["width"], info["height"]))
    except Exception:
        pass
    doc._forms[key] = out
    return out


def image_at(doc: PdfDocument, pno: int, x: float, y: float) -> ImageBox | None:
    for img in reversed(images(doc, pno)):
        if img.rect.contains(pymupdf.Point(x, y)):
            return img
    return None


# --------------------------------------------------------------- fonts
_BASE14 = {  # (serif, mono, bold, italic) -> PyMuPDF base-14 name
    (False, False, False, False): "helv", (False, False, True, False): "hebo",
    (False, False, False, True): "heit", (False, False, True, True): "hebi",
    (True, False, False, False): "tiro", (True, False, True, False): "tibo",
    (True, False, False, True): "tiit", (True, False, True, True): "tibi",
    (False, True, False, False): "cour", (False, True, True, False): "cobo",
    (False, True, False, True): "coit", (False, True, True, True): "cobi",
}


def _base14_for(line: TextLine) -> str:
    name = line.font.lower()
    serif = bool(line.flags & SERIF) or any(k in name for k in ("times", "serif", "roman", "georgia", "garamond")) \
        and "sans" not in name
    mono = bool(line.flags & MONO) or any(k in name for k in ("courier", "mono", "consol"))
    return _BASE14[(serif and not mono, mono, line.bold, line.italic)]


def _embedded_font(doc: PdfDocument, pno: int, line: TextLine, text: str):
    """(fontbuffer, Font) of the line's own font if it can show ``text``."""
    want = line.font.split("+", 1)[-1]
    try:
        for f in doc.doc.get_page_fonts(pno):
            xref, base = f[0], (f[3] or "").split("+", 1)[-1]
            if base != want:
                continue
            _name, ext, _type, buf = doc.doc.extract_font(xref)
            if not buf or ext in ("n/a", ""):
                return None
            font = pymupdf.Font(fontbuffer=buf)
            if all(font.has_glyph(ord(ch)) or ch.isspace() for ch in text):
                return buf, font
            return None
    except Exception:
        return None
    return None


def _write_line(doc: PdfDocument, page: pymupdf.Page, pno: int, line: TextLine, text: str):
    emb = _embedded_font(doc, pno, line, text)
    x, y = line.origin
    if emb is not None:
        buf, _font = emb
        name = f"PaperleafE{abs(hash(line.font)) % 100000}"
        page.insert_font(fontname=name, fontbuffer=buf)
        page.insert_text((x, y), text, fontname=name, fontsize=line.size, color=line.color)
        return
    from paperleaf.core.pages import _font_for
    fname, ffile = _font_for(text)
    if ffile:
        page.insert_text((x, y), text, fontname=fname, fontfile=ffile, fontsize=line.size, color=line.color)
    else:
        page.insert_text((x, y), text, fontname=_base14_for(line), fontsize=line.size, color=line.color)


def _remove_text(page: pymupdf.Page, rect: pymupdf.Rect):
    """Remove only the text inside ``rect`` (pictures and drawings stay)."""
    h = rect.height
    r = pymupdf.Rect(rect.x0 - 0.5, rect.y0 + h * 0.2, rect.x1 + 0.5, rect.y1 - h * 0.2)
    page.add_redact_annot(r)
    page.apply_redactions(images=pymupdf.PDF_REDACT_IMAGE_NONE, graphics=pymupdf.PDF_REDACT_LINE_ART_NONE,
                          text=pymupdf.PDF_REDACT_TEXT_REMOVE)


def replace_line(doc: PdfDocument, line: TextLine, text: str) -> bool:
    """Replace a line of text (empty ``text`` deletes it)."""
    text = text.replace("\n", " ").rstrip()
    if text == line.text:
        return False
    if not line.editable:
        raise ValueError("Only straight, left-to-right lines can be edited.")
    with doc.edit("Edit text" if text else "Delete text", line.pno, content=True):
        page = doc.doc[line.pno]
        _remove_text(page, line.rect)
        if text:
            _write_line(doc, page, line.pno, line, text)
    return True


def add_text(doc: PdfDocument, pno: int, point, text: str, size: float = 12, color=(0, 0, 0)) -> None:
    """Add new text to the page content (baseline at ``point``, unrotated coordinates)."""
    if not text.strip():
        return
    from paperleaf.core.pages import _font_for
    fname, ffile = _font_for(text)
    with doc.edit("Add text", pno, content=True):
        page = doc.doc[pno]
        lines = text.splitlines()
        for i, ln in enumerate(lines):
            p = (point[0], point[1] + i * size * 1.2)
            kw = dict(fontsize=size, color=color, rotate=0)
            if ffile:
                page.insert_text(p, ln, fontname=fname, fontfile=ffile, **kw)
            else:
                page.insert_text(p, ln, fontname=fname, **kw)


# --------------------------------------------------------------- images
def _image_bytes(doc: PdfDocument, xref: int) -> bytes:
    info = doc.doc.extract_image(xref)
    if info.get("smask"):
        base = pymupdf.Pixmap(doc.doc, xref)
        if base.alpha:
            base = pymupdf.Pixmap(base, 0)
        if base.colorspace and base.colorspace.n not in (1, 3):
            base = pymupdf.Pixmap(pymupdf.csRGB, base)
        mask = pymupdf.Pixmap(doc.doc, info["smask"])
        return pymupdf.Pixmap(base, mask).tobytes("png")
    return info["image"]


def _remove_image(page: pymupdf.Page, img: ImageBox):
    r = pymupdf.Rect(img.rect)
    inset = min(r.width, r.height) * 0.1
    page.add_redact_annot(pymupdf.Rect(r.x0 + inset, r.y0 + inset, r.x1 - inset, r.y1 - inset))
    page.apply_redactions(images=pymupdf.PDF_REDACT_IMAGE_REMOVE, graphics=pymupdf.PDF_REDACT_LINE_ART_NONE,
                          text=pymupdf.PDF_REDACT_TEXT_NONE)


def move_image(doc: PdfDocument, img: ImageBox, rect, label: str = "Move image") -> None:
    rect = pymupdf.Rect(rect).normalize()
    data = _image_bytes(doc, img.xref)
    with doc.edit(label, img.pno, content=True):
        page = doc.doc[img.pno]
        _remove_image(page, img)
        page.insert_image(rect, stream=data, keep_proportion=False, rotate=img.angle)


def replace_image(doc: PdfDocument, img: ImageBox, path: str) -> None:
    with doc.edit("Replace image", img.pno, content=True):
        page = doc.doc[img.pno]
        _remove_image(page, img)
        page.insert_image(img.rect, filename=path, keep_proportion=True)


def delete_image(doc: PdfDocument, img: ImageBox) -> None:
    with doc.edit("Delete image", img.pno, content=True):
        _remove_image(doc.doc[img.pno], img)


def add_image(doc: PdfDocument, pno: int, rect, path: str) -> None:
    if not os.path.isfile(path):
        raise ValueError("Choose an image file.")
    with doc.edit("Add image", pno, content=True):
        doc.doc[pno].insert_image(pymupdf.Rect(rect), filename=path, keep_proportion=True)


def image_size_for(path: str, max_w: float = 200) -> tuple[float, float]:
    pix = pymupdf.Pixmap(path)
    w, h = pix.width, pix.height
    s = min(1.0, max_w / max(1, w))
    return w * s, h * s
