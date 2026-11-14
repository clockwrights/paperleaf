"""Page operations (Organize Pages and friends). No Qt here.

Changes to the open document run inside ``PdfDocument.edit(..., structure=True)``
so they are undoable and every view re-reads the pages afterwards. Operations
that make new files (extract, split, combine) work on a fresh copy of the
document, never on the open one.

Page numbers are 0-based here; the UI shows them 1-based.
"""
from __future__ import annotations

import datetime as _dt
import os
import re
from dataclasses import dataclass, field

import pymupdf

from paperleaf.core.document import PdfDocument

mupdf = pymupdf.mupdf

PAPER_SIZES = {  # points, portrait
    "Letter": (612, 792), "Legal": (612, 1008), "Tabloid": (792, 1224),
    "A3": (842, 1191), "A4": (595, 842), "A5": (420, 595), "B5": (499, 709),
}
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".bmp", ".gif", ".tif", ".tiff", ".jxr", ".jpx", ".webp"}
OTHER_DOC_EXTS = {".xps", ".oxps", ".epub", ".cbz", ".fb2", ".mobi"}
COMBINE_FILTER = ("PDFs and images (*.pdf *.png *.jpg *.jpeg *.bmp *.gif *.tif *.tiff *.xps *.oxps *.epub);;"
                  "All files (*.*)")
IMAGE_FILTER = "Images (*.png *.jpg *.jpeg *.bmp *.gif *.tif *.tiff);;All files (*.*)"

MARK_KEY = "PaperleafMark"   # tags the content streams Paperleaf adds, so they can be removed again
WATERMARK, HEADER_FOOTER, BATES, OCR = "Watermark", "HeaderFooter", "Bates", "OCR"
_ARTIFACT = {WATERMARK: "Watermark", HEADER_FOOTER: "Header", BATES: "BatesN"}


# ===================================================================== ranges
def parse_ranges(text: str, page_count: int) -> list[int] | None:
    """'1-3, 5, 8-' -> zero-based page list. None if invalid."""
    pages: list[int] = []
    text = text.replace(" ", "")
    if not text:
        return None
    for part in text.split(","):
        if not part:
            continue
        m = re.fullmatch(r"(\d*)-(\d*)", part)
        if m:
            a = int(m.group(1)) if m.group(1) else 1
            b = int(m.group(2)) if m.group(2) else page_count
        elif part.isdigit():
            a = b = int(part)
        else:
            return None
        if a < 1 or b > page_count or a > b:
            return None
        pages.extend(range(a - 1, b))
    return pages or None


def parse_groups(text: str, page_count: int) -> list[list[int]] | None:
    """'1-3; 4-10; 11-' -> one page list per group (for splitting)."""
    groups = []
    for part in re.split(r"[;\n]", text):
        if not part.strip():
            continue
        g = parse_ranges(part, page_count)
        if g is None:
            return None
        groups.append(g)
    return groups or None


def format_ranges(pages) -> str:
    """[0, 1, 2, 4] -> '1-3, 5'."""
    pages = sorted(set(pages))
    out, i = [], 0
    while i < len(pages):
        j = i
        while j + 1 < len(pages) and pages[j + 1] == pages[j] + 1:
            j += 1
        out.append(f"{pages[i] + 1}" if i == j else f"{pages[i] + 1}-{pages[j] + 1}")
        i = j + 1
    return ", ".join(out)


def select_pages(page_count: int, which: str = "all", rng: list[int] | None = None,
                 current: int = 0) -> list[int]:
    """Pages for the common "All / Current / Range" + "Even / Odd" choices."""
    if which == "current":
        return [current]
    pages = list(rng) if which == "range" and rng else list(range(page_count))
    if which == "even":
        return [p for p in range(page_count) if (p + 1) % 2 == 0]
    if which == "odd":
        return [p for p in range(page_count) if (p + 1) % 2 == 1]
    return pages


def can_organize(doc: PdfDocument) -> bool:
    perms = doc.doc.permissions if doc.is_pdf else 0
    return doc.is_pdf and bool(perms & (pymupdf.PDF_PERM_ASSEMBLE | pymupdf.PDF_PERM_MODIFY))


# ===================================================================== helpers
def _pdf(d: pymupdf.Document):
    return pymupdf._as_pdf_document(d)


def fresh_copy(doc: PdfDocument) -> pymupdf.Document:
    """A separate, editable (not journalled) copy of the document as it is now."""
    copy = pymupdf.open(stream=doc.current_bytes(), filetype="pdf")
    if copy.needs_pass:
        copy.authenticate(doc.password or "")
    return copy


def open_source(path: str, password: str | None = None) -> pymupdf.Document:
    """Open a PDF, an image or another document type as a PDF document."""
    ext = os.path.splitext(path)[1].lower()
    src = pymupdf.open(path)
    if src.needs_pass:
        if password is None or not src.authenticate(password):
            src.close()
            from paperleaf.core.document import PasswordRequired
            raise PasswordRequired(password is not None)
    if src.is_pdf:
        return src
    if ext in IMAGE_EXTS or not src.is_pdf:
        data = src.convert_to_pdf()
        src.close()
        return pymupdf.open(stream=data, filetype="pdf")
    return src


def _subset(src: pymupdf.Document, pages) -> pymupdf.Document:
    """New (not journalled) document with ``pages`` of ``src``, links, comments and fields kept."""
    out = pymupdf.open()
    pages = list(pages)
    if pages == list(range(src.page_count)):
        out.insert_pdf(src)
        return out
    for p in pages:
        out.insert_pdf(src, from_page=p, to_page=p)
    return out


def _save_pdf(d: pymupdf.Document, path: str):
    tmp = path + ".paperleaf-saving"
    try:
        d.save(tmp, garbage=3, deflate=True)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass


def _ref_num(obj) -> int:
    return mupdf.pdf_to_num(obj) if obj.m_internal and mupdf.pdf_is_indirect(obj) else 0


# ===================================================================== page order
def rotate(doc: PdfDocument, pages, delta: int):
    pages = sorted(set(pages))
    if not pages or delta % 360 == 0:
        return
    label = {90: "Rotate pages clockwise", 270: "Rotate pages counterclockwise", 180: "Rotate pages 180°"}
    with doc.edit(label.get(delta % 360, "Rotate pages"), None, structure=True):
        for p in pages:
            page = doc.doc[p]
            page.set_rotation((page.rotation + delta) % 360)


def delete(doc: PdfDocument, pages):
    pages = sorted(set(pages))
    if not pages:
        return
    if len(pages) >= doc.page_count:
        raise ValueError("You can't delete all the pages of a document.")
    with doc.edit("Delete page" if len(pages) == 1 else f"Delete {len(pages)} pages", None, structure=True):
        doc.doc.delete_pages(pages)


def reorder(doc: PdfDocument, order: list[int], label: str = "Move pages"):
    """Put the pages in a new order (a permutation of all page numbers)."""
    order = list(order)
    if sorted(order) != list(range(doc.page_count)):
        raise ValueError("The new order must contain every page once.")
    if order == list(range(doc.page_count)):
        return
    with doc.edit(label, None, structure=True):
        doc.doc.select(order)


def move(doc: PdfDocument, pages, to: int) -> list[int]:
    """Move ``pages`` so they start before page ``to`` (in the current order).
    Returns the new positions of the moved pages."""
    pages = sorted(set(pages))
    rest = [p for p in range(doc.page_count) if p not in pages]
    at = len([p for p in rest if p < to])
    order = rest[:at] + pages + rest[at:]
    reorder(doc, order)
    return list(range(at, at + len(pages)))


def duplicate(doc: PdfDocument, pages) -> list[int]:
    """Copy each page right after itself. Returns the positions of the copies."""
    pages = sorted(set(pages))
    if not pages:
        return []
    out = []
    with doc.edit("Duplicate page" if len(pages) == 1 else "Duplicate pages", None, structure=True):
        for shift, p in enumerate(pages):
            src = p + shift
            target = src + 1
            if target >= doc.doc.page_count:
                doc.doc.fullcopy_page(src, -1)
            else:
                doc.doc.fullcopy_page(src, target)
            out.append(target)
    return out


def insert_blank(doc: PdfDocument, at: int, size: tuple | None = None, count: int = 1) -> list[int]:
    """Insert blank pages before page ``at`` (``at == page_count`` appends)."""
    if size is None:
        ref = doc.page_rects[min(max(at - 1, 0), doc.page_count - 1)] if doc.page_count else pymupdf.Rect(0, 0, 612, 792)
        size = (ref.width, ref.height)
    w, h = size
    pdf = _pdf(doc.doc)
    with doc.edit("Insert blank page" if count == 1 else "Insert blank pages", None, structure=True):
        for i in range(count):
            res = mupdf.pdf_new_dict(pdf, 1)
            page = mupdf.pdf_add_page(pdf, mupdf.FzRect(0, 0, w, h), 0, res, mupdf.fz_new_buffer(0))
            mupdf.pdf_insert_page(pdf, at + i, page)
    return list(range(at, at + count))


def _prepare_source(src: pymupdf.Document):
    """Get a (not journalled) source ready for grafting: annotations lose
    their page and popup links, link targets become page numbers."""
    spdf = _pdf(src)
    page_nums = {_ref_num(mupdf.pdf_lookup_page_obj(spdf, i)): i for i in range(src.page_count)}
    name = mupdf.pdf_new_name
    for i in range(src.page_count):
        pobj = mupdf.pdf_lookup_page_obj(spdf, i)
        annots = mupdf.pdf_dict_get(pobj, name("Annots"))
        if not mupdf.pdf_is_array(annots):
            continue
        for k in range(mupdf.pdf_array_len(annots)):
            a = mupdf.pdf_array_get(annots, k)
            if not mupdf.pdf_is_dict(a):
                continue
            mupdf.pdf_dict_del(a, name("P"))
            mupdf.pdf_dict_del(a, name("Popup"))
            for holder in (a, mupdf.pdf_dict_get(a, name("A"))):
                if not mupdf.pdf_is_dict(holder):
                    continue
                for key in ("Dest", "D"):
                    dest = mupdf.pdf_dict_get(holder, name(key))
                    if mupdf.pdf_is_array(dest) and mupdf.pdf_array_len(dest):
                        target = mupdf.pdf_array_get(dest, 0)
                        num = _ref_num(target)
                        if num in page_nums:
                            mupdf.pdf_array_put(dest, 0, mupdf.pdf_new_int(page_nums[num]))
    return page_nums


def _graft(doc: PdfDocument, src: pymupdf.Document, at: int) -> int:
    """Copy every page of ``src`` into the open document before page ``at``,
    with comments, links and form fields. Must run inside an edit."""
    pdf, spdf = _pdf(doc.doc), _pdf(src)
    name = mupdf.pdf_new_name
    n = src.page_count
    _prepare_source(src)
    gm = mupdf.pdf_new_graft_map(pdf)
    new_pages = []
    widgets = []
    for i in range(n):
        mupdf.pdf_graft_mapped_page(gm, at + i, spdf, i)
        new_pages.append(mupdf.pdf_lookup_page_obj(pdf, at + i))
    for i in range(n):
        sobj = mupdf.pdf_lookup_page_obj(spdf, i)
        annots = mupdf.pdf_dict_get(sobj, name("Annots"))
        if not mupdf.pdf_is_array(annots):
            continue
        arr = mupdf.pdf_new_array(pdf, mupdf.pdf_array_len(annots))
        for k in range(mupdf.pdf_array_len(annots)):
            a = mupdf.pdf_array_get(annots, k)
            if not mupdf.pdf_is_dict(a) or mupdf.pdf_to_name(mupdf.pdf_dict_get(a, name("Subtype"))) == "Popup":
                continue
            copy = mupdf.pdf_graft_mapped_object(gm, a)
            mupdf.pdf_dict_put(copy, name("P"), new_pages[i])
            for holder in (copy, mupdf.pdf_dict_get(copy, name("A"))):
                if not mupdf.pdf_is_dict(holder):
                    continue
                for key in ("Dest", "D"):
                    dest = mupdf.pdf_dict_get(holder, name(key))
                    if mupdf.pdf_is_array(dest) and mupdf.pdf_array_len(dest) and \
                            mupdf.pdf_is_int(mupdf.pdf_array_get(dest, 0)):
                        k2 = mupdf.pdf_to_int(mupdf.pdf_array_get(dest, 0))
                        if 0 <= k2 < n:
                            mupdf.pdf_array_put(dest, 0, new_pages[k2])
            mupdf.pdf_array_push(arr, copy)
            if mupdf.pdf_to_name(mupdf.pdf_dict_get(copy, name("Subtype"))) == "Widget":
                widgets.append(copy)
        mupdf.pdf_dict_put(new_pages[i], name("Annots"), arr)
    if widgets:
        _register_fields(doc, src, gm, widgets)
    return n


def _register_fields(doc: PdfDocument, src: pymupdf.Document, gm, widgets):
    """Add the top-level fields of grafted widgets to the AcroForm."""
    pdf = _pdf(doc.doc)
    name = mupdf.pdf_new_name
    root = mupdf.pdf_dict_get(mupdf.pdf_trailer(pdf), name("Root"))
    form = mupdf.pdf_dict_get(root, name("AcroForm"))
    if not mupdf.pdf_is_dict(form):
        form = mupdf.pdf_new_dict(pdf, 3)
        mupdf.pdf_dict_put(root, name("AcroForm"), mupdf.pdf_add_object(pdf, form))
        form = mupdf.pdf_dict_get(root, name("AcroForm"))
    fields = mupdf.pdf_dict_get(form, name("Fields"))
    if not mupdf.pdf_is_array(fields):
        fields = mupdf.pdf_new_array(pdf, len(widgets))
        mupdf.pdf_dict_put(form, name("Fields"), fields)
    have = {_ref_num(mupdf.pdf_array_get(fields, i)) for i in range(mupdf.pdf_array_len(fields))}
    for w in widgets:
        top, seen = w, set()
        while True:
            parent = mupdf.pdf_dict_get(top, name("Parent"))
            if not mupdf.pdf_is_dict(parent) or _ref_num(parent) in seen:
                break
            seen.add(_ref_num(parent))
            top = parent
        num = _ref_num(top)
        if num and num not in have:
            mupdf.pdf_array_push(fields, top)
            have.add(num)
    sform = mupdf.pdf_dict_get(mupdf.pdf_dict_get(mupdf.pdf_trailer(_pdf(src)), name("Root")), name("AcroForm"))
    if mupdf.pdf_is_dict(sform):
        for key in ("DR", "DA"):
            if not mupdf.pdf_dict_get(form, name(key)).m_internal and mupdf.pdf_dict_get(sform, name(key)).m_internal:
                mupdf.pdf_dict_put(form, name(key), mupdf.pdf_graft_mapped_object(gm, mupdf.pdf_dict_get(sform, name(key))))


def insert_document(doc: PdfDocument, src: pymupdf.Document, at: int, pages=None,
                    label: str = "Insert pages") -> list[int]:
    """Insert pages of another document before page ``at``. ``src`` is not changed."""
    sub = _subset(src, range(src.page_count) if pages is None else pages)
    try:
        with doc.edit(label, None, structure=True):
            n = _graft(doc, sub, at)
    finally:
        sub.close()
    return list(range(at, at + n))


def insert_files(doc: PdfDocument, paths: list[str], at: int, passwords: dict | None = None) -> list[int]:
    """Insert whole PDFs or images (one page per image) before page ``at``, as one step."""
    combined = pymupdf.open()
    try:
        for path in paths:
            src = open_source(path, (passwords or {}).get(path))
            try:
                combined.insert_pdf(src)
            finally:
                src.close()
        label = "Insert pages" if any(p.lower().endswith(".pdf") for p in paths) else "Insert images"
        with doc.edit(label, None, structure=True):
            n = _graft(doc, combined, at)
    finally:
        combined.close()
    return list(range(at, at + n))


def replace_pages(doc: PdfDocument, pages, src: pymupdf.Document, src_pages) -> None:
    """Replace ``pages`` (consecutive) with pages of another document (Acrobat's Replace Pages)."""
    pages = sorted(set(pages))
    sub = _subset(src, src_pages)
    try:
        with doc.edit("Replace pages", None, structure=True):
            at = pages[0]
            _graft(doc, sub, at)
            doc.doc.delete_pages([p + sub.page_count for p in pages])
    finally:
        sub.close()


# ===================================================================== new files
def extract(doc: PdfDocument, pages, path: str, separate: bool = False) -> list[str]:
    """Save ``pages`` as a new PDF (or one PDF per page). Returns the files written."""
    pages = sorted(set(pages))
    src = fresh_copy(doc)
    out = []
    try:
        if separate:
            base, ext = os.path.splitext(path)
            for p in pages:
                sub = _subset(src, [p])
                target = f"{base}_{p + 1}{ext or '.pdf'}"
                _save_pdf(sub, target)
                sub.close()
                out.append(target)
        else:
            sub = _subset(src, pages)
            _save_pdf(sub, path)
            sub.close()
            out.append(path)
    finally:
        src.close()
    return out


def _safe_name(text: str) -> str:
    text = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", text).strip(" .")
    return text[:80] or "Part"


def split_groups(doc: PdfDocument, mode: str, value=None) -> list[tuple[str, list[int]]]:
    """How a split would cut the document: [(name suffix, pages)].

    mode: "pages" (value = pages per file), "size" (value = max MB per file),
    "bookmarks" (top-level bookmarks), "ranges" (value = "1-3; 4-10" text).
    """
    n = doc.page_count
    if mode == "pages":
        step = max(1, int(value or 1))
        return [(f"Part{i // step + 1}", list(range(i, min(n, i + step)))) for i in range(0, n, step)]
    if mode == "ranges":
        groups = parse_groups(str(value or ""), n)
        if groups is None:
            raise ValueError("Enter page ranges like 1-3; 4-10.")
        return [(f"Part{i + 1}", g) for i, g in enumerate(groups)]
    if mode == "bookmarks":
        starts = []
        for entry in doc.toc():
            if entry[0] == 1 and entry[2] >= 1:
                starts.append((entry[2] - 1, entry[1].strip()))
        starts = sorted({s[0]: s for s in starts}.values())
        if not starts:
            raise ValueError("This document has no bookmarks to split by.")
        if starts[0][0] > 0:
            starts.insert(0, (0, "Start"))
        out = []
        for i, (p, title) in enumerate(starts):
            end = starts[i + 1][0] if i + 1 < len(starts) else n
            if end > p:
                out.append((_safe_name(title), list(range(p, end))))
        return out
    if mode == "size":
        limit = max(0.05, float(value or 1)) * 1024 * 1024
        src = fresh_copy(doc)
        try:
            sizes = []
            for p in range(n):
                one = _subset(src, [p])
                sizes.append(len(one.tobytes(garbage=3, deflate=True)))
                one.close()
        finally:
            src.close()
        groups, cur, total = [], [], 0
        for p, s in enumerate(sizes):
            if cur and total + s > limit:
                groups.append(cur)
                cur, total = [], 0
            cur.append(p)
            total += s
        if cur:
            groups.append(cur)
        return [(f"Part{i + 1}", g) for i, g in enumerate(groups)]
    raise ValueError(f"unknown split mode {mode!r}")


def split(doc: PdfDocument, mode: str, value, folder: str, base: str | None = None) -> list[str]:
    """Split into several PDFs in ``folder``. Returns the files written."""
    groups = split_groups(doc, mode, value)
    base = base or os.path.splitext(doc.name)[0]
    src = fresh_copy(doc)
    out, used = [], set()
    try:
        for suffix, pages in groups:
            name = f"{base}_{suffix}"
            k = 2
            while name.lower() in used:
                name = f"{base}_{suffix} ({k})"
                k += 1
            used.add(name.lower())
            target = os.path.join(folder, name + ".pdf")
            sub = _subset(src, pages)
            _save_pdf(sub, target)
            sub.close()
            out.append(target)
    finally:
        src.close()
    return out


def combine(items: list, path: str, bookmarks: bool = True) -> int:
    """Combine files into one PDF. ``items``: paths, or (path, password) pairs.
    Each file gets a bookmark (with its own bookmarks below it). Returns the page count."""
    out = pymupdf.open()
    toc = []
    try:
        for item in items:
            p, pw = (item, None) if isinstance(item, str) else (item[0], item[1])
            src = open_source(p, pw)
            try:
                start = out.page_count
                out.insert_pdf(src)
                if bookmarks:
                    toc.append([1, os.path.splitext(os.path.basename(p))[0], start + 1])
                    try:
                        for e in src.get_toc():
                            if e[2] >= 1:
                                toc.append([e[0] + 1, e[1], e[2] + start])
                    except Exception:
                        pass
            finally:
                src.close()
        if not out.page_count:
            raise ValueError("There is nothing to combine.")
        if bookmarks and toc:
            try:
                out.set_toc(toc)
            except Exception:
                out.set_toc([e for e in toc if e[0] == 1])
        _save_pdf(out, path)
        return out.page_count
    finally:
        out.close()


# ===================================================================== page boxes
def _shown_to_unrotated(page: pymupdf.Page, r: pymupdf.Rect) -> pymupdf.Rect:
    """A rect in displayed page space -> unrotated page space."""
    return (pymupdf.Rect(r) * page.derotation_matrix).normalize()


def crop(doc: PdfDocument, pages, margins: tuple | None = None, rect=None):
    """Crop pages. ``margins`` (left, top, right, bottom, in points, as the page
    is displayed) are taken off each page; or ``rect`` (displayed page space)
    sets the visible area of every page."""
    pages = sorted(set(pages))
    with doc.edit("Crop pages", None, structure=True):
        for p in pages:
            page = doc.doc[p]
            shown = pymupdf.Rect(page.rect)
            if rect is not None:
                r = pymupdf.Rect(rect) & shown
            else:
                left, top, right, bottom = margins
                r = pymupdf.Rect(shown.x0 + left, shown.y0 + top, shown.x1 - right, shown.y1 - bottom)
            if r.is_empty or r.width < 9 or r.height < 9:
                raise ValueError("The margins are too large for these pages.")
            un = _shown_to_unrotated(page, r)
            cb = page.cropbox  # unrotated, top-left origin, in mediabox space
            new = pymupdf.Rect(un.x0 + cb.x0, un.y0 + cb.y0, un.x1 + cb.x0, un.y1 + cb.y0) & page.mediabox
            page.set_cropbox(new)


def white_margins(doc: PdfDocument, pno: int) -> tuple:
    """Margins (displayed left, top, right, bottom) around the visible content of a page."""
    page = doc.page(pno)
    pix = page.get_pixmap(matrix=pymupdf.Matrix(0.5, 0.5), colorspace=pymupdf.csGRAY, annots=False)
    w, h, samples = pix.width, pix.height, pix.samples
    rows = [any(samples[y * pix.stride + x] < 245 for x in range(w)) for y in range(h)]
    cols = [any(samples[y * pix.stride + x] < 245 for y in range(h)) for x in range(w)]
    if not any(rows):
        return (0, 0, 0, 0)
    y0, y1 = rows.index(True), h - 1 - rows[::-1].index(True)
    x0, x1 = cols.index(True), w - 1 - cols[::-1].index(True)
    s = 2.0  # back to points
    rot = page.rotation
    left, top, right, bottom = x0 * s, y0 * s, (w - 1 - x1) * s, (h - 1 - y1) * s
    if rot in (90, 270):  # the pixmap is unrotated; map its sides to the displayed ones
        left, top, right, bottom = (bottom, left, top, right) if rot == 90 else (top, right, bottom, left)
    elif rot == 180:
        left, top, right, bottom = right, bottom, left, top
    pad = 4
    return tuple(max(0.0, v - pad) for v in (left, top, right, bottom))


def resize(doc: PdfDocument, pages, width: float, height: float):
    """Change the page size (as displayed). The content stays centred and is not scaled."""
    pages = sorted(set(pages))
    with doc.edit("Change page size", None, structure=True):
        for p in pages:
            page = doc.doc[p]
            w, h = (height, width) if page.rotation in (90, 270) else (width, height)
            cb = page.cropbox
            cx, cy = (cb.x0 + cb.x1) / 2, (cb.y0 + cb.y1) / 2
            # MediaBox in PDF space (y up); the cropbox centre above is in top-left space
            mb = page.mediabox
            raw = doc.doc.xref_get_key(page.xref, "MediaBox")
            nums = [float(v) for v in re.findall(r"-?\d*\.?\d+", raw[1])] if raw[0] == "array" else \
                [mb.x0, mb.y0, mb.x1, mb.y1]
            px0, py1 = min(nums[0], nums[2]), max(nums[1], nums[3])
            # centre of the visible area in PDF space
            ccx = px0 + cx
            ccy = py1 - cy
            nx0, ny0 = ccx - w / 2, ccy - h / 2
            box = f"[{nx0:.3f} {ny0:.3f} {nx0 + w:.3f} {ny0 + h:.3f}]"
            doc.doc.xref_set_key(page.xref, "MediaBox", box)
            for key in ("CropBox", "TrimBox", "BleedBox", "ArtBox"):
                if doc.doc.xref_get_key(page.xref, key)[0] != "null":
                    doc.doc.xref_set_key(page.xref, key, "null")


# ===================================================================== marks
@dataclass
class TextStyle:
    fontsize: float = 10.0
    color: tuple = (0, 0, 0)
    opacity: float = 1.0


@dataclass
class HeaderFooter:
    """Six text boxes (header and footer, left/centre/right). Tokens:
    <<page>>, <<pages>>, <<date>>, <<file>>, <<title>>, <<bates>>."""
    header: tuple = ("", "", "")
    footer: tuple = ("", "", "")
    style: TextStyle = field(default_factory=TextStyle)
    margins: tuple = (72.0, 36.0, 72.0, 36.0)   # left, top, right, bottom (points)
    start: int = 1                              # number of the first page in the range
    bates_prefix: str = ""
    bates_suffix: str = ""
    bates_digits: int = 6
    date_format: str = "%x"


TOKENS = ("<<page>>", "<<pages>>", "<<date>>", "<<file>>", "<<title>>", "<<bates>>")


def _expand(text: str, spec: HeaderFooter, number: int, total: int, doc: PdfDocument) -> str:
    if "<<" not in text:
        return text
    md = doc.metadata()
    out = text.replace("<<page>>", str(number)).replace("<<pages>>", str(total))
    out = out.replace("<<date>>", _dt.date.today().strftime(spec.date_format))
    out = out.replace("<<file>>", doc.name).replace("<<title>>", md.get("title") or os.path.splitext(doc.name)[0])
    out = out.replace("<<bates>>", f"{spec.bates_prefix}{number:0{max(1, spec.bates_digits)}d}{spec.bates_suffix}")
    return out


_FONT_FILES = [  # used only for text that Helvetica can't show
    # Segoe UI first: Arial (and Tahoma, Verdana, Calibri...) use one glyph for both
    # space and no-break space, so text copied or searched back gets U+00A0 instead of spaces
    r"C:\Windows\Fonts\segoeui.ttf", r"C:\Windows\Fonts\arial.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "/usr/share/fonts/dejavu/DejaVuSans.ttf",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
]


def _font_for(text: str) -> tuple[str, str | None]:
    """(fontname, fontfile) able to show ``text``."""
    if all(ord(ch) < 256 for ch in text):   # the built-in Helvetica covers Latin-1 only
        return "helv", None
    for f in _FONT_FILES:
        if os.path.exists(f):
            return "paperleaf-sans", f
    return "helv", None


def _text_width(text: str, fontname: str, fontfile: str | None, size: float) -> float:
    if fontfile:
        return pymupdf.Font(fontfile=fontfile).text_length(text, fontsize=size)
    return pymupdf.get_text_length(text, fontname=fontname, fontsize=size)


def _mark_new_streams(doc: PdfDocument, page: pymupdf.Page, before: list[int], kind: str):
    """Tag the content streams just added to ``page`` so they can be removed later,
    and mark them as page artifacts (like Acrobat does)."""
    for x in page.get_contents():
        if x in before:
            continue
        data = doc.doc.xref_stream(x) or b""
        art = _ARTIFACT[kind]
        doc.doc.update_stream(x, b"/Artifact <</Type /Pagination /Subtype /" + art.encode() + b">> BDC\n"
                              + data + b"\nEMC\n")
        doc.doc.xref_set_key(x, MARK_KEY, "/" + kind)


def _put_text(page: pymupdf.Page, shown_pt, text: str, style: TextStyle, align: str = "left",
              angle: float = 0.0, overlay: bool = True):
    """Write one line of text at a point given as the page is displayed (baseline)."""
    fontname, fontfile = _font_for(text)
    w = _text_width(text, fontname, fontfile, style.fontsize)
    x, y = shown_pt
    if align == "center":
        x -= w / 2
    elif align == "right":
        x -= w
    start = pymupdf.Point(x, y) * page.derotation_matrix
    kw = dict(fontsize=style.fontsize, color=style.color, rotate=page.rotation, overlay=overlay,
              fill_opacity=style.opacity, stroke_opacity=style.opacity)
    if fontfile:
        kw.update(fontname=fontname, fontfile=fontfile)
    else:
        kw.update(fontname=fontname)
    if angle:
        centre = pymupdf.Point(shown_pt) * page.derotation_matrix
        kw["morph"] = (centre, pymupdf.Matrix(-angle))
    page.insert_text(start, text, **kw)


def add_header_footer(doc: PdfDocument, pages, spec: HeaderFooter, kind: str = HEADER_FOOTER,
                      label: str = "Add header & footer"):
    pages = sorted(set(pages))
    total = doc.page_count
    with doc.edit(label, pages, content=True):
        for i, p in enumerate(pages):
            page = doc.doc[p]
            before = page.get_contents()
            r = page.rect
            left, top, right, bottom = spec.margins
            fs = spec.style.fontsize
            number = spec.start + i
            rows = ((spec.header, top + fs * 0.8), (spec.footer, r.height - bottom))
            wrote = False
            for texts, y in rows:
                for text, align, x in zip(texts, ("left", "center", "right"),
                                          (left, r.width / 2, r.width - right)):
                    if not text:
                        continue
                    line = _expand(text, spec, number, total, doc)
                    if line.strip():
                        _put_text(page, (x, y), line, spec.style, align)
                        wrote = True
            if wrote:
                _mark_new_streams(doc, page, before, kind)


def add_page_numbers(doc: PdfDocument, pages, position: str = "bottom-center", fmt: str = "<<page>>",
                     style: TextStyle | None = None, start: int = 1, margin: float = 30.0):
    """position: top-/bottom- + left/center/right."""
    row, col = position.split("-")
    boxes = ["", "", ""]
    boxes[["left", "center", "right"].index(col)] = fmt
    spec = HeaderFooter(header=tuple(boxes) if row == "top" else ("", "", ""),
                        footer=tuple(boxes) if row == "bottom" else ("", "", ""),
                        style=style or TextStyle(), margins=(margin * 1.5, margin, margin * 1.5, margin), start=start)
    add_header_footer(doc, pages, spec, label="Add page numbers")


def add_bates(doc: PdfDocument, pages, prefix: str = "", suffix: str = "", start: int = 1, digits: int = 6,
              position: str = "bottom-right", style: TextStyle | None = None, margin: float = 30.0) -> int:
    """Bates numbers. Returns the next unused number."""
    row, col = position.split("-")
    boxes = ["", "", ""]
    boxes[["left", "center", "right"].index(col)] = "<<bates>>"
    spec = HeaderFooter(header=tuple(boxes) if row == "top" else ("", "", ""),
                        footer=tuple(boxes) if row == "bottom" else ("", "", ""),
                        style=style or TextStyle(), margins=(margin * 1.5, margin, margin * 1.5, margin),
                        start=start, bates_prefix=prefix, bates_suffix=suffix, bates_digits=digits)
    pages = sorted(set(pages))
    add_header_footer(doc, pages, spec, kind=BATES, label="Add Bates numbers")
    return start + len(pages)


@dataclass
class Watermark:
    text: str = ""
    image: str = ""            # path to an image (used when text is empty)
    style: TextStyle = field(default_factory=lambda: TextStyle(fontsize=60, color=(0.8, 0.1, 0.1), opacity=0.3))
    angle: float = 45.0        # degrees, counterclockwise as displayed
    scale: float = 0.5         # image: fraction of the page width
    behind: bool = False       # under the page content instead of on top


def _faded_png(path: str, opacity: float) -> tuple[bytes, int, int]:
    pix = pymupdf.Pixmap(path)
    if pix.colorspace and pix.colorspace.n not in (1, 3):
        pix = pymupdf.Pixmap(pymupdf.csRGB, pix)
    if not pix.alpha:
        pix = pymupdf.Pixmap(pix, 1)
    if opacity < 1:
        n, stride = pix.n, pix.stride
        data = bytearray(pix.samples)
        for y in range(pix.height):
            row = y * stride
            for x in range(pix.width):
                i = row + x * n + n - 1
                data[i] = int(data[i] * opacity)
        pix = pymupdf.Pixmap(pix.colorspace, pix.width, pix.height, bytes(data), 1)
    return pix.tobytes("png"), pix.width, pix.height


_IMAGE_ROTATE = 1  # insert_image's rotation that keeps an image upright on a rotated page


def add_watermark(doc: PdfDocument, pages, wm: Watermark):
    pages = sorted(set(pages))
    if not wm.text and not wm.image:
        raise ValueError("Enter watermark text or choose an image.")
    img = _faded_png(wm.image, wm.style.opacity) if not wm.text and wm.image else None
    with doc.edit("Add watermark", pages, content=True):
        for p in pages:
            page = doc.doc[p]
            before = page.get_contents()
            r = page.rect
            if img is None:
                lines = wm.text.splitlines() or [wm.text]
                fs = wm.style.fontsize
                for k, line in enumerate(lines):
                    dy = (k - (len(lines) - 1) / 2) * fs * 1.2
                    centre = (r.width / 2, r.height / 2 + dy + fs * 0.35)
                    _put_text(page, centre, line, wm.style, "center", wm.angle, overlay=not wm.behind)
            else:
                png, iw, ih = img
                w = r.width * wm.scale
                h = w * ih / max(1, iw)
                shown = pymupdf.Rect((r.width - w) / 2, (r.height - h) / 2, (r.width + w) / 2, (r.height + h) / 2)
                page.insert_image(_shown_to_unrotated(page, shown), stream=png, overlay=not wm.behind,
                                  rotate=_IMAGE_ROTATE * page.rotation % 360)
            _mark_new_streams(doc, page, before, WATERMARK)


def marked_pages(doc: PdfDocument, kind: str) -> list[int]:
    out = []
    for p in range(doc.page_count):
        for x in doc.doc[p].get_contents():
            if doc.doc.xref_get_key(x, MARK_KEY)[1] == "/" + kind:
                out.append(p)
                break
    return out


def remove_marks(doc: PdfDocument, kind: str) -> int:
    """Remove the watermarks / headers and footers / Bates numbers Paperleaf added."""
    pages = marked_pages(doc, kind)
    if not pages:
        return 0
    label = {WATERMARK: "Remove watermark", HEADER_FOOTER: "Remove header & footer", BATES: "Remove Bates numbers",
             OCR: "Remove recognized text"}
    with doc.edit(label[kind], pages, content=True):
        for p in pages:
            page = doc.doc[p]
            keep = [x for x in page.get_contents() if doc.doc.xref_get_key(x, MARK_KEY)[1] != "/" + kind]
            doc.doc.xref_set_key(page.xref, "Contents", "[" + " ".join(f"{x} 0 R" for x in keep) + "]")
    return len(pages)


# ===================================================================== thumbnails
def thumbnail_png(doc: PdfDocument, pno: int, width: int = 160) -> bytes:
    r = doc.page_rects[pno]
    s = width / max(1.0, r.width)
    return doc.render(pno, pymupdf.Matrix(s, s)).tobytes("png")
