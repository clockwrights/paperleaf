"""Protect, Reduce File Size, Export To and Create PDF commands for the main window."""
from __future__ import annotations

import os

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QFileDialog, QInputDialog, QLineEdit, QMessageBox, QProgressDialog

from paperleaf.core import convert as C
from paperleaf.core import pages as P
from paperleaf.core import protect as X
from paperleaf.core.document import human_size
from paperleaf.ui import protect_dialogs as D


class _Busy:
    """Wait cursor while something slow runs."""

    def __enter__(self):
        QGuiApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        QGuiApplication.processEvents()

    def __exit__(self, *exc):
        QGuiApplication.restoreOverrideCursor()
        return False


class ProtectCommands:
    def __init__(self, win):
        self.win = win

    # ------------------------------------------------------------- helpers
    def _tab(self):
        t = self.win._tab()
        if t is not None:
            t.view.annots.cancel()
            t.view.forms.commit()
        return t

    def _msg(self, text, ms=6000):
        self.win.statusBar().showMessage(text, ms)

    def _warn(self, title, text):
        QMessageBox.warning(self.win, title, text)

    def _pdf_tab(self, what="This"):
        t = self._tab()
        if t is None:
            return None
        if not t.doc.is_pdf:
            self._msg(f"{what} works on PDF files only.")
            return None
        return t

    def _save_path(self, title, suggested, flt, ext):
        path, _ = QFileDialog.getSaveFileName(self.win, title, suggested, flt)
        if path and not path.lower().endswith(ext):
            path += ext
        return path or None

    def _done_box(self, title, text, path, open_it=True):
        box = QMessageBox(self.win)
        box.setWindowTitle(title)
        box.setIcon(QMessageBox.Icon.Information)
        box.setText(text)
        box.setInformativeText(path)
        open_btn = box.addButton("Open", QMessageBox.ButtonRole.AcceptRole) if open_it else None
        show = box.addButton("Show in Folder", QMessageBox.ButtonRole.ActionRole)
        box.addButton(QMessageBox.StandardButton.Close)
        box.exec()
        if open_btn is not None and box.clickedButton() is open_btn:
            if path.lower().endswith(".pdf"):
                self.win.open_file(path)
            elif hasattr(os, "startfile"):
                os.startfile(path)
        elif box.clickedButton() is show:
            from paperleaf.winshell import registry
            registry.show_in_explorer(path)

    def _owner_ok(self, doc) -> bool:
        """The owner may change the security; others must type the permissions password."""
        if doc.is_owner():
            return True
        pw, ok = QInputDialog.getText(self.win, "Permissions Password",
                                      "Changing the security of this document needs its permissions password:",
                                      QLineEdit.EchoMode.Password)
        if not ok:
            return False
        if not doc.check_owner_password(pw):
            self._warn("Permissions Password", "That is not the permissions password.")
            return False
        doc.auth |= 4
        return True

    # ------------------------------------------------------------ security
    def security(self):
        t = self._pdf_tab("Password security")
        if t is None or not self._owner_ok(t.doc):
            return
        dlg = D.SecurityDialog(t.doc, self.win)
        if not dlg.exec():
            return
        open_pw, owner, perms, method = dlg.values()
        X.protect(t.doc, open_pw, owner, perms, method)
        self.win._sync_title(t)
        self.win._sync_edit_state()
        self._msg("Save the document to apply the password security.", 8000)

    def remove_security(self):
        t = self._pdf_tab("Removing security")
        if t is None:
            return
        doc = t.doc
        if not doc.encrypted and not doc.new_security:
            self._msg("This document has no security to remove.")
            return
        if not self._owner_ok(doc):
            return
        if doc.new_security and not doc.encrypted:
            doc.cancel_security()
            self._msg("The password security you added was removed.")
        else:
            ans = QMessageBox.question(self.win, "Remove Security",
                                       "Remove the passwords and restrictions from this document? "
                                       "This takes effect when you save it.")
            if ans != QMessageBox.StandardButton.Yes:
                return
            X.remove_security(doc)
            self._msg("Save the document to remove its security.", 8000)
        self.win._sync_title(t)
        self.win._sync_edit_state()

    # ----------------------------------------------------------- redaction
    def redact_style(self) -> X.RedactStyle:
        return self.win.env.redact_style()

    def redact_properties(self):
        dlg = D.RedactStyleDialog(self.redact_style(), self.win)
        if dlg.exec():
            self.win.env.set_redact_style(dlg.style())

    def find_redact(self):
        t = self._pdf_tab("Redaction")
        if t is None:
            return
        if not t.doc.can_annotate():
            self._msg("This document does not allow changes.")
            return
        dlg = D.FindRedactDialog(t.doc, self.win)
        if dlg.exec():
            n = X.mark_ranges(t.doc, dlg.checked(), self.redact_style(), self.win.env.author(),
                              label="Mark search results for redaction")
            self._msg(f"Marked {n} area{'s' if n != 1 else ''} for redaction. Choose Apply Redactions to "
                      "remove them.")

    def apply_redactions(self):
        t = self._pdf_tab("Redaction")
        if t is None:
            return
        doc = t.doc
        marks = X.marks(doc)
        if not marks:
            self._msg("Nothing is marked for redaction.")
            return
        if not doc.can_modify():
            self._msg("This document does not allow changes.")
            return
        ans = QMessageBox.question(
            self.win, "Apply Redactions",
            f"Remove the content under {len(marks)} redaction mark{'s' if len(marks) != 1 else ''}?\n\n"
            "Text, images and drawings in the marked areas are deleted, not just covered. You can undo "
            "this until you close the document; save to make it permanent.")
        if ans != QMessageBox.StandardButton.Yes:
            return
        with _Busy():
            n = X.apply(doc)
        self._msg(f"Applied {n} redaction{'s' if n != 1 else ''}. To also remove metadata and other "
                  "hidden content, use Remove Hidden Information.", 9000)

    def sanitize(self):
        t = self._pdf_tab("Remove Hidden Information")
        if t is None:
            return
        if not t.doc.can_modify():
            self._msg("This document does not allow changes.")
            return
        dlg = D.SanitizeDialog(t.doc, self.win)
        if not dlg.exec():
            return
        with _Busy():
            removed = X.sanitize(t.doc, dlg.items())
        n = sum(removed.values())
        self._msg(f"Removed {n} item{'s' if n != 1 else ''}. Save the document to make it permanent."
                  if n else "There was nothing to remove.", 8000)

    # ------------------------------------------------------ reduce file size
    def compress(self):
        t = self._pdf_tab("Reduce File Size")
        if t is None:
            return
        dlg = D.CompressDialog(t.doc, self.win)
        if not dlg.exec():
            return
        path = self._save_path("Save Smaller Copy", os.path.splitext(t.doc.path)[0] + "_reduced.pdf",
                               "PDF files (*.pdf)", ".pdf")
        if not path:
            return
        if os.path.normcase(os.path.abspath(path)) == os.path.normcase(t.doc.path):
            self._warn("Reduce File Size", "Choose a new file name: the open document is kept as it is.")
            return
        try:
            with _Busy():
                before, after = C.compress(t.doc, path, **dlg.settings())
        except Exception as exc:
            self._warn("Reduce File Size", f"Could not make the smaller copy:\n{exc}")
            return
        pct = (1 - after / before) * 100 if before else 0
        text = (f"{human_size(before)} → {human_size(after)} ({pct:.0f}% smaller)." if after < before else
                f"{human_size(before)} → {human_size(after)}. This file was already compact.")
        self._done_box("Reduce File Size", text, path)

    # ---------------------------------------------------------------- export
    def export_word(self):
        t = self._pdf_tab("Export to Word")
        if t is None:
            return
        if not t.doc.can_copy():
            self._msg("This document does not allow copying its content.")
            return
        if not C.word_available():
            self._warn("Export to Word", "Exporting to Word needs the pdf2docx package, which is missing "
                                         "from this installation.")
            return
        path = self._save_path("Export to Word", os.path.splitext(t.doc.path)[0] + ".docx",
                               "Word documents (*.docx)", ".docx")
        if not path:
            return
        self._msg("Converting to Word…", 60000)
        try:
            with _Busy():
                C.to_word(t.doc, path)
        except Exception as exc:
            self._warn("Export to Word", f"Could not convert the document:\n{exc}")
            return
        finally:
            self.win.statusBar().clearMessage()
        self._done_box("Export to Word", "The Word document is ready.", path)

    def export_images(self):
        t = self._pdf_tab("Export to Images")
        if t is None:
            return
        if not t.doc.can_copy():
            self._msg("This document does not allow copying its content.")
            return
        dlg = D.ExportImagesDialog(t.doc, t.view.current_page(), self.win)
        if not dlg.exec():
            return
        folder = QFileDialog.getExistingDirectory(self.win, "Choose a folder for the images",
                                                  os.path.dirname(t.doc.path))
        if not folder:
            return
        pages = dlg.range.pages()
        prog = QProgressDialog("Exporting pages…", "Cancel", 0, len(pages), self.win)
        prog.setWindowTitle("Export to Images")
        prog.setMinimumDuration(400)

        def step(i, n):
            prog.setValue(i)
            QGuiApplication.processEvents()
            return not prog.wasCanceled()
        try:
            files = C.to_images(t.doc, folder, D.base_name(t.doc.path), dlg.fmt.currentData(),
                                dlg.dpi.currentData(), pages, dlg.quality.value(), progress=step)
        except Exception as exc:
            prog.close()
            self._warn("Export to Images", f"Could not export the images:\n{exc}")
            return
        prog.close()
        if files:
            self._done_box("Export to Images", f"Saved {len(files)} image{'s' if len(files) != 1 else ''}.",
                           files[0], open_it=False)

    def export_text(self):
        t = self._pdf_tab("Export to Text")
        if t is None:
            return
        if not t.doc.can_copy():
            self._msg("This document does not allow copying its content.")
            return
        path = self._save_path("Export to Text", os.path.splitext(t.doc.path)[0] + ".txt",
                               "Text files (*.txt)", ".txt")
        if path:
            C.to_text(t.doc, path)
            self._done_box("Export to Text", "The text file is ready.", path)

    def export_html(self):
        t = self._pdf_tab("Export to HTML")
        if t is None:
            return
        if not t.doc.can_copy():
            self._msg("This document does not allow copying its content.")
            return
        path = self._save_path("Export to HTML", os.path.splitext(t.doc.path)[0] + ".html",
                               "Web pages (*.html *.htm)", ".html")
        if path:
            with _Busy():
                C.to_html(t.doc, path)
            self._done_box("Export to HTML", "The web page is ready.", path)

    # ---------------------------------------------------------------- create
    def from_images(self, paths=None):
        if not paths:
            paths, _ = QFileDialog.getOpenFileNames(self.win, "Create PDF from Images", "", P.IMAGE_FILTER)
        if not paths:
            return None
        out = self._save_path("Save PDF", os.path.splitext(paths[0])[0] + ".pdf", "PDF files (*.pdf)", ".pdf")
        if not out:
            return None
        try:
            with _Busy():
                C.images_to_pdf(paths, out)
        except Exception as exc:
            self._warn("Create PDF", f"Could not create the PDF:\n{exc}")
            return None
        self.win.open_file(out)
        return out

    def from_office(self, path=None):
        if not path:
            path, _ = QFileDialog.getOpenFileName(self.win, "Create PDF from Office File", "", C.OFFICE_FILTER)
        if not path:
            return None
        kind = C.office_kind(path)
        if kind is None:
            self._warn("Create PDF", "Choose a Word, Excel or PowerPoint file.")
            return None
        if not C.office_available(kind):
            name = {"word": "Word", "excel": "Excel", "powerpoint": "PowerPoint"}[kind]
            self._warn("Create PDF", f"Converting this file needs Microsoft {name}, which was not found on "
                                     "this computer.")
            return None
        out = self._save_path("Save PDF", os.path.splitext(path)[0] + ".pdf", "PDF files (*.pdf)", ".pdf")
        if not out:
            return None
        self._msg(f"Converting with Microsoft Office: {os.path.basename(path)}…", 60000)
        try:
            with _Busy():
                C.office_to_pdf(path, out)
        except Exception as exc:
            self._warn("Create PDF", f"Office could not convert “{os.path.basename(path)}”.\n\n{exc}")
            return None
        finally:
            self.win.statusBar().clearMessage()
        self.win.open_file(out)
        return out

