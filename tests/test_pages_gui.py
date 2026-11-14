"""GUI tests: Organize Pages, the Document menu and the page dialogs."""
import os
import shutil

import pymupdf
import pytest
from PySide6.QtCore import QMimeData, QPointF, Qt, QUrl
from PySide6.QtGui import QDropEvent
from PySide6.QtWidgets import QDialog, QFileDialog, QMessageBox

from paperleaf.core import annotations as an
from paperleaf.core import pages as P
from paperleaf.core import recovery
from paperleaf.core.settings import Settings
from paperleaf.ui import page_dialogs as D
from tests.test_annot_gui import key, pump, vpos



@pytest.fixture
def pw(qapp, tmp_path, samples):
    from paperleaf.ui.main_window import MainWindow
    path = tmp_path / "doc.pdf"
    shutil.copy(samples["main"], path)
    w = MainWindow(Settings(str(tmp_path / "cfg")))
    w.resize(1300, 950)
    w.show()
    pump(100)
    assert w.open_file(str(path))
    pump(200)
    yield w, w._tab(), str(path)
    for i in range(w.tabs.count()):
        d = w.tabs.widget(i).doc
        recovery.discard(d.path)
        d._saved_step = d._step()
    w.close()


def first_line(doc, pno):
    return doc.page_text(pno).text.split("\n")[0]


def accept_with(monkeypatch, cls, setup=None):
    """Make ``cls.exec`` run ``setup(dialog)`` and then accept, without showing it."""
    def fake(self):
        if setup:
            setup(self)
        return QDialog.DialogCode.Accepted if self.validate() else QDialog.DialogCode.Rejected
    monkeypatch.setattr(cls, "exec", fake)


def test_organize_view_commands(pw):
    w, t, _ = pw
    w.a["organize"].trigger()
    assert t.organizing() and w.a["organize"].isChecked()
    grid = t.organizer.grid
    assert grid.count() == 12
    pump(100)
    t.organizer.select_pages([1, 2])
    t.organizer.actions_by_name["rotate_cw"].trigger()
    assert t.doc.doc[1].rotation == 90 and grid.selected_pages() == [1, 2]
    t.organizer.actions_by_name["duplicate"].trigger()
    assert grid.count() == 14 and grid.selected_pages() == [2, 4]
    w.undo()
    w.undo()
    assert grid.count() == 12 and t.doc.doc[1].rotation == 0
    # move pages 11 and 12 to the front (what a drag and drop does)
    t.organizer.select_pages([10, 11])
    grid.pagesMoved.emit([10, 11], 0)
    assert first_line(t.doc, 0) == "Chapter 4 - Page 11" and grid.selected_pages() == [0, 1]
    # Delete key deletes the selection
    grid.setFocus()
    key(grid, Qt.Key.Key_Delete)
    assert grid.count() == 10 and t.doc.page_count == 10
    assert "Deleted 2 pages" in w.statusBar().currentMessage()
    # the page view and the page counter follow
    assert w.page_total.text().strip() == "/ 10" or "of 10" in w.page_total.text()
    # double-click opens the page
    grid.pageActivated.emit(4)
    assert not t.organizing() and t.view.current_page() == 4
    w.undo()
    assert t.doc.page_count == 12


def test_drop_files_into_organize(pw, samples):
    w, t, _ = pw
    t.set_organize(True)
    grid = t.organizer.grid
    pump(50)
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(samples["form"])])
    r = grid.visualItemRect(grid.item(1))
    pos = QPointF(r.left() + 2, r.center().y())
    ev = QDropEvent(pos, Qt.DropAction.CopyAction, mime, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    grid.dropEvent(ev)
    pump(50)
    assert t.doc.page_count == 14 and first_line(t.doc, 1) == "Membership application"
    assert grid.count() == 14 and grid.selected_pages() == [1, 2]


def test_view_follows_page_changes(pw):
    w, t, _ = pw
    v = t.view
    v.go_to_page(11)
    pump(50)
    t.show_panel("thumbnails")
    t.show_panel("bookmarks")
    v._set_selection((11, 0), (11, 5))
    P.delete(t.doc, [9, 10, 11])
    pump(50)
    assert v.current_page() == 8 and not v.has_selection()
    assert len([r for r in v._rects if not r.isNull()]) == 9
    assert t.panels["thumbnails"].count() == 9
    assert w.page_edit.text() and "9" in w.page_total.text()
    # bookmarks were rebuilt: "Chapter 4" now has no page, the others still work
    assert t.panels["bookmarks"].topLevelItemCount() >= 3
    w.undo()
    pump(50)
    assert t.panels["thumbnails"].count() == 12 and len(v._rects) == 12
    # search hits are dropped when pages move
    t.open_find()
    t.findbar.edit.setText("Chapter")
    t.findbar._run()
    for _ in range(50):
        pump(20)
        if v.search_finished():
            break
    assert v.total_hits() > 0
    P.rotate(t.doc, [0], 90)
    assert v.total_hits() == 0
    # annotations still work on a moved page
    w._set_tool("rect")
    P.move(t.doc, [3], 0)
    v.set_zoom(1.0)
    v.go_to_page(0)
    pump(50)
    from tests.test_annot_gui import drag
    drag(v, vpos(v, 0, 100, 600), vpos(v, 0, 200, 650))
    assert [r.kind for r in an.page_annots(t.doc, 0)] == ["Square"]


def test_insert_extract_split_combine_dialogs(pw, monkeypatch, tmp_path, samples):
    w, t, path = pw
    doc = t.doc
    # insert from file, after page 2
    monkeypatch.setattr(QFileDialog, "getOpenFileNames", staticmethod(lambda *a, **k: ([samples["form"]], "")))

    def where(dlg):
        dlg.page.setValue(2)
    accept_with(monkeypatch, D.InsertDialog, where)
    w.a["page_insert_file"].trigger()
    assert doc.page_count == 14 and first_line(doc, 2) == "Membership application"
    assert t.view.current_page() == 2
    # blank pages from the menu
    accept_with(monkeypatch, D.InsertDialog, lambda d: (d.r_last.setChecked(True), d.count.setValue(2)))
    w.a["page_insert_blank"].trigger()
    assert doc.page_count == 16 and doc.page_text(15).text == ""
    # extract pages 1-2, deleting them afterwards
    out = str(tmp_path / "out.pdf")
    monkeypatch.setattr(QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: (out, "")))
    accept_with(monkeypatch, D.ExtractDialog, lambda d: (d.range.edit.setText("1-2"), d.range.r_range.setChecked(True),
                                                        d.delete.setChecked(True)))
    w.a["page_extract"].trigger()
    assert os.path.exists(out) and pymupdf.open(out).page_count == 2
    assert doc.page_count == 14
    assert w.tabs.count() == 2  # the extracted file opened in a new tab
    w.tabs.setCurrentIndex(0)
    # split every 5 pages into a folder
    folder = tmp_path / "parts"
    folder.mkdir()
    accept_with(monkeypatch, D.SplitDialog, lambda d: (d.n_pages.setValue(5), d.folder.setText(str(folder))))
    monkeypatch.setattr(QMessageBox, "exec", lambda self: 0)
    w.a["page_split"].trigger()
    assert sorted(os.listdir(folder)) == ["doc_Part1.pdf", "doc_Part2.pdf", "doc_Part3.pdf"]
    # combine two files
    combined = str(tmp_path / "both.pdf")
    monkeypatch.setattr(QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: (combined, "")))
    accept_with(monkeypatch, D.CombineDialog, lambda d: (d.list.clear(), d.add_paths([samples["main"], samples["form"]])))
    w.a["combine"].trigger()
    assert pymupdf.open(combined).page_count == 14
    assert os.path.normcase(w._tab().doc.path) == os.path.normcase(combined)


def test_combine_dialog_list(qapp, samples):
    dlg = D.CombineDialog([samples["main"], samples["form"], samples["xfa"]])
    assert dlg.paths() == [os.path.abspath(p) for p in (samples["main"], samples["form"], samples["xfa"])]
    dlg.list.item(2).setSelected(True)
    dlg._move(-1)
    assert os.path.basename(dlg.paths()[1]) == "xfa.pdf"
    dlg.list.clearSelection()
    dlg.list.item(0).setSelected(True)
    dlg._remove()
    assert len(dlg.paths()) == 2
    dlg.list.clear()
    import unittest.mock as um
    with um.patch.object(QMessageBox, "warning") as warn:
        assert not dlg.validate() and warn.called


def test_marks_and_crop_dialogs(pw, monkeypatch):
    w, t, _ = pw
    doc = t.doc

    def hf(d):
        d.boxes[("Footer", 1)].setText("Page <<page>> of <<pages>>")
        d.boxes[("Header", 2)].setText("Draft")
    accept_with(monkeypatch, D.HeaderFooterDialog, hf)
    w.a["page_header_footer"].trigger()
    assert "Page 4 of 12" in doc.page_text(3).text
    w.a["page_remove_header_footer"].trigger()
    assert "Page 4 of 12" not in doc.page_text(3).text
    accept_with(monkeypatch, D.PageNumbersDialog, lambda d: d.fmt.setCurrentIndex(2))
    w.a["page_page_numbers"].trigger()
    assert "Page 2 of 12" in doc.page_text(1).text
    accept_with(monkeypatch, D.BatesDialog, lambda d: (d.prefix.setText("XY"), d.start.setValue(100)))
    w.a["page_bates"].trigger()
    assert "XY000111" in doc.page_text(11).text
    assert int(w.settings.value("bates/next")) == 112
    accept_with(monkeypatch, D.WatermarkDialog, lambda d: d.text.setPlainText("DRAFT COPY"))
    w.a["page_watermark"].trigger()
    assert "DRAFT COPY" in doc.page_text(0).text
    w.a["page_remove_watermark"].trigger()
    assert "DRAFT COPY" not in doc.page_text(0).text
    # crop: white margins of the current page
    t.view.go_to_page(1)
    accept_with(monkeypatch, D.CropDialog, lambda d: (d.margins.set_points((36, 36, 36, 36)),
                                                      d.range.r_cur.setChecked(True)))
    w.a["page_crop"].trigger()
    assert abs(doc.page_rects[1].width - (595 - 72)) < 1
    accept_with(monkeypatch, D.PageSizeDialog, lambda d: (d.size.setCurrentIndex(d.size.findText("A4", Qt.MatchFlag.MatchStartsWith)),
                                                          d.orient.setCurrentIndex(1), d.range.r_cur.setChecked(True)))
    w.a["page_page_size"].trigger()
    assert (round(doc.page_rects[1].width), round(doc.page_rects[1].height)) == (842, 595)
    accept_with(monkeypatch, D.RotateDialog, lambda d: (d.direction.setCurrentIndex(2),))
    w.a["page_rotate"].trigger()
    assert all(doc.doc[p].rotation == (270 if p == 7 else 180) for p in range(12))
    accept_with(monkeypatch, D.DeleteDialog, lambda d: (d.range.edit.setText("2-12"), d.range.r_range.setChecked(True)))
    w.a["page_delete_pages"].trigger()
    assert doc.page_count == 1


def test_dialog_validation(qapp, samples):
    from paperleaf.core.document import PdfDocument
    doc = PdfDocument(samples["main"])
    dlg = D.DeleteDialog(12, 0)
    dlg.range.r_all.setChecked(True)
    import unittest.mock as um
    with um.patch.object(QMessageBox, "warning") as warn:
        assert not dlg.validate()
        assert warn.called
        dlg.range.edit.setText("50")
        dlg.range.r_range.setChecked(True)
        assert not dlg.validate()
        s = D.SplitDialog(doc)
        s.r_ranges.setChecked(True)
        s.ranges.setText("bad")
        assert not s.validate()
        s.ranges.setText("1-2; 3-12")
        s._update()
        assert "2 files" in s.preview.text()
    rng = D.PageRangeBox(10, 3)
    rng.subset.setCurrentIndex(1)
    assert rng.pages() == [1, 3, 5, 7, 9]
    rng.r_cur.setChecked(True)
    rng.subset.setCurrentIndex(0)
    assert rng.pages() == [3]
    doc.close()


def test_password_for_inserted_file(pw, monkeypatch, samples):
    w, t, _ = pw
    from PySide6.QtWidgets import QInputDialog
    answers = iter([("wrong", True), ("secret", True)])
    monkeypatch.setattr(QInputDialog, "getText", staticmethod(lambda *a, **k: next(answers)))
    monkeypatch.setattr(QFileDialog, "getOpenFileNames", staticmethod(lambda *a, **k: ([samples["encrypted"]], "")))
    accept_with(monkeypatch, D.InsertDialog, lambda d: d.r_first.setChecked(True))
    w.a["page_insert_file"].trigger()
    assert t.doc.page_count == 13 and first_line(t.doc, 0) == "Top secret content"


def test_permissions_block_page_changes(pw, samples, tmp_path):
    w, t, _ = pw
    p = tmp_path / "locked.pdf"
    src = pymupdf.open(samples["main"])
    src.save(str(p), encryption=pymupdf.PDF_ENCRYPT_AES_256, owner_pw="o", user_pw="",
             permissions=pymupdf.PDF_PERM_PRINT)
    src.close()
    assert w.open_file(str(p))
    pump(100)
    assert not w.a["page_insert_file"].isEnabled() and not w.a["page_watermark"].isEnabled()
    assert w.a["page_extract"].isEnabled()
    w.pages.run("delete", [0])
    assert w._tab().doc.page_count == 12 and "does not allow" in w.statusBar().currentMessage()


def test_explorer_tools(pw, monkeypatch, samples):
    w, t, path = pw
    got = []
    monkeypatch.setattr(w.pages, "combine", lambda paths=(): got.append(list(paths)))
    # Explorer starts one Paperleaf per selected file; the running window gathers them
    w.handle_message({"tool": "combine", "paths": [samples["main"]]})
    w.handle_message({"tool": "combine", "paths": [samples["form"]]})
    w.handle_message({"tool": "combine", "paths": [samples["main"]]})
    pump(1000)
    assert got == [[samples["main"], samples["form"]]]
    runs = []
    monkeypatch.setattr(w.pages, "run", lambda name, *a, **k: runs.append(name))
    w.handle_message({"tool": "split", "paths": [samples["form"]]})
    pump(50)
    assert runs == ["split"] and os.path.basename(w._tab().doc.path) == "form.pdf"
    w.handle_message({"paths": [samples["xfa"]], "page": None})
    assert os.path.basename(w._tab().doc.path) == "xfa.pdf"


def test_registry_tool_entries():
    from paperleaf.winshell import registry
    entries = registry.tool_entries()
    verbs = [v for k, n, v in entries if n == "MUIVerb"]
    assert verbs[:5] == ["Paperleaf", "Combine files", "Split…", "Extract pages…", "Rotate pages…"]
    cmds = [v for k, n, v in entries if k.endswith("command")]
    assert all(c.endswith('"%1"') and "--tool" in c for c in cmds)
    assert ("Paperleaf.tools", "SubCommands", "") in entries


def test_command_line_tool_argument():
    from paperleaf.app import parse_args
    a = parse_args(["--tool", "combine", "a.pdf", "b.pdf"])
    assert a.tool == "combine" and a.files == ["a.pdf", "b.pdf"]
