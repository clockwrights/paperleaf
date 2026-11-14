"""Core form tests: reading, filling, undo, clearing, flattening, data files, Prepare Form."""
import json
import shutil

import pymupdf
import pytest

from paperleaf.core import forms as F
from paperleaf.core.document import PdfDocument


@pytest.fixture
def doc(samples, tmp_path):
    p = tmp_path / "form.pdf"
    shutil.copy(samples["form"], p)
    d = PdfDocument(str(p))
    yield d
    d.close()


def fld(doc, name, i=0):
    return [f for f in F.all_fields(doc) if f.name == name][i]


def reopen_values(path):
    d = pymupdf.open(path)
    out = {}
    for page in d:
        for w in page.widgets():
            out.setdefault(w.field_name, w.field_value)
    d.close()
    return out


def test_reading_fields_and_info(doc):
    info = F.form_info(doc)
    assert info.any and info.count == 18 and not info.xfa
    assert info.calculated and info.javascript
    names = [f.name for f in F.page_fields(doc, 0)]
    assert names[:5] == ["name", "notes", "zip", "member_id", "agree"]
    assert fld(doc, "name").required and fld(doc, "name").tooltip == "Your full name"
    assert fld(doc, "notes").multiline
    z = fld(doc, "zip")
    assert z.comb and z.maxlen == 5
    assert fld(doc, "member_id").readonly and fld(doc, "member_id").value == "A-123"
    assert fld(doc, "city").editable and fld(doc, "langs").multiselect
    radios = [f for f in F.all_fields(doc) if f.name == "colour"]
    assert [r.on_state for r in radios] == ["Red", "Green", "Blue"]
    assert [r.is_on for r in radios] == [False, True, False]
    assert fld(doc, "total").calculated
    assert fld(doc, "reset").action == {"type": "ResetForm"}
    assert len(F.group(doc, fld(doc, "name"))) == 2  # also on page 2
    hit = F.field_at(doc, 0, 200, 110)
    assert hit is not None and hit.name == "name"
    assert F.can_fill(doc) and F.can_prepare(doc) and F.can_flatten(doc)


def test_fill_text_undo_redo_and_save(doc):
    events = []
    doc.listeners.append(events.append)
    assert F.set_value(doc, fld(doc, "name"), "Jane Doe")
    assert events[-1] == {0, 1}  # both widgets of "name" re-render
    assert doc.undo_name() == "Fill in field"
    assert fld(doc, "name").value == "Jane Doe" and fld(doc, "name", 1).value == "Jane Doe"
    assert not F.set_value(doc, fld(doc, "name"), "Jane Doe")  # no change, no step
    F.set_value(doc, fld(doc, "zip"), "2801399")
    assert fld(doc, "zip").value == "28013"  # max length
    F.set_value(doc, fld(doc, "notes"), "line 1\nline 2")
    assert fld(doc, "notes").value == "line 1\nline 2"
    doc.undo()
    doc.undo()
    assert fld(doc, "zip").value == "" and fld(doc, "name").value == "Jane Doe"
    doc.redo()
    assert fld(doc, "zip").value == "28013"
    doc.save()
    vals = reopen_values(doc.path)
    assert vals["name"] == "Jane Doe" and vals["zip"] == "28013"
    # the appearance shows the value too (another viewer would see it)
    d = pymupdf.open(doc.path)
    d.bake()
    assert "Jane Doe" in d[0].get_text()
    d.close()


def test_checkbox_and_radio(doc):
    agree = fld(doc, "agree")
    assert F.toggle(doc, agree)
    assert fld(doc, "agree").is_on and fld(doc, "agree").value == "Yes"
    assert F.toggle(doc, fld(doc, "agree"))
    assert not fld(doc, "agree").is_on
    blue = fld(doc, "colour", 2)
    assert F.toggle(doc, blue)
    assert [r.is_on for r in F.group(doc, blue)] == [False, False, True]
    assert fld(doc, "colour").value == "Blue"
    # NoToggleToOff: clicking the selected button again changes nothing
    assert not F.toggle(doc, fld(doc, "colour", 2))
    doc.undo()
    assert [r.is_on for r in F.group(doc, blue)] == [False, True, False]
    doc.redo()
    doc.save()
    d = pymupdf.open(doc.path)
    assert d.xref_get_key(fld(doc, "colour").field_xref, "V") == ("name", "/Blue")
    d.close()


def test_choices(doc):
    F.set_value(doc, fld(doc, "country"), "Italy")
    assert fld(doc, "country").value == "Italy"
    F.set_value(doc, fld(doc, "city"), "Barcelona")  # editable combo accepts any text
    assert fld(doc, "city").value == "Barcelona"
    F.set_value(doc, fld(doc, "langs"), ["German", "Spanish"])
    lg = fld(doc, "langs")
    assert lg.value == ["Spanish", "German"]  # option order
    assert doc.doc.xref_get_key(lg.field_xref, "I")[1].replace(" ", "") == "[13]"
    # the list appearance highlights the selected rows
    ap = doc.doc.xref_get_key(lg.xref, "AP/N")
    stream = doc.doc.xref_stream(int(ap[1].split()[0]))
    assert b"0.6 0.75 0.86 rg" in stream and stream.count(b" re f") == 2
    F.set_value(doc, lg, [])
    assert fld(doc, "langs").value == []


def test_clear_form(doc):
    F.set_value(doc, fld(doc, "name"), "Jane")
    F.set_value(doc, fld(doc, "country"), "France")
    F.toggle(doc, fld(doc, "colour", 0))
    F.toggle(doc, fld(doc, "agree"))
    assert F.reset_form(doc)
    assert doc.undo_name() == "Clear form"
    assert fld(doc, "name").value == ""
    assert fld(doc, "country").value == "Spain"         # back to its default
    assert fld(doc, "colour", 1).is_on                   # default "Green"
    assert not fld(doc, "agree").is_on
    assert fld(doc, "member_id").value == "A-123"        # read-only fields are kept
    assert not F.reset_form(doc)                         # nothing left to clear
    doc.undo()
    assert fld(doc, "name").value == "Jane"


def test_permissions(samples, tmp_path):
    p = tmp_path / "locked.pdf"
    d = pymupdf.open(samples["form"])
    d.save(str(p), encryption=pymupdf.PDF_ENCRYPT_AES_256, owner_pw="owner", user_pw="",
           permissions=pymupdf.PDF_PERM_PRINT)
    d.close()
    doc = PdfDocument(str(p), "")
    assert not F.can_fill(doc) and not F.can_prepare(doc)
    p2 = tmp_path / "fill_only.pdf"
    d = pymupdf.open(samples["form"])
    d.save(str(p2), encryption=pymupdf.PDF_ENCRYPT_AES_256, owner_pw="owner", user_pw="",
           permissions=pymupdf.PDF_PERM_PRINT | pymupdf.PDF_PERM_FORM)
    d.close()
    doc2 = PdfDocument(str(p2), "")
    assert F.can_fill(doc2) and not F.can_prepare(doc2) and not F.can_flatten(doc2)
    F.set_value(doc2, fld(doc2, "name"), "Encrypted Jane")
    doc2.save()
    re = pymupdf.open(str(p2))
    assert re.needs_pass or re.is_encrypted or re.metadata.get("encryption")
    re.close()
    assert reopen_values(str(p2))["name"] == "Encrypted Jane"


def test_xfa_is_detected_and_dropped_on_fill(samples, tmp_path):
    p = tmp_path / "xfa.pdf"
    shutil.copy(samples["xfa"], p)
    doc = PdfDocument(str(p))
    info = F.form_info(doc)
    assert info.xfa and not info.xfa_only
    F.set_value(doc, fld(doc, "field1"), "hello")
    assert not F.form_info(doc).xfa
    doc.undo()
    assert F.form_info(doc).xfa


def test_flatten_and_undo(doc):
    F.set_value(doc, fld(doc, "name"), "Flat Jane")
    F.toggle(doc, fld(doc, "agree"))
    assert "Flat Jane" not in doc.page_text(0).text
    F.flatten(doc)
    assert doc.undo_name() == "Flatten form"
    assert F.all_fields(doc) == [] and not F.form_info(doc).any
    assert "Flat Jane" in doc.page_text(0).text  # search and selection see the new content
    doc.undo()
    assert len(F.all_fields(doc)) == 18
    assert "Flat Jane" not in doc.page_text(0).text
    doc.redo()
    assert F.all_fields(doc) == []
    doc.save()
    d = pymupdf.open(doc.path)
    assert not d.is_form_pdf and "Flat Jane" in d[0].get_text()
    d.close()


def test_flatten_with_comments(doc):
    from paperleaf.core import annotations as an
    an.add_note(doc, 0, (500, 100), "hi", an.Style(), "T")
    F.flatten(doc, comments=True)
    assert an.page_annots(doc, 0) == [] and F.all_fields(doc) == []
    doc.undo()
    assert [r.kind for r in an.page_annots(doc, 0)] == ["Text"]


@pytest.mark.parametrize("ext", [".fdf", ".xfdf", ".json"])
def test_export_import_roundtrip(doc, tmp_path, ext):
    F.set_value(doc, fld(doc, "name"), "Zoë (O'Brien) \\ test")
    F.set_value(doc, fld(doc, "notes"), "two\nlines")
    F.toggle(doc, fld(doc, "agree"))
    F.toggle(doc, fld(doc, "colour", 2))
    F.set_value(doc, fld(doc, "langs"), ["English", "French"])
    F.set_value(doc, fld(doc, "city"), "Lyon")
    out = str(tmp_path / f"data{ext}")
    F.export_data(doc, out)
    data = F.read_data_file(out)
    assert data["name"] == "Zoë (O'Brien) \\ test"
    assert data["agree"] == "Yes" and data["colour"] == "Blue"
    assert data["langs"] == ["English", "French"]
    assert "reset" not in data and "signature" not in data
    F.reset_form(doc)
    F.set_value(doc, fld(doc, "member_id"), "changed")
    n, unknown = F.import_data(doc, out)
    assert n >= 6 and unknown == []
    assert doc.undo_name() == "Import form data"
    assert fld(doc, "name").value == "Zoë (O'Brien) \\ test"
    assert fld(doc, "notes").value == "two\nlines"
    assert fld(doc, "agree").is_on and fld(doc, "colour", 2).is_on
    assert fld(doc, "langs").value == ["English", "French"]
    assert fld(doc, "member_id").value == "A-123"


def test_import_formats_from_other_apps(doc, tmp_path):
    fdf = (b"%FDF-1.2\n1 0 obj\n<< /FDF << /Fields 2 0 R >> >>\nendobj\n"
           b"2 0 obj\n[ << /T (name) /V <FEFF004A0061006E0065> >> << /T (agree) /V /Yes >>\n"
           b"<< /T (langs) /V [(German)] >> << /T (nosuch) /V (x) >> ]\nendobj\n"
           b"trailer\n<< /Root 1 0 R >>\n%%EOF\n")
    p = tmp_path / "acrobat.fdf"
    p.write_bytes(fdf)
    n, unknown = F.import_data(doc, str(p))
    assert n == 3 and unknown == ["nosuch"]
    assert fld(doc, "name").value == "Jane" and fld(doc, "agree").is_on
    assert fld(doc, "langs").value == ["German"]
    xfdf = (b'<?xml version="1.0" encoding="UTF-8"?><xfdf xmlns="http://ns.adobe.com/xfdf/" '
            b'xml:space="preserve"><fields><field name="country"><value>France</value></field>'
            b'<field name="colour"><value>Red</value></field></fields></xfdf>')
    p2 = tmp_path / "data.xfdf"
    p2.write_bytes(xfdf)
    F.import_data(doc, str(p2))
    assert fld(doc, "country").value == "France" and fld(doc, "colour", 0).is_on
    p3 = tmp_path / "data.json"
    p3.write_text(json.dumps({"fields": {"agree": False, "zip": 12345}}))
    F.import_data(doc, str(p3))
    assert not fld(doc, "agree").is_on and fld(doc, "zip").value == "12345"
    bad = tmp_path / "bad.json"
    bad.write_text("[1, 2]")
    with pytest.raises(ValueError):
        F.read_data_file(str(bad))


def test_hierarchical_names_in_fdf():
    raw = F.to_fdf({"person.first": "A", "person.last": "B", "ok": "Yes"}, {"ok"})
    assert F.parse_fdf(raw) == {"person.first": "A", "person.last": "B", "ok": "Yes"}
    assert b"/Kids" in raw and b"/V /Yes" in raw
    x = F.to_xfdf({"person.first": "A", "multi": ["1", "2"]})
    assert F.parse_xfdf(x) == {"person.first": "A", "multi": ["1", "2"]}


def test_sign_field(doc):
    from paperleaf.core import annotations as an
    png = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 80, 20), 0).tobytes("png")
    sig = fld(doc, "signature")
    x = F.sign_field(doc, sig, png, 160, 40, "Jane")
    rec = an.find(doc, 0, x)
    assert rec.kind == "Stamp" and rec.subject == "Signature"
    assert sig.rect.contains(rec.rect) or abs(rec.rect.width - sig.rect.width) < 1
    assert doc.undo_name() == "Sign"
    doc.undo()
    assert an.page_annots(doc, 0) == []


def test_prepare_add_move_resize_delete(doc):
    names = F.field_names(doc)
    x = F.add_field(doc, 0, F.TEXT, (300, 600, 450, 622))
    f = F.find(doc, 0, x)
    assert f.kind == F.TEXT and f.name == "Text1" and f.name not in names
    assert doc.undo_name() == "Add text field"
    F.set_value(doc, f, "typed")
    F.move_fields(doc, [F.find(doc, 0, x)], 10, 20)
    f = F.find(doc, 0, x)
    assert abs(f.rect.x0 - 310) < 0.5 and abs(f.rect.y0 - 620) < 0.5 and f.value == "typed"
    F.set_rect(doc, f, (310, 620, 500, 660), "Resize field")
    assert abs(F.find(doc, 0, x).rect.x1 - 500) < 0.5
    x2 = F.add_field(doc, 0, F.TEXT, (300, 700, 450, 722))
    assert F.find(doc, 0, x2).name == "Text2"
    with pytest.raises(ValueError):
        F.add_field(doc, 0, F.TEXT, (300, 730, 450, 750), name="name")
    F.delete_fields(doc, [F.find(doc, 0, x)])
    assert F.find(doc, 0, x) is None
    doc.undo()
    assert F.find(doc, 0, x) is not None
    for kind in (F.CHECK, F.COMBO, F.LIST, F.SIGNATURE):
        xx = F.add_field(doc, 1, kind, (100, 300, 200, 330))
        assert F.find(doc, 1, xx).kind == kind
    doc.save()
    d = pymupdf.open(doc.path)
    kinds = sorted(w.field_type_string for w in d[1].widgets())
    assert kinds == ["CheckBox", "ComboBox", "ListBox", "Signature", "Text", "Text"]
    d.close()


def test_prepare_radio_groups(doc):
    a = F.add_field(doc, 0, F.RADIO, (450, 600, 464, 614))
    g = F.find(doc, 0, a).name
    b = F.add_field(doc, 0, F.RADIO, (480, 600, 494, 614), group_name=g)
    fa, fb = F.find(doc, 0, a), F.find(doc, 0, b)
    assert fa.name == fb.name == g and fa.field_xref == fb.field_xref != fa.xref
    assert {fa.on_state, fb.on_state} == {"Choice1", "Choice2"}
    F.toggle(doc, fb)
    assert F.find(doc, 0, b).is_on and not F.find(doc, 0, a).is_on
    F.toggle(doc, F.find(doc, 0, a))
    assert F.find(doc, 0, a).is_on and not F.find(doc, 0, b).is_on
    F.delete_fields(doc, [F.find(doc, 0, a), F.find(doc, 0, b)])
    assert g not in F.field_names(doc)
    cat = doc.doc.pdf_catalog()
    assert f"{fa.field_xref} 0 R" not in doc.doc.xref_get_key(cat, "AcroForm/Fields")[1]


def test_properties(doc):
    f = fld(doc, "notes")
    F.set_properties(doc, f, name="remarks", tooltip="Anything else?", required=True, readonly=False,
                     default="none", fontsize=14, multiline=True)
    g = fld(doc, "remarks")
    assert g.tooltip == "Anything else?" and g.required and g.fontsize == 14 and g.default == "none"
    assert doc.undo_name() == "Change field properties"
    with pytest.raises(ValueError):
        F.set_properties(doc, g, name="name")  # names must be unique
    assert F.name_problem(doc, g, "a.b")
    F.set_properties(doc, fld(doc, "zip"), maxlen=3)
    assert fld(doc, "zip").maxlen == 3
    F.set_properties(doc, fld(doc, "country"), options=[("ES", "Spain"), ("FR", "France")], editable=True)
    c = fld(doc, "country")
    assert c.options == [("ES", "Spain"), ("FR", "France")] and c.editable
    F.set_value(doc, c, "France")
    assert fld(doc, "country").value == "FR"
    F.set_properties(doc, fld(doc, "agree"), export="On")
    a = fld(doc, "agree")
    assert a.on_state == "On"
    F.toggle(doc, a)
    assert fld(doc, "agree").value == "On"
    F.set_values(doc, {"agree": "Off"})
    F.set_values(doc, {"agree": "Yes"})  # data files often say "Yes"
    assert fld(doc, "agree").is_on and fld(doc, "agree").value == "On"
    F.set_properties(doc, fld(doc, "colour", 0), export="Crimson")
    assert [r.on_state for r in F.group(doc, fld(doc, "colour"))] == ["Crimson", "Green", "Blue"]
    with pytest.raises(ValueError):
        F.set_properties(doc, fld(doc, "colour", 0), export="Blue")
    doc.undo()  # undo the failed step is a no-op; this undoes "Crimson"
    assert fld(doc, "colour", 0).on_state == "Red"


def test_pdf_parser():
    v = F.parse_pdf_value("[(a\\(b\\)) <FEFF00E9> /N#20x 12 3.5 true null [1 2] <</K (v)>>]")
    assert v == ["a(b)", "é", "N x", 12, 3.5, True, None, [1, 2], {"K": "v"}]
    assert isinstance(v[2], F.Name)
    assert F.pdf_name("A b/c") == "/A#20b#2Fc"
