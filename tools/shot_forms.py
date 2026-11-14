"""Screenshot the form features (manual visual check). Usage: python tools/shot_forms.py OUTDIR [theme]"""
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtWidgets import QApplication  # noqa: E402

from paperleaf.app import apply_theme  # noqa: E402
from paperleaf.core import forms as F  # noqa: E402
from paperleaf.core.settings import Settings  # noqa: E402
from paperleaf.ui.main_window import MainWindow  # noqa: E402
from tests.samples import make_form  # noqa: E402


def pump(ms=500):
    end = time.time() + ms / 1000
    while time.time() < end:
        QApplication.processEvents()
        time.sleep(0.01)


def main():
    out = sys.argv[1]
    theme = sys.argv[2] if len(sys.argv) > 2 else "light"
    os.makedirs(out, exist_ok=True)
    work = os.path.join(tempfile.mkdtemp(), "form.pdf")
    make_form(work)
    app = QApplication([])
    apply_theme(app, theme)
    s = Settings(tempfile.mkdtemp())
    w = MainWindow(s, apply_theme=lambda n: apply_theme(app, n))
    w.resize(1400, 1000)
    w.show()
    pump(300)
    w.open_file(work)
    pump(500)
    v = w._view()
    d = v.doc
    g = lambda n, i=0: [f for f in F.all_fields(d) if f.name == n][i]  # noqa: E731
    F.set_value(d, g("name"), "Jane Doe")
    F.toggle(d, g("agree"))
    F.set_value(d, g("langs"), ["Spanish", "German"])
    v.set_zoom(1.25)
    v.go_to_page(0)
    pump(300)
    v.forms.open_editor(g("notes"))
    v.forms.editor.insertPlainText("Typing in a multi-line field")
    pump(300)
    w.grab().save(os.path.join(out, f"forms_fill_{theme}.png"))
    v.forms.commit()
    w.toggle_prepare(True)
    v.forms.select([(0, g("zip").xref)])
    pump(400)
    w.grab().save(os.path.join(out, f"forms_prepare_{theme}.png"))
    w.toggle_prepare(False)
    for i in range(w.tabs.count()):
        w.tabs.widget(i).doc._saved_step = w.tabs.widget(i).doc._step()
    w.close()


if __name__ == "__main__":
    main()
