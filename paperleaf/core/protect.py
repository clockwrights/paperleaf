"""Protect: password security, redaction and removing hidden information. No Qt here.

* Security changes are applied when the document is saved
  (``PdfDocument.set_security``), like Acrobat does.
* Redaction marks are standard Redact annotations. Applying them really
  removes the text, image parts and drawings underneath (one undoable step
  until the document is closed).
* Removing hidden information is one undoable step too.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

import pymupdf

from paperleaf.core import annotations as an
from paperleaf.core.document import PdfDocument

mupdf = pymupdf.mupdf

# ======================================================================= security
METHODS = [("AES 256-bit (Acrobat X and later)", pymupdf.PDF_ENCRYPT_AES_256),
           ("AES 128-bit (Acrobat 7 and later)", pymupdf.PDF_ENCRYPT_AES_128)]

PRINT_NONE, PRINT_LOW, PRINT_HIGH = "none", "low", "high"
PRINTING = [("Not allowed", PRINT_NONE), ("Low resolution (150 dpi)", PRINT_LOW), ("High resolution", PRINT_HIGH)]
CHANGES = [  # Acrobat's "Changes allowed" choices
    ("None", "none"),
    ("Inserting, deleting and rotating pages", "pages"),
    ("Filling in form fields and signing", "forms"),
    ("Commenting, filling in form fields and signing", "comments"),
    ("Any except extracting pages", "any"),
]
_CHANGE_BITS = {
    "none": 0,
    "pages": pymupdf.PDF_PERM_ASSEMBLE,
    "forms": pymupdf.PDF_PERM_FORM,
    "comments": pymupdf.PDF_PERM_FORM | pymupdf.PDF_PERM_ANNOTATE,
    "any": pymupdf.PDF_PERM_FORM | pymupdf.PDF_PERM_ANNOTATE | pymupdf.PDF_PERM_ASSEMBLE | pymupdf.PDF_PERM_MODIFY,
}


def permissions_value(printing: str = PRINT_HIGH, changes: str = "any", copy: bool = True,
                      accessibility: bool = True) -> int:
    bits = pymupdf.PDF_PERM_ACCESSIBILITY if accessibility or copy else 0
    if printing in (PRINT_LOW, PRINT_HIGH):
        bits |= pymupdf.PDF_PERM_PRINT
    if printing == PRINT_HIGH:
        bits |= pymupdf.PDF_PERM_PRINT_HQ
    bits |= _CHANGE_BITS[changes]
    if copy:
        bits |= pymupdf.PDF_PERM_COPY
    return bits


def describe_permissions(perms: int) -> dict:
    """Current permissions as the dialog's choices."""
    if perms & pymupdf.PDF_PERM_PRINT_HQ and perms & pymupdf.PDF_PERM_PRINT:
        printing = PRINT_HIGH
    elif perms & pymupdf.PDF_PERM_PRINT:
        printing = PRINT_LOW
    else:
        printing = PRINT_NONE
    changes = "none"
    for key in ("any", "comments", "forms", "pages"):
        bits = _CHANGE_BITS[key]
        if bits and perms & bits == bits:
            changes = key
            break
    return {"printing": printing, "changes": changes, "copy": bool(perms & pymupdf.PDF_PERM_COPY),
            "accessibility": bool(perms & pymupdf.PDF_PERM_ACCESSIBILITY)}


def password_problem(open_pw: str, owner_pw: str, restrict: bool) -> str:
    if not open_pw and not (restrict and owner_pw):
        return "Enter a password to open the document, or set permissions with a permissions password."
    if restrict and not owner_pw:
        return "Enter a permissions password, or turn off the restrictions."
    if open_pw and owner_pw and open_pw == owner_pw:
        return "The permissions password must be different from the open password."
    return ""


def protect(doc: PdfDocument, open_pw: str = "", owner_pw: str = "", permissions: int | None = None,
            method: int = pymupdf.PDF_ENCRYPT_AES_256):
    """Encrypt when the document is next saved."""
    restrict = permissions is not None
    problem = password_problem(open_pw, owner_pw, restrict)
    if problem:
        raise ValueError(problem)
    if not owner_pw:
        owner_pw = open_pw  # without restrictions the owner password is the open password
    doc.set_security(method, open_pw, owner_pw, -1 if permissions is None else permissions)


def remove_security(doc: PdfDocument):
    doc.set_security(pymupdf.PDF_ENCRYPT_NONE)


# ======================================================================= redaction
@dataclass
class RedactStyle:
    fill: tuple = (0.0, 0.0, 0.0)
    text: str = ""              # optional overlay text, e.g. "Redacted"
    text_color: tuple = (1.0, 1.0, 1.0)
    fontsize: float = 10.0


PATTERNS = {  # name -> regex (searched in the page text)
    "Email addresses": r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+",
    "Phone numbers": r"(?<![\w])(?:\+?\d{1,3}[\s.-]?)?(?:\(\d{2,4}\)[\s.-]?)?\d{2,4}[\s.-]\d{2,4}[\s.-]?\d{2,5}(?![\w])",
    "Credit card numbers": r"(?<!\d)(?:\d[ -]?){13,19}(?!\d)",
    "US Social Security numbers": r"(?<!\d)\d{3}-\d{2}-\d{4}(?!\d)",
    "Dates": r"(?<!\d)(?:\d{1,2}[./-]\d{1,2}[./-]\d{2,4}|\d{4}-\d{2}-\d{2})(?!\d)",
    "Web addresses": r"(?:https?://|www\.)[^\s]+",
}


def _luhn(digits: str) -> bool:
    s, alt = 0, False
    for ch in reversed(digits):
        d = int(ch)
        if alt:
            d = d * 2 - 9 if d > 4 else d * 2
        s += d
        alt = not alt
    return s % 10 == 0


def search_regex(text: str, whole_word: bool = False, match_case: bool = False):
    from paperleaf.core.document import make_search_regex
    return make_search_regex(text, match_case, whole_word)


def find_matches(doc: PdfDocument, regexes, pages=None) -> list[tuple[int, int, int, str]]:
    """[(page, start, end, matched text)] for ``regexes`` (compiled patterns)."""
    out = []
    for pno in (range(doc.page_count) if pages is None else pages):
        text = doc.page_text(pno).text
        for rx in regexes:
            for m in rx.finditer(text):
                if m.end() <= m.start():
                    continue
                found = m.group(0)
                if rx.pattern == PATTERNS["Credit card numbers"]:
                    digits = re.sub(r"\D", "", found)
                    if not (13 <= len(digits) <= 19 and _luhn(digits)):
                        continue
                out.append((pno, m.start(), m.end(), found))
    out.sort()
    return out


def _add_redact(page: pymupdf.Page, area, style: RedactStyle, author: str):
    kw = dict(fill=style.fill, cross_out=True)
    if style.text:
        kw.update(text=style.text, text_color=style.text_color, fontsize=style.fontsize, align=pymupdf.TEXT_ALIGN_CENTER)
    a = page.add_redact_annot(area, **kw)
    an._info(a, author, subject="Redaction")
    a.update()
    return a.xref


def mark_rects(doc: PdfDocument, pno: int, rects, style: RedactStyle | None = None, author: str = "") -> list[int]:
    """Mark areas (unrotated page coordinates) for redaction."""
    style = style or RedactStyle()
    with doc.edit("Mark for redaction", pno):
        page = doc.page(pno)
        return [_add_redact(page, pymupdf.Rect(r), style, author) for r in rects]


def mark_ranges(doc: PdfDocument, ranges, style: RedactStyle | None = None, author: str = "",
                label: str = "Mark for redaction") -> int:
    """Mark text ranges [(page, start, end), ...] for redaction. Returns the number of marks."""
    style = style or RedactStyle()
    by_page: dict[int, list] = {}
    for pno, s, e, *_rest in ranges:
        rects = doc.page_text(pno).rects_for(s, e)
        if rects:
            by_page.setdefault(pno, []).append(rects)
    if not by_page:
        return 0
    n = 0
    with doc.edit(label, by_page.keys()):
        for pno, groups in by_page.items():
            page = doc.page(pno)
            for rects in groups:
                for r in rects:
                    _add_redact(page, pymupdf.Rect(r), style, author)
                    n += 1
    return n


def marks(doc: PdfDocument) -> list[an.AnnotRecord]:
    out = []
    for pno in range(doc.page_count):
        out += [r for r in an.page_annots(doc, pno) if r.kind == "Redact"]
    return out


def apply(doc: PdfDocument, pages=None) -> int:
    """Apply all redaction marks: the content underneath is removed. Returns the number applied."""
    todo = {}
    for r in marks(doc):
        if pages is None or r.pno in pages:
            todo[r.pno] = todo.get(r.pno, 0) + 1
    if not todo:
        return 0
    doc.full_save = True  # the removed content must not stay in an earlier revision
    with doc.edit("Apply redactions", todo.keys(), content=True):
        for pno in todo:
            doc.doc[pno].apply_redactions(images=pymupdf.PDF_REDACT_IMAGE_PIXELS,
                                          graphics=pymupdf.PDF_REDACT_LINE_ART_REMOVE_IF_TOUCHED,
                                          text=pymupdf.PDF_REDACT_TEXT_REMOVE)
    return sum(todo.values())


# ===================================================================== sanitizing
SANITIZE_ITEMS = [  # (key, label, checked by default)
    ("metadata", "Metadata (title, author, keywords, editing history)", True),
    ("attachments", "File attachments", True),
    ("comments", "Comments and markups", True),
    ("forms", "Form fields (their values become part of the page)", True),
    ("javascript", "JavaScript and actions", True),
    ("links", "Links", False),
    ("bookmarks", "Bookmarks", False),
    ("layers", "Hidden layers", True),
    ("hidden_text", "Hidden text (this also removes the searchable text of scanned pages)", False),
    ("thumbnails", "Embedded page thumbnails", True),
]


@dataclass
class Findings:
    counts: dict = field(default_factory=dict)

    def total(self) -> int:
        return sum(self.counts.values())


def _catalog(doc):
    return doc.doc.pdf_catalog()


def _hidden_layers(doc: PdfDocument) -> list[int]:
    """xrefs of optional content groups that are off by default."""
    out = []
    try:
        for xref, info in doc.doc.get_ocgs().items():
            if not info.get("on", True):
                out.append(xref)
    except Exception:
        pass
    return out


def _invisible_text_rects(doc: PdfDocument, pno: int) -> list[pymupdf.Rect]:
    """Text drawn invisibly (render mode 3, like the text layer of scanned pages)."""
    out = []
    try:
        for span in doc.text_doc.load_page(pno).get_texttrace():
            if span.get("type") == 3 or span.get("opacity", 1) == 0:
                out.append(pymupdf.Rect(span["bbox"]))
    except Exception:
        pass
    return out


def inspect(doc: PdfDocument) -> Findings:
    """What hidden information the document has (for the dialog)."""
    f = Findings()
    md = {k: v for k, v in (doc.doc.metadata or {}).items() if v and k not in ("format", "encryption")}
    xml = doc.doc.xref_get_key(_catalog(doc), "Metadata")[0] != "null"
    f.counts["metadata"] = len(md) + (1 if xml else 0)
    f.counts["attachments"] = doc.doc.embfile_count() + sum(
        1 for p in range(doc.page_count) for r in an.page_annots(doc, p) if r.kind == "FileAttachment")
    f.counts["comments"] = sum(1 for p in range(doc.page_count) for r in an.page_annots(doc, p))
    from paperleaf.core import forms as F
    f.counts["forms"] = len(F.all_fields(doc))
    f.counts["javascript"] = len(_scripts(doc))
    f.counts["links"] = sum(len(doc.links(p)) for p in range(doc.page_count))
    f.counts["bookmarks"] = len(doc.toc())
    f.counts["layers"] = len(_hidden_layers(doc))
    f.counts["hidden_text"] = sum(len(_invisible_text_rects(doc, p)) for p in range(min(doc.page_count, 500)))
    f.counts["thumbnails"] = sum(1 for p in range(doc.page_count)
                                 if doc.doc.xref_get_key(doc.doc[p].xref, "Thumb")[0] != "null")
    return f


_SCRIPTY = {"JavaScript", "Launch", "SubmitForm", "ImportData", "Rendition", "GoToE"}


def _scripts(doc: PdfDocument) -> list[tuple[int, str]]:
    """(xref, key) of JavaScript (and other active) actions, and additional-action dicts."""
    out = []
    D = doc.doc
    cat = _catalog(doc)
    if D.xref_get_key(cat, "Names/JavaScript")[0] != "null":
        out.append((cat, "Names/JavaScript"))
    oa = D.xref_get_key(cat, "OpenAction/S")
    if oa[0] == "name" and oa[1].lstrip("/") in _SCRIPTY:
        out.append((cat, "OpenAction"))
    if D.xref_get_key(cat, "AA")[0] != "null":
        out.append((cat, "AA"))
    for p in range(doc.page_count):
        page = D[p]
        if D.xref_get_key(page.xref, "AA")[0] != "null":
            out.append((page.xref, "AA"))
        for x in [a[0] for a in page.annot_xrefs()]:
            s = D.xref_get_key(x, "A/S")
            if s[0] == "name" and s[1].lstrip("/") in _SCRIPTY:
                out.append((x, "A"))
            if D.xref_get_key(x, "AA")[0] != "null":
                out.append((x, "AA"))
            px = D.xref_get_key(x, "Parent")
            while px[0] == "xref":
                pn = int(px[1].split()[0])
                if D.xref_get_key(pn, "AA")[0] != "null" and (pn, "AA") not in out:
                    out.append((pn, "AA"))
                px = D.xref_get_key(pn, "Parent")
    if D.xref_get_key(cat, "AcroForm/CO")[0] != "null":
        out.append((cat, "AcroForm/CO"))
    return out


def _strip_marked_content(data: bytes, names: set[bytes]) -> bytes:
    """Remove "/OC /name BDC ... EMC" blocks for hidden layer resource names."""
    if not names:
        return data
    out = bytearray()
    i = 0
    tok = re.compile(rb"/OC\s*/(\S+?)\s+BDC|\bBDC\b|\bBMC\b|\bEMC\b")
    while True:
        m = tok.search(data, i)
        if not m:
            out += data[i:]
            return bytes(out)
        if m.group(1) is not None and m.group(1) in names:
            out += data[i:m.start()]
            depth, j = 1, m.end()
            while depth and j < len(data):
                n = tok.search(data, j)
                if not n:
                    j = len(data)
                    break
                depth += -1 if n.group(0) == b"EMC" else 1
                j = n.end()
            i = j
        else:
            out += data[i:m.end()]
            i = m.end()


def _remove_hidden_layers(doc: PdfDocument):
    hidden = set(_hidden_layers(doc))
    if not hidden:
        return
    D = doc.doc
    for p in range(doc.page_count):
        page = D[p]
        names = set()
        props = D.xref_get_key(page.xref, "Resources/Properties")
        text = props[1] if props[0] == "dict" else (D.xref_object(int(props[1].split()[0]), compressed=True)
                                                    if props[0] == "xref" else "")
        for name, num in re.findall(r"/(\S+?)\s+(\d+)\s+0\s+R", text):
            if int(num) in hidden:
                names.add(name.encode())
        if names:
            for x in page.get_contents():
                data = D.xref_stream(x) or b""
                new = _strip_marked_content(data, names)
                if new != data:
                    D.update_stream(x, new)
        # images and forms that belong to a hidden layer
        for img in page.get_images(full=True):
            xref = img[0]
            oc = D.xref_get_key(xref, "OC")
            if oc[0] == "xref" and int(oc[1].split()[0]) in hidden:
                D.update_stream(xref, b"")
    D.xref_set_key(_catalog(doc), "OCProperties", "null")


def sanitize(doc: PdfDocument, items) -> dict:
    """Remove the chosen kinds of hidden information. Returns what was removed (counts)."""
    items = set(items)
    found = inspect(doc).counts
    removed = {k: found.get(k, 0) for k in items if found.get(k, 0)}
    if not removed:
        return {}
    doc.full_save = True
    D = doc.doc
    with doc.edit("Remove hidden information", None, structure=True):
        if "metadata" in items:
            D.set_metadata({})
            D.del_xml_metadata()
            for key in ("PieceInfo", "SpiderInfo"):
                D.xref_set_key(_catalog(doc), key, "null")
        if "hidden_text" in items:
            for p in range(doc.page_count):
                rects = _invisible_text_rects(doc, p)
                if rects:
                    page = D[p]
                    for r in rects:
                        page.add_redact_annot(r)
                    page.apply_redactions(images=pymupdf.PDF_REDACT_IMAGE_NONE,
                                          graphics=pymupdf.PDF_REDACT_LINE_ART_NONE,
                                          text=pymupdf.PDF_REDACT_TEXT_REMOVE)
        if "javascript" in items:
            for xref, key in _scripts(doc):
                D.xref_set_key(xref, key, "null")
        if "forms" in items and found.get("forms"):
            D.bake(annots=False, widgets=True)
        if "layers" in items:
            _remove_hidden_layers(doc)
        for p in range(doc.page_count):
            page = D[p]
            if "thumbnails" in items:
                D.xref_set_key(page.xref, "Thumb", "null")
            if "links" in items:
                for link in page.get_links():
                    page.delete_link(link)
            if "comments" in items or "attachments" in items:
                for a in list(page.annots()):
                    kind = a.type[1]
                    if "comments" in items or kind == "FileAttachment":
                        page.delete_annot(a)
        if "attachments" in items:
            for name in D.embfile_names():
                D.embfile_del(name)
            D.xref_set_key(_catalog(doc), "Collection", "null")
        if "bookmarks" in items and found.get("bookmarks"):
            D.xref_set_key(_catalog(doc), "Outlines", "null")
            if D.xref_get_key(_catalog(doc), "PageMode")[1] == "/UseOutlines":
                D.xref_set_key(_catalog(doc), "PageMode", "null")
    return removed
