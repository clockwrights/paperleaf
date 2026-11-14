"""Document properties, preferences, shortcuts and about dialogs."""
from __future__ import annotations

import os
import sys

import pymupdf
import PySide6
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit,
    QMessageBox, QPushButton, QTableWidget, QTableWidgetItem, QTabWidget, QTextBrowser, QVBoxLayout,
    QWidget, QAbstractItemView, QHeaderView,
)

from paperleaf import APP_NAME, VERSION
from paperleaf.core.document import PdfDocument, human_size, parse_pdf_date
from paperleaf.core.settings import Settings
from paperleaf.winshell import registry


def _ro_label(text: str) -> QLabel:
    lbl = QLabel(text or "—")
    lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    lbl.setWordWrap(True)
    return lbl


def _page_size_text(rect: pymupdf.Rect) -> str:
    w_in, h_in = rect.width / 72, rect.height / 72
    w_mm, h_mm = w_in * 25.4, h_in * 25.4
    return f"{w_in:.2f} × {h_in:.2f} in  ({w_mm:.0f} × {h_mm:.0f} mm)"


class PropertiesDialog(QDialog):
    def __init__(self, doc: PdfDocument, current_page: int, parent=None):
        super().__init__(parent)
        self.doc = doc
        self.setWindowTitle("Document Properties")
        self.resize(560, 520)
        tabs = QTabWidget()
        lay = QVBoxLayout(self)
        lay.addWidget(tabs)

        md = doc.metadata()
        desc = QWidget()
        f = QFormLayout(desc)
        f.addRow("File:", _ro_label(doc.name))
        f.addRow("Location:", _ro_label(os.path.dirname(doc.path)))
        f.addRow("File size:", _ro_label(f"{human_size(doc.file_size)} ({doc.file_size:,} bytes)"))
        f.addRow(QLabel(""))
        for key, title in (("title", "Title"), ("author", "Author"), ("subject", "Subject"),
                           ("keywords", "Keywords")):
            f.addRow(f"{title}:", _ro_label(md.get(key, "")))
        f.addRow(QLabel(""))
        f.addRow("Created:", _ro_label(parse_pdf_date(md.get("creationDate", ""))))
        f.addRow("Modified:", _ro_label(parse_pdf_date(md.get("modDate", ""))))
        f.addRow("Application:", _ro_label(md.get("creator", "")))
        f.addRow("PDF Producer:", _ro_label(md.get("producer", "")))
        f.addRow("PDF Version:", _ro_label(md.get("format", "")))
        f.addRow("Number of pages:", _ro_label(str(doc.page_count)))
        f.addRow("Page size:", _ro_label(_page_size_text(doc.page_rects[current_page])
                                        if doc.page_count else ""))
        tagged = ""
        if doc.is_pdf:
            try:
                tagged = "Yes" if doc.doc.xref_get_key(doc.doc.pdf_catalog(), "MarkInfo")[0] != "null" else "No"
            except Exception:
                tagged = "Unknown"
        f.addRow("Tagged PDF:", _ro_label(tagged))
        tabs.addTab(desc, "Description")

        sec = QWidget()
        s = QFormLayout(sec)
        enc = md.get("encryption") or "No security"
        s.addRow("Security method:", _ro_label(enc))
        perms = doc.doc.permissions if doc.is_pdf else -1
        def allowed(bit):
            return "Allowed" if perms & bit else "Not allowed"
        if doc.is_pdf:
            s.addRow("Printing:", _ro_label(allowed(pymupdf.PDF_PERM_PRINT)))
            s.addRow("High quality printing:", _ro_label(allowed(pymupdf.PDF_PERM_PRINT_HQ)))
            s.addRow("Changing the document:", _ro_label(allowed(pymupdf.PDF_PERM_MODIFY)))
            s.addRow("Content copying:", _ro_label(allowed(pymupdf.PDF_PERM_COPY)))
            s.addRow("Commenting:", _ro_label(allowed(pymupdf.PDF_PERM_ANNOTATE)))
            s.addRow("Filling of form fields:", _ro_label(allowed(pymupdf.PDF_PERM_FORM)))
            s.addRow("Page extraction / assembly:", _ro_label(allowed(pymupdf.PDF_PERM_ASSEMBLE)))
        tabs.addTab(sec, "Security")

        self.fonts_tab = QTableWidget(0, 4)
        self.fonts_tab.setHorizontalHeaderLabels(["Font", "Type", "Encoding", "Embedded"])
        self.fonts_tab.verticalHeader().setVisible(False)
        self.fonts_tab.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.fonts_tab.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        tabs.addTab(self.fonts_tab, "Fonts")
        self._fonts_loaded = False
        tabs.currentChanged.connect(self._tab_changed)

        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        bb.rejected.connect(self.reject)
        bb.accepted.connect(self.accept)
        lay.addWidget(bb)

    def _tab_changed(self, idx):
        if idx == 2 and not self._fonts_loaded:
            self._fonts_loaded = True
            for name, ftype, enc, ext in self.doc.fonts():
                r = self.fonts_tab.rowCount()
                self.fonts_tab.insertRow(r)
                base = name.split("+", 1)[-1] if "+" in name else name
                embedded = "Yes" + (" (subset)" if "+" in name else "") if ext and ext != "n/a" else "No"
                for c, v in enumerate((base, ftype, enc, embedded)):
                    self.fonts_tab.setItem(r, c, QTableWidgetItem(v))


class PreferencesDialog(QDialog):
    def __init__(self, settings: Settings, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.setWindowTitle("Preferences")
        self.resize(520, 480)
        lay = QVBoxLayout(self)

        gen = QGroupBox("General")
        f = QFormLayout(gen)
        self.theme = QComboBox()
        self.theme.addItem("Use Windows setting", "system")
        self.theme.addItem("Light", "light")
        self.theme.addItem("Dark", "dark")
        self._select(self.theme, settings.get("theme"))
        f.addRow("Theme:", self.theme)
        self.zoom = QComboBox()
        self.zoom.addItem("Fit width", "fit_width")
        self.zoom.addItem("Fit page", "fit_page")
        self.zoom.addItem("Actual size (100%)", "actual")
        self._select(self.zoom, settings.get("default_zoom"))
        f.addRow("Default zoom:", self.zoom)
        self.layout_combo = QComboBox()
        for key, title in (("single_continuous", "Single page, scrolling"), ("single", "Single page"),
                           ("two_continuous", "Two pages, scrolling"), ("two", "Two pages")):
            self.layout_combo.addItem(title, key)
        self._select(self.layout_combo, settings.get("default_layout"))
        f.addRow("Default page layout:", self.layout_combo)
        self.remember = QCheckBox("Reopen documents at the last page viewed")
        self.remember.setChecked(settings.get("remember_page"))
        f.addRow(self.remember)
        self.restore = QCheckBox("Restore open documents when Paperleaf starts")
        self.restore.setChecked(settings.get("restore_tabs"))
        f.addRow(self.restore)
        self.author = QLineEdit(str(settings.value("author", "") or os.environ.get("USERNAME", "")))
        self.author.setPlaceholderText("Shown on your comments")
        f.addRow("Your name:", self.author)
        self.confirm = QCheckBox("Ask before opening web links found in documents")
        self.confirm.setChecked(settings.get("confirm_links"))
        f.addRow(self.confirm)
        lay.addWidget(gen)

        win = QGroupBox("Windows integration")
        wl = QVBoxLayout(win)
        self.status = QLabel()
        self.status.setWordWrap(True)
        wl.addWidget(self.status)
        row = QHBoxLayout()
        self.reg_btn = QPushButton("Register with Windows")
        self.reg_btn.clicked.connect(self._register)
        self.def_btn = QPushButton("Make default PDF app…")
        self.def_btn.clicked.connect(registry.open_default_apps)
        self.unreg_btn = QPushButton("Remove from Windows")
        self.unreg_btn.clicked.connect(self._unregister)
        row.addWidget(self.reg_btn)
        row.addWidget(self.def_btn)
        row.addWidget(self.unreg_btn)
        wl.addLayout(row)
        lay.addWidget(win)

        priv = QLabel("Privacy: Paperleaf never connects to the internet, has no background services, "
                      "no updater and no telemetry. Settings are stored in %APPDATA%\\Paperleaf.")
        priv.setWordWrap(True)
        priv.setEnabled(False)
        lay.addWidget(priv)
        lay.addStretch(1)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self._save)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        self._refresh_status()

    @staticmethod
    def _select(combo: QComboBox, value):
        i = combo.findData(value)
        if i >= 0:
            combo.setCurrentIndex(i)

    def _refresh_status(self):
        if registry.is_default():
            text = "Paperleaf is your default PDF app. Double-clicking a PDF opens it here."
        elif registry.is_registered_here():
            text = ("Paperleaf is registered: it appears in “Open with” and in the PDF right-click menu. "
                    "To open PDFs with a double-click, make it the default PDF app.")
        elif registry.is_registered():
            text = "Another copy of Paperleaf is registered. Click Register to use this copy."
        else:
            text = "Paperleaf is not registered with Windows yet."
        self.status.setText(text)
        self.unreg_btn.setEnabled(registry.is_registered())

    def _register(self):
        try:
            registry.register()
        except OSError as exc:
            QMessageBox.warning(self, "Windows integration", f"Registration failed:\n{exc}")
        self._refresh_status()

    def _unregister(self):
        try:
            registry.unregister()
        except OSError as exc:
            QMessageBox.warning(self, "Windows integration", f"Could not remove registration:\n{exc}")
        self._refresh_status()

    def _save(self):
        s = self.settings
        s.set("theme", self.theme.currentData())
        s.set("default_zoom", self.zoom.currentData())
        s.set("default_layout", self.layout_combo.currentData())
        s.set("remember_page", self.remember.isChecked())
        s.set("restore_tabs", self.restore.isChecked())
        s.set("confirm_links", self.confirm.isChecked())
        s.set("author", self.author.text().strip())
        s.sync()
        self.accept()


SHORTCUTS = [
    ("Files", [("Ctrl+O", "Open"), ("Ctrl+S", "Save"), ("Ctrl+Shift+S", "Save as"), ("Ctrl+W", "Close tab"),
               ("Ctrl+P", "Print"), ("Ctrl+D", "Document properties"), ("Ctrl+Q", "Exit")]),
    ("Navigation", [("Right / Left arrow", "Next / previous page (when the page fits horizontally)"),
                    ("Page Down / Page Up", "Scroll one screen"), ("Home / End", "First / last page"),
                    ("Ctrl+Shift+N", "Go to page"), ("Alt+Left / Alt+Right", "Previous / next view"),
                    ("Ctrl+Tab / Ctrl+Shift+Tab", "Next / previous document")]),
    ("Zoom & view", [("Ctrl+= / Ctrl+-", "Zoom in / out"), ("Ctrl+mouse wheel", "Zoom at the pointer"),
                     ("Ctrl+0", "Fit page"), ("Ctrl+1", "Actual size"), ("Ctrl+2", "Fit width"),
                     ("Ctrl+Shift+= / Ctrl+Shift+-", "Rotate view clockwise / counter-clockwise"),
                     ("F4", "Show / hide side panel"), ("Ctrl+H", "Read mode"), ("F11", "Full screen"),
                     ("Ctrl+L", "Presentation mode"), ("Ctrl+Shift+D", "Night reading")]),
    ("Tools", [("V", "Select text tool"), ("H", "Hand tool"), ("Hold Space", "Temporary hand tool"),
               ("Z", "Marquee zoom"), ("Ctrl+Shift+A", "Snapshot (copy an area as an image)"),
               ("Ctrl+C", "Copy selected text"), ("Ctrl+A", "Select all text"),
               ("Ctrl+F", "Find"), ("Enter / Shift+Enter, F3 / Shift+F3", "Next / previous result")]),
    ("Comment & Sign", [("Ctrl+Shift+E", "Show / hide the Comment & Sign toolbar"),
                        ("Ctrl+Z / Ctrl+Y", "Undo / redo"), ("Delete", "Delete the selected comment"),
                        ("Arrow keys (Shift = 10x)", "Nudge the selected comment"),
                        ("Double-click", "Edit a text box or open a comment"),
                        ("Shift while drawing", "Square / circle / 45° lines"),
                        ("Esc", "Stop the current tool")]),
    ("Forms", [("Tab / Shift+Tab", "Next / previous form field"),
               ("Enter", "Finish typing in a field"), ("Ctrl+Enter", "Finish typing in a multi-line field"),
               ("Esc", "Cancel typing in a field"), ("Space", "Check or uncheck the selected box or button"),
               ("Arrow keys", "Choose another button in a radio group"),
               ("Alt+Down / F4", "Open a dropdown list"), ("Ctrl+click", "Choose several items in a list box")]),
    ("Pages", [("Ctrl+Shift+O", "Organize Pages"), ("Delete", "Delete the selected pages (Organize Pages)"),
               ("Ctrl+A", "Select all pages (Organize Pages)"),
               ("Enter / double-click", "Open the selected page (Organize Pages)"),
               ("Drag pages / drop files", "Move pages / insert files (Organize Pages)"),
               ("Esc", "Close Organize Pages")]),
    ("Protect", [("Ctrl+Shift+R", "Show / hide the Redact tools"),
                 ("Drag over text / elsewhere", "Mark text / an area for redaction")]),
    ("Edit PDF", [("Click text", "Change that line of text"), ("Enter / Esc", "Finish / cancel the change"),
                  ("Ctrl+Enter", "Finish new text"), ("Drag an image or its corners", "Move / resize it "
                                                                                   "(Shift = stretch)"),
                  ("Arrow keys / Delete", "Nudge / delete the selected image")]),
    ("Read Out Loud", [("Ctrl+Shift+V", "Read this page"), ("Ctrl+Shift+B", "Read to the end of the document"),
                       ("Ctrl+Shift+C", "Pause / resume"), ("Ctrl+Shift+X", "Stop")]),
    ("Prepare Form", [("Delete", "Delete the selected field"),
                      ("Arrow keys (Shift = 10x)", "Nudge the selected field"),
                      ("Enter / double-click", "Field properties"),
                      ("Shift+click / Ctrl+click", "Select several fields"),
                      ("Esc", "Stop adding fields")]),
]


def shortcuts_dialog(parent=None) -> QDialog:
    dlg = QDialog(parent)
    dlg.setWindowTitle("Keyboard Shortcuts")
    dlg.resize(560, 600)
    lay = QVBoxLayout(dlg)
    tb = QTextBrowser()
    html = []
    for section, items in SHORTCUTS:
        html.append(f"<h3>{section}</h3><table cellspacing='4'>")
        for k, v in items:
            html.append(f"<tr><td width='230'><b>{k}</b></td><td>{v}</td></tr>")
        html.append("</table>")
    tb.setHtml("".join(html))
    lay.addWidget(tb)
    bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
    bb.rejected.connect(dlg.reject)
    lay.addWidget(bb)
    return dlg


def about_text() -> str:
    return (f"<h2>{APP_NAME} {VERSION}</h2>"
            "<p>A fast, private PDF reader for Windows.</p>"
            "<p>No background services, no updater, no telemetry, no cloud.</p>"
            "<p>Free software under the GNU Affero General Public License v3.0. "
            "It comes with no warranty.</p>"
            f"<p><small>MuPDF {pymupdf.VersionFitz} · PyMuPDF {pymupdf.VersionBind} · "
            f"Qt {PySide6.__version__} · Python {sys.version.split()[0]}</small></p>")
