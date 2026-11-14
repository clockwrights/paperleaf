"""Recognize text (OCR) in scanned pages, offline, with the Tesseract engine
built into MuPDF. Paperleaf adds an invisible text layer, so the page looks the
same but its text can be searched, selected and copied. No Qt here.

Language data (``<lang>.traineddata``) is looked for in Paperleaf's own resources
(English is included), %APPDATA%\\Paperleaf\\tessdata, a Tesseract-OCR
installation and TESSDATA_PREFIX.
"""
from __future__ import annotations

import glob
import os
import sys

import pymupdf

from paperleaf.core.document import PdfDocument

OCR_MARK = "OCR"   # tags the text layers Paperleaf adds (see pages.MARK_KEY)
LANGUAGE_NAMES = {
    "eng": "English", "spa": "Spanish", "fra": "French", "deu": "German", "ita": "Italian",
    "por": "Portuguese", "nld": "Dutch", "cat": "Catalan", "pol": "Polish", "swe": "Swedish",
    "dan": "Danish", "nor": "Norwegian", "fin": "Finnish", "ces": "Czech", "rus": "Russian",
    "ell": "Greek", "tur": "Turkish", "jpn": "Japanese", "chi_sim": "Chinese (simplified)",
    "chi_tra": "Chinese (traditional)", "kor": "Korean", "ara": "Arabic", "heb": "Hebrew",
}


def _resource_dir() -> str:
    if getattr(sys, "frozen", False):
        base = getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
        return os.path.join(base, "resources")
    return os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "resources")


def tessdata_dirs() -> list[str]:
    dirs = []
    if os.environ.get("PAPERLEAF_TESSDATA"):
        dirs.append(os.environ["PAPERLEAF_TESSDATA"])
    appdata = os.environ.get("APPDATA") or os.path.expanduser("~")
    dirs.append(os.path.join(appdata, "Paperleaf", "tessdata"))
    dirs.append(os.path.join(_resource_dir(), "tessdata"))
    for base in (os.environ.get("ProgramFiles", r"C:\Program Files"), os.environ.get("ProgramFiles(x86)", ""),
                 os.environ.get("LOCALAPPDATA", "")):
        if base:
            dirs.append(os.path.join(base, "Tesseract-OCR", "tessdata"))
            dirs.append(os.path.join(base, "Programs", "Tesseract-OCR", "tessdata"))
    if os.environ.get("TESSDATA_PREFIX"):
        dirs.append(os.environ["TESSDATA_PREFIX"])
    dirs += sorted(glob.glob("/usr/share/tesseract-ocr/*/tessdata")) + ["/usr/share/tessdata",
                                                                       "/usr/local/share/tessdata"]
    seen, out = set(), []
    for d in dirs:
        key = os.path.normcase(os.path.abspath(d))
        if key not in seen and os.path.isdir(d):
            seen.add(key)
            out.append(d)
    return out


def languages() -> dict[str, str]:
    """{code: folder} of the installed OCR languages."""
    out = {}
    for d in tessdata_dirs():
        for f in glob.glob(os.path.join(d, "*.traineddata")):
            code = os.path.splitext(os.path.basename(f))[0]
            if code not in ("osd", "equ") and code not in out:
                out[code] = d
    return out


def language_label(code: str) -> str:
    return " + ".join(LANGUAGE_NAMES.get(c, c) for c in code.split("+"))


def tessdata_for(language: str) -> str | None:
    """A folder that has every language in ``language`` ("eng+spa")."""
    codes = language.split("+")
    for d in tessdata_dirs():
        if all(os.path.exists(os.path.join(d, f"{c}.traineddata")) for c in codes):
            return d
    return None


def available() -> bool:
    return bool(languages())


def page_has_text(doc: PdfDocument, pno: int) -> bool:
    return bool(doc.page_text(pno).text.strip())


def looks_scanned(doc: PdfDocument, max_pages: int = 5) -> bool:
    """No text but images on the first pages: probably a scan."""
    if not doc.is_pdf or not doc.page_count:
        return False
    pages = range(min(doc.page_count, max_pages))
    if any(page_has_text(doc, p) for p in pages):
        return False
    try:
        return any(doc.page(p).get_images() for p in pages)
    except Exception:
        return False


def _ocr_words(page: pymupdf.Page, dpi: int, language: str, tessdata: str) -> list:
    """OCR the page as it is displayed. Words come back in displayed page coordinates."""
    pix = page.get_pixmap(dpi=dpi, annots=False)
    data = pix.pdfocr_tobytes(compress=True, language=language, tessdata=tessdata)
    od = pymupdf.open("pdf", data)
    try:
        op = od[0]
        sx, sy = page.rect.width / op.rect.width, page.rect.height / op.rect.height
        return [(w[0] * sx, w[1] * sy, w[2] * sx, w[3] * sy, w[4], w[5], w[6], w[7])
                for w in op.get_text("words")]
    finally:
        od.close()


def _text_layer(page: pymupdf.Page, words) -> bool:
    """Write invisible text for OCR words: one run per line, scaled to its width.
    ``words`` are in displayed coordinates; the text follows the page rotation."""
    from paperleaf.core.pages import _font_for, _text_width
    lines: dict[tuple, list] = {}
    for w in words:
        lines.setdefault((w[5], w[6]), []).append(w)
    if not lines:
        return False
    shape = page.new_shape()
    wrote = False
    for ws in lines.values():
        ws.sort(key=lambda w: w[0])
        x0, x1 = ws[0][0], ws[-1][2]
        y0 = min(w[1] for w in ws)
        y1 = max(w[3] for w in ws)
        text = " ".join(w[4] for w in ws)
        h = max(1.0, y1 - y0)
        fontname, fontfile = _font_for(text)
        try:
            length = _text_width(text, fontname, fontfile, 1)
        except Exception:
            continue
        size = (x1 - x0) / length if length else h
        size = max(h * 0.4, min(size, h * 1.6))
        start = pymupdf.Point(x0, y1 - h * 0.22) * page.derotation_matrix
        kw = dict(fontsize=size, render_mode=3, rotate=page.rotation, fontname=fontname)
        if fontfile:
            kw["fontfile"] = fontfile
        try:
            shape.insert_text(start, text, **kw)
            wrote = True
        except Exception:
            continue  # characters the font can't show: skip the line
    if wrote:
        shape.commit()
    return wrote


def recognize(doc: PdfDocument, pages=None, language: str = "eng", dpi: int = 300, skip_text: bool = True,
              progress=None) -> int:
    """OCR ``pages`` (default: all) and add invisible text. One undoable step.
    ``progress(i, n)`` may return False to stop. Returns the number of pages done."""
    tessdata = tessdata_for(language)
    if tessdata is None:
        raise RuntimeError(f"The {language_label(language)} text recognition data is not installed.")
    pages = list(range(doc.page_count)) if pages is None else sorted(set(pages))
    if skip_text:
        pages = [p for p in pages if not page_has_text(doc, p)]
    if not pages:
        return 0
    from paperleaf.core.pages import MARK_KEY
    # OCR first (slow, read-only), then change the document in one quick step
    found = {}
    for i, p in enumerate(pages):
        if progress is not None and progress(i, len(pages)) is False:
            break
        words = _ocr_words(doc.page(p), dpi, language, tessdata)
        if words:
            found[p] = words
    if progress is not None:
        progress(len(pages), len(pages))
    if not found:
        return 0
    done = 0
    with doc.edit("Recognize text", list(found), content=True):
        for p, words in found.items():
            page = doc.doc[p]
            before = set(page.get_contents())
            if _text_layer(page, words):
                for x in page.get_contents():
                    if x not in before:
                        doc.doc.xref_set_key(x, MARK_KEY, "/" + OCR_MARK)
                done += 1
    return done


def remove_text_layers(doc: PdfDocument) -> int:
    """Remove the OCR text Paperleaf added."""
    from paperleaf.core import pages as P
    return P.remove_marks(doc, OCR_MARK)
