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


# === posts_page: the finished posts, each health line with the records behind it (steer site-pages posts) ============

POSTS_CSS = """<style>.imgs{display:flex;gap:8px;overflow-x:auto}.imgs img{height:220px;max-width:none}
details{background:#fff;border:1px solid #E6D2C6;border-radius:8px;padding:8px 14px;margin:8px 0}summary{cursor:pointer}
details dl{display:grid;grid-template-columns:11em 1fr;gap:4px 14px;margin:10px 0}details dt{color:#7A5A50;font-size:14px}details dd{margin:0}
blockquote{margin:0;padding-left:12px;border-left:3px solid #D9B8A8}.blocked{border-left:4px solid #9b2c2c;padding-left:10px}
@media(max-width:700px){details dl{grid-template-columns:1fr}details dt{margin-top:6px}.imgs img{height:160px}}</style>"""


def _where(path: str) -> str:
    p = path.split("/")
    if p[0] == "slides":
        return f"slide {int(p[1]) + 1}"
    return {"static": "static image", "caption": "caption", "reel_script": "reel"}.get(p[0], p[0])


def _health(folder: Path) -> list[dict]:
    """The distinct health lines of a post's trace, each with where it appears, its claims and its cross-check(s)."""
    out: dict[str, dict] = {}
    for ln in json.loads((folder / "trace.json").read_text())["lines"]:
        if ln["kind"] != "health":
            continue
        h = out.setdefault(ln["text"], {"text": ln["text"], "where": [], "claims": ln.get("claims") or [], "checks": []})
        if _where(ln["path"]) not in h["where"]:
            h["where"].append(_where(ln["path"]))
        cc = ln.get("crosscheck") or {}
        key = (cc.get("verdict"), cc.get("model"), cc.get("independence"))
        if key not in [(c.get("verdict"), c.get("model"), c.get("independence")) for c in h["checks"]]:
            h["checks"].append(cc)
    return list(out.values())


def _line_block(h: dict, paper: str, status_of: dict, loc_of: dict) -> str:
    rows = [f"<dt>Used in</dt><dd>{_E(', '.join(h['where']))}</dd>"]
    for c in h["claims"]:
        cid = c["claim_id"]
        rows.append(f'<dt>Claim</dt><dd><a href="../studio/ledger.html#claim-{_E(paper)}-{_E(cid)}">{_E(paper)}/{_E(cid)}</a></dd>')
        rows.append(f"<dt>Exact quote</dt><dd><blockquote>{_E(c['quote'])}</blockquote></dd>")
        rows.append(f"<dt>Where in the paper</dt><dd>{_E(loc_of.get(cid, 'not stated'))}</dd>")
        rows.append(f"<dt>Gate result</dt><dd>{_E(status_of.get(f'{paper}/{cid}', 'not in the store'))}</dd>")
    for cc in h["checks"]:
        rows.append("<dt>Independent cross-check</dt><dd>" + _E(f"{cc.get('verdict', 'not run')}; model {cc.get('model', 'n/a')}; "
                    f"{(cc.get('independence') or 'n/a').replace('_', ' ')}") + (f". {_E(cc['reason'])}" if cc.get("reason") else "") + "</dd>")
    rows.append("<dt>Review</dt><dd>pending doctor review</dd>")
    return f"<details><summary>{_E(h['text'])}</summary><dl>{''.join(rows)}</dl></details>"


def _blocked(folder: Path) -> str:
    rec, tr = json.loads((folder / "post.json").read_text()), json.loads((folder / "trace.json").read_text())
    bad = [ln for ln in tr["lines"] if ln.get("failures")]
    items = "".join(f"<details open><summary>{_E(ln['kind'])}: {_E(ln['text'])}</summary><dl><dt>Failed checks</dt><dd>"
                    + _E("; ".join(f"{f['code']} ({f.get('detail', '')})" for f in ln["failures"])) + "</dd><dt>Claims</dt><dd>"
                    + _E(", ".join(ln.get("claim_ids") or []) or "none") + "</dd></dl></details>" for ln in bad)
    return (f'<section id="{_E(rec["post_id"])}-blocked"><h2>A post the checks blocked</h2><p class="small">{_E(rec["post_id"])} · '
            f"status {_E(rec['status'])}. This is what a refusal looks like: the lines below failed, so the post was "
            f"<strong>never rendered and never published</strong>.</p><div class=\"blocked\">{items}</div></section>")


def posts_page(db, post_dirs: list[Path], reviewers_path, order: list[str] = (), notes: dict[str, str] | None = None,
               blocked_dir: Path | None = None) -> str:
    """posts/index.html: the given posts in `order`, each with its images, reel, captions and, per health line, a <details>
    block built only from the stored records (post.json, trace.json, the studio's own claim status)."""
    from . import studio

    snap = studio.public_data(db, post_dirs, reviewers_path)
    status_of = {c["key"]: c["status"] for c in snap["claims"]}
    mine = {p["id"]: p for p in snap["posts"] if any(Path(p["folder"]) == pj.parent for d in post_dirs
                                                      for pj in Path(d).glob("**/post.json"))}
    ids = [i for i in order if i in mine] + [i for i in mine if i not in order]
    body = []
    for pid in ids:
        p, folder = mine[pid], Path(mine[pid]["folder"])
        short = snap["papers"][p["paper"]]["short"]
        kind = ("carousel + reel (v5)" if p["has_reel"] else "carousel") if p["format"] == "carousel" else "static"
        title = f"{short} — {kind}" + (f", {notes[pid]}" if notes and pid in notes else "")
        loc_of = {s["claim_id"]: s["location"] for s in json.loads((folder / "post.json").read_text()).get("sources", [])}
        imgs = "".join(f'<a href="../studio/{_E(u)}"><img src="../studio/{_E(u)}" alt="{_E(Path(u).name)}" loading="lazy"></a>' for u in p["pngs"])
        claims = sorted({i for ln in p["lines"] if ln["kind"] == "health" for i in ln["claim_ids"]})
        body.append(
            f'<section id="{_E(pid)}"><h2>{_E(title)}</h2><p class="small">{_E(pid)} · draft, pending doctor review</p>'
            f'<div class="imgs">{imgs}</div>'
            + (f'<p><a href="../studio/files/{_E(pid)}/reel.mp4">Watch the reel (MP4)</a></p>' if p["has_reel"] else "")
            + f'<p><a href="{_E(pid)}/caption.txt">Caption (English)</a> · <a href="{_E(pid)}/caption-hinglish.txt">Caption (Hinglish)</a>'
            f' · <a href="../studio/ledger.html">Its claims in the ledger</a>: {_E(", ".join(claims))}</p>'
            f"<h3>Why this claim? Every health line, with the records behind it</h3>"
            + "".join(_line_block(h, p["paper"], status_of, loc_of) for h in _health(folder)) + "</section>")
    if blocked_dir:
        body.append(_blocked(Path(blocked_dir)))
    return _page("Finished posts", POSTS_CSS + "".join(body), "The final drafts, with every health line opened up to the quote, the page, the "
                 "gate result and the independent cross-check behind it.", font_base="../ps1/fonts/", back="../index.html")
