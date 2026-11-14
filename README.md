# Paperleaf

A fast, private PDF app for Windows, built as a free replacement for Adobe Acrobat. It has no
background services, no updater, no telemetry and no cloud: it makes no network connections at all
and runs only while its window is open.

**Features:** viewer with File Explorer integration (thumbnails, preview pane, right-click tools),
comments and markup, fill & sign, forms (filling and creating), organizing pages, passwords and
redaction, reducing size and converting, text recognition (OCR), editing text and images, comparing
files, certificate signatures and Read Out Loud.

Paperleaf is built with Python 3.12, [PySide6](https://doc.qt.io/qtforpython-6/) (Qt) and
[PyMuPDF](https://pymupdf.readthedocs.io/) (MuPDF).

## Privacy
- No network access: no update checks, telemetry, crash reports or cloud features.
- Links in PDFs ask before opening; launch actions are blocked; PDF JavaScript is never run.
- Settings, saved signatures and crash-recovery backups stay in `%APPDATA%\Paperleaf`.

## Install
Build the installer (see [Build](#build)) and run `installer\Output\PaperleafSetup-0.6.0.exe`. It installs for the current user only, with no
admin prompt, into `%LOCALAPPDATA%\Programs\Paperleaf`, and:
- adds Paperleaf to **Open with** and the PDF **right-click menu** ("Open with Paperleaf", and a **Paperleaf**
  submenu with Combine files, Split, Extract pages, Rotate pages and more)
- shows PDF **thumbnails** and the **preview pane** in File Explorer (when `PaperleafShell.dll` was built)
- optionally adds Paperleaf to the **Windows 11 right-click menu** itself, not only under "Show more
  options" (asks for administrator permission once; see [shellext/README.md](shellext/README.md))
- lists Paperleaf in **Settings › Apps › Default apps**, where you can make it the default PDF app
- adds a Start menu shortcut

Uninstalling (Settings › Apps) removes all registry entries. Settings are kept in `%APPDATA%\Paperleaf`.

## Develop
Requires Windows 10/11 and Python 3.12. Most of the code and tests also run on Linux (see
[CONTRIBUTING.md](CONTRIBUTING.md)).
```bash
python -m venv .venv
.venv\Scripts\pip install -r requirements-dev.txt
.venv\Scripts\python -m paperleaf tests\_out\sample.pdf     # run from source
.venv\Scripts\python -m pytest tests                    # tests
.venv\Scripts\python tests\samples.py                   # regenerate sample PDFs
```

## Build
```bash
powershell -ExecutionPolicy Bypass -File shellext\build.ps1     # optional: thumbnails, preview, Windows 11 menu
.venv\Scripts\pyinstaller paperleaf.spec --noconfirm
"%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe" installer\paperleaf.iss
```

## Command line
| Command | What it does |
|---|---|
| `Paperleaf.exe a.pdf b.pdf --page 5` | Open files in tabs (reuses the running window) |
| `Paperleaf.exe --print a.pdf` | Show the print dialog for a file |
| `Paperleaf.exe --printto a.pdf "Printer"` | Print silently to a printer |
| `Paperleaf.exe --tool combine a.pdf b.pdf` | Open a tool (`combine`, `split`, `extract`, `rotate`, `compress`, `word`, `images`, `protect`, `topdf`) |
| `Paperleaf.exe --register` / `--unregister` | Add or remove the Explorer integration |

## Forms
- Click a field to fill it in; Tab / Shift+Tab move between fields. Check boxes and radio buttons
  toggle with a click or Space. Every change is one Undo step.
- The bar above the page has **Highlight fields** and **Clear form**. The **Form** menu has
  Import Data / Export Data (FDF, XFDF or JSON) and Flatten Form (optionally with comments).
- Click a signature field to place one of your saved signatures (Comment & Sign ▸ Sign).
- **Form ▸ Prepare Form** adds, moves, resizes and deletes fields. Double-click a field for its
  properties.
- Paperleaf never runs PDF JavaScript, so calculated fields don't update by themselves. XFA forms are
  only partly supported.

## Pages
- **Document ▸ Organize Pages** (Ctrl+Shift+O) shows every page as a thumbnail: drag to move pages,
  drop files to insert them, and rotate, delete, duplicate, extract or split from the toolbar.
- The **Document** menu also has Insert, Replace, Extract, Delete and Rotate Pages, Split Document,
  Combine Files, Crop Pages, Change Page Size, Header & Footer, Page Numbers, Bates Numbering
  and Watermark. Everything that changes the open document can be undone.

## Protect, reduce and convert
- **Protect ▸ Encrypt with Password** adds an open password and/or permissions (printing, changes,
  copying). Security is applied when you save; **Remove Security** takes it off again.
- **Protect ▸ Redact** (Ctrl+Shift+R): select text or drag boxes to mark them, or use **Find Text &
  Redact** for words and patterns, then **Apply Redactions**. The content is removed, not covered.
- **Protect ▸ Remove Hidden Information** removes metadata, attachments, scripts, hidden layers and more.
- **File ▸ Reduce File Size** saves a smaller copy. **File ▸ Export To** makes Word, image, text or
  HTML files. **File ▸ Create PDF** makes PDFs from images or (with Microsoft Office installed)
  Word, Excel and PowerPoint files; opening such a file in Paperleaf does the same.

## Advanced tools
- **Document ▸ Recognize Text** makes scanned pages searchable and selectable (OCR with the
  Tesseract engine built into MuPDF, offline). English is included; more languages are Tesseract
  `.traineddata` files in `%APPDATA%\Paperleaf\tessdata`. A bar offers it when a file looks scanned.
- **Edit ▸ Edit Text & Images**: click a line of text to change it, or click an image to move,
  resize, replace or delete it; **Add Text** and **Add Image** put new content on the page. Edits
  work line by line; paragraphs are not reflowed.
- **Document ▸ Compare Files** makes a report: a summary, then each pair of pages side by side
  with the changed text marked (and, optionally, changed pictures and drawings).
- **Protect ▸ Sign with Certificate** signs with a digital ID (.pfx/.p12), in an empty signature
  field or a box you drag; **Create Digital ID** makes a self-signed one. A bar shows whether the
  signatures are valid, and the **Signature Panel** has the details. Paperleaf checks signatures offline,
  and saving a signed document adds the changes after the signatures, so they stay valid.
- **View ▸ Read Out Loud** reads a page or the rest of the document with the Windows voices.

## Layout
- `paperleaf/core`: document model (PyMuPDF wrapper, text, search, undo/redo, saving), annotations,
  forms (`forms.py`: reading, filling, flattening, FDF/XFDF/JSON, Prepare Form), page operations
  (`pages.py`: organize, insert, extract, split, combine, crop, headers/footers, Bates, watermarks),
  protect (`protect.py`: passwords, redaction, hidden information), reduce size and conversion
  (`convert.py`), text recognition (`ocr.py`), editing text and images (`edit.py`), comparing
  files (`compare.py`), certificate signatures (`signing.py`), crash recovery and settings
- `paperleaf/viewer`: the page canvas (tiled rendering, zoom, layouts, selection, tools), the
  annotation tools (`annot_tools.py`), the form tools (`form_tools.py`) and Edit PDF (`edit_tools.py`)
- `paperleaf/panels`: thumbnails, bookmarks, comments, attachments and search results
- `paperleaf/ui`: main window, tabs, find bar, Comment & Sign toolbar, form bar and Prepare Form
  toolbar, field properties, Organize Pages, page dialogs, Redact toolbar, protect and convert
  dialogs, Edit PDF toolbar, OCR / compare / certificate / read-aloud dialogs and commands,
  stamps, signatures, print, dialogs, presentation mode
- `paperleaf/winshell`: Windows registry integration
- `shellext`: PaperleafShell.dll (C++: Explorer thumbnails, preview pane, Windows 11 menu) and the
  sparse package for the Windows 11 menu
- `resources/tessdata`: English text recognition data (Apache-2.0)

## Contributing
Bug reports, ideas and pull requests are welcome. See [CONTRIBUTING.md](CONTRIBUTING.md). To report
a security problem privately, see [SECURITY.md](SECURITY.md).

## License
Paperleaf is free software under the [GNU Affero General Public License v3.0](LICENSE). It uses
PyMuPDF / MuPDF, which is AGPL-3.0 as well. Third-party components and their licenses are listed in
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

Paperleaf is not affiliated with Adobe. Adobe and Acrobat are trademarks of Adobe Inc.
