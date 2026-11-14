"""Open files in the main window, run a few UI steps, and save screenshots.

Usage: python tools/shot.py OUTDIR FILE [step ...]
Steps: wait, find:TEXT, zoom:N, page:N, layout:MODE, rotate, night, sidebar:KEY, select, shot:NAME
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtCore import QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from paperleaf.app import apply_theme  # noqa: E402
from paperleaf.core.settings import Settings  # noqa: E402
from paperleaf.ui.main_window import MainWindow  # noqa: E402


def pump(ms=600):
    end = time.time() + ms / 1000
    while time.time() < end:
        QApplication.processEvents()
        time.sleep(0.01)


def main():
    out, path, *steps = sys.argv[1:]
    os.makedirs(out, exist_ok=True)
    app = QApplication([])
    cfg = os.path.join(out, "cfg")
    os.makedirs(cfg, exist_ok=True)
    s = Settings(cfg)
    theme = next((st.split(":", 1)[1] for st in steps if st.startswith("theme:")), "light")
    apply_theme(app, theme)
    w = MainWindow(s, apply_theme=lambda n: apply_theme(app, n))
    w.resize(1400, 900)
    w.show()
    pump(300)
    if path != "-":
        w.open_file(path)
    pump(800)
    for st in steps:
        name, _, arg = st.partition(":")
        t = w._tab()
        v = t.view if t else None
        if name == "find":
            t.findbar.open_bar(arg)
            t.findbar._run()
            t.show_panel("search")
        elif name == "zoom":
            v.set_zoom(float(arg) / 100)
        elif name == "page":
            v.go_to_page(int(arg) - 1)
        elif name == "layout":
            v.set_layout(arg)
        elif name == "rotate":
            w._rotate(90)
        elif name == "night":
            w.a["night"].setChecked(True)
            w.toggle_night()
        elif name == "sidebar":
            t.show_panel(arg)
        elif name == "select":
            v.select_all()
        elif name == "selectline":
            pt = v.doc.page_text(v.current_page())
            v._set_selection((v.current_page(), 0), (v.current_page(), min(120, len(pt.text))))
        elif name == "close":
            w.close_all()
        elif name == "shot":
            pump(900)
            w.grab().save(os.path.join(out, arg + ".png"))
            continue
        pump(700)
    w.close()


if __name__ == "__main__":
    main()
