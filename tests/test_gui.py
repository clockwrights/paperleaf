import os
import time

import pytest
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QApplication

from paperleaf.core.document import make_search_regex
from paperleaf.core.settings import Settings
from paperleaf.ui.printing import parse_ranges


def pump(ms=300):
    end = time.time() + ms / 1000
    while time.time() < end:
        QApplication.processEvents()
        time.sleep(0.005)


@pytest.fixture
def win(qapp, tmp_path):
    from paperleaf.ui.main_window import MainWindow
    w = MainWindow(Settings(str(tmp_path)))
    w.resize(1300, 900)
    w.show()
    pump(100)
    yield w
    w.close()


def test_open_navigate_zoom(win, samples):
    assert win.open_file(samples["main"])
    pump(300)
    v = win._view()
    assert v.zoom_mode == "fit_width"
    r = v._rects[0]
    vw = v.viewport().width()
    assert abs(r.width() - (vw - 24)) < 2, "fit width fills the view"
    assert r.left() - v.horizontalScrollBar().value() >= 0, "current page is centred, not cut off"
    v.go_to_page(4)
    pump(100)
    assert v.current_page() == 4
    assert win.page_edit.text() == "3"  # page labels: i, ii, 1, 2, 3
    v.zoom_in()
    assert v.zoom_mode is None and v.zoom > 0
    z = v.zoom
    v.zoom_out()
    assert v.zoom < z
    v.fit_page()
    pump(100)
    assert v._rects[v.current_page()].height() <= v.viewport().height()
    # opening the same file again switches to the existing tab
    assert win.open_file(samples["main"]) and win.tabs.count() == 1


def test_history_and_links(win, samples):
    win.open_file(samples["main"])
    pump(200)
    v = win._view()
    link = v.doc.links(0)[0]
    win._link(v, link)
    pump(100)
    assert v.current_page() == 9
    assert v.can_go_back()
    v.go_back()
    pump(100)
    assert v.current_page() == 0


def test_layouts_and_rotation(win, samples):
    win.open_file(samples["main"])
    pump(200)
    v = win._view()
    for mode in ("single", "two", "two_continuous", "single_continuous"):
        v.set_layout(mode)
        pump(50)
        assert win.layout_actions[mode].isChecked()
    v.set_layout("single")
    shown = [i for i, r in enumerate(v._rects) if not r.isNull()]
    assert shown == [v.current_page()]
    v.next_page()
    assert [i for i, r in enumerate(v._rects) if not r.isNull()] == [shown[0] + 1]
    v.set_layout("two", cover=True)
    v.go_to_page(0)
    assert [i for i, r in enumerate(v._rects) if not r.isNull()] == [0]
    v.next_page()
    assert [i for i, r in enumerate(v._rects) if not r.isNull()] == [1, 2]
    v.set_layout("single_continuous", cover=False)
    v.rotate_view(90)
    assert v.rotation == 90
    w, h = v._rects[0].width(), v._rects[0].height()
    assert w > h


def test_search_and_copy(win, samples):
    win.open_file(samples["main"])
    pump(200)
    t = win._tab()
    t.open_find()
    t.findbar.edit.setText("lazy dog")
    t.findbar._run()
    for _ in range(100):
        pump(20)
        if t.view.search_finished():
            break
    total = t.view.total_hits()
    assert total == sum(len(t.doc.search_page(p, make_search_regex("lazy dog", False, False)))
                        for p in range(t.doc.page_count))
    assert total > 10
    assert t.findbar.count.text().startswith("1 of")
    t.view.search_next()
    assert t.findbar.count.text().startswith("2 of")
    t.view.search_next(backward=True)
    assert t.findbar.count.text().startswith("1 of")
    assert t.results.list.count() == total
    # selection + copy
    t.view.go_to_page(0)
    pt = t.doc.page_text(0)
    t.view._set_selection((0, 0), (0, 18))
    assert t.view.copy_selection()
    assert QGuiApplication.clipboard().text() == "Chapter 1 - Page 1"
    t.view.select_all()
    assert t.view.selected_text().count("Chapter") == 12


def test_password_prompt_cancel(win, samples, monkeypatch):
    from PySide6.QtWidgets import QInputDialog
    monkeypatch.setattr(QInputDialog, "getText", staticmethod(lambda *a, **k: ("", False)))
    assert not win.open_file(samples["encrypted"])
    monkeypatch.setattr(QInputDialog, "getText", staticmethod(lambda *a, **k: ("secret", True)))
    assert win.open_file(samples["encrypted"])


def test_position_is_remembered(qapp, tmp_path, samples):
    from paperleaf.ui.main_window import MainWindow
    s = Settings(str(tmp_path))
    w = MainWindow(s)
    w.resize(1200, 800)
    w.show()
    pump(100)
    w.open_file(samples["main"])
    pump(200)
    w._view().go_to_page(6)
    pump(50)
    w.close_all()
    w.open_file(samples["main"])
    pump(300)
    assert w._view().current_page() == 6
    w.close()


def test_page_ranges():
    assert parse_ranges("1-3, 5", 10) == [0, 1, 2, 4]
    assert parse_ranges("8-", 10) == [7, 8, 9]
    assert parse_ranges("-2", 10) == [0, 1]
    assert parse_ranges("0", 10) is None
    assert parse_ranges("11", 10) is None
    assert parse_ranges("a", 10) is None


def test_print_dialog_builds(win, samples):
    from paperleaf.ui.printing import PrintDialog
    win.open_file(samples["main"])
    pump(100)
    t = win._tab()
    dlg = PrintDialog(t.doc, 2, win)
    dlg.r_range.setChecked(True)
    dlg.range_edit.setText("2-4")
    dlg.subset.setCurrentIndex(1)
    assert dlg.pages() == [2]
    dlg.r_cur.setChecked(True)
    assert dlg.pages() == [2]
    dlg.deleteLater()


@pytest.mark.skipif(os.name != "nt", reason="Windows Explorer integration")
def test_registry_commands():
    from paperleaf.winshell import registry
    cmd = registry.app_command()
    assert cmd[-1].endswith("paperleaf.pyw") and os.path.exists(cmd[-1])
    app_icon, doc_icon = registry.icon_paths()
    assert os.path.exists(app_icon.rsplit(",", 1)[0]) and os.path.exists(doc_icon.rsplit(",", 1)[0])
