"""Advanced tools: Edit PDF, OCR, Compare Files, certificate signatures, Read Out Loud."""
import os
import shutil

import pymupdf
import pytest

from paperleaf.core import compare as C
from paperleaf.core import edit as E
from paperleaf.core import ocr
from paperleaf.core import pages as P
from paperleaf.core import signing as S
from paperleaf.core.document import PdfDocument
from tests.samples import SCAN_LINES


def _open(samples, tmp_path, key="main", name=None):
    path = tmp_path / (name or os.path.basename(samples[key]))
    shutil.copy(samples[key], path)
    return PdfDocument(str(path))


@pytest.fixture
def ids(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    return tmp_path


# =================================================================== edit text
def test_text_lines_and_hit_test(samples, tmp_path):
    d = _open(samples, tmp_path)
    lines = E.text_lines(d, 0)
    assert lines[0].text == "Chapter 1 - Page 1" and lines[0].size == pytest.approx(20)
    assert lines[0].editable and not lines[0].italic
    r = lines[0].rect
    assert E.line_at(d, 0, r.x0 + 5, (r.y0 + r.y1) / 2) is lines[0]
    assert E.line_at(d, 0, 5, 5) is None


def test_invisible_text_is_not_offered_for_editing(samples, tmp_path):
    d = _open(samples, tmp_path, "hidden")
    texts = [ln.text for ln in E.text_lines(d, 0)]
    assert "Visible text on page one" in texts and "INVISIBLE TEXT" not in texts


def test_replace_line_keeps_place_and_undoes(samples, tmp_path):
    d = _open(samples, tmp_path)
    first = E.text_lines(d, 0)[0]
    assert E.replace_line(d, first, "Chapter One - First Page")
    text = d.page_text(0).text
    assert "Chapter One - First Page" in text and "Chapter 1 - Page 1" not in text
    assert d.undo_name() == "Edit text"
    new = [ln for ln in E.text_lines(d, 0) if ln.text == "Chapter One - First Page"][0]
    assert abs(new.origin[1] - first.origin[1]) < 1 and abs(new.origin[0] - first.origin[0]) < 1
    assert new.size == pytest.approx(first.size, abs=0.5)
    # the rest of the page is untouched
    assert "The quick brown fox" in text
    assert not E.replace_line(d, new, new.text)   # no change: no step
    d.undo()
    assert "Chapter 1 - Page 1" in d.page_text(0).text
    assert E.text_lines(d, 0)[0].text == "Chapter 1 - Page 1"


def test_delete_line_and_rotated_page(samples, tmp_path):
    d = _open(samples, tmp_path)
    # page 8 is rotated in the sample: text coordinates stay unrotated
    assert d.doc[7].rotation != 0
    ln = E.text_lines(d, 7)[0]
    E.replace_line(d, ln, "Changed title")
    assert "Changed title" in d.page_text(7).text
    ln = [x for x in E.text_lines(d, 7) if x.text == "Changed title"][0]
    E.replace_line(d, ln, "")
    assert d.undo_name() == "Delete text"
    assert "Changed title" not in d.page_text(7).text


def test_replace_line_with_characters_the_font_lacks(samples, tmp_path):
    d = _open(samples, tmp_path)
    ln = E.text_lines(d, 0)[0]
    E.replace_line(d, ln, "Ünïcødé – €100")
    assert "Ünïcødé – €100" in d.page_text(0).text


def test_add_text_lines(samples, tmp_path):
    d = _open(samples, tmp_path)
    E.add_text(d, 1, (100, 700), "First new line\nSecond new line", size=14, color=(1, 0, 0))
    text = d.page_text(1).text
    assert "First new line" in text and "Second new line" in text
    added = [ln for ln in E.text_lines(d, 1) if ln.text == "Second new line"][0]
    assert added.origin[1] == pytest.approx(700 + 14 * 1.2, abs=1)
    assert added.color[0] > 0.9 and added.color[1] < 0.1
    E.add_text(d, 1, (100, 700), "   ")   # nothing to add
    assert d.undo_name() == "Add text"


def test_save_after_text_edit(samples, tmp_path):
    d = _open(samples, tmp_path)
    E.replace_line(d, E.text_lines(d, 0)[0], "Saved heading")
    d.save()
    again = pymupdf.open(d.path)
    assert "Saved heading" in again[0].get_text()
    assert "Chapter 1 - Page 1" not in again[0].get_text()


# ================================================================== edit images
def test_move_resize_replace_delete_image(samples, tmp_path):
    d = _open(samples, tmp_path, "hidden")
    img = E.images(d, 0)[0]
    assert img.rect == pymupdf.Rect(72, 200, 252, 380) and img.angle == 0
    assert E.image_at(d, 0, 100, 250) == img and E.image_at(d, 0, 10, 10) is None
    E.move_image(d, img, img.rect + (200, 100, 200, 100))
    moved = E.images(d, 0)
    assert len(moved) == 1 and moved[0].rect == pymupdf.Rect(272, 300, 452, 480)
    assert "Visible text on page one" in d.page_text(0).text   # text is kept
    E.move_image(d, moved[0], pymupdf.Rect(272, 300, 362, 390), "Resize image")
    assert d.undo_name() == "Resize image"
    assert E.images(d, 0)[0].rect == pymupdf.Rect(272, 300, 362, 390)
    png = str(tmp_path / "red.png")
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 40, 20), 0)
    pix.set_rect(pix.irect, (255, 0, 0))
    pix.save(png)
    E.replace_image(d, E.images(d, 0)[0], png)
    rep = E.images(d, 0)
    assert len(rep) == 1 and rep[0].width == 40   # kept the proportions inside the old box
    assert rep[0].rect.width == pytest.approx(90, abs=0.5) and rep[0].rect.height == pytest.approx(45, abs=0.5)
    E.delete_image(d, rep[0])
    assert E.images(d, 0) == []
    for _ in range(4):
        d.undo()
    assert E.images(d, 0)[0].rect == pymupdf.Rect(72, 200, 252, 380)


def test_add_image(samples, tmp_path):
    d = _open(samples, tmp_path)
    png = str(tmp_path / "blue.png")
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 400, 100), 0)
    pix.set_rect(pix.irect, (0, 0, 255))
    pix.save(png)
    assert E.image_size_for(png) == (200, 50)
    E.add_image(d, 2, pymupdf.Rect(100, 100, 300, 150), png)
    assert [i.rect for i in E.images(d, 2)] == [pymupdf.Rect(100, 100, 300, 150)]
    with pytest.raises(ValueError):
        E.add_image(d, 2, pymupdf.Rect(0, 0, 10, 10), str(tmp_path / "missing.png"))


# ======================================================================== OCR
def test_ocr_languages_and_scanned_detection(samples, tmp_path):
    assert "eng" in ocr.languages() and ocr.available()
    assert ocr.language_label("eng+spa") == "English + Spanish"
    assert ocr.tessdata_for("eng") and ocr.tessdata_for("xxx") is None
    assert ocr.looks_scanned(_open(samples, tmp_path, "scanned"))
    assert not ocr.looks_scanned(_open(samples, tmp_path, "main"))


def test_ocr_makes_scans_searchable_also_rotated(samples, tmp_path):
    d = _open(samples, tmp_path, "scanned")
    steps = []
    n = ocr.recognize(d, language="eng", dpi=300, progress=lambda i, total: steps.append((i, total)))
    assert n == 2 and steps[0] == (0, 2)
    assert d.undo_name() == "Recognize text"
    for p, prefix in ((0, ""), (1, "Page two ")):
        text = d.page_text(p).text
        for line in SCAN_LINES:
            assert prefix + line in text
    assert not ocr.looks_scanned(d)
    d.save()
    out = pymupdf.open(d.path)
    for p in range(2):
        # the found word must lie over the word in the picture, also on the rotated page
        assert len(out[p].search_for("Invoice")) == 1
    # the layer is invisible: the page renders the same as before
    before = pymupdf.open(samples["scanned"])
    for p in range(2):
        a = before[p].get_pixmap(dpi=40).samples
        b = out[p].get_pixmap(dpi=40).samples
        assert a == b
    # second run skips pages that have text now
    assert ocr.recognize(d) == 0
    # the recognized text can be removed again
    assert P.marked_pages(d, ocr.OCR_MARK) == [0, 1]
    ocr.remove_text_layers(d)
    assert not d.page_text(0).text.strip()


def test_ocr_word_positions_on_rotated_page(samples, tmp_path):
    d = _open(samples, tmp_path, "scanned")
    ocr.recognize(d, pages=[1])
    d.save()
    out = pymupdf.open(d.path)
    page = out[1]
    assert page.rotation == 90
    shown = page.rotation_matrix                 # unrotated -> displayed coordinates
    hit = page.search_for("Invoice")[0] * shown
    # in the picture, "Invoice" follows "Page two" on the first line
    first = sorted((r * shown for r in page.search_for("Page two")), key=lambda r: r.y0)[0]
    assert abs(hit.y0 - first.y0) < 4 and hit.x0 > first.x1
    assert hit.width > hit.height                # reads left to right as displayed
    assert 90 < hit.y0 < 140                     # first line, near the top of the shown page


def test_ocr_progress_can_cancel(samples, tmp_path):
    d = _open(samples, tmp_path, "scanned")
    assert ocr.recognize(d, progress=lambda i, n: i < 1) == 1
    assert d.page_text(0).text.strip() and not d.page_text(1).text.strip()


def test_ocr_missing_language(samples, tmp_path):
    d = _open(samples, tmp_path, "scanned")
    with pytest.raises(RuntimeError):
        ocr.recognize(d, language="xxx")


# ==================================================================== compare
def _edited_copy(samples, tmp_path):
    old = str(tmp_path / "old.pdf")
    new = str(tmp_path / "new.pdf")
    shutil.copy(samples["main"], old)
    d = PdfDocument(old)
    E.replace_line(d, E.text_lines(d, 1)[0], "Chapter 1 - Page Two")
    E.add_text(d, 3, (72, 760), "A brand new sentence")
    d.save(new)
    d.close()
    return old, new


def test_compare_finds_text_changes(samples, tmp_path):
    old, new = _edited_copy(samples, tmp_path)
    a, b = pymupdf.open(old), pymupdf.open(new)
    res = C.compare(a, b)
    counts = res.counts()
    assert counts["replaced"] >= 1 and counts["inserted"] >= 1
    texts = [c.describe() for c in res.changes]
    assert any("Two" in t for t in texts) and any("brand new sentence" in t for t in texts)
    assert res.pairs[:4] == [(0, 0), (1, 1), (2, 2), (3, 3)]
    same = C.compare(a, pymupdf.open(old))
    assert same.changes == []


def test_compare_report_and_visual(samples, tmp_path):
    old, new = _edited_copy(samples, tmp_path)
    out = str(tmp_path / "report.pdf")
    res = C.compare_files(old, new, out, visual=True)
    rep = pymupdf.open(out)
    assert rep.page_count >= 1 + len(res.pairs)
    assert "Compare" in rep[0].get_text() or "changes" in rep[0].get_text().lower()
    notes = [a.info.get("content", "") for p in rep for a in p.annots()]
    assert any("brand new sentence" in n for n in notes)
    assert res.visual and any(res.visual.values())


def test_compare_page_inserted(samples, tmp_path):
    old = str(tmp_path / "old.pdf")
    new = str(tmp_path / "new.pdf")
    shutil.copy(samples["main"], old)
    d = PdfDocument(old)
    P.insert_blank(d, 2)
    E.add_text(d, 2, (72, 100), "An inserted page with words")
    d.save(new)
    res = C.compare(pymupdf.open(old), pymupdf.open(new))
    assert (None, 2) in res.pairs
    assert res.pairs.index((None, 2)) == 2 and res.pairs[3] == (2, 3)


# ============================================================ certificates
def test_create_id_and_info(ids):
    path = S.create_id("Jane Doe", "secret1", email="jane@example.com", organization="ACME")
    assert path.startswith(S.ids_dir()) and S.list_ids() == [path]
    info = S.id_info(path, "secret1")
    assert "Jane Doe" in info.name and info.self_signed
    with pytest.raises(Exception):
        S.id_info(path, "wrong")
    with pytest.raises(ValueError):
        S.create_id("Jane", "123")
    assert S.create_id("Jane Doe", "secret1") != path   # no overwrite


def test_sign_new_box_validate_trust_and_tamper(samples, tmp_path, ids):
    pfx = S.create_id("Jane Doe", "secret1")
    out = str(tmp_path / "signed.pdf")
    S.sign(samples["main"], out, pfx, "secret1", page=0, rect=(350, 700, 550, 760), reason="I approve",
           location="Madrid")
    data = open(out, "rb").read()
    st = S.check(data)
    assert len(st) == 1
    s = st[0]
    assert s.signer == "Jane Doe" and s.reason == "I approve" and s.location == "Madrid"
    assert s.intact and s.valid and s.whole_file and not s.trusted and s.level == "unknown"
    assert "unknown" in s.summary
    # the box is where it was asked for (unrotated top-left coordinates)
    d = PdfDocument(out)
    assert d.is_signed() and S.has_signatures(d)
    w = [w for w in d.page(0).widgets() if w.field_type == pymupdf.PDF_WIDGET_TYPE_SIGNATURE][0]
    assert abs(w.rect.x0 - 350) < 1 and abs(w.rect.y0 - 700) < 1 and abs(w.rect.y1 - 760) < 1
    d.close()
    S.trust_certificate_of(pfx, "secret1")
    assert S.check(data)[0].level == "valid"
    bad = bytearray(data)
    i = bad.find(b"The quick brown fox")
    if i < 0:   # compressed content: damage the bytes of the first stream instead
        i = bad.find(b"stream") + 20
    bad[i] = (bad[i] + 1) % 256
    assert S.check(bytes(bad))[0].level == "invalid"


def test_sign_existing_field(samples, tmp_path, ids):
    pfx = S.create_id("Jane Doe", "secret1")
    fields = S.empty_signature_fields(samples["form"])
    assert fields and fields[0][0] == "signature"
    out = str(tmp_path / "signed-form.pdf")
    S.sign(samples["form"], out, pfx, "secret1", field="signature")
    assert S.empty_signature_fields(out) == []
    assert S.check(open(out, "rb").read())[0].field == "signature"


def test_saving_a_signed_document_keeps_the_signature(samples, tmp_path, ids):
    pfx = S.create_id("Jane Doe", "secret1")
    S.trust_certificate_of(pfx, "secret1")
    out = str(tmp_path / "signed.pdf")
    S.sign(samples["main"], out, pfx, "secret1", page=0, rect=(350, 700, 550, 760))
    signed = open(out, "rb").read()
    d = PdfDocument(out)
    assert d.keeps_signatures()
    with d.edit("Add note", [1]):
        d.doc[1].add_text_annot((100, 100), "A comment after signing")
    d.save()
    data = open(out, "rb").read()
    assert data.startswith(signed)                 # appended, the signed bytes are unchanged
    st = S.check(data)[0]
    assert st.intact and st.valid and not st.whole_file and st.level == "unknown"
    assert "added after signing" in st.summary
    # redaction must rewrite the file (removed content must not stay in an earlier revision)
    from paperleaf.core import protect
    protect.mark_rects(d, 2, [pymupdf.Rect(70, 90, 300, 120)], protect.RedactStyle(), "me")
    protect.apply(d)
    assert not d.keeps_signatures()
    d.save()
    assert not open(out, "rb").read().startswith(signed)
    d.close()


def test_sign_encrypted_document(samples, tmp_path, ids):
    pfx = S.create_id("Jane Doe", "secret1")
    out = str(tmp_path / "signed-enc.pdf")
    S.sign(samples["encrypted"], out, pfx, "secret1", page=0, rect=(350, 700, 550, 760), doc_password="secret")
    d = PdfDocument(out, "secret")
    assert d.encrypted and d.is_signed()
    assert S.check(open(out, "rb").read(), "secret")[0].intact


# ============================================================== read aloud
class FakeVoice:
    def __init__(self):
        self.said, self.speaking, self.events = [], False, []

    def voices(self):
        return ["Voice A", "Voice B"]

    def set_voice(self, name):
        self.events.append(("voice", name))

    def set_rate(self, rate):
        self.events.append(("rate", rate))

    def say(self, text):
        self.said.append(text)
        self.speaking = True

    def busy(self):
        return self.speaking

    def pause(self):
        self.events.append("pause")

    def resume(self):
        self.events.append("resume")

    def stop(self):
        self.speaking = False
        self.events.append("stop")


def test_page_speech_text(samples, tmp_path):
    from paperleaf.ui.read_aloud import page_speech_text
    d = _open(samples, tmp_path)
    text = page_speech_text(d, 0)
    assert "\n" not in text.strip() or text.count("\n") < d.page_text(0).text.count("\n")
    assert "jumps over the lazy dog" in text


def test_reader_reads_pages_in_order(qapp, samples, tmp_path):
    from tests.test_annot_gui import pump
    from paperleaf.ui.read_aloud import Reader
    d = _open(samples, tmp_path)
    voice = FakeVoice()
    r = Reader(voice)
    started, states = [], []
    r.pageStarted.connect(lambda doc, p: started.append(p))
    r.stateChanged.connect(states.append)
    assert r.read(d, [0, 1, 2])
    assert started == [0] and r.state == "speaking" and len(voice.said) == 1
    voice.speaking = False
    pump(400)
    assert started[:2] == [0, 1]
    r.toggle_pause()
    assert r.state == "paused" and "pause" in voice.events
    voice.speaking = False
    pump(400)
    assert started[:2] == [0, 1] and len(started) == 2   # paused: no next page
    r.toggle_pause()
    assert r.state == "speaking" and "resume" in voice.events
    voice.speaking = False
    pump(400)
    assert started == [0, 1, 2]
    voice.speaking = False
    pump(400)
    assert r.state == "stopped" and states[-1] == "stopped"


def test_reader_without_voices(qapp):
    from paperleaf.ui.read_aloud import Reader
    r = Reader.__new__(Reader)
    Reader.__init__(r, backend=False)
    r.backend = None
    assert not r.available and r.read(None, [0]) is False


def test_signing_code_cannot_reach_the_network():
    import socket
    with S._offline():
        with pytest.raises(OSError, match="offline"):
            socket.create_connection(("127.0.0.1", 9))
        with pytest.raises(OSError, match="offline"):
            socket.getaddrinfo("example.com", 443)
        # asyncio needs an in-process socket pair (loopback on Windows); that still works
        a, b = socket.socketpair()
        a.close()
        b.close()
        with pytest.raises(OSError, match="offline"):
            socket.create_connection(("127.0.0.1", 9))
    # restored afterwards
    assert socket.create_connection.__name__ == "create_connection"


# ========================================================= shell extension
def test_shell_extension_ids_match_everywhere():
    import re
    from paperleaf.winshell import registry as R
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    guids = open(os.path.join(root, "shellext", "src", "guids.h")).read()
    found = {m[1]: "{" + m[0].upper() + "}" for m in re.findall(r"// \{([0-9A-Fa-f-]{36})\}\s+(\w+)", guids)}
    assert found == {"Explorer": R.THUMB_CLSID, "Preview": R.PREVIEW_CLSID,
                     "Windows": "{6067870F-1205-4F4C-9373-75BE95DBD111}"}
    manifest = open(os.path.join(root, "shellext", "package", "AppxManifest.xml")).read()
    assert manifest.count("6067870F-1205-4F4C-9373-75BE95DBD111") == 2
    # the Windows 11 menu offers the same tools as the classic menu
    menu = open(os.path.join(root, "shellext", "src", "menu.cpp"), encoding="utf-8").read()
    assert re.findall(r'\{L"(\w+)", L"', menu) == [t for t, _ in R.TOOL_VERBS]


def test_shell_extension_registry_entries():
    from paperleaf.winshell import registry as R
    entries = R.shellext_entries(r"C:\Paperleaf\PaperleafShell.dll")
    keys = {(k, n): v for k, n, v in entries}
    clsid = rf"Software\Classes\CLSID\{R.THUMB_CLSID}\InprocServer32"
    assert keys[(clsid, None)] == r"C:\Paperleaf\PaperleafShell.dll" and keys[(clsid, "ThreadingModel")] == "Apartment"
    assert keys[(rf"Software\Classes\CLSID\{R.PREVIEW_CLSID}", "AppID")] == R.PREVHOST_APPID
    assert keys[(rf"Software\Classes\SystemFileAssociations\.pdf\ShellEx\{R.THUMB_SHELLEX}", None)] == R.THUMB_CLSID
    assert keys[(R.PREVIEW_HANDLERS, R.PREVIEW_CLSID)] == "Paperleaf PDF Preview"
    assert all(k.startswith("Software\\") for k, _n, _v in entries)   # per-user (HKCU) only
