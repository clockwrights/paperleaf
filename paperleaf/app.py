"""Application entry point.

Command line:
  Paperleaf.exe [files...] [--page N]     open files (in the running window if any)
  Paperleaf.exe --print FILE              show the print dialog for FILE, then exit
  Paperleaf.exe --printto FILE PRINTER    print FILE to PRINTER without dialogs
  Paperleaf.exe --register / --unregister add or remove Windows Explorer integration
  Paperleaf.exe --new-instance            do not hand files to a running window
  Paperleaf.exe --tool TOOL FILE...       open a page tool (combine, split, extract, rotate)
"""
from __future__ import annotations

import argparse
import ctypes
import getpass
import json
import os
import re
import sys

from PySide6.QtCore import QByteArray, Qt, QTimer
from PySide6.QtGui import QIcon
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QApplication, QMessageBox

from paperleaf import APP_ID, APP_NAME, VERSION

_MUTEX = None  # keep the single-instance mutex alive for the whole process


def resource_dir() -> str:
    if getattr(sys, "frozen", False):
        return os.path.join(getattr(sys, "_MEIPASS", os.path.dirname(sys.executable)), "resources")
    return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "resources")


def _server_name() -> str:
    user = re.sub(r"[^A-Za-z0-9_-]", "_", getpass.getuser() or "user")
    return f"{APP_ID}-{user}-instance"


def _is_first_instance() -> bool:
    global _MUTEX
    if os.name != "nt":
        return True
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateMutexW.restype = ctypes.c_void_p
    _MUTEX = k32.CreateMutexW(None, False, f"Local\\{_server_name()}")
    return ctypes.get_last_error() != 183  # ERROR_ALREADY_EXISTS


def _send_to_running(payload: dict, timeout_ms: int = 6000) -> bool:
    """Hand files to the running instance. Retries while it is starting up."""
    try:
        ctypes.windll.user32.AllowSetForegroundWindow(-1)  # ASFW_ANY: let it come to front
    except Exception:
        pass
    waited = 0
    while waited <= timeout_ms:
        sock = QLocalSocket()
        sock.connectToServer(_server_name())
        if sock.waitForConnected(300):
            sock.write(QByteArray(json.dumps(payload).encode("utf-8")))
            sock.flush()
            sock.waitForBytesWritten(1000)
            sock.disconnectFromServer()
            return True
        QApplication.processEvents()
        import time
        time.sleep(0.1)
        waited += 400
    return False


class InstanceServer:
    def __init__(self, on_message):
        self.on_message = on_message
        self.server = QLocalServer()
        QLocalServer.removeServer(_server_name())
        self.server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
        self.server.listen(_server_name())
        self.server.newConnection.connect(self._accept)

    def _accept(self):
        while self.server.hasPendingConnections():
            sock = self.server.nextPendingConnection()
            buf = bytearray()

            def read(s=sock, b=buf):
                b.extend(bytes(s.readAll()))

            def done(s=sock, b=buf):
                read(s, b)
                try:
                    self.on_message(json.loads(b.decode("utf-8")))
                except ValueError:
                    pass
                s.deleteLater()

            sock.readyRead.connect(read)
            sock.disconnected.connect(done)


def apply_theme(app: QApplication, name: str):
    hints = app.styleHints()
    scheme = {"light": Qt.ColorScheme.Light, "dark": Qt.ColorScheme.Dark}.get(name, Qt.ColorScheme.Unknown)
    try:
        hints.setColorScheme(scheme)
    except AttributeError:
        pass
    app.setStyleSheet(
        "#PanelTitle { font-weight: 600; }"
        "#Banner { border: 1px solid palette(mid); border-radius: 6px; padding: 6px; }"
        "#SideStrip { border: none; padding: 2px; }"
        "#FormBar { border-bottom: 1px solid palette(mid); background: palette(alternate-base); }"
    )
    for w in app.topLevelWidgets():
        w.update()


def parse_args(argv: list[str]):
    p = argparse.ArgumentParser(prog=APP_NAME, add_help=True)
    p.add_argument("files", nargs="*")
    p.add_argument("--page", type=int, default=None)
    p.add_argument("--print", dest="print_file", default=None)
    p.add_argument("--printto", nargs=2, metavar=("FILE", "PRINTER"), default=None)
    p.add_argument("--register", action="store_true")
    p.add_argument("--unregister", action="store_true")
    p.add_argument("--new-instance", action="store_true")
    p.add_argument("--tool", default=None)
    p.add_argument("--version", action="version", version=f"{APP_NAME} {VERSION}")
    # Acrobat-style "/p" and "/t" switches
    fixed = []
    it = iter(argv)
    for a in it:
        if a.lower() == "/p":
            fixed.append("--print")
        elif a.lower() == "/t":
            fixed.append("--printto")
        else:
            fixed.append(a)
    args, _unknown = p.parse_known_args(fixed)
    return args


def _headless_print(app, path: str, printer: str | None) -> int:
    from paperleaf.core.document import PasswordRequired, PdfDocument
    from paperleaf.ui.printing import run_print
    from PySide6.QtWidgets import QInputDialog, QLineEdit
    password = None
    while True:
        try:
            doc = PdfDocument(path, password)
            break
        except PasswordRequired as e:
            pw, ok = QInputDialog.getText(None, "Password", "Enter the document password:", QLineEdit.EchoMode.Password)
            if not ok:
                return 1
            password = pw
        except Exception as exc:
            QMessageBox.critical(None, APP_NAME, f"Could not open the file:\n{exc}")
            return 1
    ok = run_print(doc, 0, None, printer_name=printer, silent=printer is not None)
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    args = parse_args(argv)

    if args.register or args.unregister:
        from paperleaf.winshell import registry
        (registry.register if args.register else registry.unregister)()
        return 0

    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Paperleaf.PDFReader")
    except Exception:
        pass

    QApplication.setApplicationName(APP_NAME)
    QApplication.setOrganizationName(APP_NAME)
    QApplication.setApplicationVersion(VERSION)
    app = QApplication([sys.argv[0]])
    ico = os.path.join(resource_dir(), "paperleaf.ico")
    if os.path.exists(ico):
        app.setWindowIcon(QIcon(ico))

    from paperleaf.core.settings import Settings
    settings = Settings()
    apply_theme(app, settings.get("theme"))

    if args.print_file or args.printto:
        path, printer = (args.printto if args.printto else (args.print_file, None))
        return _headless_print(app, os.path.abspath(path), printer)

    files = [os.path.abspath(f) for f in args.files]
    message = {"paths": files, "page": args.page, "tool": args.tool}
    if not args.new_instance and not _is_first_instance():
        if _send_to_running(message):
            return 0

    from paperleaf.ui.main_window import MainWindow
    win = MainWindow(settings, apply_theme=lambda name: apply_theme(app, name))
    server = None
    if not args.new_instance:
        server = InstanceServer(win.handle_message)
    win.show()
    if args.tool:
        QTimer.singleShot(0, lambda: win.handle_message(message, first=True))
    elif files:
        page = args.page - 1 if args.page and args.page > 0 else None
        QTimer.singleShot(0, lambda: [win.open_file(f, page) for f in files])
    else:
        QTimer.singleShot(0, win.restore_session)
    code = app.exec()
    del server
    return code


if __name__ == "__main__":
    raise SystemExit(main())
