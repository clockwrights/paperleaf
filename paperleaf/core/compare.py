"""Compare two PDFs. No Qt here.

The result is a report PDF: a summary page, then the old and new pages side by
side. Text differences are highlight comments ("Replaced …", "Inserted …",
"Deleted …"), so the Comments panel lists every change; optional visual
differences (pictures, drawings) are outlined boxes.
"""
from __future__ import annotations

import difflib
import os
from dataclasses import dataclass, field

import pymupdf

DELETED = (0.95, 0.35, 0.35)
INSERTED = (0.35, 0.8, 0.35)
CHANGED = (1.0, 0.75, 0.2)
VISUAL = (0.2, 0.45, 0.95)
GAP, MARGIN, HEADER = 24.0, 24.0, 40.0


@dataclass
class Word:
    text: str
    page: int
    rect: pymupdf.Rect      # displayed page space


@dataclass
class Change:
    kind: str               # "replaced", "inserted", "deleted"
    old_words: list = field(default_factory=list)
    new_words: list = field(default_factory=list)

    @property
    def old_text(self) -> str:
        return " ".join(w.text for w in self.old_words)

    @property
    def new_text(self) -> str:
        return " ".join(w.text for w in self.new_words)

    def describe(self, limit: int = 120) -> str:
        def cut(s):
            return s if len(s) <= limit else s[:limit] + "…"
        if self.kind == "replaced":
            return f"Replaced “{cut(self.old_text)}” with “{cut(self.new_text)}”"
        if self.kind == "inserted":
            return f"Inserted “{cut(self.new_text)}”"
        return f"Deleted “{cut(self.old_text)}”"


@dataclass
class Result:
    changes: list
    pairs: list             # [(old page or None, new page or None)]
    visual: dict            # {(old page, new page): [rects in new page space]}
    old_pages: int = 0
    new_pages: int = 0

    def counts(self) -> dict:
        out = {"replaced": 0, "inserted": 0, "deleted": 0}
        for c in self.changes:
            out[c.kind] += 1
        return out


def _words(doc: pymupdf.Document) -> list[Word]:
    out = []
    for p in range(doc.page_count):
        page = doc[p]
        rot = page.rotation_matrix
        for w in page.get_text("words", sort=True):
            out.append(Word(w[4], p, (pymupdf.Rect(w[:4]) * rot).normalize()))
    return out


def _align_pages(old: pymupdf.Document, new: pymupdf.Document, a: list[Word], b: list[Word], blocks) -> list:
    """Pair old and new pages by how many words they share (pages may be
    inserted or deleted), keeping both orders."""
    n, m = old.page_count, new.page_count
    shared = [[0] * m for _ in range(n)]
    for blk in blocks:
        for k in range(blk.size):
            shared[a[blk.a + k].page][b[blk.b + k].page] += 1
    # longest-path style alignment (like aligning two sequences)
    score = [[0] * (m + 1) for _ in range(n + 1)]
    move = [[None] * (m + 1) for _ in range(n + 1)]
    for i in range(n - 1, -1, -1):
        move[i][m] = "old"
    for j in range(m - 1, -1, -1):
        move[n][j] = "new"
    for i in range(n - 1, -1, -1):
        for j in range(m - 1, -1, -1):
            best, how = score[i + 1][j + 1] + shared[i][j] + (1 if shared[i][j] or (n == m) else 0), "pair"
            if score[i + 1][j] > best:
                best, how = score[i + 1][j], "old"
            if score[i][j + 1] > best:
                best, how = score[i][j + 1], "new"
            score[i][j], move[i][j] = best, how
    pairs, i, j = [], 0, 0
    while i < n or j < m:
        how = move[i][j]
        if how == "pair":
            pairs.append((i, j))
            i, j = i + 1, j + 1
        elif how == "old":
            pairs.append((i, None))
            i += 1
        else:
            pairs.append((None, j))
            j += 1
    return pairs


def visual_differences(old: pymupdf.Document, op: int, new: pymupdf.Document, np_: int,
                       dpi: int = 36, cell: int = 6) -> list[pymupdf.Rect]:
    """Areas (new page space) where the rendered pages differ."""
    s = dpi / 72
    pa = old[op].get_pixmap(matrix=pymupdf.Matrix(s, s), colorspace=pymupdf.csGRAY, annots=False)
    pb = new[np_].get_pixmap(matrix=pymupdf.Matrix(s, s), colorspace=pymupdf.csGRAY, annots=False)
    w, h = min(pa.width, pb.width), min(pa.height, pb.height)
    sa, sb = pa.samples, pb.samples
    cols, rows = (w + cell - 1) // cell, (h + cell - 1) // cell
    hot = set()
    for cy in range(rows):
        for cx in range(cols):
            diff = 0
            for y in range(cy * cell, min(h, (cy + 1) * cell)):
                ra, rb = y * pa.stride, y * pb.stride
                for x in range(cx * cell, min(w, (cx + 1) * cell)):
                    if abs(sa[ra + x] - sb[rb + x]) > 60:
                        diff += 1
            if diff >= 3:
                hot.add((cx, cy))
    rects = []
    seen = set()
    for c in sorted(hot):  # merge touching cells into boxes
        if c in seen:
            continue
        stack, box = [c], [c[0], c[1], c[0], c[1]]
        seen.add(c)
        while stack:
            x, y = stack.pop()
            box = [min(box[0], x), min(box[1], y), max(box[2], x), max(box[3], y)]
            for d in ((1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (-1, -1), (1, -1), (-1, 1)):
                q = (x + d[0], y + d[1])
                if q in hot and q not in seen:
                    seen.add(q)
                    stack.append(q)
        rects.append(pymupdf.Rect(box[0] * cell / s, box[1] * cell / s, (box[2] + 1) * cell / s,
                                  (box[3] + 1) * cell / s))
    return rects


def compare(old: pymupdf.Document, new: pymupdf.Document, visual: bool = False, progress=None) -> Result:
    a, b = _words(old), _words(new)
    sm = difflib.SequenceMatcher(None, [w.text for w in a], [w.text for w in b], autojunk=False)
    blocks = sm.get_matching_blocks()
    changes = []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            continue
        kind = {"replace": "replaced", "insert": "inserted", "delete": "deleted"}[tag]
        changes.append(Change(kind, a[i1:i2], b[j1:j2]))
    pairs = _align_pages(old, new, a, b, blocks)
    vis = {}
    if visual:
        both = [(i, j) for i, j in pairs if i is not None and j is not None]
        for k, (i, j) in enumerate(both):
            if progress is not None and progress(k, len(both)) is False:
                break
            vis[(i, j)] = visual_differences(old, i, new, j)
    return Result(changes, pairs, vis, old.page_count, new.page_count)


def _merge_line_rects(words: list[Word]) -> dict[int, list[pymupdf.Rect]]:
    """One rect per line of consecutive changed words, by page."""
    out: dict[int, list] = {}
    for w in words:
        rs = out.setdefault(w.page, [])
        if rs and abs(rs[-1].y0 - w.rect.y0) < 2 and abs(rs[-1].y1 - w.rect.y1) < 2 and \
                -1 <= w.rect.x0 - rs[-1].x1 < 12:
            rs[-1] |= w.rect
        else:
            rs.append(pymupdf.Rect(w.rect))
    return out


def report(old: pymupdf.Document, new: pymupdf.Document, result: Result, path: str,
           old_name: str = "Old", new_name: str = "New", author: str = "Paperleaf Compare") -> str:
    """Write the comparison report PDF."""
    out = pymupdf.open()
    counts = result.counts()
    # --- summary page
    page = out.new_page(width=612, height=792)
    y = 72
    page.insert_text((60, y), "Compare Results", fontsize=20)
    y += 30
    for line in (f"Old: {old_name} ({result.old_pages} pages)", f"New: {new_name} ({result.new_pages} pages)", "",
                 f"{len(result.changes)} text difference{'s' if len(result.changes) != 1 else ''}: "
                 f"{counts['replaced']} replaced, {counts['inserted']} inserted, {counts['deleted']} deleted.",
                 f"{sum(1 for i, j in result.pairs if i is None)} page(s) added, "
                 f"{sum(1 for i, j in result.pairs if j is None)} page(s) removed.",
                 (f"{sum(len(v) for v in result.visual.values())} area(s) look different."
                  if result.visual else "")):
        if line:
            page.insert_text((60, y), line, fontsize=11)
        y += 18
    y += 6
    for color, label in ((DELETED, "Deleted (old document)"), (INSERTED, "Inserted (new document)"),
                         (CHANGED, "Replaced"), (VISUAL, "Looks different (pictures, drawings)")):
        page.draw_rect(pymupdf.Rect(60, y - 9, 74, y + 3), color=color, fill=color)
        page.insert_text((82, y), label, fontsize=10)
        y += 16
    y += 10
    page.insert_text((60, y), "Every difference is also a comment: open the Comments panel to go through them.",
                     fontsize=10, color=(0.35, 0.35, 0.35))
    # --- side-by-side pages
    where_old, where_new = {}, {}
    for i, j in result.pairs:
        wo = old[i].rect if i is not None else pymupdf.Rect(0, 0, 0, 0)
        wn = new[j].rect if j is not None else pymupdf.Rect(0, 0, 0, 0)
        ref = wo if i is not None else wn
        width = MARGIN * 2 + GAP + (wo.width or ref.width) + (wn.width or ref.width)
        height = HEADER + MARGIN + max(wo.height, wn.height)
        rp = out.new_page(width=width, height=height)
        left = pymupdf.Rect(MARGIN, HEADER, MARGIN + (wo.width or ref.width), HEADER + (wo.height or ref.height))
        right = pymupdf.Rect(left.x1 + GAP, HEADER, left.x1 + GAP + (wn.width or ref.width),
                             HEADER + (wn.height or ref.height))
        if i is not None:
            rp.show_pdf_page(left, old, i)
            rp.draw_rect(left, color=(0.6, 0.6, 0.6), width=0.5)
            where_old[i] = (rp.number, left.tl)
        else:
            rp.draw_rect(left, color=(0.7, 0.7, 0.7), width=0.5, dashes="[3 3] 0")
            rp.insert_text((left.x0 + 10, left.y0 + 20), "(no page: inserted in the new document)", fontsize=10,
                           color=(0.4, 0.4, 0.4))
        if j is not None:
            rp.show_pdf_page(right, new, j)
            rp.draw_rect(right, color=(0.6, 0.6, 0.6), width=0.5)
            where_new[j] = (rp.number, right.tl)
        else:
            rp.draw_rect(right, color=(0.7, 0.7, 0.7), width=0.5, dashes="[3 3] 0")
            rp.insert_text((right.x0 + 10, right.y0 + 20), "(page removed in the new document)", fontsize=10,
                           color=(0.4, 0.4, 0.4))
        label_old = f"Old: page {i + 1}" if i is not None else "Old: —"
        label_new = f"New: page {j + 1}" if j is not None else "New: —"
        rp.insert_text((left.x0, 26), label_old, fontsize=11)
        rp.insert_text((right.x0, 26), label_new, fontsize=11)
        for r in result.visual.get((i, j), []):
            box = pymupdf.Rect(r) + (right.x0, right.y0, right.x0, right.y0)
            a = rp.add_rect_annot(box & right)
            a.set_colors(stroke=VISUAL)
            a.set_border(width=1, dashes=[3, 2])
            a.set_info(title=author, content="Looks different", subject="Visual difference")
            a.update()

    def mark(where, words, color, text):
        for pno, rects in _merge_line_rects(words).items():
            if pno not in where:
                continue
            rpno, off = where[pno]
            rp = out[rpno]
            quads = [(r + (off.x, off.y, off.x, off.y)).quad for r in rects]
            a = rp.add_highlight_annot(quads)
            a.set_colors(stroke=color)
            a.set_info(title=author, content=text)
            a.update()
    for c in result.changes:
        if c.kind == "replaced":
            mark(where_old, c.old_words, CHANGED, c.describe())
            mark(where_new, c.new_words, CHANGED, c.describe())
        elif c.kind == "inserted":
            mark(where_new, c.new_words, INSERTED, c.describe())
        else:
            mark(where_old, c.old_words, DELETED, c.describe())
    out.set_metadata({"title": f"Compare {old_name} and {new_name}", "creator": "Paperleaf"})
    tmp = path + ".paperleaf-saving"
    out.save(tmp, garbage=3, deflate=True)
    out.close()
    os.replace(tmp, path)
    return path


def compare_files(old_path: str, new_path: str, out_path: str, visual: bool = False,
                  old_password=None, new_password=None, progress=None) -> Result:
    old = pymupdf.open(old_path)
    new = pymupdf.open(new_path)
    try:
        for d, pw in ((old, old_password), (new, new_password)):
            if d.needs_pass and not d.authenticate(pw or ""):
                from paperleaf.core.document import PasswordRequired
                raise PasswordRequired(pw is not None)
        res = compare(old, new, visual, progress)
        report(old, new, res, out_path, os.path.basename(old_path), os.path.basename(new_path))
        return res
    finally:
        old.close()
        new.close()
