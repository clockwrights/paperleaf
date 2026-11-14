"""The Comment & Sign toolbar: annotation tools, style controls, stamps and signatures."""
from __future__ import annotations

import json
import os

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QAction, QColor, QIcon, QPixmap
from PySide6.QtWidgets import (
    QColorDialog, QComboBox, QFileDialog, QGridLayout, QLabel, QMenu, QMessageBox, QToolBar,
    QToolButton, QWidget, QWidgetAction,
)

from paperleaf.core import annotations as an
from paperleaf.ui import stamps
from paperleaf.ui.icons import color_swatch, icon

# (tool, label, icon) in toolbar order; None = separator
TOOL_DEFS = [
    ("highlight", "Highlight Text", "highlight"), ("underline", "Underline Text", "underline"),
    ("strikeout", "Strikethrough Text", "strikeout"), ("squiggly", "Squiggly Underline", "squiggly"),
    None,
    ("note", "Add Sticky Note", "note"), ("freetext", "Add Text Box", "textbox"),
    ("callout", "Add Callout", "callout"),
    None,
    ("pen", "Draw (Pen)", "pen"), ("highlighter", "Highlighter Pen", "highlight"),
    ("eraser", "Eraser (removes drawings)", "eraser"),
    None,
    ("rect", "Rectangle", "rect"), ("ellipse", "Oval", "ellipse"), ("line", "Line", "line"),
    ("arrow", "Arrow", "arrow"), ("polygon", "Polygon (double-click to finish)", "polygon"),
    ("polyline", "Connected Lines (double-click to finish)", "polyline"),
    ("cloud", "Cloud (double-click to finish)", "cloud"),
    None,
    ("filltext", "Add Text (Fill & Sign)", "filltext"), ("check", "Add Checkmark", "checkmark"),
    ("cross", "Add Cross", "crossmark"), ("dot", "Add Dot", "dot"), ("date", "Add Today's Date", "date"),
]

TOOL_LABELS = {d[0]: d[1] for d in TOOL_DEFS if d}
TOOL_LABELS.update(stamp="Add Stamp", signature="Sign")

YELLOW, RED, BLUE, GREEN, BLACK = (1.0, 0.85, 0.0), (0.86, 0.12, 0.12), (0.1, 0.35, 0.85), (0.1, 0.6, 0.2), (0, 0, 0)

DEFAULT_STYLES = {
    "highlight": an.Style(YELLOW, None, 1, 1.0),
    "underline": an.Style(GREEN, None, 1, 1.0),
    "strikeout": an.Style(RED, None, 1, 1.0),
    "squiggly": an.Style(BLUE, None, 1, 1.0),
    "note": an.Style(YELLOW, None, 1, 1.0),
    "freetext": an.Style(RED, None, 1, 1.0, 12),
    "callout": an.Style(RED, (1, 1, 1), 1, 1.0, 12),
    "filltext": an.Style(BLACK, None, 0, 1.0, 12),
    "pen": an.Style(RED, None, 2, 1.0),
    "highlighter": an.Style(YELLOW, None, 12, 0.4),
    "rect": an.Style(RED, None, 1.5, 1.0), "ellipse": an.Style(RED, None, 1.5, 1.0),
    "line": an.Style(RED, None, 1.5, 1.0), "arrow": an.Style(RED, None, 1.5, 1.0),
    "polygon": an.Style(RED, None, 1.5, 1.0), "polyline": an.Style(RED, None, 1.5, 1.0),
    "cloud": an.Style(RED, None, 1.5, 1.0),
    "check": an.Style(BLACK, None, 0, 1.0, 16), "cross": an.Style(BLACK, None, 0, 1.0, 16),
    "dot": an.Style(BLACK, None, 0, 1.0, 10), "date": an.Style(BLACK, None, 0, 1.0, 12),
}

SWATCHES = [
    (0, 0, 0), (0.4, 0.4, 0.4), (0.75, 0.75, 0.75), (1, 1, 1),
    (0.86, 0.12, 0.12), (1, 0.5, 0), (1, 0.85, 0), (1, 1, 0),
    (0.1, 0.6, 0.2), (0.4, 0.85, 0.3), (0, 0.7, 0.75), (0.55, 0.85, 1),
    (0.1, 0.35, 0.85), (0.35, 0.2, 0.75), (0.8, 0.2, 0.7), (1, 0.6, 0.8),
]

# which controls apply to which annotation kinds / tools
HAS_FILL = {"Square", "Circle", "Polygon", "FreeText", "rect", "ellipse", "polygon", "cloud", "freetext", "callout"}
HAS_WIDTH = {"Square", "Circle", "Polygon", "PolyLine", "Line", "Ink", "FreeText", "rect", "ellipse", "line",
             "arrow", "polygon", "polyline", "cloud", "pen", "highlighter", "freetext", "callout"}
HAS_FONT = {"FreeText", "freetext", "callout", "filltext", "check", "cross", "dot", "date"}
HAS_DASH = {"Square", "Circle", "Polygon", "PolyLine", "Line", "rect", "ellipse", "line", "arrow", "polygon",
            "polyline", "cloud"}


class ToolStyles:
    """Per-tool default appearance, remembered in settings."""

    def __init__(self, settings):
        self.settings = settings
        self._styles: dict[str, an.Style] = {}
        for tool, st in DEFAULT_STYLES.items():
            saved = settings.value(f"style/{tool}")
            style = an.Style(**vars(st))
            if saved:
                try:
                    data = json.loads(saved)
                    for k, v in data.items():
                        if hasattr(style, k):
                            setattr(style, k, tuple(v) if isinstance(v, list) else v)
                except (ValueError, TypeError):
                    pass
            self._styles[tool] = style

    def get(self, tool: str) -> an.Style:
        return self._styles.get(tool) or an.Style()

    def update(self, tool: str, **changes):
        st = self.get(tool)
        for k, v in changes.items():
            setattr(st, k, v)
        self._styles[tool] = st
        self.settings.set(f"style/{tool}", json.dumps(vars(st)))


class ColorButton(QToolButton):
    """Tool button with a palette of swatches (and optionally 'No color')."""

    colorChosen = Signal(object)  # tuple | None

    def __init__(self, tip: str, allow_none: bool, parent=None):
        super().__init__(parent)
        self.setToolTip(tip)
        self.allow_none = allow_none
        self.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self._color = None
        menu = QMenu(self)
        grid_w = QWidget()
        grid = QGridLayout(grid_w)
        grid.setContentsMargins(6, 6, 6, 6)
        grid.setSpacing(3)
        for i, rgb in enumerate(SWATCHES):
            b = QToolButton()
            b.setIcon(color_swatch(rgb, 18))
            b.setIconSize(QSize(18, 18))
            b.setAutoRaise(True)
            b.clicked.connect(lambda _c=False, c=rgb: self._pick(c))
            grid.addWidget(b, i // 4, i % 4)
        wa = QWidgetAction(menu)
        wa.setDefaultWidget(grid_w)
        menu.addAction(wa)
        if allow_none:
            menu.addAction(color_swatch(None), "No color", lambda: self._pick(None))
        menu.addAction("Other color…", self._custom)
        self.setMenu(menu)
        self.set_color((0, 0, 0))

    def _pick(self, rgb):
        self.menu().hide()
        self.set_color(rgb)
        self.colorChosen.emit(rgb)

    def _custom(self):
        start = QColor.fromRgbF(*self._color) if self._color else QColor("black")
        c = QColorDialog.getColor(start, self, "Choose color")
        if c.isValid():
            self._pick((round(c.redF(), 3), round(c.greenF(), 3), round(c.blueF(), 3)))

    def set_color(self, rgb):
        self._color = tuple(rgb) if rgb else None
        self.setIcon(color_swatch(self._color, 18))


class AnnotateToolbar(QToolBar):
    """Second toolbar row. ``tool_actions`` are the window's checkable tool actions."""

    styleEdited = Signal(dict)   # changes for the selected annotation
    stampChosen = Signal()
    signatureChosen = Signal()

    def __init__(self, tool_actions: dict, styles: ToolStyles, env, parent=None):
        super().__init__("Comment && Sign", parent)
        self.setObjectName("AnnotateToolbar")
        self.setMovable(False)
        self.setIconSize(QSize(20, 20))
        self.styles = styles
        self.env = env
        self.tool = None
        self.target = None  # selected AnnotRecord
        self._updating = False
        for d in TOOL_DEFS:
            if d is None:
                self.addSeparator()
            else:
                self.addAction(tool_actions[d[0]])
        self.addSeparator()
        # stamps
        self.stamp_btn = QToolButton()
        self.stamp_btn.setIcon(icon("stamp"))
        self.stamp_btn.setToolTip("Add a stamp")
        self.stamp_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.stamp_menu = QMenu(self.stamp_btn)
        self.stamp_menu.aboutToShow.connect(self._fill_stamps)
        self.stamp_btn.setMenu(self.stamp_menu)
        self.addWidget(self.stamp_btn)
        # signatures
        self.sign_btn = QToolButton()
        self.sign_btn.setIcon(icon("sign"))
        self.sign_btn.setToolTip("Sign the document")
        self.sign_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.sign_menu = QMenu(self.sign_btn)
        self.sign_menu.aboutToShow.connect(self._fill_signatures)
        self.sign_btn.setMenu(self.sign_menu)
        self.addWidget(self.sign_btn)
        self.addSeparator()
        # style controls
        self.color_btn = ColorButton("Color", allow_none=False)
        self.color_btn.colorChosen.connect(lambda c: self._edit(color=c))
        self.fill_btn = ColorButton("Fill color", allow_none=True)
        self.fill_btn.colorChosen.connect(lambda c: self._edit(fill=c))
        self.width_box = QComboBox()
        self.width_box.setToolTip("Line thickness")
        for w in (0.5, 1, 1.5, 2, 3, 4, 6, 8, 12, 16):
            self.width_box.addItem(f"{w:g} pt", w)
        self.width_box.activated.connect(lambda i: self._edit(width=self.width_box.itemData(i)))
        self.opacity_box = QComboBox()
        self.opacity_box.setToolTip("Opacity")
        for o in (100, 90, 75, 60, 50, 40, 25, 10):
            self.opacity_box.addItem(f"{o}%", o / 100)
        self.opacity_box.activated.connect(lambda i: self._edit(opacity=self.opacity_box.itemData(i)))
        self.font_box = QComboBox()
        self.font_box.setToolTip("Font size")
        for f in (6, 8, 9, 10, 11, 12, 14, 16, 18, 20, 24, 28, 36, 48, 72):
            self.font_box.addItem(f"{f} pt", f)
        self.font_box.activated.connect(lambda i: self._edit(fontsize=self.font_box.itemData(i)))
        self.dash_btn = QToolButton()
        self.dash_btn.setText("- - -")
        self.dash_btn.setToolTip("Dashed line")
        self.dash_btn.setCheckable(True)
        self.dash_btn.toggled.connect(lambda on: self._updating or self._edit(dashed=on))
        self.style_label = QLabel()
        self.style_label.setEnabled(False)
        self._controls = {
            "color": self.addWidget(self.color_btn), "fill": self.addWidget(self.fill_btn),
            "width": self.addWidget(self.width_box), "opacity": self.addWidget(self.opacity_box),
            "font": self.addWidget(self.font_box), "dash": self.addWidget(self.dash_btn),
        }
        self.addWidget(self.style_label)
        self.set_tool(None)

    # ------------------------------------------------------------ controls
    @staticmethod
    def _select(box: QComboBox, value):
        best, dist = 0, None
        for i in range(box.count()):
            d = abs(box.itemData(i) - value)
            if dist is None or d < dist:
                best, dist = i, d
        box.setCurrentIndex(best)

    def _show_controls(self, key):
        self._controls["color"].setVisible(key is not None)
        self._controls["fill"].setVisible(key in HAS_FILL)
        self._controls["width"].setVisible(key in HAS_WIDTH)
        self._controls["opacity"].setVisible(key is not None)
        self._controls["font"].setVisible(key in HAS_FONT)
        self._controls["dash"].setVisible(key in HAS_DASH)

    def _load(self, color, fill, width, opacity, fontsize, dashed):
        self._updating = True
        self.color_btn.set_color(color)
        self.fill_btn.set_color(fill)
        self._select(self.width_box, width or 1)
        self._select(self.opacity_box, opacity if opacity is not None else 1)
        self._select(self.font_box, fontsize or 12)
        self.dash_btn.setChecked(bool(dashed))
        self._updating = False

    def set_tool(self, tool):
        """A tool was chosen: show and edit that tool's default style."""
        self.tool = tool if tool in DEFAULT_STYLES else None
        if self.target is not None:
            return
        if self.tool is None:
            self._show_controls(None)
            self.style_label.setText("")
            return
        st = self.styles.get(self.tool)
        self._show_controls(self.tool)
        self._load(st.color, st.fill, st.width, st.opacity, st.fontsize, st.dashed)
        self.style_label.setText(f"  {TOOL_LABELS.get(self.tool, '')} defaults")

    def set_target(self, rec):
        """An annotation was selected: show and edit its style."""
        self.target = rec if rec is not None and rec.kind not in ("Stamp",) else None
        if self.target is None:
            self.set_tool(self.tool)
            return
        r = self.target
        color = r.text_color if r.kind == "FreeText" else r.stroke
        self._show_controls(r.kind)
        self._load(color, r.fill, r.width, r.opacity, r.fontsize, r.dashed)
        self.style_label.setText(f"  Selected: {r.label}")

    def _edit(self, **changes):
        if self._updating:
            return
        if self.target is not None:
            self.styleEdited.emit(changes)
        elif self.tool is not None:
            self.styles.update(self.tool, **changes)

    # -------------------------------------------------------------- stamps
    def _fill_stamps(self):
        m = self.stamp_menu
        m.clear()
        for label, color, dynamic in stamps.STAMPS:
            img = stamps.render_stamp(label, color, "By Name at time, date" if dynamic else None)
            pm = QPixmap.fromImage(img).scaledToHeight(28, Qt.TransformationMode.SmoothTransformation)
            text = f"{label} (with name and date)" if dynamic else label
            act = m.addAction(QIcon(pm), text)
            act.triggered.connect(lambda _c=False, l=label, c=color, d=dynamic: self._choose_stamp(("builtin", l, c, d)))
        custom = stamps.list_images(stamps.custom_stamps_dir())
        if custom:
            m.addSeparator()
            for path in custom:
                pm = QPixmap(path).scaledToHeight(28, Qt.TransformationMode.SmoothTransformation)
                act = m.addAction(QIcon(pm), os.path.splitext(os.path.basename(path))[0].split("_")[0])
                act.triggered.connect(lambda _c=False, p=path: self._choose_stamp(("image", p)))
        m.addSeparator()
        m.addAction("Create Custom Stamp from Image…", self._new_custom_stamp)
        m.addAction("Open Custom Stamps Folder", lambda: os.startfile(stamps.custom_stamps_dir()))
        m.setIconSize(QSize(120, 28))

    def _choose_stamp(self, spec):
        self.env.current_stamp = spec
        self.stampChosen.emit()

    def _new_custom_stamp(self):
        path, _ = QFileDialog.getOpenFileName(self, "Choose an image for the stamp", "",
                                              "Images (*.png *.jpg *.jpeg *.bmp *.gif)")
        if not path:
            return
        from PySide6.QtGui import QImage
        img = QImage(path)
        if img.isNull():
            QMessageBox.warning(self, "Custom stamp", "That image could not be read.")
            return
        if img.width() > 1600:
            img = img.scaledToWidth(1600, Qt.TransformationMode.SmoothTransformation)
        name = os.path.splitext(os.path.basename(path))[0].replace("_", " ")
        saved = os.path.join(stamps.custom_stamps_dir(), f"{name}_{os.getpid()}.png")
        img.save(saved, "PNG")
        self._choose_stamp(("image", saved))

    # ---------------------------------------------------------- signatures
    def _fill_signatures(self):
        m = self.sign_menu
        m.clear()
        sigs = stamps.list_images(stamps.signatures_dir())
        for path in sigs:
            pm = QPixmap(path).scaledToHeight(30, Qt.TransformationMode.SmoothTransformation)
            act = m.addAction(QIcon(pm), "")
            act.setToolTip("Place this signature")
            act.triggered.connect(lambda _c=False, p=path: self._choose_signature(p))
        if sigs:
            m.addSeparator()
        m.addAction(icon("sign"), "Add Signature…", self.add_signature)
        if sigs:
            rm = m.addMenu("Remove Signature")
            for path in sigs:
                pm = QPixmap(path).scaledToHeight(24, Qt.TransformationMode.SmoothTransformation)
                rm.addAction(QIcon(pm), "", lambda p=path: self._remove_signature(p))
            rm.setIconSize(QSize(120, 24))
        m.setIconSize(QSize(140, 30))

    def add_signature(self):
        from paperleaf.ui.signatures import SignatureDialog
        dlg = SignatureDialog(self.env.author(), self)
        if dlg.exec() and dlg.result_path:
            self._choose_signature(dlg.result_path)

    def _choose_signature(self, path):
        self.env.current_signature = path
        self.signatureChosen.emit()

    def _remove_signature(self, path):
        if QMessageBox.question(self, "Remove signature", "Remove this saved signature?") == QMessageBox.StandardButton.Yes:
            try:
                os.remove(path)
            except OSError:
                pass
            if self.env.current_signature == path:
                self.env.current_signature = None


class AnnotEnv:
    """What the annotation tools ask the window: author, styles, current stamp/signature."""

    def __init__(self, settings, styles: ToolStyles):
        self.settings = settings
        self.styles = styles
        self.current_stamp = None       # ("builtin", label, QColor, dynamic) | ("image", path)
        self.current_signature = None   # path to PNG

    def author(self) -> str:
        name = self.settings.value("author", "")
        if not name:
            name = os.environ.get("USERNAME", "")
        return str(name)

    def style(self, tool: str) -> an.Style:
        st = self.styles.get(tool)
        return an.Style(**vars(st))

    def stamp(self):
        spec = self.current_stamp
        if not spec:
            return None
        if spec[0] == "builtin":
            _, label, color, dynamic = spec
            img = stamps.render_stamp(label, color, stamps.dynamic_line(self.author()) if dynamic else None)
            w, h = stamps.size_in_points(img)
            return stamps.to_png(img), w, h, label
        from PySide6.QtGui import QImage
        img = QImage(spec[1])
        if img.isNull():
            return None
        w = 150.0
        h = w * img.height() / max(1, img.width())
        name = os.path.splitext(os.path.basename(spec[1]))[0].split("_")[0]
        return stamps.to_png(img), w, h, name or "Stamp"

    def redact_style(self):
        from paperleaf.core.protect import RedactStyle
        st = RedactStyle()
        raw = self.settings.value("redact/style")
        if raw:
            try:
                for k, v in json.loads(raw).items():
                    if hasattr(st, k):
                        setattr(st, k, tuple(v) if isinstance(v, list) else v)
            except (ValueError, TypeError):
                pass
        return st

    def set_redact_style(self, st):
        self.settings.set("redact/style", json.dumps(vars(st)))

    def signature(self):
        path = self.current_signature
        if not path or not os.path.exists(path):
            return None
        from PySide6.QtGui import QImage
        img = QImage(path)
        if img.isNull():
            return None
        w = 140.0
        h = w * img.height() / max(1, img.width())
        if h > 60:
            w, h = w * 60 / h, 60.0
        with open(path, "rb") as f:
            return f.read(), w, h, "Signature"
