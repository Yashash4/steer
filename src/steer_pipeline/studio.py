"""The studio backend (S4): statuses derived from stored rows and append-only reviewer actions, the review gate,
and the two exports.

Honesty is the design: every status word means exactly what was checked. Statuses are pure functions of the
stored claims, posts and review_action rows (never stored, never edited). A reviewer's role comes only from
reviewers.toml on the server; whatever the browser says about a role is ignored.

Post stages, in order (a stage can be acted on only when every stage before it is complete; none implies another):
  code (checks at write time) -> layout (every claim that needs a layout check is layout_checked)
  -> editorial (an editor or clinician) -> clinical (a clinician only).
"""

import hashlib
import io
import json
import re
import tomllib
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from . import store

LAYOUT = "LAYOUT_CHECK_NEEDED"
REASON_CATEGORIES = ("inaccurate", "overstated", "missing context", "tone/brand", "other")
STAGE_OF = {"layout_checked": "layout", "layout_unresolved": "layout", "check_cleared": "check", "clearance_revoked": "check", "editorial_approved": "editorial",
            "editorial_rejected": "editorial", "clinically_approved": "clinical", "clinically_rejected": "clinical"}
REVIEW_GAP_S = 600   # estimated review time: gaps over 10 min between actions are not counted
DPI = 130


MIN_REASON_CHARS, MIN_REASON_WORDS = 40, 6
THIN_REASON = (f"A clearance needs a reason of at least {MIN_REASON_CHARS} characters and {MIN_REASON_WORDS} words: "
               "say what you checked and where.")


def status_word(bucket: str, usable: bool) -> str:
    """The short status for the pill: verified / needs check / rejected."""
    return "rejected" if bucket == "rejected" else ("verified" if usable else "needs check")


class Refused(Exception):
    """An action or export the gate does not allow: .http is the status code (400 / 403 / 404 / 409)."""

    def __init__(self, http: int, message: str, stage: str | None = None):
        super().__init__(message)
        self.http, self.stage = http, stage


def load_reviewers(path: str | Path) -> dict[str, str]:
    """name -> role ("editor" | "clinician") from reviewers.toml ([[reviewer]] name, role)."""
    data = tomllib.loads(Path(path).read_text())
    out = {}
    for r in data.get("reviewer", []):
        if r["role"] not in ("editor", "clinician"):
            raise ValueError(f"unknown role {r['role']!r} for {r['name']!r}")
        out[r["name"]] = r["role"]
    return out


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# --- statuses (pure) ---

def latest(acts: list[dict], kind: str, target: str, stage: str) -> dict | None:
    hits = [a for a in acts if a["target_kind"] == kind and a["target"] == target and a["stage"] == stage]
    return hits[-1] if hits else None


def who(a: dict) -> str:
    return f"{a['reviewer']} ({a['role']}), {a['at']}"


def to_clear(c: dict) -> set[str]:
    """The review reasons and code flags a reviewer must clear (layout has its own check)."""
    return (set(c.get("review_reasons") or []) | {f["code"] for f in c.get("flags") or []}) - {LAYOUT}


def clearances(c: dict, acts: list[dict]) -> dict[str, dict]:
    """The ACTIVE clearances by code: a clearance a later clearance_revoked points at no longer counts (it stays in
    the log, see clearance_history)."""
    key = f"{c['run_id']}/{c['claim_id']}"
    gone = {a["revokes"] for a in acts if a["action"] == "clearance_revoked"}
    return {a["clears"]: a for a in acts if a["target_kind"] == "claim" and a["target"] == key
            and a["action"] == "check_cleared" and a.get("id") not in gone}


def clearance_history(c: dict, acts: list[dict]) -> list[dict]:
    """Every clearance and revocation on the claim, in the order they happened (append-only, nothing dropped)."""
    key = f"{c['run_id']}/{c['claim_id']}"
    mine = [a for a in acts if a["target_kind"] == "claim" and a["target"] == key
            and a["action"] in ("check_cleared", "clearance_revoked")]
    revoked = {a["revokes"]: a for a in mine if a["action"] == "clearance_revoked"}
    return [{"id": a["id"], "code": a["clears"], "reviewer": a["reviewer"], "role": a["role"], "at": a["at"],
             "reason": a["reason"]} | ({"revoked": {"reviewer": revoked[a["id"]]["reviewer"], "role": revoked[a["id"]]["role"],
                                                     "at": revoked[a["id"]]["at"], "reason": revoked[a["id"]]["reason"]}}
                                       if a["id"] in revoked else {})
            for a in mine if a["action"] == "check_cleared"]


def fully_cleared(c: dict, acts: list[dict]) -> bool:
    """Every review reason and flag has a named reviewer's clearance and any layout check is done (S4). The caller
    still requires the independent supported cross-check and that the claim is not excluded."""
    key = f"{c['run_id']}/{c['claim_id']}"
    need, done = to_clear(c), clearances(c, acts)
    lay = latest(acts, "claim", key, "layout")
    layout_ok = LAYOUT not in (c.get("review_reasons") or []) or bool(lay and lay["action"] == "layout_checked")
    return c["status"] != "rejected" and bool(need) and need <= set(done) and layout_ok


def claim_status(c: dict, acts: list[dict]) -> dict:
    """The claim's status words and what they allow. c: a stored claim with its latest library cross-check."""
    key = f"{c['run_id']}/{c['claim_id']}"
    reasons = c.get("review_reasons") or []
    cc = c.get("crosscheck") or {}
    independent = cc.get("verdict") == "supported" and cc.get("independence") in ("different_provider",
                                                                                   "different_model_same_provider")
    lay = latest(acts, "claim", key, "layout")
    needs_layout = LAYOUT in reasons
    base = {"needs_layout": needs_layout, "layout": lay, "open": []}
    if c["status"] == "rejected":
        return base | {"words": "rejected by the gate", "usable": False, "needs_layout": False}
    if c.get("excluded"):   # the paper's boundaries file keeps it out of every post (it stays in the library)
        return base | {"words": f"excluded from posts ({c['excluded']})", "usable": False, "needs_layout": False}
    if lay and lay["action"] == "layout_unresolved":
        return base | {"words": f"layout unresolved ({lay['reason']})", "usable": False}
    done = clearances(c, acts)
    open_ = sorted(to_clear(c) - set(done)) + ([LAYOUT] if needs_layout and not (lay and lay["action"] == "layout_checked") else [])
    notes = [f"{code} cleared by {who(a)}: {a['reason']}" for code, a in sorted(done.items())]
    if lay and lay["action"] == "layout_checked":
        notes.append(f"layout checked by {who(lay)}")
    tail = ("; " + "; ".join(notes)) if notes else ""
    if open_:
        return base | {"words": f"needs check ({', '.join(open_)}){tail}", "usable": False, "open": open_}
    head = "verified" if independent else "passed code checks"
    return base | {"words": head + tail, "usable": independent}


def post_stages(post: dict, claims: dict[str, dict], acts: list[dict]) -> dict:
    """The four stages of a post: {stage: {status, done, words}} and the post's status words."""
    run = post["run_id"]
    used = sorted({i for ln in _post_lines(post) for i in ln.get("claim_ids", [])})
    statuses = {i: claim_status(claims[f"{run}/{i}"], acts) for i in used if f"{run}/{i}" in claims}
    unresolved = [i for i, s in statuses.items() if s["layout"] and s["layout"]["action"] == "layout_unresolved"]
    render_ok = (post.get("render") or {}).get("status") == "passed"
    now_failing = post.get("current_failures") or []   # the code checks re-run against today's rules (load())
    if unresolved:
        code = {"done": False, "words": f"blocked (layout unresolved: {', '.join(unresolved)})"}
    elif now_failing and post["status"] != "blocked":
        code = {"done": False, "words": f"blocked under the current rules ({', '.join(now_failing)})"}
    elif now_failing:   # blocked when written, and still failing: both lists of codes
        codes = sorted(set(post.get("write_failures") or []) | set(now_failing))
        code = {"done": False, "words": f"blocked ({', '.join(codes)})"}
    elif post["status"] == "blocked":
        why = ", ".join(post.get("write_failures") or sorted({f["code"] for f in post.get("post_failures", [])}))
        code = {"done": False, "words": f"blocked ({why or 'a check failed when it was written'})"}
    elif post["status"] == "crosscheck_unavailable":
        code = {"done": False, "words": "cross-check unavailable"}
    elif not render_ok:
        code = {"done": False, "words": "blocked (render QA)"}
    else:
        code = {"done": True, "words": "passed code checks"}
    need = [i for i, s in statuses.items() if s["needs_layout"]]
    open_ = [i for i in need if not (statuses[i]["layout"] and statuses[i]["layout"]["action"] == "layout_checked")]
    layout = {"done": code["done"] and not open_, "needs": need, "open": open_,
              "words": "no layout check needed" if not need else
              ("layout checked" if not open_ else f"layout check needed: {', '.join(open_)}")}
    ed = latest(acts, "post", post["post_id"], "editorial")
    editorial = {"done": bool(ed and ed["action"] == "editorial_approved"),
                 "words": "awaiting editorial review" if not ed else
                 (f"editorial approved by {who(ed)}" if ed["action"] == "editorial_approved"
                  else f"rejected by {ed['reviewer']} ({ed['role']}): {ed['reason']}")}
    cl = latest(acts, "post", post["post_id"], "clinical")
    clinical = {"done": bool(cl and cl["action"] == "clinically_approved"),
                "words": "pending doctor review" if not cl else
                (f"clinically approved by {who(cl)}" if cl["action"] == "clinically_approved"
                 else f"rejected by {cl['reviewer']} ({cl['role']}): {cl['reason']}")}
    stages = {"code": code, "layout": layout, "editorial": editorial, "clinical": clinical}
    first_open = next((k for k, v in stages.items() if not v["done"]), None)
    order = list(stages)
    acted = {"editorial": ed, "clinical": cl}
    for k in order[order.index(first_open) + 1:] if first_open else []:
        if not stages[k]["done"] and not acted.get(k):   # a later stage is not "awaiting" while an earlier one is open
            stages[k] = stages[k] | {"words": f"not reached ({first_open} stage first)"}
    return {"stages": stages, "first_open": first_open, "words": stages[first_open]["words"] if first_open else
            clinical["words"], "claims": {i: s["words"] for i, s in statuses.items()}}


def _post_lines(post: dict) -> list[dict]:
    p = post["post"]
    lines = [ln for sl in p.get("slides") or [] for ln in sl["lines"]]
    if p.get("static"):
        lines += [p["static"]["headline"], *p["static"]["supporting"]]
    lines += p["caption"]["lines"] + [ln for sc in p["reel_script"]["scenes"] for ln in sc["lines"]]
    return lines


# --- actions (the review gate) ---

def thick(reason: str | None) -> bool:
    return bool(reason) and len(reason) >= MIN_REASON_CHARS and len(reason.split()) >= MIN_REASON_WORDS


def act(con, reviewers: dict[str, str], body: dict, claims: dict[str, dict], posts: dict[str, dict]) -> dict:
    """Validate one reviewer action and append it. Raises Refused (400 / 403 / 404 / 409) when the gate says no."""
    name = (body.get("reviewer") or "").strip()
    role = reviewers.get(name)   # the role comes only from the server's reviewers file; body["role"] is ignored
    if not role:
        raise Refused(403, f"unknown reviewer {name!r}: not in reviewers.toml")
    action = body.get("action")
    stage = STAGE_OF.get(action)
    if not stage:
        raise Refused(400, f"unknown action {action!r}")
    reason = (body.get("reason") or "").strip() or None
    category = body.get("reason_category")
    rejecting = action in ("layout_unresolved", "editorial_rejected", "clinically_rejected")
    if rejecting and not reason:
        raise Refused(400, "a reason is required")
    if action in ("editorial_rejected", "clinically_rejected") and category not in REASON_CATEGORIES:
        raise Refused(400, f"reason_category must be one of {list(REASON_CATEGORIES)}")
    acts = store.actions(con)
    target = body.get("target") or ""
    clears = None
    if stage in ("layout", "check"):
        c = claims.get(target)
        if not c:
            raise Refused(404, f"no claim {target!r}")
        before = claim_status(c, acts)
        if stage == "layout" and not before["needs_layout"]:
            raise Refused(409, "this claim needs no layout check", "layout")
        if action == "layout_checked" and not thick(reason):
            raise Refused(400, THIN_REASON, "layout")
        if stage == "check" and action == "check_cleared":
            if c["status"] == "rejected":   # the gate's rejection is final: a rejected claim is re-extracted, not cleared
                raise Refused(409, "a claim rejected by the gate cannot be cleared", "check")
            if c.get("excluded"):
                raise Refused(409, f"this claim is excluded from posts: {c['excluded']}", "check")
            clears = body.get("clears")
            if clears not in to_clear(c):
                raise Refused(400, f"clears must name one of this claim's open reasons: {sorted(to_clear(c))}")
            if clears in clearances(c, acts):
                raise Refused(409, f"{clears} is already cleared", "check")
            if not thick(reason):
                raise Refused(400, THIN_REASON, "check")
        if action == "clearance_revoked":
            rid = body.get("revokes")
            tgt = next((a for a in acts if a["id"] == rid and a["action"] == "check_cleared" and a["target"] == target), None)
            if not tgt:
                raise Refused(404, f"no clearance {rid!r} on {target!r}", "check")
            if rid in {a["revokes"] for a in acts if a["action"] == "clearance_revoked"}:
                raise Refused(409, f"clearance {rid} is already revoked", "check")
            if not thick(reason):
                raise Refused(400, THIN_REASON, "check")
            clears = tgt["clears"]
        kind = "claim"
    else:
        p = posts.get(target)
        if not p:
            raise Refused(404, f"no post {target!r}")
        st = post_stages(p, claims, acts)
        order = ["code", "layout", "editorial", "clinical"]
        for s in order[:order.index(stage)]:
            if not st["stages"][s]["done"]:
                raise Refused(409, f"the {s} stage is not complete: {st['stages'][s]['words']}", s)
        if stage == "clinical" and role != "clinician":
            raise Refused(403, f"{name} is an {role}: only a clinician may act on clinical approval", "clinical")
        before = {"words": st["words"]}
        kind = "post"
    row = {"at": now(), "reviewer": name, "role": role, "target_kind": kind, "target": target, "stage": stage,
           "action": action, "clears": clears, "revokes": body.get("revokes") if action == "clearance_revoked" else None, "reason": reason, "reason_category": category if rejecting else None,
           "before_status": before["words"]}
    acts_after = acts + [row | {"id": None}]
    row["after_status"] = (claim_status(claims[target], acts_after)["words"] if kind == "claim"
                           else post_stages(posts[target], claims, acts_after)["words"])
    row["id"] = store.add_action(con, row)
    return row


# --- review time ---

def review_minutes(acts: list[dict], run_of_target) -> dict[str, float]:
    """Estimated review time per run (timestamp-based): the sum of gaps between consecutive actions on that run's
    items, gaps over 10 minutes excluded. It may include short idle time; it is not measured attention."""
    by_run: dict[str, list[datetime]] = {}
    for a in acts:
        r = run_of_target(a)
        if r:
            by_run.setdefault(r, []).append(datetime.fromisoformat(a["at"]))
    out = {}
    for r, ts in by_run.items():
        ts.sort()
        gaps = [(b - a).total_seconds() for a, b in zip(ts, ts[1:])]
        out[r] = round(sum(g for g in gaps if g <= REVIEW_GAP_S) / 60, 1)
    return out


# --- page images and licences ---

def cc_by(citation: dict) -> bool:
    """True only for CC BY (any version): a public copy may show its page images. CC BY-NC and others may not."""
    return bool(re.fullmatch(r"cc[ -]by(?:[ -]\d(?:\.\d)?)?", (citation.get("license") or "").strip().lower()))


def page_image(doc: dict, page: int, cache: Path) -> Path:
    """The PDF page as JPEG, rendered from the PDF the pipeline ingested (fetched once; refused if its SHA-256 differs
    from the stored content_sha256)."""
    import httpx
    import pymupdf

    cache.mkdir(parents=True, exist_ok=True)
    pmcid = doc["citation"].get("pmcid") or doc["document_id"]
    out = cache / f"{pmcid}-p{page}.jpg"
    if out.exists():
        return out
    if any(w["code"] == "NO_PAGE_LAYOUT" for w in doc["warnings"]):
        raise Refused(404, "no page layout for this source")
    pdf_path = cache / f"{pmcid}.pdf"
    if not pdf_path.exists():
        url = doc["source"]["provenance"]["resolved_url"]
        data = httpx.get(url, follow_redirects=True, timeout=120).content
        if hashlib.sha256(data).hexdigest() != doc["source"]["content_sha256"]:
            raise Refused(409, "the PDF online is not the one the pipeline read (SHA-256 differs)")
        pdf_path.write_bytes(data)
    d = pymupdf.open(pdf_path)
    if not 1 <= page <= d.page_count:
        raise Refused(404, f"no page {page}")
    d[page - 1].get_pixmap(dpi=DPI).save(out, jpg_quality=82)
    return out


# --- the studio's data (one JSON for every screen) ---

def load(con, post_dirs: list[Path]) -> tuple[dict, dict, dict]:
    """(documents by run, claims by 'run/claim', posts by id) from the store, registering post folders found under
    post_dirs whose run is in the store."""
    from .checks import check_post, excluded, load_boundaries, study_context
    from .posts import claims_from_store, load_brand
    from .router import load_settings

    rules, brand = load_settings()["rules"], load_brand()
    runs = [r[0] for r in con.execute("SELECT run_id FROM runs ORDER BY started_at")]
    docs, claims, by_run = {}, {}, {}
    for run in runs:
        cs, doc = claims_from_store(con, run)
        docs[run] = doc
        bounds = load_boundaries(doc["citation"].get("pmcid") or "")
        for c in cs.values():
            c["excluded"] = excluded(c, bounds)
        by_run[run] = (cs, bounds, study_context(bounds, doc))
        rejected = store.export_run(con, run)["claims"]
        for c in rejected:
            if c["status"] == "rejected":
                cs[c["claim_id"]] = c
        for cid, c in cs.items():
            claims[f"{run}/{cid}"] = c | {"run_id": run}
    for d in post_dirs:
        for pj in sorted(Path(d).glob("**/post.json")):
            rec = json.loads(pj.read_text())
            if rec.get("run_id") in docs:
                store.save_post(con, rec, pj.parent, rec.get("written_at") or now())
    posts = {}
    for pid, folder, rec in con.execute("SELECT post_id, folder, record FROM posts"):
        rec = json.loads(rec)
        if rec["run_id"] in docs:
            pj = Path(folder) / "post.json"
            p = (json.loads(pj.read_text()) if pj.exists() else rec) | {"folder": folder}
            # the code checks again, against today's rules and boundaries (no model call): a post written under
            # older rules is not shown as reviewable when it would fail now
            cs, bounds, ctx = by_run[p["run_id"]]
            again = check_post(p["post"], cs, brand, rules, bounds, None, p["writer"]["endpoint"], ctx)
            p["current_failures"] = sorted({f["code"] for f in again["post_failures"]}
                                           | {f["code"] for ln in again["lines"] for f in ln["failures"]})
            tr = Path(folder) / "trace.json"
            if tr.exists():
                t = json.loads(tr.read_text())
                p["write_failures"] = sorted({f["code"] for f in t.get("post_failures", [])}
                                             | {f["code"] for ln in t["lines"] for f in ln["failures"]})
            posts[pid] = p
    return docs, claims, posts


def _paper_facts(con, run: str, doc: dict) -> dict:
    """Run and ingest facts for the overview, read from the stored run and document (nothing estimated)."""
    from collections import Counter

    row = con.execute("SELECT summary, timings, chunks, model_calls FROM runs WHERE run_id = ?", (run,)).fetchone()
    summary, timings, chunks, calls = (json.loads(x) if x else None for x in row) if row else (None,) * 4
    calls = calls or []
    in_chunks = {s for c in chunks or [] for s in c.get("sids", [])}
    no_layout = any(w["code"] == "NO_PAGE_LAYOUT" for w in doc["warnings"])
    prov = doc["source"].get("provenance") or {}
    tok = lambda k: sum((a.get("tokens") or {}).get(k) or 0 for a in calls)
    return {"summary": summary or {}, "timing_seconds": timings or {}, "chunks": len(chunks or []),
            "sentences_in_document": len(doc.get("sentences") or []), "sentences_in_chunks": len(in_chunks),
            "all_sentences_covered": bool(doc.get("sentences")) and len(in_chunks) >= len(doc["sentences"]),
            "model_calls": len(calls), "call_outcomes": dict(Counter(a["outcome"] for a in calls)),
            "models": sorted({f"{a['endpoint']} ({a.get('model')})" for a in calls}),
            "tokens_in": tok("input_tokens"), "tokens_out": tok("output_tokens"),
            "pages": None if no_layout else len(doc["pages"]),
            "layouts": dict(Counter(pg.get("layout") for pg in doc["pages"])) if not no_layout else {},
            "page_sizes_pt": {} if no_layout else {str(pg["page"]): [pg["width"], pg["height"]] for pg in doc["pages"]},
            "ingest_warnings": dict(Counter(w["code"] for w in doc["warnings"])),
            "access": doc["source"].get("access"), "fetched_via": prov.get("fetched_via"), "no_page_layout": no_layout}


def data(con, docs: dict, claims: dict, posts: dict, reviewers: dict[str, str], public: bool = False) -> dict:
    """Everything the studio screens show, in the screens' shape (papers keyed by PMCID, claims keyed 'PMCID/claim').
    Each claim also carries `target` ('run/claim'), the key reviewer actions use. public=True (the read-only copy):
    page images only for CC BY papers."""
    acts = store.actions(con)
    run_of = lambda a: a["target"].split("/")[0] if a["target_kind"] == "claim" else posts.get(a["target"], {}).get("run_id")
    minutes = review_minutes(acts, run_of)
    pm_of = {run: doc["citation"].get("pmcid") or run for run, doc in docs.items()}
    papers = {}
    for run, doc in docs.items():
        cit = doc["citation"]
        authors = cit.get("authors") or ["Unknown"]
        papers[pm_of[run]] = {"run_id": run, "pmcid": cit.get("pmcid"), "citation": cit, "license": cit.get("license"),
                              "short": f"{authors[0].split()[0]} {cit.get('year') or 'n.d.'}",
                              "page_images": (not public) or cc_by(cit),
                              "pmc_url": f"https://pmc.ncbi.nlm.nih.gov/articles/{cit.get('pmcid')}/" if cit.get("pmcid") else None,
                              "estimated_review_minutes": minutes.get(run, 0.0)} | _paper_facts(con, run, doc)
    used_by: dict[str, list[dict]] = {}
    for pid, p in posts.items():
        for ln in _post_lines(p):
            for i in ln.get("claim_ids", []):
                used_by.setdefault(f"{p['run_id']}/{i}", []).append({"post": pid, "line_text": ln["text"]})
    line_maps = {run: {ln["line_id"]: ln for pg in doc.get("pages") or [] for ln in pg.get("lines", [])}
                 for run, doc in docs.items()}
    sents = {run: {x["sid"]: x for x in doc.get("sentences") or []} for run, doc in docs.items()}
    order = {run: [x["sid"] for x in doc.get("sentences") or []] for run, doc in docs.items()}
    out_claims = []
    for key, c in claims.items():
        run, st = c["run_id"], claim_status(c, acts)
        pm, paper = pm_of[run], papers[pm_of[run]]
        lines = line_maps.get(run, {})
        boxes: dict[str, list] = {}   # the highlight is drawn only from S1 line boxes, never invented
        for lid in c.get("line_ids") or []:
            if lid in lines and lines[lid].get("bbox"):
                boxes.setdefault(str(int(lid[1:5])), []).append({"line_id": lid, "bbox": lines[lid]["bbox"]})
        gate = c.get("gate") if isinstance(c.get("gate"), dict) else json.loads(c.get("gate") or "{}")
        sids = c.get("sids") or []
        ctx = None
        if paper["no_page_layout"] and sids and sids[0] in order[run]:
            i, j = order[run].index(sids[0]), order[run].index(sids[-1])
            ctx = [{"sid": x, "text": sents[run][x]["text"], "hit": x in sids} for x in order[run][max(0, i - 2): j + 3]]
        bucket = c["status"]
        out_claims.append({
            "key": f"{pm}/{c['claim_id']}", "target": key, "paper": pm, "run_id": run, "claim_id": c["claim_id"],
            "bucket": bucket, "status": st["words"], "status_word": status_word(bucket, st["usable"]), "usable": st["usable"], "needs_layout": st["needs_layout"],
            "reasons": [r["code"] for r in gate.get("reasons", [])] if bucket == "rejected" else (c.get("review_reasons") or []),
            "claim_text": c["claim_text"], "quote": c["quote"], "claim_type": c.get("claim_type"),
            "population": c.get("population"), "comparison_group": c.get("comparison_group"), "outcome": c.get("outcome"),
            "time_point": c.get("time_point"), "limitations": c.get("limitations"), "hedged": c.get("hedged"),
            "direction": c.get("direction"), "sids": sids, "line_ids": c.get("line_ids") or [],
            "pages": None if paper["no_page_layout"] else sorted({int(l[1:5]) for l in c.get("line_ids") or []}),
            "sentences": [{"sid": x, "text": sents[run][x]["text"]} for x in sids if x in sents[run]], "context": ctx,
            "gate_status": gate.get("status"), "gate_reasons": gate.get("reasons", []), "gate_warnings": gate.get("warnings", []),
            "flags": c.get("flags") or [], "numbers_checked": gate.get("numbers_checked", []), "boxes": boxes,
            "labels": c.get("labels"), "crosscheck": c.get("crosscheck"), "excluded": c.get("excluded"),
            "open": st["open"], "clearable": sorted(to_clear(c) - set(clearances(c, acts))) if bucket != "rejected"
            and not c.get("excluded") else [],
            "clearance_history": clearance_history(c, acts),
            "clearances": [{"id": a["id"], "code": code, "reviewer": a["reviewer"], "role": a["role"], "at": a["at"], "reason": a["reason"]}
                           for code, a in sorted(clearances(c, acts).items())],
            "used_in": used_by.get(key, []),
            "page_note": None if paper["page_images"] or paper["no_page_layout"] else f"page image not shown: {paper['license']}"})
    out_posts = []
    for pid, p in posts.items():
        st = post_stages(p, claims, acts)
        trace = Path(p["folder"]) / "trace.json"
        lines = json.loads(trace.read_text())["lines"] if trace.exists() else []
        out_posts.append({"id": pid, "post_id": pid, "run_id": p["run_id"], "paper": pm_of.get(p["run_id"]),
                          "format": p.get("format"), "status": st["words"], "first_open": st["first_open"],
                          "stages": st["stages"], "claim_status": st["claims"], "message": p.get("message"),
                          "reasoning": p.get("reasoning"), "brief": p.get("brief"),
                          "pngs": [f"files/{pid}/{n}" for n in (p.get("render") or {}).get("pngs", [])],
                          "has_reel": (Path(p["folder"]) / "reel.mp4").exists(), "folder": p["folder"],
                          "lines": [{"path": ln["path"], "kind": ln["kind"], "text": ln["text"], "claim_ids": ln["claim_ids"],
                                     "failures": ln.get("failures", []), "crosscheck": ln.get("crosscheck")} for ln in lines],
                          "current_failures": p.get("current_failures") or [], "write_failures": p.get("write_failures") or []})
    return {"generated_at": now(), "public": public, "papers": papers, "claims": out_claims, "posts": out_posts,
            "reviewers": {"names": sorted(reviewers), "has_clinician": "clinician" in reviewers.values(),
                          "note": "self-identified (prototype, no login)"},
            "review_time_method": "estimated review time (timestamp-based): the sum of gaps between consecutive actions "
                                  "on a paper's items, gaps over 10 minutes excluded; may include short idle time",
            "actions": acts}


# --- exports ---

def _manifest(post: dict, st: dict, claims: dict, acts: list[dict]) -> dict:
    run = post["run_id"]
    used = sorted({i for ln in _post_lines(post) for i in ln.get("claim_ids", [])})
    return {"post_id": post["post_id"], "run_id": run, "stages": {k: v["words"] for k, v in st["stages"].items()},
            "claims": [{"claim_id": i, "quote": claims[f"{run}/{i}"]["quote"], "sids": claims[f"{run}/{i}"].get("sids"),
                        "status": st["claims"].get(i),
                        "automated_flags": sorted(to_clear(claims[f"{run}/{i}"])),   # kept, even when cleared
                        "clearances": [{"code": k, "reviewer": a["reviewer"], "role": a["role"], "at": a["at"],
                                        "reason": a["reason"]} for k, a in sorted(clearances(claims[f"{run}/{i}"], acts).items())]}
                       for i in used if f"{run}/{i}" in claims],
            "sources": post.get("sources"),
            "actions": [a for a in acts if (a["target_kind"] == "post" and a["target"] == post["post_id"])
                        or (a["target_kind"] == "claim" and a["target"].split("/")[1] in used and a["target"].startswith(run + "/"))]}


def export_draft_pack(con, post_ids: list[str], claims: dict, posts: dict) -> bytes:
    """Any post: its PNGs as rendered (every one carries 'draft · pending doctor review'), caption, reel files, and a
    manifest with all four stage statuses."""
    acts = store.actions(con)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        manifest = []
        for pid in post_ids:
            p = posts.get(pid)
            if not p:
                raise Refused(404, f"no post {pid!r}")
            folder = Path(p["folder"])
            for f in sorted(folder.iterdir()):
                if f.suffix in (".png", ".mp4", ".srt") or f.name in ("caption.txt", "reel_script.json"):
                    z.write(f, f"{pid}/{f.name}")
            manifest.append(_manifest(p, post_stages(p, claims, acts), claims, acts))
        z.writestr("manifest.json", json.dumps({"kind": "draft pack: every image carries 'draft · pending doctor review'",
                                                 "posts": manifest}, ensure_ascii=False, indent=1))
    return buf.getvalue()


def export_publishable(con, post_id: str, claims: dict, posts: dict, brand: dict) -> bytes:
    """Only a post whose four stages are all complete: a clean re-render (no draft mark) + caption + manifest.
    Otherwise Refused(409) naming the first missing stage."""
    from .render import render

    acts = store.actions(con)
    p = posts.get(post_id)
    if not p:
        raise Refused(404, f"no post {post_id!r}")
    st = post_stages(p, claims, acts)
    if st["first_open"]:
        raise Refused(409, f"not publishable: the {st['first_open']} stage is not complete "
                           f"({st['stages'][st['first_open']]['words']})", st["first_open"])
    import tempfile
    buf = io.BytesIO()
    with tempfile.TemporaryDirectory(prefix="steer-publish-") as tmp, zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        qa = render(p, brand, Path(tmp), draft=False)
        if qa["status"] != "passed":
            raise Refused(409, "the clean re-render failed render QA", "code")
        for f in sorted(Path(tmp).glob("*.png")):
            z.write(f, f"{post_id}/{f.name}")
        cap = Path(p["folder"]) / "caption.txt"
        if cap.exists():
            z.write(cap, f"{post_id}/caption.txt")
        z.writestr("manifest.json", json.dumps({"kind": "publishable", "posts": [_manifest(p, st, claims, acts)]},
                                                ensure_ascii=False, indent=1))
    return buf.getvalue()


# --- the local server and the static copy ---

def make_handler(con, state, reviewers: dict[str, str], brand: dict, cache: Path, static_dir: Path | None):
    """state() -> (docs, claims, posts), read fresh on every request (posts written meanwhile appear)."""
    from http.server import BaseHTTPRequestHandler

    class Handler(BaseHTTPRequestHandler):
        def _send(self, code: int, body: bytes, ctype: str, extra: dict | None = None):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)

        def _json(self, code: int, obj):   # never cached: statuses change with every reviewer action
            self._send(code, json.dumps(obj, ensure_ascii=False, default=str).encode(), "application/json; charset=utf-8",
                       {"Cache-Control": "no-store"})

        def log_message(self, *a):   # quiet
            pass

        def do_GET(self):
            docs, claims, posts = state()
            path = self.path.split("?")[0]
            try:
                if path == "/api/data":
                    return self._json(200, data(con, docs, claims, posts, reviewers))
                m = re.fullmatch(r"/pages/(PMC\d+)-p(\d+)\.jpg", path)
                if m:
                    doc = next((d for d in docs.values() if d["citation"].get("pmcid") == m[1]), None)
                    if not doc:
                        raise Refused(404, "unknown paper")
                    return self._send(200, page_image(doc, int(m[2]), cache).read_bytes(), "image/jpeg")
                m = re.fullmatch(r"/files/(post-[0-9a-f]+)/([\w.-]+\.(png|mp4|srt|txt))", path)
                if m and m[1] in posts:
                    f = Path(posts[m[1]]["folder"]) / m[2]
                    if f.is_file():
                        types = {"png": "image/png", "mp4": "video/mp4", "srt": "text/plain", "txt": "text/plain"}
                        return self._send(200, f.read_bytes(), types[m[3]])
                if static_dir:
                    f = (static_dir / (path.lstrip("/") or "index.html")).resolve()
                    if f.is_file() and static_dir.resolve() in f.parents:
                        ctype = {".html": "text/html", ".js": "text/javascript", ".css": "text/css", ".ttf": "font/ttf",
                                 ".jpg": "image/jpeg", ".png": "image/png"}.get(f.suffix, "application/octet-stream")
                        return self._send(200, f.read_bytes(), ctype)
                self._json(404, {"error": "not found"})
            except Refused as e:
                self._json(e.http, {"error": str(e), "stage": e.stage})

        def do_POST(self):
            docs, claims, posts = state()
            try:
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
                if self.path == "/api/action":
                    return self._json(200, act(con, reviewers, body, claims, posts))
                if self.path == "/api/export/draft-pack":
                    return self._send(200, export_draft_pack(con, body.get("post_ids") or [], claims, posts), "application/zip",
                                      {"Content-Disposition": "attachment; filename=steer-draft-pack.zip"})
                if self.path == "/api/export/publishable":
                    return self._send(200, export_publishable(con, body.get("post_id") or "", claims, posts, brand),
                                      "application/zip", {"Content-Disposition": "attachment; filename=steer-publishable.zip"})
                self._json(404, {"error": "not found"})
            except Refused as e:
                self._json(e.http, {"error": str(e), "stage": e.stage})
            except (ValueError, KeyError) as e:
                self._json(400, {"error": f"bad request: {e}"})

    return Handler


def serve(db: str | Path, post_dirs: list[Path], reviewers_path: str | Path, port: int = 8765,
          static_dir: Path | None = None) -> None:
    from http.server import HTTPServer

    from .posts import load_brand

    con = store.connect(db)
    reviewers = load_reviewers(reviewers_path)
    state = lambda: load(con, post_dirs)
    handler = make_handler(con, state, reviewers, load_brand(), Path(db).parent / "studio-cache", static_dir)
    print(f"studio on http://127.0.0.1:{port} (reviewers: {', '.join(f'{n} ({r})' for n, r in reviewers.items())})")
    HTTPServer(("127.0.0.1", port), handler).serve_forever()   # one thread: one SQLite connection, no races


def export_site(db: str | Path, post_dirs: list[Path], reviewers_path: str | Path, out: Path,
                static_dir: Path | None = None) -> dict:
    """A read-only copy for judges: the data snapshot (public=True), the post images, and page images only for
    CC BY papers. No action routes exist in it (there is no server); the screens show 'demo copy: actions disabled'."""
    import shutil

    con = store.connect(db)
    docs, claims, posts = load(con, post_dirs)
    snap = data(con, docs, claims, posts, load_reviewers(reviewers_path), public=True)
    snap["banner"] = "demo copy: actions disabled"
    out.mkdir(parents=True, exist_ok=True)
    if static_dir:
        shutil.copytree(static_dir, out, dirs_exist_ok=True)
    for p in snap["posts"]:
        dest = out / "files" / p["post_id"]
        dest.mkdir(parents=True, exist_ok=True)
        for url in p["pngs"]:
            src = Path(p["folder"]) / Path(url).name
            if src.exists():
                shutil.copy(src, dest / src.name)
        p["folder"] = f"files/{p['post_id']}"
    pages = []
    for c in snap["claims"]:
        paper = snap["papers"][c["paper"]]
        if paper["page_images"] and not paper["no_page_layout"]:
            for n in c["pages"] or []:
                f = page_image(docs[c["run_id"]], n, Path(db).parent / "studio-cache")
                (out / "pages").mkdir(exist_ok=True)
                shutil.copy(f, out / "pages" / f.name)
                pages.append(f.name)
    (out / "data.js").write_text("window.STEER_DATA = " + json.dumps(snap, ensure_ascii=False, default=str) + ";\n",
                                 encoding="utf-8")
    return {"posts": len(snap["posts"]), "claims": len(snap["claims"]), "page_images": sorted(set(pages))}


# --- the public, read-only snapshot (D-031): static files only, built from a COPY of the store ---

PUBLIC_UI = Path(__file__).parent / "studio_ui" / "public"
_PUB_CLAIM = ("key", "paper", "claim_id", "bucket", "status", "status_word", "reasons", "claim_text", "quote", "claim_type",
              "population", "comparison_group", "outcome", "time_point", "limitations", "hedged", "direction", "sids", "pages",
              "sentences", "context", "gate_status", "gate_reasons", "gate_warnings", "flags", "numbers_checked", "boxes",
              "excluded", "clearance_history", "used_in", "page_note")
_PUB_PAPER = ("pmcid", "citation", "license", "short", "page_images", "pmc_url", "page_sizes_pt", "no_page_layout", "pages",
              "sentences_in_document", "ingest_warnings", "access")


def _merge_into(con, other: str | Path) -> None:
    """Copy documents/runs/claims/crosschecks of another store into this (copied) one; existing rows win."""
    con.execute("ATTACH DATABASE ? AS o", (str(other),))
    with con:
        for t in ("documents", "runs", "claims", "crosschecks"):
            con.execute(f"INSERT OR IGNORE INTO {t} SELECT * FROM o.{t}")
    con.execute("DETACH DATABASE o")


def public_snapshot(snap: dict, acts: list[dict], reel_of) -> dict:
    """Whitelist what the public copy may carry: no folders, no model endpoints, no reviewer list, no ids that only the
    live studio needs. The review history is the append-only log as recorded."""
    claims = [{k: c.get(k) for k in _PUB_CLAIM} | {"crosscheck": {"verdict": (c.get("crosscheck") or {}).get("verdict"),
                                                                  "reason": (c.get("crosscheck") or {}).get("reason"),
                                                                  "independence": (c.get("crosscheck") or {}).get("independence")}
                                                    if c.get("crosscheck") else None} for c in snap["claims"]]
    keyname = {c["target"]: f"{c['paper']}/{c['claim_id']}" for c in snap["claims"]}
    posts = [{"id": p["post_id"], "paper": p["paper"], "format": p["format"], "status": p["status"], "stages": p["stages"],
              "message": p["message"], "claim_status": p["claim_status"], "pngs": p["pngs"], "reel": reel_of(p),
              "lines": [{k: ln[k] for k in ("path", "kind", "text", "claim_ids")} | {"failures": [f["code"] for f in ln["failures"]],
                        "crosscheck": (ln.get("crosscheck") or {}).get("verdict") if isinstance(ln.get("crosscheck"), dict) else None}
                        for ln in p["lines"]]} for p in snap["posts"]]
    history = [{"id": a["id"], "at": a["at"], "reviewer": a["reviewer"], "role": a["role"], "action": a["action"],
                "target": keyname.get(a["target"], a["target"]), "target_kind": a["target_kind"], "clears": a.get("clears"),
                "revokes": a.get("revokes"), "reason": a.get("reason"), "after_status": a.get("after_status")} for a in acts]
    return {"generated_at": snap["generated_at"], "papers": {k: {f: v.get(f) for f in _PUB_PAPER} for k, v in snap["papers"].items()},
            "claims": claims, "posts": posts, "history": history,
            "clinician_review": "no Steer clinician has reviewed anything here" if not any(
                a["role"] == "clinician" for a in acts) else "see the review history"}


def export_public(db: str | Path, post_dirs: list[Path], reviewers_path: str | Path, out: Path, merge: list[str] = (),
                  cache: Path | None = None) -> dict:
    """steer studio --public OUT: a static bundle (HTML + JS + images + reel MP4s) of real runs with their TRUE review
    statuses. Works on a copy of the store (the live file is only read); the bundle has no API, forms or db file."""
    import shutil
    import sqlite3
    import tempfile

    out.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="steer-public-") as tmp:
        copy = Path(tmp) / "copy.db"
        src = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        dst = sqlite3.connect(copy)
        src.backup(dst)
        src.close(), dst.close()
        con = store.connect(copy)
        for m in merge:
            _merge_into(con, m)
        docs, claims, posts = load(con, post_dirs)
        snap = data(con, docs, claims, posts, load_reviewers(reviewers_path), public=True)
        cache = cache or Path(tmp) / "cache"
        for sub in ("files", "pages", "fonts"):
            shutil.rmtree(out / sub, ignore_errors=True)
        shutil.copytree(PUBLIC_UI, out, dirs_exist_ok=True)
        shutil.copytree(Path(__file__).parent / "studio_ui" / "static" / "fonts", out / "fonts")
        shutil.copy(Path(__file__).parent / "studio_ui" / "static" / "studio.css", out / "studio.css")
        reel_of = lambda p: f"files/{p['post_id']}/reel.mp4" if (Path(p["folder"]) / "reel.mp4").exists() else None
        for p in snap["posts"]:
            dest = out / "files" / p["post_id"]
            dest.mkdir(parents=True, exist_ok=True)
            for url in p["pngs"]:
                if (Path(p["folder"]) / Path(url).name).exists():
                    shutil.copy(Path(p["folder"]) / Path(url).name, dest / Path(url).name)
            if reel_of(p):
                shutil.copy(Path(p["folder"]) / "reel.mp4", dest / "reel.mp4")
        pages = set()
        for c in snap["claims"]:
            paper = snap["papers"][c["paper"]]
            if paper["page_images"] and not paper["no_page_layout"]:   # CC BY only (cc_by); BY-NC and unknown never
                for n in c["pages"] or []:
                    f = page_image(docs[c["run_id"]], n, cache)
                    (out / "pages").mkdir(exist_ok=True)
                    shutil.copy(f, out / "pages" / f.name)
                    pages.add(f.name)
        pub = public_snapshot(snap, store.actions(con), reel_of)
        con.close()
    (out / "data.js").write_text("window.STEER_PUBLIC = " + json.dumps(pub, ensure_ascii=False, default=str) + ";\n",
                                 encoding="utf-8")
    return {"papers": sorted(pub["papers"]), "posts": [p["id"] for p in pub["posts"]], "claims": len(pub["claims"]),
            "page_images": sorted(pages), "reels": [p["id"] for p in pub["posts"] if p["reel"]]}
