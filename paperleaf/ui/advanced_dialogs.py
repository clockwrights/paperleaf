"""Dialogs for Recognize Text, Compare Files, certificate signatures and Read Out Loud."""
from __future__ import annotations

import os

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit,
    QListWidget, QListWidgetItem, QMessageBox, QPushButton, QSlider, QSpinBox, QTextBrowser, QVBoxLayout, QWidget,
)

from paperleaf.core import ocr
from paperleaf.core import signing as S
from paperleaf.ui.page_dialogs import PageRangeBox


def _warn(parent, title, text):
    QMessageBox.warning(parent, title, text)


class _Base(QDialog):
    def __init__(self, title, parent=None, ok="OK"):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.lay = QVBoxLayout(self)
        self.bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        self.ok_btn = self.bb.addButton(ok, QDialogButtonBox.ButtonRole.AcceptRole)
        self.ok_btn.setDefault(True)
        self.bb.accepted.connect(self._ok)
        self.bb.rejected.connect(self.reject)

    def done_layout(self):
        self.lay.addWidget(self.bb)

    def _ok(self):
        if self.validate():
            self.accept()

    def validate(self) -> bool:
        return True


def _note(text: str) -> QLabel:
    lab = QLabel(text)
    lab.setWordWrap(True)
    lab.setEnabled(False)
    return lab


# ===================================================================== OCR
class OcrDialog(_Base):
    """Recognize Text: language, resolution and pages."""

    def __init__(self, doc, current=0, selected=None, language="eng", parent=None):
        super().__init__("Recognize Text", parent, ok="Recognize Text")
        self.setMinimumWidth(420)
        f = QFormLayout()
        self.language = QComboBox()
        langs = ocr.languages()
        for code in sorted(langs, key=lambda c: (c != "eng", ocr.language_label(c))):
            self.language.addItem(ocr.language_label(code), code)
        if "eng" in langs:
            for code in sorted(langs):
                if code != "eng":
                    self.language.addItem(f"English + {ocr.language_label(code)}", f"eng+{code}")
        i = self.language.findData(language)
        if i >= 0:
            self.language.setCurrentIndex(i)
        f.addRow("Document language:", self.language)
        self.dpi = QComboBox()
        for label, dpi in (("Fast (200 dpi)", 200), ("Standard (300 dpi)", 300), ("Small text (400 dpi)", 400)):
            self.dpi.addItem(label, dpi)
        self.dpi.setCurrentIndex(1)
        f.addRow("Quality:", self.dpi)
        self.lay.addLayout(f)
        self.range = PageRangeBox(doc.page_count, current, selected, subset=False)
        self.lay.addWidget(self.range)
        self.skip = QCheckBox("Skip pages that already have text")
        self.skip.setChecked(True)
        self.lay.addWidget(self.skip)
        self.lay.addWidget(_note(
            "Paperleaf adds invisible text over the scanned pages, so you can search, select and copy it. "
            "The pages look the same. More languages: put Tesseract “.traineddata” files in "
            f"{os.path.join('%APPDATA%', 'Paperleaf', 'tessdata')}."))
        self.done_layout()
        if not langs:
            self.ok_btn.setEnabled(False)

    def validate(self):
        if self.range.pages() is None:
            _warn(self, "Recognize Text", "Enter the pages, like 1-3, 5.")
            return False
        return True

    def values(self):
        return dict(pages=self.range.pages(), language=self.language.currentData(), dpi=self.dpi.currentData(),
                    skip_text=self.skip.isChecked())


# ================================================================= compare
class CompareDialog(_Base):
    def __init__(self, old_path="", new_path="", parent=None):
        super().__init__("Compare Files", parent, ok="Compare")
        self.setMinimumWidth(520)
        f = QFormLayout()
        self.old = self._picker(old_path)
        self.new = self._picker(new_path)
        f.addRow("Old file:", self.old[0])
        f.addRow("New file:", self.new[0])
        self.lay.addLayout(f)
        self.visual = QCheckBox("Also compare pictures and drawings (slower)")
        self.visual.setChecked(True)
        self.lay.addWidget(self.visual)
        self.lay.addWidget(_note("Paperleaf makes a report: a summary page, then each pair of pages side by side "
                                 "with the changes marked. Nothing is changed in your files."))
        self.done_layout()

    def _picker(self, path):
        row = QHBoxLayout()
        edit = QLineEdit(path)
        btn = QPushButton("Choose…")
        btn.clicked.connect(lambda: self._browse(edit))
        row.addWidget(edit, 1)
        row.addWidget(btn)
        row.setContentsMargins(0, 0, 0, 0)
        w = QWidget()
        w.setLayout(row)
        return w, edit

    def _browse(self, edit):
        start = os.path.dirname(edit.text()) if edit.text() else ""
        path, _ = QFileDialog.getOpenFileName(self, "Choose a PDF", start, "PDF files (*.pdf);;All files (*.*)")
        if path:
            edit.setText(path)

    def validate(self):
        a, b = self.old[1].text().strip(), self.new[1].text().strip()
        for p in (a, b):
            if not p or not os.path.isfile(p):
                _warn(self, "Compare Files", "Choose two PDF files to compare.")
                return False
        if os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b)):
            _warn(self, "Compare Files", "Choose two different files.")
            return False
        return True

    def values(self):
        return self.old[1].text().strip(), self.new[1].text().strip(), self.visual.isChecked()


# ============================================================ digital IDs
class CreateIdDialog(_Base):
    """A self-signed digital ID, saved as a password-protected .pfx file."""

    def __init__(self, name="", parent=None):
        super().__init__("Create a Digital ID", parent, ok="Create")
        self.setMinimumWidth(420)
        f = QFormLayout()
        self.name = QLineEdit(name)
        self.email = QLineEdit()
        self.org = QLineEdit()
        self.pw = QLineEdit()
        self.pw.setEchoMode(QLineEdit.EchoMode.Password)
        self.again = QLineEdit()
        self.again.setEchoMode(QLineEdit.EchoMode.Password)
        self.years = QSpinBox()
        self.years.setRange(1, 20)
        self.years.setValue(5)
        self.years.setSuffix(" years")
        f.addRow("Name:", self.name)
        f.addRow("Email (optional):", self.email)
        f.addRow("Organization (optional):", self.org)
        f.addRow("Password:", self.pw)
        f.addRow("Confirm password:", self.again)
        f.addRow("Valid for:", self.years)
        self.lay.addLayout(f)
        self.lay.addWidget(_note("A self-signed ID proves the document was not changed after you signed it. "
                                 "Others see your identity as “unknown” until they trust your certificate."))
        self.done_layout()
        self.path = None

    def validate(self):
        if not self.name.text().strip():
            _warn(self, "Create a Digital ID", "Enter your name.")
            return False
        if len(self.pw.text()) < 6:
            _warn(self, "Create a Digital ID", "Use a password of at least 6 characters.")
            return False
        if self.pw.text() != self.again.text():
            _warn(self, "Create a Digital ID", "The passwords don't match.")
            return False
        try:
            self.path = S.create_id(self.name.text(), self.pw.text(), self.email.text(), self.org.text(),
                                    years=self.years.value())
        except Exception as exc:
            _warn(self, "Create a Digital ID", f"Could not create the digital ID:\n{exc}")
            return False
        return True


REASONS = ["I am the author of this document", "I have reviewed this document", "I approve this document",
           "I agree to the terms defined by this document"]
NEW_BOX = "__new__"


class SignCertificateDialog(_Base):
    """Sign with a certificate: choose a digital ID, where to sign, reason and location."""

    def __init__(self, fields, author="", reason="", location="", last_id="", parent=None):
        super().__init__("Sign with Certificate", parent, ok="Continue")
        self.setMinimumWidth(480)
        self.author = author
        f = QFormLayout()
        idrow = QHBoxLayout()
        self.ids = QComboBox()
        self.ids.setMinimumWidth(260)
        idrow.addWidget(self.ids, 1)
        browse = QPushButton("Browse…")
        browse.setToolTip("Use a digital ID file (.pfx or .p12)")
        browse.clicked.connect(self._browse)
        idrow.addWidget(browse)
        create = QPushButton("New ID…")
        create.setToolTip("Create a self-signed digital ID")
        create.clicked.connect(self._create)
        idrow.addWidget(create)
        f.addRow("Digital ID:", idrow)
        self.pw = QLineEdit()
        self.pw.setEchoMode(QLineEdit.EchoMode.Password)
        f.addRow("Password:", self.pw)
        self.where = QComboBox()
        for name, pno in fields:
            self.where.addItem(f"Field “{name}” (page {pno + 1})", (name, pno))
        self.where.addItem("Draw a new signature box…", NEW_BOX)
        f.addRow("Sign in:", self.where)
        self.reason = QComboBox()
        self.reason.setEditable(True)
        self.reason.addItems(REASONS)
        self.reason.setCurrentText(reason or REASONS[0])
        f.addRow("Reason:", self.reason)
        self.location = QLineEdit(location)
        f.addRow("Location:", self.location)
        self.lay.addLayout(f)
        self.trust = QCheckBox("Trust this ID on this computer (your own signatures show as valid)")
        self.lay.addWidget(self.trust)
        self.lay.addWidget(_note("Paperleaf saves a signed copy. Changes made after signing are added to the "
                                 "file and show in the signature check."))
        self.done_layout()
        self._fill_ids(last_id)

    def _fill_ids(self, select=""):
        self.ids.clear()
        for path in S.list_ids():
            self.ids.addItem(os.path.splitext(os.path.basename(path))[0], path)
        if select and self.ids.findData(select) < 0 and os.path.isfile(select):
            self.ids.addItem(os.path.basename(select), select)
        i = self.ids.findData(select)
        if i >= 0:
            self.ids.setCurrentIndex(i)

    def _browse(self):
        path, _ = QFileDialog.getOpenFileName(self, "Choose a Digital ID", "", S.ID_FILTER)
        if path:
            self._fill_ids(path)

    def _create(self):
        dlg = CreateIdDialog(self.author, self)
        if dlg.exec() and dlg.path:
            self._fill_ids(dlg.path)
            self.pw.setText(dlg.pw.text())
            self.trust.setChecked(True)

    def validate(self):
        path = self.ids.currentData()
        if not path:
            _warn(self, "Sign with Certificate", "Choose a digital ID, or create one with New ID.")
            return False
        try:
            S.id_info(path, self.pw.text())
        except Exception:
            _warn(self, "Sign with Certificate", "The digital ID could not be opened. Check its password.")
            return False
        return True

    def values(self):
        where = self.where.currentData()
        return dict(pfx=self.ids.currentData(), password=self.pw.text(),
                    field=None if where == NEW_BOX else where[0],
                    page=None if where == NEW_BOX else where[1],
                    reason=self.reason.currentText().strip(), location=self.location.text().strip(),
                    trust=self.trust.isChecked())


LEVEL_COLORS = {"valid": QColor(16, 124, 16), "unknown": QColor(176, 112, 0), "invalid": QColor(196, 43, 28)}
LEVEL_TEXT = {"valid": "Valid", "unknown": "Identity unknown", "invalid": "Invalid"}


class SignaturePanel(QDialog):
    """Signature Panel: every certificate signature and what its check found."""

    def __init__(self, statuses, modified=False, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Signatures")
        self.resize(560, 380)
        lay = QVBoxLayout(self)
        self.list = QListWidget()
        for st in statuses:
            who = st.signer or "Unknown signer"
            item = QListWidgetItem(f"{LEVEL_TEXT[st.level]}: signed by {who}" + (f", {st.when}" if st.when else ""))
            item.setForeground(LEVEL_COLORS[st.level])
            item.setData(Qt.ItemDataRole.UserRole, st)
            self.list.addItem(item)
        lay.addWidget(self.list, 1)
        self.details = QTextBrowser()
        lay.addWidget(self.details, 1)
        if modified:
            lay.addWidget(_note("You have unsaved changes. The check is of the file as it was last saved."))
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        bb.rejected.connect(self.reject)
        bb.accepted.connect(self.accept)
        lay.addWidget(bb)
        self.list.currentRowChanged.connect(self._show)
        if statuses:
            self.list.setCurrentRow(0)

    def _show(self, row):
        item = self.list.item(row)
        if item is None:
            self.details.clear()
            return
        st = item.data(Qt.ItemDataRole.UserRole)
        rows = [("Field", st.field), ("Signed by", st.signer), ("Date", st.when), ("Reason", st.reason),
                ("Location", st.location)]
        html = f"<p><b>{st.summary}</b></p><table>"
        for k, v in rows:
            if v:
                html += f"<tr><td style='padding-right:12px'>{k}:</td><td>{_esc(v)}</td></tr>"
        html += "</table>"
        self.details.setHtml(html)


def _esc(s: str) -> str:
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


# ============================================================== read aloud
class ReadAloudDialog(_Base):
    """Read Out Loud preferences: voice and speed."""

    def __init__(self, voices, voice="", rate=0, parent=None):
        super().__init__("Read Out Loud", parent)
        f = QFormLayout()
        self.voice = QComboBox()
        self.voice.addItem("Default voice", "")
        for v in voices:
            self.voice.addItem(v, v)
        i = self.voice.findData(voice)
        self.voice.setCurrentIndex(max(0, i))
        f.addRow("Voice:", self.voice)
        self.rate = QSlider(Qt.Orientation.Horizontal)
        self.rate.setRange(-10, 10)
        self.rate.setValue(int(rate))
        f.addRow("Speed:", self.rate)
        self.lay.addLayout(f)
        self.done_layout()

    def values(self):
        return self.voice.currentData(), self.rate.value()
