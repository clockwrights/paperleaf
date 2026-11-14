"""Edit PDF, Recognize Text, Compare Files, certificate signatures and Read Out
Loud commands for the main window."""
from __future__ import annotations

import os
import shutil
import tempfile

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox, QProgressDialog

from paperleaf.core import ocr
from paperleaf.core import signing as S
from paperleaf.ui import advanced_dialogs as D
from paperleaf.viewer.edit_tools import ADD_IMAGE, ADD_TEXT

IMAGE_FILTER = "Images (*.png *.jpg *.jpeg *.bmp *.gif *.tif *.tiff);;All files (*.*)"


class AdvancedCommands:
    def __init__(self, win):
        self.win = win
        self.editing = False
        self._reader = None
        self._temp_dirs: list[str] = []

    # ------------------------------------------------------------- helpers
    def _tab(self, view=None):
        if view is not None:
            for i in range(self.win.tabs.count()):
                t = self.win.tabs.widget(i)
                if t.view is view:
                    return t
        t = self.win._tab()
        if t is not None:
            t.view.annots.cancel()
            t.view.forms.commit()
            t.view.editing.commit()
        return t

    def _msg(self, text, ms=6000):
        self.win.statusBar().showMessage(text, ms)

    def _warn(self, title, text):
        QMessageBox.warning(self.win, title, text)

    def _progress(self, title, label):
        dlg = QProgressDialog(label, "Cancel", 0, 100, self.win)
        dlg.setWindowTitle(title)
        dlg.setWindowModality(Qt.WindowModality.WindowModal)
        dlg.setMinimumDuration(300)
        dlg.setAutoClose(False)
        dlg.setValue(0)

        def step(i, n):
            dlg.setMaximum(max(1, n))
            dlg.setValue(i)
            QApplication.processEvents()
            return not dlg.wasCanceled()
        return dlg, step

    def cleanup(self):
        for d in self._temp_dirs:
            shutil.rmtree(d, ignore_errors=True)
        self._temp_dirs = []
        if self._reader is not None:
            self._reader.stop()

    # ============================================================ Edit PDF
    def toggle_edit(self, on=None):
        a = self.win.a
        if self.win.sender() is a["edit_pdf"]:
            on = a["edit_pdf"].isChecked()
        elif not isinstance(on, bool):
            on = not self.editing
        t = self._tab()
        if on and (t is None or not t.doc.is_pdf):
            self._msg("Only PDF files can be edited.")
            on = False
        if on and self.win._prepare:
            self.win.toggle_prepare(False)
        if on and self.win.redact_bar.isVisible():
            self.win.toggle_redact_bar(False)
        if on and t is not None and t.organizing():
            self.win.toggle_organize(False)
        self.editing = on
        a["edit_pdf"].setChecked(on)
        self.win.edit_bar.setVisible(on)
        for i in range(self.win.tabs.count()):
            self.win.tabs.widget(i).view.editing.set_active(on)
        v = self.win._view()
        if on and v is not None:
            if v.tool != "select":
                self.win._set_tool("select")
            if not v.doc.can_modify():
                self._msg("This document does not allow changes.")
            else:
                self._msg("Click text to change it, or click an image to move or resize it.", 8000)
        self.sync_edit_actions()

    def sync_edit_actions(self):
        a = self.win.a
        t = self.win._tab()
        allowed = bool(t is not None and t.doc.is_pdf and t.doc.can_modify())
        ok = bool(self.editing and allowed)
        a["edit_pdf"].setEnabled(bool(t is not None and t.doc.is_pdf) or self.editing)
        a["edit_add_text"].setEnabled(allowed)    # turns Edit PDF on when needed
        a["edit_add_image"].setEnabled(allowed)
        sel = t.view.editing.sel if ok else None
        a["edit_replace_image"].setEnabled(sel is not None)
        a["edit_delete_image"].setEnabled(sel is not None)
        mode = t.view.editing.mode if ok else ""
        a["edit_add_text"].setChecked(mode == ADD_TEXT)
        self.win.edit_bar.set_mode(mode)

    def _edit_ctl(self):
        if not self.editing:
            self.toggle_edit(True)
        t = self.win._tab()
        if t is None or not self.editing or not t.doc.can_modify():
            return None
        if t.view.tool != "select":
            self.win._set_tool("select")
        return t.view.editing

    def add_text(self):
        ctl = self._edit_ctl()
        if ctl is None:
            self.sync_edit_actions()
            return
        ctl.set_mode("" if ctl.mode == ADD_TEXT else ADD_TEXT)
        self.sync_edit_actions()

    def add_image(self):
        ctl = self._edit_ctl()
        if ctl is None:
            return
        path, _ = QFileDialog.getOpenFileName(self.win, "Add Image", "", IMAGE_FILTER)
        if path:
            ctl.set_mode(ADD_IMAGE, path)
            self._msg("Click on the page where the image goes.", 8000)
        self.sync_edit_actions()

    def replace_image(self):
        t = self.win._tab()
        if t is None or t.view.editing.sel is None:
            return
        path, _ = QFileDialog.getOpenFileName(self.win, "Replace Image", "", IMAGE_FILTER)
        if path:
            t.view.editing.replace_selected(path)

    def delete_image(self):
        t = self.win._tab()
        if t is not None:
            t.view.editing.delete_selected()

    # ========================================================= Recognize text
    def recognize(self):
        t = self._tab()
        if t is None or not t.doc.is_pdf:
            self._msg("Text can only be recognized in PDF files.")
            return
        if not t.doc.can_modify():
            self._msg("This document does not allow changes.")
            return
        if not ocr.available():
            self._warn("Recognize Text", "No text recognition languages are installed.\n\nPut Tesseract "
                       "“.traineddata” files in %APPDATA%\\Paperleaf\\tessdata.")
            return
        sel = t.selected_pages() if t.organizing() else None
        lang = str(self.win.settings.value("ocr/language", "eng"))
        dlg = D.OcrDialog(t.doc, t.view.current_page(), sel, lang, self.win)
        if not dlg.exec():
            return
        vals = dlg.values()
        self.win.settings.set("ocr/language", vals["language"])
        prog, step = self._progress("Recognize Text", "Recognizing text…")
        try:
            n = ocr.recognize(t.doc, vals["pages"], vals["language"], vals["dpi"], vals["skip_text"], step)
        except Exception as exc:
            prog.close()
            self._warn("Recognize Text", f"Text recognition did not work:\n{exc}")
            return
        prog.close()
        t.ocrbar.hide()
        if n:
            self._msg(f"Recognized text on {n} page{'s' if n != 1 else ''}. You can now search and copy it.")
        else:
            self._msg("No pages needed text recognition: they already have text.")

    def check_scanned(self, tab):
        try:
            if ocr.looks_scanned(tab.doc):
                tab.ocrbar.show_message("This document looks scanned: its text can't be searched or copied yet.")
        except Exception:
            pass

    # =========================================================== compare
    def compare(self):
        from paperleaf.core import compare as C
        t = self._tab()
        old = t.doc.path if t is not None and t.doc.is_pdf and os.path.isfile(t.doc.path) else ""
        dlg = D.CompareDialog(old, "", self.win)
        if not dlg.exec():
            return
        a, b, visual = dlg.values()
        pws = self.win.pages.passwords_for([a, b])
        if pws is None:
            return
        tmp = tempfile.mkdtemp(prefix="paperleaf-compare-")
        self._temp_dirs.append(tmp)
        name = f"Compare {os.path.splitext(os.path.basename(a))[0]} and {os.path.splitext(os.path.basename(b))[0]}.pdf"
        out = os.path.join(tmp, name)
        prog, step = self._progress("Compare Files", "Comparing…")
        try:
            res = C.compare_files(a, b, out, visual, pws.get(a), pws.get(b), step)
        except Exception as exc:
            prog.close()
            self._warn("Compare Files", f"The files could not be compared:\n{exc}")
            return
        prog.close()
        c = res.counts()
        n = sum(c.values()) + sum(len(v) for v in res.visual.values())
        self.win.open_file(out)
        if n == 0:
            self._msg("No differences found.", 8000)
        else:
            self._msg(f"Found {c['replaced']} replaced, {c['inserted']} inserted and {c['deleted']} deleted "
                      "text passages. Use Save As to keep the report.", 10000)

    # =================================================== certificate signing
    def sign_certificate(self, view=None, field=None):
        t = self._tab(view)
        if t is None or not t.doc.is_pdf:
            self._msg("Only PDF files can be signed.")
            return
        if not S.available():
            self._warn("Sign with Certificate", "Certificate signing is not installed with this copy of Paperleaf.")
            return
        doc = t.doc
        if doc.modified or not os.path.isfile(doc.path) or not doc.path.lower().endswith(".pdf"):
            ans = QMessageBox.question(self.win, "Sign with Certificate",
                                       "The document must be saved before it is signed. Save it now?",
                                       QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Cancel)
            if ans != QMessageBox.StandardButton.Save or not self.win.save(t) or doc.modified:
                return
        try:
            fields = S.empty_signature_fields(doc.path, doc.password)
        except Exception:
            fields = []
        if field is not None:
            fields.sort(key=lambda f: f[0] != field)
        st = self.win.settings
        dlg = D.SignCertificateDialog(fields, self.win.env.author(), str(st.value("sign/reason", "")),
                                      str(st.value("sign/location", "")), str(st.value("sign/id", "")), self.win)
        if not dlg.exec():
            return
        vals = dlg.values()
        st.set("sign/reason", vals["reason"])
        st.set("sign/location", vals["location"])
        st.set("sign/id", vals["pfx"])
        if vals["trust"]:
            try:
                S.trust_certificate_of(vals["pfx"], vals["password"])
            except Exception as exc:
                self._msg(f"Could not trust the certificate: {exc}")
        if vals["field"] is not None:
            self._finish_sign(t, vals, vals["page"], None)
            return
        t.view.pick_area(lambda pno, rect: self._finish_sign(t, vals, pno, rect) if pno is not None
                         else self._msg("Signing cancelled."),
                         "Drag a box where the signature goes. Esc cancels.")

    def _finish_sign(self, t, vals, pno, rect):
        doc = t.doc
        base, _ext = os.path.splitext(doc.path)
        out, _ = QFileDialog.getSaveFileName(self.win, "Save Signed Document", base + " (signed).pdf",
                                             "PDF files (*.pdf)")
        if not out:
            return
        if not out.lower().endswith(".pdf"):
            out += ".pdf"
        same = os.path.normcase(os.path.abspath(out)) == os.path.normcase(os.path.abspath(doc.path))
        for i in range(self.win.tabs.count()):
            other = self.win.tabs.widget(i)
            if other is not t and os.path.normcase(other.doc.path) == os.path.normcase(os.path.abspath(out)):
                self._warn("Sign with Certificate", "That file is open in another tab. Close it first.")
                return
        try:
            QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
            S.sign(doc.path, out, vals["pfx"], vals["password"], field=vals["field"], page=pno or 0, rect=rect,
                   reason=vals["reason"], location=vals["location"], doc_password=doc.password)
        except Exception as exc:
            QApplication.restoreOverrideCursor()
            self._warn("Sign with Certificate", f"The document could not be signed:\n{exc}")
            return
        QApplication.restoreOverrideCursor()
        if same:
            doc._reopen(doc.password)
            t.sigbar._dismissed = False
            self.check_signatures(t)
            self.win._sync_ui()
        else:
            self.win.open_file(out)
        self._msg("Signed. Don't change the signed file, or the signature will show the change.", 8000)

    def check_signatures(self, tab):
        if not S.available() or not S.has_signatures(tab.doc):
            tab.sig_statuses = []
            tab.sigbar.hide()
            return
        try:
            sts = S.check(tab.doc.data, tab.doc.password)
        except Exception as exc:
            tab.sig_statuses = []
            tab.sigbar.show_message(f"The signatures could not be checked: {exc}", "invalid")
            return
        tab.sig_statuses = sts
        if not sts:
            tab.sigbar.hide()
            return
        levels = {s.level for s in sts}
        if "invalid" in levels:
            tab.sigbar.show_message("At least one signature is invalid.", "invalid")
        elif "unknown" in levels:
            if any(not s.whole_file for s in sts if s.level == "unknown"):
                text = "Signed, but the document was changed after signing, or a signer's identity is unknown."
            else:
                text = "Signed, but the identity of one or more signers is unknown."
            tab.sigbar.show_message(text, "unknown")
        else:
            tab.sigbar.show_message("Signed and all signatures are valid.", "valid")

    def signature_panel(self, tab=None):
        t = tab or self._tab()
        if t is None:
            return
        if not getattr(t, "sig_statuses", None):
            self.check_signatures(t)
        sts = getattr(t, "sig_statuses", [])
        if not sts:
            self._msg("This document has no certificate signatures.")
            return
        D.SignaturePanel(sts, t.doc.modified, self.win).exec()

    # ============================================================ read aloud
    def reader(self):
        if self._reader is None:
            from paperleaf.ui.read_aloud import Reader
            self._reader = Reader(parent=self.win)
            self._reader.stateChanged.connect(self._read_state)
            self._reader.pageStarted.connect(self._read_page)
            self._apply_voice()
        return self._reader

    def _apply_voice(self):
        r = self._reader
        if r is None or not r.available:
            return
        voice = str(self.win.settings.value("read/voice", "") or "")
        try:
            if voice:
                r.backend.set_voice(voice)
            r.backend.set_rate(int(self.win.settings.value("read/rate", 0) or 0))
        except Exception:
            pass

    def _start(self, to_end: bool):
        t = self._tab()
        if t is None:
            return
        r = self.reader()
        if not r.available:
            self._warn("Read Out Loud", "Read Out Loud needs the speech voices of Windows.")
            return
        cur = t.view.current_page()
        pages = list(range(cur, t.doc.page_count)) if to_end else [cur]
        if not any(t.doc.page_text(p).text.strip() for p in pages[:50]):
            self._msg("There is no text to read on this page. If it is scanned, use Recognize Text first.")
            return
        r.read(t.doc, pages)

    def read_page(self):
        self._start(False)

    def read_to_end(self):
        self._start(True)

    def pause(self):
        if self._reader is not None:
            self._reader.toggle_pause()

    def stop(self):
        if self._reader is not None:
            self._reader.stop()

    def read_options(self):
        r = self.reader()
        voices = r.backend.voices() if r.available else []
        st = self.win.settings
        dlg = D.ReadAloudDialog(voices, str(st.value("read/voice", "") or ""), int(st.value("read/rate", 0) or 0),
                                self.win)
        if dlg.exec():
            voice, rate = dlg.values()
            st.set("read/voice", voice)
            st.set("read/rate", rate)
            self._apply_voice()

    def _read_page(self, doc, pno):
        t = self.win._tab()
        if t is not None and t.doc is doc and t.view.current_page() != pno:
            t.view.go_to_page(pno)

    def _read_state(self, state):
        a = self.win.a
        a["read_pause"].setEnabled(state != "stopped")
        a["read_pause"].setText("&Resume" if state == "paused" else "&Pause")
        a["read_stop"].setEnabled(state != "stopped")
        if state == "speaking":
            self._msg("Reading out loud… (Ctrl+Shift+X stops)", 3000)

    def stop_if_reading(self, doc):
        """A document is closing: stop reading it."""
        r = self._reader
        if r is not None and r._doc is doc:
            r.stop()

    # --------------------------------------------------- after opening
    def opened(self, tab):
        QTimer.singleShot(0, lambda: self._after_open(tab))

    def _after_open(self, tab):
        try:
            if tab.doc.doc is None or tab.doc.doc.is_closed:
                return  # the tab was closed already
        except (RuntimeError, AttributeError):
            return
        if tab.doc.is_pdf:
            self.check_scanned(tab)
            self.check_signatures(tab)
