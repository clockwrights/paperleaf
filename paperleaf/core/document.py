"""Document model: a thin, cached wrapper around a PyMuPDF document.

Coordinate spaces used across the app:
  * "page space"  - PyMuPDF's page.rect space (rotation from /Rotate applied,
                     origin top-left, units = points). Links use this space.
  * "text space"  - unrotated page coordinates. Text extraction and search
                     results use this space; convert with page.rotation_matrix.
"""
from __future__ import annotations

import datetime as _dt
import os
import re
from collections import OrderedDict
from contextlib import contextmanager
from dataclasses import dataclass, field

import pymupdf

# File types MuPDF can open besides PDF.
OPENABLE_EXTENSIONS = {
    ".pdf": "pdf", ".xps": "xps", ".oxps": "xps", ".epub": "epub",
    ".cbz": "cbz", ".fb2": "fb2", ".mobi": "mobi",
    ".png": "png", ".jpg": "jpg", ".jpeg": "jpg", ".tif": "tiff", ".tiff": "tiff",
    ".bmp": "bmp", ".gif": "gif",
}
OPEN_FILTER = (
    "PDF files (*.pdf);;"
    "All supported (*.pdf *.xps *.oxps *.epub *.cbz *.fb2 *.png *.jpg *.jpeg *.tif *.tiff *.bmp);;"
    "Office files, converted to PDF (*.doc *.docx *.rtf *.odt *.xls *.xlsx *.ods *.ppt *.pptx *.odp);;"
    "All files (*.*)"
)

TEXT_FLAGS = (
    pymupdf.TEXT_PRESERVE_WHITESPACE
    | pymupdf.TEXT_MEDIABOX_CLIP
)


class PasswordRequired(Exception):
    """Raised when a document needs a password (or the given one was wrong)."""

    def __init__(self, wrong: bool = False):
        super().__init__("password required")
        self.wrong = wrong


class _LRU(OrderedDict):
    def __init__(self, limit: int):
        super().__init__()
        self.limit = limit

    def get_or(self, key, factory):
        if key in self:
            self.move_to_end(key)
            return self[key]
        value = factory()
        self[key] = value
        if len(self) > self.limit:
            self.popitem(last=False)
        return value


@dataclass
class PageText:
    """Characters of one page in reading order, with a parallel text string.

    ``text[i]`` corresponds to ``boxes[i]`` (text-space rect tuple) and
    ``line_of[i]`` (index into ``lines``). Line breaks are inserted as "\n"
    characters whose box is ``None``.
    """

    text: str = ""
    boxes: list = field(default_factory=list)
    line_of: list = field(default_factory=list)
    lines: list = field(default_factory=list)  # (x0, y0, x1, y1) per line

    @property
    def empty(self) -> bool:
        return not self.lines

    # -- hit testing -------------------------------------------------------
    def hit(self, x: float, y: float, slack: float = 3.0) -> bool:
        """True if point is on (or very near) a text line."""
        for (x0, y0, x1, y1) in self.lines:
            if x0 - slack <= x <= x1 + slack and y0 - slack <= y <= y1 + slack:
                return True
        return False

    def index_at(self, x: float, y: float) -> int:
        """Caret index (0..len(text)) closest to point."""
        if not self.lines:
            return 0
        best_line, best_d = 0, None
        for li, (x0, y0, x1, y1) in enumerate(self.lines):
            dy = 0.0 if y0 <= y <= y1 else min(abs(y - y0), abs(y - y1))
            dx = 0.0 if x0 <= x <= x1 else min(abs(x - x0), abs(x - x1))
            d = dy * 4 + dx  # vertical distance dominates
            if best_d is None or d < best_d:
                best_line, best_d = li, d
        # characters on that line
        first = last = None
        for i, li in enumerate(self.line_of):
            if li == best_line:
                if first is None:
                    first = i
                last = i
        if first is None:
            return 0
        for i in range(first, last + 1):
            b = self.boxes[i]
            if b is None:
                continue
            if x < (b[0] + b[2]) / 2:
                return i
        return last + 1

    def word_at(self, idx: int) -> tuple[int, int]:
        n = len(self.text)
        if n == 0:
            return 0, 0
        idx = max(0, min(idx, n - 1))
        if not self.text[idx].isalnum() and idx > 0 and self.text[idx - 1].isalnum():
            idx -= 1
        if not self.text[idx].isalnum():
            return idx, idx + 1
        a = idx
        while a > 0 and (self.text[a - 1].isalnum() or self.text[a - 1] in "_'-"):
            a -= 1
        b = idx
        while b < n and (self.text[b].isalnum() or self.text[b] in "_'-"):
            b += 1
        return a, b

    def line_range(self, idx: int) -> tuple[int, int]:
        n = len(self.text)
        if n == 0:
            return 0, 0
        idx = max(0, min(idx, n - 1))
        li = self.line_of[idx]
        if li < 0:
            return idx, idx
        a = idx
        while a > 0 and self.line_of[a - 1] == li:
            a -= 1
        b = idx
        while b < n and self.line_of[b] == li:
            b += 1
        return a, b

    def rects_for(self, start: int, end: int) -> list[tuple]:
        """Highlight rectangles (text space), one per line, for text[start:end]."""
        out = []
        cur_line, acc = None, None
        for i in range(max(0, start), min(end, len(self.text))):
            b = self.boxes[i]
            li = self.line_of[i]
            if b is None or li < 0:
                continue
            if li != cur_line:
                if acc:
                    out.append(tuple(acc))
                ly = self.lines[li]
                acc = [b[0], ly[1], b[2], ly[3]]
                cur_line = li
            else:
                acc[0] = min(acc[0], b[0])
                acc[2] = max(acc[2], b[2])
        if acc:
            out.append(tuple(acc))
        return out


def build_page_text(page: pymupdf.Page) -> PageText:
    raw = page.get_text("rawdict", flags=TEXT_FLAGS)
    pt = PageText()
    chars, boxes, line_of = [], [], []
    for block in raw.get("blocks", []):
        if block.get("type", 0) != 0:
            continue
        for line in block.get("lines", []):
            line_chars = []
            for span in line.get("spans", []):
                for ch in span.get("chars", []):
                    c = ch.get("c", "")
                    if not c:
                        continue
                    line_chars.append((c, tuple(ch["bbox"])))
            # strip trailing whitespace-only lines
            if not "".join(c for c, _ in line_chars).strip():
                continue
            li = len(pt.lines)
            x0 = min(b[0] for _, b in line_chars)
            y0 = min(b[1] for _, b in line_chars)
            x1 = max(b[2] for _, b in line_chars)
            y1 = max(b[3] for _, b in line_chars)
            pt.lines.append((x0, y0, x1, y1))
            if chars:
                chars.append("\n")
                boxes.append(None)
                line_of.append(-1)
            for c, b in line_chars:
                chars.append(c)
                boxes.append(b)
                line_of.append(li)
    pt.text = "".join(chars)
    pt.boxes = boxes
    pt.line_of = line_of
    return pt


def make_search_regex(query: str, match_case: bool, whole_word: bool):
    query = query.strip()
    if not query:
        return None
    parts = [re.escape(p) for p in query.split()]
    pattern = r"\s+".join(parts)
    if whole_word:
        pattern = r"(?<!\w)" + pattern + r"(?!\w)"
    flags = 0 if match_case else re.IGNORECASE
    return re.compile(pattern, flags)


def parse_pdf_date(value: str) -> str:
    """Turn 'D:20240131120000+01'00'' into a readable string."""
    if not value:
        return ""
    v = value[2:] if value.startswith("D:") else value
    m = re.match(r"(\d{4})(\d{2})?(\d{2})?(\d{2})?(\d{2})?(\d{2})?", v)
    if not m:
        return value
    parts = [int(p) if p else d for p, d in zip(m.groups(), (1, 1, 1, 0, 0, 0))]
    try:
        dt = _dt.datetime(*parts)
    except ValueError:
        return value
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def human_size(n: int) -> str:
    size = float(n)
    for unit in ("bytes", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "bytes" else f"{size:.2f} {unit}"
        size /= 1024
    return f"{n} bytes"


SAVE_OP = "__save__"  # journal steps made by saving; hidden from undo/redo


@contextmanager
def _pymupdf_in_step(journaled: bool):
    """Let PyMuPDF helpers run inside a journal step.

    Some PyMuPDF functions (show_pdf_page, set_toc, get_new_xref,
    update_object...) check for an open journal step with a test that itself
    fails while a step is open. Inside ``PdfDocument.edit()`` a step is open, so
    the check is skipped there (and only there).
    """
    if not journaled:
        yield
        return
    orig = pymupdf.JM_have_operation
    pymupdf.JM_have_operation = lambda pdf: 1
    try:
        yield
    finally:
        pymupdf.JM_have_operation = orig


class PdfDocument:
    """An open document. Not thread-safe: use from the GUI thread only.

    Edits go through ``edit()``, which records an undoable step in MuPDF's
    journal and notifies ``listeners`` with the set of changed pages
    (``None`` means "any page").
    """

    def __init__(self, path: str, password: str | None = None, data: bytes | None = None):
        self.path = os.path.abspath(path)
        ext = os.path.splitext(self.path)[1].lower()
        self.filetype = OPENABLE_EXTENSIONS.get(ext, "pdf")
        if data is None:
            with open(self.path, "rb") as f:
                data = f.read()  # keep in memory so the file is never locked
        self.data = data
        self.file_size = len(self.data)
        self.file_mtime = os.path.getmtime(self.path) if os.path.exists(self.path) else 0
        try:
            self.doc = pymupdf.open(stream=self.data, filetype=self.filetype)
        except Exception:
            if self.filetype == "pdf":
                raise
            self.doc = pymupdf.open(stream=self.data)
        self.auth = 0  # what the password unlocked: 2 = user (open) password, 4 = owner password
        if self.doc.needs_pass:
            if password is None:
                raise PasswordRequired(False)
            self.auth = self.doc.authenticate(password)
            if not self.auth:
                raise PasswordRequired(True)
        elif password and self.doc.is_encrypted is False and self.doc.is_pdf:
            try:  # no open password, but an owner password unlocks the permissions
                self.auth = self.doc.authenticate(password)
            except Exception:
                self.auth = 0
        self.password = password
        self.new_security: dict | None = None  # applied by the next save (see set_security)
        if self.doc.is_reflowable:
            self.doc.layout(width=500, height=700, fontsize=11)
        self.page_count = 0
        self.page_rects: list[pymupdf.Rect] = []
        self._rot_mats: list[pymupdf.Matrix] = []
        self.structure = 0   # bumped whenever pages are added, removed, moved, rotated or resized
        self._read_geometry()
        self._pages = _LRU(40)
        self._dlists = _LRU(16)
        self._texts = _LRU(400)
        self._links = _LRU(200)
        self._annots = _LRU(200)
        self._widgets = _LRU(200)   # form fields per page (paperleaf.core.forms)
        self._forms: dict = {}      # document-wide form data (paperleaf.core.forms)
        self.full_save = False      # set by redaction / sanitize: never save incrementally
        self._labels = None
        # Text does not change when annotations change, so it is read from a
        # separate, never-edited copy. (MuPDF's journal forbids the temporary
        # page rotation PyMuPDF uses when extracting text from rotated pages.)
        self.text_doc = self.doc
        self.listeners: list = []
        self._ops: list = []      # one (name, pages) entry per journal step
        self._saved_step = 0
        self.journaled = False
        if self.doc.is_pdf:
            try:
                self.text_doc = pymupdf.open(stream=self.data, filetype="pdf")
                if self.text_doc.needs_pass:
                    self.text_doc.authenticate(password or "")
            except Exception:
                self.text_doc = self.doc
            try:
                self.doc.journal_enable()
                self.journaled = True
            except Exception:
                self.journaled = False

    def _read_geometry(self):
        self.page_count = self.doc.page_count
        self.page_rects = []
        self._rot_mats = []
        for i in range(self.page_count):
            p = self.doc.load_page(i)
            self.page_rects.append(pymupdf.Rect(p.rect))
            self._rot_mats.append(pymupdf.Matrix(p.rotation_matrix))

    def _structure_changed(self):
        """Pages were added, removed, moved, rotated or resized: re-read them."""
        self._invalidate(None)
        self._texts.clear()
        self._links.clear()
        self._labels = None
        try:
            self.doc._reset_page_refs()
        except Exception:
            pass
        self._read_geometry()
        try:
            self.doc._outline = self.doc._loadOutline()
        except Exception:
            pass
        self.structure += 1
        self._refresh_text()

    # -- basic access ------------------------------------------------------
    @property
    def name(self) -> str:
        return os.path.basename(self.path)

    @property
    def is_pdf(self) -> bool:
        return self.doc.is_pdf

    def page(self, pno: int) -> pymupdf.Page:
        return self._pages.get_or(pno, lambda: self.doc.load_page(pno))

    def display_list(self, pno: int) -> pymupdf.DisplayList:
        return self._dlists.get_or(pno, lambda: self.page(pno).get_displaylist(annots=True))

    def rotation_matrix(self, pno: int) -> pymupdf.Matrix:
        """Maps text space -> page space."""
        return self._rot_mats[pno]

    def close(self):
        self._pages.clear()
        self._dlists.clear()
        self._annots.clear()
        self._widgets.clear()
        for d in {id(self.doc): self.doc, id(self.text_doc): self.text_doc}.values():
            try:
                d.close()
            except Exception:
                pass

    # -- editing -------------------------------------------------------------
    def can_annotate(self) -> bool:
        return self.doc.is_pdf and bool(self.doc.permissions & pymupdf.PDF_PERM_ANNOTATE)

    def can_fill_forms(self) -> bool:
        perms = self.doc.permissions if self.doc.is_pdf else 0
        return self.doc.is_pdf and bool(perms & (pymupdf.PDF_PERM_FORM | pymupdf.PDF_PERM_ANNOTATE))

    def can_modify(self) -> bool:
        return self.doc.is_pdf and bool(self.doc.permissions & pymupdf.PDF_PERM_MODIFY)

    def _step(self) -> int:
        return self.doc.journal_position()[0] if self.journaled else len(self._ops)

    @contextmanager
    def edit(self, name: str, pages, content: bool = False, structure: bool = False):
        """Group changes into one undoable step. ``pages``: int, iterable or None (all).

        ``content=True`` marks a step that changes page content (text), such as
        flattening a form: the text copy used for search and selection is
        refreshed after it, and after undoing or redoing it.
        ``structure=True`` marks a step that adds, removes, moves, rotates or
        resizes pages; listeners then get ``None`` and ``doc.structure`` changes.
        """
        if structure:
            pages, content = None, True
        elif pages is not None:
            pages = {pages} if isinstance(pages, int) else set(pages)
        if self.journaled:
            self.doc.journal_start_op(name)
        try:
            with _pymupdf_in_step(self.journaled):
                yield
        except Exception:
            if self.journaled:
                self.doc.journal_stop_op()
                try:
                    self.doc.journal_undo()  # roll back the half-done step
                except Exception:
                    pass
            self._invalidate(None)
            if structure:
                self._structure_changed()
            elif content:
                self._refresh_text()
            self._notify(None)
            raise
        if self.journaled:
            self.doc.journal_stop_op()
            del self._ops[self._step() - 1:]
        self._ops.append((name, pages, content, structure))
        if self._saved_step >= len(self._ops) and name != SAVE_OP:
            self._saved_step = -1  # the saved state can no longer be reached
        if structure:
            self._structure_changed()
        elif content:
            self._invalidate(None)
            self._refresh_text()
        else:
            self._invalidate(pages)
        if name != SAVE_OP:
            self._notify(pages)

    def _invalidate(self, pages):
        self._forms.clear()
        if pages is None:
            self._pages.clear()
            self._dlists.clear()
            self._annots.clear()
            self._widgets.clear()
            return
        for p in pages:
            self._dlists.pop(p, None)
            self._annots.pop(p, None)
            self._widgets.pop(p, None)

    def _refresh_text(self):
        """Rebuild the text copy from the current state (after page content changed)."""
        self._texts.clear()
        if self.text_doc is self.doc:
            return
        try:
            fresh = pymupdf.open()
            # text only: copying widgets would edit the source's form (not allowed
            # outside a journal step)
            fresh.insert_pdf(self.doc, links=False, annots=False, widgets=False)
        except Exception:
            return
        old, self.text_doc = self.text_doc, fresh
        try:
            old.close()
        except Exception:
            pass

    def _notify(self, pages):
        for cb in list(self.listeners):
            try:
                cb(pages)
            except Exception:
                import traceback
                traceback.print_exc()

    def _undo_span(self, redo=False):
        """(number of journal steps to move, name of the user-visible op)."""
        if not self.journaled:
            return 0, None
        i = self._step()
        n = 0
        if not redo:
            while i > 0:
                name = self._ops[i - 1][0]
                n += 1
                i -= 1
                if name != SAVE_OP:
                    return n, name
            return 0, None
        while i < len(self._ops):
            name = self._ops[i][0]
            n += 1
            i += 1
            if name != SAVE_OP:
                while i < len(self._ops) and self._ops[i][0] == SAVE_OP:
                    n += 1
                    i += 1
                return n, name
        return 0, None

    def undo_name(self):
        return self._undo_span()[1]

    def redo_name(self):
        return self._undo_span(redo=True)[1]

    def _move(self, redo: bool) -> bool:
        n, _name = self._undo_span(redo)
        if not n:
            return False
        step = self._step()
        idx = range(step, step + n) if redo else range(step - n, step)
        pages = set()
        content = structure = False
        for i in idx:
            content = content or self._ops[i][2]
            structure = structure or self._ops[i][3]
            pg = self._ops[i][1]
            if pg is None:
                pages = None
            elif pages is not None:
                pages |= pg
        for _ in range(n):
            (self.doc.journal_redo if redo else self.doc.journal_undo)()
        self._invalidate(None)  # page objects may be stale after undo/redo
        if structure:
            self._structure_changed()
        elif content:
            self._refresh_text()
        self._notify(pages)
        return True

    def undo(self) -> bool:
        return self._move(False)

    def redo(self) -> bool:
        return self._move(True)

    @property
    def modified(self) -> bool:
        return self._step() != self._saved_step or self.new_security is not None

    def mark_modified(self):
        """Used for recovered documents: treat as unsaved."""
        self._saved_step = -1

    def _write(self, target: str, security: bool = False):
        # garbage=1 leaves out objects nothing refers to any more: removed attachments,
        # scripts and the old content of redacted pages must not stay in the file
        kwargs = dict(garbage=1, deflate=True, encryption=pymupdf.PDF_ENCRYPT_KEEP)
        if security and self.new_security is not None:
            sec = self.new_security
            kwargs.update(encryption=sec["method"], user_pw=sec.get("user_pw") or "",
                          owner_pw=sec.get("owner_pw") or "", permissions=sec.get("permissions", -1))
        incremental = security and self.new_security is None and self.keeps_signatures()
        if self.journaled:
            with self.edit(SAVE_OP, ()):
                self._save_to(target, incremental, kwargs)
        else:
            self._save_to(target, incremental, kwargs)

    def _save_to(self, target, incremental, kwargs):
        if not incremental:
            self.doc.save(target, **kwargs)
            return
        # signed: append the changes to the original bytes, so the signatures stay valid
        opts = pymupdf.mupdf.PdfWriteOptions()
        opts.do_incremental = 1
        opts.do_encrypt = pymupdf.PDF_ENCRYPT_KEEP
        with open(target, "wb") as f:
            out = pymupdf.JM_new_output_fileptr(f)
            pymupdf.mupdf.pdf_write_document(pymupdf._as_pdf_document(self.doc), out, opts)
            out.fz_close_output()

    def keeps_signatures(self) -> bool:
        """Saving appends changes (signatures stay valid) unless something must
        really be removed from the file (redaction, removing hidden information)."""
        return not self.full_save and self.is_signed()

    def save(self, path: str | None = None):
        """Save to ``path`` (default: the document's own file), atomically."""
        if not self.doc.is_pdf:
            raise ValueError("Only PDF documents can be saved with changes.")
        target = os.path.abspath(path or self.path)
        tmp = target + ".paperleaf-saving"
        security = self.new_security
        try:
            self._write(tmp, security=True)
            os.replace(tmp, target)
        finally:
            if os.path.exists(tmp):
                try:
                    os.remove(tmp)
                except OSError:
                    pass
        self.path = target
        self.filetype = "pdf"
        with open(target, "rb") as f:
            self.data = f.read()
        self.file_size = len(self.data)
        self.file_mtime = os.path.getmtime(target)
        self._saved_step = self._step()
        if security is not None:
            # the open document still has the old encryption: reopen the saved file
            password = security.get("owner_pw") or security.get("user_pw") or None
            self._reopen(password)

    def _reopen(self, password):
        listeners, structure = self.listeners, self.structure
        self.close()
        self.__init__(self.path, password)
        self.listeners = listeners
        self.structure = structure + 1
        self._notify(None)

    # -- security --------------------------------------------------------------
    @property
    def encrypted(self) -> bool:
        return bool(self.doc.is_pdf and (self.doc.metadata or {}).get("encryption"))

    def encryption_name(self) -> str:
        return (self.doc.metadata or {}).get("encryption") or ""

    def is_owner(self) -> bool:
        """May the security settings be changed? (no encryption, no restrictions, or the owner password)"""
        if not self.doc.is_pdf:
            return False
        if not self.encrypted:
            return True
        if self.auth & 4:
            return True
        full = (pymupdf.PDF_PERM_PRINT | pymupdf.PDF_PERM_MODIFY | pymupdf.PDF_PERM_COPY
                | pymupdf.PDF_PERM_ANNOTATE | pymupdf.PDF_PERM_FORM | pymupdf.PDF_PERM_ASSEMBLE
                | pymupdf.PDF_PERM_PRINT_HQ)
        return (self.doc.permissions & full) == full

    def check_owner_password(self, password: str) -> bool:
        try:
            d = pymupdf.open(stream=self.data, filetype="pdf")
            ok = bool(d.authenticate(password) & 4)
            d.close()
            return ok
        except Exception:
            return False

    def set_security(self, method: int, user_pw: str = "", owner_pw: str = "", permissions: int = -1):
        """Encrypt (or with PDF_ENCRYPT_NONE, decrypt) when the document is next saved."""
        self.new_security = {"method": method, "user_pw": user_pw, "owner_pw": owner_pw,
                             "permissions": permissions}
        self._notify(set())

    def cancel_security(self):
        self.new_security = None
        self._notify(set())

    def write_backup(self, target: str):
        """Write the current state somewhere without changing the saved state."""
        self._write(target)

    def current_bytes(self) -> bytes:
        """The document as it is now (including unsaved changes)."""
        if not self.modified:
            return self.data
        import tempfile
        fd, tmp = tempfile.mkstemp(suffix=".pdf")
        os.close(fd)
        try:
            self._write(tmp)
            with open(tmp, "rb") as f:
                return f.read()
        finally:
            os.remove(tmp)

    def is_signed(self) -> bool:
        """True if a signature field holds a digital signature (an empty
        signature field also sets the document's SigFlags)."""
        try:
            if not self.doc.is_pdf or self.doc.get_sigflags() <= 0:
                return False
            for pno in range(self.page_count):
                for w in self.page(pno).widgets(types=[pymupdf.PDF_WIDGET_TYPE_SIGNATURE]):
                    if w.is_signed:
                        return True
            return False
        except Exception:
            return False

    # -- permissions ---------------------------------------------------------
    def can_print(self) -> bool:
        return not self.doc.is_pdf or bool(self.doc.permissions & pymupdf.PDF_PERM_PRINT)

    def can_copy(self) -> bool:
        return not self.doc.is_pdf or bool(self.doc.permissions & pymupdf.PDF_PERM_COPY)

    # -- rendering ---------------------------------------------------------
    def render(self, pno: int, matrix: pymupdf.Matrix, clip=None, *, annots=True,
               gray=False, night=False) -> pymupdf.Pixmap:
        if annots and not gray:
            pix = self.display_list(pno).get_pixmap(matrix=matrix, clip=clip, alpha=False)
        else:
            cs = pymupdf.csGRAY if gray else pymupdf.csRGB
            pix = self.page(pno).get_pixmap(matrix=matrix, clip=clip, alpha=False,
                                            annots=annots, colorspace=cs)
        if night and pix.n >= 3:
            pix.invert_irect()
            pix.tint_with(0x2A2A2A, 0xE6E6E6)  # dark gray paper, light gray ink
        return pix

    # -- text --------------------------------------------------------------
    def page_text(self, pno: int) -> PageText:
        return self._texts.get_or(pno, lambda: build_page_text(self.text_doc.load_page(pno)))

    def plain_text(self, pno: int) -> str:
        return self.page_text(pno).text

    def search_page(self, pno: int, regex) -> list[tuple[int, int]]:
        pt = self.page_text(pno)
        return [(m.start(), m.end()) for m in regex.finditer(pt.text) if m.end() > m.start()]

    # -- links -------------------------------------------------------------
    def links(self, pno: int) -> list[dict]:
        def load():
            try:
                return self.page(pno).get_links()
            except Exception:
                return []
        return self._links.get_or(pno, load)

    def link_at(self, pno: int, x: float, y: float):
        for link in self.links(pno):
            r = link.get("from")
            if r is not None and r.contains(pymupdf.Point(x, y)):
                return link
        return None

    # -- outline / labels / metadata -----------------------------------------
    def toc(self) -> list:
        try:
            return self.doc.get_toc(simple=False)
        except Exception:
            return []

    def has_labels(self) -> bool:
        if self._labels is None:
            try:
                self._labels = bool(self.doc.is_pdf and self.doc.get_page_labels())
            except Exception:
                self._labels = False
        return self._labels

    def page_label(self, pno: int) -> str:
        if self.has_labels():
            try:
                label = self.page(pno).get_label()
                if label:
                    return label
            except Exception:
                pass
        return str(pno + 1)

    def find_label(self, text: str) -> int | None:
        text = text.strip()
        if not text:
            return None
        if self.has_labels():
            try:
                hits = self.doc.get_page_numbers(text)
                if hits:
                    return hits[0]
            except Exception:
                pass
        if text.isdigit():
            n = int(text) - 1
            if 0 <= n < self.page_count:
                return n
        return None

    def metadata(self) -> dict:
        return dict(self.doc.metadata or {})

    def attachments(self) -> list[dict]:
        out = []
        if not self.doc.is_pdf:
            return out
        try:
            for name in self.doc.embfile_names():
                info = self.doc.embfile_info(name)
                out.append({"name": name, "filename": info.get("filename") or name,
                            "size": info.get("size", 0), "desc": info.get("desc", "")})
        except Exception:
            pass
        return out

    def attachment_bytes(self, name: str) -> bytes:
        return self.doc.embfile_get(name)

    def fonts(self, max_pages: int = 500) -> list[tuple]:
        seen = {}
        for pno in range(min(self.page_count, max_pages)):
            try:
                for f in self.page(pno).get_fonts(full=False):
                    # (xref, ext, type, basefont, name, encoding)
                    key = (f[3], f[2])
                    if key not in seen:
                        seen[key] = (f[3] or f[4], f[2], f[5], f[1])
            except Exception:
                continue
        return sorted(seen.values(), key=lambda t: t[0].lower())
