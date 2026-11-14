"""Interactive PDF forms (AcroForm): reading, filling, clearing, flattening,
data import/export (FDF, XFDF, JSON) and Prepare Form editing. No Qt here.

* Coordinates are *unrotated* page coordinates, like annotations.
* Every change runs inside ``PdfDocument.edit()``, so it is one undoable step.
* Values are written straight into the field dictionaries and the appearances
  are rebuilt with MuPDF, so a form keeps its own fonts, scripts and styling.
* PDF JavaScript is never run (PyMuPDF does not enable MuPDF's JS engine, and
  no trigger events are fired).
"""
from __future__ import annotations

import json
import os
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field, replace

import pymupdf

from paperleaf.core.document import PdfDocument

mupdf = pymupdf.mupdf

TEXT, CHECK, RADIO, COMBO, LIST, SIGNATURE, BUTTON = (
    "text", "checkbox", "radio", "combo", "list", "signature", "button")

_KINDS = {
    pymupdf.PDF_WIDGET_TYPE_TEXT: TEXT, pymupdf.PDF_WIDGET_TYPE_CHECKBOX: CHECK,
    pymupdf.PDF_WIDGET_TYPE_RADIOBUTTON: RADIO, pymupdf.PDF_WIDGET_TYPE_COMBOBOX: COMBO,
    pymupdf.PDF_WIDGET_TYPE_LISTBOX: LIST, pymupdf.PDF_WIDGET_TYPE_SIGNATURE: SIGNATURE,
    pymupdf.PDF_WIDGET_TYPE_BUTTON: BUTTON,
}
_TYPES = {v: k for k, v in _KINDS.items()}

KIND_LABELS = {TEXT: "Text field", CHECK: "Check box", RADIO: "Radio button", COMBO: "Dropdown",
               LIST: "List box", SIGNATURE: "Signature field", BUTTON: "Button"}
# base names for new fields, like Acrobat ("Text1", "Check Box1", ...)
NEW_NAMES = {TEXT: "Text", CHECK: "Check Box", RADIO: "Group", COMBO: "Dropdown", LIST: "List Box",
             SIGNATURE: "Signature"}
# default size (points) of a field placed with a single click
DEFAULT_SIZES = {TEXT: (150, 22), CHECK: (14, 14), RADIO: (14, 14), COMBO: (150, 22), LIST: (150, 66),
                 SIGNATURE: (180, 40)}

FF_READONLY, FF_REQUIRED, FF_NOEXPORT = 1, 2, 4
FF_MULTILINE = 1 << 12
FF_PASSWORD = 1 << 13
FF_NOTOGGLEOFF = 1 << 14
FF_RADIO = 1 << 15
FF_PUSHBUTTON = 1 << 16
FF_COMBO = 1 << 17
FF_EDIT = 1 << 18
FF_MULTISELECT = 1 << 21
FF_COMB = 1 << 24

LIST_SELECTION = (0.6, 0.75, 0.86)  # Acrobat's highlight for selected list items
LIST_LINE = 1.15                     # list box row height, in font sizes
AUTO_LIST_FONT = 12.0


@dataclass
class Field:
    """One widget of a form field. Radio groups and fields shown in several
    places have several widgets that share one ``name``."""

    xref: int
    pno: int
    rect: pymupdf.Rect
    name: str
    kind: str
    flags: int = 0
    value: object = ""        # field value: str, or list[str] for multi-select list boxes
    state: str = ""           # check boxes / radio buttons: this widget's state (AS)
    on_state: str = ""        # check boxes / radio buttons: this widget's "on" name
    options: list = field(default_factory=list)   # [(export value, display text)]
    maxlen: int = 0
    tooltip: str = ""
    default: object = ""
    fontsize: float = 0.0     # 0 = auto
    text_color: tuple = (0, 0, 0)
    align: int = 0            # 0 left, 1 centre, 2 right
    field_xref: int = 0       # the field dictionary that holds /V
    hidden: bool = False
    signed: bool = False
    calculated: bool = False  # has a JavaScript calculation (never run)
    action: dict | None = None  # push buttons: {"type": ..., ...}

    @property
    def readonly(self) -> bool:
        return bool(self.flags & FF_READONLY)

    @property
    def required(self) -> bool:
        return bool(self.flags & FF_REQUIRED)

    @property
    def multiline(self) -> bool:
        return self.kind == TEXT and bool(self.flags & FF_MULTILINE)

    @property
    def password(self) -> bool:
        return self.kind == TEXT and bool(self.flags & FF_PASSWORD)

    @property
    def comb(self) -> bool:
        return (self.kind == TEXT and bool(self.flags & FF_COMB) and self.maxlen > 0
                and not self.flags & (FF_MULTILINE | FF_PASSWORD))

    @property
    def editable(self) -> bool:
        """Combo box that accepts typed text."""
        return self.kind == COMBO and bool(self.flags & FF_EDIT)

    @property
    def multiselect(self) -> bool:
        return self.kind == LIST and bool(self.flags & FF_MULTISELECT)

    @property
    def is_on(self) -> bool:
        return self.kind in (CHECK, RADIO) and self.state not in ("", "Off")

    @property
    def label(self) -> str:
        return KIND_LABELS.get(self.kind, "Field")

    @property
    def interactive(self) -> bool:
        """Can be clicked or typed into (ignores permissions)."""
        return not self.hidden and not self.readonly and self.kind != BUTTON

    @property
    def values(self) -> list[str]:
        """The value as a list (list boxes), handy for choice fields."""
        if isinstance(self.value, list):
            return list(self.value)
        return [self.value] if self.value else []

    def display_of(self, export: str) -> str:
        return next((d for e, d in self.options if e == export), export)

    def export_of(self, text: str) -> str:
        """Export value for a typed or displayed choice text."""
        for e, d in self.options:
            if text in (e, d):
                return e
        return text


@dataclass
class FormInfo:
    count: int = 0            # number of widgets
    names: int = 0            # number of distinct fields
    xfa: bool = False         # has an XFA (LiveCycle) form
    calculated: bool = False  # some fields are calculated by JavaScript
    javascript: bool = False  # any form JavaScript at all
    signed: int = 0           # signature fields that already hold a certificate signature

    @property
    def xfa_only(self) -> bool:
        return self.xfa and self.count == 0

    @property
    def any(self) -> bool:
        return self.count > 0 or self.xfa

    @property
    def fillable(self) -> bool:
        """Something to fill in (signed signature fields don't count)."""
        return self.count > self.signed or self.xfa


# ===================================================================== PDF objects
class Name(str):
    """A PDF name object (``/Yes``), as opposed to a string."""


class _Ref(tuple):
    pass


def decode_pdf_bytes(raw: bytes) -> str:
    if raw.startswith(b"\xfe\xff"):
        return raw[2:].decode("utf-16-be", "replace")
    if raw.startswith(b"\xff\xfe"):
        return raw[2:].decode("utf-16-le", "replace")
    if raw.startswith(b"\xef\xbb\xbf"):
        return raw[3:].decode("utf-8", "replace")
    return raw.decode("latin-1")


_WS = b" \t\r\n\f\x00"
_DELIM = b"()<>[]{}/%"
_ESC = {ord("n"): b"\n", ord("r"): b"\r", ord("t"): b"\t", ord("b"): b"\b", ord("f"): b"\f",
        ord("("): b"(", ord(")"): b")", ord("\\"): b"\\"}


class PdfParser:
    """Small parser for PDF object syntax (enough for field values and FDF files)."""

    def __init__(self, data: bytes):
        self.d = data
        self.i = 0

    def ws(self):
        d, n = self.d, len(self.d)
        while self.i < n:
            c = d[self.i]
            if c in _WS:
                self.i += 1
            elif c == ord("%"):
                while self.i < n and d[self.i] not in b"\r\n":
                    self.i += 1
            else:
                break

    def at_end(self) -> bool:
        self.ws()
        return self.i >= len(self.d)

    def token(self) -> bytes:
        self.ws()
        j = self.i
        while self.i < len(self.d) and self.d[self.i] not in _WS and self.d[self.i] not in _DELIM:
            self.i += 1
        return self.d[j:self.i]

    def parse(self):
        self.ws()
        d = self.d
        if self.i >= len(d):
            raise ValueError("unexpected end of data")
        c = d[self.i]
        if d.startswith(b"<<", self.i):
            self.i += 2
            out = {}
            while True:
                self.ws()
                if d.startswith(b">>", self.i):
                    self.i += 2
                    return out
                key = self.parse()
                val = self.parse()
                out[str(key)] = val
        if c == ord("<"):
            j = d.index(b">", self.i)
            hexdata = re.sub(rb"\s", b"", d[self.i + 1:j])
            self.i = j + 1
            if len(hexdata) % 2:
                hexdata += b"0"
            return decode_pdf_bytes(bytes.fromhex(hexdata.decode("ascii")))
        if c == ord("["):
            self.i += 1
            out = []
            while True:
                self.ws()
                if self.i < len(d) and d[self.i] == ord("]"):
                    self.i += 1
                    return out
                out.append(self.parse())
        if c == ord("("):
            return decode_pdf_bytes(self._literal())
        if c == ord("/"):
            self.i += 1
            j = self.i
            while self.i < len(d) and d[self.i] not in _WS and d[self.i] not in _DELIM:
                self.i += 1
            raw = re.sub(rb"#([0-9A-Fa-f]{2})", lambda m: bytes([int(m.group(1), 16)]), d[j:self.i])
            return Name(raw.decode("utf-8", "replace"))
        tok = self.token()
        if not tok:
            self.i += 1
            return None
        if tok in (b"true", b"false"):
            return tok == b"true"
        if tok == b"null":
            return None
        if re.fullmatch(rb"[+-]?\d+", tok):
            save = self.i
            gen = self.token()
            if re.fullmatch(rb"\d+", gen or b""):
                if self.token() == b"R":
                    return _Ref((int(tok), int(gen)))
            self.i = save
            return int(tok)
        if re.fullmatch(rb"[+-]?(\d+\.?\d*|\.\d+)", tok):
            return float(tok)
        return tok.decode("latin-1")  # keyword (obj, endobj, ...)

    def _literal(self) -> bytes:
        d = self.d
        self.i += 1
        depth = 1
        out = bytearray()
        while self.i < len(d):
            c = d[self.i]
            self.i += 1
            if c == ord("\\"):
                if self.i >= len(d):
                    break
                e = d[self.i]
                self.i += 1
                if e in _ESC:
                    out += _ESC[e]
                elif ord("0") <= e <= ord("7"):
                    digits = bytes([e])
                    while len(digits) < 3 and self.i < len(d) and ord("0") <= d[self.i] <= ord("7"):
                        digits += bytes([d[self.i]])
                        self.i += 1
                    out.append(int(digits, 8) & 0xFF)
                elif e == ord("\r"):
                    if self.i < len(d) and d[self.i] == ord("\n"):
                        self.i += 1
                elif e == ord("\n"):
                    pass
                else:
                    out.append(e)
            elif c == ord("("):
                depth += 1
                out.append(c)
            elif c == ord(")"):
                depth -= 1
                if depth == 0:
                    break
                out.append(c)
            else:
                out.append(c)
        return bytes(out)


def parse_pdf_value(text: str):
    """Parse the text of a PDF object as returned by ``xref_get_key``."""
    try:
        return PdfParser(text.encode("latin-1", "replace")).parse()
    except (ValueError, IndexError):
        return None


def _key_value(kv):
    """Turn an ``xref_get_key`` result into a Python value."""
    kind, val = kv
    if kind == "null":
        return None
    if kind == "string":
        return val
    if kind == "name":
        return Name(val[1:] if val.startswith("/") else val)
    if kind == "int":
        return int(val)
    if kind == "float":
        return float(val)
    if kind == "bool":
        return val == "true"
    if kind == "xref":
        return _Ref((int(val.split()[0]), 0))
    return parse_pdf_value(val)


def pdf_name(value: str) -> str:
    """``/Name`` syntax, escaping characters that need it."""
    out = []
    for ch in value:
        b = ch.encode("utf-8")
        if len(b) > 1 or ch in "()<>[]{}/%#" or ord(ch) <= 32 or ord(ch) >= 127:
            out.append("".join(f"#{x:02X}" for x in b))
        else:
            out.append(ch)
    return "/" + "".join(out)


def pdf_string(value: str) -> str:
    return pymupdf.get_pdf_str(value or "")


def _value_text(value) -> str:
    """PDF syntax for a field value: string, or array of strings."""
    if isinstance(value, (list, tuple)):
        return "[" + " ".join(pdf_string(v) for v in value) + "]"
    return pdf_string(value)


# ===================================================================== reading
def _xg(doc: PdfDocument, xref: int, key: str):
    try:
        return doc.doc.xref_get_key(xref, key)
    except Exception:
        return ("null", "null")


def _parent(doc: PdfDocument, xref: int) -> int:
    t = _xg(doc, xref, "Parent")
    return int(t[1].split()[0]) if t[0] == "xref" else 0


def _inherited(doc: PdfDocument, xref: int, key: str):
    """Value of an inheritable field key (looks up the /Parent chain)."""
    x, seen = xref, set()
    while x and x not in seen:
        seen.add(x)
        t = _xg(doc, x, key)
        if t[0] != "null":
            return _key_value(t)
        x = _parent(doc, x)
    return None


def _terminal(doc: PdfDocument, xref: int) -> int:
    """The field dictionary of a widget: itself, or the nearest parent with a /T."""
    x, seen = xref, set()
    while x and x not in seen:
        seen.add(x)
        if _xg(doc, x, "T")[0] != "null":
            return x
        p = _parent(doc, x)
        if not p:
            break
        x = p
    return xref


def _ap_states(doc: PdfDocument, xref: int) -> list[str]:
    t = _xg(doc, xref, "AP/N")
    if t[0] == "xref":
        try:
            t = ("dict", doc.doc.xref_object(int(t[1].split()[0]), compressed=True))
        except Exception:
            return []
    if t[0] != "dict":
        return []
    d = parse_pdf_value(t[1])
    return [str(k) for k in d] if isinstance(d, dict) else []


def _choice_options(raw) -> list[tuple[str, str]]:
    out = []
    for o in raw if isinstance(raw, list) else []:
        if isinstance(o, list) and len(o) >= 2:
            out.append((str(o[0]), str(o[1])))
        elif isinstance(o, list) and o:
            out.append((str(o[0]), str(o[0])))
        elif o is not None and not isinstance(o, (list, dict)):
            out.append((str(o), str(o)))
    return out


def _as_value(raw, kind: str):
    if kind == LIST:
        if isinstance(raw, list):
            return [str(v) for v in raw if v is not None]
        return [str(raw)] if raw not in (None, "") else []
    if kind in (CHECK, RADIO):
        return str(raw) if raw not in (None, "") else "Off"
    if isinstance(raw, list):
        return str(raw[0]) if raw else ""
    return "" if raw is None else str(raw)


def _action(doc: PdfDocument, xref: int) -> dict | None:
    s = _xg(doc, xref, "A/S")
    if s[0] != "name":
        if _xg(doc, xref, "AA")[0] != "null":
            return {"type": "JavaScript"}
        return None
    kind = s[1].lstrip("/")
    act = {"type": kind}
    if kind == "Named":
        act["name"] = _xg(doc, xref, "A/N")[1].lstrip("/")
    elif kind == "URI":
        act["uri"] = _key_value(_xg(doc, xref, "A/URI")) or ""
    return act


def _record(doc: PdfDocument, pno: int, w: pymupdf.Widget) -> Field | None:
    kind = _KINDS.get(w.field_type)
    if kind is None:
        return None
    xref = w.xref
    fx = _terminal(doc, xref)
    f = Field(xref=xref, pno=pno, rect=pymupdf.Rect(w.rect), name=w.field_name or "", kind=kind,
              flags=int(w.field_flags or 0), field_xref=fx)
    f.tooltip = w.field_label or ""
    f.hidden = w.field_display in (1, 3)
    f.fontsize = float(w.text_fontsize or 0)
    tc = w.text_color or (0, 0, 0)
    f.text_color = tuple(tc) * 3 if len(tc) == 1 else tuple(tc[:3])
    f.align = int(_inherited(doc, xref, "Q") or 0)
    f.calculated = _inherited(doc, xref, "AA") is not None and \
        any(_xg(doc, x, "AA/C")[0] != "null" for x in (xref, fx))
    raw_v = _inherited(doc, xref, "V")
    raw_dv = _inherited(doc, xref, "DV")
    f.value = _as_value(raw_v, kind)
    f.default = _as_value(raw_dv, kind)
    if kind == TEXT:
        f.maxlen = int(_inherited(doc, xref, "MaxLen") or 0)
        if raw_v is None:
            f.value = w.field_value or ""
    elif kind in (COMBO, LIST):
        f.options = _choice_options(_inherited(doc, xref, "Opt"))
    elif kind in (CHECK, RADIO):
        states = [s for s in _ap_states(doc, xref) if s != "Off"]
        f.on_state = states[0] if states else "Yes"
        a = _xg(doc, xref, "AS")
        f.state = a[1].lstrip("/") if a[0] == "name" else ("Off" if states else f.value)
    elif kind == SIGNATURE:
        f.signed = bool(w.is_signed)
    elif kind == BUTTON:
        f.action = _action(doc, xref)
    return f


def _tab_order(doc: PdfDocument, pno: int, fields: list[Field]) -> list[Field]:
    try:
        tabs = doc.doc.xref_get_key(doc.page(pno).xref, "Tabs")
    except Exception:
        tabs = ("null", "null")
    mode = tabs[1].lstrip("/") if tabs[0] == "name" else ""
    rot = doc.rotation_matrix(pno)

    def shown(f):  # position as the page is displayed
        return (pymupdf.Rect(f.rect) * rot).normalize()
    if mode == "R":
        return sorted(fields, key=lambda f: (round(shown(f).y0 / 4), shown(f).x0))
    if mode == "C":
        return sorted(fields, key=lambda f: (round(shown(f).x0 / 4), shown(f).y0))
    return fields  # annotation order (Acrobat's default)


def page_fields(doc: PdfDocument, pno: int) -> list[Field]:
    """Form fields (widgets) on a page, in tab order."""
    def load():
        if not doc.is_pdf or not has_fields(doc):
            return []
        out = []
        try:
            for w in doc.page(pno).widgets():
                rec = _record(doc, pno, w)
                if rec is not None:
                    out.append(rec)
        except Exception:
            return out
        return _tab_order(doc, pno, out)
    return doc._widgets.get_or(pno, load)


def has_fields(doc: PdfDocument) -> bool:
    if not doc.is_pdf:
        return False
    if "has" not in doc._forms:
        try:
            doc._forms["has"] = bool(doc.doc.is_form_pdf)
        except Exception:
            doc._forms["has"] = False
    return doc._forms["has"]


def all_fields(doc: PdfDocument) -> list[Field]:
    """Every widget in the document, in tab order (page by page)."""
    if "all" not in doc._forms:
        out = []
        if has_fields(doc):
            for pno in range(doc.page_count):
                out.extend(page_fields(doc, pno))
        doc._forms["all"] = out
    return doc._forms["all"]


def find(doc: PdfDocument, pno: int, xref: int) -> Field | None:
    return next((f for f in page_fields(doc, pno) if f.xref == xref), None)


def group(doc: PdfDocument, f: Field) -> list[Field]:
    """All widgets of the field ``f`` belongs to (same name)."""
    return [g for g in all_fields(doc) if g.name == f.name] or [f]


def by_name(doc: PdfDocument) -> dict[str, list[Field]]:
    out: dict[str, list[Field]] = {}
    for f in all_fields(doc):
        out.setdefault(f.name, []).append(f)
    return out


def field_at(doc: PdfDocument, pno: int, x: float, y: float, tol: float = 0.0,
             include_hidden: bool = False) -> Field | None:
    for f in reversed(page_fields(doc, pno)):
        if f.hidden and not include_hidden:
            continue
        r = f.rect
        if r.x0 - tol <= x <= r.x1 + tol and r.y0 - tol <= y <= r.y1 + tol:
            return f
    return None


def _acroform_key(doc: PdfDocument, key: str):
    try:
        return doc.doc.xref_get_key(doc.doc.pdf_catalog(), f"AcroForm/{key}")
    except Exception:
        return ("null", "null")


def form_info(doc: PdfDocument) -> FormInfo:
    if "info" in doc._forms:
        return doc._forms["info"]
    info = FormInfo()
    if doc.is_pdf:
        fields = all_fields(doc)
        info.count = len(fields)
        info.names = len({f.name for f in fields})
        info.signed = sum(1 for f in fields if f.kind == SIGNATURE and f.signed)
        info.xfa = _acroform_key(doc, "XFA")[0] != "null"
        co = _acroform_key(doc, "CO")
        info.calculated = (co[0] == "array" and co[1].strip("[] ") != "") or any(f.calculated for f in fields)
        info.javascript = info.calculated or any(
            _xg(doc, f.xref, "AA")[0] != "null" or _xg(doc, f.field_xref, "AA")[0] != "null"
            or (f.action or {}).get("type") == "JavaScript" for f in fields)
    doc._forms["info"] = info
    return info


def can_fill(doc: PdfDocument) -> bool:
    return doc.can_fill_forms()


def can_prepare(doc: PdfDocument) -> bool:
    return doc.can_annotate() and doc.can_modify()


def can_flatten(doc: PdfDocument) -> bool:
    return doc.can_modify()


def data_fields(doc: PdfDocument) -> dict[str, list[Field]]:
    """Fields that hold data (for export, clearing...), by name."""
    out = {}
    for name, fs in by_name(doc).items():
        if not name or fs[0].kind in (BUTTON, SIGNATURE):
            continue
        out[name] = fs
    return out


# ===================================================================== writing
# PyMuPDF's get_new_xref() and update_object() refuse to run inside a journal
# step, so new objects are made with MuPDF's own calls.
def _pdf(doc: PdfDocument):
    return pymupdf._as_pdf_document(doc.doc)


def _obj_from_str(pdf, text: str):
    buf = mupdf.fz_new_buffer_from_copied_data(text.encode("utf-8"))
    stm = mupdf.fz_open_buffer(buf)
    lexbuf = mupdf.PdfLexbuf(mupdf.PDF_LEXBUF_SMALL)
    return mupdf.pdf_parse_stm_obj(pdf, stm, lexbuf)


def _new_object(doc: PdfDocument, text: str, stream: bytes | None = None) -> int:
    pdf = _pdf(doc)
    xref = mupdf.pdf_create_object(pdf)
    mupdf.pdf_update_object(pdf, xref, _obj_from_str(pdf, text))
    if stream is not None:
        doc.doc.update_stream(xref, stream)
    return xref


def _replace_object(doc: PdfDocument, xref: int, text: str):
    pdf = _pdf(doc)
    mupdf.pdf_update_object(pdf, xref, _obj_from_str(pdf, text))


def _widget(doc: PdfDocument, f: Field, keep: list):
    w = doc.page(f.pno).load_widget(f.xref)
    if w is None:
        raise KeyError(f"form field {f.name!r} not found")
    keep.append(w)  # the page and widget must stay alive while MuPDF uses them
    return w


def _regenerate(doc: PdfDocument, f: Field, keep: list):
    """Rebuild a widget's appearance from its dictionary (inside an edit)."""
    if f.kind == LIST:
        _write_listbox_ap(doc, f)
        return
    w = _widget(doc, f, keep)
    a = w._annot.this
    mupdf.pdf_dirty_annot(a)
    mupdf.pdf_update_annot(a)


def _drop_xfa(doc: PdfDocument):
    """Filled AcroForm values only show in other apps once the XFA copy is gone."""
    if _acroform_key(doc, "XFA")[0] != "null":
        doc.doc.xref_set_key(doc.doc.pdf_catalog(), "AcroForm/XFA", "null")


def _normalize(f: Field, value):
    if f.kind == LIST:
        if value is None or value == "":
            vals = []
        elif isinstance(value, (list, tuple)):
            vals = [f.export_of(str(v)) for v in value]
        else:
            vals = [f.export_of(str(value))]
        if not f.multiselect:
            vals = vals[:1]
        known = [e for e, _d in f.options]
        return [v for v in known if v in vals] + [v for v in vals if v not in known]
    if f.kind in (CHECK, RADIO):
        if value is True:
            return f.on_state
        if value in (None, False, "", "Off"):
            return "Off"
        value = str(value)
        if f.kind == CHECK and value != f.on_state and value.lower() in ("yes", "on", "true", "1", "x", "checked"):
            return f.on_state  # e.g. "Yes" from a data file for a box whose "on" value is "On"
        return value
    value = "" if value is None else str(value)
    if f.kind == TEXT:
        if not f.multiline:
            value = value.replace("\r\n", " ").replace("\n", " ").replace("\r", " ")
        if f.maxlen > 0:
            value = value[:f.maxlen]
    elif f.kind == COMBO:
        value = f.export_of(value)
    return value


def _same(a, b) -> bool:
    if isinstance(a, list) or isinstance(b, list):
        return list(a or []) == list(b or [])
    return (a or "") == (b or "")


def _write_value(doc: PdfDocument, fields: list[Field], value, keep: list):
    """Set the value of one field (all its widgets). Must run inside an edit."""
    f0 = fields[0]
    kind = f0.kind
    field_xrefs = sorted({f.field_xref for f in fields})
    if kind in (CHECK, RADIO):
        val = value or "Off"
        for fx in field_xrefs:
            doc.doc.xref_set_key(fx, "V", pdf_name(val))
        for f in fields:
            states = _ap_states(doc, f.xref)
            on = val != "Off" and (val in states or (not states and val == f.on_state))
            doc.doc.xref_set_key(f.xref, "AS", pdf_name(val if on else "Off"))
            if not states:
                _regenerate(doc, f, keep)
        return
    if kind == LIST:
        vals = list(value or [])
        for fx in field_xrefs:
            if not vals:
                doc.doc.xref_set_key(fx, "V", "null")
                doc.doc.xref_set_key(fx, "I", "null")
            else:
                doc.doc.xref_set_key(fx, "V", _value_text(vals if len(vals) > 1 else vals[0]))
                exports = [e for e, _d in f0.options]
                idx = sorted(exports.index(v) for v in vals if v in exports)
                doc.doc.xref_set_key(fx, "I", "[" + " ".join(map(str, idx)) + "]" if idx else "null")
        for f in fields:
            _write_listbox_ap(doc, replace(f, value=vals))
        return
    for fx in field_xrefs:
        doc.doc.xref_set_key(fx, "V", pdf_string(value))
    if kind == COMBO:
        for fx in field_xrefs:
            doc.doc.xref_set_key(fx, "I", "null")
    for f in fields:
        _regenerate(doc, f, keep)


def _current(fields: list[Field]):
    f0 = fields[0]
    if f0.kind in (CHECK, RADIO):
        return next((g.state for g in fields if g.is_on), "Off")
    return f0.value


def _pages_of(fields) -> set[int]:
    return {f.pno for f in fields}


def set_value(doc: PdfDocument, f: Field, value) -> bool:
    """Fill in a field (one undoable step). Returns False if nothing changed."""
    fields = group(doc, f)
    value = _normalize(fields[0], value)
    if _same(_current(fields), value):
        return False
    with doc.edit("Fill in field", _pages_of(fields)):
        keep: list = []
        _drop_xfa(doc)
        _write_value(doc, fields, value, keep)
    return True


def toggle(doc: PdfDocument, f: Field) -> bool:
    """Click on a check box or radio button."""
    if f.kind == CHECK:
        return set_value(doc, f, "Off" if f.is_on else f.on_state)
    if f.kind == RADIO:
        if f.is_on:
            if f.flags & FF_NOTOGGLEOFF:
                return False
            return set_value(doc, f, "Off")
        return set_value(doc, f, f.on_state)
    return False


def _default_of(f: Field):
    if f.kind == LIST:
        return list(f.default or [])
    if f.kind in (CHECK, RADIO):
        return f.default or "Off"
    return f.default or ""


def reset_form(doc: PdfDocument) -> bool:
    """Set every field back to its default value ("Clear form"). One undoable step.
    Read-only fields are left alone."""
    changes = []
    for _name, fs in data_fields(doc).items():
        f0 = fs[0]
        if f0.readonly:
            continue
        target = _normalize(f0, _default_of(f0))
        if not _same(_current(fs), target):
            changes.append((fs, target))
    if not changes:
        return False
    pages = set().union(*(_pages_of(fs) for fs, _t in changes))
    with doc.edit("Clear form", pages):
        keep: list = []
        _drop_xfa(doc)
        for fs, target in changes:
            _write_value(doc, fs, target, keep)
    return True


def set_values(doc: PdfDocument, data: dict, label: str = "Import form data") -> tuple[int, list[str]]:
    """Set many fields at once: {name: value}. Returns (fields changed, unknown names)."""
    fields = data_fields(doc)
    changes, unknown = [], []
    for name, value in data.items():
        fs = fields.get(name)
        if fs is None:
            unknown.append(name)
            continue
        f0 = fs[0]
        target = _normalize(f0, value)
        if not _same(_current(fs), target):
            changes.append((fs, target))
    if changes:
        pages = set().union(*(_pages_of(fs) for fs, _t in changes))
        with doc.edit(label, pages):
            keep: list = []
            _drop_xfa(doc)
            for fs, target in changes:
                _write_value(doc, fs, target, keep)
    return len(changes), unknown


# ------------------------------------------------------------- list box appearance
def _helv_font(doc: PdfDocument) -> int:
    t = _acroform_key(doc, "DR/Font/Helv")
    if t[0] == "xref":
        return int(t[1].split()[0])
    x = _new_object(doc, "<</Type/Font/Subtype/Type1/BaseFont/Helvetica/Encoding/WinAnsiEncoding>>")
    cat = doc.doc.pdf_catalog()
    dr = doc.doc.xref_get_key(cat, "AcroForm/DR")
    if dr[0] == "null":
        doc.doc.xref_set_key(cat, "AcroForm/DR", f"<</Font<</Helv {x} 0 R>>>>")
    elif doc.doc.xref_get_key(cat, "AcroForm/DR/Font")[0] == "null":
        doc.doc.xref_set_key(cat, "AcroForm/DR/Font", f"<</Helv {x} 0 R>>")
    else:
        doc.doc.xref_set_key(cat, "AcroForm/DR/Font/Helv", f"{x} 0 R")
    return x


def _ps_text(s: str) -> str:
    b = s.encode("cp1252", "replace")
    return "(" + b.decode("latin-1").replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)") + ")"


def _fmt(v: float) -> str:
    return f"{v:.2f}".rstrip("0").rstrip(".") or "0"


def listbox_layout(f: Field) -> tuple[float, float, int]:
    """(font size, row height, first visible row) of a list box."""
    fs = f.fontsize or AUTO_LIST_FONT
    lh = fs * LIST_LINE
    rows = max(1, int((f.rect.height - 2) // lh))
    exports = [e for e, _d in f.options]
    sel = [exports.index(v) for v in f.values if v in exports]
    top = 0
    if sel and sel[0] >= rows:
        top = min(sel[0], max(0, len(exports) - rows))
    return fs, lh, top


def _write_listbox_ap(doc: PdfDocument, f: Field):
    """Appearance of a list box with its selected rows highlighted (MuPDF's own
    list appearance does not show the selection)."""
    raw = doc.doc.xref_get_key(f.xref, "Rect")
    nums = [float(v) for v in re.findall(r"-?\d*\.?\d+", raw[1])] if raw[0] == "array" else []
    w = abs(nums[2] - nums[0]) if len(nums) == 4 else f.rect.width
    h = abs(nums[3] - nums[1]) if len(nums) == 4 else f.rect.height
    fs, lh, top = listbox_layout(f)
    sel = set(f.values)
    r, g, b = (list(f.text_color) + [0, 0, 0])[:3]
    out = []

    def color(key):
        v = parse_pdf_value(_xg(doc, f.xref, key)[1]) if _xg(doc, f.xref, key)[0] == "array" else None
        if isinstance(v, list) and len(v) in (1, 3) and all(isinstance(c, (int, float)) for c in v):
            return " ".join(_fmt(c) for c in v) + (" g" if len(v) == 1 else " rg")
        return None
    bg, bc = color("MK/BG"), color("MK/BC")
    bw = _key_value(_xg(doc, f.xref, "BS/W"))
    bw = float(bw) if isinstance(bw, (int, float)) else 1.0
    if bg:
        out.append(f"{bg} 0 0 {_fmt(w)} {_fmt(h)} re f")
    if bc and bw > 0:
        out.append(f"{bc.replace('rg', 'RG').replace(' g', ' G')} {_fmt(bw)} w "
                   f"{_fmt(bw / 2)} {_fmt(bw / 2)} {_fmt(w - bw)} {_fmt(h - bw)} re S")
    out += ["/Tx BMC", "q", f"1 1 {_fmt(w - 2)} {_fmt(h - 2)} re W n"]
    rows = []
    y = h - 1
    for i, (exp, disp) in enumerate(f.options[top:]):
        if y - lh < 0 and i:
            break
        rows.append((y, exp, disp))
        y -= lh
    for y, exp, _d in rows:
        if exp in sel:
            r0, g0, b0 = LIST_SELECTION
            out.append(f"{_fmt(r0)} {_fmt(g0)} {_fmt(b0)} rg 1 {_fmt(y - lh)} {_fmt(w - 2)} {_fmt(lh)} re f")
    out.append(f"BT /Helv {_fmt(fs)} Tf {_fmt(r)} {_fmt(g)} {_fmt(b)} rg")
    for y, _e, disp in rows:
        base = y - lh + (lh - fs) / 2 + fs * 0.22
        out.append(f"1 0 0 1 3 {_fmt(base)} Tm {_ps_text(disp)} Tj")
    out += ["ET", "Q", "EMC"]
    font = _helv_font(doc)
    x = _new_object(doc, f"<</Type/XObject/Subtype/Form/BBox[0 0 {_fmt(w)} {_fmt(h)}]"
                         f"/Resources<</Font<</Helv {font} 0 R>>>>>>", "\n".join(out).encode("latin-1"))
    doc.doc.xref_set_key(f.xref, "AP", f"<</N {x} 0 R>>")
    doc.doc.xref_set_key(f.field_xref, "TI", str(top) if top else "null")


# ------------------------------------------------------------------ flattening
def flatten(doc: PdfDocument, comments: bool = False):
    """Burn field values (and optionally comments) into the page content."""
    label = "Flatten form and comments" if comments else "Flatten form"
    empty_sigs = [f for f in all_fields(doc) if f.kind == SIGNATURE and not f.signed]
    with doc.edit(label, range(doc.page_count), content=True):
        for f in empty_sigs:  # the "sign here" placeholder should not be burned in
            page = doc.page(f.pno)
            w = page.load_widget(f.xref)
            if w is not None:
                page.delete_widget(w)
        doc.doc.bake(annots=comments, widgets=True)


# ------------------------------------------------------------------- signatures
def sign_field(doc: PdfDocument, f: Field, png: bytes, w: float, h: float, author: str) -> int:
    """Place a saved signature image in a signature field (as a stamp, like
    Fill & Sign). The field's "sign here" placeholder is hidden."""
    from paperleaf.core import annotations as an
    r = pymupdf.Rect(f.rect)
    s = min(r.width / w, r.height / h) if w > 0 and h > 0 else 1.0
    sw, sh = w * s, h * s
    cx, cy = (r.x0 + r.x1) / 2, (r.y0 + r.y1) / 2
    box = pymupdf.Rect(cx - sw / 2, cy - sh / 2, cx + sw / 2, cy + sh / 2)
    with doc.edit("Sign", f.pno):
        raw = doc.doc.xref_get_key(f.xref, "Rect")
        nums = [float(v) for v in re.findall(r"-?\d*\.?\d+", raw[1])] if raw[0] == "array" else [0, 0, 1, 1]
        bw, bh = abs(nums[2] - nums[0]), abs(nums[3] - nums[1])
        x = _new_object(doc, f"<</Type/XObject/Subtype/Form/BBox[0 0 {_fmt(bw)} {_fmt(bh)}]>>", b" ")
        doc.doc.xref_set_key(f.xref, "AP", f"<</N {x} 0 R>>")
        a = doc.page(f.pno).add_stamp_annot(box, stamp=png)
        an._info(a, author, subject="Signature", contents="")
        a.update()
        return a.xref


# ===================================================================== data files
DATA_FILTER = "FDF (*.fdf);;XFDF (*.xfdf);;JSON (*.json);;All files (*.*)"
IMPORT_FILTER = "Form data (*.fdf *.xfdf *.json);;All files (*.*)"


def form_data(doc: PdfDocument) -> dict:
    """{name: value} of every field that holds data, in tab order."""
    out = {}
    for name, fs in data_fields(doc).items():
        f0 = fs[0]
        if f0.flags & FF_NOEXPORT:
            continue
        if f0.kind in (CHECK, RADIO):
            out[name] = _current(fs)
        elif f0.kind == LIST:
            vals = f0.values
            out[name] = vals if f0.multiselect else (vals[0] if vals else "")
        else:
            out[name] = f0.value
    return out


def _button_names(doc: PdfDocument) -> set:
    return {n for n, fs in data_fields(doc).items() if fs[0].kind in (CHECK, RADIO)}


def _tree(data: dict) -> dict:
    """{"a.b": 1} -> {"a": {"b": 1}} (values stay in "\0" keys)."""
    root: dict = {}
    for name, value in data.items():
        node = root
        parts = name.split(".")
        for p in parts[:-1]:
            node = node.setdefault(p, {})
            if not isinstance(node, dict):
                break
        node.setdefault(parts[-1], {})["\0"] = value
    return root


def to_fdf(data: dict, buttons: set = frozenset(), pdf_file: str = "") -> bytes:
    def value(name, v):
        if isinstance(v, list):
            return "[" + " ".join(pdf_string(x) for x in v) + "]"
        if name in buttons:
            return pdf_name(v or "Off")
        return pdf_string(v)

    def fields(node, prefix):
        out = []
        for key, child in node.items():
            if key == "\0":
                continue
            full = f"{prefix}.{key}" if prefix else key
            parts = [f"/T {pdf_string(key)}"]
            if "\0" in child:
                v = child["\0"]
                parts.append(f"/V {value(full, v)}")
            kids = fields(child, full)
            if kids:
                parts.append("/Kids [" + " ".join(kids) + "]")
            out.append("<<" + " ".join(parts) + ">>")
        return out

    body = "<</FDF <<"
    if pdf_file:
        body += f"/F {pdf_string(pdf_file)} "
    body += "/Fields [\n" + "\n".join(fields(_tree(data), "")) + "\n]>>>>"
    text = f"%FDF-1.2\n%\xe2\xe3\xcf\xd3\n1 0 obj\n{body}\nendobj\ntrailer\n<</Root 1 0 R>>\n%%EOF\n"
    return text.encode("latin-1")


def parse_fdf(raw: bytes) -> dict:
    objs, trailer, top = {}, None, []
    p = PdfParser(raw)
    stack = []
    while not p.at_end():
        try:
            item = p.parse()
        except (ValueError, IndexError):
            break
        if item == "obj" and len(stack) >= 2 and isinstance(stack[-1], int) and isinstance(stack[-2], int):
            num = stack[-2]
            stack = stack[:-2]
            objs[num] = p.parse()
        elif item == "trailer":
            trailer = p.parse()
        elif item == "stream":
            end = raw.find(b"endstream", p.i)
            p.i = end + 9 if end >= 0 else len(raw)
        else:
            stack.append(item)
            if isinstance(item, dict):
                top.append(item)

    def deref(v):
        seen = 0
        while isinstance(v, _Ref) and seen < 50:
            v = objs.get(v[0])
            seen += 1
        return v
    root = deref((trailer or {}).get("Root")) if trailer else None
    fdf = deref(root.get("FDF")) if isinstance(root, dict) else None
    if not isinstance(fdf, dict):
        fdf = next((deref(o.get("FDF")) for o in list(objs.values()) + top
                    if isinstance(o, dict) and "FDF" in o), None)
    if not isinstance(fdf, dict):
        raise ValueError("This is not an FDF form data file.")
    out = {}

    def walk(items, prefix):
        for it in deref(items) or []:
            it = deref(it)
            if not isinstance(it, dict):
                continue
            t = deref(it.get("T"))
            name = f"{prefix}.{t}" if prefix and t is not None else (str(t) if t is not None else prefix)
            if "V" in it:
                v = deref(it["V"])
                if isinstance(v, list):
                    v = [str(deref(x)) for x in v if deref(x) is not None]
                elif v is not None:
                    v = str(v)
                out[name] = v if v is not None else ""
            if "Kids" in it:
                walk(it["Kids"], name)
    walk(fdf.get("Fields"), "")
    return out


XFDF_NS = "http://ns.adobe.com/xfdf/"


def to_xfdf(data: dict, pdf_file: str = "") -> bytes:
    ET.register_namespace("", XFDF_NS)
    root = ET.Element(f"{{{XFDF_NS}}}xfdf", {"{http://www.w3.org/XML/1998/namespace}space": "preserve"})
    if pdf_file:
        ET.SubElement(root, f"{{{XFDF_NS}}}f", {"href": pdf_file})
    fields_el = ET.SubElement(root, f"{{{XFDF_NS}}}fields")

    def add(parent, node):
        for key, child in node.items():
            if key == "\0":
                continue
            el = ET.SubElement(parent, f"{{{XFDF_NS}}}field", {"name": key})
            if "\0" in child:
                v = child["\0"]
                for item in (v if isinstance(v, list) else [v]):
                    ET.SubElement(el, f"{{{XFDF_NS}}}value").text = "" if item is None else str(item)
            add(el, child)
    add(fields_el, _tree(data))
    ET.indent(root)
    return b'<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(root, encoding="utf-8")


def parse_xfdf(raw: bytes) -> dict:
    if b"<!ENTITY" in raw:
        raise ValueError("This XFDF file uses XML entities, which Paperleaf does not read.")
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as exc:
        raise ValueError(f"This is not a valid XFDF file ({exc}).") from None

    def local(tag):
        return tag.rsplit("}", 1)[-1]
    if local(root.tag) != "xfdf":
        raise ValueError("This is not an XFDF form data file.")
    out = {}

    def walk(el, prefix):
        for child in el:
            if local(child.tag) != "field":
                continue
            name = child.get("name", "")
            full = f"{prefix}.{name}" if prefix else name
            values = [v.text or "" for v in child if local(v.tag) == "value"]
            if values:
                out[full] = values if len(values) > 1 else values[0]
            walk(child, full)
    for fields_el in (c for c in root if local(c.tag) == "fields"):
        walk(fields_el, "")
    return out


def parse_json(raw: bytes) -> dict:
    try:
        data = json.loads(raw.decode("utf-8-sig"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise ValueError(f"This is not a valid JSON file ({exc}).") from None
    if isinstance(data, dict) and isinstance(data.get("fields"), dict):
        data = data["fields"]
    if not isinstance(data, dict):
        raise ValueError("The JSON file should contain field names and values, like {\"Name\": \"Jane\"}.")
    out = {}
    for k, v in data.items():
        if isinstance(v, list):
            out[str(k)] = [str(x) for x in v]
        elif isinstance(v, bool) or v is None:
            out[str(k)] = v
        else:
            out[str(k)] = str(v)
    return out


def export_data(doc: PdfDocument, path: str):
    """Write the form's data to an .fdf, .xfdf or .json file (by extension)."""
    data = form_data(doc)
    ext = os.path.splitext(path)[1].lower()
    pdf_file = os.path.basename(doc.path)
    if ext == ".xfdf":
        raw = to_xfdf(data, pdf_file)
    elif ext == ".json":
        raw = (json.dumps(data, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
    else:
        raw = to_fdf(data, _button_names(doc), pdf_file)
    tmp = path + ".paperleaf-saving"
    with open(tmp, "wb") as fh:
        fh.write(raw)
    os.replace(tmp, path)


def read_data_file(path: str) -> dict:
    with open(path, "rb") as fh:
        raw = fh.read()
    ext = os.path.splitext(path)[1].lower()
    head = raw.lstrip()[:64]
    if ext == ".json" or head.startswith((b"{", b"\xef\xbb\xbf{")):
        return parse_json(raw)
    if ext == ".xfdf" or head.startswith(b"<"):
        return parse_xfdf(raw)
    return parse_fdf(raw)


def import_data(doc: PdfDocument, path: str) -> tuple[int, list[str]]:
    return set_values(doc, read_data_file(path), "Import form data")


# ===================================================================== Prepare Form
def field_names(doc: PdfDocument) -> set[str]:
    return {f.name for f in all_fields(doc)}


def unique_name(doc: PdfDocument, base: str, taken: set | None = None) -> str:
    taken = field_names(doc) if taken is None else taken
    n = 1
    while f"{base}{n}" in taken:
        n += 1
    return f"{base}{n}"


def name_problem(doc: PdfDocument, f: Field | None, name: str) -> str:
    """Why ``name`` can't be used for field ``f`` ("" if it can)."""
    name = name.strip()
    if not name:
        return "Enter a field name."
    if f is not None and name == f.name:
        return ""
    prefix = f.name.rsplit(".", 1)[0] + "." if f is not None and "." in f.name else ""
    if "." in name[len(prefix):] or (prefix and not name.startswith(prefix)):
        return "Field names can't contain periods." if not prefix else \
            f"Keep the “{prefix}” part of the name, and don't add periods after it."
    if name in field_names(doc):
        return f"There is already a field named “{name}”. Field names must be unique."
    return ""


def _new_widget(page: pymupdf.Page, kind: str, name: str, rect: pymupdf.Rect, **props) -> pymupdf.Annot:
    w = pymupdf.Widget()
    w.field_type = _TYPES[kind]
    w.field_name = name
    w.rect = pymupdf.Rect(rect)
    w.text_font = "Helv"
    w.text_fontsize = props.get("fontsize", 0)
    w.text_color = (0, 0, 0)
    w.field_label = props.get("tooltip") or None
    if kind in (CHECK, RADIO):
        w.border_color = (0, 0, 0)
        w.border_width = 1
        w.field_value = False
        w.text_fontsize = 0
    elif kind in (COMBO, LIST):
        opts = props.get("options") or []
        if opts:
            w.choice_values = [e if e == d else [e, d] for e, d in opts]
    elif kind == SIGNATURE:
        w.border_color = (0, 0, 0)
        w.border_width = 1
    return page.add_widget(w)


def _rename_state(doc: PdfDocument, xref: int, old: str, new: str):
    """Rename a button's "on" appearance state (AP/N, AP/D and AS)."""
    if old == new:
        return
    pat = re.compile(re.escape(pdf_name(old)) + r"(?=[\s/<>\[\]()%]|$)")
    for key in ("AP/N", "AP/D"):
        t = _xg(doc, xref, key)
        if t[0] == "dict":
            doc.doc.xref_set_key(xref, key, pat.sub(pdf_name(new), t[1]))
        elif t[0] == "xref":
            n = int(t[1].split()[0])
            _replace_object(doc, n, pat.sub(pdf_name(new), doc.doc.xref_object(n, compressed=True)))
    a = _xg(doc, xref, "AS")
    if a[0] == "name" and a[1].lstrip("/") == old:
        doc.doc.xref_set_key(xref, "AS", pdf_name(new))


def _fields_array_replace(doc: PdfDocument, remove: list[int], add: list[int]):
    cat = doc.doc.pdf_catalog()
    t = doc.doc.xref_get_key(cat, "AcroForm/Fields")
    refs = [int(m) for m in re.findall(r"(\d+)\s+0\s+R", t[1])] if t[0] == "array" else []
    refs = [r for r in refs if r not in remove] + [a for a in add if a not in refs]
    doc.doc.xref_set_key(cat, "AcroForm/Fields", "[" + " ".join(f"{r} 0 R" for r in refs) + "]")


def _kids(doc: PdfDocument, xref: int) -> list[int]:
    t = _xg(doc, xref, "Kids")
    return [int(m) for m in re.findall(r"(\d+)\s+0\s+R", t[1])] if t[0] == "array" else []


def _attach_radio(doc: PdfDocument, wxref: int, group_xref: int, choice: str):
    """Turn a freshly made radio widget into a kid of a radio group."""
    _rename_state(doc, wxref, "Yes", choice)
    for k in ("T", "FT", "Ff", "V", "DV", "TU"):
        doc.doc.xref_set_key(wxref, k, "null")
    doc.doc.xref_set_key(wxref, "Parent", f"{group_xref} 0 R")
    doc.doc.xref_set_key(wxref, "AS", "/Off")
    kids = _kids(doc, group_xref) + [wxref]
    doc.doc.xref_set_key(group_xref, "Kids", "[" + " ".join(f"{k} 0 R" for k in kids) + "]")
    _fields_array_replace(doc, [wxref], [group_xref])


def radio_groups(doc: PdfDocument) -> dict[str, list[Field]]:
    return {n: fs for n, fs in by_name(doc).items() if fs and fs[0].kind == RADIO}


def add_field(doc: PdfDocument, pno: int, kind: str, rect, name: str | None = None,
              group_name: str | None = None, **props) -> int:
    """Add a form field (Prepare Form). Radio buttons join ``group_name`` if that
    radio group exists. Returns the widget xref."""
    rect = pymupdf.Rect(rect).normalize()
    names = field_names(doc)
    label = {TEXT: "Add text field", CHECK: "Add check box", RADIO: "Add radio button", COMBO: "Add dropdown",
             LIST: "Add list box", SIGNATURE: "Add signature field"}[kind]
    with doc.edit(label, pno):
        page = doc.page(pno)
        if kind == RADIO:
            groups = radio_groups(doc)
            gname = group_name or unique_name(doc, NEW_NAMES[RADIO], names)
            existing = groups.get(gname)
            used = {f.on_state for f in existing or []}
            choice = props.get("choice") or unique_name(doc, "Choice", used)
            tmp = unique_name(doc, "__paperleaf_radio", names)
            annot = _new_widget(page, RADIO, tmp, rect)
            wx = annot.xref
            if existing and existing[0].field_xref != existing[0].xref:
                gx = existing[0].field_xref
            else:
                flags = FF_RADIO | FF_NOTOGGLEOFF
                gx = _new_object(doc, f"<</FT/Btn/Ff {flags}/T {pdf_string(gname)}/Kids[]/V/Off>>")
                if existing:  # a lone radio widget that is its own field: move it under the group
                    old = existing[0]
                    _detach_terminal_radio(doc, old.xref, gx)
            _attach_radio(doc, wx, gx, choice)
            del annot
            return wx
        nm = name or unique_name(doc, NEW_NAMES[kind], names)
        if nm in names:
            raise ValueError(f"There is already a field named “{nm}”.")
        annot = _new_widget(page, kind, nm, rect, **props)
        return annot.xref


def _detach_terminal_radio(doc: PdfDocument, wxref: int, group_xref: int):
    states = [s for s in _ap_states(doc, wxref) if s != "Off"]
    st = states[0] if states else "Yes"
    as_ = _xg(doc, wxref, "AS")
    for k in ("T", "FT", "Ff", "V", "DV", "TU"):
        doc.doc.xref_set_key(wxref, k, "null")
    doc.doc.xref_set_key(wxref, "Parent", f"{group_xref} 0 R")
    kids = _kids(doc, group_xref) + [wxref]
    doc.doc.xref_set_key(group_xref, "Kids", "[" + " ".join(f"{k} 0 R" for k in kids) + "]")
    if as_[0] == "name" and as_[1] != "/Off":
        doc.doc.xref_set_key(group_xref, "V", pdf_name(st))
    _fields_array_replace(doc, [wxref], [group_xref])


def set_rect(doc: PdfDocument, f: Field, rect, label: str = "Move field") -> None:
    rect = pymupdf.Rect(rect).normalize()
    with doc.edit(label, f.pno):
        keep: list = []
        w = _widget(doc, f, keep)
        w._annot.set_rect(rect)
        _regenerate(doc, replace(f, rect=rect), keep)


def move_fields(doc: PdfDocument, fields: list[Field], dx: float, dy: float) -> None:
    with doc.edit("Move field", _pages_of(fields)):
        keep: list = []
        for f in fields:
            w = _widget(doc, f, keep)
            r = pymupdf.Rect(f.rect) + (dx, dy, dx, dy)
            w._annot.set_rect(r)
            _regenerate(doc, replace(f, rect=r), keep)


def delete_fields(doc: PdfDocument, fields: list[Field]) -> None:
    if not fields:
        return
    label = "Delete field" if len(fields) == 1 else f"Delete {len(fields)} fields"
    with doc.edit(label, _pages_of(fields)):
        parents = set()
        for f in fields:
            if f.field_xref != f.xref:
                parents.add(f.field_xref)
            page = doc.page(f.pno)
            w = page.load_widget(f.xref)
            if w is not None:
                page.delete_widget(w)
        for px in parents:  # drop radio groups (or other parents) that lost all widgets
            kids = [k for k in _kids(doc, px) if _xg(doc, k, "Subtype")[0] != "null"]
            if not kids:
                _fields_array_replace(doc, [px], [])
                gp = _parent(doc, px)
                if gp:
                    rest = [k for k in _kids(doc, gp) if k != px]
                    doc.doc.xref_set_key(gp, "Kids", "[" + " ".join(f"{k} 0 R" for k in rest) + "]")
            else:
                doc.doc.xref_set_key(px, "Kids", "[" + " ".join(f"{k} 0 R" for k in kids) + "]")


def _set_flag(flags: int, bit: int, on) -> int:
    if on is None:
        return flags
    return flags | bit if on else flags & ~bit


def _set_da_size(da: str, size: float) -> str:
    if re.search(r"/[^\s/]+\s+[\d.]+\s+Tf", da or ""):
        return re.sub(r"(/[^\s/]+\s+)[\d.]+(\s+Tf)", lambda m: f"{m.group(1)}{_fmt(size)}{m.group(2)}", da)
    return f"0 g /Helv {_fmt(size)} Tf"


def set_properties(doc: PdfDocument, f: Field, **props) -> None:
    """Change a field's properties (Prepare Form), as one undoable step.

    Keys: name, tooltip, required, readonly, default, fontsize, options,
    multiline, maxlen, comb, editable, multiselect, export (check box / radio
    "on" value).
    """
    fields = group(doc, f)
    fx_all = sorted({g.field_xref for g in fields})
    name = props.get("name")
    if name is not None and name != f.name:
        problem = name_problem(doc, f, name)
        if problem:
            raise ValueError(problem)
    with doc.edit("Change field properties", _pages_of(fields)):
        keep: list = []
        D = doc.doc
        if name is not None and name != f.name:
            for fx in fx_all:
                D.xref_set_key(fx, "T", pdf_string(name.rsplit(".", 1)[-1] if "." in f.name else name))
        if "tooltip" in props:
            for fx in fx_all:
                D.xref_set_key(fx, "TU", pdf_string(props["tooltip"]) if props["tooltip"] else "null")
        flags = f.flags
        flags = _set_flag(flags, FF_REQUIRED, props.get("required"))
        flags = _set_flag(flags, FF_READONLY, props.get("readonly"))
        if f.kind == TEXT:
            flags = _set_flag(flags, FF_MULTILINE, props.get("multiline"))
            flags = _set_flag(flags, FF_COMB, props.get("comb"))
        if f.kind == COMBO:
            flags = _set_flag(flags, FF_EDIT, props.get("editable")) | FF_COMBO
        if f.kind == LIST:
            flags = _set_flag(flags, FF_MULTISELECT, props.get("multiselect"))
        if flags != f.flags:
            for fx in fx_all:
                D.xref_set_key(fx, "Ff", str(flags))
            for g in fields:  # widgets with their own (overriding) flags
                if g.xref != g.field_xref and _xg(doc, g.xref, "Ff")[0] != "null":
                    D.xref_set_key(g.xref, "Ff", str(flags))
        regen = False
        if f.kind == TEXT and "maxlen" in props:
            ml = int(props["maxlen"] or 0)
            for fx in fx_all:
                D.xref_set_key(fx, "MaxLen", str(ml) if ml > 0 else "null")
            if ml > 0 and len(f.value or "") > ml:
                for fx in fx_all:
                    D.xref_set_key(fx, "V", pdf_string(f.value[:ml]))
            regen = True
        if "fontsize" in props and props["fontsize"] is not None:
            for g in fields:
                da = _inherited(doc, g.xref, "DA")
                D.xref_set_key(g.xref, "DA", pdf_string(_set_da_size(str(da or ""), float(props["fontsize"]))))
            regen = True
        if f.kind in (COMBO, LIST) and "options" in props:
            opts = [(str(e), str(d)) for e, d in props["options"]]
            arr = "[" + " ".join(pdf_string(e) if e == d else f"[{pdf_string(e)} {pdf_string(d)}]"
                                 for e, d in opts) + "]"
            for fx in fx_all:
                D.xref_set_key(fx, "Opt", arr if opts else "null")
            exports = [e for e, _d in opts]
            if f.kind == LIST:
                keepv = [v for v in f.values if v in exports]
                if keepv != f.values:
                    for fx in fx_all:
                        D.xref_set_key(fx, "V", _value_text(keepv if len(keepv) != 1 else keepv[0]) if keepv else "null")
                        D.xref_set_key(fx, "I", "null")
            elif f.value and f.value not in exports and not (flags & FF_EDIT):
                for fx in fx_all:
                    D.xref_set_key(fx, "V", "null")
            regen = True
        if f.kind in (CHECK, RADIO) and props.get("export") and props["export"] != f.on_state:
            new = str(props["export"])
            if f.kind == RADIO and any(g.on_state == new for g in fields if g.xref != f.xref):
                raise ValueError(f"Another button in this group already uses “{new}”.")
            targets = [f] if f.kind == RADIO else [g for g in fields if g.on_state == f.on_state]
            for g in targets:
                _rename_state(doc, g.xref, f.on_state, new)
            for fx in fx_all:
                for key in ("V", "DV"):
                    t = _xg(doc, fx, key)
                    if t[0] == "name" and t[1].lstrip("/") == f.on_state:
                        D.xref_set_key(fx, key, pdf_name(new))
            f = _fresh(doc, f)
        if "default" in props:
            dv = props["default"]
            for fx in fx_all:
                if f.kind in (CHECK, RADIO):
                    D.xref_set_key(fx, "DV", pdf_name(dv) if dv and dv != "Off" else "null")
                elif f.kind == LIST:
                    vals = list(dv or [])
                    D.xref_set_key(fx, "DV", _value_text(vals if len(vals) != 1 else vals[0]) if vals else "null")
                else:
                    D.xref_set_key(fx, "DV", pdf_string(dv) if dv else "null")
        if regen or flags != f.flags:
            for g in fields:
                g2 = _fresh(doc, g)
                if g2.kind not in (CHECK, RADIO):
                    _regenerate(doc, g2, keep)


def _fresh(doc: PdfDocument, f: Field) -> Field:
    """Re-read a field inside an edit (caches are only cleared after it)."""
    w = doc.page(f.pno).load_widget(f.xref)
    rec = _record(doc, f.pno, w) if w is not None else None
    return rec or f
