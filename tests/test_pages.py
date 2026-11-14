"""Core tests for page operations: organize, insert, extract, split, combine, crop,
headers/footers, page numbers, Bates numbers and watermarks."""
import os
import shutil

import pymupdf
import pytest

from paperleaf.core import annotations as an
from paperleaf.core import forms as F
from paperleaf.core import pages as P
from paperleaf.core.document import PdfDocument

STYLE = an.Style(color=(1, 0, 0), width=2)


@pytest.fixture
def doc(samples, tmp_path):
    p = tmp_path / "work.pdf"
    shutil.copy(samples["main"], p)
    d = PdfDocument(str(p))
    yield d
    d.close()


def first_line(doc, pno):
    return doc.page_text(pno).text.split("\n")[0]


def titles(doc):
    return [first_line(doc, p) for p in range(doc.page_count)]


def test_ranges():
    assert P.parse_ranges("1-3, 5", 10) == [0, 1, 2, 4]
    assert P.parse_groups("1-3; 4-10", 10) == [[0, 1, 2], list(range(3, 10))]
    assert P.parse_groups("1-3; x", 10) is None
    assert P.format_ranges([0, 1, 2, 4, 6, 7]) == "1-3, 5, 7-8"
    assert P.select_pages(6, "even") == [1, 3, 5] and P.select_pages(6, "odd") == [0, 2, 4]


def test_rotate_delete_undo(doc):
    events = []
    doc.listeners.append(events.append)
    s0 = doc.structure
    P.rotate(doc, [0, 2], 90)
    assert doc.doc[0].rotation == 90 and doc.doc[2].rotation == 90
    assert doc.page_rects[0].width > doc.page_rects[0].height  # page sizes are re-read
    assert doc.structure > s0 and events[-1] is None
    assert doc.undo_name() == "Rotate pages clockwise"
    doc.undo()
    assert doc.doc[0].rotation == 0 and doc.page_rects[0].width < doc.page_rects[0].height
    P.delete(doc, [1, 3])
    assert doc.page_count == 10
    assert titles(doc)[:3] == ["Chapter 1 - Page 1", "Chapter 1 - Page 3", "Chapter 2 - Page 5"]
    with pytest.raises(ValueError):
        P.delete(doc, range(doc.page_count))
    doc.undo()
    assert doc.page_count == 12 and first_line(doc, 1) == "Chapter 1 - Page 2"
    doc.redo()
    assert doc.page_count == 10 and len(doc.page_rects) == 10


def test_move_and_reorder(doc):
    new = P.move(doc, [4, 5], 1)
    assert new == [1, 2]
    assert titles(doc)[:4] == ["Chapter 1 - Page 1", "Chapter 2 - Page 5", "Chapter 2 - Page 6",
                               "Chapter 1 - Page 2"]
    assert doc.page_rects[2].width > doc.page_rects[2].height  # the landscape page moved with its size
    # links still point at the same page
    link = doc.links(0)[0]
    assert first_line(doc, link["page"]) == "Chapter 4 - Page 10"
    doc.undo()
    P.reorder(doc, list(reversed(range(12))))
    assert first_line(doc, 0) == "Chapter 4 - Page 12"
    with pytest.raises(ValueError):
        P.reorder(doc, [0, 0, 1])


def test_duplicate_is_independent(doc):
    copies = P.duplicate(doc, [0, 2])
    assert copies == [1, 4] and doc.page_count == 14
    assert titles(doc)[:5] == ["Chapter 1 - Page 1"] * 2 + ["Chapter 1 - Page 2"] + ["Chapter 1 - Page 3"] * 2
    an.add_shape(doc, 1, "rect", [(100, 100), (200, 200)], STYLE, "T")
    assert an.page_annots(doc, 0) == [] and len(an.page_annots(doc, 1)) == 1


def test_insert_blank_and_files(doc, samples, tmp_path):
    P.insert_blank(doc, 0, (300, 400))
    assert doc.page_count == 13 and doc.page_rects[0].width == 300 and doc.page_text(0).text == ""
    # a form with fields, comments and links
    src_path = tmp_path / "src.pdf"
    src = pymupdf.open(samples["form"])
    src[0].add_text_annot((300, 60), "a note")
    src[0].insert_link({"kind": pymupdf.LINK_GOTO, "from": pymupdf.Rect(72, 40, 200, 60), "page": 1,
                        "to": pymupdf.Point(0, 0)})
    src.save(str(src_path))
    src.close()
    new = P.insert_files(doc, [str(src_path)], 2)
    assert new == [2, 3] and doc.page_count == 15
    assert first_line(doc, 2) == "Membership application"
    assert [r.kind for r in an.page_annots(doc, 2)] == ["Text"]
    assert any(lk.get("page") == 3 for lk in doc.links(2))      # internal link follows the pages
    assert first_line(doc, 1) == "Chapter 1 - Page 1"            # original pages untouched
    names = {f.name for f in F.all_fields(doc)}
    assert {"name", "agree", "colour"} <= names
    F.set_value(doc, [f for f in F.all_fields(doc) if f.name == "notes"][0], "inserted form works")
    doc.save()
    re = pymupdf.open(doc.path)
    assert re.page_count == 15 and re.is_form_pdf
    assert any(w.field_value == "inserted form works" for w in re[2].widgets())
    re.close()
    doc.undo()  # undo the fill
    doc.undo()  # undo the insert
    assert doc.page_count == 13 and not F.all_fields(doc)
    # images become pages
    img = tmp_path / "pic.png"
    pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 200, 100), 0).save(str(img))
    P.insert_files(doc, [str(img), str(img)], doc.page_count)
    assert doc.page_count == 15 and doc.undo_name() == "Insert images"
    r = doc.page_rects[-1]
    assert abs(r.width / r.height - 2) < 0.01


def test_replace_pages(doc, samples):
    src = pymupdf.open(samples["form"])
    P.replace_pages(doc, [1], src, [0])
    src.close()
    assert doc.page_count == 12 and first_line(doc, 1) == "Membership application"
    assert first_line(doc, 2) == "Chapter 1 - Page 3"


def test_extract_split_combine(doc, samples, tmp_path):
    an.add_note(doc, 3, (50, 50), "kept", STYLE, "T")  # unsaved change goes into the new files
    out = str(tmp_path / "ex.pdf")
    assert P.extract(doc, [3, 4], out) == [out]
    d = pymupdf.open(out)
    assert d.page_count == 2 and d[0].get_text().startswith("Chapter 2 - Page 4")
    assert [a.type[1] for a in d[0].annots()] == ["Text"]
    d.close()
    files = P.extract(doc, [0, 1], str(tmp_path / "each.pdf"), separate=True)
    assert [os.path.basename(f) for f in files] == ["each_1.pdf", "each_2.pdf"]
    assert doc.page_count == 12  # the open document is unchanged
    folder = tmp_path / "split"
    folder.mkdir()
    parts = P.split(doc, "pages", 5, str(folder))
    assert [os.path.basename(p) for p in parts] == ["work_Part1.pdf", "work_Part2.pdf", "work_Part3.pdf"]
    assert [pymupdf.open(p).page_count for p in parts] == [5, 5, 2]
    groups = P.split_groups(doc, "bookmarks")
    assert [g[0] for g in groups] == ["Chapter 1", "Chapter 2", "Chapter 3", "Chapter 4"]
    assert [len(g[1]) for g in groups] == [3, 3, 3, 3]
    assert [len(g[1]) for g in P.split_groups(doc, "ranges", "1-2; 3-12")] == [2, 10]
    size_groups = P.split_groups(doc, "size", 0.05)
    assert sum(len(g[1]) for g in size_groups) == 12 and len(size_groups) >= 1
    with pytest.raises(ValueError):
        P.split_groups(doc, "ranges", "nonsense")
    img = tmp_path / "pic.png"
    pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 100, 100), 0).save(str(img))
    combined = str(tmp_path / "combined.pdf")
    n = P.combine([samples["main"], str(img), (samples["encrypted"], "secret")], combined)
    assert n == 14
    c = pymupdf.open(combined)
    toc = c.get_toc()
    assert [e[1] for e in toc if e[0] == 1] == ["sample", "pic", "encrypted"]
    assert any(e[0] == 2 and e[1] == "Chapter 1" for e in toc)
    assert c[13].get_text().startswith("Top secret")
    c.close()


def test_crop_and_resize(doc):
    P.crop(doc, [0], margins=(50, 60, 70, 80))
    r = doc.page_rects[0]
    assert abs(r.width - (595 - 120)) < 0.5 and abs(r.height - (842 - 140)) < 0.5
    assert doc.undo_name() == "Crop pages"
    # rotated page: margins are as displayed
    P.crop(doc, [7], margins=(10, 20, 30, 40))
    r7 = doc.page_rects[7]
    assert abs(r7.width - (842 - 40)) < 0.5 and abs(r7.height - (595 - 60)) < 0.5
    with pytest.raises(ValueError):
        P.crop(doc, [1], margins=(400, 0, 400, 0))
    m = P.white_margins(doc, 1)
    assert 60 < m[0] < 80 and m[1] > 40  # text starts at x=72
    doc.undo()
    doc.undo()
    P.resize(doc, [1], 612, 1008)
    r = doc.page_rects[1]
    assert (round(r.width), round(r.height)) == (612, 1008)
    # content stays centred: the title moves down by half the extra height
    words = [w for w in doc.page(1).get_text("words") if w[4] == "Chapter"]
    assert abs(words[0][1] - (80 - 20 + (1008 - 842) / 2)) < 5


def test_header_footer_numbers_bates(doc):
    spec = P.HeaderFooter(header=("<<file>>", "", "Draft"), footer=("", "Page <<page>> of <<pages>>", ""))
    P.add_header_footer(doc, range(doc.page_count), spec)
    assert doc.undo_name() == "Add header & footer"
    text = doc.page_text(2).text
    assert "Page 3 of 12" in text and "work.pdf" in text and "Draft" in text
    # rotated page: the footer is at the bottom as the page is displayed
    words = doc.text_doc.load_page(7).get_text("words")
    page_word = next(w for i, w in enumerate(words) if w[4] == "Page" and words[i + 2][4] == "of")
    shown = pymupdf.Rect(page_word[:4]) * doc.rotation_matrix(7)
    assert shown.y0 > doc.page_rects[7].height - 60
    assert P.marked_pages(doc, P.HEADER_FOOTER) == list(range(12))
    assert P.remove_marks(doc, P.HEADER_FOOTER) == 12
    assert "Page 3 of 12" not in doc.page_text(2).text
    P.add_page_numbers(doc, [2, 3, 4], "bottom-right", "<<page>>", start=1)
    assert doc.page_text(3).text.rstrip().endswith("2")
    nxt = P.add_bates(doc, range(4), prefix="ABC", start=7, digits=5)
    assert nxt == 11 and "ABC00009" in doc.page_text(2).text
    P.remove_marks(doc, P.BATES)
    assert "ABC00009" not in doc.page_text(2).text
    assert P.marked_pages(doc, P.HEADER_FOOTER) == [2, 3, 4]  # page numbers stay
    doc.undo()
    assert "ABC00009" in doc.page_text(2).text


def test_watermarks(doc, tmp_path):
    P.add_watermark(doc, [0, 7], P.Watermark(text="CONFIDENTIAL"))
    assert "CONFIDENTIAL" in doc.page_text(0).text and "CONFIDENTIAL" in doc.page_text(7).text
    # it is see-through and drawn on top
    data = doc.doc.xref_stream(doc.doc[0].get_contents()[-1])
    assert b"/Artifact" in data and b"gs" in data
    assert P.remove_marks(doc, P.WATERMARK) == 2
    assert "CONFIDENTIAL" not in doc.page_text(0).text
    # image watermark, upright on a rotated page: top half red, bottom half blue
    img = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 40, 40), 0)
    for y in range(40):
        for x in range(40):
            img.set_pixel(x, y, (255, 0, 0) if y < 20 else (0, 0, 255))
    path = str(tmp_path / "wm.png")
    img.save(path)
    wm = P.Watermark(image=path, scale=0.5, style=P.TextStyle(opacity=1.0))
    P.add_watermark(doc, [7], wm)
    pix = doc.render(7, pymupdf.Matrix(0.5, 0.5))
    cx, cy = pix.width // 2, pix.height // 2
    off = pix.height // 8
    top, bottom = pix.pixel(cx, cy - off), pix.pixel(cx, cy + off)
    assert top[0] > 200 and top[2] < 80, top
    assert bottom[2] > 200 and bottom[0] < 80, bottom
    with pytest.raises(ValueError):
        P.add_watermark(doc, [0], P.Watermark())


def test_search_text_follows_page_changes(doc):
    import re
    rx = re.compile("Chapter 4 - Page 12")
    assert doc.search_page(11, rx)
    P.move(doc, [11], 0)
    assert doc.search_page(0, rx) and not doc.search_page(11, rx)
    doc.undo()
    assert doc.search_page(11, rx)


def test_encrypted_document_operations(samples, tmp_path):
    p = tmp_path / "enc.pdf"
    shutil.copy(samples["encrypted"], p)
    d = PdfDocument(str(p), "secret")
    P.insert_blank(d, 1)
    d.save()
    re = pymupdf.open(str(p))
    assert re.needs_pass and re.authenticate("secret") and re.page_count == 2
    out = str(tmp_path / "x.pdf")
    P.extract(d, [0], out)
    assert pymupdf.open(out)[0].get_text().startswith("Top secret")
