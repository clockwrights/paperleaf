# PyInstaller build spec:  .venv\Scripts\pyinstaller paperleaf.spec --noconfirm
import os

block_cipher = None

EXCLUDES = [
    "tkinter", "unittest", "pydoc", "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets",
    "PySide6.QtQml", "PySide6.QtQuick", "PySide6.QtMultimedia", "PySide6.Qt3DCore", "PySide6.QtCharts",
    "PySide6.QtDataVisualization", "PySide6.QtPdf", "PySide6.QtSql", "PySide6.QtTest",
    "PySide6.QtOpenGL", "PySide6.QtOpenGLWidgets", "PySide6.QtSvg", "PySide6.QtDesigner",
    "PIL", "pytest", "onnxruntime",
]
# numpy and OpenCV are needed by pdf2docx (Export to Word). They are only
# imported while exporting, so they don't slow down starting Paperleaf.
# ssl and aiohttp are imported by pyHanko (certificate signatures). Paperleaf never
# lets it go online: fetching is off and paperleaf.core.signing blocks network
# connections while pyHanko runs.
from PyInstaller.utils.hooks import collect_submodules

SIGNING = (collect_submodules("pyhanko") + collect_submodules("pyhanko_certvalidator")
           + collect_submodules("oscrypto") + ["asn1crypto", "tzlocal", "cryptography"])

a = Analysis(
    ["paperleaf.pyw"],
    pathex=["."],
    datas=[("resources/paperleaf.ico", "resources"), ("resources/pdf_document.ico", "resources"),
           ("resources/paperleaf.png", "resources"), ("resources/tessdata", "resources/tessdata"),
           ("LICENSE", "."), ("THIRD_PARTY_NOTICES.md", ".")],
    # PaperleafShell.dll (Explorer thumbnails, preview, Windows 11 menu) is built separately
    # (shellext/build.ps1) and copied next to Paperleaf.exe when it exists
    binaries=[("shellext/build/Release/PaperleafShell.dll", ".")] if os.path.exists(
        "shellext/build/Release/PaperleafShell.dll") else [],
    hiddenimports=["PySide6.QtPrintSupport", "PySide6.QtNetwork", "pdf2docx", "docx", "cv2",
                   "win32com.client", "pythoncom"] + SIGNING,
    excludes=EXCLUDES,
    noarchive=False,
)
# software OpenGL fallback is not needed for a widgets app
a.binaries = [b for b in a.binaries if "opengl32sw" not in b[0].lower()]
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)
exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="Paperleaf",
    icon="resources/paperleaf.ico",
    version="installer/version_info.txt",
    console=False,
    upx=False,
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="Paperleaf")
