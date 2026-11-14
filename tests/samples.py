"""Generate sample PDFs used by tests and manual checks."""
from __future__ import annotations

import os
import sys

import pymupdf

LOREM = (
    "The quick brown fox jumps over the lazy dog. Paperleaf renders PDF pages with MuPDF "
    "and lets you search, select and copy text. Ligatures like office and fine should "
    "be searchable. Whole-word search must not match foxes when looking for fox."
)


def make_main(path: str):
    doc = pymupdf.open()
    for i in range(12):
        if i == 5:
            page = doc.new_page(width=842, height=595)  # landscape A4
        else:
            page = doc.new_page(width=595, height=842)  # portrait A4
        page.insert_text((72, 80), f"Chapter {i // 3 + 1} - Page {i + 1}", fontsize=20)
        box = pymupdf.Rect(72, 110, page.rect.width - 72, page.rect.height - 72)
        page.insert_textbox(box, (LOREM + " ") * 4, fontsize=11)
        page.draw_rect(pymupdf.Rect(72, page.rect.height - 60, 200, page.rect.height - 40),
                       color=(0.8, 0.1, 0.1), fill=(1, 0.9, 0.9))
    # rotated page (stored /Rotate 90)
    doc[7].set_rotation(90)
    # links
    doc[0].insert_link({"kind": pymupdf.LINK_GOTO, "from": pymupdf.Rect(72, 60, 300, 85),
                        "page": 9, "to": pymupdf.Point(72, 72)})
    doc[1].insert_text((72, 800), "Visit example.com", fontsize=12, color=(0, 0, 1))
    doc[1].insert_link({"kind": pymupdf.LINK_URI, "from": pymupdf.Rect(70, 788, 190, 804),
                        "uri": "https://example.com/"})
    toc = [
        [1, "Chapter 1", 1], [2, "Section 1.1", 2], [2, "Section 1.2", 3],
        [1, "Chapter 2", 4], [2, "Landscape page", 6],
        [1, "Chapter 3", 7], [2, "Rotated page", 8],
        [1, "Chapter 4", 10],
    ]
    doc.set_toc(toc)
    doc.set_page_labels([
        {"startpage": 0, "prefix": "", "style": "r", "firstpagenum": 1},
        {"startpage": 2, "prefix": "", "style": "D", "firstpagenum": 1},
    ])
    doc.embfile_add("notes.txt", b"Attachment contents\n", filename="notes.txt", desc="Notes")
    doc.set_metadata({"title": "Paperleaf Sample", "author": "Paperleaf Tests", "subject": "Testing",
                      "keywords": "pdf, sample", "creator": "samples.py", "producer": "PyMuPDF",
                      "creationDate": "D:20260101120000Z", "modDate": "D:20260102120000Z"})
    doc.save(path, garbage=3, deflate=True)


def make_encrypted(path: str, password: str = "secret"):
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 72), "Top secret content", fontsize=16)
    doc.save(path, encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw=password, owner_pw=password + "-owner",
             permissions=pymupdf.PDF_PERM_ACCESSIBILITY)


def _widget(page, kind, name, rect, **props):
    w = pymupdf.Widget()
    w.field_type = kind
    w.field_name = name
    w.rect = pymupdf.Rect(rect)
    w.text_fontsize = props.pop("fontsize", 11)
    w.border_color = props.pop("border", (0.4, 0.4, 0.4))
    w.border_width = 1
    for k, v in props.items():
        setattr(w, k, v)
    return page.add_widget(w)


def _radio_group(doc, page, name, rects, choices, selected=None, default=None):
    """A radio group the way Acrobat writes it: one parent field, one kid per button."""
    kids = []
    for rect in rects:
        a = _widget(page, pymupdf.PDF_WIDGET_TYPE_RADIOBUTTON, f"{name}_tmp{len(kids)}", rect,
                    field_value=False, border=(0, 0, 0))
        kids.append(a.xref)
    parent = doc.get_new_xref()
    flags = pymupdf.PDF_BTN_FIELD_IS_RADIO | pymupdf.PDF_BTN_FIELD_IS_NO_TOGGLE_TO_OFF
    v = f"/{selected}" if selected else "/Off"
    dv = f"/DV/{default}" if default else ""
    doc.update_object(parent, f"<</FT/Btn/Ff {flags}/T({name})/Kids[{' '.join(f'{k} 0 R' for k in kids)}]/V{v}{dv}>>")
    for x, choice in zip(kids, choices):
        apn = doc.xref_get_key(x, "AP/N")[1]
        doc.xref_set_key(x, "AP/N", apn.replace("/Yes", f"/{choice}"))
        for k in ("T", "FT", "Ff", "V"):
            doc.xref_set_key(x, k, "null")
        doc.xref_set_key(x, "Parent", f"{parent} 0 R")
        doc.xref_set_key(x, "AS", f"/{choice}" if choice == selected else "/Off")
    cat = doc.pdf_catalog()
    fields = doc.xref_get_key(cat, "AcroForm/Fields")[1]
    refs = [r for r in fields.strip("[]").split(" R") if r.strip()]
    refs = [r.strip() for r in refs if int(r.split()[0]) not in kids] + [f"{parent} 0"]
    doc.xref_set_key(cat, "AcroForm/Fields", "[" + " ".join(f"{r} R" for r in refs) + "]")
    return parent, kids


def make_form(path: str):
    """A fillable form with every field type (page 2 is rotated)."""
    T = pymupdf
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    page.insert_text((72, 60), "Membership application", fontsize=18)
    labels = [("Full name", 100), ("Notes", 130), ("Postcode", 200), ("Member ID", 230), ("I agree", 260),
              ("Country", 290), ("City", 320), ("Languages", 350), ("Colour", 430), ("Quantity", 460),
              ("Price", 490), ("Total", 520), ("Signature", 560)]
    for text, y in labels:
        page.insert_text((72, y + 14), text, fontsize=11)
    _widget(page, T.PDF_WIDGET_TYPE_TEXT, "name", (160, 100, 420, 120), field_label="Your full name",
            field_flags=T.PDF_FIELD_IS_REQUIRED)
    _widget(page, T.PDF_WIDGET_TYPE_TEXT, "notes", (160, 130, 420, 190), field_flags=T.PDF_TX_FIELD_IS_MULTILINE)
    _widget(page, T.PDF_WIDGET_TYPE_TEXT, "zip", (160, 200, 260, 220), text_maxlen=5,
            field_flags=T.PDF_TX_FIELD_IS_COMB)
    _widget(page, T.PDF_WIDGET_TYPE_TEXT, "member_id", (160, 230, 300, 250), field_value="A-123",
            field_flags=T.PDF_FIELD_IS_READ_ONLY)
    _widget(page, T.PDF_WIDGET_TYPE_CHECKBOX, "agree", (160, 260, 174, 274), field_value=False, border=(0, 0, 0))
    a = _widget(page, T.PDF_WIDGET_TYPE_COMBOBOX, "country", (160, 290, 300, 310),
                choice_values=["Spain", "France", "Italy"], field_value="Spain")
    doc.xref_set_key(a.xref, "DV", "(Spain)")
    _widget(page, T.PDF_WIDGET_TYPE_COMBOBOX, "city", (160, 320, 300, 340), choice_values=["Madrid", "Paris"],
            field_flags=T.PDF_CH_FIELD_IS_EDIT)
    _widget(page, T.PDF_WIDGET_TYPE_LISTBOX, "langs", (160, 350, 300, 420),
            choice_values=["English", "Spanish", "French", "German"],
            field_flags=T.PDF_CH_FIELD_IS_MULTI_SELECT)
    _radio_group(doc, page, "colour", [(160, 430, 174, 444), (220, 430, 234, 444), (280, 430, 294, 444)],
                 ["Red", "Green", "Blue"], selected="Green", default="Green")
    _widget(page, T.PDF_WIDGET_TYPE_TEXT, "qty", (160, 460, 240, 480))
    _widget(page, T.PDF_WIDGET_TYPE_TEXT, "price", (160, 490, 240, 510))
    total = _widget(page, T.PDF_WIDGET_TYPE_TEXT, "total", (160, 520, 240, 540),
                    field_flags=T.PDF_FIELD_IS_READ_ONLY)
    js = doc.get_new_xref()
    doc.update_object(js, "<</S/JavaScript/JS(event.value = this.getField('qty').value * "
                          "this.getField('price').value;)>>")
    doc.xref_set_key(total.xref, "AA", f"<</C {js} 0 R>>")
    doc.xref_set_key(doc.pdf_catalog(), "AcroForm/CO", f"[{total.xref} 0 R]")
    _widget(page, T.PDF_WIDGET_TYPE_SIGNATURE, "signature", (160, 560, 360, 600))
    btn = _widget(page, T.PDF_WIDGET_TYPE_BUTTON, "reset", (430, 760, 520, 785), button_caption="Reset",
                  fill_color=(0.85, 0.85, 0.85))
    doc.xref_set_key(btn.xref, "A", "<</S/ResetForm>>")
    # page 2: rotated, with a second widget of "name" and its own text field
    p2 = doc.new_page(width=595, height=842)
    p2.insert_text((72, 60), "Page two", fontsize=18)
    _widget(p2, T.PDF_WIDGET_TYPE_TEXT, "name", (160, 100, 420, 120))
    _widget(p2, T.PDF_WIDGET_TYPE_TEXT, "comments", (160, 140, 420, 160))
    p2.set_rotation(90)
    doc.save(path, garbage=1)


def make_xfa(path: str):
    """A hybrid XFA form: AcroForm fields plus an /XFA packet."""
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 72), "XFA form", fontsize=16)
    _widget(page, pymupdf.PDF_WIDGET_TYPE_TEXT, "field1", (72, 100, 300, 120))
    x = doc.get_new_xref()
    doc.update_object(x, "<<>>")
    doc.update_stream(x, b"<xdp:xdp xmlns:xdp='http://ns.adobe.com/xdp/'><template/></xdp:xdp>")
    doc.xref_set_key(doc.pdf_catalog(), "AcroForm/XFA", f"{x} 0 R")
    doc.save(path)


def make_hidden(path: str):
    """A PDF with hidden information (for Remove Hidden Information) and a large image."""
    import random
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 72), "Visible text on page one", fontsize=14)
    ocg = doc.add_ocg("Secret notes", on=False)
    page.insert_text((72, 120), "SECRET LAYER TEXT", fontsize=14, oc=ocg)
    page.insert_text((72, 160), "INVISIBLE TEXT", fontsize=14, render_mode=3)
    rnd = random.Random(4)
    data = bytearray(rnd.getrandbits(8) for _ in range(900 * 900 * 3))
    pix = pymupdf.Pixmap(pymupdf.csRGB, 900, 900, bytes(data), 0)
    page.insert_image(pymupdf.Rect(72, 200, 252, 380), pixmap=pix)  # ~360 dpi
    page.add_text_annot((400, 80), "a comment")
    page.insert_link({"kind": pymupdf.LINK_URI, "from": pymupdf.Rect(72, 60, 200, 76), "uri": "https://example.com/"})
    p2 = doc.new_page()
    p2.insert_text((72, 72), "Page two", fontsize=14)
    doc.set_toc([[1, "One", 1], [1, "Two", 2]])
    doc.embfile_add("secret.txt", b"attached secret\n", filename="secret.txt")
    doc.set_metadata({"title": "Hidden things", "author": "Someone", "keywords": "private"})
    doc.set_xml_metadata("<x:xmpmeta xmlns:x='adobe:ns:meta/'><rdf:RDF xmlns:rdf="
                         "'http://www.w3.org/1999/02/22-rdf-syntax-ns#'/></x:xmpmeta>")
    js = doc.get_new_xref()
    doc.update_object(js, "<</S/JavaScript/JS(app.alert('hi');)>>")
    cat = doc.pdf_catalog()
    doc.xref_set_key(cat, "OpenAction", f"{js} 0 R")
    js2 = doc.get_new_xref()
    doc.update_object(js2, "<</S/JavaScript/JS(var x = 1;)>>")
    doc.xref_set_key(cat, "Names/JavaScript", f"<</Names [(init) {js2} 0 R]>>")
    thumb = doc.get_new_xref()
    doc.update_object(thumb, "<</Width 1/Height 1/ColorSpace/DeviceGray/BitsPerComponent 8>>")
    doc.update_stream(thumb, b"\x80")
    doc.xref_set_key(doc[0].xref, "Thumb", f"{thumb} 0 R")
    doc.save(path, deflate=True)


SCAN_LINES = ["Invoice number 4711", "The quick brown fox jumps over the lazy dog", "Total amount due 1250 euros"]


def make_scanned(path: str):
    """A "scanned" PDF: pages that are only pictures of text (page 2 is rotated)."""
    src = pymupdf.open()
    for i in range(2):
        page = src.new_page(width=595, height=842)
        for k, line in enumerate(SCAN_LINES):
            page.insert_text((72, 120 + k * 40), f"{line}" if i == 0 else f"Page two {line}", fontsize=18)
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    page.insert_image(page.rect, pixmap=src[0].get_pixmap(dpi=200, colorspace=pymupdf.csGRAY))
    # page 2 was scanned sideways: the picture lies on its side and /Rotate 90 shows it upright
    page = doc.new_page(width=842, height=595)
    page.insert_image(page.rect, pixmap=src[1].get_pixmap(dpi=200, colorspace=pymupdf.csGRAY), rotate=90)
    page.set_rotation(90)
    doc.save(path, garbage=3, deflate=True)


def make_all(out_dir: str) -> dict:
    os.makedirs(out_dir, exist_ok=True)
    paths = {"main": os.path.join(out_dir, "sample.pdf"),
             "encrypted": os.path.join(out_dir, "encrypted.pdf"),
             "form": os.path.join(out_dir, "form.pdf"),
             "xfa": os.path.join(out_dir, "xfa.pdf"),
             "hidden": os.path.join(out_dir, "hidden.pdf"),
             "scanned": os.path.join(out_dir, "scanned.pdf")}
    make_main(paths["main"])
    make_encrypted(paths["encrypted"])
    make_form(paths["form"])
    make_xfa(paths["xfa"])
    make_hidden(paths["hidden"])
    make_scanned(paths["scanned"])
    return paths


if __name__ == "__main__":
    print(make_all(sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(__file__), "_out")))
