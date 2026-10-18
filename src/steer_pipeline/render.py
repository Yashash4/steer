"""Render a checked post to PNGs (1080 x 1350): plain HTML templates per slide, headless Chromium via Playwright.

Only text that passed the line checks is drawn, plus code-built parts: the sources slide (never model-written,
never an invented page), the draft mark, slide numbers and the wordmark. Emphasis (key number + label, hook
italic) only repeats text of its own slide; check_artwork has verified that.

Text fitting: every slide has one fixed text area. Each text box carries a type ladder; the whole slide steps down
the ladders together until nothing overflows (scrollHeight <= clientHeight, scrollWidth <= clientWidth). The
ladders stop at the floors (body 28 px, sources 24 px): if the text still overflows there, render QA fails.
Render QA (fails the render): no overflow, body >= 28 px, sources >= 24 px, <= 35 body words per slide (counted
from the post's lines), <= 3 sources per sources slide, every claim used is on a sources slide, PNG size exact.
Line art (lineart.py) is decoration chosen by code per slide; render QA fails if its box touches a text box
(LINEART_OVER_TEXT) or a food drawing sits on a slide with a health line or a number (LINEART_FOOD_ON_HEALTH).
Labels (draft mark, wordmark, slide number, the short source under a health card) are not body text.

The browser lives in code/.browsers (PLAYWRIGHT_BROWSERS_PATH), installed with
`uv run playwright install chromium-headless-shell`; nothing is installed system-wide.
"""

import html
import json
import os
import re
import struct
import tempfile
from pathlib import Path

from . import lineart

CODE = Path(__file__).resolve().parents[2]
os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(CODE / ".browsers"))
FONTS = CODE / "fonts"
W, H = 1080, 1350
WORD_BUDGET = 35
FLOORS = {"body": 28, "sources": 24}
LADDERS = {  # px, largest first; the last step is the floor
    "hook": [96, 88, 80, 72, 64, 56], "serif": [64, 58, 52, 46, 40], "body": [44, 40, 36, 32, 28],
    "key": [132, 116, 100, 88, 72], "sources": [28, 26, 24],
}
SOURCES_PER_SLIDE = 3
DRAFT = "draft · pending doctor review"
FOOTNOTE = "Every claim on these slides is linked to the paper. Pending review by a Steer doctor before publishing."
# only the studio's publishable export (all four review stages complete) renders without the draft mark
PUBLISHED_FOOTNOTE = "Every claim on these slides is linked to the paper. Reviewed by a Steer doctor before publishing."

FIT_JS = """() => {
  const area = document.querySelector('.content');
  const boxes = [...document.querySelectorAll('[data-ladder]')];
  const ladder = b => b.dataset.ladder.split(',').map(Number);
  const steps = Math.max(1, ...boxes.map(b => ladder(b).length));
  const apply = i => boxes.forEach(b => { const l = ladder(b); b.style.fontSize = l[Math.min(i, l.length - 1)] + 'px'; });
  const fits = () => area.scrollHeight <= area.clientHeight && area.scrollWidth <= area.clientWidth
                     && boxes.every(b => b.scrollWidth <= b.clientWidth);
  let i = 0;
  for (; i < steps; i++) { apply(i); if (fits()) break; }
  if (i === steps) { i = steps - 1; apply(i); }
  return {step: i, overflow: !fits(),
          boxes: boxes.map(b => ({kind: b.dataset.kind, px: parseFloat(getComputedStyle(b).fontSize)}))};
}"""


TEXT_BOXES_JS = """() => { const out = [], w = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  for (let n; (n = w.nextNode());) { if (!n.textContent.trim()) continue; const r = document.createRange(); r.selectNodeContents(n);
    for (const q of r.getClientRects()) if (q.width > 0) out.push([q.left, q.top, q.right, q.bottom]); }
  document.querySelectorAll('.card, .rule').forEach(e => { const q = e.getBoundingClientRect(); out.push([q.left, q.top, q.right, q.bottom]); });
  return out; }"""
ADD_ART_JS = """([src, box]) => { const i = document.createElement('img'); i.src = src; i.className = 'lineart';
  i.style.cssText = `position:absolute;left:${box[0]}px;top:${box[1]}px;width:${box[2] - box[0]}px;height:${box[3] - box[1]}px;pointer-events:none`;
  document.body.insertBefore(i, document.body.firstChild); return i.decode(); }"""


def _art(lines: list[dict], unit: dict, bold: bool) -> dict:
    """What lineart.choose needs from a slide: its words (incl. the key number), whether it has a health line."""
    texts = [ln["text"] for ln in lines] + [unit[k] for k in ("key_number", "key_label") if unit.get(k)]
    return {"texts": texts, "has_health": any(ln["kind"] == "health" for ln in lines), "eligible": True, "bold": bold}


def _css(p: dict, w: int = W, h: int = H) -> str:
    return f"""
@font-face {{ font-family: Fraunces; src: url('Fraunces.ttf'); }}
@font-face {{ font-family: Fraunces; font-style: italic; src: url('Fraunces-Italic.ttf'); }}
@font-face {{ font-family: Inter; src: url('Inter.ttf'); }}
* {{ box-sizing: border-box; margin: 0; padding: 0; }}
html, body {{ width: {w}px; height: {h}px; overflow: hidden; }}
body {{ background: {p['cream']}; color: {p['ink']}; font-family: Inter; position: relative; }}
body.bold {{ background: {p['maroon']}; color: {p['cream']}; }}
.serif {{ font-family: Fraunces; font-variation-settings: 'WONK' 0, 'SOFT' 50; font-weight: 500; line-height: 1.12; }}
.serif em {{ font-style: italic; color: {p['rust']}; }}
.mark {{ position: absolute; top: 44px; right: 56px; font-size: 22px; color: {p['clay']}; border: 2px solid {p['blush']};
        border-radius: 999px; padding: 6px 18px; letter-spacing: 0.02em; }}
body.bold .mark {{ color: {p['blush']}; border-color: {p['clay']}; }}
.content {{ position: absolute; top: 150px; bottom: 170px; left: 96px; right: 96px; overflow: hidden;
           display: flex; flex-direction: column; justify-content: center; gap: 36px; }}
.foot {{ position: absolute; bottom: 60px; left: 96px; right: 96px; display: flex; justify-content: space-between;
        align-items: baseline; font-size: 24px; color: {p['clay']}; }}
.wordmark {{ font-family: Fraunces; font-variation-settings: 'WONK' 0, 'SOFT' 50; font-size: 44px; color: {p['rust']}; }}
body.bold .wordmark {{ color: {p['cream']}; }}
.rule {{ width: 120px; height: 4px; background: {p['blush']}; }}
.body {{ line-height: 1.32; }}
.card {{ background: {p['paper']}; border-left: 6px solid {p['rust']}; padding: 32px 36px; border-radius: 6px; }}
.label {{ font-size: 24px; color: {p['clay']}; margin-top: 14px; }}
.key {{ font-family: Fraunces; font-variation-settings: 'WONK' 0, 'SOFT' 50; color: {p['rust']}; line-height: 1.0; }}
.keylabel {{ color: {p['ink']}; line-height: 1.25; margin-top: 10px; }}
.cta {{ color: {p['rust']}; }}
.brand {{ font-style: italic; }}
.signoff {{ color: {p['rust']}; font-weight: 600; }}
.sources h2 {{ font-family: Fraunces; font-variation-settings: 'WONK' 0, 'SOFT' 50; font-weight: 500; font-size: 56px; }}
.entry {{ line-height: 1.3; border-top: 2px solid {p['blush']}; padding-top: 20px; }}
.foot-note {{ line-height: 1.3; color: {p['clay']}; }}
.doi {{ white-space: nowrap; }}
.srcline {{ line-height: 1.3; color: {p['clay']}; border-top: 2px solid {p['blush']}; padding-top: 18px; }}
"""


def _fit(kind: str, cls: str, inner: str, tag: str = "div") -> str:
    floor_kind = {"hook": "headline", "serif": "headline", "key": "headline"}.get(kind, kind)
    return (f'<{tag} class="{cls}" data-kind="{floor_kind}" data-ladder="{",".join(map(str, LADDERS[kind]))}">'
            f"{inner}</{tag}>")


JOURNALS = {  # display names for NLM catalogue titles (the stored citation keeps the catalogue form)
    "BMJ (Clinical research ed.)": "BMJ",
    "BMJ open diabetes research & care": "BMJ Open Diabetes Research & Care",
    "International journal of obesity (2005)": "International Journal of Obesity",
}


def journal_display(name: str | None) -> str:
    """The journal as readers know it: a mapped name, else the catalogue title without its trailing qualifier
    in brackets ("(2005)", "(Clinical research ed.)")."""
    if not name:
        return ""
    return JOURNALS.get(name) or re.sub(r"\s*\([^)]*\)\s*$", "", name)


UNIT = re.compile(r"(\d) (?=\d{3}\b|(?:kg|g|cm|mg|mmol|µ|%|times|years|months|weeks)\b)")  # also "12 983"


SQUARED = re.compile(r"/m2\b")


def _esc(text: str) -> str:
    """Escape for HTML. Display-only transforms (the checked text and the trace keep the original): a number stays on
    the line of its unit, and "kg/m2" is shown as "kg/m²"."""
    return html.escape(SQUARED.sub("/m²", UNIT.sub("\\1\u00a0", text)))


def _emph(text: str, part: str | None) -> str:
    if part and part in text:
        a, b = text.split(part, 1)
        return f"{_esc(a)}<em>{_esc(part)}</em>{_esc(b)}"
    return _esc(text)


def short_source(src: dict) -> str:
    surname = (src.get("authors") or "").split(",")[0].split(" ")[0] or "Source"
    return f"{surname} et al. {src.get('year', '')}, {src.get('location', '')}"


def merge_locations(locations: list[str]) -> str:
    """"page 2" + "page 5, 6" -> "pages 2, 5 and 6"; anything that is not a page list is kept as it is."""
    pages = sorted({int(n) for loc in locations if loc.startswith("page ") for n in re.findall(r"\d+", loc)})
    other = sorted({loc for loc in locations if not loc.startswith("page ")})
    parts = []
    if pages:
        nums = [str(n) for n in pages]
        parts.append(f"page {nums[0]}" if len(nums) == 1 else f"pages {', '.join(nums[:-1])} and {nums[-1]}")
    return "; ".join(parts + other)


def _lines_html(lines: list[dict], unit: dict, by_claim: dict, hook: bool) -> str:
    out = []
    if unit.get("key_number") and unit.get("key_label"):
        out.append('<div>' + _fit("key", "key", _esc(unit["key_number"]))
                   + _fit("body", "keylabel", _esc(unit["key_label"])) + "</div>")
    first_framing = True
    for ln in lines:
        k, t = ln["kind"], ln["text"]
        if k == "health":
            labels = [short_source(e) for e in source_entries([by_claim[i] for i in ln["claim_ids"] if i in by_claim])]
            out.append('<div class="card">' + _fit("body", "body", _esc(t))
                       + "".join(f'<div class="label">{html.escape(x)}</div>' for x in labels) + "</div>")
        elif k == "framing":
            big = hook and first_framing
            out.append(_fit("hook" if big else "serif", "serif", _emph(t, unit.get("emphasis"))))
            if big:
                out.append('<div class="rule"></div>')
            first_framing = False
        elif k == "brand":
            out.append(_fit("serif", "serif brand", html.escape(t)))
        elif k == "cta":
            out.append(_fit("serif", "serif cta", html.escape(t)))
        else:  # signoff
            out.append(_fit("body", "signoff", html.escape(t)))
    return "".join(out)


def _page(p: dict, content: str, n: int, total: int, bold: bool = False, extra_cls: str = "") -> str:
    number = f"{n}/{total}" if total > 1 else ""
    return (f'<!doctype html><html><head><meta charset="utf-8"><style>{_css(p)}</style></head>'
            f'<body class="{"bold" if bold else ""}"><div class="mark">{DRAFT}</div>'
            f'<div class="content {extra_cls}">{content}</div>'
            f'<div class="foot"><span class="wordmark">steer</span><span>{number}</span></div></body></html>')


def source_entries(sources: list[dict]) -> list[dict]:
    """One entry per paper (DOI), its locations merged; each lists the claims it covers."""
    entries: dict[str, dict] = {}
    for s in sources:
        e = entries.setdefault(s.get("doi") or s.get("title"), s | {"claim_ids": [], "locations": []})
        e["claim_ids"].append(s["claim_id"])
        e["locations"].append(s.get("location") or "")
    for e in entries.values():
        e["location"] = merge_locations(e["locations"])
    return list(entries.values())


def _entry_html(e: dict) -> str:
    esc = lambda k: html.escape(str(e.get(k) or ""))
    text = (f"{esc('authors')} {esc('title')}. <i>{html.escape(journal_display(e.get('journal')))}</i>, {esc('year')}. <span class=\"doi\">doi:{esc('doi')}</span>. "
            f"{html.escape(str(e.get('location') or '').capitalize())}.")
    return _fit("sources", "entry", text)


def build_pages(record: dict, brand: dict, draft: bool = True) -> list[dict]:
    """[{name, html, words, kind, claim_ids}] for the post's artwork, sources slides included."""
    p, post = brand["palette"], record["post"]
    by_claim = {s["claim_id"]: s for s in record["sources"]}
    entries = source_entries(record["sources"])
    pages = []
    if post.get("slides"):
        chunks = [entries[i:i + SOURCES_PER_SLIDE] for i in range(0, len(entries), SOURCES_PER_SLIDE)] or [[]]
        total = len(post["slides"]) + len(chunks)
        for i, sl in enumerate(post["slides"]):
            pages.append({"name": f"slide-{i + 1:02}.png", "kind": "slide",
                          "words": sum(len(ln["text"].split()) for ln in sl["lines"]),
                          "claim_ids": sorted({c for ln in sl["lines"] for c in ln["claim_ids"]}),
                          "art": _art(sl["lines"], sl, sl["role"] == "brand"),
                          "html": _page(p, _lines_html(sl["lines"], sl, by_claim, sl["role"] == "hook"), i + 1, total,
                                        bold=sl["role"] == "brand")})
        for j, chunk in enumerate(chunks):
            n = len(post["slides"]) + j + 1
            content = ('<h2>Sources</h2>' + "".join(_entry_html(e) for e in chunk)
                       + _fit("sources", "foot-note", html.escape(FOOTNOTE)))
            pages.append({"name": f"slide-{n:02}.png", "kind": "sources", "words": 0, "entries": len(chunk),
                          "claim_ids": sorted({c for e in chunk for c in e["claim_ids"]}),
                          "html": _page(p, content, n, total, extra_cls="sources")})
    if post.get("static"):
        st = post["static"]
        lines = [st["headline"], *st["supporting"]]
        src = "; ".join(f'{html.escape(short_source(e))}, <span class="doi">doi:{html.escape(str(e.get("doi", "")))}</span>'
                        for e in entries)
        content = (_lines_html(lines, st, by_claim, hook=st["headline"]["kind"] == "framing")
                   + _fit("sources", "srcline", "Source: " + src))
        pages.append({"name": "static.png", "kind": "static", "words": sum(len(ln["text"].split()) for ln in lines),
                      "claim_ids": sorted({c for e in entries for c in e["claim_ids"]}), "art": _art(lines, st, False),
                      "html": _page(p, content, 1, 1)})
    if not draft:   # the publishable re-render: no draft mark, the reviewed footnote
        for pg in pages:
            pg["html"] = pg["html"].replace(f'<div class="mark">{DRAFT}</div>', "").replace(
                html.escape(FOOTNOTE), html.escape(PUBLISHED_FOOTNOTE))
    return pages


def png_size(path: Path) -> tuple[int, int]:
    head = path.read_bytes()[:24]
    if head[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError(f"{path} is not a PNG")
    return struct.unpack(">II", head[16:24])


def render(record: dict, brand: dict, out_dir: Path, draft: bool = True) -> dict:
    """Write the PNGs and render_qa.json into out_dir; returns {status: passed|failed, pages: [...]}."""
    from playwright.sync_api import sync_playwright

    out_dir.mkdir(parents=True, exist_ok=True)
    pages = build_pages(record, brand, draft)
    used = sorted({i for sl in record["post"].get("slides") or [] for ln in sl["lines"] for i in ln["claim_ids"]}
                  | {i for ln in ([record["post"]["static"]["headline"], *record["post"]["static"]["supporting"]]
                                  if record["post"].get("static") else []) for i in ln["claim_ids"]})
    listed = {c for pg in pages if pg["kind"] in ("sources", "static") for c in pg["claim_ids"]}
    brief = record.get("brief") or {}
    art = lineart.choose([pg["art"] if "art" in pg else {"texts": [], "has_health": False, "eligible": False} for pg in pages],
                         brief.get("theme") or "", brief.get("doubt") or "")
    report = []
    with tempfile.TemporaryDirectory(prefix="steer-render-") as tmp, sync_playwright() as pw:
        tmp = Path(tmp)
        for f in ("Fraunces.ttf", "Fraunces-Italic.ttf", "Inter.ttf"):
            (tmp / f).symlink_to(FONTS / f)
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": W, "height": H}, device_scale_factor=1)
        for pg in pages:
            src = tmp / (pg["name"] + ".html")
            src.write_text(pg["html"], encoding="utf-8")
            page.goto(src.as_uri())
            page.evaluate("document.fonts.ready")
            fit = page.evaluate(FIT_JS)
            png = out_dir / pg["name"]
            fails, drawn = [], None
            if pages.index(pg) in art:
                id_, boxes = art[pages.index(pg)], page.evaluate(TEXT_BOXES_JS)
                d = lineart.drawing(id_)
                x0, y0, x1, y1 = lineart.bbox(d)
                box = lineart.place((y1 - y0) / (x1 - x0), boxes, W, H)
                if box:
                    pal = brand["palette"]; ink = pal["cream"] if pg["art"]["bold"] else pal["ink"]
                    art_png = tmp / f"{pg['name']}.lineart.png"
                    lineart.draw_png(pw, [(id_, ink, round(box[2] - box[0]), art_png)])
                    pad = 12   # the PNG's margin around the drawing's own bounding box (lineart.draw_png)
                    page.evaluate(ADD_ART_JS, [art_png.as_uri(), [box[0] - pad, box[1] - pad, box[2] + pad, box[3] + pad]])
                    drawn = {"id": id_, "box": box, "ink": ink}
                    if lineart.overlaps(box, page.evaluate(TEXT_BOXES_JS)):
                        fails.append("LINEART_OVER_TEXT")
                    if next(e for e in lineart.index() if e["id"] == id_)["food"] and not lineart.food_allowed(
                            pg["art"]["texts"], pg["art"]["has_health"]):
                        fails.append("LINEART_FOOD_ON_HEALTH")
            page.screenshot(path=str(png))
            if fit["overflow"]:
                fails.append("TEXT_OVERFLOW")
            for kind, floor in FLOORS.items():
                sizes = [b["px"] for b in fit["boxes"] if b["kind"] == kind]
                if sizes and min(sizes) < floor:
                    fails.append(f"{kind.upper()}_BELOW_{floor}PX")
            if pg["kind"] != "sources" and pg["words"] > WORD_BUDGET:
                fails.append("TOO_MANY_WORDS")
            if pg["kind"] == "sources" and pg["entries"] > SOURCES_PER_SLIDE:
                fails.append("TOO_MANY_SOURCES")
            if png_size(png) != (W, H):
                fails.append("WRONG_PNG_SIZE")
            px = lambda k: min((b["px"] for b in fit["boxes"] if b["kind"] == k), default=None)
            report.append({"png": pg["name"], "kind": pg["kind"], "words": pg["words"], "min_body_px": px("body"),
                           "min_sources_px": px("sources"), "min_headline_px": px("headline"),
                           "fit_step": fit["step"], "overflow": fit["overflow"], "size": list(png_size(png)),
                           "lineart": drawn, "failures": fails})
        browser.close()
    missing = [i for i in used if i not in listed]
    result = {"status": "failed" if missing or any(r["failures"] for r in report) else "passed",
              "missing_sources": missing, "pages": report}
    (out_dir / "render_qa.json").write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    return result


def qa_line(r: dict) -> str:
    return (f"{r['png']}: {r['kind']}, {r['words']} words, smallest body {r['min_body_px']} px, smallest sources "
            f"{r['min_sources_px']} px, overflow {'yes' if r['overflow'] else 'no'}, {r['size'][0]}x{r['size'][1]}, "
            f"{'PASS' if not r['failures'] else 'FAIL ' + ','.join(r['failures'])}")
