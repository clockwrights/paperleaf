"""GUI tests: redaction tool and toolbar, password security, Remove Hidden
Information, Reduce File Size, Export To and Create PDF."""
import os
import shutil

import pymupdf
import pytest
from PySide6.QtWidgets import QFileDialog, QInputDialog, QMessageBox

from paperleaf.core import annotations as an
from paperleaf.core import convert as C
from paperleaf.core import protect as X
from paperleaf.core import recovery
from paperleaf.core.settings import Settings
from paperleaf.ui import protect_dialogs as D
from tests.test_annot_gui import drag, pump, vpos
from tests.test_pages_gui import accept_with

YES = QMessageBox.StandardButton.Yes


@pytest.fixture
def xw(qapp, tmp_path, samples):
    from paperleaf.ui.main_window import MainWindow
    path = tmp_path / "doc.pdf"
    shutil.copy(samples["main"], path)
    w = MainWindow(Settings(str(tmp_path / "cfg")))
    w.resize(1300, 950)
    w.show()
    pump(100)
    assert w.open_file(str(path))
    pump(200)
    v = w._view()
    v.set_zoom(1.0)
    v.go_to_page(0)
    pump(50)
    yield w, w._tab(), str(path)
    for i in range(w.tabs.count()):
        d = w.tabs.widget(i).doc
        recovery.discard(d.path)
        d.new_security = None
        d._saved_step = d._step()
    w.close()


def redacts(doc, pno=0):
    return [r for r in an.page_annots(doc, pno) if r.kind == "Redact"]


def test_redact_tool_area_and_text(xw, monkeypatch):
    w, t, _ = xw
    v, doc = t.view, t.doc
    w.a["redact_mode"].trigger()
    assert w.redact_bar.isVisible() and v.tool == "redact"
    # drag over an empty area: an area mark
    drag(v, vpos(v, 0, 300, 760), vpos(v, 0, 450, 800))
    marks = redacts(doc)
    assert len(marks) == 1 and abs(marks[0].rect.x0 - 300) < 3
    # drag across text: the selected text is marked
    x0, y0, x1, y1 = doc.page_text(0).lines[1]
    drag(v, vpos(v, 0, x0 + 1, (y0 + y1) / 2), vpos(v, 0, x0 + 110, (y0 + y1) / 2))
    assert len(redacts(doc)) == 2 and not v.has_selection()
    assert v.tool == "redact"  # the tool stays on
    pump(20)
    assert "2 areas marked" in w.redact_bar.count.text() and w.redact_bar.apply_btn.isEnabled()
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: YES))
    w.redact_bar.apply_btn.click()
    assert redacts(doc) == [] and not doc.page_text(0).text.split("\n")[1].startswith("The quick")
    assert doc.undo_name() == "Apply redactions"
    w.undo()
    assert len(redacts(doc)) == 2
    w.a["redact_mode"].trigger()
    assert not w.redact_bar.isVisible() and v.tool == "select"


def test_find_and_redact_dialog(xw, monkeypatch):
    w, t, _ = xw

    def setup(d):
        d.query.setText("lazy dog, foxes")
        d.search()
        assert d.results.count() > 10
        d.results.item(0).setCheckState(d.results.item(0).checkState().Unchecked)
    accept_with(monkeypatch, D.FindRedactDialog, setup)
    w.a["find_redact"].trigger()
    n = len(X.marks(t.doc))
    assert n > 10 and "Marked" in w.statusBar().currentMessage()
    dlg = D.FindRedactDialog(t.doc, w)
    dlg.r_patterns.setChecked(True)
    dlg.patterns["Web addresses"].setChecked(True)
    dlg.search()
    assert "No results" in dlg.count.text()


def test_redaction_properties(xw, monkeypatch):
    w, t, _ = xw
    accept_with(monkeypatch, D.RedactStyleDialog, lambda d: d.text.setText("REMOVED"))
    w.a["redact_props"].trigger()
    assert w.env.redact_style().text == "REMOVED"
    X.mark_rects(t.doc, 0, [(100, 100, 200, 120)], w.env.redact_style())
    X.apply(t.doc)
    assert "REMOVED" in t.doc.page_text(0).text


def test_password_security_flow(xw, monkeypatch):
    w, t, path = xw

    def setup(d):
        d.open_pw.setText("open1")
        d.open_again.setText("open1")
        d.restrict.setChecked(True)
        d.owner_pw.setText("owner1")
        d.owner_again.setText("owner1")
        d.printing.setCurrentIndex(d.printing.findData(X.PRINT_LOW))
        d.copy.setChecked(False)
    accept_with(monkeypatch, D.SecurityDialog, setup)
    w.a["encrypt"].trigger()
    assert t.doc.new_security and "(SECURED)" in w.windowTitle() and w.tabs.tabText(0).endswith("*")
    assert w.save()
    assert not t.doc.modified and t.doc.encrypted
    pump(50)
    assert t.view.doc.page_count == 12  # the view kept working after reopening
    re = pymupdf.open(path)
    assert re.needs_pass and re.authenticate("open1")
    re.close()
    # someone without the permissions password can't remove the security
    from paperleaf.core.document import PdfDocument
    other = PdfDocument(path, "open1")
    assert not other.is_owner()
    other.close()
    # the owner removes it (Paperleaf knows the owner password it just set)
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: YES))
    w.a["remove_security"].trigger()
    assert w.save()
    assert not pymupdf.open(path).needs_pass


def test_mismatched_passwords_are_refused(xw, monkeypatch):
    w, t, _ = xw
    warned = []
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: warned.append(a[2])))
    d = D.SecurityDialog(t.doc, w)
    d.open_pw.setText("a")
    d.open_again.setText("b")
    assert not d.validate() and "don't match" in warned[-1]
    d.open_again.setText("a")
    d.restrict.setChecked(True)
    assert not d.validate() and "permissions password" in warned[-1]


def test_owner_password_prompt(xw, monkeypatch, samples, tmp_path):
    w, t, _ = xw
    p = tmp_path / "restricted.pdf"
    src = pymupdf.open(samples["main"])
    src.save(str(p), encryption=pymupdf.PDF_ENCRYPT_AES_256, owner_pw="boss", user_pw="",
             permissions=pymupdf.PDF_PERM_PRINT)
    src.close()
    w.open_file(str(p))
    pump(100)
    answers = iter([("wrong", True), ("boss", True)])
    monkeypatch.setattr(QInputDialog, "getText", staticmethod(lambda *a, **k: next(answers)))
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: None))
    shown = []
    monkeypatch.setattr(D.SecurityDialog, "exec", lambda self: shown.append(1) or 0)
    w.a["encrypt"].trigger()      # wrong password: no dialog
    assert not shown
    w.a["encrypt"].trigger()      # right password: the dialog opens
    assert shown and w._tab().doc.is_owner()


def test_sanitize_dialog(xw, monkeypatch, samples):
    w, t, _ = xw
    w.open_file(samples["hidden"])
    pump(100)
    doc = w._tab().doc
    dlg = D.SanitizeDialog(doc, w)
    assert dlg.boxes["metadata"].isChecked() and not dlg.boxes["links"].isChecked()
    assert not dlg.boxes["forms"].isEnabled()  # none found
    accept_with(monkeypatch, D.SanitizeDialog)
    w.a["sanitize"].trigger()
    counts = X.inspect(doc).counts
    assert counts["metadata"] == 0 and counts["attachments"] == 0 and counts["links"] == 1
    assert doc.undo_name() == "Remove hidden information"


def test_reduce_size_and_exports(xw, monkeypatch, tmp_path, samples):
    w, t, _ = xw
    w.open_file(samples["hidden"])
    pump(100)
    out = str(tmp_path / "smaller.pdf")
    monkeypatch.setattr(QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: (out, "")))
    accept_with(monkeypatch, D.CompressDialog, lambda d: d.presets["small"].setChecked(True))
    boxes = []
    monkeypatch.setattr(QMessageBox, "exec", lambda self: boxes.append(self.text()) or 0)
    w.a["compress"].trigger()
    assert os.path.getsize(out) < os.path.getsize(samples["hidden"]) and "smaller" in boxes[-1]
    w.tabs.setCurrentIndex(0)
    folder = tmp_path / "imgs"
    folder.mkdir()
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", staticmethod(lambda *a, **k: str(folder)))
    accept_with(monkeypatch, D.ExportImagesDialog, lambda d: (d.fmt.setCurrentIndex(1), d.range.r_cur.setChecked(True)))
    w.a["export_images"].trigger()
    assert os.listdir(folder) == ["doc_01.jpg"]
    txt = str(tmp_path / "t.txt")
    monkeypatch.setattr(QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: (txt, "")))
    w.a["export_text"].trigger()
    assert "Chapter 1 - Page 1" in open(txt, encoding="utf-8").read()
    page = str(tmp_path / "t.html")
    monkeypatch.setattr(QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: (page, "")))
    w.a["export_html"].trigger()
    assert os.path.exists(page)
    if C.word_available():
        docx = str(tmp_path / "t.docx")
        monkeypatch.setattr(QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: (docx, "")))
        w.a["export_word"].trigger()
        assert os.path.getsize(docx) > 1000


def test_create_pdf_from_images_and_office(xw, monkeypatch, tmp_path):
    w, t, _ = xw
    img = str(tmp_path / "photo.png")
    pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 120, 80), 0).save(img)
    out = str(tmp_path / "photo.pdf")
    monkeypatch.setattr(QFileDialog, "getOpenFileNames", staticmethod(lambda *a, **k: ([img], "")))
    monkeypatch.setattr(QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: (out, "")))
    w.a["create_images"].trigger()
    assert os.path.normcase(w._tab().doc.path) == os.path.normcase(out)
    if os.name != "nt":
        warned = []
        monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: warned.append(a[2])))
        docx = tmp_path / "letter.docx"
        docx.write_bytes(b"PK")
        assert not w.open_file(str(docx))
        assert "Microsoft Word" in warned[-1]


def test_explorer_protect_tools(xw, monkeypatch, samples):
    w, t, _ = xw
    calls = []
    monkeypatch.setattr(w.protect, "compress", lambda: calls.append("compress"))
    monkeypatch.setattr(w.protect, "from_office", lambda p=None: calls.append(("topdf", os.path.basename(p))))
    w.handle_message({"tool": "compress", "paths": [samples["form"]]})
    pump(50)
    w.handle_message({"tool": "topdf", "paths": ["/x/report.docx"]})
    assert calls == ["compress", ("topdf", "report.docx")]
    from paperleaf.winshell import registry
    verbs = [v for k, n, v in registry.tool_entries() if n == "MUIVerb"]
    assert "Reduce file size…" in verbs and "Convert to Word" in verbs and "Protect with password…" in verbs
