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
ROW_GAP = 0.25            # share of the text width: a wider gap between fragments on one baseline splits the row
INDENT = 20.0             # pt: rows starting this far right of a column edge (bullets, indents) still belong to it
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


def text_gutters(frags: list[dict], indent: float = 0.0, depth: int = 0) -> list[float]:
    """Every justified text-column gutter among the fragments, nested columns included.

    Each side of any column split is searched again, even when that split itself is not a
    justified text gutter (a ragged sidebar beside two justified columns).
    """
    gx, _ = find_gutter(frags, indent)
    if gx is None:
        return []
    own = [gx] if text_gutter(frags, indent) == gx else []
    if depth >= 2:
        return own
    left = [f for f in frags if f["bbox"][2] <= gx]
    right = [f for f in frags if f["bbox"][0] >= gx]
    return sorted(text_gutters(left, INDENT, depth + 1) + own + text_gutters(right, INDENT, depth + 1))


def text_gutter(frags: list[dict], indent: float = 0.0) -> float | None:
    """A column gutter among a page's unmerged fragments, accepted only for justified text columns.

    find_gutter() also fires on tables (label column vs value columns). Text columns are told
    apart by shape: on most baselines the text left of the gutter ends at one x (justified) and has
    no hole wider than 1.5 line heights inside it; table rows have holes between cells.
    """
    gx, _ = find_gutter(frags, indent)
    if gx is None:
        return None
    left: dict[int, list[dict]] = {}
    for f in frags:
        if f["bbox"][2] <= gx:
            left.setdefault(round((f["bbox"][1] + f["bbox"][3]) / 2 / SAME_ROW), []).append(f)
    if len(left) < 6:
        return None
    edge = max(f["bbox"][2] for fs in left.values() for f in fs)
    text_like = 0
    for fs in left.values():
        fs.sort(key=lambda f: f["bbox"][0])
        height = max(f["bbox"][3] - f["bbox"][1] for f in fs)
        gaps = [b["bbox"][0] - a["bbox"][2] for a, b in zip(fs, fs[1:])]
        # a text line: ends at the column edge and has no table-like hole inside
        text_like += edge - fs[-1]["bbox"][2] <= EDGE and all(g <= 1.5 * height for g in gaps)
    return gx if text_like >= 0.6 * len(left) else None


def join_fragments(frags: list[dict], text_width: float, gutters: list[float] = ()) -> list[dict]:
    """Merge fragments of one baseline (same direction) into rows, left to right.

    Two neighbours further apart than ROW_GAP x the text width stay separate rows, so a block that
    spans both columns never fuses a left-column line with the right-column line beside it.
    """
    frags = sorted(frags, key=lambda f: (f["dir"], (f["bbox"][1] + f["bbox"][3]) / 2))
    groups: list[list[dict]] = []
    for f in frags:
        g = groups[-1] if groups else None
        centre = (f["bbox"][1] + f["bbox"][3]) / 2
        if g and g[0]["dir"] == f["dir"] and centre - (g[0]["bbox"][1] + g[0]["bbox"][3]) / 2 < SAME_ROW:
            g.append(f)
        else:
            groups.append([f])
    rows = []
    for g in groups:
        g.sort(key=lambda f: f["bbox"][0])
        row = None
        for f in g:
            across = row is not None and any(row["bbox"][2] <= g + 1 <= f["bbox"][0] + 2 for g in gutters)
            if row is None or across or f["bbox"][0] - row["bbox"][2] > ROW_GAP * text_width:
                row = dict(f)
                rows.append(row)
                continue
            if len(f["raw"].strip()) > len(row["raw"].strip()):
                row["size"] = f["size"]
            row["raw"] = row["raw"].rstrip() + " " + f["raw"].lstrip()
            row["bbox"] = [row["bbox"][0], min(row["bbox"][1], f["bbox"][1]), max(row["bbox"][2], f["bbox"][2]),
                           max(row["bbox"][3], f["bbox"][3])]
            pb, fb = row["page_bbox"], f["page_bbox"]
            row["page_bbox"] = [min(pb[0], fb[0]), min(pb[1], fb[1]), max(pb[2], fb[2]), max(pb[3], fb[3])]
    return rows


def _rows(page) -> tuple[list[dict], bool]:
    """Text rows of a page and whether it has images.

    Each row has `page_bbox` (page coordinates, for highlighting), `bbox` (an upright frame for
    its text direction, used for ordering) and `dir`. Fragments on one baseline within a block
    are merged into one row (see join_fragments).
    """
    d = page.get_text("dict")
    has_image = any(b["type"] == 1 for b in d["blocks"])
    by_block: dict[int, list[dict]] = {}
    for bi, b in enumerate(d["blocks"]):
        if b["type"] != 0:
            continue
        for ln in b["lines"]:
            text = "".join(s["text"] for s in ln["spans"])
            if not text.strip():
                continue
            direction = (round(ln["dir"][0]), round(ln["dir"][1]))
            main_span = max(ln["spans"], key=lambda sp: len(sp["text"].strip()))
            by_block.setdefault(bi, []).append({
                "page_bbox": list(ln["bbox"]), "bbox": _upright(ln["bbox"], direction),
                "raw": text, "block": bi, "dir": direction, "size": main_span["size"]})
    widths: dict[tuple, float] = {}
    for f in (f for frags in by_block.values() for f in frags):
        lo, hi = widths.get(f["dir"], (f["bbox"][0], f["bbox"][2]))
        widths[f["dir"]] = (min(lo, f["bbox"][0]), max(hi, f["bbox"][2]))
    everything = [f for frags in by_block.values() for f in frags]
    gutters = {d: text_gutters([f for f in everything if f["dir"] == d]) for d in widths}
    rows = []
    for frags in by_block.values():
        for direction in {f["dir"] for f in frags}:
            lo, hi = widths[direction]
            rows += join_fragments([f for f in frags if f["dir"] == direction], hi - lo, gutters[direction])
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


def _numeric(text: str) -> bool:
    t = text.replace(" ", "")
    digits = sum(ch.isdigit() for ch in t)
    return digits >= max(2, 0.3 * len(t))


def find_gutter(rows: list[dict], indent: float = 0.0) -> tuple[float | None, int]:
    """x of the column gutter, or None for a single column; also the number of qualifying splits.

    A second column exists when a cluster of row starts (x0) sits 20-75% across the text, at least
    max(3, 10%) of the rows start there (or up to `indent` pt to its right: bullets and indents, used
    for nested columns), and at least 90% of the rows that start at the dominant left edge end before it.
    """
    rows = [r for r in rows if not _numeric(r["raw"])]  # table cells must not look like a column of text
    if len(rows) < 6:
        return None, 0
    x_min = min(r["bbox"][0] for r in rows)
    x_max = max(r["bbox"][2] for r in rows)
    width = x_max - x_min
    starts = Counter(round(r["bbox"][0] / EDGE) for r in rows)
    candidates = []
    for key in starts:
        c2 = key * EDGE
        n = sum(c2 - EDGE <= r["bbox"][0] <= c2 + max(indent, EDGE) for r in rows)
        if n < max(3, 0.10 * len(rows)) or not (x_min + 0.20 * width <= c2 <= x_min + 0.75 * width):
            continue
        before = [r for r in rows if r["bbox"][0] < c2 - EDGE]
        if not before:
            continue
        left_edge = Counter(round(r["bbox"][0] / EDGE) for r in before).most_common(1)[0][0] * EDGE
        # column-sized rows at the left edge (wide title/author lines sharing that edge form their own band)
        edge_rows = [r for r in before if abs(r["bbox"][0] - left_edge) <= EDGE
                     and r["bbox"][2] - r["bbox"][0] < 0.6 * width]
        if not edge_rows:
            continue
        ending_before = [r for r in edge_rows if r["bbox"][2] <= c2 - 2]
        if len(ending_before) >= 0.9 * len(edge_rows):
            # the gutter sits right of every row that ends before the second column, not only edge rows
            left_end = max(r["bbox"][2] for r in before if r["bbox"][2] <= c2 - 2)
            gx = (left_end + c2 - EDGE) / 2
            candidates.append((n, gx))
    if not candidates:
        return None, 0
    # several start clusters can describe the same gutter (e.g. first-line indents); count distinct gutters
    distinct: list[float] = []
    for gx in sorted(gx for _, gx in candidates):
        if not distinct or gx - distinct[-1] > 20:
            distinct.append(gx)
    return max(candidates)[1], len(distinct)


def by_lines(rows: list[dict]) -> list[dict]:
    """Top to bottom by visual line, left to right within a line.

    Rows whose vertical centres are within half a line height of the line's first row share the
    line, so table cells on one baseline read in x order even when PyMuPDF puts them in
    different blocks with slightly different y.
    """
    rows = sorted(rows, key=lambda r: (r["bbox"][1] + r["bbox"][3]) / 2)
    out, line, top = [], [], None
    for r in rows:
        centre = (r["bbox"][1] + r["bbox"][3]) / 2
        if line and centre - top > 0.5 * (line[0]["bbox"][3] - line[0]["bbox"][1]):
            out += sorted(line, key=lambda x: x["bbox"][0])
            line = []
        if not line:
            top = centre
        line.append(r)
    return out + sorted(line, key=lambda x: x["bbox"][0])


def order_rows(rows: list[dict], gx: float | None, depth: int = 0) -> list[dict]:
    """Reading order: bands top to bottom; inside a two-column band, left column then right column.

    Each side of a band is ordered again the same way, so a third column (a sidebar beside two
    columns) is found too. Column labels come from the outermost split.
    """
    rows = by_lines(rows)
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
            for side in ("left", "right"):
                part = [x for x in band if x["column"] == side]
                inner = find_gutter(part, INDENT)[0] if depth < 2 else None
                if inner is None:
                    out += by_lines(part)
                else:
                    out += order_rows(part, inner, depth + 1)
                    for x in part:
                        x["column"] = side
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
        # a short last line of a full-width paragraph can land on one side of the gutter: same left
        # edge and normal line spacing mean it continues the paragraph, not a new column
        same_flow = abs(r["bbox"][0] - p["bbox"][0]) <= EDGE and 0 <= gap <= 0.5 * height
        moved = (r["block"] != p["block"] or (r["column"] != p["column"] and not same_flow)
                 or gap > 0.5 * height or gap < -height)
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
    return ingest_pdf_bytes(path.read_bytes(), role, source_type="pdf",
                            provenance={"input": str(path), "file_path": str(path.resolve()), "fetched_via": "file"})


def ingest_pdf_bytes(content: bytes, role: str, *, source_type: str, provenance: dict,
                     citation: dict | None = None) -> dict:
    """Extract a PDF given as bytes. `citation`, when known (fetched metadata), replaces the page-1 DOI guess."""
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
        raise OcrRequired(f"OCR required: no extractable text on any page of {provenance.get('input')}")

    if citation is None:
        first_text = " ".join(ln["raw"] for ln in pages[0]["lines"])
        m = DOI_RE.search(first_text)
        citation = {"doi": m.group(1) if m else None}
        warnings.append({"code": "NO_CITATION_METADATA", "page": None,
                         "detail": "title/authors not looked up for a local PDF file" + ("" if m else "; no DOI on page 1")})
    return build_document(
        content=content,
        source_type=source_type,
        role=role,
        access="full_text",
        provenance=provenance,
        extraction_version=EXTRACTION_VERSION,
        extractor={"name": "pymupdf", "version": pymupdf.__version__},
        pages=pages,
        citation=citation,
        warnings=warnings,
    )
