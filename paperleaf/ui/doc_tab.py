"""One open document: side panel + find bar + page view."""
from __future__ import annotations

from PySide6.QtCore import QSize, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QActionGroup, QKeySequence
from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QLineEdit, QMenu, QSplitter, QStackedWidget, QToolBar, QToolButton,
    QTreeWidgetItem, QVBoxLayout, QWidget,
)

from paperleaf.core.document import PdfDocument, make_search_regex
from paperleaf.panels.panels import AttachmentsPanel, OutlinePanel, SearchResultsPanel, ThumbnailPanel
from paperleaf.ui.form_bar import FormBar
from paperleaf.ui.notice_bar import NoticeBar
from paperleaf.ui.icons import icon
from paperleaf.viewer.view import DocumentView


class FindBar(QWidget):
    closed = Signal()

    def __init__(self, view: DocumentView, results: SearchResultsPanel, parent=None):
        super().__init__(parent)
        self.view = view
        self.results = results
        self.setObjectName("FindBar")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(8, 4, 8, 4)
        lay.setSpacing(4)
        self.edit = QLineEdit()
        self.edit.setPlaceholderText("Find in document")
        self.edit.setClearButtonEnabled(True)
        self.edit.setMinimumWidth(260)
        self.edit.addAction(icon("search"), QLineEdit.ActionPosition.LeadingPosition)
        lay.addWidget(self.edit)
        self.count = QLabel("")
        self.count.setMinimumWidth(90)
        lay.addWidget(self.count)
        self.prev_btn = self._button("up", "Previous result (Shift+Enter)", lambda: self.view.search_next(True))
        self.next_btn = self._button("down", "Next result (Enter)", lambda: self.view.search_next(False))
        lay.addWidget(self.prev_btn)
        lay.addWidget(self.next_btn)
        self.opt_btn = QToolButton()
        self.opt_btn.setText("Options")
        self.opt_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        menu = QMenu(self.opt_btn)
        self.case_act = menu.addAction("Match case")
        self.case_act.setCheckable(True)
        self.word_act = menu.addAction("Whole words only")
        self.word_act.setCheckable(True)
        self.case_act.toggled.connect(lambda _c: self._run())
        self.word_act.toggled.connect(lambda _c: self._run())
        self.opt_btn.setMenu(menu)
        lay.addWidget(self.opt_btn)
        lay.addStretch(1)
        lay.addWidget(self._button("close", "Close (Esc)", self.close_bar))
        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(250)
        self._debounce.timeout.connect(self._run)
        self.edit.textChanged.connect(lambda _t: self._debounce.start())
        self.edit.returnPressed.connect(self._enter)
        self._last_query = None
        view.searchProgress.connect(self._progress)
        view.currentHitChanged.connect(self._hit_changed)
        view.searchPageHits.connect(results.add_hits)
        results.hitClicked.connect(view.select_hit_by_ordinal)

    def _button(self, name, tip, slot):
        b = QToolButton()
        b.setIcon(icon(name))
        b.setToolTip(tip)
        b.setAutoRaise(True)
        b.setIconSize(QSize(16, 16))
        b.clicked.connect(slot)
        return b

    def keyPressEvent(self, e):
        if e.key() == Qt.Key.Key_Escape:
            self.close_bar()
            return
        super().keyPressEvent(e)

    def _enter(self):
        from PySide6.QtWidgets import QApplication
        if self._debounce.isActive() or self._query_key() != self._last_query:
            self._debounce.stop()
            self._run()
            return
        back = bool(QApplication.keyboardModifiers() & Qt.KeyboardModifier.ShiftModifier)
        self.view.search_next(back)

    def _query_key(self):
        return (self.edit.text().strip(), self.case_act.isChecked(), self.word_act.isChecked())

    def _run(self):
        key = self._query_key()
        self._last_query = key
        query = key[0]
        rx = make_search_regex(query, key[1], key[2])
        self.results.reset(query if rx else None)
        self.view.start_search(rx)
        if rx is None:
            self.count.setText("")

    def _progress(self, total, done):
        self.results.finished(total, done)
        if self._last_query and self._last_query[0]:
            if total == 0 and done:
                self.count.setText("No results")
            elif not done and total == 0:
                self.count.setText("Searching…")

    def _hit_changed(self, ordinal, total):
        if ordinal >= 0:
            more = "" if self.view.search_finished() else "+"
            self.count.setText(f"{ordinal + 1} of {total}{more}")

    def open_bar(self, text: str | None = None):
        self.show()
        if text:
            self.edit.setText(text)
        self.edit.setFocus()
        self.edit.selectAll()

    def close_bar(self):
        self.hide()
        self._debounce.stop()
        self._last_query = None
        self.view.stop_search()
        self.results.reset(None)
        self.count.setText("")
        self.view.setFocus()
        self.closed.emit()


class DocumentTab(QWidget):
    PANELS = (("thumbnails", "thumbnails", "Page Thumbnails"),
              ("bookmarks", "bookmarks", "Bookmarks"),
              ("comments", "comments", "Comments"),
              ("attachments", "attach", "Attachments"),
              ("search", "search", "Search Results"))

    sidebarToggled = Signal(bool)
    commentAction = Signal(str, int, int, str)   # action, page, xref, extra (status)
    formCommand = Signal(str, object)            # "highlight" (bool) | "clear"
    pageCommand = Signal(str, list)              # Organize Pages command, selected pages
    organizeToggled = Signal(bool)
    noticeAction = Signal(str)                   # "signatures" | "ocr"

    def __init__(self, doc: PdfDocument, parent=None):
        super().__init__(parent)
        self.doc = doc
        self.view = DocumentView(doc)
        self.results = SearchResultsPanel(doc)
        self.findbar = FindBar(self.view, self.results)
        self.findbar.hide()
        self.formbar = FormBar()
        self.formbar.highlightToggled.connect(lambda on: self.formCommand.emit("highlight", on))
        self.formbar.clearRequested.connect(lambda: self.formCommand.emit("clear", None))
        self.sigbar = NoticeBar("certificate", "Signature Panel", "Show the signatures and their check")
        self.sigbar.clicked.connect(lambda: self.noticeAction.emit("signatures"))
        self.ocrbar = NoticeBar("ocr", "Recognize Text", "Make the text of the scanned pages searchable")
        self.ocrbar.clicked.connect(lambda: self.noticeAction.emit("ocr"))

        # side icon strip
        self.strip = QToolBar()
        self.strip.setOrientation(Qt.Orientation.Vertical)
        self.strip.setIconSize(QSize(20, 20))
        self.strip.setObjectName("SideStrip")
        self.strip.setMovable(False)
        group = QActionGroup(self)
        group.setExclusionPolicy(QActionGroup.ExclusionPolicy.ExclusiveOptional)
        self.panel_actions: dict[str, QAction] = {}
        for key, ic, title in self.PANELS:
            act = QAction(icon(ic), title, self)
            act.setCheckable(True)
            act.setToolTip(title)
            act.triggered.connect(lambda checked, k=key: self._strip_clicked(k, checked))
            group.addAction(act)
            self.strip.addAction(act)
            self.panel_actions[key] = act

        self.stack = QStackedWidget()
        self.panels: dict[str, QWidget] = {}
        self.panel_box = QWidget()
        pl = QVBoxLayout(self.panel_box)
        pl.setContentsMargins(0, 0, 0, 0)
        pl.setSpacing(0)
        self.panel_title = QLabel()
        self.panel_title.setObjectName("PanelTitle")
        self.panel_title.setContentsMargins(10, 8, 10, 6)
        pl.addWidget(self.panel_title)
        pl.addWidget(self.stack)

        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(0)
        rl.addWidget(self.sigbar)
        rl.addWidget(self.ocrbar)
        rl.addWidget(self.formbar)
        rl.addWidget(self.findbar)
        rl.addWidget(self.view)

        self.center = QStackedWidget()
        self.center.addWidget(right)
        self.organizer = None
        self.sig_statuses = []
        self._structure = doc.structure

        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.addWidget(self.panel_box)
        self.splitter.addWidget(self.center)
        self.splitter.setStretchFactor(0, 0)
        self.splitter.setStretchFactor(1, 1)
        self.splitter.setCollapsible(1, False)
        self.splitter.setSizes([230, 900])

        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(self.strip)
        lay.addWidget(self.splitter)

        self.view.currentPageChanged.connect(self._page_changed)
        self.view.annotSelected.connect(self._annot_selected)
        doc.listeners.append(self._doc_changed)
        self.panel_box.hide()
        if doc.attachments():
            self.panel_actions["attachments"].setToolTip(f"Attachments ({len(doc.attachments())})")
        self.refresh_formbar()

    def refresh_formbar(self):
        self.formbar.set_highlight(self.view.forms.highlight)
        self.formbar.refresh(self.doc, self.view.forms.prepare)

    # ------------------------------------------------------------------ panels
    def default_panel(self) -> str:
        return "bookmarks" if self.doc.toc() else "thumbnails"

    def _panel(self, key: str) -> QWidget:
        if key not in self.panels:
            if key == "thumbnails":
                w = ThumbnailPanel(self.doc)
                w.pageClicked.connect(lambda p: self.view.go_to_page(p))
                w.set_view_options(self.view.rotation, self.view.night)
                w.set_current(self.view.current_page())
            elif key == "bookmarks":
                w = OutlinePanel(self.doc)
                w.destinationClicked.connect(lambda p, pt: self.view.go_to_page(p, pt))
                if w.is_empty():
                    w.addTopLevelItem(QTreeWidgetItem(["This document has no bookmarks."]))
            elif key == "attachments":
                w = AttachmentsPanel(self.doc)
            elif key == "comments":
                from paperleaf.panels.comments import CommentsPanel
                w = CommentsPanel(self.doc)
                w.annotClicked.connect(self.reveal_annot)
                w.annotActivated.connect(lambda p, x: self.commentAction.emit("open", p, x, ""))
                w.replyRequested.connect(lambda p, x: self.commentAction.emit("reply", p, x, ""))
                w.statusRequested.connect(lambda p, x, st: self.commentAction.emit("status", p, x, st))
                w.deleteRequested.connect(lambda p, x: self.commentAction.emit("delete", p, x, ""))
            else:
                w = self.results
            self.panels[key] = w
            self.stack.addWidget(w)
        return self.panels[key]

    def _strip_clicked(self, key: str, checked: bool):
        if checked:
            self.show_panel(key)
        else:
            self.panel_box.hide()
            self.sidebarToggled.emit(False)

    def show_panel(self, key: str):
        w = self._panel(key)
        self.stack.setCurrentWidget(w)
        self.panel_title.setText(dict((k, t) for k, _i, t in self.PANELS)[key])
        self.panel_actions[key].setChecked(True)
        if not self.panel_box.isVisible():
            self.panel_box.show()
            self.sidebarToggled.emit(True)

    def sidebar_visible(self) -> bool:
        return self.panel_box.isVisible()

    def set_sidebar(self, visible: bool):
        if visible:
            cur = next((k for k, a in self.panel_actions.items() if a.isChecked()), None)
            self.show_panel(cur or self.default_panel())
        else:
            for a in self.panel_actions.values():
                a.setChecked(False)
            self.panel_box.hide()

    def set_strip_visible(self, visible: bool):
        self.strip.setVisible(visible)

    def _doc_changed(self, pages):
        th = self.panels.get("thumbnails")
        if self._structure != self.doc.structure:
            self._structure = self.doc.structure
            if th is not None:
                th._populate()
                th.set_current(self.view.current_page())
            ol = self.panels.pop("bookmarks", None)
            if ol is not None:  # bookmarks point at other page numbers now: rebuild
                shown = self.stack.currentWidget() is ol
                self.stack.removeWidget(ol)
                ol.deleteLater()
                if shown:
                    self.show_panel("bookmarks")
            self.results.reset(None)
            self.findbar.count.setText("")
        elif th is not None:
            th.refresh_pages(pages)
        self.refresh_formbar()

    # ---------------------------------------------------------- organize
    def organizing(self) -> bool:
        return self.organizer is not None and self.center.currentWidget() is self.organizer

    def set_organize(self, on: bool):
        if on == self.organizing():
            return
        if on:
            if self.organizer is None:
                from paperleaf.ui.organize import OrganizePages
                self.organizer = OrganizePages(self.doc)
                self.organizer.command.connect(self.pageCommand.emit)
                self.organizer.closed.connect(lambda: self.set_organize(False))
                self.organizer.pageActivated.connect(self._organize_open_page)
                self.center.addWidget(self.organizer)
            self.view.annots.cancel()
            self.view.forms.commit()
            self.organizer.select_pages([self.view.current_page()])
            self.center.setCurrentWidget(self.organizer)
            self.organizer.grid.setFocus()
        else:
            self.center.setCurrentIndex(0)
            self.view.setFocus()
        self.organizeToggled.emit(on)

    def _organize_open_page(self, pno: int):
        self.set_organize(False)
        self.view.go_to_page(pno)

    def selected_pages(self) -> list[int]:
        """Pages selected in Organize Pages, else the current page."""
        if self.organizing():
            return self.organizer.grid.selected_pages() or [self.view.current_page()]
        return [self.view.current_page()]

    def _annot_selected(self, rec):
        cp = self.panels.get("comments")
        if cp is not None and rec is not None:
            cp.select(rec.pno, rec.xref)

    def reveal_annot(self, pno: int, xref: int):
        """Scroll to an annotation and select it."""
        from paperleaf.core import annotations as an
        rec = an.find(self.doc, pno, xref)
        if rec is None:
            return
        v = self.view
        if v.annots.sel != (pno, xref):
            v._push_history()
        v._ensure_visible(pno, tuple(rec.rect))
        v.annots.select(pno, xref)
        v.setFocus()

    def _page_changed(self, pno: int):
        th = self.panels.get("thumbnails")
        if th is not None:
            th.set_current(pno)

    def sync_view_options(self):
        th = self.panels.get("thumbnails")
        if th is not None:
            th.set_view_options(self.view.rotation, self.view.night)

    def open_find(self):
        text = self.view.selected_text().strip() if self.view.has_selection() else None
        if text and ("\n" in text or len(text) > 80):
            text = None
        self.findbar.open_bar(text)
