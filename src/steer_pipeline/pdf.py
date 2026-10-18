"""PDF extraction with PyMuPDF: positioned lines, furniture, explicit column order.

We read raw lines from page.get_text("dict") and order them ourselves; PyMuPDF's
sort and de-hyphenation flags are not used because they would lose the mapping
from reading text back to the raw lines.
"""

import re
from collections import Counter
from pathlib import Path

import pymupdf

from .contract import build_document

EXTRACTION_VERSION = "pdf-1"
FURNITURE_BAND = 0.07      # top/bottom share of the page height checked for headers/footers
SAME_ROW = 2.0             # pt: line fragments whose vertical centres differ less than this are one row
FONT_STEP = 1.0           # pt: a larger change of the main font size between rows starts a paragraph
EDGE = 3.0                 # pt: tolerance when clustering line start positions
DOI_RE = re.compile(r"\b(10\.\d{4,9}/[^\s\"<>]+[^\s\"<>.,;)])")
PAGE_NO_RE = re.compile(r"^(page\s*)?\d{1,4}(\s*(of|/)\s*\d{1,4})?$", re.I)


class OcrRequired(Exception):
    pass


def _frame(page):
    """The unrotated page rectangle: the coordinate frame get_text() reports bboxes in."""
    return page.rect * page.derotation_matrix


def _upright(bbox: list[float], direction: tuple[float, float]) -> list[float]:
    """The bbox in a frame where the text runs left to right (only the axes matter, not the origin)."""
    x0, y0, x1, y1 = bbox
    dx, dy = round(direction[0]), round(direction[1])
    if (dx, dy) == (0, -1):   # text runs upwards
        return [-y1, x0, -y0, x1]
    if (dx, dy) == (0, 1):    # text runs downwards
        return [y0, -x1, y1, -x0]
    if (dx, dy) == (-1, 0):   # upside down
        return [-x1, -y1, -x0, -y0]
    return list(bbox)


def _rows(page) -> tuple[list[dict], bool]:
    """Text rows of a page and whether it has images.

    Each row has `page_bbox` (page coordinates, for highlighting), `bbox` (an upright frame for
    its text direction, used for ordering) and `dir`. Justified-text fragments on one baseline
    within a block are merged into one row, joined by a single space.
    """
    d = page.get_text("dict")
    has_image = any(b["type"] == 1 for b in d["blocks"])
    rows = []
    for bi, b in enumerate(d["blocks"]):
        if b["type"] != 0:
            continue
        frags = []
        for ln in b["lines"]:
            text = "".join(s["text"] for s in ln["spans"])
            if not text.strip():
                continue
            direction = (round(ln["dir"][0]), round(ln["dir"][1]))
            main_span = max(ln["spans"], key=lambda sp: len(sp["text"].strip()))
            frags.append({"page_bbox": list(ln["bbox"]), "bbox": _upright(ln["bbox"], direction),
                          "raw": text, "block": bi, "dir": direction, "size": main_span["size"]})
        frags.sort(key=lambda f: (f["dir"], (f["bbox"][1] + f["bbox"][3]) / 2, f["bbox"][0]))
        for f in frags:
            prev = rows[-1] if rows else None
            if (prev and prev["block"] == bi and prev["dir"] == f["dir"]
                    and abs((prev["bbox"][1] + prev["bbox"][3]) / 2 - (f["bbox"][1] + f["bbox"][3]) / 2) < SAME_ROW
                    and f["bbox"][0] >= prev["bbox"][2] - 1):
                if len(f["raw"].strip()) > len(prev["raw"].strip()):
                    prev["size"] = f["size"]
                prev["raw"] = prev["raw"].rstrip() + " " + f["raw"].lstrip()
                prev["bbox"] = [prev["bbox"][0], min(prev["bbox"][1], f["bbox"][1]), f["bbox"][2], max(prev["bbox"][3], f["bbox"][3])]
                pb, fb = prev["page_bbox"], f["page_bbox"]
                prev["page_bbox"] = [min(pb[0], fb[0]), min(pb[1], fb[1]), max(pb[2], fb[2]), max(pb[3], fb[3])]
            else:
                rows.append(f)
    return rows, has_image


def _furniture_keys(pages_rows: list[tuple[list[dict], float]]) -> set[str]:
    """Digit-stripped texts of top/bottom-band rows that repeat on at least half the pages."""
    seen = Counter()
    for rows, height in pages_rows:
        keys = {re.sub(r"\d+", "#", r["raw"].strip()) for r in rows if _in_band(r, height)}
        seen.update(keys)
    need = max(2, (len(pages_rows) + 1) // 2)
    return {k for k, n in seen.items() if n >= need}


def _in_band(row: dict, height: float) -> bool:
    box = row["page_bbox"]
    return box[3] <= height * FURNITURE_BAND or box[1] >= height * (1 - FURNITURE_BAND)


def find_gutter(rows: list[dict]) -> tuple[float | None, int]:
    """x of the column gutter, or None for a single column; also the number of qualifying splits.

    A second column exists when a cluster of row starts (x0) sits 20-75% across the text,
    holds at least max(3, 10%) of the rows, and at least 90% of the rows that start at the
    dominant left edge end before it.
    """
    if len(rows) < 6:
        return None, 0
    x_min = min(r["bbox"][0] for r in rows)
    x_max = max(r["bbox"][2] for r in rows)
    width = x_max - x_min
    starts = Counter(round(r["bbox"][0] / EDGE) for r in rows)
    candidates = []
    for key, n in starts.items():
        c2 = key * EDGE
        if n < max(3, 0.10 * len(rows)) or not (x_min + 0.20 * width <= c2 <= x_min + 0.75 * width):
            continue
        before = [r for r in rows if r["bbox"][0] < c2 - EDGE]
        if not before:
            continue
        left_edge = Counter(round(r["bbox"][0] / EDGE) for r in before).most_common(1)[0][0] * EDGE
        edge_rows = [r for r in before if abs(r["bbox"][0] - left_edge) <= EDGE]
        ending_before = [r for r in edge_rows if r["bbox"][2] <= c2 - 2]
        if len(ending_before) >= 0.9 * len(edge_rows):
            gx = (max(r["bbox"][2] for r in ending_before) + c2 - EDGE) / 2
            candidates.append((n, gx))
    if not candidates:
        return None, 0
    # several start clusters can describe the same gutter (e.g. first-line indents); count distinct gutters
    distinct: list[float] = []
    for gx in sorted(gx for _, gx in candidates):
        if not distinct or gx - distinct[-1] > 20:
            distinct.append(gx)
    return max(candidates)[1], len(distinct)


def order_rows(rows: list[dict], gx: float | None) -> list[dict]:
    """Reading order: bands top to bottom; inside a two-column band, left column then right column."""
    rows = sorted(rows, key=lambda r: (r["bbox"][1], r["bbox"][0]))
    if gx is None:
        for r in rows:
            r["column"] = "full"
        return rows
    for r in rows:
        x0, x1 = r["bbox"][0], r["bbox"][2]
        r["column"] = "left" if x1 <= gx else "right" if x0 >= gx else "full"
    out, band = [], []
    for r in rows + [None]:
        if r is None or r["column"] == "full":
            out += [x for x in band if x["column"] == "left"] + [x for x in band if x["column"] == "right"]
            band = []
            if r is not None:
                out.append(r)
        else:
            band.append(r)
    return out


def _table_like(row: dict, width: float) -> bool:
    t = row["raw"].strip()
    digits = sum(ch.isdigit() for ch in t)
    return (row["bbox"][2] - row["bbox"][0]) < 0.4 * width and digits >= max(2, 0.3 * len(t.replace(" ", "")))


def mark_paragraphs(rows: list[dict]) -> None:
    """para_break on a row that starts a new paragraph (rows already in reading order)."""
    edges: dict[str, tuple[float, float]] = {}
    for col in ("left", "right", "full"):
        xs = [r["bbox"] for r in rows if r["column"] == col]
        if xs:
            edges[col] = (min(b[0] for b in xs), max(b[2] for b in xs))
    for i, r in enumerate(rows):
        if i == 0:
            r["para_break"] = False
            continue
        p = rows[i - 1]
        height = max(p["bbox"][3] - p["bbox"][1], 1.0)
        gap = r["bbox"][1] - p["bbox"][3]
        moved = r["block"] != p["block"] or r["column"] != p["column"] or gap > 0.5 * height or gap < -height
        if abs(r["size"] - p["size"]) > FONT_STEP:
            r["para_break"] = True  # a heading or caption: the font size changes
            continue
        if not moved:
            r["para_break"] = False
            continue
        left, right = edges[p["column"]]
        prev_short = p["bbox"][2] < right - 0.15 * (right - left)
        prev_ends = p["raw"].rstrip()[-1:] in ".?!:"
        indented = r["bbox"][0] > edges[r["column"]][0] + 6
        big_gap = gap > height
        r["para_break"] = prev_short or prev_ends or indented or big_gap


def ingest_pdf(path: str | Path, role: str) -> dict:
    path = Path(path)
    content = path.read_bytes()
    doc = pymupdf.open(stream=content, filetype="pdf")
    raw_pages = []
    for page in doc:
        rows, has_image = _rows(page)
        raw_pages.append((page, rows, has_image))
    furniture = _furniture_keys([(rows, _frame(page).height) for page, rows, _ in raw_pages])

    pages, warnings, any_text, rotated_pages = [], [], False, set()
    for page, rows, has_image in raw_pages:
        pno, height, width = page.number + 1, _frame(page).height, _frame(page).width
        for r in rows:
            r["furniture"] = (_in_band(r, height) and (
                re.sub(r"\d+", "#", r["raw"].strip()) in furniture or bool(PAGE_NO_RE.match(r["raw"].strip()))))
        body = [r for r in rows if not r["furniture"]]
        if not body:
            code, why = ("OCR_REQUIRED_PAGE", "page has images but no extractable text") if has_image \
                else ("EMPTY_PAGE", "page has no text")
            warnings.append({"code": code, "page": pno, "detail": why})
            pages.append({"page": pno, "width": width, "height": height, "layout": "empty", "lines": []})
            continue
        any_text = True
        groups = sorted({r["dir"] for r in body}, key=lambda d: d != (1, 0))
        ordered, cols, gutters, layouts = [], set(), [], []
        for direction in groups:
            part = [r for r in body if r["dir"] == direction]
            gx, n_gutters = find_gutter(part)
            part = order_rows(part, gx)
            mark_paragraphs(part)
            if ordered:
                part[0]["para_break"] = True
            text_width = max(r["bbox"][2] for r in part) - min(r["bbox"][0] for r in part)
            for r in part:
                r["table_like"] = _table_like(r, text_width) or r["dir"] != (1, 0)
            ordered += part
            cols |= {r["column"] for r in part}
            gutters.append((gx, n_gutters, part))
        main_gx = gutters[0][0]
        layout = "single" if main_gx is None else "two_column" if "full" not in cols else "mixed"
        if len(groups) > 1 or groups[0] != (1, 0):
            n_rot = sum(r["dir"] != (1, 0) for r in body)
            rotated_pages.add(pno)
            warnings.append({"code": "TABLE_OR_FIGURE_TEXT", "page": pno,
                             "detail": f"{n_rot} rotated lines (usually a landscape table), read after the upright text"})
        for gx, n_gutters, part in gutters:
            if n_gutters > 1:
                warnings.append({"code": "UNCERTAIN_COLUMN_ORDER", "page": pno,
                                 "detail": f"{n_gutters} possible column splits; ordered by the strongest"})
            elif gx is not None:
                side = [r for r in part if r["column"] != "full"]
                right = sum(r["column"] == "right" for r in side)
                if min(right, len(side) - right) < 0.15 * len(side):
                    warnings.append({"code": "UNCERTAIN_COLUMN_ORDER", "page": pno,
                                     "detail": "one column holds under 15% of the column lines"})
        n_table = sum(r["table_like"] for r in ordered)
        if n_table >= 3:
            warnings.append({"code": "TABLE_OR_FIGURE_TEXT", "page": pno,
                             "detail": f"{n_table} short number-heavy lines (table or figure text)"})
        furn = [r for r in rows if r["furniture"]]
        lines = []
        for k, r in enumerate(ordered + furn, start=1):
            lines.append({
                "line_id": f"p{pno:04d}-l{k:04d}",
                "block": r["block"],
                "column": r.get("column", "full"),
                "bbox": [round(v, 2) for v in r["page_bbox"]],
                "raw": r["raw"],
                "furniture": r["furniture"],
                "table_like": r.get("table_like", False),
                "para_break": r.get("para_break", False),
            })
        pages.append({"page": pno, "width": width, "height": height, "layout": layout, "lines": lines})

    # the first body line of a page continues the previous page's last body line unless that ended
    # a sentence, or either page holds rotated (table) text
    prev, prev_page = None, None
    for pg in pages:
        body = [ln for ln in pg["lines"] if not ln["furniture"]]
        if body:
            body[0]["para_break"] = (prev is None or prev["raw"].rstrip()[-1:] in ".?!:"
                                     or pg["page"] in rotated_pages or prev_page in rotated_pages)
            prev, prev_page = body[-1], pg["page"]

    if not any_text:
        raise OcrRequired(f"OCR required: no extractable text on any page of {path}")

    first_text = " ".join(ln["raw"] for ln in pages[0]["lines"])
    m = DOI_RE.search(first_text)
    citation = {"doi": m.group(1) if m else None}
    warnings.append({"code": "NO_CITATION_METADATA", "page": None,
                     "detail": "title/authors not looked up for a local PDF file" + ("" if m else "; no DOI on page 1")})
    return build_document(
        content=content,
        source_type="pdf",
        role=role,
        access="full_text",
        provenance={"input": str(path), "file_path": str(path.resolve()), "fetched_via": "file"},
        extraction_version=EXTRACTION_VERSION,
        extractor={"name": "pymupdf", "version": pymupdf.__version__},
        pages=pages,
        citation=citation,
        warnings=warnings,
    )
