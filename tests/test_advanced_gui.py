"""Advanced tools in the window: Edit PDF, Recognize Text, Compare Files, Sign with
Certificate and Read Out Loud, driven with simulated input."""
import os
import shutil

import pymupdf
import pytest
from PySide6.QtCore import QPointF, Qt
from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox

from paperleaf.core import edit as E
from paperleaf.core import recovery
from paperleaf.core import signing as S
from paperleaf.core.settings import Settings
from paperleaf.ui import advanced_dialogs as AD
from paperleaf.viewer import edit_tools as ET
from tests.test_advanced import FakeVoice
from tests.test_annot_gui import click, drag, key, pump, vpos
from tests.test_forms_gui import choose_menu_item
from tests.test_pages_gui import accept_with


@pytest.fixture
def make_win(qapp, tmp_path, samples, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    wins = []

    def make(key="main", name=None):
        from paperleaf.ui.main_window import MainWindow
        path = tmp_path / (name or os.path.basename(samples[key]))
        shutil.copy(samples[key], path)
        w = MainWindow(Settings(str(tmp_path / "cfg")))
        w.resize(1300, 1000)
        w.show()
        pump(100)
        assert w.open_file(str(path))
        pump(250)
        v = w._view()
        v.set_zoom(1.0)
        v.go_to_page(0)
        pump(100)
        wins.append(w)
        return w, v, str(path)
    yield make
    for w in wins:
        for i in range(w.tabs.count()):
            d = w.tabs.widget(i).doc
            recovery.discard(d.path)
            d._saved_step = d._step()
        w.close()


def centre_of(v, pno, rect):
    return v.text_rect_to_view(pno, rect).center()


# ===================================================================== Edit PDF
def test_edit_mode_toggles_and_edits_a_line(make_win):
    w, v, _ = make_win()
    d = v.doc
    assert not w.edit_bar.isVisible()
    w.a["edit_pdf"].trigger()
    assert w.adv.editing and w.edit_bar.isVisible() and v.editing.active
    assert w.a["edit_add_text"].isEnabled() and not w.a["edit_delete_image"].isEnabled()
    line = E.text_lines(d, 0)[0]
    click(v, centre_of(v, 0, line.rect))
    ed = v.editing.editor
    assert isinstance(ed, ET.LineEdit) and ed.text() == "Chapter 1 - Page 1" and ed.isVisible()
    ed.selectAll()
    ed.insert("Chapter One")
    key(ed, Qt.Key.Key_Return)
    pump(50)
    assert v.editing.editor is None
    assert "Chapter One" in d.page_text(0).text and d.undo_name() == "Edit text"
    # Escape throws the typing away
    line = [ln for ln in E.text_lines(d, 0) if ln.text == "Chapter One"][0]
    click(v, centre_of(v, 0, line.rect))
    ed = v.editing.editor
    ed.setText("Not this")
    key(ed, Qt.Key.Key_Escape)
    pump(50)
    assert "Not this" not in d.page_text(0).text
    # clicking text in edit mode does not select it
    assert not v.has_selection()
    w.a["edit_pdf"].trigger()
    assert not w.adv.editing and not w.edit_bar.isVisible() and not v.editing.active


def test_edit_line_finishes_when_clicking_elsewhere(make_win):
    w, v, _ = make_win()
    d = v.doc
    w.adv.toggle_edit(True)
    line = E.text_lines(d, 0)[1]
    click(v, centre_of(v, 0, line.rect))
    ed = v.editing.editor
    ed.setText("Short line")
    click(v, vpos(v, 0, 300, 700))   # empty part of the page
    pump(80)
    assert v.editing.editor is None and "Short line" in d.page_text(0).text


def test_move_resize_and_delete_image(make_win):
    w, v, _ = make_win("hidden")
    d = v.doc
    w.adv.toggle_edit(True)
    img = E.images(d, 0)[0]
    c = centre_of(v, 0, img.rect)
    click(v, c)
    assert v.editing.sel is not None and w.a["edit_delete_image"].isEnabled()
    assert w.a["edit_replace_image"].isEnabled()
    s = v.zoom * 96 / 72
    drag(v, c, c + QPointF(100 * s, 50 * s))
    moved = E.images(d, 0)[0]
    assert abs(moved.rect.x0 - 172) < 2 and abs(moved.rect.y0 - 250) < 2 and d.undo_name() == "Move image"
    assert v.editing.sel is not None and v.editing.sel.rect == moved.rect
    # bottom-right handle: corners keep the proportions
    corner = v.text_rect_to_view(0, moved.rect).bottomRight()
    drag(v, corner, corner + QPointF(-90 * s, -20 * s))
    resized = E.images(d, 0)[0]
    assert d.undo_name() == "Resize image"
    assert resized.rect.width == pytest.approx(90, abs=2) and resized.rect.height == pytest.approx(90, abs=2)
    # arrow keys nudge, Delete removes
    key(v, Qt.Key.Key_Right)
    assert E.images(d, 0)[0].rect.x0 == pytest.approx(resized.rect.x0 + 1 / s, abs=0.2)
    key(v, Qt.Key.Key_Delete)
    assert E.images(d, 0) == [] and v.editing.sel is None and not w.a["edit_delete_image"].isEnabled()
    w.undo()
    assert len(E.images(d, 0)) == 1


def test_add_text_and_image(make_win, tmp_path, monkeypatch):
    w, v, _ = make_win()
    d = v.doc
    w.a["edit_add_text"].trigger()      # turns Edit PDF on as well
    assert w.adv.editing and v.editing.mode == ET.ADD_TEXT and w.a["edit_add_text"].isChecked()
    assert "Click where the new text goes" in w.edit_bar.hint.text()
    click(v, vpos(v, 0, 100, 650))
    ed = v.editing.editor
    assert isinstance(ed, ET.BlockEdit)
    ed.insertPlainText("Hello there\nSecond line")
    key(ed, Qt.Key.Key_Return, Qt.KeyboardModifier.ControlModifier)
    pump(50)
    text = d.page_text(0).text
    assert "Hello there" in text and "Second line" in text and d.undo_name() == "Add text"
    assert v.editing.mode == "" and not w.a["edit_add_text"].isChecked()
    png = str(tmp_path / "logo.png")
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 100, 50), 0)
    pix.set_rect(pix.irect, (0, 128, 0))
    pix.save(png)
    monkeypatch.setattr(QFileDialog, "getOpenFileName", staticmethod(lambda *a, **k: (png, "")))
    w.a["edit_add_image"].trigger()
    assert v.editing.mode == ET.ADD_IMAGE
    click(v, vpos(v, 0, 300, 600))
    imgs = E.images(d, 0)
    assert len(imgs) == 1 and imgs[0].rect.width == pytest.approx(100, abs=1)
    assert abs((imgs[0].rect.x0 + imgs[0].rect.x1) / 2 - 300) < 2
    assert v.editing.sel is not None and w.a["edit_delete_image"].isEnabled()
    w.a["edit_delete_image"].trigger()
    assert E.images(d, 0) == []


def test_choosing_a_comment_tool_leaves_edit_mode(make_win):
    w, v, _ = make_win()
    w.adv.toggle_edit(True)
    w.tool_actions["highlight"].trigger()
    assert not w.adv.editing and not v.editing.active and v.tool == "highlight"
    # Prepare Form and Edit PDF don't run together
    w.adv.toggle_edit(True)
    w.a["prepare_form"].trigger()
    assert w._prepare
    w.adv.toggle_edit(True)
    assert w.adv.editing and not w._prepare and not w.prepare_bar.isVisible()


def test_edit_mode_respects_permissions(make_win, monkeypatch):
    w, v, _ = make_win()
    monkeypatch.setattr(type(v.doc), "can_modify", lambda self: False)
    w.adv.toggle_edit(True)
    assert not w.a["edit_add_text"].isEnabled()
    line = E.text_lines(v.doc, 0)[0]
    click(v, centre_of(v, 0, line.rect))
    assert v.editing.editor is None


# ================================================================ Recognize text
def test_recognize_text_dialog_and_scanned_bar(make_win, monkeypatch):
    w, v, _ = make_win("scanned")
    t = w._tab()
    pump(100)
    assert t.ocrbar.isVisible() and "scanned" in t.ocrbar.text.text()
    accept_with(monkeypatch, AD.OcrDialog, lambda dlg: dlg.dpi.setCurrentIndex(1))
    t.ocrbar.button.click()
    pump(50)
    assert "Invoice number 4711" in v.doc.page_text(0).text
    assert "Page two Invoice number 4711" in v.doc.page_text(1).text
    assert not t.ocrbar.isVisible() and v.doc.undo_name() == "Recognize text"
    # the found text can be searched in the view
    import re
    v.start_search(re.compile("Invoice"))
    for _ in range(50):
        if v.search_finished():
            break
        pump(20)
    assert v.total_hits() == 2


def test_ocr_dialog_page_choice(qapp, samples, tmp_path):
    from paperleaf.core.document import PdfDocument
    d = PdfDocument(samples["scanned"])
    dlg = AD.OcrDialog(d, current=1)
    assert dlg.language.currentData() == "eng" and dlg.dpi.currentData() == 300
    dlg.range.r_cur.setChecked(True)
    assert dlg.values()["pages"] == [1] and dlg.values()["skip_text"]
    dlg.range.r_range.setChecked(True)
    dlg.range.edit.setText("9")
    monkey = []
    QMessageBox.warning, old = (lambda *a, **k: monkey.append(a)), QMessageBox.warning
    try:
        assert not dlg.validate() and monkey
    finally:
        QMessageBox.warning = old
    d.close()


# ============================================================ compare
def test_compare_files_opens_a_report(make_win, tmp_path, monkeypatch):
    w, v, path = make_win()
    other = str(tmp_path / "changed.pdf")
    shutil.copy(path, other)
    from paperleaf.core.document import PdfDocument
    od = PdfDocument(other)
    E.replace_line(od, E.text_lines(od, 0)[0], "A different heading")
    od.save()
    od.close()
    accept_with(monkeypatch, AD.CompareDialog, lambda dlg: dlg.new[1].setText(other))
    w.a["compare"].trigger()
    pump(100)
    assert w.tabs.count() == 2
    rep = w._tab().doc
    assert rep.path.endswith(".pdf") and "Compare" in os.path.basename(rep.path)
    assert "replaced" in w.statusBar().currentMessage()
    tmp_dir = os.path.dirname(rep.path)
    assert tmp_dir in w.adv._temp_dirs
    w.adv.cleanup()
    assert not os.path.exists(tmp_dir)


def test_compare_dialog_needs_two_files(qapp, tmp_path, monkeypatch):
    warned = []
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: warned.append(a[2])))
    dlg = AD.CompareDialog(str(tmp_path / "missing.pdf"))
    assert not dlg.validate() and "two PDF files" in warned[-1]


# =================================================== certificate signatures
def _id(tmp_path):
    return S.create_id("Jane Doe", "secret1")


def test_sign_with_certificate_new_box(make_win, tmp_path, monkeypatch):
    w, v, path = make_win()
    pfx = _id(tmp_path)

    def setup(dlg):
        assert dlg.ids.findData(pfx) >= 0
        dlg.ids.setCurrentIndex(dlg.ids.findData(pfx))
        dlg.pw.setText("secret1")
        dlg.where.setCurrentIndex(dlg.where.findData(AD.NEW_BOX))
        dlg.location.setText("Madrid")
        dlg.trust.setChecked(True)
    accept_with(monkeypatch, AD.SignCertificateDialog, setup)
    out = str(tmp_path / "signed.pdf")
    monkeypatch.setattr(QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: (out, "")))
    w.a["sign_cert"].trigger()
    assert v._pick is not None    # now drag the box
    drag(v, vpos(v, 0, 350, 650), vpos(v, 0, 550, 720))
    pump(200)
    assert os.path.exists(out) and w.tabs.count() == 2
    t = w._tab()
    pump(100)
    assert t.doc.path == os.path.abspath(out) and t.sigbar.isVisible()
    assert not t.formbar.isVisible()   # a signed signature field is nothing to fill in
    assert "valid" in t.sigbar.text.text()
    st = t.sig_statuses[0]
    assert st.level == "valid" and st.location == "Madrid" and st.signer == "Jane Doe"
    wid = [x for x in t.doc.page(0).widgets() if x.field_type == pymupdf.PDF_WIDGET_TYPE_SIGNATURE][0]
    assert abs(wid.rect.x0 - 350) < 2 and abs(wid.rect.y1 - 720) < 2
    assert w.settings.value("sign/location") == "Madrid"
    # the panel shows the check
    shown = []
    monkeypatch.setattr(AD.SignaturePanel, "exec", lambda self: shown.append(self.list.count()) or 0)
    t.sigbar.button.click()
    assert shown == [1]


def test_pick_area_can_be_cancelled(make_win, tmp_path, monkeypatch):
    w, v, _ = make_win()
    got = []
    v.pick_area(lambda pno, rect: got.append((pno, rect)))
    key(v, Qt.Key.Key_Escape)
    assert got == [(None, None)] and v._pick is None


def test_sign_signature_field_and_save_over(make_win, tmp_path, monkeypatch):
    w, v, path = make_win("form")
    pfx = _id(tmp_path)
    choose_menu_item(monkeypatch, w, "Sign with Certificate")

    def setup(dlg):
        assert dlg.where.currentData()[0] == "signature"
        dlg.ids.setCurrentIndex(dlg.ids.findData(pfx))
        dlg.pw.setText("secret1")
    accept_with(monkeypatch, AD.SignCertificateDialog, setup)
    monkeypatch.setattr(QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: (path, "")))
    from paperleaf.core import forms as F
    field = [f for f in F.all_fields(v.doc) if f.name == "signature"][0]
    click(v, v.text_rect_to_view(0, field.rect).center())
    pump(200)
    t = w._tab()
    assert w.tabs.count() == 1 and t.doc.is_signed()   # signed in place and reopened
    assert t.sigbar.isVisible() and t.sig_statuses[0].field == "signature"
    assert t.sig_statuses[0].level == "unknown"          # not trusted on this computer
    # a change saved afterwards keeps the signature intact (added after signing)
    with t.doc.edit("Add note", [0]):
        t.doc.doc[0].add_text_annot((300, 300), "after")
    assert w.save(t)
    st = t.sig_statuses[0]
    assert st.intact and not st.whole_file


def test_sign_asks_to_save_first(make_win, tmp_path, monkeypatch):
    w, v, _ = make_win()
    E.add_text(v.doc, 0, (100, 100), "unsaved change")
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.StandardButton.Cancel))
    called = []
    monkeypatch.setattr(AD.SignCertificateDialog, "exec", lambda self: called.append(1) or 0)
    w.a["sign_cert"].trigger()
    assert called == []


def test_sign_dialog_checks_password_and_creates_id(qapp, tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    warned = []
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: warned.append(a[2])))
    dlg = AD.SignCertificateDialog([], author="Jane")
    assert not dlg.validate() and "digital ID" in warned[-1]

    def make(cdlg):
        assert cdlg.name.text() == "Jane"
        cdlg.pw.setText("secret1")
        cdlg.again.setText("secret1")
    accept_with(monkeypatch, AD.CreateIdDialog, make)
    dlg._create()
    assert dlg.ids.count() == 1 and dlg.pw.text() == "secret1" and dlg.trust.isChecked()
    assert dlg.validate()
    dlg.pw.setText("wrong")
    assert not dlg.validate() and "password" in warned[-1]
    c = AD.CreateIdDialog("X")
    c.pw.setText("secret1")
    c.again.setText("secret2")
    assert not c.validate() and "match" in warned[-1]


# ================================================================ read aloud
def test_read_out_loud_actions(make_win, monkeypatch):
    voice = FakeVoice()
    import paperleaf.ui.read_aloud as RA
    monkeypatch.setattr(RA, "make_backend", lambda: voice)
    w, v, _ = make_win()
    assert not w.a["read_pause"].isEnabled()
    v.go_to_page(2)
    pump(50)
    w.a["read_end"].trigger()
    assert len(voice.said) == 1 and "Page 3" in voice.said[0]
    assert w.a["read_pause"].isEnabled() and w.a["read_stop"].isEnabled()
    voice.speaking = False
    pump(400)
    assert len(voice.said) == 2 and v.current_page() == 3   # the view follows the reading
    w.a["read_pause"].trigger()
    assert w.a["read_pause"].text() == "&Resume"
    w.a["read_pause"].trigger()
    w.a["read_stop"].trigger()
    assert not w.a["read_stop"].isEnabled() and "stop" in voice.events
    w.a["read_page"].trigger()
    voice.speaking = False
    pump(400)
    assert w.adv.reader().state == "stopped"   # only this page
    accept_with(monkeypatch, AD.ReadAloudDialog, lambda dlg: (dlg.voice.setCurrentIndex(2), dlg.rate.setValue(3)))
    w.a["read_options"].trigger()
    assert ("voice", "Voice B") in voice.events and ("rate", 3) in voice.events
    assert w.settings.value("read/voice") == "Voice B"


def test_read_out_loud_without_voices(make_win, monkeypatch):
    import paperleaf.ui.read_aloud as RA
    monkeypatch.setattr(RA, "make_backend", lambda: None)
    warned = []
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: warned.append(a[2])))
    w, v, _ = make_win()
    w.a["read_page"].trigger()
    assert warned and "speech" in warned[0]


def test_closing_the_tab_stops_reading(make_win, monkeypatch):
    voice = FakeVoice()
    import paperleaf.ui.read_aloud as RA
    monkeypatch.setattr(RA, "make_backend", lambda: voice)
    w, v, _ = make_win()
    w.a["read_page"].trigger()
    assert w.adv.reader().state == "speaking"
    w.close_tab(0)
    assert w.adv.reader().state == "stopped"
    QApplication.processEvents()
