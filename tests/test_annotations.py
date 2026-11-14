import os

import pymupdf
import pytest

from paperleaf.core import annotations as an
from paperleaf.core.document import PdfDocument

STYLE = an.Style(color=(1, 0, 0), width=2, opacity=1, fontsize=14)


@pytest.fixture
def doc(samples, tmp_path):
    import shutil
    p = tmp_path / "work.pdf"
    shutil.copy(samples["main"], p)
    return PdfDocument(str(p))


def kinds(doc, pno):
    return [r.kind for r in an.page_annots(doc, pno)]


def test_create_undo_redo(doc):
    events = []
    doc.listeners.append(lambda pages: events.append(pages))
    assert not doc.modified and doc.undo_name() is None
    pt = doc.page_text(0)
    rects = pt.rects_for(0, 18)
    an.add_markup(doc, 0, "highlight", rects, STYLE, "Tester")
    assert kinds(doc, 0) == ["Highlight"] and doc.modified
    assert events[-1] == {0}
    assert an.markup_text(doc, an.page_annots(doc, 0)[0]) == "Chapter 1 - Page 1"
    an.add_shape(doc, 0, "rect", [(100, 300), (200, 400)], STYLE, "Tester")
    assert kinds(doc, 0) == ["Highlight", "Square"]
    assert doc.undo_name() == "Add rectangle"
    assert doc.undo()
    assert kinds(doc, 0) == ["Highlight"]
    assert doc.redo_name() == "Add rectangle"
    assert doc.redo()
    assert kinds(doc, 0) == ["Highlight", "Square"]
    doc.undo()
    doc.undo()
    assert kinds(doc, 0) == [] and not doc.modified


def test_rotated_page_text_still_works_while_editing(doc):
    an.add_note(doc, 7, (100, 100), "hi", STYLE, "T")
    assert doc.page_text(7).text  # text extraction on a rotated page with journalling on


def test_move_and_resize_keep_identity(doc):
    x = an.add_ink(doc, 2, [[(100, 100), (150, 130), (200, 100)]], STYLE, "T")
    an.reply(doc, 2, x, "a reply", "T")
    an.move(doc, 2, x, 50, 200)
    rec = an.find(doc, 2, x)
    assert rec is not None
    assert [tuple(round(c) for c in p) for p in rec.vertices[0]] == [(150, 300), (200, 330), (250, 300)]
    assert len(an.replies(doc, rec)) == 1
    an.resize(doc, 2, x, pymupdf.Rect(rec.rect.x0, rec.rect.y0, rec.rect.x1 + 100, rec.rect.y1 + 30))
    rec2 = an.find(doc, 2, x)
    assert rec2.rect.width > rec.rect.width + 90
    s = an.add_shape(doc, 2, "rect", [(10, 10), (50, 50)], STYLE, "T")
    an.move(doc, 2, s, 5, 5)
    assert abs(an.find(doc, 2, s).rect.x0 - 14) < 1.5  # 10 + 5 - border padding


def test_line_and_arrow(doc):
    x = an.add_shape(doc, 3, "arrow", [(100, 100), (300, 200)], STYLE, "T")
    rec = an.find(doc, 3, x)
    assert rec.kind == "Line" and rec.line_ends[1] == pymupdf.PDF_ANNOT_LE_OPEN_ARROW
    an.set_line_points(doc, 3, x, (120, 110), (320, 400))
    rec = an.find(doc, 3, x)
    assert [tuple(round(c) for c in p) for p in rec.vertices] == [(120, 110), (320, 400)]
    assert an.hit(rec, 220, 255, 3)
    assert not an.hit(rec, 120, 300, 3)


def test_freetext_symbols_stamps(doc):
    x = an.add_freetext(doc, 0, (100, 500, 300, 540), "Hello", STYLE, "T")
    rec = an.find(doc, 0, x)
    assert rec.kind == "FreeText" and rec.fontsize == 14 and rec.contents == "Hello"
    an.set_freetext(doc, 0, x, "Hello world", (100, 500, 320, 560))
    assert an.find(doc, 0, x).contents == "Hello world"
    an.set_style(doc, 0, x, color=(0, 0, 1), fontsize=20)
    rec = an.find(doc, 0, x)
    assert rec.fontsize == 20 and rec.text_color == (0, 0, 1)
    c = an.add_symbol(doc, 0, (400, 400), "check", STYLE, "T")
    assert an.find(doc, 0, c).contents == "4"
    from PySide6.QtGui import QGuiApplication
    png = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 40, 20), 0).tobytes("png")
    st = an.add_image_stamp(doc, 0, (300, 300, 380, 340), png, "T", subject="Approved")
    assert an.find(doc, 0, st).label == "Approved"


def test_style_reply_status_delete(doc):
    x = an.add_shape(doc, 4, "ellipse", [(100, 100), (200, 160)], STYLE, "T")
    an.set_style(doc, 4, x, color=(0, 1, 0), fill=(1, 1, 0), width=4, opacity=0.5, dashed=True)
    rec = an.find(doc, 4, x)
    assert rec.stroke == (0, 1, 0) and rec.fill == (1, 1, 0) and rec.width == 4 and rec.dashed
    assert abs(rec.opacity - 0.5) < 0.01
    an.reply(doc, 4, x, "looks good", "Bob")
    an.set_status(doc, 4, x, "Accepted", "Bob")
    assert [r.contents for r in an.replies(doc, rec)] == ["looks good"]
    assert an.status_of(doc, rec) == "Accepted"
    assert all(r.hidden for r in an.page_annots(doc, 4) if r.irt)
    an.delete(doc, 4, x)
    assert an.page_annots(doc, 4) == []  # replies removed with it
    doc.undo()
    assert len(an.page_annots(doc, 4)) == 3


def test_save_keeps_undo_and_marks_clean(doc, tmp_path):
    an.add_note(doc, 1, (50, 50), "note", STYLE, "T")
    assert doc.modified
    doc.save()
    assert not doc.modified
    reopened = pymupdf.open(doc.path)
    assert [a.type[1] for a in reopened[1].annots()] == ["Text"]
    reopened.close()
    assert doc.undo_name() == "Add sticky note"
    doc.undo()
    assert doc.modified
    doc.redo()
    assert not doc.modified  # back at the saved state
    an.add_note(doc, 1, (80, 80), "note 2", STYLE, "T")
    out = str(tmp_path / "copy.pdf")
    doc.save(out)
    assert doc.path == out and os.path.exists(out)


def test_encrypted_stays_encrypted(samples, tmp_path):
    import shutil
    p = tmp_path / "enc.pdf"
    shutil.copy(samples["encrypted"], p)
    d = PdfDocument(str(p), "secret")
    an.add_note(d, 0, (50, 50), "x", STYLE, "T")
    d.save()
    re = pymupdf.open(str(p))
    assert re.needs_pass and re.authenticate("secret")


def test_hit_testing(doc):
    x = an.add_shape(doc, 6, "rect", [(100, 100), (300, 300)], STYLE, "T")
    rec = an.find(doc, 6, x)
    assert an.hit(rec, 100, 200, 3)        # on the border
    assert not an.hit(rec, 200, 200, 3)    # inside an unfilled box
    an.set_style(doc, 6, x, fill=(1, 0, 0))
    assert an.hit(an.find(doc, 6, x), 200, 200, 3)
    ink = an.add_ink(doc, 6, [[(400, 400), (500, 400)]], STYLE, "T")
    assert an.annot_at(doc, 6, 450, 401, 3).xref == ink
