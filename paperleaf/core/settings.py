"""User settings stored in %APPDATA%\\Paperleaf (INI + a small JSON for page memory)."""
from __future__ import annotations

import json
import os

from PySide6.QtCore import QSettings

from paperleaf import APP_ID

MAX_RECENT = 20
MAX_REMEMBERED = 300


def config_dir() -> str:
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    path = os.path.join(base, APP_ID)
    os.makedirs(path, exist_ok=True)
    return path


DEFAULTS = {
    "theme": "system",            # system | light | dark
    "default_zoom": "fit_width",  # fit_width | fit_page | actual | auto
    "default_layout": "single_continuous",
    "remember_page": True,
    "restore_tabs": False,
    "night_mode": False,
    "show_sidebar": True,
    "confirm_links": True,
    "smooth_scroll": True,
    "show_annotate": False,
    "highlight_fields": True,
}


class Settings:
    def __init__(self, path: str | None = None):
        self._dir = path or config_dir()
        self.q = QSettings(os.path.join(self._dir, "Paperleaf.ini"), QSettings.Format.IniFormat)
        self._pos_file = os.path.join(self._dir, "positions.json")
        self._positions = None

    # -- generic -----------------------------------------------------------
    def get(self, key: str):
        default = DEFAULTS.get(key)
        value = self.q.value(key, default)
        if isinstance(default, bool):
            if isinstance(value, str):
                return value.lower() in ("1", "true", "yes")
            return bool(value)
        return value

    def set(self, key: str, value):
        self.q.setValue(key, value)

    def value(self, key, default=None):
        return self.q.value(key, default)

    def sync(self):
        self.q.sync()

    # -- recent files ------------------------------------------------------
    def recent_files(self) -> list[str]:
        value = self.q.value("recent", [])
        if isinstance(value, str):
            value = [value] if value else []
        return [p for p in (value or []) if p]

    def add_recent(self, path: str):
        path = os.path.abspath(path)
        items = [p for p in self.recent_files() if os.path.normcase(p) != os.path.normcase(path)]
        items.insert(0, path)
        self.q.setValue("recent", items[:MAX_RECENT])

    def remove_recent(self, path: str):
        items = [p for p in self.recent_files() if os.path.normcase(p) != os.path.normcase(path)]
        self.q.setValue("recent", items)

    def clear_recent(self):
        self.q.setValue("recent", [])

    # -- last page per file --------------------------------------------------
    def _load_positions(self) -> dict:
        if self._positions is None:
            try:
                with open(self._pos_file, "r", encoding="utf-8") as f:
                    self._positions = json.load(f)
            except (OSError, ValueError):
                self._positions = {}
        return self._positions

    def position(self, path: str) -> dict | None:
        return self._load_positions().get(os.path.normcase(os.path.abspath(path)))

    def save_position(self, path: str, data: dict):
        pos = self._load_positions()
        key = os.path.normcase(os.path.abspath(path))
        pos.pop(key, None)
        pos[key] = data
        while len(pos) > MAX_REMEMBERED:
            pos.pop(next(iter(pos)))
        try:
            with open(self._pos_file, "w", encoding="utf-8") as f:
                json.dump(pos, f)
        except OSError:
            pass
