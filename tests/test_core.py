import pymupdf
import pytest

from paperleaf.core.document import PasswordRequired, PdfDocument, make_search_regex, parse_pdf_date


def test_open_and_basic_info(samples):
    d = PdfDocument(samples["main"])
    assert d.page_count == 12
    assert d.page_rects[5].width > d.page_rects[5].height  # landscape
    assert d.page_rects[7].width > d.page_rects[7].height  # rotated portrait
    assert d.metadata()["title"] == "Paperleaf Sample"
    assert d.can_print() and d.can_copy()


def test_file_not_locked(samples, tmp_path):
    import shutil, os
    p = tmp_path / "copy.pdf"
    shutil.copy(samples["main"], p)
    d = PdfDocument(str(p))
    os.remove(p)  # must succeed while open
    assert d.page_count == 12


def test_password(samples):
    with pytest.raises(PasswordRequired) as e:
        PdfDocument(samples["encrypted"])
    assert not e.value.wrong
    with pytest.raises(PasswordRequired) as e:
        PdfDocument(samples["encrypted"], "nope")
    assert e.value.wrong
    d = PdfDocument(samples["encrypted"], "secret")
    assert "Top secret" in d.plain_text(0)


def test_text_selection_helpers(samples):
    d = PdfDocument(samples["main"])
    pt = d.page_text(0)
    assert pt.text.startswith("Chapter 1 - Page 1")
    # caret at the start of the first line
    x0, y0, x1, y1 = pt.lines[0]
    assert pt.index_at(x0 - 5, (y0 + y1) / 2) == 0
    a, b = pt.word_at(2)
    assert pt.text[a:b] == "Chapter"
    a, b = pt.line_range(3)
    assert pt.text[a:b].strip() == "Chapter 1 - Page 1"
    rects = pt.rects_for(0, 7)
    assert len(rects) == 1 and rects[0][2] > rects[0][0]


def test_search(samples):
    d = PdfDocument(samples["main"])
    rx = make_search_regex("quick   brown", False, False)
    hits = d.search_page(0, rx)
    assert len(hits) >= 4
    assert d.plain_text(0)[hits[0][0]:hits[0][1]].lower().startswith("quick")
    case = make_search_regex("QUICK", True, False)
    assert d.search_page(0, case) == []
    whole = make_search_regex("fox", False, True)
    loose = make_search_regex("fox", False, False)
    assert len(d.search_page(0, loose)) > len(d.search_page(0, whole)) > 0
    lig = make_search_regex("office", False, False)
    assert d.search_page(0, lig)


def test_rotated_page_text_maps_into_page(samples):
    d = PdfDocument(samples["main"])
    pt = d.page_text(7)
    r = pymupdf.Rect(pt.lines[0]) * d.rotation_matrix(7)
    assert d.page_rects[7].contains(r.tl) and d.page_rects[7].contains(r.br)


def test_links_outline_labels_attachments(samples):
    d = PdfDocument(samples["main"])
    links = d.links(0)
    assert links and links[0]["kind"] == pymupdf.LINK_GOTO and links[0]["page"] == 9
    assert any(l.get("uri") == "https://example.com/" for l in d.links(1))
    toc = d.toc()
    assert toc[0][1] == "Chapter 1" and len(toc) == 8
    assert d.page_label(0) == "i" and d.page_label(2) == "1"
    assert d.find_label("ii") == 1 and d.find_label("3") == 4
    atts = d.attachments()
    assert atts[0]["name"] == "notes.txt"
    assert d.attachment_bytes("notes.txt").startswith(b"Attachment")


def test_render_tiles_match_full(samples):
    d = PdfDocument(samples["main"])
    m = pymupdf.Matrix(1.5, 1.5).prerotate(90)
    full = d.render(3, m)
    irect = (d.page_rects[3] * m).irect
    tile_rect = pymupdf.IRect(irect.x0 + 256, irect.y0 + 256, irect.x0 + 512, irect.y0 + 512)
    tile = d.render(3, m, pymupdf.Rect(tile_rect) * ~m)
    assert (tile.x, tile.y) == (tile_rect.x0, tile_rect.y0)
    assert tile.pixel(10, 10) == full.pixel(tile.x - full.x + 10, tile.y - full.y + 10)


def test_dates():
    assert parse_pdf_date("D:20260101120000Z") == "2026-01-01 12:00:00"
    assert parse_pdf_date("") == ""
