# Third-party notices

Paperleaf uses the following components. Each one is the property of its authors and is used
under the license shown. The installer bundles them together with their license files.

| Component | Used for | License |
|---|---|---|
| [MuPDF](https://mupdf.com/) / [PyMuPDF](https://github.com/pymupdf/PyMuPDF) (Artifex) | PDF engine | AGPL-3.0 (or Artifex commercial license) |
| [Qt](https://www.qt.io/) / [PySide6](https://doc.qt.io/qtforpython-6/) (The Qt Company) | User interface | LGPL-3.0 |
| [pyHanko](https://github.com/MatthiasValvekens/pyHanko) | Certificate signatures | MIT |
| [pdf2docx](https://github.com/ArtifexSoftware/pdf2docx) | PDF to Word | MIT |
| [pywin32](https://github.com/mhammond/pywin32) | Windows integration | PSF |
| [Tesseract](https://github.com/tesseract-ocr/tesseract) English data (`resources/tessdata/eng.traineddata`, from [tessdata_fast](https://github.com/tesseract-ocr/tessdata_fast)) | Text recognition | Apache-2.0 |
| Python | Runtime | PSF |

Their dependencies (for example cryptography, asn1crypto, numpy and OpenCV, pulled in by
pyHanko and pdf2docx) come with their own licenses, which are included in the installed
packages.

The icons in the user interface are drawn in code or come from the Segoe Fluent Icons font that is
part of Windows; the font is not redistributed.
