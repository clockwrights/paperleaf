"""Main application window: tabs, menus, toolbar, welcome page."""
from __future__ import annotations

import os
import urllib.parse

import pymupdf
from PySide6.QtCore import QByteArray, QEvent, QSize, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QAction, QActionGroup, QCursor, QDesktopServices, QKeySequence
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QFileDialog, QHBoxLayout, QInputDialog, QLabel, QLineEdit,
    QListWidget, QListWidgetItem, QMainWindow, QMenu, QMessageBox, QPushButton, QStackedWidget,
    QTabWidget, QToolBar, QToolButton, QVBoxLayout, QWidget, QSizePolicy, QFrame,
)

from paperleaf import APP_NAME
from paperleaf.core import annotations as an
from paperleaf.core import forms as F
from paperleaf.core import recovery
from paperleaf.core.document import OPEN_FILTER, PasswordRequired, PdfDocument
from paperleaf.core.settings import Settings
from paperleaf.ui import dialogs
from paperleaf.ui.annot_dialogs import CommentDialog, PropertiesDialog
from paperleaf.ui.annotate_bar import TOOL_DEFS, TOOL_LABELS, AnnotateToolbar, AnnotEnv, ToolStyles
from paperleaf.ui.doc_tab import DocumentTab
from paperleaf.ui.form_bar import FIELD_TOOL_DEFS, FIELD_TOOL_LABELS, PrepareFormToolbar
from paperleaf.ui.page_commands import PageCommands
from paperleaf.ui.protect_commands import ProtectCommands
from paperleaf.ui.redact_bar import RedactToolbar
from paperleaf.ui.advanced_commands import AdvancedCommands
from paperleaf.ui.edit_bar import EditToolbar
from paperleaf.ui.icons import icon
from paperleaf.ui.presentation import PresentationWindow
from paperleaf.ui.printing import run_print
from paperleaf.viewer.annot_tools import ANNOT_TOOLS, MARKUP_TOOLS, REDACT, STAY_ACTIVE
from paperleaf.viewer.form_tools import FIELD_TOOL_STAY, FIELD_TOOLS
from paperleaf.viewer.view import Tool
from paperleaf.winshell import registry

BACKUP_INTERVAL_MS = 60_000

ZOOM_PRESETS = [10, 25, 50, 75, 100, 125, 150, 200, 300, 400, 800, 1600, 3200, 6400]


class WelcomePage(QWidget):
    openRequested = Signal()
    combineRequested = Signal()
    fileRequested = Signal(str)
    integrateRequested = Signal()

    def __init__(self, settings: Settings, parent=None):
        super().__init__(parent)
        self.settings = settings
        outer = QHBoxLayout(self)
        outer.addStretch(1)
        col = QVBoxLayout()
        outer.addLayout(col, 3)
        outer.addStretch(1)
        col.addSpacing(40)
        title = QLabel(f"<span style='font-size:28pt; font-weight:600'>{APP_NAME}</span>")
        col.addWidget(title)
        sub = QLabel("Fast, private PDF reader. Nothing runs in the background.")
        sub.setEnabled(False)
        col.addWidget(sub)
        col.addSpacing(16)
        row = QHBoxLayout()
        open_btn = QPushButton(icon("open"), "  Open a file…")
        open_btn.setIconSize(QSize(20, 20))
        open_btn.setMinimumHeight(36)
        open_btn.clicked.connect(self.openRequested.emit)
        row.addWidget(open_btn)
        combine_btn = QPushButton(icon("combine"), "  Combine Files…")
        combine_btn.setIconSize(QSize(20, 20))
        combine_btn.setMinimumHeight(36)
        combine_btn.clicked.connect(self.combineRequested.emit)
        row.addWidget(combine_btn)
        row.addStretch(1)
        col.addLayout(row)
        self.banner = QFrame()
        self.banner.setObjectName("Banner")
        bl = QHBoxLayout(self.banner)
        self.banner_text = QLabel()
        self.banner_text.setWordWrap(True)
        bl.addWidget(self.banner_text, 1)
        self.banner_btn = QPushButton()
        self.banner_btn.clicked.connect(self.integrateRequested.emit)
        bl.addWidget(self.banner_btn)
        col.addSpacing(12)
        col.addWidget(self.banner)
        col.addSpacing(16)
        col.addWidget(QLabel("<b>Recent files</b>"))
        self.recent = QListWidget()
        self.recent.setFrameShape(QListWidget.Shape.NoFrame)
        self.recent.itemActivated.connect(self._activated)
        self.recent.itemClicked.connect(self._activated)
        col.addWidget(self.recent, 1)
        hint = QLabel("Tip: drag PDF files here to open them.")
        hint.setEnabled(False)
        col.addWidget(hint)
        col.addSpacing(20)

    def refresh(self):
        self.recent.clear()
        for path in self.settings.recent_files():
            it = QListWidgetItem(icon("doc"), f"{os.path.basename(path)}\n{os.path.dirname(path)}")
            it.setData(Qt.ItemDataRole.UserRole, path)
            it.setToolTip(path)
            if not os.path.exists(path):
                it.setFlags(it.flags() & ~Qt.ItemFlag.ItemIsEnabled)
            self.recent.addItem(it)
        if not self.recent.count():
            it = QListWidgetItem("No recent files")
            it.setFlags(Qt.ItemFlag.NoItemFlags)
            self.recent.addItem(it)
        try:
            if registry.is_default():
                self.banner.hide()
            elif registry.is_registered_here():
                self.banner_text.setText("Paperleaf is not your default PDF app yet, so double-clicking a PDF "
                                         "still opens another program.")
                self.banner_btn.setText("Make default…")
                self.banner.show()
            else:
                self.banner_text.setText("Add Paperleaf to Windows so PDFs can be opened from Explorer "
                                         "(Open with, right-click menu, default app).")
                self.banner_btn.setText("Set up")
                self.banner.show()
        except Exception:
            self.banner.hide()

    def _activated(self, item):
        path = item.data(Qt.ItemDataRole.UserRole)
        if path:
            self.fileRequested.emit(path)


class MainWindow(QMainWindow):
    def __init__(self, settings: Settings, apply_theme=None):
        super().__init__()
        self.settings = settings
        self.apply_theme = apply_theme
        self.night = settings.get("night_mode")
        self._read_mode = False
        self._was_maximized = False
        self._presentation = None
        self._prepare = False
        self.highlight_fields = settings.get("highlight_fields")
        self.tool_styles = ToolStyles(settings)
        self.env = AnnotEnv(settings, self.tool_styles)
        self.pages = PageCommands(self)
        self.protect = ProtectCommands(self)
        self.adv = AdvancedCommands(self)
        self.setWindowTitle(APP_NAME)
        self.setAcceptDrops(True)
        self.resize(1280, 860)

        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self.tabs.setTabsClosable(True)
        self.tabs.setMovable(True)
        self.tabs.setElideMode(Qt.TextElideMode.ElideMiddle)
        self.tabs.tabCloseRequested.connect(self.close_tab)
        self.tabs.currentChanged.connect(self._tab_changed)
        self.tabs.tabBar().installEventFilter(self)
        self.welcome = WelcomePage(settings)
        self.welcome.openRequested.connect(self.open_dialog)
        self.welcome.combineRequested.connect(lambda: self.pages.combine())
        self.welcome.fileRequested.connect(self.open_file)
        self.welcome.integrateRequested.connect(self._integrate)
        self.stack = QStackedWidget()
        self.stack.addWidget(self.welcome)
        self.stack.addWidget(self.tabs)
        self.setCentralWidget(self.stack)

        self._create_actions()
        self._create_menus()
        self._create_toolbar()
        self.annot_bar = AnnotateToolbar(self.tool_actions, self.tool_styles, self.env, self)
        self.addToolBarBreak(Qt.ToolBarArea.TopToolBarArea)
        self.addToolBar(Qt.ToolBarArea.TopToolBarArea, self.annot_bar)
        self.annot_bar.styleEdited.connect(self._edit_selected_style)
        self.annot_bar.stampChosen.connect(lambda: self._set_tool("stamp"))
        self.annot_bar.signatureChosen.connect(lambda: self._set_tool("signature"))
        self.annot_bar.setVisible(self.settings.get("show_annotate"))
        self.a["comment_mode"].setChecked(self.annot_bar.isVisible())
        self.prepare_bar = PrepareFormToolbar(self.tool_actions, self.a, self)
        self.addToolBarBreak(Qt.ToolBarArea.TopToolBarArea)
        self.addToolBar(Qt.ToolBarArea.TopToolBarArea, self.prepare_bar)
        self.prepare_bar.hide()
        self.redact_bar = RedactToolbar(self.tool_actions[REDACT], self.a, self)
        self.addToolBarBreak(Qt.ToolBarArea.TopToolBarArea)
        self.addToolBar(Qt.ToolBarArea.TopToolBarArea, self.redact_bar)
        self.redact_bar.hide()
        self.edit_bar = EditToolbar(self.a, self)
        self.addToolBarBreak(Qt.ToolBarArea.TopToolBarArea)
        self.addToolBar(Qt.ToolBarArea.TopToolBarArea, self.edit_bar)
        self.edit_bar.hide()
        self.backup_timer = QTimer(self)
        self.backup_timer.setInterval(BACKUP_INTERVAL_MS)
        self.backup_timer.timeout.connect(self._backup_all)
        self.backup_timer.start()
        self.size_label = QLabel()
        self.statusBar().addPermanentWidget(self.size_label)
        self.statusBar().setSizeGripEnabled(True)

        geo = settings.value("window/geometry")
        if isinstance(geo, QByteArray):
            self.restoreGeometry(geo)
        self.welcome.refresh()
        self._sync_ui()

    # ================================================================ actions
    def _act(self, name, text, slot=None, shortcut=None, ic=None, checkable=False, tip=None):
        a = QAction(text, self)
        if ic:
            a.setIcon(icon(ic))
        if shortcut:
            if isinstance(shortcut, (list, tuple)):
                a.setShortcuts([QKeySequence(s) for s in shortcut])
            else:
                a.setShortcut(QKeySequence(shortcut))
        a.setCheckable(checkable)
        if slot:
            a.triggered.connect(slot)
        sc = a.shortcut().toString(QKeySequence.SequenceFormat.NativeText)
        a.setToolTip(f"{tip or text.replace('&', '').replace('…', '')}" + (f" ({sc})" if sc else ""))
        self.addAction(a)
        self.a[name] = a
        return a

    def page_action(self, name, text, ic):
        return self._act(f"page_{name}", text, lambda: self.pages.run(name), None, ic)

    def _create_actions(self):
        self.a: dict[str, QAction] = {}
        A = self._act
        A("open", "&Open…", self.open_dialog, QKeySequence.StandardKey.Open, "open")
        A("close", "&Close", lambda: self.close_tab(self.tabs.currentIndex()), "Ctrl+W")
        A("close_all", "Close A&ll", self.close_all, "Ctrl+Shift+W")
        A("save", "&Save", self.save, QKeySequence.StandardKey.Save, "save")
        A("save_as", "Save &As…", self.save_as, "Ctrl+Shift+S")
        A("save_copy", "Save a &Copy…", self.save_copy)
        A("undo", "&Undo", self.undo, QKeySequence.StandardKey.Undo, "undo")
        A("redo", "&Redo", self.redo, ["Ctrl+Y", "Ctrl+Shift+Z"], "redo")
        A("comment_mode", "&Comment && Sign", self.toggle_annotate_bar, "Ctrl+Shift+E", "comment_mode",
          checkable=True, tip="Show the Comment & Sign tools")
        A("hl_sel", "&Highlight Text", lambda: self._markup_selection("highlight"), None, "highlight")
        A("ul_sel", "&Underline Text", lambda: self._markup_selection("underline"), None, "underline")
        A("so_sel", "&Strikethrough Text", lambda: self._markup_selection("strikeout"), None, "strikeout")
        A("delete_annot", "&Delete Comment", self._delete_selected_annot, None, "delete")
        A("highlight_fields", "&Highlight Fields", lambda: self.set_highlight_fields(self.a["highlight_fields"].isChecked()),
          checkable=True, tip="Tint the form fields so they are easy to find")
        A("clear_form", "&Clear Form…", self.clear_form, tip="Set every field back to its default value")
        A("import_form", "&Import Data…", self.import_form_data, tip="Fill the form from an FDF, XFDF or JSON file")
        A("export_form", "&Export Data…", self.export_form_data, tip="Save the form's data as FDF, XFDF or JSON")
        A("flatten_form", "&Flatten Form", lambda: self.flatten_form(False),
          tip="Burn the field values into the page; the fields are removed")
        A("flatten_all", "Flatten Form and C&omments", lambda: self.flatten_form(True),
          tip="Burn the field values and comments into the page")
        A("prepare_form", "&Prepare Form", self.toggle_prepare, None, "form", checkable=True,
          tip="Add, move and edit form fields")
        A("field_props", "Field P&roperties…", lambda: self.field_properties(), None, "settings")
        # pages
        P = self.page_action
        A("organize", "&Organize Pages", self.toggle_organize, "Ctrl+Shift+O", "organize", checkable=True,
          tip="Move, rotate, delete and insert pages")
        P("insert_file", "Insert Pages from &File…", "insert")
        P("insert_blank", "Insert &Blank Pages…", "page")
        P("replace", "&Replace Pages…", "replace")
        P("extract", "E&xtract Pages…", "extract")
        P("delete_pages", "&Delete Pages…", "delete")
        P("rotate", "R&otate Pages…", "rotate")
        P("split", "&Split Document…", "split")
        A("combine", "Co&mbine Files…", lambda: self.pages.combine(), None, "combine",
          tip="Combine PDFs and images into one PDF")
        P("crop", "&Crop Pages…", "crop")
        P("page_size", "Change Page Si&ze…", None)
        P("header_footer", "&Add Header && Footer…", "header")
        P("remove_header_footer", "&Remove Header && Footer", None)
        P("page_numbers", "Add Page &Numbers…", None)
        P("bates", "&Add Bates Numbering…", None)
        P("remove_bates", "&Remove Bates Numbering", None)
        P("watermark", "&Add Watermark…", "watermark")
        P("remove_watermark", "&Remove Watermark", None)
        # protect, reduce, export, create
        R = self.protect
        A("encrypt", "&Encrypt with Password…", R.security, None, "lock", tip="Add a password and permissions")
        A("remove_security", "&Remove Security…", R.remove_security, None, "unlock")
        A("redact_mode", "&Redact", self.toggle_redact_bar, "Ctrl+Shift+R", "redact", checkable=True,
          tip="Show the Redact tools")
        A("find_redact", "&Find Text && Redact…", R.find_redact, None, "search")
        A("apply_redactions", "&Apply Redactions…", R.apply_redactions)
        A("redact_props", "Redaction &Properties…", R.redact_properties, None, "settings")
        A("sanitize", "Remove &Hidden Information…", R.sanitize, None, "clean")
        A("compress", "Reduce File Si&ze…", R.compress, None, "compress", tip="Save a smaller copy")
        A("export_word", "&Word Document…", R.export_word)
        A("export_images", "&Images…", R.export_images)
        A("export_text", "&Text…", R.export_text)
        A("export_html", "&HTML Web Page…", R.export_html)
        A("create_images", "From &Images…", lambda: R.from_images())
        A("create_office", "From &Word, Excel or PowerPoint File…", lambda: R.from_office())
        A("delete_field", "&Delete Field", self._delete_selected_fields, None, "delete")
        # edit, OCR, compare, certificates, read aloud
        V = self.adv
        A("edit_pdf", "&Edit Text && Images", V.toggle_edit, None, "edit", checkable=True,
          tip="Change text, move and resize images")
        A("edit_add_text", "Add &Text", V.add_text, None, "font", checkable=True, tip="Add new text to the page")
        A("edit_add_image", "Add &Image…", V.add_image, None, "image", tip="Add a picture to the page")
        A("edit_replace_image", "&Replace Image…", V.replace_image, None, "replace")
        A("edit_delete_image", "De&lete Image", V.delete_image, None, "delete")
        A("ocr", "Recognize &Text…", V.recognize, None, "ocr", tip="Make scanned pages searchable (OCR)")
        A("compare", "Compare &Files…", V.compare, None, "compare", tip="Show the differences between two PDFs")
        A("sign_cert", "Sign with &Certificate…", lambda: V.sign_certificate(), None, "certificate",
          tip="Add a digital signature with a digital ID")
        A("sig_panel", "Signature &Panel", lambda: V.signature_panel(), tip="Check the signatures")
        A("create_id", "Create &Digital ID…", self._create_id)
        A("read_page", "Read This &Page Only", V.read_page, "Ctrl+Shift+V", "speak")
        A("read_end", "Read to &End of Document", V.read_to_end, "Ctrl+Shift+B")
        A("read_pause", "&Pause", V.pause, "Ctrl+Shift+C", "pause")
        A("read_stop", "&Stop", V.stop, "Ctrl+Shift+X", "stop")
        A("read_options", "Read Out Loud &Options…", V.read_options)
        self.a["read_pause"].setEnabled(False)
        self.a["read_stop"].setEnabled(False)
        A("show_folder", "Show in &Folder", self.show_in_folder)
        A("print", "&Print…", self.print_doc, QKeySequence.StandardKey.Print, "print")
        A("properties", "P&roperties…", self.show_properties, "Ctrl+D", "info")
        A("exit", "E&xit", self.close, "Ctrl+Q")
        A("copy", "&Copy", self.copy, QKeySequence.StandardKey.Copy, "copy")
        A("select_all", "Select &All", lambda: self._view() and self._view().select_all(), QKeySequence.StandardKey.SelectAll)
        A("find", "&Find…", self.find, QKeySequence.StandardKey.Find, "search", tip="Find text")
        A("find_next", "Find &Next", lambda: self._view() and self._view().search_next(False), "F3")
        A("find_prev", "Find Pre&vious", lambda: self._view() and self._view().search_next(True), "Shift+F3")
        A("prefs", "&Preferences…", self.preferences, "Ctrl+K", "settings")
        A("next_page", "&Next Page", lambda: self._view() and self._view().next_page(), None, "down")
        A("prev_page", "&Previous Page", lambda: self._view() and self._view().prev_page(), None, "up")
        A("first_page", "&First Page", lambda: self._view() and self._view().first_page(), "Ctrl+Home")
        A("last_page", "&Last Page", lambda: self._view() and self._view().last_page(), "Ctrl+End")
        A("goto_page", "&Go to Page…", self.goto_page, "Ctrl+Shift+N")
        A("back", "Previous &View", lambda: self._view() and self._view().go_back(), "Alt+Left", "back")
        A("forward", "Next V&iew", lambda: self._view() and self._view().go_forward(), "Alt+Right", "forward")
        A("zoom_in", "Zoom &In", lambda: self._view() and self._view().zoom_in(), ["Ctrl+=", "Ctrl++"], "zoom_in")
        A("zoom_out", "Zoom &Out", lambda: self._view() and self._view().zoom_out(), "Ctrl+-", "zoom_out")
        A("fit_page", "Zoom to &Page Level", lambda: self._view() and self._view().fit_page(), "Ctrl+0", "fit_page", tip="Fit page")
        A("actual", "&Actual Size", lambda: self._view() and self._view().actual_size(), "Ctrl+1")
        A("fit_width", "Fit &Width", lambda: self._view() and self._view().fit_width(), "Ctrl+2", "fit_width")
        A("rotate_cw", "Rotate &Clockwise", lambda: self._rotate(90), ["Ctrl+Shift+=", "Ctrl+Shift++"], "rotate", tip="Rotate view clockwise")
        A("rotate_ccw", "Rotate Co&unterclockwise", lambda: self._rotate(-90), "Ctrl+Shift+-", "rotate_ccw")
        A("sidebar", "&Navigation Panes", self.toggle_sidebar, "F4", "sidebar", checkable=True, tip="Show/hide side panel")
        A("toolbar", "&Toolbar", self._toggle_toolbar, "F8", checkable=True)
        A("menubar", "&Menu Bar", self._toggle_menubar, "F9", checkable=True)
        A("night", "&Night Reading", self.toggle_night, "Ctrl+Shift+D", "night", checkable=True, tip="Night reading (dark pages)")
        A("read_mode", "&Read Mode", self.toggle_read_mode, "Ctrl+H", "read", checkable=True)
        A("fullscreen", "&Full Screen", self.toggle_fullscreen, "F11", "fullscreen", checkable=True)
        A("present", "&Presentation Mode", self.present, "Ctrl+L", "presentation")
        A("tool_select", "&Select Text", lambda: self._set_tool(Tool.SELECT), "V", "text", checkable=True, tip="Select text")
        A("tool_hand", "&Hand Tool", lambda: self._set_tool(Tool.HAND), "H", "hand", checkable=True)
        A("tool_marquee", "&Marquee Zoom", lambda: self._set_tool(Tool.MARQUEE), "Z", "marquee", checkable=True)
        A("tool_snapshot", "Take a S&napshot", lambda: self._set_tool(Tool.SNAPSHOT), "Ctrl+Shift+A", "snapshot", checkable=True)
        A("next_tab", "Next Document", lambda: self._cycle_tab(1), "Ctrl+Tab")
        A("prev_tab", "Previous Document", lambda: self._cycle_tab(-1), "Ctrl+Shift+Tab")
        A("shortcuts", "&Keyboard Shortcuts", lambda: dialogs.shortcuts_dialog(self).exec(), "F1")
        A("about", f"&About {APP_NAME}", self.about)
        A("make_default", f"Make {APP_NAME} the &Default PDF App…", self._integrate)
        self.a["menubar"].setChecked(True)
        self.a["toolbar"].setChecked(True)
        self.a["night"].setChecked(self.night)
        self.a["highlight_fields"].setChecked(self.highlight_fields)
        tg = QActionGroup(self)
        self.tool_actions = {Tool.SELECT: self.a["tool_select"], Tool.HAND: self.a["tool_hand"],
                             Tool.MARQUEE: self.a["tool_marquee"], Tool.SNAPSHOT: self.a["tool_snapshot"],
                             "select": self.a["tool_select"]}
        for d in TOOL_DEFS + [("stamp", "Add Stamp", "stamp"), ("signature", "Place Signature", "sign"),
                              (REDACT, "Mark for Redaction", "redact")] + FIELD_TOOL_DEFS:
            if d is None:
                continue
            tool, text, ic = d
            act = QAction(icon(ic), text, self)
            act.setCheckable(True)
            act.setToolTip(text)
            act.triggered.connect(lambda _c=False, t=tool: self._set_tool(t))
            self.tool_actions[tool] = act
        for act in self.tool_actions.values():
            tg.addAction(act)
        self.a["tool_select"].setChecked(True)
        # page layouts
        lg = QActionGroup(self)
        self.layout_actions = {}
        for key, text in (("single", "&Single Page View"), ("single_continuous", "&Enable Scrolling"),
                          ("two", "&Two Page View"), ("two_continuous", "Two Page Sc&rolling")):
            a = QAction(text, self, checkable=True)
            a.triggered.connect(lambda _c, k=key: self._view() and self._view().set_layout(k))
            lg.addAction(a)
            self.layout_actions[key] = a
        self.cover_action = QAction("Show &Cover Page in Two Page View", self, checkable=True)
        self.cover_action.triggered.connect(lambda c: self._view() and self._view().set_layout(cover=c))

    def _create_menus(self):
        mb = self.menuBar()
        a = self.a
        m = mb.addMenu("&File")
        m.addAction(a["open"])
        self.recent_menu = m.addMenu("Open &Recent")
        self.recent_menu.aboutToShow.connect(self._fill_recent)
        cr = m.addMenu("Create &PDF")
        cr.addAction(a["create_images"])
        cr.addAction(a["create_office"])
        cr.addAction(a["combine"])
        m.addSeparator()
        m.addAction(a["save"])
        m.addAction(a["save_as"])
        m.addAction(a["save_copy"])
        ex = m.addMenu("&Export To")
        for k in ("export_word", "export_images", "export_text", "export_html"):
            ex.addAction(a[k])
        m.addAction(a["compress"])
        m.addAction(a["show_folder"])
        m.addSeparator()
        m.addAction(a["close"])
        m.addAction(a["close_all"])
        m.addSeparator()
        m.addAction(a["properties"])
        m.addAction(a["print"])
        m.addSeparator()
        m.addAction(a["exit"])

        m = mb.addMenu("&Edit")
        m.addAction(a["undo"])
        m.addAction(a["redo"])
        m.addSeparator()
        m.addAction(a["copy"])
        m.addAction(a["select_all"])
        m.addSeparator()
        m.addAction(a["tool_snapshot"])
        m.addSeparator()
        m.addAction(a["edit_pdf"])
        m.addAction(a["edit_add_text"])
        m.addAction(a["edit_add_image"])
        m.addSeparator()
        m.addAction(a["find"])
        m.addAction(a["find_next"])
        m.addAction(a["find_prev"])
        m.addSeparator()
        m.addAction(a["prefs"])

        m = mb.addMenu("&View")
        rot = m.addMenu("&Rotate View")
        rot.addAction(a["rotate_cw"])
        rot.addAction(a["rotate_ccw"])
        nav = m.addMenu("Page &Navigation")
        for k in ("first_page", "prev_page", "next_page", "last_page"):
            nav.addAction(a[k])
        nav.addSeparator()
        nav.addAction(a["goto_page"])
        nav.addSeparator()
        nav.addAction(a["back"])
        nav.addAction(a["forward"])
        disp = m.addMenu("Page &Display")
        for act in self.layout_actions.values():
            disp.addAction(act)
        disp.addSeparator()
        disp.addAction(self.cover_action)
        z = m.addMenu("&Zoom")
        for k in ("zoom_in", "zoom_out", "fit_page", "actual", "fit_width"):
            z.addAction(a[k])
        z.addSeparator()
        z.addAction(a["tool_marquee"])
        tools = m.addMenu("&Tools")
        for k in ("tool_select", "tool_hand", "tool_marquee", "tool_snapshot"):
            tools.addAction(a[k])
        m.addSeparator()
        sh = m.addMenu("Show/&Hide")
        sh.addAction(a["sidebar"])
        sh.addAction(a["toolbar"])
        sh.addAction(a["comment_mode"])
        sh.addAction(a["menubar"])
        theme = m.addMenu("&Theme")
        tg = QActionGroup(self)
        self.theme_actions = {}
        for key, text in (("system", "Use &Windows Setting"), ("light", "&Light"), ("dark", "&Dark")):
            act = QAction(text, self, checkable=True)
            act.triggered.connect(lambda _c, k=key: self._set_theme(k))
            tg.addAction(act)
            theme.addAction(act)
            self.theme_actions[key] = act
        self.theme_actions.get(self.settings.get("theme"), self.theme_actions["system"]).setChecked(True)
        m.addAction(a["night"])
        m.addSeparator()
        ra = m.addMenu("Read &Out Loud")
        for k in ("read_page", "read_end", "read_pause", "read_stop"):
            ra.addAction(a[k])
        ra.addSeparator()
        ra.addAction(a["read_options"])
        m.addAction(a["read_mode"])
        m.addAction(a["fullscreen"])
        m.addAction(a["present"])

        dm = mb.addMenu("&Document")
        dm.addAction(a["organize"])
        ins = dm.addMenu("&Insert Pages")
        ins.addAction(a["page_insert_file"])
        ins.addAction(a["page_insert_blank"])
        dm.addAction(a["page_replace"])
        dm.addAction(a["page_extract"])
        dm.addAction(a["page_delete_pages"])
        dm.addAction(a["page_rotate"])
        dm.addSeparator()
        dm.addAction(a["page_split"])
        dm.addAction(a["combine"])
        dm.addSeparator()
        dm.addAction(a["page_crop"])
        dm.addAction(a["page_page_size"])
        dm.addSeparator()
        hf = dm.addMenu("&Header && Footer")
        hf.addAction(a["page_header_footer"])
        hf.addAction(a["page_remove_header_footer"])
        dm.addAction(a["page_page_numbers"])
        bn = dm.addMenu("&Bates Numbering")
        bn.addAction(a["page_bates"])
        bn.addAction(a["page_remove_bates"])
        wm = dm.addMenu("&Watermark")
        wm.addAction(a["page_watermark"])
        wm.addAction(a["page_remove_watermark"])
        dm.addSeparator()
        dm.addAction(a["ocr"])
        dm.addAction(a["compare"])

        cm = mb.addMenu("&Comment")
        cm.addAction(a["comment_mode"])
        cm.addSeparator()
        for d in TOOL_DEFS:
            if d is None:
                cm.addSeparator()
            else:
                cm.addAction(self.tool_actions[d[0]])
        cm.addSeparator()
        cm.addAction(self.tool_actions["stamp"])
        cm.addAction(self.tool_actions["signature"])
        cm.addAction("Add Signature…", lambda: self.annot_bar.add_signature())
        cm.addSeparator()
        cm.addAction("Show Comments List", self.show_comments)

        fm = mb.addMenu("F&orm")
        fm.addAction(a["highlight_fields"])
        fm.addAction(a["clear_form"])
        fm.addSeparator()
        fm.addAction(a["import_form"])
        fm.addAction(a["export_form"])
        fm.addSeparator()
        fm.addAction(a["flatten_form"])
        fm.addAction(a["flatten_all"])
        fm.addSeparator()
        fm.addAction(a["prepare_form"])
        for tool, _label, _ic in FIELD_TOOL_DEFS:
            fm.addAction(self.tool_actions[tool])
        fm.addAction(a["field_props"])
        fm.addAction(a["delete_field"])
        pm = mb.addMenu("&Protect")
        pm.addAction(a["encrypt"])
        pm.addAction(a["remove_security"])
        pm.addSeparator()
        pm.addAction(a["redact_mode"])
        pm.addAction(self.tool_actions[REDACT])
        pm.addAction(a["find_redact"])
        pm.addAction(a["apply_redactions"])
        pm.addAction(a["redact_props"])
        pm.addSeparator()
        pm.addAction(a["sanitize"])
        pm.addSeparator()
        pm.addAction(a["sign_cert"])
        pm.addAction(a["sig_panel"])
        pm.addAction(a["create_id"])
        self.window_menu = mb.addMenu("&Window")
        self.window_menu.aboutToShow.connect(self._fill_window_menu)

        m = mb.addMenu("&Help")
        m.addAction(a["shortcuts"])
        m.addAction(a["make_default"])
        m.addSeparator()
        m.addAction(a["about"])

    def _create_toolbar(self):
        tb = QToolBar("Main toolbar")
        tb.setObjectName("MainToolbar")
        tb.setMovable(False)
        tb.setIconSize(QSize(20, 20))
        tb.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        self.addToolBar(Qt.ToolBarArea.TopToolBarArea, tb)
        self.toolbar = tb
        a = self.a
        for k in ("open", "save", "print"):
            tb.addAction(a[k])
        tb.addSeparator()
        tb.addAction(a["undo"])
        tb.addAction(a["redo"])
        tb.addSeparator()
        tb.addAction(a["find"])
        tb.addSeparator()
        tb.addAction(a["prev_page"])
        tb.addAction(a["next_page"])
        self.page_edit = QLineEdit()
        self.page_edit.setFixedWidth(56)
        self.page_edit.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.page_edit.setToolTip("Page number (Ctrl+Shift+N)")
        self.page_edit.returnPressed.connect(self._page_entered)
        tb.addWidget(self.page_edit)
        self.page_total = QLabel(" / 0 ")
        self.page_total.setMinimumWidth(56)
        tb.addWidget(self.page_total)
        tb.addSeparator()
        tb.addAction(a["zoom_out"])
        self.zoom_combo = QComboBox()
        self.zoom_combo.setEditable(True)
        self.zoom_combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.zoom_combo.setFixedWidth(96)
        self.zoom_combo.setToolTip("Zoom")
        self.zoom_combo.addItem("Fit Width", "fit_width")
        self.zoom_combo.addItem("Fit Page", "fit_page")
        for z in ZOOM_PRESETS:
            self.zoom_combo.addItem(f"{z}%", z)
        self.zoom_combo.activated.connect(self._zoom_chosen)
        self.zoom_combo.lineEdit().returnPressed.connect(self._zoom_typed)
        tb.addWidget(self.zoom_combo)
        tb.addAction(a["zoom_in"])
        tb.addAction(a["fit_width"])
        tb.addAction(a["fit_page"])
        tb.addSeparator()
        for k in ("tool_select", "tool_hand", "tool_marquee", "tool_snapshot"):
            tb.addAction(a[k])
        tb.addSeparator()
        tb.addAction(a["rotate_cw"])
        layout_btn = QToolButton()
        layout_btn.setIcon(icon("two_page"))
        layout_btn.setToolTip("Page display")
        layout_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        lm = QMenu(layout_btn)
        for act in self.layout_actions.values():
            lm.addAction(act)
        lm.addSeparator()
        lm.addAction(self.cover_action)
        layout_btn.setMenu(lm)
        tb.addWidget(layout_btn)
        tb.addSeparator()
        tb.addAction(a["organize"])
        tb.addAction(a["edit_pdf"])
        tb.addAction(a["comment_mode"])
        tb.addAction(a["prepare_form"])
        tb.addSeparator()
        tb.addAction(a["sidebar"])
        tb.addAction(a["night"])
        tb.addAction(a["read_mode"])
        tb.addAction(a["present"])
        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        tb.addWidget(spacer)
        tb.addAction(a["prefs"])

    # ================================================================ helpers
    def _tab(self) -> DocumentTab | None:
        w = self.tabs.currentWidget()
        return w if isinstance(w, DocumentTab) else None

    def _view(self):
        t = self._tab()
        return t.view if t else None

    def _tab_of(self, view):
        for i in range(self.tabs.count()):
            t = self.tabs.widget(i)
            if t.view is view:
                return t
        return None

    def _sync_ui(self):
        t = self._tab()
        has = t is not None
        for k in ("close", "close_all", "save_as", "save_copy", "show_folder", "print", "properties", "copy",
                  "select_all", "find", "find_next", "find_prev", "next_page", "prev_page", "first_page",
                  "last_page", "goto_page", "zoom_in", "zoom_out", "fit_page", "actual", "fit_width",
                  "rotate_cw", "rotate_ccw", "sidebar", "present", "tool_select", "tool_hand",
                  "tool_marquee", "tool_snapshot", "read_mode"):
            self.a[k].setEnabled(has)
        for act in list(self.layout_actions.values()) + [self.cover_action]:
            act.setEnabled(has)
        editable = bool(has and t.doc.can_annotate())
        for tool in ANNOT_TOOLS:
            if tool in self.tool_actions:
                self.tool_actions[tool].setEnabled(editable)
        self.annot_bar.stamp_btn.setEnabled(editable)
        self.annot_bar.sign_btn.setEnabled(editable)
        self._sync_form_actions()
        self._sync_page_actions()
        self._sync_protect_actions()
        self._sync_adv_actions()
        self._sync_edit_state()
        self.page_edit.setEnabled(has)
        self.zoom_combo.setEnabled(has)
        if not has:
            self.setWindowTitle(APP_NAME)
            self.page_edit.setText("")
            self.page_total.setText(" / 0 ")
            self.zoom_combo.setEditText("")
            self.a["back"].setEnabled(False)
            self.a["forward"].setEnabled(False)
            self.size_label.setText("")
            return
        v = t.view
        self._sync_title(t)
        self.a["print"].setEnabled(t.doc.can_print())
        self._sync_page(v.current_page())
        self._sync_zoom(v.zoom)
        self._sync_history()
        self.a["copy"].setEnabled(v.has_selection())
        self.a["sidebar"].setChecked(t.sidebar_visible())
        self.tool_actions.get(v.tool, self.a["tool_select"]).setChecked(True)
        self.annot_bar.set_tool(v.tool)
        self.annot_bar.set_target(v.annots.record())
        self.prepare_bar.set_selection(v.forms.selected())
        self._sync_layout()

    def _sync_page(self, pno: int):
        t = self._tab()
        if not t:
            return
        doc = t.doc
        if doc.has_labels():
            self.page_edit.setText(doc.page_label(pno))
            self.page_total.setText(f" ({pno + 1} of {doc.page_count}) ")
        else:
            self.page_edit.setText(str(pno + 1))
            self.page_total.setText(f" / {doc.page_count} ")
        self.a["prev_page"].setEnabled(pno > 0)
        self.a["next_page"].setEnabled(pno < doc.page_count - 1)
        r = doc.page_rects[pno] if doc.page_count else None
        if r is not None:
            self.size_label.setText(f"{r.width / 72:.2f} × {r.height / 72:.2f} in  ")

    def _sync_zoom(self, zoom: float):
        if not self.zoom_combo.lineEdit().hasFocus():
            self.zoom_combo.setEditText(f"{zoom * 100:.0f}%")

    def _sync_history(self):
        v = self._view()
        self.a["back"].setEnabled(bool(v and v.can_go_back()))
        self.a["forward"].setEnabled(bool(v and v.can_go_forward()))

    def _sync_layout(self):
        v = self._view()
        if not v:
            return
        self.layout_actions[v.layout_mode].setChecked(True)
        self.cover_action.setChecked(v.cover)

    def _tab_changed(self, _idx):
        self.stack.setCurrentWidget(self.tabs if self.tabs.count() else self.welcome)
        if not self.tabs.count():
            self.welcome.refresh()
        t = self._tab()
        if t:
            t.view.setFocus()
        self._sync_ui()

    # ================================================================ opening
    def open_dialog(self):
        start = ""
        t = self._tab()
        if t:
            start = os.path.dirname(t.doc.path)
        elif self.settings.recent_files():
            start = os.path.dirname(self.settings.recent_files()[0])
        paths, _ = QFileDialog.getOpenFileNames(self, "Open", start, OPEN_FILTER)
        for p in paths:
            self.open_file(p)

    def open_file(self, path: str, page: int | None = None) -> bool:
        path = os.path.abspath(path)
        from paperleaf.core.convert import office_kind
        if office_kind(path) and os.path.exists(path):
            return bool(self.protect.from_office(path))  # Word/Excel/PowerPoint: make a PDF first
        for i in range(self.tabs.count()):
            t = self.tabs.widget(i)
            if os.path.normcase(t.doc.path) == os.path.normcase(path):
                self.tabs.setCurrentIndex(i)
                if page is not None:
                    t.view.go_to_page(page)
                return True
        if not os.path.exists(path):
            QMessageBox.warning(self, APP_NAME, f"The file could not be found:\n{path}")
            self.settings.remove_recent(path)
            self.welcome.refresh()
            return False
        password = None
        recovered = self._offer_recovery(path)
        while True:
            try:
                doc = PdfDocument(path, password, data=recovered)
                if recovered is not None:
                    doc.mark_modified()
                break
            except PasswordRequired as e:
                msg = f"“{os.path.basename(path)}” is protected.\nEnter the password to open it:"
                if e.wrong:
                    msg = "The password is incorrect. Try again:\n"
                pw, ok = QInputDialog.getText(self, "Password", msg, QLineEdit.EchoMode.Password)
                if not ok:
                    return False
                password = pw
            except Exception as exc:
                QMessageBox.critical(self, APP_NAME, f"Paperleaf could not open “{os.path.basename(path)}”.\n\n{exc}")
                return False
        tab = DocumentTab(doc)
        v = tab.view
        v.linkActivated.connect(lambda link, vv=v: self._link(vv, link))
        v.currentPageChanged.connect(lambda p, vv=v: vv is self._view() and self._sync_page(p))
        v.zoomChanged.connect(lambda z, vv=v: vv is self._view() and self._sync_zoom(z))
        v.historyChanged.connect(lambda vv=v: vv is self._view() and self._sync_history())
        v.selectionChanged.connect(lambda s, vv=v: vv is self._view() and self.a["copy"].setEnabled(s))
        v.layoutModeChanged.connect(lambda vv=v: vv is self._view() and self._sync_layout())
        v.message.connect(lambda m: self.statusBar().showMessage(m, 4000))
        v.context_menu_builder = lambda menu, pos, vv=v: self._context_menu(vv, menu, pos)
        v.annots.env = self.env
        v.annotSelected.connect(lambda rec, vv=v: vv is self._view() and self.annot_bar.set_target(rec))
        v.annotActivated.connect(lambda pno, x, tt=tab: self._open_comment(tt, pno, x))
        v.annotCreated.connect(lambda tool, pno, x, vv=v: self._annot_created(vv, tool))
        v.markupRequested.connect(lambda tool, vv=v: vv is self._view() and self._markup_selection(tool))
        v.signatureFieldClicked.connect(lambda pno, x, vv=v: self._sign_field(vv, pno, x))
        v.formAction.connect(lambda act, vv=v: self._form_action(vv, act))
        v.fieldSelected.connect(lambda fields, vv=v: vv is self._view() and self._field_selected(fields))
        v.fieldActivated.connect(lambda pno, x, vv=v: self.field_properties(vv, pno, x))
        v.fieldCreated.connect(lambda tool, pno, x, vv=v: self._field_created(vv, tool))
        v.pagesChanged.connect(lambda vv=v: vv is self._view() and self._pages_changed())
        tab.pageCommand.connect(lambda name, pages, tt=tab: self.pages.run(name, pages, tt))
        tab.organizeToggled.connect(lambda on, tt=tab: tt is self._tab() and self._sync_page_actions())
        v.forms.highlight = self.highlight_fields
        v.forms.set_prepare(self._prepare)
        v.editing.set_active(self.adv.editing)
        v.editing.selectionChanged.connect(lambda _img, vv=v: vv is self._view() and self.adv.sync_edit_actions())
        v.editing.modeChanged.connect(lambda _m, vv=v: vv is self._view() and self.adv.sync_edit_actions())
        tab.noticeAction.connect(lambda what, tt=tab: self._notice_action(tt, what))
        tab.refresh_formbar()
        tab.formCommand.connect(lambda cmd, arg, tt=tab: self._form_command(tt, cmd, arg))
        doc.listeners.append(lambda pages, tt=tab: self._doc_changed(tt))
        tab.commentAction.connect(lambda act, pno, x, extra, tt=tab: self._comment_action(tt, act, pno, x, extra))
        tab.sidebarToggled.connect(lambda vis, tt=tab: tt is self._tab() and self.a["sidebar"].setChecked(vis))

        state = {"layout": self.settings.get("default_layout")}
        dz = self.settings.get("default_zoom")
        if dz == "actual":
            state.update(zoom_mode=None, zoom=1.0)
        else:
            state["zoom_mode"] = dz if dz in ("fit_width", "fit_page") else "fit_width"
        saved = self.settings.position(path) if self.settings.get("remember_page") else None
        if saved:
            state.update(saved)
        if page is not None:
            state["page"] = page
            state["frac"] = None
        v.night = self.night
        v.restore_state(state)
        idx = self.tabs.addTab(tab, doc.name)
        self.tabs.setTabToolTip(idx, path)
        self.tabs.setCurrentIndex(idx)
        if self.settings.get("show_sidebar") and not self._read_mode:
            tab.set_sidebar(True)
        if self._read_mode:
            tab.set_strip_visible(False)
        self.settings.add_recent(path)
        self.stack.setCurrentWidget(self.tabs)
        tab._backup_step = None
        if recovered is not None:
            self._doc_changed(tab)
            self.statusBar().showMessage("Recovered unsaved changes. Save to keep them.", 8000)
        v.setFocus()
        self._sync_ui()
        self.adv.opened(tab)
        return True

    def _save_position(self, tab: DocumentTab):
        if tab.view._laid_out:
            self.settings.save_position(tab.doc.path, tab.view.state())

    def close_tab(self, index: int, ask: bool = True) -> bool:
        if index < 0:
            return True
        tab = self.tabs.widget(index)
        tab.view.forms.commit()
        if ask and tab.doc.modified:
            self.tabs.setCurrentIndex(index)
            if not self._confirm_discard(tab):
                return False
        recovery.discard(tab.doc.path)
        tab.view.annots.cancel()
        tab.view.forms.cancel()
        tab.view.editing.cancel()
        self.adv.stop_if_reading(tab.doc)
        self._save_position(tab)
        self.tabs.removeTab(index)
        tab.view.release()
        tab.doc.close()
        tab.deleteLater()
        if not self.tabs.count():
            self.stack.setCurrentWidget(self.welcome)
            self.welcome.refresh()
        self._sync_ui()
        return True

    def close_all(self) -> bool:
        while self.tabs.count():
            if not self.close_tab(self.tabs.count() - 1):
                return False
        return True

    def _cycle_tab(self, d):
        n = self.tabs.count()
        if n > 1:
            self.tabs.setCurrentIndex((self.tabs.currentIndex() + d) % n)

    def _fill_recent(self):
        self.recent_menu.clear()
        files = self.settings.recent_files()
        for i, p in enumerate(files):
            act = self.recent_menu.addAction(f"&{i + 1 if i < 9 else 0}  {os.path.basename(p)}" if i < 10
                                             else os.path.basename(p))
            act.setToolTip(p)
            act.setStatusTip(p)
            act.triggered.connect(lambda _c, pp=p: self.open_file(pp))
        if not files:
            self.recent_menu.addAction("(empty)").setEnabled(False)
        else:
            self.recent_menu.addSeparator()
            self.recent_menu.addAction("Clear Recent Files", self._clear_recent)

    def _clear_recent(self):
        self.settings.clear_recent()
        self.welcome.refresh()

    def _fill_window_menu(self):
        m = self.window_menu
        m.clear()
        m.addAction(self.a["next_tab"])
        m.addAction(self.a["prev_tab"])
        m.addSeparator()
        for i in range(self.tabs.count()):
            t = self.tabs.widget(i)
            act = m.addAction(t.doc.name)
            act.setCheckable(True)
            act.setChecked(i == self.tabs.currentIndex())
            act.triggered.connect(lambda _c, ii=i: self.tabs.setCurrentIndex(ii))

    # ============================================================ file actions
    def save_copy(self):
        t = self._tab()
        if not t:
            return
        path, _ = QFileDialog.getSaveFileName(self, "Save a Copy", t.doc.path, "PDF files (*.pdf);;All files (*.*)")
        if not path:
            return
        if os.path.normcase(os.path.abspath(path)) == os.path.normcase(t.doc.path):
            self.save()
            return
        try:
            data = t.doc.current_bytes() if t.doc.is_pdf else t.doc.data
            with open(path, "wb") as f:
                f.write(data)
            self.statusBar().showMessage(f"Saved a copy to {path}", 5000)
        except OSError as exc:
            QMessageBox.warning(self, "Save a Copy", f"Could not save the file:\n{exc}")

    def show_in_folder(self):
        t = self._tab()
        if t:
            registry.show_in_explorer(t.doc.path)

    def print_doc(self):
        t = self._tab()
        if t:
            run_print(t.doc, t.view.current_page(), self)

    def show_properties(self):
        t = self._tab()
        if t:
            dialogs.PropertiesDialog(t.doc, t.view.current_page(), self).exec()

    def copy(self):
        v = self._view()
        if v and v.copy_selection():
            self.statusBar().showMessage("Copied.", 2000)

    def find(self):
        t = self._tab()
        if t:
            t.open_find()

    def goto_page(self):
        t = self._tab()
        if not t:
            return
        doc = t.doc
        text, ok = QInputDialog.getText(self, "Go To Page", f"Page number (1 - {doc.page_count}):",
                                        text=doc.page_label(t.view.current_page()))
        if ok:
            pno = doc.find_label(text)
            if pno is None:
                QMessageBox.information(self, "Go To Page", f"There is no page “{text}”.")
            else:
                t.view.go_to_page(pno)

    def _page_entered(self):
        t = self._tab()
        if not t:
            return
        pno = t.doc.find_label(self.page_edit.text())
        if pno is None:
            self._sync_page(t.view.current_page())
        else:
            t.view.go_to_page(pno)
            t.view.setFocus()

    def _zoom_chosen(self, index: int):
        v = self._view()
        if not v:
            return
        data = self.zoom_combo.itemData(index)
        if data == "fit_width":
            v.fit_width()
        elif data == "fit_page":
            v.fit_page()
        elif data:
            v.set_zoom(float(data) / 100.0)
        v.setFocus()

    def _zoom_typed(self):
        v = self._view()
        if not v:
            return
        text = self.zoom_combo.currentText().strip().rstrip("%").strip()
        try:
            z = float(text.replace(",", "."))
            v.set_zoom(z / 100.0)
        except ValueError:
            self._sync_zoom(v.zoom)
        v.setFocus()

    def _rotate(self, d):
        t = self._tab()
        if t:
            t.view.rotate_view(d)
            t.sync_view_options()

    def _set_tool(self, tool):
        v = self._view()
        if not v:
            return
        if tool in ANNOT_TOOLS and not v.doc.can_annotate():
            self.statusBar().showMessage("This document does not allow comments or changes." if v.doc.is_pdf
                                         else "Comments can only be added to PDF files.", 5000)
            tool = Tool.SELECT
        if tool == "signature" and not self.env.current_signature:
            from paperleaf.ui import stamps
            sigs = stamps.list_images(stamps.signatures_dir())
            if sigs:
                self.env.current_signature = sigs[-1]
            else:
                self.annot_bar.add_signature()
                if not self.env.current_signature:
                    tool = Tool.SELECT
        if tool == "stamp" and not self.env.current_stamp:
            from paperleaf.ui import stamps
            label, color, dyn = stamps.STAMPS[0]
            self.env.current_stamp = ("builtin", label, color, dyn)
        if tool == REDACT:
            if not self.redact_bar.isVisible():
                self.toggle_redact_bar(True)
        elif tool in ANNOT_TOOLS and not self.annot_bar.isVisible():
            self.toggle_annotate_bar(True)
        if tool in FIELD_TOOLS and not self._prepare:
            self.toggle_prepare(True)
            if not self._prepare:
                tool = Tool.SELECT
        if self.adv.editing and tool not in (Tool.SELECT, Tool.HAND, Tool.MARQUEE):
            self.adv.toggle_edit(False)
        v.set_tool(tool)
        self.tool_actions.get(tool, self.a["tool_select"]).setChecked(True)
        self.annot_bar.set_tool(tool)
        if tool in ANNOT_TOOLS:
            hints = {"stamp": "Click on the page to place the stamp (or drag to set its size).",
                     REDACT: "Select text, or drag a box around an area, to mark it for redaction.",
                     "signature": "Click where you want to sign (or drag to set the size).",
                     "polygon": "Click to add corners; double-click to finish.",
                     "cloud": "Click to add corners; double-click to finish.",
                     "polyline": "Click to add points; double-click to finish."}
            msg = hints.get(tool) or (f"{TOOL_LABELS.get(tool, '')}: select text to mark it." if tool in MARKUP_TOOLS
                                      else f"{TOOL_LABELS.get(tool, '')}: click or drag on the page. Esc to stop.")
            self.statusBar().showMessage(msg, 6000)
        elif tool in FIELD_TOOLS:
            msg = ("Click to add radio buttons to one group. Choose the tool again to start a new group."
                   if tool == "field_radio" else
                   f"{FIELD_TOOL_LABELS[tool]}: drag on the page, or click to use the default size. Esc to stop.")
            self.statusBar().showMessage(msg, 6000)

    # =============================================================== view menu
    def toggle_sidebar(self):
        t = self._tab()
        if not t:
            return
        vis = not t.sidebar_visible()
        t.set_sidebar(vis)
        self.a["sidebar"].setChecked(vis)
        self.settings.set("show_sidebar", vis)

    def _toggle_toolbar(self):
        self.toolbar.setVisible(self.a["toolbar"].isChecked())

    def _toggle_menubar(self):
        self.menuBar().setVisible(self.a["menubar"].isChecked())

    def toggle_night(self):
        self.night = self.a["night"].isChecked()
        self.settings.set("night_mode", self.night)
        for i in range(self.tabs.count()):
            t = self.tabs.widget(i)
            t.view.set_night(self.night)
            t.sync_view_options()

    def _set_theme(self, key):
        self.settings.set("theme", key)
        if self.apply_theme:
            self.apply_theme(key)

    def toggle_read_mode(self):
        self._read_mode = not self._read_mode
        on = self._read_mode
        self.a["read_mode"].setChecked(on)
        self.menuBar().setVisible(not on and self.a["menubar"].isChecked())
        self.toolbar.setVisible(not on and self.a["toolbar"].isChecked())
        self.statusBar().setVisible(not on)
        self.tabs.tabBar().setVisible(not on)
        for i in range(self.tabs.count()):
            t = self.tabs.widget(i)
            t.set_strip_visible(not on)
            if on:
                t._sidebar_before = t.sidebar_visible()
                t.set_sidebar(False)
            elif getattr(t, "_sidebar_before", False):
                t.set_sidebar(True)
        if on:
            self.statusBar().showMessage("Read mode: press Ctrl+H or Esc to exit.", 3000)

    def toggle_fullscreen(self):
        if self.isFullScreen():
            self.showMaximized() if self._was_maximized else self.showNormal()
            self.a["fullscreen"].setChecked(False)
        else:
            self._was_maximized = self.isMaximized()
            self.showFullScreen()
            self.a["fullscreen"].setChecked(True)

    def present(self):
        t = self._tab()
        if not t or self._presentation is not None:
            return
        w = PresentationWindow(t.doc, t.view.current_page(), self.night, self.screen())
        w.closed.connect(lambda p, tt=t: self._presentation_closed(tt, p))
        self._presentation = w
        w.start()

    def _presentation_closed(self, tab, page):
        self._presentation = None
        if tab.parent() is not None:
            tab.view.go_to_page(page)
        self.activateWindow()

    def preferences(self):
        dlg = dialogs.PreferencesDialog(self.settings, self)
        if dlg.exec():
            if self.apply_theme:
                self.apply_theme(self.settings.get("theme"))
            self.theme_actions.get(self.settings.get("theme"), self.theme_actions["system"]).setChecked(True)
        self.welcome.refresh()

    def about(self):
        QMessageBox.about(self, f"About {APP_NAME}", dialogs.about_text())

    def _integrate(self):
        try:
            if not registry.is_registered_here():
                registry.register()
            self.welcome.refresh()
            if not registry.is_default():
                QMessageBox.information(
                    self, "Make Paperleaf the default",
                    "Paperleaf is now in “Open with” and in the PDF right-click menu.\n\n"
                    "Windows will now open Settings. Under “.pdf”, choose Paperleaf to open PDFs "
                    "with a double-click.")
                registry.open_default_apps()
        except OSError as exc:
            QMessageBox.warning(self, APP_NAME, f"Could not register with Windows:\n{exc}")

    # ============================================================ editing
    def _sync_title(self, t):
        mark = " *" if t.doc.modified else ""
        secured = " (SECURED)" if t.doc.is_pdf and (t.doc.encrypted or (t.doc.new_security or {}).get(
            "method", pymupdf.PDF_ENCRYPT_NONE) != pymupdf.PDF_ENCRYPT_NONE) else ""
        self.setWindowTitle(f"{t.doc.name}{secured}{mark} - {APP_NAME}")
        idx = self.tabs.indexOf(t)
        if idx >= 0:
            self.tabs.setTabText(idx, t.doc.name + mark)
            self.tabs.setTabToolTip(idx, t.doc.path)

    def _sync_edit_state(self):
        t = self._tab()
        doc = t.doc if t else None
        un = doc.undo_name() if doc else None
        rn = doc.redo_name() if doc else None
        self.a["undo"].setEnabled(bool(un))
        self.a["redo"].setEnabled(bool(rn))
        self.a["undo"].setText(f"&Undo {un}" if un else "&Undo")
        self.a["redo"].setText(f"&Redo {rn}" if rn else "&Redo")
        self.a["undo"].setToolTip(f"Undo {un} (Ctrl+Z)" if un else "Undo (Ctrl+Z)")
        self.a["redo"].setToolTip(f"Redo {rn} (Ctrl+Y)" if rn else "Redo (Ctrl+Y)")
        self.a["save"].setEnabled(bool(doc and doc.is_pdf and doc.modified))

    def _doc_changed(self, tab):
        self._sync_title(tab)
        if tab is self._tab():
            self._sync_edit_state()
            self._sync_form_actions()
            self._sync_protect_actions()
            self.annot_bar.set_target(tab.view.annots.record())

    def undo(self):
        t = self._tab()
        if t and t.view.forms.editor is not None:
            t.view.forms.editor.finish(False)  # undo while typing: drop the typing
            return
        if t and t.view.annots.editor is None:
            t.view.annots.cancel()
            t.doc.undo()

    def redo(self):
        t = self._tab()
        if t and t.view.forms.editor is not None:
            return
        if t and t.view.annots.editor is None:
            t.view.annots.cancel()
            t.doc.redo()

    def toggle_annotate_bar(self, show=None):
        if self.sender() is self.a["comment_mode"]:
            vis = self.a["comment_mode"].isChecked()   # toggled from the button / menu
        elif isinstance(show, bool):
            vis = show
        else:
            vis = not self.annot_bar.isVisible()
        self.annot_bar.setVisible(vis)
        self.a["comment_mode"].setChecked(vis)
        self.settings.set("show_annotate", vis)
        if not vis:
            v = self._view()
            if v is not None and v.tool in ANNOT_TOOLS:
                self._set_tool(Tool.SELECT)

    def show_comments(self):
        t = self._tab()
        if t:
            t.show_panel("comments")
            self.a["sidebar"].setChecked(True)

    def _annot_created(self, view, tool):
        if tool not in STAY_ACTIVE and view is self._view():
            self._set_tool(Tool.SELECT)

    def _markup_selection(self, tool):
        v = self._view()
        if not v or not v.has_selection():
            return
        if not v.doc.can_annotate():
            self.statusBar().showMessage("This document does not allow comments.", 4000)
            return
        ranges = v.selection_ranges()
        if tool == REDACT:
            from paperleaf.core import protect
            try:
                protect.mark_ranges(v.doc, ranges, self.env.redact_style(), self.env.author())
            except Exception as exc:
                QMessageBox.warning(self, APP_NAME, f"Could not mark the text:\n{exc}")
                return
            v.clear_selection()
            return
        try:
            an.add_markup_ranges(v.doc, ranges, tool, self.env.style(tool), self.env.author())
        except Exception as exc:
            QMessageBox.warning(self, APP_NAME, f"Could not add the markup:\n{exc}")
            return
        v.clear_selection()

    def _edit_selected_style(self, changes):
        v = self._view()
        rec = v.annots.record() if v else None
        if rec is None:
            return
        try:
            an.set_style(v.doc, rec.pno, rec.xref, **changes)
        except Exception as exc:
            self.statusBar().showMessage(f"Could not change the comment: {exc}", 5000)

    def _delete_selected_annot(self):
        v = self._view()
        if v:
            v.annots.delete_selected()

    def _open_comment(self, tab, pno, xref):
        rec = an.find(tab.doc, pno, xref)
        if rec is None:
            return
        dlg = CommentDialog(tab.doc, rec, self.env.author(), self, editable=tab.doc.can_annotate())
        dlg.exec()

    def _annot_menu(self, view, rec, menu):
        editable = view.doc.can_annotate()
        tab = self._tab_of(view)
        if rec.kind == "FreeText" and not rec.is_symbol:
            act = menu.addAction("Edit Text", lambda: view.annots.edit_text(rec))
            act.setEnabled(editable)
        title = "Open Note…" if rec.kind == "Text" else "Open Comment…"
        menu.addAction(icon("note"), title, lambda: self._open_comment(tab, rec.pno, rec.xref))
        act = menu.addAction(icon("reply"), "Reply…", lambda: self._comment_action(tab, "reply", rec.pno, rec.xref, ""))
        act.setEnabled(editable)
        sm = menu.addMenu("Set Status")
        sm.setEnabled(editable)
        for st in an.STATUSES:
            sm.addAction(st, lambda s=st: self._comment_action(tab, "status", rec.pno, rec.xref, s))
        menu.addSeparator()
        act = menu.addAction("Properties…", lambda: self._annot_properties(view, rec))
        act.setEnabled(editable)
        act = menu.addAction(icon("delete"), "Delete", lambda: self._comment_action(tab, "delete", rec.pno, rec.xref, ""))
        act.setEnabled(editable)

    def _annot_properties(self, view, rec):
        PropertiesDialog(view.doc, rec, self).exec()

    def _comment_action(self, tab, action, pno, xref, extra):
        doc = tab.doc
        try:
            if action == "open":
                tab.reveal_annot(pno, xref)
                self._open_comment(tab, pno, xref)
            elif action == "reply":
                text, ok = QInputDialog.getMultiLineText(self, "Reply", "Your reply:")
                if ok and text.strip():
                    an.reply(doc, pno, xref, text.strip(), self.env.author())
            elif action == "status":
                an.set_status(doc, pno, xref, extra, self.env.author())
            elif action == "delete":
                an.delete(doc, pno, xref)
        except Exception as exc:
            QMessageBox.warning(self, APP_NAME, f"That did not work:\n{exc}")

    # ================================================================== protect
    def _sync_protect_actions(self):
        t = self._tab()
        pdf = bool(t is not None and t.doc.is_pdf)
        for k in ("encrypt", "remove_security", "sanitize", "compress", "export_word", "export_images",
                  "export_text", "export_html", "find_redact", "apply_redactions"):
            self.a[k].setEnabled(pdf)
        self.a["redact_mode"].setEnabled(pdf or self.redact_bar.isVisible())
        n = 0
        if pdf and self.redact_bar.isVisible():
            from paperleaf.core import protect
            n = len(protect.marks(t.doc))
        self.redact_bar.set_count(n)

    def toggle_redact_bar(self, show=None):
        if self.sender() is self.a["redact_mode"]:
            vis = self.a["redact_mode"].isChecked()
        elif isinstance(show, bool):
            vis = show
        else:
            vis = not self.redact_bar.isVisible()
        self.redact_bar.setVisible(vis)
        self.a["redact_mode"].setChecked(vis)
        v = self._view()
        if vis and v is not None and v.tool != REDACT:
            self._set_tool(REDACT)
        elif not vis and v is not None and v.tool == REDACT:
            self._set_tool(Tool.SELECT)
        self._sync_protect_actions()

    # ================================================================== pages
    def _sync_page_actions(self):
        from paperleaf.core import pages as P
        t = self._tab()
        pdf = bool(t is not None and t.doc.is_pdf)
        organize = bool(pdf and P.can_organize(t.doc))
        modify = bool(pdf and t.doc.can_modify())
        self.a["organize"].setEnabled(pdf)
        self.a["organize"].setChecked(bool(t is not None and t.organizing()))
        for name in ("insert_file", "insert_blank", "replace", "delete_pages", "rotate", "crop", "page_size"):
            self.a[f"page_{name}"].setEnabled(organize)
        for name in ("extract", "split"):
            self.a[f"page_{name}"].setEnabled(pdf)
        for name in ("header_footer", "remove_header_footer", "page_numbers", "bates", "remove_bates",
                     "watermark", "remove_watermark"):
            self.a[f"page_{name}"].setEnabled(modify)

    def toggle_organize(self, on=None):
        t = self._tab()
        if t is None:
            self.a["organize"].setChecked(False)
            return
        if self.sender() is self.a["organize"]:
            on = self.a["organize"].isChecked()
        elif not isinstance(on, bool):
            on = not t.organizing()
        if on and not t.doc.is_pdf:
            self.statusBar().showMessage("Pages can only be organized in PDF files.", 5000)
            on = False
        t.set_organize(on)
        self.a["organize"].setChecked(t.organizing())

    def _pages_changed(self):
        v = self._view()
        if v is not None:
            self._sync_page(v.current_page())
            self._sync_history()
            self.a["copy"].setEnabled(False)

    # ============================================================ advanced tools
    def _sync_adv_actions(self):
        t = self._tab()
        pdf = bool(t is not None and t.doc.is_pdf)
        for k in ("ocr", "sign_cert", "sig_panel"):
            self.a[k].setEnabled(pdf)
        for k in ("read_page", "read_end"):
            self.a[k].setEnabled(t is not None)
        self.adv.sync_edit_actions()

    def _notice_action(self, tab, what):
        self.tabs.setCurrentWidget(tab)
        if what == "signatures":
            self.adv.signature_panel(tab)
        elif what == "ocr":
            self.adv.recognize()

    def _create_id(self):
        from paperleaf.ui.advanced_dialogs import CreateIdDialog
        from paperleaf.core import signing
        if not signing.available():
            QMessageBox.warning(self, APP_NAME, "Certificate signing is not installed with this copy of Paperleaf.")
            return
        dlg = CreateIdDialog(self.env.author(), self)
        if dlg.exec() and dlg.path:
            self.statusBar().showMessage(f"Digital ID saved: {dlg.path}", 8000)

    # ================================================================== forms
    def _sync_form_actions(self):
        t = self._tab()
        doc = t.doc if t else None
        info = F.form_info(doc) if doc is not None and doc.is_pdf else F.FormInfo()
        has = info.count > 0
        fill = bool(doc is not None and F.can_fill(doc))
        self.a["highlight_fields"].setEnabled(has)
        self.a["clear_form"].setEnabled(has and fill)
        self.a["import_form"].setEnabled(has and fill)
        self.a["export_form"].setEnabled(has)
        flat = bool(has and doc is not None and F.can_flatten(doc))
        self.a["flatten_form"].setEnabled(flat)
        self.a["flatten_all"].setEnabled(flat)
        self.a["prepare_form"].setEnabled(bool(doc is not None and doc.is_pdf))
        prep = bool(doc is not None and self._prepare and F.can_prepare(doc))
        for tool, _l, _i in FIELD_TOOL_DEFS:
            self.tool_actions[tool].setEnabled(prep)
        sel = t.view.forms.selected() if t and self._prepare else []
        self.a["field_props"].setEnabled(len(sel) == 1)
        self.a["delete_field"].setEnabled(bool(sel) and prep)

    def _form_command(self, tab, cmd, arg):
        if cmd == "highlight":
            self.set_highlight_fields(bool(arg))
        elif cmd == "clear":
            self.tabs.setCurrentWidget(tab)
            self.clear_form()

    def set_highlight_fields(self, on: bool):
        self.highlight_fields = on
        self.settings.set("highlight_fields", on)
        self.a["highlight_fields"].setChecked(on)
        for i in range(self.tabs.count()):
            t = self.tabs.widget(i)
            t.view.forms.set_highlight(on)
            t.formbar.set_highlight(on)

    def toggle_prepare(self, on=None):
        if self.sender() is self.a["prepare_form"]:
            on = self.a["prepare_form"].isChecked()
        elif not isinstance(on, bool):
            on = not self._prepare
        t = self._tab()
        if on and t is not None and not t.doc.is_pdf:
            self.statusBar().showMessage("Form fields can only be added to PDF files.", 5000)
            on = False
        self._prepare = on
        self.a["prepare_form"].setChecked(on)
        self.prepare_bar.setVisible(on)
        for i in range(self.tabs.count()):
            tt = self.tabs.widget(i)
            tt.view.forms.set_prepare(on)
            tt.refresh_formbar()
        v = self._view()
        if v is not None and not on and v.tool in FIELD_TOOLS:
            self._set_tool(Tool.SELECT)
        if on and t is not None:
            if not F.can_prepare(t.doc):
                self.statusBar().showMessage("This document does not allow adding or changing form fields.", 6000)
            else:
                self.statusBar().showMessage("Prepare Form: choose a field type, then click or drag on the page. "
                                             "Double-click a field to change its properties.", 8000)
        self._sync_form_actions()

    def _field_selected(self, fields):
        self.prepare_bar.set_selection(fields)
        self._sync_form_actions()

    def _field_created(self, view, tool):
        if view is self._view() and tool not in FIELD_TOOL_STAY:
            self._set_tool(Tool.SELECT)

    def _delete_selected_fields(self):
        v = self._view()
        if v is not None and v.forms.prepare:
            v.forms.delete_selected()

    def field_properties(self, view=None, pno=None, xref=None):
        from paperleaf.ui.form_dialogs import FieldPropertiesDialog
        v = view if view is not None else self._view()
        if v is None:
            return
        f = F.find(v.doc, pno, xref) if pno is not None else (v.forms.selected() or [None])[0]
        if f is None:
            return
        if not F.can_prepare(v.doc):
            self.statusBar().showMessage("This document does not allow changing form fields.", 5000)
            return
        FieldPropertiesDialog(v.doc, f, self).exec()

    def clear_form(self):
        t = self._tab()
        if not t or not F.can_fill(t.doc):
            return
        t.view.forms.commit()
        ans = QMessageBox.question(self, "Clear Form", "Clear all the form fields? Read-only fields are kept.\n\n"
                                   "You can undo this.", QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if ans != QMessageBox.StandardButton.Yes:
            return
        try:
            changed = F.reset_form(t.doc)
        except Exception as exc:
            QMessageBox.warning(self, "Clear Form", f"Could not clear the form:\n{exc}")
            return
        self.statusBar().showMessage("The form was cleared." if changed else "The form is already clear.", 4000)

    def flatten_form(self, comments=False):
        t = self._tab()
        if not t or not F.can_flatten(t.doc) or not F.form_info(t.doc).count:
            return
        t.view.forms.commit()
        t.view.annots.cancel()
        what = "form fields and comments" if comments else "form fields"
        if not t.doc.journaled:
            ans = QMessageBox.question(self, "Flatten", f"Flatten the {what}? Their contents become part of the "
                                       "page and can no longer be edited. This can't be undone.",
                                       QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            if ans != QMessageBox.StandardButton.Yes:
                return
        try:
            F.flatten(t.doc, comments)
        except Exception as exc:
            QMessageBox.warning(self, "Flatten", f"Could not flatten the {what}:\n{exc}")
            return
        self.statusBar().showMessage(f"The {what} are now part of the page. Use Undo to bring them back.", 6000)

    def import_form_data(self):
        t = self._tab()
        if not t or not F.can_fill(t.doc):
            return
        t.view.forms.commit()
        path, _ = QFileDialog.getOpenFileName(self, "Import Form Data", os.path.dirname(t.doc.path), F.IMPORT_FILTER)
        if not path:
            return
        try:
            changed, unknown = F.import_data(t.doc, path)
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "Import Form Data", f"Could not read “{os.path.basename(path)}”.\n\n{exc}")
            return
        msg = f"Filled in {changed} field{'s' if changed != 1 else ''} from {os.path.basename(path)}."
        if unknown:
            names = ", ".join(unknown[:8]) + ("…" if len(unknown) > 8 else "")
            QMessageBox.information(self, "Import Form Data",
                                    f"{msg}\n\n{len(unknown)} field{'s' if len(unknown) != 1 else ''} in the file "
                                    f"{'are' if len(unknown) != 1 else 'is'} not in this form: {names}")
        self.statusBar().showMessage(msg, 5000)

    def export_form_data(self):
        t = self._tab()
        if not t or not F.form_info(t.doc).count:
            return
        t.view.forms.commit()
        base = os.path.splitext(t.doc.path)[0] + ".fdf"
        path, flt = QFileDialog.getSaveFileName(self, "Export Form Data", base, F.DATA_FILTER)
        if not path:
            return
        if not os.path.splitext(path)[1]:
            path += ".xfdf" if flt.startswith("XFDF") else ".json" if flt.startswith("JSON") else ".fdf"
        try:
            F.export_data(t.doc, path)
        except OSError as exc:
            QMessageBox.warning(self, "Export Form Data", f"Could not save the file:\n{exc}")
            return
        self.statusBar().showMessage(f"Saved the form data to {path}", 5000)

    def popup_menu(self, menu):
        """Show a popup menu at the mouse and return the chosen action (or None)."""
        return menu.exec(QCursor.pos())

    def _sign_field(self, view, pno, xref):
        """A signature field was clicked: sign with a certificate, or place a saved signature in it."""
        f = F.find(view.doc, pno, xref)
        if f is None:
            return
        from paperleaf.core import signing
        if signing.available():
            menu = QMenu(self)
            cert = menu.addAction(icon("certificate"), "Sign with Certificate…")
            menu.addAction(icon("sign"), "Place Signature Image")
            chosen = self.popup_menu(menu)
            if chosen is None:
                return
            if chosen is cert:
                self.adv.sign_certificate(view, f.name)
                return
        if not view.doc.can_annotate():
            self.statusBar().showMessage("This document does not allow adding a signature.", 5000)
            return
        if not self.env.signature():
            from paperleaf.ui import stamps
            sigs = stamps.list_images(stamps.signatures_dir())
            if sigs:
                self.env.current_signature = sigs[-1]
            else:
                self.annot_bar.add_signature()
        sig = self.env.signature()
        if not sig:
            return
        png, w, h, _subject = sig
        try:
            F.sign_field(view.doc, f, png, w, h, self.env.author())
        except Exception as exc:
            QMessageBox.warning(self, APP_NAME, f"Could not place the signature:\n{exc}")
            return
        self.statusBar().showMessage("Signature placed. Choose another saved signature in the Sign menu "
                                     "of the Comment & Sign toolbar.", 6000)

    def _form_action(self, view, act: dict):
        kind = act.get("type", "")
        if kind == "ResetForm":
            self.clear_form()
        elif kind == "Named" and act.get("name"):
            self._link(view, {"kind": pymupdf.LINK_NAMED, "name": act["name"], "page": -1})
        elif kind == "URI" and act.get("uri"):
            self._open_uri(act["uri"])
        elif kind == "SubmitForm":
            QMessageBox.information(self, APP_NAME, "This button would send the form over the internet. "
                                    "Paperleaf never sends anything: save the form and send the file yourself.")
        elif kind == "JavaScript":
            self.statusBar().showMessage("This button runs JavaScript, which Paperleaf never runs.", 5000)
        else:
            self.statusBar().showMessage("Paperleaf can't run this button's action.", 4000)

    # ---------------------------------------------------------------- saving
    def save(self, tab=None) -> bool:
        t = tab if isinstance(tab, DocumentTab) else self._tab()
        if not t:
            return False
        if not t.doc.is_pdf:
            return self.save_as(t)
        t.view.annots.cancel()
        t.view.forms.commit()
        if t.doc.is_signed() and not t.doc.keeps_signatures():
            ans = QMessageBox.warning(
                self, "Save", "This document has digital signatures. Removing content rewrites the file, "
                "so those signatures will show as invalid.\n\nSave anyway? (Use Save As to keep the "
                "original intact.)",
                QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Cancel)
            if ans != QMessageBox.StandardButton.Save:
                return False
        try:
            t.doc.save()
        except Exception as exc:
            QMessageBox.warning(self, "Save", f"Could not save “{t.doc.name}”.\n\n{exc}\n\n"
                                              "If another program has the file open, close it or use Save As.")
            return False
        recovery.discard(t.doc.path)
        self._sync_title(t)
        self._sync_edit_state()
        self.adv.check_signatures(t)
        self.statusBar().showMessage(f"Saved {t.doc.path}", 4000)
        return True

    def save_as(self, tab=None) -> bool:
        t = tab if isinstance(tab, DocumentTab) else self._tab()
        if not t:
            return False
        if not t.doc.is_pdf:
            QMessageBox.information(self, "Save As", "Only PDF files can be saved with changes. "
                                                     "Use Save a Copy to keep the original file.")
            return False
        t.view.annots.cancel()
        t.view.forms.commit()
        base = t.doc.path if t.doc.path.lower().endswith(".pdf") else os.path.splitext(t.doc.path)[0] + ".pdf"
        path, _ = QFileDialog.getSaveFileName(self, "Save As", base, "PDF files (*.pdf)")
        if not path:
            return False
        if not path.lower().endswith(".pdf"):
            path += ".pdf"
        old = t.doc.path
        for i in range(self.tabs.count()):
            other = self.tabs.widget(i)
            if other is not t and os.path.normcase(other.doc.path) == os.path.normcase(os.path.abspath(path)):
                QMessageBox.warning(self, "Save As", "That file is open in another tab. Close it first.")
                return False
        try:
            t.doc.save(path)
        except Exception as exc:
            QMessageBox.warning(self, "Save As", f"Could not save the file.\n\n{exc}")
            return False
        recovery.discard(old)
        self.settings.add_recent(t.doc.path)
        self._sync_title(t)
        self._sync_edit_state()
        self.adv.check_signatures(t)
        self.statusBar().showMessage(f"Saved {t.doc.path}", 4000)
        return True

    def _confirm_discard(self, tab) -> bool:
        """Ask to save unsaved changes. True = OK to close."""
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle(APP_NAME)
        box.setText(f"Do you want to save changes to “{tab.doc.name}” before closing?")
        box.setStandardButtons(QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard
                               | QMessageBox.StandardButton.Cancel)
        box.setDefaultButton(QMessageBox.StandardButton.Save)
        ans = box.exec()
        if ans == QMessageBox.StandardButton.Save:
            return self.save(tab)
        if ans == QMessageBox.StandardButton.Discard:
            recovery.discard(tab.doc.path)
            return True
        return False

    def _backup_all(self):
        for i in range(self.tabs.count()):
            t = self.tabs.widget(i)
            doc = t.doc
            if not doc.is_pdf or not doc.modified or doc.redo_name():
                continue  # backing up while redo is possible would drop the redo history
            step = doc._step()
            if getattr(t, "_backup_step", None) == step:
                continue
            try:
                recovery.write(doc)
                t._backup_step = doc._step()
            except Exception:
                pass

    def _offer_recovery(self, path):
        data = recovery.find(path)
        if data is None:
            return None
        ans = QMessageBox.question(
            self, "Recover unsaved changes",
            f"Paperleaf closed without saving your changes to “{os.path.basename(path)}”.\n\n"
            "Do you want to recover them?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if ans == QMessageBox.StandardButton.Yes:
            return data
        recovery.discard(path)
        return None

    # ================================================================= links
    def _link(self, view, link: dict):
        kind = link.get("kind")
        if kind == pymupdf.LINK_GOTO or (kind == pymupdf.LINK_NAMED and link.get("page", -1) >= 0
                                         and "name" not in link):
            page = link.get("page", -1)
            if page is not None and page >= 0:
                to = link.get("to")
                view.go_to_page(page, to if isinstance(to, pymupdf.Point) and (to.x or to.y) else None)
            return
        if kind == pymupdf.LINK_URI:
            self._open_uri(link.get("uri", ""))
            return
        if kind == pymupdf.LINK_GOTOR:
            tab = self._tab_of(view)
            target = link.get("file", "")
            if target and tab:
                path = os.path.normpath(os.path.join(os.path.dirname(tab.doc.path), target))
                if os.path.splitext(path)[1].lower() == ".pdf" and os.path.exists(path):
                    page = link.get("page", 0)
                    self.open_file(path, page if page and page > 0 else None)
                else:
                    QMessageBox.information(self, APP_NAME, f"The linked document was not found:\n{path}")
            return
        if kind == pymupdf.LINK_LAUNCH:
            QMessageBox.warning(self, APP_NAME,
                                "This document tried to launch a file or program:\n\n"
                                f"{link.get('file', '')}\n\nPaperleaf blocked it for your safety.")
            return
        if kind == pymupdf.LINK_NAMED:
            name = (link.get("name") or "").lower()
            actions = {"nextpage": view.next_page, "prevpage": view.prev_page, "firstpage": view.first_page,
                       "lastpage": view.last_page, "goback": view.go_back, "goforward": view.go_forward,
                       "print": self.print_doc, "find": self.find}
            fn = actions.get(name)
            if fn:
                fn()

    def _open_uri(self, uri: str):
        uri = uri.strip()
        if not uri:
            return
        scheme = urllib.parse.urlsplit(uri).scheme.lower()
        if scheme in ("", "www") or uri.lower().startswith("www."):
            uri = "https://" + uri
            scheme = "https"
        if scheme == "file":
            path = QUrl(uri).toLocalFile()
            if path.lower().endswith(".pdf") and os.path.exists(path):
                self.open_file(path)
            else:
                QMessageBox.warning(self, APP_NAME, f"Paperleaf blocked a link to a local file:\n{path}")
            return
        if scheme not in ("http", "https", "mailto"):
            QMessageBox.warning(self, APP_NAME, f"Paperleaf blocked a link of an unsupported type:\n{uri}")
            return
        if self.settings.get("confirm_links"):
            box = QMessageBox(self)
            box.setWindowTitle("Open link")
            box.setIcon(QMessageBox.Icon.Question)
            box.setText("This document wants to open a web address:")
            box.setInformativeText(uri)
            box.setStandardButtons(QMessageBox.StandardButton.Open | QMessageBox.StandardButton.Cancel)
            cb = QCheckBox("Don't ask again")
            box.setCheckBox(cb)
            if box.exec() != QMessageBox.StandardButton.Open:
                return
            if cb.isChecked():
                self.settings.set("confirm_links", False)
        QDesktopServices.openUrl(QUrl(uri))

    # ============================================================ context menu
    def _context_menu(self, view, menu: QMenu, pos=None):
        a = self.a
        if view.forms.prepare and pos is not None:
            f = view.forms.field_at(pos)
            if f is not None:
                if (f.pno, f.xref) not in view.forms.sel:
                    view.forms.select([(f.pno, f.xref)])
                menu.addAction(a["field_props"])
                menu.addAction(a["delete_field"])
                menu.addSeparator()
                menu.addAction(a["prepare_form"])
                return
        rec = view.annots.record_at(pos) if pos is not None else None
        if rec is not None:
            view.annots.select(rec.pno, rec.xref)
            self._annot_menu(view, rec, menu)
            return
        if view.has_selection():
            menu.addAction(a["copy"])
            if view.doc.can_annotate():
                menu.addAction(a["hl_sel"])
                menu.addAction(a["ul_sel"])
                menu.addAction(a["so_sel"])
            menu.addSeparator()
        menu.addAction(a["select_all"])
        menu.addSeparator()
        menu.addAction(a["prev_page"])
        menu.addAction(a["next_page"])
        menu.addAction(a["back"])
        menu.addSeparator()
        menu.addAction(a["zoom_in"])
        menu.addAction(a["zoom_out"])
        menu.addAction(a["fit_width"])
        menu.addAction(a["fit_page"])
        menu.addAction(a["rotate_cw"])
        menu.addSeparator()
        menu.addAction(a["tool_select"])
        menu.addAction(a["tool_hand"])
        menu.addAction(a["tool_snapshot"])
        menu.addSeparator()
        menu.addAction(a["find"])
        menu.addAction(a["print"])
        menu.addAction(a["properties"])

    # ================================================================= events
    def eventFilter(self, obj, ev):
        if obj is self.tabs.tabBar() and ev.type() == QEvent.Type.MouseButtonRelease \
                and ev.button() == Qt.MouseButton.MiddleButton:
            idx = obj.tabAt(ev.position().toPoint())
            if idx >= 0:
                self.close_tab(idx)
                return True
        return super().eventFilter(obj, ev)

    def keyPressEvent(self, e):
        if e.key() == Qt.Key.Key_Escape:
            v = self._view()
            t = self._tab()
            if t is not None and t.organizing():
                self.toggle_organize(False)
                return
            if v is not None and (v.tool in ANNOT_TOOLS or v.tool in FIELD_TOOLS):
                self._set_tool(Tool.SELECT)
                return
            if self._read_mode:
                self.toggle_read_mode()
                return
            if self.isFullScreen():
                self.toggle_fullscreen()
                return
        super().keyPressEvent(e)

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls() and any(u.isLocalFile() for u in e.mimeData().urls()):
            e.acceptProposedAction()

    def dropEvent(self, e):
        for u in e.mimeData().urls():
            if u.isLocalFile():
                self.open_file(u.toLocalFile())
        e.acceptProposedAction()

    def open_paths(self, paths: list[str], page: int | None = None):
        for p in paths:
            self.open_file(p, page)
        self.bring_to_front()

    def handle_message(self, msg: dict, first: bool = False):
        """A message from another Paperleaf process (or this one's command line)."""
        paths = msg.get("paths") or []
        tool = msg.get("tool")
        if tool:
            self.run_tool(tool, paths)
            return
        page = msg.get("page")
        self.open_paths(paths, page - 1 if first and page and page > 0 else page)

    def run_tool(self, tool: str, paths: list[str]):
        """Explorer's right-click tools. Combine gathers the files that arrive
        close together (Explorer starts one Paperleaf per selected file)."""
        self.bring_to_front()
        if tool == "combine":
            dlg = getattr(self, "_combine_dialog", None)
            if dlg is not None and dlg.isVisible():
                dlg.add_paths(paths)
                return
            self._combine_pending = getattr(self, "_combine_pending", []) + list(paths)
            if not getattr(self, "_combine_timer", None):
                self._combine_timer = QTimer(self)
                self._combine_timer.setSingleShot(True)
                self._combine_timer.setInterval(700)
                self._combine_timer.timeout.connect(self._start_combine)
            self._combine_timer.start()
            return
        if tool == "topdf":
            for p in paths:
                self.protect.from_office(p)
            return
        protect_tools = {"compress": self.protect.compress, "word": self.protect.export_word,
                         "images": self.protect.export_images, "protect": self.protect.security}
        if tool in protect_tools:
            for p in paths[:1]:
                if self.open_file(p):
                    QTimer.singleShot(0, protect_tools[tool])
            return
        commands = {"split": "split", "extract": "extract", "rotate": "rotate"}
        if tool not in commands:
            self.open_paths(paths)
            return
        for p in paths[:1]:
            if self.open_file(p):
                QTimer.singleShot(0, lambda: self.pages.run(commands[tool]))

    def _start_combine(self):
        paths, self._combine_pending = list(dict.fromkeys(self._combine_pending)), []
        self.pages.combine(paths)

    def bring_to_front(self):
        if self.isMinimized():
            self.showNormal()
        self.show()
        self.raise_()
        self.activateWindow()

    def restore_session(self):
        if not self.settings.get("restore_tabs"):
            return
        paths = self.settings.value("session/tabs", []) or []
        if isinstance(paths, str):
            paths = [paths]
        for p in paths:
            if os.path.exists(p):
                self.open_file(p)

    def closeEvent(self, e):
        if self._presentation is not None:
            self._presentation.close()
        for i in range(self.tabs.count()):
            t = self.tabs.widget(i)
            t.view.annots.cancel()
            t.view.forms.commit()
            if t.doc.modified:
                self.tabs.setCurrentIndex(i)
                if not self._confirm_discard(t):
                    e.ignore()
                    return
        for i in range(self.tabs.count()):
            recovery.discard(self.tabs.widget(i).doc.path)
        paths = []
        for i in range(self.tabs.count()):
            t = self.tabs.widget(i)
            self._save_position(t)
            paths.append(t.doc.path)
        self.settings.set("session/tabs", paths)
        self.settings.set("window/geometry", self.saveGeometry())
        self.settings.sync()
        self.adv.cleanup()
        super().closeEvent(e)
