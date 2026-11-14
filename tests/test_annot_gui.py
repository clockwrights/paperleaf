"""Drive the annotation tools with simulated mouse/keyboard input."""
import os
import shutil
import time

import pymupdf
import pytest
from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QKeyEvent, QMouseEvent
from PySide6.QtWidgets import QApplication, QMessageBox

from paperleaf.core import annotations as an
from paperleaf.core import recovery
from paperleaf.core.settings import Settings


def pump(ms=120):
    end = time.time() + ms / 1000
    while time.time() < end:
        QApplication.processEvents()
        time.sleep(0.005)


def send_mouse(w, kind, pos, button=Qt.MouseButton.LeftButton, buttons=None, mods=Qt.KeyboardModifier.NoModifier):
    if buttons is None:
        buttons = button if kind != QEvent.Type.MouseButtonRelease else Qt.MouseButton.NoButton
    if kind == QEvent.Type.MouseMove:
        button = Qt.MouseButton.NoButton
    ev = QMouseEvent(kind, QPointF(pos), QPointF(w.mapToGlobal(QPoint(int(pos.x()), int(pos.y())))),
                     button, buttons, mods)
    QApplication.sendEvent(w, ev)


def drag(view, a: QPointF, b: QPointF, steps=6):
    vp = view.viewport()
    send_mouse(vp, QEvent.Type.MouseButtonPress, a)
    for i in range(1, steps + 1):
        p = a + (b - a) * (i / steps)
        send_mouse(vp, QEvent.Type.MouseMove, p, buttons=Qt.MouseButton.LeftButton)
    send_mouse(vp, QEvent.Type.MouseButtonRelease, b)
    pump(60)


def click(view, p: QPointF):
    drag(view, p, p, steps=1)


def key(widget, k, mods=Qt.KeyboardModifier.NoModifier, text=""):
    QApplication.sendEvent(widget, QKeyEvent(QEvent.Type.KeyPress, k, mods, text))
    QApplication.sendEvent(widget, QKeyEvent(QEvent.Type.KeyRelease, k, mods, text))


def vpos(view, pno, x, y) -> QPointF:
    """View position of a text-space point."""
    p = pymupdf.Point(x, y) * view.doc.rotation_matrix(pno)
    return view.page_to_content(pno, p.x, p.y) - view._offset()


@pytest.fixture
def setup(qapp, tmp_path, samples):
    from paperleaf.ui.main_window import MainWindow
    path = tmp_path / "doc.pdf"
    shutil.copy(samples["main"], path)
    s = Settings(str(tmp_path / "cfg"))
    w = MainWindow(s)
    w.resize(1300, 950)
    w.show()
    pump(100)
    assert w.open_file(str(path))
    pump(250)
    v = w._view()
    v.set_zoom(1.0)
    v.go_to_page(0)
    pump(100)
    yield w, v, str(path)
    for i in range(w.tabs.count()):
        recovery.discard(w.tabs.widget(i).doc.path)
    w.tabs.clear()
    w.close()


def kinds(doc, pno=0):
    return [r.kind for r in an.page_annots(doc, pno) if not r.is_reply]


def test_draw_rect_select_move_delete_undo(setup):
    w, v, _ = setup
    w._set_tool("rect")
    drag(v, vpos(v, 0, 100, 600), vpos(v, 0, 250, 700))
    assert kinds(v.doc) == ["Square"]
    rec = an.page_annots(v.doc, 0)[0]
    assert abs(rec.rect.x0 - 100) < 3 and abs(rec.rect.y1 - 700) < 3
    assert w.tabs.tabText(0).endswith("*")  # unsaved marker
    w._set_tool("select")
    # click the border to select, then drag it
    drag(v, vpos(v, 0, 100, 650), vpos(v, 0, 140, 670))
    assert v.annots.sel == (0, rec.xref)
    moved = an.find(v.doc, 0, rec.xref)
    assert abs(moved.rect.x0 - (rec.rect.x0 + 40)) < 2 and abs(moved.rect.y0 - (rec.rect.y0 + 20)) < 2
    key(v, Qt.Key.Key_Delete)
    assert kinds(v.doc) == []
    w.undo()
    assert kinds(v.doc) == ["Square"]
    w.undo()
    w.undo()
    assert kinds(v.doc) == [] and not v.doc.modified
    assert not w.tabs.tabText(0).endswith("*")


def test_highlight_by_dragging_text(setup):
    w, v, _ = setup
    w._set_tool("highlight")
    pt = v.doc.page_text(0)
    x0, y0, x1, y1 = pt.lines[1]
    drag(v, vpos(v, 0, x0 + 1, (y0 + y1) / 2), vpos(v, 0, x0 + 120, (y0 + y1) / 2))
    recs = an.page_annots(v.doc, 0)
    assert [r.kind for r in recs] == ["Highlight"]
    assert an.markup_text(v.doc, recs[0]).startswith("The quick")
    assert not v.has_selection()
    assert v.tool == "highlight"  # markup tools stay active


def test_highlight_from_selection_menu(setup):
    w, v, _ = setup
    v._set_selection((0, 0), (0, 9))
    w._markup_selection("underline")
    assert kinds(v.doc) == ["Underline"]


def test_text_box_typing(setup):
    w, v, _ = setup
    w._set_tool("freetext")
    click(v, vpos(v, 0, 120, 650))
    ed = v.annots.editor
    assert ed is not None and ed.isVisible()
    ed.insertPlainText("Hello\nsecond line")
    ed.finish(True)
    pump(50)
    recs = an.page_annots(v.doc, 0)
    assert [r.kind for r in recs] == ["FreeText"]
    assert recs[0].contents == "Hello\nsecond line"
    assert recs[0].rect.height > 2 * recs[0].fontsize
    assert v.tool == "select" and v.annots.sel == (0, recs[0].xref)
    # edit it again by double-clicking
    v.annots.edit_text(recs[0])
    v.annots.editor.setPlainText("Changed")
    v.annots.editor.finish(True)
    assert an.page_annots(v.doc, 0)[0].contents == "Changed"


def test_pen_shapes_symbols_stamp_signature(setup, tmp_path):
    w, v, _ = setup
    w._set_tool("pen")
    drag(v, vpos(v, 0, 100, 650), vpos(v, 0, 300, 720), steps=12)
    w._set_tool("arrow")
    drag(v, vpos(v, 0, 300, 600), vpos(v, 0, 450, 650))
    w._set_tool("check")
    click(v, vpos(v, 0, 500, 600))
    w._set_tool("date")
    click(v, vpos(v, 0, 400, 760))
    w._set_tool("polygon")
    vp = v.viewport()
    for x, y in ((100, 400), (200, 400), (150, 480)):
        click(v, vpos(v, 0, x, y))
    send_mouse(vp, QEvent.Type.MouseButtonDblClick, vpos(v, 0, 150, 480))
    pump(30)
    from paperleaf.ui import stamps
    label, color, dyn = stamps.STAMPS[1]
    w.env.current_stamp = ("builtin", label, color, dyn)
    w._set_tool("stamp")
    click(v, vpos(v, 0, 300, 300))
    assert v.tool == "select"
    got = kinds(v.doc)
    assert got == ["Ink", "Line", "FreeText", "FreeText", "Polygon", "Stamp"], got
    stamp = an.page_annots(v.doc, 0)[-1]
    assert stamp.subject == "Approved" and stamp.rect.width > 60
    # signature
    from PySide6.QtGui import QImage
    img = stamps.typed_signature("Jane Doe", stamps.available_script_fonts()[0], stamps.QColor("black"))
    sig = str(tmp_path / "sig.png")
    img.save(sig)
    w.env.current_signature = sig
    w._set_tool("signature")
    click(v, vpos(v, 0, 300, 200))
    assert kinds(v.doc)[-1] == "Stamp" and an.page_annots(v.doc, 0)[-1].subject == "Signature"
    # eraser removes the pen stroke only
    w._set_tool("eraser")
    ink = an.page_annots(v.doc, 0)[0]
    p = ink.vertices[0][len(ink.vertices[0]) // 2]
    click(v, vpos(v, 0, p[0], p[1]))
    assert "Ink" not in kinds(v.doc)


def test_save_close_prompt_and_recovery(setup, monkeypatch):
    w, v, path = setup
    w._set_tool("note")
    monkeypatch.setattr("paperleaf.ui.main_window.CommentDialog.exec", lambda self: 0)
    click(v, vpos(v, 0, 200, 200))
    assert kinds(v.doc) == ["Text"]
    assert w.save()
    assert not v.doc.modified and not w.tabs.tabText(0).endswith("*")
    d = pymupdf.open(path)
    assert [a.type[1] for a in d[0].annots()] == ["Text"]
    d.close()
    # unsaved change + crash-style backup
    w._set_tool("rect")
    drag(v, vpos(v, 0, 100, 500), vpos(v, 0, 200, 560))
    w._backup_all()
    assert recovery.find(path) is not None
    # closing asks; "Cancel" keeps the tab open, "Discard" closes it
    monkeypatch.setattr(QMessageBox, "exec", lambda self: QMessageBox.StandardButton.Cancel)
    assert not w.close_tab(0)
    assert w.tabs.count() == 1
    # simulate a crash: reopen from the backup in a new window
    from paperleaf.ui.main_window import MainWindow
    w2 = MainWindow(Settings(os.path.join(os.path.dirname(path), "cfg2")))
    w2.show()
    w.tabs.widget(0).doc.data  # keep first window's tab alive (like a crashed app that never closed)
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes))
    # open the same path in the second window (first one "crashed")
    assert w2.open_file(path)
    d2 = w2._tab().doc
    assert d2.modified and sorted(kinds(d2)) == ["Square", "Text"]
    monkeypatch.setattr(QMessageBox, "exec", lambda self: QMessageBox.StandardButton.Discard)
    assert w2.close_tab(0)
    assert recovery.find(path) is None
    w2.close()
