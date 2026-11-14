"""Core tests: password security, redaction, removing hidden information,
reducing file size and converting."""
import os
import re
import shutil

import pymupdf
import pytest

from paperleaf.core import annotations as an
from paperleaf.core import convert as C
from paperleaf.core import protect as X
from paperleaf.core.document import PasswordRequired, PdfDocument


def copy(samples, tmp_path, name="main", target="work.pdf"):
    p = tmp_path / target
    shutil.copy(samples[name], p)
    return str(p)


@pytest.fixture
def doc(samples, tmp_path):
    d = PdfDocument(copy(samples, tmp_path))
    yield d
    d.close()


# ------------------------------------------------------------------ security
def test_permissions_mapping():
    v = X.permissions_value(X.PRINT_LOW, "comments", copy=False)
    assert v & pymupdf.PDF_PERM_PRINT and not v & pymupdf.PDF_PERM_PRINT_HQ
    assert v & pymupdf.PDF_PERM_ANNOTATE and not v & pymupdf.PDF_PERM_MODIFY and not v & pymupdf.PDF_PERM_COPY
    d = X.describe_permissions(v)
    assert d == {"printing": X.PRINT_LOW, "changes": "comments", "copy": False, "accessibility": True}
    assert X.describe_permissions(X.permissions_value())["changes"] == "any"
    assert X.password_problem("", "", False)
    assert X.password_problem("a", "a", True)
    assert X.password_problem("", "owner", True) == ""


def test_add_and_remove_password(doc):
    X.protect(doc, open_pw="open123", owner_pw="own456",
              permissions=X.permissions_value(X.PRINT_HIGH, "none", copy=False))
    assert doc.modified and doc.new_security
    doc.save()
    assert not doc.modified and doc.new_security is None
    with pytest.raises(PasswordRequired):
        PdfDocument(doc.path)
    user = PdfDocument(doc.path, "open123")
    assert user.encrypted and not user.can_copy() and not user.can_modify() and user.can_print()
    assert not user.is_owner() and user.check_owner_password("own456") and not user.check_owner_password("x")
    user.close()
    # the open document was reopened with the new encryption and can be edited and saved again
    assert doc.encrypted and doc.is_owner() and "AES" in doc.encryption_name()
    an.add_note(doc, 0, (50, 50), "after protecting", an.Style(), "T")
    doc.save()
    again = PdfDocument(doc.path, "open123")
    assert [r.kind for r in an.page_annots(again, 0)] == ["Text"]
    again.close()
    # remove the security (the owner may)
    X.remove_security(doc)
    doc.save()
    plain = pymupdf.open(doc.path)
    assert not plain.needs_pass and not plain.metadata.get("encryption")
    plain.close()


def test_permissions_password_only(doc):
    X.protect(doc, owner_pw="own", permissions=X.permissions_value(X.PRINT_NONE, "forms"))
    doc.save()
    d = PdfDocument(doc.path)  # opens without a password
    assert not d.can_print() and d.can_fill_forms() and not d.can_annotate() and not d.is_owner()
    d.close()


def test_cancel_pending_security(doc):
    X.protect(doc, open_pw="x")
    assert doc.modified
    doc.cancel_security()
    assert not doc.modified


# ------------------------------------------------------------------ redaction
def test_mark_and_apply_redactions(doc):
    rx = re.compile(r"lazy dog")
    hits = X.find_matches(doc, [rx], pages=[0, 7])
    assert hits and all(h[3] == "lazy dog" for h in hits)
    n = X.mark_ranges(doc, hits, X.RedactStyle(text="REDACTED"), "Tester")
    assert n >= len(hits)
    assert "lazy dog" in doc.page_text(0).text  # marking alone removes nothing
    x = X.mark_rects(doc, 2, [(72, 100, 300, 140)])
    assert len(X.marks(doc)) == n + len(x)
    applied = X.apply(doc)
    assert applied == n + 1 and X.marks(doc) == []
    assert "lazy dog" not in doc.page_text(0).text and "lazy dog" not in doc.page_text(7).text
    assert "lazy dog" in doc.page_text(1).text  # other pages untouched
    assert doc.undo_name() == "Apply redactions"
    doc.save()
    re_doc = pymupdf.open(doc.path)
    assert "lazy dog" not in re_doc[0].get_text()
    # the old text survives only in the content of pages that were not redacted:
    # not on the redacted pages, and not in leftover (unreferenced) objects
    old_hex = "".join(f"{ord(c):02x}" for c in "lazy dog").encode()
    other_pages = {x for p in range(re_doc.page_count) if p not in (0, 7) for x in re_doc[p].get_contents()}
    for x in range(1, re_doc.xref_length()):
        data = (re_doc.xref_stream(x) or b"") if re_doc.xref_is_stream(x) else b""
        if b"lazy dog" in data or old_hex in data.lower():
            assert x in other_pages, x
    pages_left = re_doc[1].get_text()
    re_doc.close()
    assert "lazy dog" in pages_left
    doc.undo()
    assert "lazy dog" in doc.page_text(0).text and len(X.marks(doc)) == n + 1


def test_redaction_patterns(doc):
    from paperleaf.core import pages as P
    P.add_header_footer(doc, [0], P.HeaderFooter(footer=("jane.doe@example.com  +1 555-123-4567",
                                                         "4111 1111 1111 1111", "078-05-1120")))
    found = {name: [h[3] for h in X.find_matches(doc, [re.compile(rx)], pages=[0])]
             for name, rx in X.PATTERNS.items()}
    assert found["Email addresses"] == ["jane.doe@example.com"]
    assert any("555-123-4567" in t for t in found["Phone numbers"])
    assert [t.strip() for t in found["Credit card numbers"]] == ["4111 1111 1111 1111"]
    assert found["US Social Security numbers"] == ["078-05-1120"]


def test_redact_area_removes_image_parts(samples, tmp_path):
    d = PdfDocument(copy(samples, tmp_path, "hidden"))
    img_rect = d.page(0).get_image_rects(d.page(0).get_images()[0][0])[0]
    X.mark_rects(d, 0, [img_rect])
    X.apply(d)
    pix = d.render(0, pymupdf.Matrix(1, 1))
    c = pix.pixel(int(img_rect.x0 + 90), int(img_rect.y0 + 90))
    assert c == (0, 0, 0)  # the black redaction box, the image pixels below it are gone
    d.close()


# ------------------------------------------------------------------ hidden information
def test_inspect_and_sanitize(samples, tmp_path):
    d = PdfDocument(copy(samples, tmp_path, "hidden"))
    found = X.inspect(d).counts
    for key in ("metadata", "attachments", "comments", "javascript", "links", "bookmarks", "layers",
                "hidden_text", "thumbnails"):
        assert found[key] > 0, key
    removed = X.sanitize(d, [k for k, _l, _on in X.SANITIZE_ITEMS])
    assert set(removed) >= {"metadata", "attachments", "javascript", "layers", "thumbnails"}
    after = X.inspect(d).counts
    assert all(v == 0 for v in after.values()), after
    assert "INVISIBLE TEXT" not in d.page_text(0).text and "Visible text" in d.page_text(0).text
    d.save()
    raw = open(d.path, "rb").read()
    assert b"SECRET LAYER" not in pymupdf.open(d.path)[0].read_contents()
    assert b"app.alert" not in raw and b"attached secret" not in raw
    re_doc = pymupdf.open(d.path)
    assert re_doc.embfile_count() == 0 and not re_doc.get_toc() and not re_doc.metadata.get("title")
    re_doc.close()
    d.undo()
    assert X.inspect(d).counts["attachments"] == 1
    d.close()


def test_sanitize_only_chosen_items(samples, tmp_path):
    d = PdfDocument(copy(samples, tmp_path, "hidden"))
    X.sanitize(d, ["metadata"])
    c = X.inspect(d).counts
    assert c["metadata"] == 0 and c["attachments"] == 1 and c["comments"] == 1
    assert X.sanitize(d, ["metadata"]) == {}  # nothing left of that kind: no step
    d.close()


# ------------------------------------------------------------------ size and conversion
def test_compress(samples, tmp_path):
    d = PdfDocument(copy(samples, tmp_path, "hidden"))
    out = str(tmp_path / "small.pdf")
    before, after = C.compress(d, out, *C.PRESETS["small"])
    assert os.path.getsize(out) == after and after < before * 0.5
    r = pymupdf.open(out)
    assert r.page_count == 2 and "Visible text" in r[0].get_text()
    info = r.extract_image(r[0].get_images()[0][0])
    assert info["width"] < 900
    r.close()
    assert not d.modified  # the open document is unchanged
    d.close()


def test_compress_keeps_encryption(samples, tmp_path):
    d = PdfDocument(copy(samples, tmp_path, "encrypted"), "secret")
    out = str(tmp_path / "small.pdf")
    C.compress(d, out)
    r = pymupdf.open(out)
    assert r.needs_pass and r.authenticate("secret")


def test_export_images_text_html(doc, tmp_path):
    files = C.to_images(doc, str(tmp_path), "pg", "png", 50, pages=[0, 5])
    assert [os.path.basename(f) for f in files] == ["pg_01.png", "pg_06.png"]
    assert pymupdf.Pixmap(files[1]).width > pymupdf.Pixmap(files[1]).height  # landscape page
    jpg = C.to_images(doc, str(tmp_path), "j", "jpg", 40, pages=[1])[0]
    assert open(jpg, "rb").read(3) == b"\xff\xd8\xff"
    tif = C.to_images(doc, str(tmp_path), "t", "tif", 40, pages=[2])[0]
    assert open(tif, "rb").read(2) in (b"II", b"MM")
    txt = C.to_text(doc, str(tmp_path / "a.txt"))
    text = open(txt, encoding="utf-8").read()
    assert text.count("Chapter") >= 12 and "\f" in text
    page_html = C.to_html(doc, str(tmp_path / "a.html"), pages=[0, 1])
    h = open(page_html, encoding="utf-8").read()
    assert h.startswith("<!DOCTYPE html>") and "Chapter 1 - Page 2" in h and h.count('class="page"') == 2


@pytest.mark.skipif(not C.word_available(), reason="pdf2docx is not installed")
def test_export_word(doc, tmp_path):
    out = C.to_word(doc, str(tmp_path / "a.docx"), pages=[0, 1])
    import zipfile
    with zipfile.ZipFile(out) as z:
        xml = z.read("word/document.xml").decode("utf-8")
    assert "Chapter 1 - Page 1" in xml.replace("</w:t></w:r><w:r><w:t>", "") or "Chapter" in xml


def test_images_to_pdf(tmp_path):
    paths = []
    for i, (w, h) in enumerate(((200, 100), (100, 300))):
        p = str(tmp_path / f"i{i}.png")
        pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, w, h), 0).save(p)
        paths.append(p)
    out = str(tmp_path / "imgs.pdf")
    assert C.images_to_pdf(paths, out) == 2
    d = pymupdf.open(out)
    assert d[0].rect.width > d[0].rect.height and d[1].rect.height > d[1].rect.width
    assert not d.get_toc()


def test_office_detection():
    assert C.office_kind("x.DOCX") == "word" and C.office_kind("x.pptx") == "powerpoint"
    assert C.office_kind("x.pdf") is None
    if os.name != "nt":
        assert not C.office_available("word")
        with pytest.raises(RuntimeError):
            C.office_to_pdf("x.docx", "x.pdf")
