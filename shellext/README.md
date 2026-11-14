# PaperleafShell: File Explorer integration (Windows only)

`PaperleafShell.dll` is a small C++ shell extension with three COM objects:

| Object | What it does | Registered by |
|---|---|---|
| Thumbnail provider `{27B1C595-…}` | PDF thumbnails in File Explorer (first page) | `Paperleaf.exe --register` (per user, HKCU) |
| Preview handler `{EFED9792-…}` | The preview pane (Alt+P): every page, scrollable | `Paperleaf.exe --register` (per user, HKCU) |
| Explorer command `{E37FAEF7-…}` | A **Paperleaf** entry with the tools in the Windows 11 top-level right-click menu | the sparse package (`install_menu.ps1`) |

Pages are drawn with Windows' own PDF engine (`Windows.Data.Pdf`), so the DLL needs no extra
libraries and never goes online. Password-protected PDFs get no thumbnail or preview.

The classic right-click menu ("Show more options" on Windows 11, and Windows 10) keeps working
without any of this: it is plain registry verbs (`paperleaf/winshell/registry.py`).

## Build
Needs Visual Studio 2022 with the C++ desktop workload, CMake and the Windows 11 SDK.

```bash
powershell -ExecutionPolicy Bypass -File shellext\build.ps1
```

This builds `build\Release\PaperleafShell.dll`, packs `build\PaperleafMenu.msix` (a *sparse* package:
just the manifest and logos; the DLL and Paperleaf.exe stay in Paperleaf's folder) and signs it with a
self-signed certificate `CN=Paperleaf` that it creates in your user certificate store
(`build\PaperleafMenu.cer` is its public part). PyInstaller (`paperleaf.spec`) copies the DLL next to
Paperleaf.exe, and the installer adds the package and `install_menu.ps1`.

To sign with a real code-signing certificate instead, pass its subject:
`build.ps1 -Publisher "CN=Your Company, O=..., C=..."` (it must match the certificate exactly).

## Windows 11 top-level menu
Windows only shows packaged menu handlers in the new menu. Installing the package needs its
certificate to be trusted, which takes administrator permission once:

```bash
powershell -ExecutionPolicy Bypass -File "%LOCALAPPDATA%\Programs\Paperleaf\install_menu.ps1"
powershell -ExecutionPolicy Bypass -File "%LOCALAPPDATA%\Programs\Paperleaf\install_menu.ps1" -Remove
```

The installer runs this when "Add Paperleaf to the Windows 11 right-click menu" is ticked, and removes
the package when Paperleaf is uninstalled. With a certificate from a trusted authority no admin step
would be needed.

## Status
Checked on Windows 11 (VS 2022 17.14, SDK 10.0.26100):
- [x] The DLL builds without warnings (`/W4`); it only depends on Windows system DLLs.
- [x] Thumbnail provider, loaded directly: first page in ~0.3 s; a password-protected PDF gets none.
- [x] Preview handler hosted in a test window: shows and scrolls pages, resizes, unloads cleanly.
- [x] `Paperleaf.exe --register` points both handlers at the installed DLL.

Still to check:
1. `makeappx` accepts the manifest and `install_menu.ps1` installs the package: **Paperleaf** appears in
   the top-level menu with its submenu; Combine is hidden for one file; with several files
   selected, one Paperleaf window gets all of them.
2. Uninstalling removes the registry entries and the package.
