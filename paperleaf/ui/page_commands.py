"""Runs page tools for the main window (menus, Organize Pages, Explorer verbs)."""
from __future__ import annotations

import os

from PySide6.QtWidgets import QFileDialog, QInputDialog, QLineEdit, QMessageBox

from paperleaf import APP_NAME
from paperleaf.core import pages as P
from paperleaf.core.document import PasswordRequired
from paperleaf.ui import page_dialogs as D

# commands that change the page structure (need the "assemble" permission)
ORGANIZE = {"rotate_cw", "rotate_ccw", "rotate_180", "rotate", "delete", "delete_pages", "duplicate",
            "insert_blank", "insert_file", "insert_paths", "move", "replace", "crop", "page_size"}
# commands that add page content (need the "change the document" permission)
CONTENT = {"header_footer", "page_numbers", "bates", "watermark", "remove_watermark",
           "remove_header_footer", "remove_bates"}


class PageCommands:
    def __init__(self, win):
        self.win = win

    # ------------------------------------------------------------ helpers
    def _msg(self, text, ms=5000):
        self.win.statusBar().showMessage(text, ms)

    def _warn(self, title, text):
        QMessageBox.warning(self.win, title, text)

    def _ask_password(self, path, wrong=False) -> str | None:
        msg = (f"“{os.path.basename(path)}” is protected.\nEnter the password to open it:" if not wrong
               else "The password is incorrect. Try again:")
        pw, ok = QInputDialog.getText(self.win, "Password", msg, QLineEdit.EchoMode.Password)
        return pw if ok else None

    def open_with_password(self, path):
        """Open a source file, asking for its password if needed. None if cancelled."""
        password = None
        while True:
            try:
                return P.open_source(path, password)
            except PasswordRequired as e:
                password = self._ask_password(path, e.wrong)
                if password is None:
                    return None

    def passwords_for(self, paths) -> dict | None:
        """{path: password} for the protected files in ``paths``; None if cancelled or unreadable."""
        out = {}
        for p in paths:
            password = None
            while True:
                try:
                    P.open_source(p, password).close()
                    break
                except PasswordRequired as e:
                    password = self._ask_password(p, e.wrong)
                    if password is None:
                        return None
                except Exception as exc:
                    self._warn(APP_NAME, f"Paperleaf could not open “{os.path.basename(p)}”.\n\n{exc}")
                    return None
            if password is not None:
                out[p] = password
        return out

    def _allowed(self, tab, name) -> bool:
        doc = tab.doc
        if not doc.is_pdf:
            self._msg("Pages can only be changed in PDF files. Use Save As… or Combine Files to make a PDF.")
            return False
        if name in ORGANIZE and not P.can_organize(doc):
            self._msg("This document does not allow changing its pages.")
            return False
        if name in CONTENT and not doc.can_modify():
            self._msg("This document does not allow changes.")
            return False
        return True

    def _start_dir(self, tab):
        return os.path.dirname(tab.doc.path) if tab else ""

    # -------------------------------------------------------------- main
    def run(self, name: str, pages: list | None = None, tab=None):
        """Run a page command. ``name`` may carry an index ("move:3")."""
        win = self.win
        tab = tab or win._tab()
        base, _, arg = name.partition(":")
        if base == "combine":
            return self.combine()
        if tab is None:
            return None
        if not self._allowed(tab, base):
            return None
        tab.view.annots.cancel()
        tab.view.forms.commit()
        doc = tab.doc
        cur = tab.view.current_page()
        sel = pages if pages else tab.selected_pages()
        title = {"insert_file": "Insert Pages", "insert_blank": "Insert Pages"}.get(base, APP_NAME)
        try:
            result = getattr(self, f"_{base}")(tab, doc, cur, sel, arg)
        except PasswordRequired:
            return None
        except ValueError as exc:
            self._warn(title, str(exc))
            return None
        except Exception as exc:
            self._warn(title, f"That did not work:\n{exc}")
            return None
        return result

    def _reselect(self, tab, pages):
        if tab.organizer is not None and pages:
            tab.organizer.select_pages(pages)

    # ----------------------------------------------------------- organize
    def _rotate_cw(self, tab, doc, cur, sel, arg):
        P.rotate(doc, sel, 90)
        self._reselect(tab, sel)

    def _rotate_ccw(self, tab, doc, cur, sel, arg):
        P.rotate(doc, sel, 270)
        self._reselect(tab, sel)

    def _rotate_180(self, tab, doc, cur, sel, arg):
        P.rotate(doc, sel, 180)
        self._reselect(tab, sel)

    def _rotate(self, tab, doc, cur, sel, arg):
        dlg = D.RotateDialog(doc.page_count, cur, sel if tab.organizing() else None, self.win)
        if dlg.exec():
            P.rotate(doc, dlg.range.pages(), dlg.direction.currentData())

    def _delete(self, tab, doc, cur, sel, arg):
        if len(sel) >= doc.page_count:
            raise ValueError("You can't delete all the pages of a document.")
        P.delete(doc, sel)
        self._reselect(tab, [min(sel[0], doc.page_count - 1)])
        self._msg(f"Deleted {len(sel)} page{'s' if len(sel) != 1 else ''}. Use Undo to bring "
                  f"{'them' if len(sel) != 1 else 'it'} back.")

    def _delete_pages(self, tab, doc, cur, sel, arg):
        dlg = D.DeleteDialog(doc.page_count, cur, sel if tab.organizing() else None, self.win)
        if dlg.exec():
            self._delete(tab, doc, cur, dlg.range.pages(), arg)

    def _duplicate(self, tab, doc, cur, sel, arg):
        self._reselect(tab, P.duplicate(doc, sel))

    def _move(self, tab, doc, cur, sel, arg):
        self._reselect(tab, P.move(doc, sel, int(arg)))

    def _insert_blank(self, tab, doc, cur, sel, arg):
        if tab.organizing():
            at = (max(sel) + 1) if sel else doc.page_count
            self._reselect(tab, P.insert_blank(doc, at))
            return
        r = doc.page_rects[cur]
        dlg = D.InsertDialog(doc.page_count, cur, "Insert Blank Pages", blank=True,
                             sizes_from=(r.width, r.height), parent=self.win)
        if dlg.exec():
            new = P.insert_blank(doc, dlg.position(), dlg.size.currentData(), dlg.count.value())
            tab.view.go_to_page(new[0])

    def _insert_paths(self, tab, doc, cur, sel, arg):
        paths = list(tab.organizer.pending_files) if tab.organizer else []
        if not paths:
            return
        pw = self.passwords_for(paths)
        if pw is None:
            return
        self._reselect(tab, P.insert_files(doc, paths, int(arg), pw))

    def _insert_file(self, tab, doc, cur, sel, arg):
        paths, _ = QFileDialog.getOpenFileNames(self.win, "Insert Pages", self._start_dir(tab), P.COMBINE_FILTER)
        if not paths:
            return
        anchor = max(sel) if tab.organizing() and sel else cur
        dlg = D.InsertDialog(doc.page_count, anchor, parent=self.win)
        if not dlg.exec():
            return
        pw = self.passwords_for(paths)
        if pw is None:
            return
        new = P.insert_files(doc, paths, dlg.position(), pw)
        self._reselect(tab, new)
        if not tab.organizing() and new:
            tab.view.go_to_page(new[0])
        self._msg(f"Inserted {len(new)} page{'s' if len(new) != 1 else ''}.")

    def _replace(self, tab, doc, cur, sel, arg):
        path, _ = QFileDialog.getOpenFileName(self.win, "Replace Pages", self._start_dir(tab), P.COMBINE_FILTER)
        if not path:
            return
        src = self.open_with_password(path)
        if src is None:
            return
        try:
            dlg = D.ReplaceDialog(doc.page_count, sel, os.path.basename(path), src.page_count, self.win)
            if dlg.exec():
                target, source = dlg.ranges()
                if len(target) >= doc.page_count and not source:
                    raise ValueError("Choose the pages to put in.")
                P.replace_pages(doc, target, src, source)
                self._reselect(tab, list(range(target[0], target[0] + len(source))))
        finally:
            src.close()

    def _crop(self, tab, doc, cur, sel, arg):
        dlg = D.CropDialog(doc, cur, sel if tab.organizing() else None, self.win)
        if dlg.exec():
            P.crop(doc, dlg.range.pages(), margins=dlg.margins.points())

    def _page_size(self, tab, doc, cur, sel, arg):
        dlg = D.PageSizeDialog(doc, cur, sel if tab.organizing() else None, self.win)
        if dlg.exec():
            w, h = dlg.page_size()
            P.resize(doc, dlg.range.pages(), w, h)

    # ---------------------------------------------------------- new files
    def _extract(self, tab, doc, cur, sel, arg):
        dlg = D.ExtractDialog(doc.page_count, cur, sel if tab.organizing() else [cur],
                              allow_delete=P.can_organize(doc), parent=self.win)
        if not dlg.exec():
            return
        pages = dlg.range.pages()
        base = os.path.splitext(doc.path)[0] + f"_pages {P.format_ranges(pages).replace(', ', ',')}.pdf"
        path, _ = QFileDialog.getSaveFileName(self.win, "Save Extracted Pages", base, "PDF files (*.pdf)")
        if not path:
            return
        if not path.lower().endswith(".pdf"):
            path += ".pdf"
        files = P.extract(doc, pages, path, separate=dlg.separate.isChecked())
        if dlg.delete.isChecked():
            P.delete(doc, pages)
        if len(files) == 1:
            self.win.open_file(files[0])
        self._msg(f"Saved {len(files)} file{'s' if len(files) != 1 else ''}.")
        return files

    def _split(self, tab, doc, cur, sel, arg):
        dlg = D.SplitDialog(doc, self.win)
        if not dlg.exec():
            return
        mode, value = dlg.mode()
        files = P.split(doc, mode, value, dlg.folder.text(), dlg.base.text().strip())
        box = QMessageBox(self.win)
        box.setWindowTitle("Split Document")
        box.setIcon(QMessageBox.Icon.Information)
        box.setText(f"Split “{doc.name}” into {len(files)} files.")
        box.setInformativeText(dlg.folder.text())
        show = box.addButton("Show in Folder", QMessageBox.ButtonRole.ActionRole)
        box.addButton(QMessageBox.StandardButton.Ok)
        box.exec()
        if box.clickedButton() is show and files:
            from paperleaf.winshell import registry
            registry.show_in_explorer(files[0])
        return files

    def combine(self, paths=()):
        win = self.win
        t = win._tab()
        if not paths and t is not None:
            paths = [t.doc.path] if t.doc.is_pdf and os.path.exists(t.doc.path) and not t.doc.modified else []
        dlg = D.CombineDialog(paths, win, start_dir=self._start_dir(t))
        win._combine_dialog = dlg  # Explorer may send more files while it is open
        try:
            if not dlg.exec():
                return None
        finally:
            win._combine_dialog = None
        files = dlg.paths()
        pw = self.passwords_for(files)
        if pw is None:
            return None
        start = os.path.join(os.path.dirname(files[0]), "Combined.pdf")
        path, _ = QFileDialog.getSaveFileName(win, "Save Combined File", start, "PDF files (*.pdf)")
        if not path:
            return None
        if not path.lower().endswith(".pdf"):
            path += ".pdf"
        if any(os.path.normcase(os.path.abspath(path)) == os.path.normcase(os.path.abspath(f)) for f in files):
            self._warn("Combine Files", "Choose a new file name: the combined file can't replace one of its parts.")
            return None
        try:
            n = P.combine([(f, pw.get(f)) for f in files], path, dlg.bookmarks.isChecked())
        except Exception as exc:
            self._warn("Combine Files", f"Could not combine the files:\n{exc}")
            return None
        win.open_file(path)
        self._msg(f"Combined {len(files)} files into {n} pages.")
        return path

    # ---------------------------------------------------------- page marks
    def _header_footer(self, tab, doc, cur, sel, arg):
        dlg = D.HeaderFooterDialog(doc, cur, self.win)
        if dlg.exec():
            P.add_header_footer(doc, dlg.range.pages(), dlg.spec())

    def _page_numbers(self, tab, doc, cur, sel, arg):
        dlg = D.PageNumbersDialog(doc, cur, self.win)
        if dlg.exec():
            style = P.TextStyle(dlg.font.size.value(), dlg.font.color.rgb)
            P.add_page_numbers(doc, dlg.range.pages(), dlg.position.currentData(), dlg.fmt.currentData(),
                               style, dlg.start.value())

    def _bates(self, tab, doc, cur, sel, arg):
        settings = self.win.settings
        try:
            nxt = int(settings.value("bates/next", 1))
        except (TypeError, ValueError):
            nxt = 1
        dlg = D.BatesDialog(doc, cur, nxt, self.win)
        if dlg.exec():
            style = P.TextStyle(dlg.font.size.value(), dlg.font.color.rgb)
            nxt = P.add_bates(doc, dlg.range.pages(), dlg.prefix.text(), dlg.suffix.text(), dlg.start.value(),
                              dlg.digits.value(), dlg.position.currentData(), style)
            settings.set("bates/next", nxt)
            self._msg(f"Added Bates numbers. The next number is {nxt}.")

    def _watermark(self, tab, doc, cur, sel, arg):
        dlg = D.WatermarkDialog(doc, cur, self.win)
        if dlg.exec():
            P.add_watermark(doc, dlg.range.pages(), dlg.watermark())

    def _remove(self, doc, kind, what):
        n = P.remove_marks(doc, kind)
        self._msg(f"Removed the {what} from {n} page{'s' if n != 1 else ''}." if n else
                  f"There is no {what} added by Paperleaf in this document.")

    def _remove_watermark(self, tab, doc, cur, sel, arg):
        self._remove(doc, P.WATERMARK, "watermark")

    def _remove_header_footer(self, tab, doc, cur, sel, arg):
        self._remove(doc, P.HEADER_FOOTER, "header and footer")

    def _remove_bates(self, tab, doc, cur, sel, arg):
        self._remove(doc, P.BATES, "Bates numbers")
