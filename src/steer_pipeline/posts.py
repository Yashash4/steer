"""`steer write`: from a stored run, pick a brief, write a post, check every line, save post + trace.

Writes out/<run_id>/<post_id>/: post.json (message, reasoning, brief, lines with kinds and claim ids, status),
caption.txt, reel_script.json and trace.json (every line -> claim ids -> quote -> sids/pages -> every check
result, with the checking model and its independence). Nothing here approves a post.
"""

import json
import tomllib
import uuid
from pathlib import Path

from . import store
from .checks import check_post, load_boundaries, writable
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
                    brief: dict | None = None, out_dir: str | Path = "out") -> dict:
    router = router or Router(settings)
    claims, doc = claims_from_store(con, run_id)
    usable = [c for c in claims.values() if writable(c)]
    brief = brief or pick_brief(usable, fmt, settings["writer"].get("brief_claims", 4))
    brand = load_brand()
    boundaries = load_boundaries(doc["citation"].get("pmcid") or "")
    hashtags = settings["writer"]["hashtags"]
    written = write_post(brief, usable, brand, boundaries, hashtags, router)
    result = check_post(written["post"], claims, brand, settings["rules"], boundaries, router, written["endpoint"])
    post_id = f"post-{uuid.uuid4().hex[:8]}"
    used = sorted({i for ln in result["lines"] for i in ln["claim_ids"]})
    srcs = sources(used, claims, doc)
    folder = Path(out_dir) / run_id / post_id
    folder.mkdir(parents=True, exist_ok=True)
    post = written["post"]
    record = {"post_id": post_id, "run_id": run_id, "status": result["status"], "brief": brief,
              "message": post["message"], "reasoning": post["reasoning"], "format": post["format"],
              "writer": {"endpoint": written["endpoint"], "model": written["model"], "usage": written["usage"]},
              "post": post, "sources": srcs, "flags": result["flags"], "needs_layout_check": result["needs_layout_check"],
              "post_failures": result["post_failures"], "boundaries_version": boundaries.get("version")}
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
        f"{s['authors']} ({s['year']}) {s['journal']}, doi:{s['doi']}" for s in {s["doi"]: s for s in srcs}.values()) \
        + "\n\n" + " ".join(post["caption"]["hashtags"])
    (folder / "caption.txt").write_text(caption, encoding="utf-8")
    return record | {"folder": str(folder), "lines_checked": len(result["lines"])}
