"""`steer write`: from a stored run, pick a brief, write a post, check every line, save post + trace.

Writes out/<run_id>/<post_id>/: post.json (message, reasoning, brief, lines with kinds and claim ids, status),
caption.txt, reel_script.json and trace.json (every line -> claim ids -> quote -> sids/pages -> every check
result, with the checking model and its independence). Nothing here approves a post.
"""

import json
import tomllib
import uuid
from datetime import datetime, timezone
from pathlib import Path

from . import store
from .checks import check_post, load_boundaries, required_claims, study_context, writable
from .render import journal_display
from .router import Router
from .write import pick_brief, write_post

BRAND_PATH = Path(__file__).resolve().parents[2] / "brand.toml"


def load_brand(path: Path = BRAND_PATH) -> dict:
    return tomllib.loads(path.read_text())


def claims_from_store(con, run_id: str) -> tuple[dict, dict]:
    """The run's kept claims as dicts (with their latest library cross-check) and the document row."""
    from .studio import fully_cleared

    data = store.export_run(con, run_id)
    acts = store.actions(con)
    latest = {}
    for c in data["crosschecks"]:
        latest[c["claim_id"]] = c
    claims = {}
    for c in data["claims"]:
        if c["status"] == "rejected":
            continue
        c["gate"] = c["gate"] if isinstance(c["gate"], dict) else json.loads(c["gate"])
        c["crosscheck"] = latest.get(c["claim_id"])
        c["labels"] = {"theme": c.get("theme"), "doubts": c.get("doubts") or []}
        c["s4_cleared"] = fully_cleared(c | {"run_id": run_id}, acts)   # cleared by a named reviewer in the studio
        claims[c["claim_id"]] = c
    doc = json.loads(con.execute("SELECT document FROM documents WHERE document_id = ?",
                                 (data["run"]["document_id"],)).fetchone()[0])
    return claims, doc


def sources(claim_ids: list[str], claims: dict, doc: dict) -> list[dict]:
    """Code-built sources: never model-written, never an invented page."""
    cit = doc["citation"]
    authors = cit.get("authors") or []
    who = ", ".join(authors[:3]) + (" et al." if len(authors) > 3 else "")
    no_layout = any(w["code"] == "NO_PAGE_LAYOUT" for w in doc["warnings"])
    out = []
    for i in claim_ids:
        sids = claims[i]["sids"]
        location = "section text, no page layout" if no_layout else \
            "page " + ", ".join(str(int(s[1:5])) for s in sorted(set(x[:5] for x in sids)))
        out.append({"claim_id": i, "authors": who, "title": cit.get("title"), "journal": cit.get("journal"),
                    "year": cit.get("year"), "doi": cit.get("doi"), "location": location, "sids": sids})
    return out


def write_and_check(con, run_id: str, fmt: str, settings: dict, router: Router | None = None,
                    brief: dict | None = None, out_dir: str | Path = "out", render: bool = True) -> dict:
    router = router or Router(settings)
    claims, doc = claims_from_store(con, run_id)
    boundaries = load_boundaries(doc["citation"].get("pmcid") or "")
    usable = [c for c in claims.values() if writable(c, boundaries)]
    brief = brief or pick_brief(usable, fmt, settings["writer"].get("brief_claims", 4))
    brand = load_brand()
    context = study_context(boundaries, doc)  # paper sentences behind the qualifiers: an error before any write call
    extra = required_claims(brief["claim_ids"], {c["claim_id"]: c for c in usable}, boundaries)
    if extra:  # a claim the paper's require_with rules make compulsory with the brief's claims
        brief = brief | {"claim_ids": brief["claim_ids"] + extra, "why": f"{brief['why']}; added {extra} for require_with"}
    hashtags = settings["writer"]["hashtags"]
    written = write_post(brief, usable, brand, boundaries, hashtags, router)
    result = check_post(written["post"], claims, brand, settings["rules"], boundaries, router, written["endpoint"], context)
    post_id = f"post-{uuid.uuid4().hex[:8]}"
    written_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    used = sorted({i for ln in result["lines"] for i in ln["claim_ids"]})
    srcs = sources(used, claims, doc)
    folder = Path(out_dir) / run_id / post_id
    folder.mkdir(parents=True, exist_ok=True)
    post = written["post"]
    record = {"post_id": post_id, "run_id": run_id, "written_at": written_at, "status": result["status"], "brief": brief,
              "message": post["message"], "reasoning": post["reasoning"], "format": post["format"],
              "writer": {"endpoint": written["endpoint"], "model": written["model"], "usage": written["usage"]},
              "post": post, "sources": srcs, "flags": result["flags"], "needs_layout_check": result["needs_layout_check"],
              "post_failures": result["post_failures"], "boundaries_version": boundaries.get("version"),
              "study_context": context}
    trace = {"post_id": post_id, "lines": [
        {"path": ln["path"], "kind": ln["kind"], "text": ln["text"], "claim_ids": ln["claim_ids"],
         "claims": [{"claim_id": i, "quote": claims[i]["quote"], "sids": claims[i]["sids"],
                     "line_ids": claims[i]["line_ids"]} for i in ln["claim_ids"] if i in claims],
         "failures": ln["failures"], "flags": ln.get("flags", []), "crosscheck": ln.get("crosscheck")}
        for ln in result["lines"]], "post_failures": result["post_failures"], "model_calls": router.log}
    (folder / "post.json").write_text(json.dumps(record, ensure_ascii=False, indent=1), encoding="utf-8")
    (folder / "trace.json").write_text(json.dumps(trace, ensure_ascii=False, indent=1), encoding="utf-8")
    (folder / "reel_script.json").write_text(json.dumps(post["reel_script"], ensure_ascii=False, indent=1), encoding="utf-8")
    caption = "\n".join(ln["text"] for ln in post["caption"]["lines"]) + "\n\nSources: " + "; ".join(
        f"{s['authors']} ({s['year']}) {journal_display(s['journal'])}, doi:{s['doi']}"
        for s in {s["doi"]: s for s in srcs}.values()) \
        + "\n\n" + " ".join(post["caption"]["hashtags"])
    (folder / "caption.txt").write_text(caption, encoding="utf-8")
    if render and record["status"] == "draft_pending_review":  # a blocked post gets no artwork
        record["render"] = render_post(folder, brand)
    store.save_post(con, record, folder, written_at)   # the studio's register of posts (post.json is the file of record)
    return record | {"folder": str(folder), "lines_checked": len(result["lines"])}


def render_post(folder: str | Path, brand: dict | None = None) -> dict:
    """Render a written post's PNGs into its folder and record the render QA in post.json."""
    from .render import render as draw

    folder = Path(folder)
    record = json.loads((folder / "post.json").read_text())
    qa = draw(record, brand or load_brand(), folder)
    record["render"] = {"status": qa["status"], "missing_sources": qa["missing_sources"],
                        "pngs": [r["png"] for r in qa["pages"]],
                        "lineart": {r["png"]: r["lineart"] for r in qa["pages"] if r.get("lineart")}}
    (folder / "post.json").write_text(json.dumps(record, ensure_ascii=False, indent=1), encoding="utf-8")
    return record["render"]


class ReelRefused(Exception):
    """The post may not become a reel; .reasons lists why."""

    def __init__(self, reasons: list[dict]):
        super().__init__("; ".join(r["code"] for r in reasons))
        self.reasons = reasons


def reel_post(folder: str | Path, con, settings: dict, tts=None, transcribe=None, engine: str = "remotion",
              presenter: dict | None = None, v4: dict | None = None) -> dict:
    """Make the reel of a written post, after checking it may be voiced: a rendered draft whose code checks still
    pass against the current boundaries, and whose reel lines all have an independent supported cross-check."""
    from .checks import COUNTS
    from .reel import checked_keys, make_reel

    folder = Path(folder)
    record = json.loads((folder / "post.json").read_text())
    trace = json.loads((folder / "trace.json").read_text())
    reasons = []
    if record["status"] != "draft_pending_review":
        reasons.append({"code": "REFUSED_STATUS", "detail": record["status"]})
    if (record.get("render") or {}).get("status") != "passed":
        reasons.append({"code": "REFUSED_NOT_RENDERED", "detail": record.get("render")})
    if reasons:  # nothing else to look at
        raise ReelRefused(reasons)
    claims, doc = claims_from_store(con, record["run_id"])
    boundaries = load_boundaries(doc["citation"].get("pmcid") or "")
    again = check_post(record["post"], claims, load_brand(), settings["rules"], boundaries, None,
                       record["writer"]["endpoint"], study_context(boundaries, doc))
    failed = again["post_failures"] + [f | {"path": ln["path"]} for ln in again["lines"] for f in ln["failures"]]
    if failed:
        reasons.append({"code": "REFUSED_CODE_CHECKS", "detail": failed,
                        "boundaries_version": boundaries.get("version")})
    for ln in trace["lines"]:
        cc = ln.get("crosscheck") or {}
        if ln["path"].startswith("reel_script/") and ln["kind"] in ("health", "framing") and \
                (cc.get("verdict") != "supported" or cc.get("independence") not in COUNTS):
            reasons.append({"code": "REFUSED_CROSSCHECK", "detail": ln["path"]})
    if reasons:
        raise ReelRefused(reasons)
    post = record["post"]
    emphasis = (post.get("slides") or [{}])[0].get("emphasis") if post.get("slides") else (post.get("static") or {}).get("emphasis")
    script = json.loads((folder / "reel_script.json").read_text())
    if v4 is not None:   # the v4 edit: the claims' exact quotes for the evidence phone; the checks above passed (checklist)
        v4 = v4 | {"quotes": {i: c["quote"] for i, c in claims.items()}, "checked": True}
        if v4.get("db"):   # a CC BY paper with a page layout: the phone shows the real page, cropped around the quote
            v4 = v4 | {"pages": evidence_pages(doc, claims, folder, Path(v4["db"]).parent / "studio-cache")}
        if v4.get("pullback_claim"):   # the pull-back screenshot from the studio's public copy (licence rule applies)
            from .reel import studio_pullback_shot
            shot = folder / "studio-pullback.png"
            v4 = v4 | {"pullback": str(shot), "pullback_meta": studio_pullback_shot(v4["db"], v4["pullback_claim"], shot)}
    return make_reel(record, script, load_brand(), folder, settings["voice"], tts=tts, transcribe=transcribe,
                     post_emphasis=emphasis, engine=engine, keys=checked_keys(post), presenter=presenter, v4=v4)


HI_SYSTEM = """You translate the checked English Instagram caption of a health post into natural Hinglish (Hindi and English
mixed, in Roman script, as Indians write on Instagram). Translate only; add no fact, advice, promise or number. Keep every
number, unit, confidence interval, population and comparison group exactly as written. Keep each line's "keep" phrases in
English, word for word, once each. Keep every number with its English unit and range words exactly as written ("0.4 kg",
"1.4 years", "0.3 to 0.5", "95% confidence interval"). Where a line has "use one of", write one of those Hinglish phrases
there instead. Always say what was stopped ("dawai band karne ke baad" or "medicines band karne ke baad"), never only
"rukne ke baad". Return the same lines in the same order, each with its kind and
claim_ids."""


TRANSLATED = ("health", "framing")


def capitalise(text: str) -> str:
    """Display only: the first letter of a caption line upper-case ("on average, ..." -> "On average, ...")."""
    return text[:1].upper() + text[1:]


def _hi_schema(n: int) -> dict:
    line = {"type": "object", "required": ["kind", "text", "claim_ids"], "additionalProperties": False,
            "properties": {"kind": {"type": "string"}, "text": {"type": "string"}, "claim_ids": {"type": "array", "items": {"type": "string"}}}}
    return {"type": "object", "required": ["lines"], "additionalProperties": False,
            "properties": {"lines": {"type": "array", "items": line, "minItems": n, "maxItems": n}}}


def hinglish_caption(folder: str | Path, con, settings: dict, router: Router | None = None) -> dict:
    """caption-hinglish.txt beside caption.txt: the writer's Hinglish wording of the post's checked caption lines, then
    the post's own checks again with that caption (numbers, wording, boundaries' qualifiers, the cross-check). The
    qualifier phrases the English caption carries are kept in English (no Hinglish any_of entries exist). Every line's
    numbers must equal its English line's. Nothing is written unless the post still passes."""
    from .checks import _has
    from .reel import number_values
    folder, router = Path(folder), router or Router(settings)
    record = json.loads((folder / "post.json").read_text())
    claims, doc = claims_from_store(con, record["run_id"])
    boundaries = load_boundaries(doc["citation"].get("pmcid") or "")
    en = record["post"]["caption"]["lines"]
    # per line: the first phrase of each qualifier rule that the English line carries stays in English
    rules = [r for r in boundaries.get("require_qualifier", []) if not r.get("hinglish")]
    native = [r for r in boundaries.get("require_qualifier", []) if r.get("hinglish")]   # a native wording exists: use it
    keeps = [[next(p for p in r.get("any_of", []) if _has(ln["text"], p)) for r in rules
              if any(_has(ln["text"], p) for p in r.get("any_of", []))] for ln in en]
    uses = [[r["hinglish"] for r in native if any(_has(ln["text"], p) for p in r.get("any_of", []))] for ln in en]
    keep = sorted({p for k in keeps for p in k})
    todo = [i for i, ln in enumerate(en) if ln["kind"] in TRANSLATED]   # cta / brand / sign-off: fixed lines, kept as they are
    prompt = "English caption lines:\n" + json.dumps(
        [{k: en[i][k] for k in ("kind", "text", "claim_ids")} | {"keep": keeps[i]} | ({"use one of": uses[i][0]} if uses[i] else {})
         for i in todo], ensure_ascii=False, indent=1)
    r = router.call("write", HI_SYSTEM, prompt, _hi_schema(len(todo)))
    hi = [dict(ln) for ln in en]
    for i, ln in zip(todo, r["output"]["lines"]):
        hi[i] = ln
    fails = [f"line {i}: kind or claims changed" for i, (a, b) in enumerate(zip(en, hi))
             if a["kind"] != b["kind"] or a["claim_ids"] != b["claim_ids"]]
    fails += [f"line {i}: numbers {number_values(b['text'])} != {number_values(a['text'])}" for i, (a, b) in enumerate(zip(en, hi))
              if number_values(a["text"]) != number_values(b["text"])]
    fails += [f"line {i}: qualifier {p!r} not kept" for i, k in enumerate(keeps) for p in k if not _has(hi[i]["text"], p)]
    fails += [f"line {i}: none of {u} used" for i, us in enumerate(uses) for u in us if not any(_has(hi[i]["text"], p) for p in u)]
    post = record["post"] | {"caption": record["post"]["caption"] | {"lines": hi}}
    result = check_post(post, claims, load_brand(), settings["rules"], boundaries, router, r["endpoint"],
                        record.get("study_context") or study_context(boundaries, doc))
    cap = [ln for ln in result["lines"] if ln["path"].startswith("caption")]
    fails += [f"{ln['path']}: {f['code']}" for ln in cap for f in ln["failures"]]
    fails += [f"{ln['path']}: cross-check {ln['crosscheck']['verdict']}" for ln in cap
              if ln.get("crosscheck") and ln["crosscheck"]["verdict"] != "supported"]
    fails += [f"post: {f['code']}" for f in result["post_failures"]]
    trace = {"post_id": record["post_id"], "writer": r["endpoint"], "keep_verbatim": keep, "failures": fails,
             "lines": [{"path": ln["path"], "kind": ln["kind"], "text": ln["text"], "failures": ln["failures"],
                        "crosscheck": ln.get("crosscheck")} for ln in cap]}
    (folder / "caption-hinglish-trace.json").write_text(json.dumps(trace, ensure_ascii=False, indent=1), encoding="utf-8")
    if fails:
        return {"status": "failed", "failures": fails}
    en_txt = (folder / "caption.txt").read_text(encoding="utf-8").split("\n\nSources: ", 1)
    (folder / "caption-hinglish.txt").write_text("\n".join(capitalise(ln["text"]) for ln in hi) + "\n\nSources: " + en_txt[1],
                                                 encoding="utf-8")
    return {"status": "passed", "lines": len(hi), "keep_verbatim": keep}


PHONE_ASPECT = 544 / 604   # the evidence phone's page area, height / width (Fx.tsx PhoneEvidence at 640 x 740 px)


def evidence_pages(doc: dict, claims: dict, folder: Path, cache: Path) -> dict:
    """{claim_id: {image, boxes}} for a CC BY paper with a page layout (else {}): the claim's first page, rendered from
    the ingested PDF (studio.page_image: SHA-256 checked) and cropped to the quote's own width and its S1
    line boxes; boxes are those lines as fractions of the crop (the highlight is never invented)."""
    import pymupdf
    from .studio import DPI, Refused, cc_by, page_image
    if not cc_by(doc["citation"]) or any(w["code"] == "NO_PAGE_LAYOUT" for w in doc["warnings"]):
        return {}
    pages = {pg["page"]: pg for pg in doc.get("pages") or []}
    lines = {ln["line_id"]: ln for pg in pages.values() for ln in pg.get("lines", [])}
    out = {}
    for cid, c in claims.items():
        lids = [lid for lid in c.get("line_ids") or [] if lines.get(lid, {}).get("bbox")]
        if not lids:
            continue
        page = int(lids[0][1:5])
        boxes = [lines[lid]["bbox"] for lid in lids if int(lid[1:5]) == page]
        try:
            page_image(doc, page, cache)   # fetches and SHA-checks the ingested PDF into the cache once
        except Refused:
            continue
        pw, ph = pages[page]["width"], pages[page]["height"]
        x0, x1 = max(0.0, min(b[0] for b in boxes) - 12), min(pw, max(b[2] for b in boxes) + 12)   # the quote's own width
        w = x1 - x0
        y_mid = (min(b[1] for b in boxes) + max(b[3] for b in boxes)) / 2
        h = min(ph, w * PHONE_ASPECT)
        y0 = min(max(0.0, y_mid - h / 2), ph - h)
        name = f"evidence-{cid}.jpg"
        pdf = pymupdf.open(cache / f"{doc['citation'].get('pmcid') or doc['document_id']}.pdf")
        pdf[page - 1].get_pixmap(dpi=DPI * 2, clip=pymupdf.Rect(x0, y0, x1, y0 + h)).save(folder / name, jpg_quality=85)
        out[cid] = {"image": str(folder / name), "name": name,
                    "boxes": [[round((b[0] - x0) / w, 4), round((b[1] - y0) / h, 4), round((b[2] - x0) / w, 4), round((b[3] - y0) / h, 4)]
                              for b in boxes]}
    return out
