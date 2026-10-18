"""Line-art decoration for slides: chosen and placed by code, never by a model, and never part of the checked text.

choose():  a drawing per slide from lineart/index.json topic_tags matched to the post's theme, doubt and the slide's
           own words. A food drawing (index food=true) never goes on a slide with a health line or any number.
place():   a box at the slide's bottom or side (it may bleed off the edge) that touches none of the laid-out text boxes.
overlaps(): the render-QA check of the final box against the same text boxes (LINEART_OVER_TEXT).
draw_png(): the drawing as a transparent single-colour PNG (p5.brush ink, headless Chromium with software WebGL)."""

import json
import re
from functools import lru_cache
from pathlib import Path

CODE = Path(__file__).resolve().parents[2]
LA = CODE / "lineart"
MAX_PER_POST = 2
WIDTHS = (520, 440, 370, 310, 260)   # displayed width of the drawing's own bounding box, largest first
PAD = 30                             # clear space between the drawing and any text box
BLEED = 0.15                         # at most this share of the drawing may run off the slide edge
STEP = 20


@lru_cache(maxsize=1)
def index() -> list[dict]:
    return json.loads((LA / "index.json").read_text())


def drawing(id_: str) -> dict:
    return json.loads((LA / f"{id_}.json").read_text())


def bbox(d: dict) -> tuple[float, float, float, float]:
    xs = [x for p in d["paths"] for x, _ in p]
    ys = [y for p in d["paths"] for _, y in p]
    return min(xs), min(ys), max(xs), max(ys)


def _words(text: str) -> str:
    return " " + " ".join(re.findall(r"[a-z]+", text.lower())) + " "


def food_allowed(texts: list[str], has_health: bool) -> bool:
    """A food drawing next to a health line or a number would imply a cause: it goes only on number-free, health-free slides."""
    return not has_health and not any(re.search(r"\d", t) for t in texts)


def score(entry: dict, theme_words: str, slide_text: str) -> int:
    """Tag hits: a tag counts once per source (the post's theme/doubt, the slide's words); a slide hit counts double."""
    hit = lambda tag, hay: re.search(r"\b" + re.escape(tag.lower()) + r"\w*\b", hay) is not None
    return sum((1 if hit(t, theme_words) else 0) + (2 if hit(t, slide_text) else 0) for t in entry["topic_tags"])


def choose(slides: list[dict], theme: str = "", doubt: str = "") -> dict[int, str]:
    """{slide index: drawing id}. slides: [{"texts": [...], "has_health": bool, "eligible": bool}]. Deterministic:
    best score (> 0), ties by id, each drawing at most once per post, at most MAX_PER_POST drawings."""
    theme_words = _words(f"{theme} {doubt}")
    out: dict[int, str] = {}
    for i, sl in enumerate(slides):
        if not sl["eligible"] or len(out) >= MAX_PER_POST:
            continue
        text = _words(" ".join(sl["texts"]))
        ok = [e for e in index() if (not e["food"] or food_allowed(sl["texts"], sl["has_health"])) and e["id"] not in out.values()]
        ranked = sorted(((-score(e, theme_words, text), e["id"]) for e in ok))
        if ranked and ranked[0][0] < 0:
            out[i] = ranked[0][1]
    return out


def _hits(a, b, pad=0) -> bool:
    return a[0] < b[2] + pad and b[0] < a[2] + pad and a[1] < b[3] + pad and b[1] < a[3] + pad


def overlaps(box, text_boxes, pad=0) -> list:
    """The text boxes ([x0, y0, x1, y1]) the drawing's box touches (within pad)."""
    return [t for t in text_boxes if _hits(box, t, pad)]


def place(aspect: float, text_boxes: list, w: int = 1080, h: int = 1350) -> list[float] | None:
    """The drawing's box [x0, y0, x1, y1] on the slide, or None when nothing fits. Largest size first; among the
    positions that clear every text box by PAD, the one nearest a slide edge (bottom or side), lowest first."""
    for width in WIDTHS:
        height = width * aspect
        best = None
        x = -width * BLEED
        while x <= w - width * (1 - BLEED):
            y = 0.0
            while y <= h - height * (1 - BLEED):
                box = [x, y, x + width, y + height]
                if not overlaps(box, text_boxes, PAD):
                    edge = min(max(x, 0), max(w - box[2], 0), max(h - box[3], 0))
                    key = (edge, -y, x)
                    if best is None or key < best[0]:
                        best = (key, box)
                y += STEP
            x += STEP
        if best:
            return [round(v, 1) for v in best[1]]
    return None


PAGE = """<!doctype html><meta charset=utf-8><body style="margin:0">
<script src="{brush}"></script><script src="{draw}"></script>
<script>brush.createCanvas({w}, {h}, {{parent: document.body, pixelDensity: 1}}); brush.scaleBrushes(2);
window.go = (d, ink) => {{ window.INK_WEIGHT = {weight}; drawChai(brush, d, 1, {w}, {h}, '#000000', '#ffffff'); }}</script>"""

# p5.brush mixes pigment, so it draws black on white; darkness becomes the alpha of the ink colour (one colour).
ALPHA_JS = """(ink) => { const s = document.querySelector('canvas'), c = document.createElement('canvas');
  c.width = s.width; c.height = s.height; const x = c.getContext('2d'); x.drawImage(s, 0, 0);
  const d = x.getImageData(0, 0, c.width, c.height), h = [1, 3, 5].map(i => parseInt(ink.slice(i, i + 2), 16));
  for (let i = 0; i < d.data.length; i += 4) { const a = 255 - d.data[i]; d.data[i] = h[0]; d.data[i + 1] = h[1]; d.data[i + 2] = h[2]; d.data[i + 3] = a; }
  x.putImageData(d, 0, 0); return c.toDataURL('image/png'); }"""


def draw_png(pw, requests: list[tuple[str, str, int, Path]]) -> None:
    """Render [(drawing id, ink colour '#rrggbb', displayed width px of its bounding box, out path)] as transparent PNGs."""
    import base64
    import tempfile

    with tempfile.TemporaryDirectory(prefix="steer-lineart-") as tmp:   # pw: a running sync_playwright() (render.py's)
        b = pw.chromium.launch(args=["--use-angle=swiftshader", "--enable-unsafe-swiftshader"])
        for id_, ink, width, out in requests:
            d = drawing(id_)
            x0, y0, x1, y1 = bbox(d)
            s = width / (x1 - x0)
            pad = 12                                           # px of margin so the brush is not clipped
            cw, ch = round(width + 2 * pad), round((y1 - y0) * s + 2 * pad)
            box = min(cw, ch) / s                              # drawChai scales by min(W, H) / box and centres the box
            ox, oy = (cw / s - box) / 2, (ch / s - box) / 2    # put the bounding box's top-left at (pad, pad)
            shifted = {"box": box, "paths": [[[x - x0 + pad / s - ox, y - y0 + pad / s - oy] for x, y in p] for p in d["paths"]]}
            html = Path(tmp) / "p.html"
            html.write_text(PAGE.format(brush=(CODE / "vendor/p5brush/brush.js").as_uri(), draw=(LA / "draw.js").as_uri(),
                                        w=cw, h=ch, weight=6 * 1.08 / s))
            pg = b.new_page(viewport={"width": cw, "height": ch})
            pg.goto(html.as_uri())
            pg.evaluate("([d, i]) => go(d, i)", [shifted, ink])
            out.write_bytes(base64.b64decode(pg.evaluate(ALPHA_JS, ink).split(",", 1)[1]))
            pg.close()
        b.close()
