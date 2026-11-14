"""Drive form filling and Prepare Form with simulated mouse and keyboard input."""
import shutil

import pymupdf
import pytest
from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtWidgets import QFileDialog, QMessageBox

from paperleaf.core import annotations as an
from paperleaf.core import forms as F
from paperleaf.core import recovery
from paperleaf.core.settings import Settings
from paperleaf.viewer import form_tools as FT
from tests.test_annot_gui import click, drag, key, pump, send_mouse, vpos

SHIFT = Qt.KeyboardModifier.ShiftModifier


@pytest.fixture
def fw(qapp, tmp_path, samples):
    from paperleaf.ui.main_window import MainWindow
    path = tmp_path / "form.pdf"
    shutil.copy(samples["form"], path)
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
    yield w, v, str(path)
    for i in range(w.tabs.count()):
        recovery.discard(w.tabs.widget(i).doc.path)
        w.tabs.widget(i).doc._saved_step = w.tabs.widget(i).doc._step()  # no "save changes?" prompt
    w.close()


def fld(doc, name, i=0):
    return [f for f in F.all_fields(doc) if f.name == name][i]


def centre(v, f):
    r = f.rect
    return vpos(v, f.pno, (r.x0 + r.x1) / 2, (r.y0 + r.y1) / 2)


def type_text(widget, text):
    for ch in text:
        if ch == "\n":
            key(widget, Qt.Key.Key_Return, text="\r")
        else:
            key(widget, Qt.Key.Key_A, text=ch)
    pump(10)


def test_text_fields_typing_and_tab_order(fw):
    w, v, _ = fw
    d = v.doc
    click(v, centre(v, fld(d, "name")))
    ed = v.forms.editor
    assert isinstance(ed, FT.LineEditor) and ed.isVisible()
    vr = v.text_rect_to_view(0, fld(d, "name").rect)
    assert abs(ed.geometry().left() - vr.left()) <= 1 and abs(ed.geometry().width() - vr.width()) <= 2
    type_text(ed, "Jane Doe")
    key(ed, Qt.Key.Key_Tab)
    assert fld(d, "name").value == "Jane Doe" and fld(d, "name", 1).value == "Jane Doe"
    assert d.undo_name() == "Fill in field"
    ed = v.forms.editor
    assert isinstance(ed, FT.TextEditor) and ed.field.name == "notes"
    type_text(ed, "first\nsecond")
    key(ed, Qt.Key.Key_Backtab, SHIFT)
    assert fld(d, "notes").value == "first\nsecond"
    assert v.forms.editor.field.name == "name"
    # Esc cancels typing
    type_text(v.forms.editor, " X")
    key(v.forms.editor, Qt.Key.Key_Escape)
    assert v.forms.editor is None and fld(d, "name").value == "Jane Doe"
    # the comb field keeps at most 5 characters
    click(v, centre(v, fld(d, "zip")))
    type_text(v.forms.editor, "1234567")
    key(v.forms.editor, Qt.Key.Key_Return)
    assert fld(d, "zip").value == "12345"
    # clicking elsewhere commits too
    click(v, centre(v, fld(d, "qty")))
    type_text(v.forms.editor, "3")
    click(v, vpos(v, 0, 500, 700))
    assert v.forms.editor is None and fld(d, "qty").value == "3"
    w.undo()
    assert fld(d, "qty").value == ""
    assert w.tabs.tabText(0).endswith("*")


def test_tab_skips_read_only_and_goes_to_next_page(fw):
    w, v, _ = fw
    d = v.doc
    stops = [f.name for f in v.forms._tab_stops()]
    assert "member_id" not in stops and "total" not in stops and "reset" not in stops
    assert stops.count("colour") == 1
    v.setFocus()
    key(v, Qt.Key.Key_Tab)
    assert v.forms.editor.field.name == "name"
    key(v.forms.editor, Qt.Key.Key_Tab)   # notes
    key(v.forms.editor, Qt.Key.Key_Tab)   # zip
    key(v.forms.editor, Qt.Key.Key_Tab)   # agree (a check box has no editor)
    assert v.forms.editor is None and v.forms.focus == (0, fld(d, "agree").xref)
    key(v, Qt.Key.Key_Space)
    assert fld(d, "agree").is_on
    key(v, Qt.Key.Key_Tab)                # country
    assert isinstance(v.forms.editor, FT.ComboEditor)
    v.forms.commit()
    sig = fld(d, "signature")
    v.forms.focus = (0, sig.xref)
    key(v, Qt.Key.Key_Tab)                # first field on page 2
    assert v.forms.editor.field.pno == 1 and v.forms.editor.field.name == "name"
    key(v.forms.editor, Qt.Key.Key_Backtab, SHIFT)
    assert v.forms.focus == (0, sig.xref)


def test_checkboxes_and_radio_buttons(fw):
    w, v, _ = fw
    d = v.doc
    click(v, centre(v, fld(d, "agree")))
    assert fld(d, "agree").is_on
    click(v, centre(v, fld(d, "agree")))
    assert not fld(d, "agree").is_on
    click(v, centre(v, fld(d, "colour", 2)))
    assert [f.is_on for f in F.group(d, fld(d, "colour"))] == [False, False, True]
    key(v, Qt.Key.Key_Left)  # arrows move within the group
    assert fld(d, "colour", 1).is_on and v.forms.focus == (0, fld(d, "colour", 1).xref)
    w.undo()
    w.undo()
    assert fld(d, "colour", 1).is_on


def test_read_only_and_no_fill_permission(fw, samples, tmp_path):
    w, v, _ = fw
    d = v.doc
    click(v, centre(v, fld(d, "member_id")))
    assert v.forms.editor is None
    assert "read-only" in w.statusBar().currentMessage()
    # a document that does not allow filling in forms
    p = tmp_path / "locked.pdf"
    src = pymupdf.open(samples["form"])
    src.save(str(p), encryption=pymupdf.PDF_ENCRYPT_AES_256, owner_pw="o", user_pw="",
             permissions=pymupdf.PDF_PERM_PRINT)
    src.close()
    assert w.open_file(str(p))
    pump(200)
    v2 = w._view()
    v2.set_zoom(1.0)
    v2.go_to_page(0)
    click(v2, centre(v2, fld(v2.doc, "name")))
    assert v2.forms.editor is None and not v2.doc.modified
    assert "does not allow filling" in w._tab().formbar.note.text()


def test_combo_boxes_and_list_boxes(fw):
    w, v, _ = fw
    d = v.doc
    click(v, centre(v, fld(d, "country")))
    ed = v.forms.editor
    assert isinstance(ed, FT.ComboEditor) and not ed.isEditable()
    pump(50)
    ed.hidePopup()
    pump(20)
    key(ed, Qt.Key.Key_Down)
    key(ed, Qt.Key.Key_Return)
    assert fld(d, "country").value == "France"
    click(v, centre(v, fld(d, "city")))
    ed = v.forms.editor
    assert ed.isEditable()
    ed.lineEdit().selectAll()
    type_text(ed.lineEdit(), "Lyon")
    key(ed.lineEdit(), Qt.Key.Key_Tab)
    assert fld(d, "city").value == "Lyon"
    ed = v.forms.editor
    assert isinstance(ed, FT.ListEditor) and ed.field.name == "langs"
    vp = ed.viewport()
    r1, r3 = ed.visualItemRect(ed.item(1)), ed.visualItemRect(ed.item(3))
    send_mouse(vp, QEvent.Type.MouseButtonPress, QPointF(r1.center()))
    send_mouse(vp, QEvent.Type.MouseButtonRelease, QPointF(r1.center()))
    ctrl = Qt.KeyboardModifier.ControlModifier
    send_mouse(vp, QEvent.Type.MouseButtonPress, QPointF(r3.center()), mods=ctrl)
    send_mouse(vp, QEvent.Type.MouseButtonRelease, QPointF(r3.center()), mods=ctrl)
    key(ed, Qt.Key.Key_Return)
    assert fld(d, "langs").value == ["Spanish", "German"]


def test_editor_follows_zoom_and_scroll(fw):
    w, v, _ = fw
    d = v.doc
    click(v, centre(v, fld(d, "notes")))
    ed = v.forms.editor
    w0 = ed.width()
    v.zoom_in()
    pump(50)
    assert v.forms.editor is ed and ed.width() > w0 * 1.1
    vr = v.text_rect_to_view(0, fld(d, "notes").rect)
    assert abs(ed.geometry().top() - vr.top()) <= 2
    v.verticalScrollBar().setValue(v.verticalScrollBar().value() + 40)
    pump(30)
    vr = v.text_rect_to_view(0, fld(d, "notes").rect)
    assert abs(ed.geometry().top() - vr.top()) <= 2


def choose_menu_item(monkeypatch, w, text):
    """Make the window's next popup menu pick the item whose text starts with ``text``."""
    shown = []

    def fake_popup(menu):
        items = [act for act in menu.actions() if act.text()]
        shown.append([act.text() for act in items])
        return next((act for act in items if act.text().startswith(text)), None)
    monkeypatch.setattr(w, "popup_menu", fake_popup)
    return shown


def test_signature_field_places_saved_signature(fw, tmp_path, monkeypatch):
    w, v, _ = fw
    shown = choose_menu_item(monkeypatch, w, "Place Signature Image")
    d = v.doc
    from paperleaf.ui import stamps
    img = stamps.typed_signature("Jane Doe", stamps.available_script_fonts()[0], stamps.QColor("black"))
    sig = str(tmp_path / "sig.png")
    img.save(sig)
    w.env.current_signature = sig
    field = fld(d, "signature")
    click(v, centre(v, field))
    stamp = [r for r in an.page_annots(d, 0) if r.kind == "Stamp"]
    assert len(stamp) == 1 and stamp[0].subject == "Signature"
    r = stamp[0].rect
    assert field.rect.x0 - 1 <= r.x0 and r.x1 <= field.rect.x1 + 1 and field.rect.y0 - 1 <= r.y0
    assert d.undo_name() == "Sign"
    assert shown == [["Sign with Certificate…", "Place Signature Image"]]


def test_form_bar_highlight_clear_and_reset_button(fw, monkeypatch):
    w, v, _ = fw
    d = v.doc
    t = w._tab()
    bar = t.formbar
    assert bar.isVisible() and "fillable form fields" in bar.text.text()
    assert "JavaScript" in bar.note.text()
    assert bar.highlight_btn.isChecked() and v.forms.highlight
    bar.highlight_btn.click()
    assert not v.forms.highlight and not w.settings.get("highlight_fields")
    assert not w.a["highlight_fields"].isChecked()
    bar.highlight_btn.click()
    assert v.forms.highlight
    F.set_value(d, fld(d, "name"), "Someone")
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes))
    bar.clear_btn.click()
    assert fld(d, "name").value == "" and d.undo_name() == "Clear form"
    F.set_value(d, fld(d, "name"), "Again")
    click(v, centre(v, fld(d, "reset")))  # the form's own Reset button (no JavaScript involved)
    assert fld(d, "name").value == ""


def test_xfa_note(fw, samples):
    w, v, _ = fw
    assert w.open_file(samples["xfa"])
    pump(100)
    assert "XFA" in w._tab().formbar.note.text() and "partly" in w._tab().formbar.note.text()


def test_flatten_import_export_from_menu(fw, tmp_path, monkeypatch):
    w, v, _ = fw
    d = v.doc
    F.set_value(d, fld(d, "name"), "Menu Jane")
    out = str(tmp_path / "data.xfdf")
    monkeypatch.setattr(QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: (out, "XFDF (*.xfdf)")))
    w.a["export_form"].trigger()
    assert F.read_data_file(out)["name"] == "Menu Jane"
    F.reset_form(d)
    monkeypatch.setattr(QFileDialog, "getOpenFileName", staticmethod(lambda *a, **k: (out, "")))
    w.a["import_form"].trigger()
    assert fld(d, "name").value == "Menu Jane"
    w.a["flatten_form"].trigger()
    assert F.all_fields(d) == [] and not w._tab().formbar.isVisible()
    assert not w.a["flatten_form"].isEnabled()
    w.undo()
    assert len(F.all_fields(d)) == 18 and w._tab().formbar.isVisible()


def test_prepare_form_add_move_resize_delete(fw):
    w, v, _ = fw
    d = v.doc
    w.a["prepare_form"].trigger()
    assert v.forms.prepare and w.prepare_bar.isVisible() and not w._tab().formbar.isVisible()
    w._set_tool("field_text")
    drag(v, vpos(v, 0, 300, 640), vpos(v, 0, 460, 662))
    f = next(f for f in F.page_fields(d, 0) if f.name == "Text1")
    assert abs(f.rect.x0 - 300) < 2 and abs(f.rect.y1 - 662) < 2
    assert v.tool == "select" and v.forms.sel == [(0, f.xref)]
    assert w.a["field_props"].isEnabled() and w.a["delete_field"].isEnabled()
    # move it
    drag(v, vpos(v, 0, 380, 650), vpos(v, 0, 400, 680))
    g = F.find(d, 0, f.xref)
    assert abs(g.rect.x0 - 320) < 2 and abs(g.rect.y0 - 670) < 2
    # resize with the bottom-right handle
    drag(v, vpos(v, 0, g.rect.x1, g.rect.y1), vpos(v, 0, g.rect.x1 + 40, g.rect.y1 + 20))
    h = F.find(d, 0, f.xref)
    assert abs(h.rect.x1 - (g.rect.x1 + 40)) < 2 and abs(h.rect.y1 - (g.rect.y1 + 20)) < 2
    # clicking in Prepare Form selects instead of filling
    click(v, centre(v, fld(d, "name")))
    assert v.forms.editor is None and v.forms.sel == [(0, fld(d, "name").xref)]
    click(v, centre(v, h))
    key(v, Qt.Key.Key_Delete)
    assert F.find(d, 0, f.xref) is None
    w.undo()
    assert F.find(d, 0, f.xref) is not None
    # radio tool keeps adding to one group
    w._set_tool("field_radio")
    click(v, vpos(v, 0, 450, 700))
    click(v, vpos(v, 0, 480, 700))
    radios = [f for f in F.page_fields(d, 0) if f.kind == F.RADIO and f.name.startswith("Group")]
    assert len(radios) == 2 and radios[0].name == radios[1].name == "Group1"
    w._set_tool("field_check")
    click(v, vpos(v, 0, 450, 730))
    assert any(f.name == "Check Box1" for f in F.page_fields(d, 0))
    w.a["prepare_form"].trigger()
    assert not v.forms.prepare and not w.prepare_bar.isVisible()


def test_properties_dialog(fw, monkeypatch):
    w, v, _ = fw
    d = v.doc
    from paperleaf.ui.form_dialogs import FieldPropertiesDialog
    dlg = FieldPropertiesDialog(d, fld(d, "country"), w)
    dlg.name.setText("name")           # taken
    dlg._ok()
    assert not dlg.error.isHidden() and "already" in dlg.error.text()
    dlg.name.setText("land")
    dlg.tooltip.setText("Where you live")
    dlg.required.setChecked(True)
    dlg.options.setPlainText("Spain | ES\nFrance | FR\nPortugal | PT")
    dlg.default.setCurrentIndex(dlg.default.findData("PT"))
    dlg.editable.setChecked(True)
    dlg.font.setEditText("14")
    dlg._ok()
    assert dlg.result() == dlg.DialogCode.Accepted
    c = fld(d, "land")
    assert c.tooltip == "Where you live" and c.required and c.editable and c.fontsize == 14
    assert c.options == [("ES", "Spain"), ("FR", "France"), ("PT", "Portugal")] and c.default == "PT"
    assert d.undo_name() == "Change field properties"
    dlg = FieldPropertiesDialog(d, fld(d, "zip"), w)
    assert dlg.comb.isEnabled()
    dlg.multiline.setChecked(True)
    assert not dlg.comb.isEnabled()
    dlg.maxlen.setValue(8)
    dlg._ok()
    z = fld(d, "zip")
    assert z.multiline and z.maxlen == 8 and not z.comb
    # double-clicking a field in Prepare Form opens the dialog
    w.toggle_prepare(True)
    opened = []
    monkeypatch.setattr(FieldPropertiesDialog, "exec", lambda self: opened.append(self.field.name) or 0)
    send_mouse(v.viewport(), QEvent.Type.MouseButtonDblClick, centre(v, fld(d, "notes")))
    assert opened == ["notes"]


def test_save_and_reopen_keeps_values(fw):
    w, v, path = fw
    d = v.doc
    click(v, centre(v, fld(d, "name")))
    type_text(v.forms.editor, "Saved Jane")
    assert w.save()  # saving commits the open editor first
    re = pymupdf.open(path)
    assert next(wd.field_value for wd in re[0].widgets() if wd.field_name == "name") == "Saved Jane"
    re.close()
