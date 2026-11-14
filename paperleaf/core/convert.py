"""Reduce file size and convert: PDF to Word, images, text and HTML; images and
Office files to PDF. Every function writes new files; the open document is
never changed. Nothing here uses the network.
"""
from __future__ import annotations

import base64
import html
import os
import shutil
import tempfile

import pymupdf

from paperleaf.core import pages as P
from paperleaf.core.document import PdfDocument

# ================================================================= reduce size
PRESETS = {  # name -> (image dpi, JPEG quality)
    "high": (220, 85),
    "medium": (150, 72),
    "small": (96, 55),
}
PRESET_LABELS = [("High quality (print)", "high"), ("Medium (screen)", "medium"), ("Smallest file", "small")]


def compress(doc: PdfDocument, path: str, dpi: int = 150, quality: int = 72, subset_fonts: bool = True,
             gray: bool = False, remove_metadata: bool = False) -> tuple[int, int]:
    """Save a smaller copy. Returns (size before, size after) in bytes."""
    before = len(doc.current_bytes())
    copy = P.fresh_copy(doc)
    try:
        try:
            copy.rewrite_images(dpi_threshold=int(dpi * 1.25), dpi_target=int(dpi), quality=int(quality),
                                lossy=True, lossless=True, bitonal=True, color=True, gray=True,
                                set_to_gray=gray)
        except Exception:
            pass  # unusual images: keep them as they are
        if subset_fonts:
            try:
                copy.subset_fonts()
            except Exception:
                pass
        if remove_metadata:
            copy.set_metadata({})
            copy.del_xml_metadata()
        for p in range(copy.page_count):
            if copy.xref_get_key(copy[p].xref, "Thumb")[0] != "null":
                copy.xref_set_key(copy[p].xref, "Thumb", "null")
        tmp = path + ".paperleaf-saving"
        copy.save(tmp, garbage=4, deflate=True, deflate_images=True, deflate_fonts=True, clean=True,
                  use_objstms=1, encryption=pymupdf.PDF_ENCRYPT_KEEP)
    finally:
        copy.close()
    after = os.path.getsize(tmp)
    os.replace(tmp, path)
    return before, after


# ================================================================= PDF to other
def word_available() -> bool:
    try:
        import importlib.util
        return importlib.util.find_spec("pdf2docx") is not None
    except Exception:
        return False


def to_word(doc: PdfDocument, path: str, pages=None, progress=None) -> str:
    """Convert to a Word document (.docx) with pdf2docx."""
    try:
        from pdf2docx import Converter
    except ImportError as exc:
        raise RuntimeError("Exporting to Word needs the pdf2docx package, which is missing from this "
                           "installation.") from exc
    import logging
    logging.getLogger("pdf2docx").setLevel(logging.WARNING)
    logging.getLogger().setLevel(max(logging.getLogger().level, logging.WARNING))
    tmpdir = tempfile.mkdtemp(prefix="paperleaf-")
    try:
        src = os.path.join(tmpdir, "source.pdf")
        copy = P.fresh_copy(doc)
        try:
            copy.save(src, encryption=pymupdf.PDF_ENCRYPT_NONE)  # pdf2docx can't open protected files
        finally:
            copy.close()
        cv = Converter(src)
        try:
            kw = {"pages": sorted(set(pages))} if pages else {}
            cv.convert(path + ".paperleaf-saving", multi_processing=False, **kw)
        finally:
            cv.close()
        os.replace(path + ".paperleaf-saving", path)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
    return path


IMAGE_FORMATS = [("PNG", "png"), ("JPEG", "jpg"), ("TIFF", "tif")]


def to_images(doc: PdfDocument, folder: str, base: str, fmt: str = "png", dpi: int = 150, pages=None,
              quality: int = 90, progress=None) -> list[str]:
    """One image per page. ``fmt``: png, jpg or tif. Returns the files written."""
    pages = list(range(doc.page_count)) if pages is None else sorted(set(pages))
    out = []
    width = len(str(doc.page_count))
    for i, p in enumerate(pages):
        if progress and progress(i, len(pages)) is False:
            break
        pix = doc.render(p, pymupdf.Matrix(dpi / 72, dpi / 72))
        pix.set_dpi(dpi, dpi)
        target = os.path.join(folder, f"{base}_{p + 1:0{width}d}.{fmt}")
        if fmt == "png":
            pix.save(target)
        elif fmt in ("jpg", "jpeg"):
            pix.save(target, jpg_quality=int(quality))
        elif fmt in ("tif", "tiff"):
            from PySide6.QtGui import QImage
            from paperleaf.viewer.view import pix_to_qimage
            img: QImage = pix_to_qimage(pix)
            img.setDotsPerMeterX(int(dpi / 0.0254))
            img.setDotsPerMeterY(int(dpi / 0.0254))
            if not img.save(target, "TIFF"):
                raise OSError(f"Could not write {target}")
        else:
            raise ValueError(f"unknown image format {fmt!r}")
        out.append(target)
    return out


def to_text(doc: PdfDocument, path: str, pages=None) -> str:
    pages = range(doc.page_count) if pages is None else sorted(set(pages))
    parts = []
    for p in pages:
        parts.append(doc.text_doc.load_page(p).get_text("text", sort=True) if doc.text_doc is not doc.doc
                     else doc.page_text(p).text)
    with open(path, "w", encoding="utf-8", newline="\r\n" if os.name == "nt" else "\n") as f:
        f.write("\n\f".join(parts))
    return path


def to_html(doc: PdfDocument, path: str, pages=None) -> str:
    pages = range(doc.page_count) if pages is None else sorted(set(pages))
    title = (doc.metadata().get("title") or os.path.splitext(doc.name)[0])
    body = []
    for p in pages:
        page = doc.text_doc.load_page(p)
        body.append(f'<section class="page" id="page{p + 1}">\n{page.get_text("xhtml")}\n</section>')
    out = ("<!DOCTYPE html>\n<html><head><meta charset=\"utf-8\">"
           f"<title>{html.escape(title)}</title>"
           "<style>body{font-family:Segoe UI,Arial,sans-serif;max-width:52em;margin:2em auto;line-height:1.4}"
           ".page{border-bottom:1px solid #ccc;padding-bottom:1.5em;margin-bottom:1.5em}"
           "img{max-width:100%}</style></head><body>\n" + "\n".join(body) + "\n</body></html>\n")
    with open(path, "w", encoding="utf-8") as f:
        f.write(out)
    return path


def image_data_uri(png: bytes) -> str:
    return "data:image/png;base64," + base64.b64encode(png).decode("ascii")


# ================================================================= to PDF
OFFICE_EXTS = {
    ".doc": "word", ".docx": "word", ".docm": "word", ".rtf": "word", ".odt": "word", ".txt": "word",
    ".xls": "excel", ".xlsx": "excel", ".xlsm": "excel", ".ods": "excel", ".csv": "excel",
    ".ppt": "powerpoint", ".pptx": "powerpoint", ".pptm": "powerpoint", ".odp": "powerpoint",
}
OFFICE_FILTER = ("Office files (*.doc *.docx *.rtf *.odt *.txt *.xls *.xlsx *.ods *.csv *.ppt *.pptx *.odp);;"
                 "All files (*.*)")
_PROGIDS = {"word": "Word.Application", "excel": "Excel.Application", "powerpoint": "PowerPoint.Application"}


def office_kind(path: str) -> str | None:
    return OFFICE_EXTS.get(os.path.splitext(path)[1].lower())


def office_available(kind: str) -> bool:
    """Is that Microsoft Office application installed? (Windows only)"""
    try:
        import winreg
    except ImportError:
        return False
    try:
        with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, _PROGIDS[kind] + r"\CLSID"):
            return True
    except OSError:
        return False


def office_to_pdf(path: str, out: str) -> str:
    """Convert a Word, Excel or PowerPoint file to PDF with the installed
    Microsoft Office (it runs hidden and closes again)."""
    kind = office_kind(path)
    if kind is None:
        raise ValueError("This is not a Word, Excel or PowerPoint file.")
    if not office_available(kind):
        raise RuntimeError(f"Converting this file needs Microsoft {kind.capitalize() if kind != 'powerpoint' else 'PowerPoint'} "
                           "installed on this computer.")
    import pythoncom
    import win32com.client
    src, out = os.path.abspath(path), os.path.abspath(out)
    pythoncom.CoInitialize()
    app = None
    try:
        app = win32com.client.DispatchEx(_PROGIDS[kind])
        if kind == "word":
            app.Visible = False
            app.DisplayAlerts = 0
            d = app.Documents.Open(src, ConfirmConversions=False, ReadOnly=True, AddToRecentFiles=False,
                                   Visible=False)
            try:
                d.ExportAsFixedFormat(out, 17)  # wdExportFormatPDF
            finally:
                d.Close(0)
        elif kind == "excel":
            app.Visible = False
            app.DisplayAlerts = False
            wb = app.Workbooks.Open(src, UpdateLinks=0, ReadOnly=True, AddToMru=False)
            try:
                wb.ExportAsFixedFormat(0, out)  # xlTypePDF
            finally:
                wb.Close(False)
        else:
            pres = app.Presentations.Open(src, ReadOnly=True, Untitled=False, WithWindow=False)
            try:
                pres.SaveAs(out, 32)  # ppSaveAsPDF
            finally:
                pres.Close()
    finally:
        if app is not None:
            try:
                app.Quit()
            except Exception:
                pass
        pythoncom.CoUninitialize()
    if not os.path.exists(out):
        raise RuntimeError("Office did not create the PDF.")
    return out


def images_to_pdf(paths: list[str], out: str) -> int:
    return P.combine(list(paths), out, bookmarks=False)
