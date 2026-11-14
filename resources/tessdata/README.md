# OCR language data

`eng.traineddata` is the English model from Tesseract's `tessdata_fast` set (via the Ubuntu
`tesseract-ocr-eng` package), licensed under the Apache License 2.0. Paperleaf uses it offline with the
Tesseract engine built into MuPDF.

More languages: copy other `<language>.traineddata` files (from
https://github.com/tesseract-ocr/tessdata_fast) into `%APPDATA%\Paperleaf\tessdata`, or install
Tesseract-OCR; Paperleaf finds its `tessdata` folder too.
