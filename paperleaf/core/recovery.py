"""Automatic backups of unsaved changes, so a crash or power cut loses at most a minute of work.

Backups live in %APPDATA%\\Paperleaf\\recovery and are deleted when the document is
saved or closed normally.
"""
from __future__ import annotations

import hashlib
import json
import os
import time

from paperleaf.core.settings import config_dir


def _dir() -> str:
    path = os.path.join(config_dir(), "recovery")
    os.makedirs(path, exist_ok=True)
    return path


def _base(path: str) -> str:
    key = hashlib.sha1(os.path.normcase(os.path.abspath(path)).encode("utf-8")).hexdigest()[:20]
    return os.path.join(_dir(), key)


def write(doc) -> None:
    base = _base(doc.path)
    tmp = base + ".tmp"
    doc.write_backup(tmp)
    os.replace(tmp, base + ".pdf")
    with open(base + ".json", "w", encoding="utf-8") as f:
        json.dump({"path": doc.path, "time": time.time()}, f)


def find(path: str) -> bytes | None:
    """Backup bytes for ``path`` if one exists and is newer than the file."""
    base = _base(path)
    if not os.path.exists(base + ".pdf"):
        return None
    try:
        if os.path.exists(path) and os.path.getmtime(path) > os.path.getmtime(base + ".pdf"):
            discard(path)  # the file was saved later (maybe by another program)
            return None
        with open(base + ".pdf", "rb") as f:
            return f.read()
    except OSError:
        return None


def discard(path: str) -> None:
    base = _base(path)
    for ext in (".pdf", ".json", ".tmp"):
        try:
            os.remove(base + ext)
        except OSError:
            pass
