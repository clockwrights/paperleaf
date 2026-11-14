"""Screenshot the annotation features (manual visual check). Usage: python tools/shot_annot.py OUTDIR PDF"""
import os
import shutil
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pymupdf  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from paperleaf.app import apply_theme  # noqa: E402
from paperleaf.core import annotations as an  # noqa: E402
from paperleaf.core.settings import Settings  # noqa: E402
from paperleaf.ui import stamps  # noqa: E402
from paperleaf.ui.main_window import MainWindow  # noqa: E402


def pump(ms=500):
    end = time.time() + ms / 1000
    while time.time() < end:
        QApplication.processEvents()
        time.sleep(0.01)


def main():
    out, src = sys.argv[1], sys.argv[2]
    theme = sys.argv[3] if len(sys.argv) > 3 else "light"
    os.makedirs(out, exist_ok=True)
    work = os.path.join(tempfile.mkdtemp(), "annotated.pdf")
    shutil.copy(src, work)
    app = QApplication([])
    apply_theme(app, theme)
    s = Settings(tempfile.mkdtemp())
    s.set("author", "Reviewer")
    s.set("show_annotate", True)
    w = MainWindow(s, apply_theme=lambda n: apply_theme(app, n))
    w.resize(1500, 1000)
    w.show()
    pump(300)
    w.open_file(work)
    pump(500)
    v = w._view()
    d = v.doc
    st = w.env.style
    au = "Reviewer"
    pt = d.page_text(0)
    an.add_markup_ranges(d, [(0, 19, 60)], "highlight", st("highlight"), au)
    an.add_markup_ranges(d, [(0, 120, 170)], "underline", st("underline"), au)
    an.add_markup_ranges(d, [(0, 200, 240)], "strikeout", st("strikeout"), au)
    an.add_markup_ranges(d, [(0, 260, 300)], "squiggly", st("squiggly"), au)
    n = an.add_note(d, 0, (540, 110), "Please double-check this paragraph.", st("note"), au)
    an.reply(d, 0, n, "Done, looks right now.", "Alex")
    an.set_status(d, 0, n, "Accepted", "Alex")
    an.add_freetext(d, 0, (80, 470, 290, 500), "Text box comment", st("freetext"), au)
    an.add_freetext(d, 0, (330, 470, 520, 500), "Callout", st("callout"), au, callout=((300, 540), (330, 485)))
    an.add_ink(d, 0, [[(90, 560), (120, 540), (150, 575), (180, 545), (210, 570)]], st("pen"), au)
    an.add_ink(d, 0, [[(250, 560), (400, 560)]], st("highlighter"), au)
    an.add_shape(d, 0, "rect", [(80, 600), (180, 660)], st("rect"), au)
    an.add_shape(d, 0, "ellipse", [(200, 600), (300, 660)], st("ellipse"), au)
    an.add_shape(d, 0, "arrow", [(320, 660), (420, 600)], st("arrow"), au)
    an.add_shape(d, 0, "cloud", [(440, 600), (540, 610), (520, 670), (450, 660)], st("cloud"), au)
    for i, kind in enumerate(("check", "cross", "dot")):
        an.add_symbol(d, 0, (100 + i * 30, 700), kind, st(kind), au)
    an.add_freetext(d, 0, (200, 690, 300, 710), "Jane Doe", st("filltext"), au, border=False, subject="Fill & Sign")
    label, color, dyn = stamps.STAMPS[1]
    img = stamps.render_stamp(label, color, stamps.dynamic_line(au))
    w_, h_ = stamps.size_in_points(img)
    an.add_image_stamp(d, 0, (360, 690, 360 + w_, 690 + h_), stamps.to_png(img), au, subject=label)
    sig = stamps.typed_signature("Jane Doe", stamps.available_script_fonts()[0], stamps.QColor(20, 50, 160))
    sw = 120
    sh = sw * sig.height() / sig.width()
    an.add_image_stamp(d, 0, (90, 740, 90 + sw, 740 + sh), stamps.to_png(sig), au, subject="Signature")
    v.fit_width()
    pump(400)
    # select the rectangle to show handles
    rect = next(r for r in an.page_annots(d, 0) if r.kind == "Square")
    v.annots.select(0, rect.xref)
    w._tab().show_panel("comments")
    pump(300)
    v.verticalScrollBar().setValue(int(v._rects[0].top() + v._rects[0].height() * 0.0))
    pump(800)
    w.grab().save(os.path.join(out, f"annot_top_{theme}.png"))
    v.verticalScrollBar().setValue(int(v._rects[0].top() + v._rects[0].height() * 0.45))
    pump(800)
    w.grab().save(os.path.join(out, f"annot_bottom_{theme}.png"))
    # inline editor
    w._set_tool("freetext")
    v.annots._open_editor(0, None, pymupdf.Rect(300, 740, 500, 770), st("freetext"), "freetext", True, None,
                          "Typing in a text box…")
    pump(300)
    w.grab().save(os.path.join(out, f"annot_editor_{theme}.png"))
    v.annots.editor.finish(False)
    for t in [w.tabs.widget(i) for i in range(w.tabs.count())]:
        t.doc._saved_step = t.doc._step()
    w.close()


if __name__ == "__main__":
    main()
