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
    data = store.export_run(con, run_id)
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
                        "pngs": [r["png"] for r in qa["pages"]]}
    (folder / "post.json").write_text(json.dumps(record, ensure_ascii=False, indent=1), encoding="utf-8")
    return record["render"]


class ReelRefused(Exception):
    """The post may not become a reel; .reasons lists why."""

    def __init__(self, reasons: list[dict]):
        super().__init__("; ".join(r["code"] for r in reasons))
        self.reasons = reasons


def reel_post(folder: str | Path, con, settings: dict, tts=None, transcribe=None, engine: str = "remotion") -> dict:
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
    return make_reel(record, script, load_brand(), folder, settings["voice"], tts=tts, transcribe=transcribe,
                     post_emphasis=emphasis, engine=engine, keys=checked_keys(post))
