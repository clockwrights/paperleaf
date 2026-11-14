# Contributing to Paperleaf

Thanks for helping. Bug reports, ideas and pull requests are all welcome.

## Ground rules
- **Privacy first.** Paperleaf never uses the network: no telemetry, update checks, crash
  reporting, online fonts or cloud services. Pull requests that add network access won't be merged.
- **Keep `pymupdf<2`.** PyMuPDF 2.x pulls in numpy and onnxruntime and starts about 6 s slower.
- **UI text** is plain and short, with wording close to Acrobat's.

## Set up
```bash
python -m venv .venv
.venv\Scripts\pip install -r requirements-dev.txt
.venv\Scripts\python -m paperleaf
```

## Tests
```bash
.venv\Scripts\python -m pytest -q tests
```
Tests create their own sample PDFs (`tests/samples.py`). GUI tests drive the real widgets with
simulated mouse and keyboard input. On Linux, run them headless:
```bash
QT_QPA_PLATFORM=offscreen python -m pytest -q tests
```
Windows-only tests (registry, Read Out Loud, Office conversion) are skipped elsewhere.
Please add or update tests for every change.

## Pull requests
- Keep each pull request to one change, and describe what it does and how you tested it.
- Every change to a document must go through `PdfDocument.edit()` so it can be undone.
- By contributing you agree that your contribution is licensed under the AGPL-3.0, like the
  rest of the project.

## Reporting bugs
Open an issue with the steps to reproduce, what you expected and what happened, and your Windows
and Paperleaf versions. If possible, attach a PDF that shows the problem, but **never attach
documents with personal or confidential information**.
