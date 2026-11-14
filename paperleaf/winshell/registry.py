"""Per-user Windows Explorer integration (HKEY_CURRENT_USER only, no admin needed).

What gets registered:
  * ProgID ``Paperleaf.Document`` with open / print / printto verbs and icon
  * ``.pdf`` -> OpenWithProgids, so Paperleaf shows up in "Open with"
  * ``Applications\\Paperleaf.exe`` entry (friendly name, supported types)
  * RegisteredApplications + Capabilities, so Paperleaf is listed in
    Settings > Apps > Default apps
  * a right-click verb "Open with Paperleaf" on every PDF, even when another app
    is the default
  * a right-click "Paperleaf" submenu with tools (Combine files, Split...). When
    several files are selected Explorer starts one copy per file; the running
    window collects them (see ``MainWindow.run_tool``)
  * when PaperleafShell.dll is installed next to Paperleaf.exe (shellext/): PDF
    thumbnails and the preview pane in File Explorer. The Windows 11 top-level
    menu comes from a signed sparse package instead (shellext/install_menu.ps1)

Windows does not let applications make themselves the default handler
silently; the user picks Paperleaf once in Settings (``open_default_apps``).
"""
from __future__ import annotations

import ctypes
import os
import subprocess
import sys

try:
    import winreg
except ImportError:  # not Windows (e.g. tests on Linux): integration is unavailable
    winreg = None

from paperleaf import APP_NAME

PROGID = "Paperleaf.Document"
CAPS_KEY = r"Software\Paperleaf\Capabilities"
CLASSES = r"Software\Classes"
EXTENSIONS = (".pdf",)
VERB_KEY = "Paperleaf.open"
TOOLS_KEY = "Paperleaf.tools"
# (tool, menu text) shown in the right-click "Paperleaf" submenu, in order
TOOL_VERBS = [
    ("combine", "Combine files"),
    ("split", "Split…"),
    ("extract", "Extract pages…"),
    ("rotate", "Rotate pages…"),
    ("compress", "Reduce file size…"),
    ("word", "Convert to Word"),
    ("images", "Convert to images…"),
    ("protect", "Protect with password…"),
]
OFFICE_EXTENSIONS = (".doc", ".docx", ".rtf", ".odt", ".xls", ".xlsx", ".ods", ".ppt", ".pptx", ".odp")
TOPDF_KEY = "Paperleaf.topdf"
# PaperleafShell.dll (see shellext/src/guids.h)
SHELL_DLL = "PaperleafShell.dll"
THUMB_CLSID = "{BDA307E0-58BF-4583-B73E-DA8130B4AE52}"
PREVIEW_CLSID = "{C72D2B1B-EE52-4E4E-BF99-08C7ACCE5C6D}"
THUMB_SHELLEX = "{e357fccd-a995-4576-b01f-234630154e96}"     # IThumbnailProvider
PREVIEW_SHELLEX = "{8895b1c6-b41f-4c1c-a562-0d564250836f}"   # IPreviewHandler
PREVHOST_APPID = "{6d2b5079-2f0b-48dd-ab7f-97cec514d30b}"    # Windows' 64-bit preview host
PREVIEW_HANDLERS = r"Software\Microsoft\Windows\CurrentVersion\PreviewHandlers"


def shell_dll() -> str | None:
    """PaperleafShell.dll next to Paperleaf.exe (or the development build), if present."""
    if getattr(sys, "frozen", False):
        base = os.path.dirname(sys.executable)
        cands = [os.path.join(base, SHELL_DLL), os.path.join(base, "_internal", SHELL_DLL)]
    else:
        root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        cands = [os.path.join(root, "shellext", "build", "Release", SHELL_DLL)]
    return next((c for c in cands if os.path.isfile(c)), None)


def shellext_entries(dll: str) -> list[tuple[str, str | None, str]]:
    """(key under HKCU, value name, value) that register the thumbnail and preview handlers."""
    out = []
    for clsid, name in ((THUMB_CLSID, "Paperleaf PDF Thumbnails"), (PREVIEW_CLSID, "Paperleaf PDF Preview")):
        k = rf"{CLASSES}\CLSID\{clsid}"
        out += [(k, None, name), (k + r"\InprocServer32", None, dll),
                (k + r"\InprocServer32", "ThreadingModel", "Apartment")]
    out.append((rf"{CLASSES}\CLSID\{PREVIEW_CLSID}", "AppID", PREVHOST_APPID))
    out.append((rf"{CLASSES}\CLSID\{PREVIEW_CLSID}", "DisplayName", "Paperleaf PDF Preview"))
    for base in (rf"{CLASSES}\{PROGID}", rf"{CLASSES}\SystemFileAssociations\.pdf"):
        out.append((rf"{base}\ShellEx\{THUMB_SHELLEX}", None, THUMB_CLSID))
        out.append((rf"{base}\ShellEx\{PREVIEW_SHELLEX}", None, PREVIEW_CLSID))
    out.append((PREVIEW_HANDLERS, PREVIEW_CLSID, "Paperleaf PDF Preview"))
    return out


def tool_command(tool: str) -> str:
    return _quote(app_command()) + f' --tool {tool} "%1"'


def tool_entries() -> list[tuple[str, str | None, str]]:
    """(subkey under the extension's shell key, value name, value) for the tools submenu."""
    app_icon, _doc = icon_paths()
    base = TOOLS_KEY
    out = [(base, "MUIVerb", APP_NAME), (base, "Icon", app_icon.rsplit(",", 1)[0]), (base, "SubCommands", "")]
    for i, (tool, text) in enumerate(TOOL_VERBS):
        k = rf"{base}\shell\{i:02d}{tool}"
        out.append((k, "MUIVerb", text))
        if tool == "combine":
            out.append((k, "MultiSelectModel", "Document"))
        out.append((k + r"\command", None, tool_command(tool)))
    return out


def app_command() -> list[str]:
    """Command prefix that launches Paperleaf (frozen exe or dev interpreter)."""
    if getattr(sys, "frozen", False):
        return [sys.executable]
    pyw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    launcher = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                            "paperleaf.pyw")
    return [pyw if os.path.exists(pyw) else sys.executable, launcher]


def exe_name() -> str:
    return os.path.basename(sys.executable) if getattr(sys, "frozen", False) else "Paperleaf.exe"


def _quote(parts: list[str]) -> str:
    return " ".join(f'"{p}"' for p in parts)


def icon_paths() -> tuple[str, str]:
    """(application icon, document icon) as 'path,index' strings."""
    if getattr(sys, "frozen", False):
        base = os.path.dirname(sys.executable)
        res = os.path.join(base, "_internal", "resources")
        if not os.path.isdir(res):
            res = os.path.join(base, "resources")
    else:
        res = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                           "resources")
    app_ico = os.path.join(res, "paperleaf.ico")
    doc_ico = os.path.join(res, "pdf_document.ico")
    return f"{app_ico},0", f"{doc_ico},0"


def _require_windows():
    if winreg is None:
        raise OSError("Windows Explorer integration is only available on Windows.")


def _set(root, path: str, name: str | None, value, kind=None):
    kind = winreg.REG_SZ if kind is None else kind
    with winreg.CreateKeyEx(root, path, 0, winreg.KEY_WRITE) as k:
        winreg.SetValueEx(k, name or "", 0, kind, value)


def _delete_tree(root, path: str):
    try:
        with winreg.OpenKey(root, path, 0, winreg.KEY_ALL_ACCESS) as k:
            while True:
                try:
                    sub = winreg.EnumKey(k, 0)
                except OSError:
                    break
                _delete_tree(root, path + "\\" + sub)
        winreg.DeleteKey(root, path)
    except FileNotFoundError:
        pass


def _delete_value(root, path: str, name: str):
    try:
        with winreg.OpenKey(root, path, 0, winreg.KEY_SET_VALUE) as k:
            winreg.DeleteValue(k, name)
    except FileNotFoundError:
        pass


def _notify_shell():
    try:
        SHCNE_ASSOCCHANGED, SHCNF_IDLIST = 0x08000000, 0
        ctypes.windll.shell32.SHChangeNotify(SHCNE_ASSOCCHANGED, SHCNF_IDLIST, None, None)
    except Exception:
        pass


def register() -> None:
    _require_windows()
    hk = winreg.HKEY_CURRENT_USER
    cmd = app_command()
    app_icon, doc_icon = icon_paths()
    open_cmd = _quote(cmd) + ' "%1"'
    # ProgID
    pid = rf"{CLASSES}\{PROGID}"
    _set(hk, pid, None, "PDF Document")
    _set(hk, pid, "FriendlyTypeName", "PDF Document")
    _set(hk, pid + r"\DefaultIcon", None, doc_icon)
    _set(hk, pid + r"\shell", None, "open")
    _set(hk, pid + r"\shell\open", "FriendlyAppName", APP_NAME)
    _set(hk, pid + r"\shell\open\command", None, open_cmd)
    _set(hk, pid + r"\shell\print\command", None, _quote(cmd) + ' --print "%1"')
    _set(hk, pid + r"\shell\printto\command", None, _quote(cmd) + ' --printto "%1" "%2"')
    _set(hk, pid + r"\shell\printto", "LegacyDisable", "")
    # extensions
    for ext in EXTENSIONS:
        _set(hk, rf"{CLASSES}\{ext}\OpenWithProgids", PROGID, b"", winreg.REG_NONE)
        # right-click "Open with Paperleaf" regardless of the default app
        vk = rf"{CLASSES}\SystemFileAssociations\{ext}\shell\{VERB_KEY}"
        _set(hk, vk, None, f"Open with {APP_NAME}")
        _set(hk, vk, "Icon", app_icon.rsplit(",", 1)[0])
        _set(hk, vk + r"\command", None, open_cmd)
        _delete_tree(hk, rf"{CLASSES}\SystemFileAssociations\{ext}\shell\{TOOLS_KEY}")
        for sub, name, value in tool_entries():
            _set(hk, rf"{CLASSES}\SystemFileAssociations\{ext}\shell\{sub}", name, value)
    # Word, Excel and PowerPoint files: "Convert to PDF with Paperleaf" (uses the installed Office)
    for ext in OFFICE_EXTENSIONS:
        vk = rf"{CLASSES}\SystemFileAssociations\{ext}\shell\{TOPDF_KEY}"
        _set(hk, vk, None, f"Convert to PDF with {APP_NAME}")
        _set(hk, vk, "Icon", app_icon.rsplit(",", 1)[0])
        _set(hk, vk + r"\command", None, tool_command("topdf"))
    # Applications\Paperleaf.exe
    ak = rf"{CLASSES}\Applications\{exe_name()}"
    _set(hk, ak, "FriendlyAppName", APP_NAME)
    _set(hk, ak + r"\DefaultIcon", None, app_icon)
    _set(hk, ak + r"\shell\open\command", None, open_cmd)
    for ext in EXTENSIONS:
        _set(hk, ak + r"\SupportedTypes", ext, "")
    # Default apps listing
    _set(hk, CAPS_KEY, "ApplicationName", APP_NAME)
    _set(hk, CAPS_KEY, "ApplicationDescription", "Fast, private, offline PDF reader")
    _set(hk, CAPS_KEY, "ApplicationIcon", app_icon)
    for ext in EXTENSIONS:
        _set(hk, CAPS_KEY + r"\FileAssociations", ext, PROGID)
    _set(hk, r"Software\RegisteredApplications", APP_NAME, CAPS_KEY)
    # thumbnails and preview pane
    dll = shell_dll()
    if dll:
        for key, name, value in shellext_entries(dll):
            _set(hk, key, name, value)
    else:
        _unregister_shellext(hk)
    _notify_shell()


def _unregister_shellext(hk):
    for clsid in (THUMB_CLSID, PREVIEW_CLSID):
        _delete_tree(hk, rf"{CLASSES}\CLSID\{clsid}")
    for base in (rf"{CLASSES}\{PROGID}", rf"{CLASSES}\SystemFileAssociations\.pdf"):
        for shellex, clsid in ((THUMB_SHELLEX, THUMB_CLSID), (PREVIEW_SHELLEX, PREVIEW_CLSID)):
            key = rf"{base}\ShellEx\{shellex}"
            try:  # only remove the handler entry if it is ours
                with winreg.OpenKey(hk, key) as k:
                    if winreg.QueryValueEx(k, "")[0].upper() != clsid.upper():
                        continue
            except OSError:
                continue
            _delete_tree(hk, key)
    _delete_value(hk, PREVIEW_HANDLERS, PREVIEW_CLSID)


def unregister() -> None:
    _require_windows()
    hk = winreg.HKEY_CURRENT_USER
    _delete_tree(hk, rf"{CLASSES}\{PROGID}")
    for ext in EXTENSIONS:
        _delete_value(hk, rf"{CLASSES}\{ext}\OpenWithProgids", PROGID)
        _delete_tree(hk, rf"{CLASSES}\SystemFileAssociations\{ext}\shell\{VERB_KEY}")
        _delete_tree(hk, rf"{CLASSES}\SystemFileAssociations\{ext}\shell\{TOOLS_KEY}")
    for ext in OFFICE_EXTENSIONS:
        _delete_tree(hk, rf"{CLASSES}\SystemFileAssociations\{ext}\shell\{TOPDF_KEY}")
    _delete_tree(hk, rf"{CLASSES}\Applications\{exe_name()}")
    _delete_tree(hk, r"Software\Paperleaf\Capabilities")
    _delete_value(hk, r"Software\RegisteredApplications", APP_NAME)
    _unregister_shellext(hk)
    _notify_shell()


def registered_command() -> str | None:
    if winreg is None:
        return None
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, rf"{CLASSES}\{PROGID}\shell\open\command") as k:
            return winreg.QueryValueEx(k, "")[0]
    except OSError:
        return None


def is_registered() -> bool:
    return registered_command() is not None


def is_registered_here() -> bool:
    """Registered, and pointing at this copy of Paperleaf."""
    cmd = registered_command()
    return bool(cmd) and cmd.startswith(_quote(app_command()))


def is_default() -> bool:
    if winreg is None:
        return False
    try:
        path = r"Software\Microsoft\Windows\CurrentVersion\Explorer\FileExts\.pdf\UserChoice"
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path) as k:
            return winreg.QueryValueEx(k, "ProgId")[0] == PROGID
    except OSError:
        return False


def current_default_name() -> str:
    if winreg is None:
        return ""
    try:
        path = r"Software\Microsoft\Windows\CurrentVersion\Explorer\FileExts\.pdf\UserChoice"
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path) as k:
            return winreg.QueryValueEx(k, "ProgId")[0]
    except OSError:
        return ""


def open_default_apps() -> None:
    """Open Windows Settings on Paperleaf's default-apps page."""
    if not hasattr(os, "startfile"):
        return
    try:
        os.startfile(f"ms-settings:defaultapps?registeredAppUser={APP_NAME}")
    except OSError:
        subprocess.Popen(["control", "/name", "Microsoft.DefaultPrograms"])


def show_in_explorer(path: str) -> None:
    subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
