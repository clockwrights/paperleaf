import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from tests.samples import make_all  # noqa: E402


@pytest.fixture(autouse=True)
def _private_appdata(tmp_path_factory, monkeypatch):
    """Keep digital IDs, trusted certificates, recovery files etc. out of the real profile."""
    monkeypatch.setenv("APPDATA", str(tmp_path_factory.mktemp("appdata")))


@pytest.fixture(autouse=True)
def _no_blocking_message_boxes(monkeypatch):
    """A message box a test didn't expect fails the test instead of waiting forever for a click."""
    from PySide6.QtWidgets import QMessageBox

    def unexpected(kind):
        def box(*a, **_k):
            text = a[2] if len(a) > 2 else ""
            raise AssertionError(f"unexpected QMessageBox.{kind}: {text}")
        return staticmethod(box)
    for kind in ("warning", "critical", "information", "question"):
        monkeypatch.setattr(QMessageBox, kind, unexpected(kind))


@pytest.fixture(scope="session")
def samples(tmp_path_factory):
    return make_all(str(tmp_path_factory.mktemp("samples")))


@pytest.fixture(scope="session")
def qapp():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    yield app
