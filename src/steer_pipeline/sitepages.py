"""Static pages for the public site, generated from stored records only (no hand-written content): "What the pipeline
learned" (learned_page) and, below, "Why this claim?" on the posts page (posts_page). Every sentence comes from a record or
is the fixed chrome below; commit hashes are shown short. Plain HTML + CSS, no network, stacked below 700 px."""
import html
import json
import re
import shutil
from pathlib import Path

DISCLAIMER = ("Checked by code against the paper and by an independent model. Not clinical approval: every post is pending "
              "review by a Steer doctor.")
FONTS = """@font-face{font-family:Fraunces;src:url("{base}Fraunces.woff2") format("woff2")}
@font-face{font-family:Inter;src:url("{base}Inter.woff2") format("woff2")}
h1,h2{font-family:Fraunces,Georgia,serif;font-weight:500}"""   # the site's own font files (ps1/fonts), no network
CSS = """body{overflow-wrap:anywhere;font:17px/1.5 Inter,system-ui,sans-serif;max-width:1000px;margin:32px auto;padding:0 16px;background:#F5EBDF;color:#351D1D}
a{color:#C04E2A}.note{background:#F8F2EB;border-left:4px solid #C04E2A;padding:10px 14px}
.card{background:#F8F2EB;border-radius:10px;padding:16px 18px;margin:18px 0}.pair{display:grid;grid-template-columns:1fr 1fr;gap:14px}
.fail{border-left:4px solid #9b2c2c;padding-left:10px}.pass{border-left:4px solid #2f6b3a;padding-left:10px}
.code{overflow-wrap:anywhere;font-family:ui-monospace,monospace;font-size:14px;background:#efe3d6;padding:1px 6px;border-radius:4px}
.small{color:#7A5A50;font-size:14px}img{max-width:100%;border-radius:8px}table{border-collapse:collapse}td,th{padding:4px 10px;text-align:left}
@media (max-width:700px){.pair{grid-template-columns:1fr}}"""
LEARNED_INTRO = "Each card is a real failure from a stored run, the rule it led to, and the line that passed afterwards."
_E = html.escape
_LEAK = re.compile(r"trials/|/home/|/tmp/|\b[0-9a-f]{12,40}\b")


def _page(title: str, body: str, intro: str = "", font_base: str = "ps1/fonts/", back: str = "index.html") -> str:
    style = FONTS.replace("{base}", font_base) + CSS
    lead = f"<p>{_E(intro)}</p>" if intro else ""
    out = (f'<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">'
           f"<title>{_E(title)}</title><style>{style}</style><h1>{_E(title)}</h1>{lead}<p class=\"note\">{_E(DISCLAIMER)}</p>"
           f'{body}<p><a href="{back}">Back</a></p>')
    leak = _LEAK.search(out)
    if leak:   # an internal path or a full hash in the public HTML fails the build
        raise ValueError(f"internal reference in the generated page: {leak.group(0)!r}")
    return out


def _codes(codes: list[dict]) -> str:
    return "".join(f'<p><span class="code">{_E(c["code"])}</span> {_E(str(c.get("detail") or ""))}</p>' for c in codes)


def _rule(r: dict) -> str:
    sha = f'<span class="code">{_E(r["sha"][:7])}</span> ' if r.get("sha") else ""
    b = f' · {_E(r["boundaries"])}' if r.get("boundaries") else ""
    return f'<p class="small">Rule added: {sha}{_E(r["subject"])}{b}</p>'


def _xc(x: dict | None) -> str:
    x = x or {}
    if not x.get("verdict"):
        return ""
    return f'<p class="small">Cross-check: {_E(x["verdict"])} ({_E(str(x.get("model") or ""))}, {_E(str(x.get("independence") or ""))})</p>'


def _card(c: dict, img_dir: str) -> str:
    f, p, kind = c["failed"], c["passed"], c.get("kind", "line")
    src = {"re-check": "re-checked with today's rule on the stored line", "review": "found in review",
           "stored": "stored failure"}.get(f.get("source"), "")
    if kind == "learn":
        rows = "".join("<tr>" + "".join(f"<td>{_E(x)}</td>" for x in r) + "</tr>" for r in c["score_rows"])
        return (f'<section class="card"><h2>The learning loop</h2><p class="small">{_E(c.get("label", ""))}</p>'
                f'<div style="overflow-x:auto"><table><tr><th>doubt</th><th>k</th><th>n</th><th>c</th><th>m</th><th>flags</th><th>lines</th><th>f</th><th>opp</th></tr>'
                f'{rows}</table></div><p>{_E(c["why"])}</p><p class="small">Brief change: <span class="code">{_E(c["added"])}</span></p>'
                f'<div class="pass"><p>The post it produced ({_E(p["post"])}): “{_E(p["text"])}”</p></div>{_rule(c["rule"])}</section>')
    if kind == "image":
        before = f'<img src="{img_dir}/{_E(f["image"])}" alt="before">'
        after = f'<img src="{img_dir}/{_E(p["image"])}" alt="after"><p>{_E(p["text"])}</p>'
    elif kind == "audio":
        before = f'<p>Heard: “{_E(f["text"])}”</p>'
        after = f'<p>{_E(p["text"])}</p>'
    else:
        before = f'<p>“{_E(f["text"])}”</p>'
        after = f'<p>“{_E(p["text"])}”</p>{_xc(p.get("crosscheck"))}'
    return (f'<section class="card"><div class="pair"><div class="fail"><p class="small">Blocked ({_E(src)})</p>{before}'
            f'{_codes(f["codes"])}</div><div class="pass"><p class="small">Passed after the rule</p>{after}</div></div>'
            f"{_rule(c['rule'])}</section>")


def learned_page(records: Path, out: Path) -> Path:
    """learned.html (+ its images in learned/) from a records folder holding lessons.json and the images it names."""
    cards = json.loads((records / "lessons.json").read_text())
    (out / "learned").mkdir(parents=True, exist_ok=True)
    for c in cards:
        for side in ("failed", "passed"):
            if c[side].get("image"):
                shutil.copy(records / c[side]["image"], out / "learned" / c[side]["image"])
    body = "".join(_card(c, "learned") for c in cards)
    page = out / "learned.html"
    page.write_text(_page("What the pipeline learned", body, LEARNED_INTRO), encoding="utf-8")
    return page
