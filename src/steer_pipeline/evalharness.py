"""`steer eval`: measure the pipeline and report what it caught, what slipped, what it could not check.

Fixtures (code/eval/*.json) hold every planted item with its `expect` written before any run. The offline parts
(gate, code flags, rules, line and boundary checks) need no model. With live=True the model-judged items run
through the configured router and record model + independence; a live result never changes an expectation.
Stored runs are read from SQLite (read-only), not re-run. Every number is reported with its n, never as one
accuracy figure. Report sections follow docs/specs/s5-eval-harness.md section 1.
"""

import json
import sqlite3
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from . import extract, posts, rules as rules_mod, store, studio
from .checks import check_boundaries, check_line, load_boundaries, study_context
from .crosscheck import COUNTS, crosscheck_library
from .normalize import canonical
from .numbers import find_numbers
from .text import ingest_text
from .verify import GATE_VERSION, _Doc, check_claim

EVAL_DIR = Path(__file__).resolve().parents[2] / "eval"
ROOT = Path(__file__).resolve().parents[2]
LIMITS = [
    "n = 3 gold claims is a smoke baseline, not an extraction rate.",
    "Many planted items are synthetic (marked in the tables); a catch on a synthetic item says the check exists, not that it generalises.",
    "Section 2 and 3 offline results test code only; the model-judged items run only with --live and are one run each (single-run variance).",
    "The extraction coverage match is word-overlap on sentence ids plus number equality: a ceiling, it cannot judge meaning.",
    "S1 soft-hyphen quirk: some stored quotes keep a soft hyphen from the PDF; the gate normalises it, a reader may see it.",
    "The checker (cross-check model) can be wrong both ways; its false positives are counted in section 3 only for the known fixtures.",
    "The store holds the runs of 3 papers (West, Laverde, Narayan); the Ismaiel safety paper has an export file but no run in this store.",
    "Human time is a timestamp estimate from review actions (gaps over 10 minutes excluded), not measured attention.",
]


def _load(name: str) -> dict:
    return json.loads((EVAL_DIR / name).read_text(encoding="utf-8"))


def git_version() -> str:
    try:
        sha = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()
        dirty = subprocess.run(["git", "status", "--porcelain", "--", "."], cwd=ROOT, capture_output=True, text=True).stdout.strip()
        return sha + ("+uncommitted" if dirty else "")
    except Exception:
        return "unknown"


# --- stored runs (read only) ---

def open_ro(db: str | Path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{db}?mode=ro", uri=True)


def load_papers(con: sqlite3.Connection) -> dict:
    """pmcid -> {run_id, doc, claims} for the latest run of each paper in the store."""
    out = {}
    rows = con.execute("SELECT r.run_id, d.document, json_extract(d.citation, '$.pmcid') FROM runs r "
                       "JOIN documents d USING (document_id) ORDER BY r.started_at").fetchall()
    for run_id, doc, pmcid in rows:
        out[pmcid] = {"run_id": run_id, "doc": json.loads(doc)}
    for p in out.values():
        p["claims"], _ = posts.claims_from_store(con, p["run_id"])
        p["chunks"] = json.loads(con.execute("SELECT chunks FROM runs WHERE run_id = ?", (p["run_id"],)).fetchone()[0])
        p["prepared"] = _Doc(p["doc"])
    return out


def synth_doc(text: str) -> dict:
    with tempfile.TemporaryDirectory() as d:
        f = Path(d) / "synthetic.txt"
        f.write_text(text, encoding="utf-8")
        return ingest_text(f, "evidence")


# --- one fixture item -> an actual result ---

def result(category, codes, stopped, **extra) -> dict:
    return {"category": category, "codes": list(codes), "stopped": stopped} | extra


def run_gate_item(it: dict, papers: dict, gate=check_claim) -> dict:
    gold = {g["id"]: g for g in _load("gold.json")["claims"]}
    quote = it.get("quote") or gold[it["base"]]["quote"]
    if it.get("doc_text"):
        doc = synth_doc(it["doc_text"])
        prepared = _Doc(doc)
    else:
        doc, prepared = papers[it["paper"]]["doc"], papers[it["paper"]]["prepared"]
    r = gate({"claim_id": it["id"], "text": it["claim_text"], "document_id": doc["document_id"], "quote": quote},
             {doc["document_id"]: prepared})
    if r["status"] == "reject":
        return result("gate_reject", [x["code"] for x in r["reasons"]], True, detail=r["reasons"])
    warn = [w["code"] for w in r["warnings"]]
    if it["kind"] == "code_flag":
        flags = extract.code_flags({"claim_text": it["claim_text"], "quote": quote, "hedged": False, "comparison_group": None})
        return result("code_flag", [f["code"] for f in flags], bool(flags), detail=flags)
    stopped = any(c in warn for c in ("SHORT_QUOTE", "HEDGE_DROPPED", "TABLE_TEXT", "LAYOUT_CHECK_NEEDED"))
    return result("gate_flag" if stopped else "gate_pass", warn, stopped, from_table_text=r.get("from_table_text"),
                  sids=sorted({s for m in r["matches"] for s in m["sids"]}), line_ids=[i for m in r["matches"] for i in m["line_ids"]][:6])


def run_rules_item(it: dict, rules: dict) -> dict:
    flags = rules_mod.check(it["text"], it["profile"], rules)
    blocks = [f["code"] for f in flags if f["action"] == "block"]
    if blocks:
        return result("rule_block", [f["code"] for f in flags], True, detail=[f["match"] for f in flags])
    return result("rule_flag" if flags else "rule_clean", [f["code"] for f in flags], bool(flags))


def line_env(it: dict, papers: dict, refs: dict):
    p = papers[it["paper"]]
    for cid in (it.get("line") or {"claim_ids": []})["claim_ids"] + [i for l in it.get("lines", []) for i in l["claim_ids"]]:
        ref = refs[cid]
        c = p["claims"].get(cid)
        if not c or not canonical(c["quote"])[0].startswith(canonical(ref["quote_start"])[0]):
            raise LookupError(f"fixture claim {cid} does not match the stored run of {it['paper']}")
    return p, load_boundaries(it["paper"])


def run_line_item(it: dict, papers: dict, refs: dict, rules: dict, brand: dict) -> dict:
    p, bounds = line_env(it, papers, refs)
    if it["expect"]["category"] == "post_boundary":
        lines = [l | {"path": f"slides/0/lines/{n}", "surface": "artwork", "unit": 0} for n, l in enumerate(it["lines"])]
        fails = check_boundaries(lines, p["claims"], bounds)
        return result("post_boundary", [f["code"] for f in fails], bool(fails))
    fails = check_line(dict(it["line"]), p["claims"], brand, rules, bounds)
    return result("line_check" if fails else "line_clean", [f["code"] for f in fails], bool(fails), detail=fails)


# --- judging ---

def judge(it: dict, act: dict | None) -> str:
    """not_run | caught_expected | caught_other | not_caught (errors); ok | false_alarm (controls)."""
    if act is None or act["stopped"] is None:
        return "not_run"
    exp = it["expect"]
    if it["type"] == "control":
        blocked = act["category"] in ("gate_reject", "rule_block", "line_check", "post_boundary") or \
            (act["category"] == "crosscheck" and act["codes"] and act["codes"][0] != "supported")
        wanted = exp.get("code")
        missing = wanted is not None and exp["category"] != "crosscheck" and wanted not in act["codes"]
        return "false_alarm" if blocked or missing else "ok"
    if not act["stopped"]:
        return "not_caught"
    accept = {exp["code"], *it.get("accept", [])} if exp["category"] == "crosscheck" else {exp["code"]}
    return "caught_expected" if act["category"] == exp["category"] and accept & set(act["codes"]) else "caught_other"


def run_offline(items: list[dict], papers: dict, refs: dict, rules: dict, brand: dict, gate=check_claim) -> dict:
    acts = {}
    for it in items:
        try:
            if it["kind"] in ("gate", "code_flag"):
                acts[it["id"]] = run_gate_item(it, papers, gate)
            elif it["kind"] == "rules":
                acts[it["id"]] = run_rules_item(it, rules)
            elif it["kind"] == "line":
                acts[it["id"]] = None if it.get("model_judged") else run_line_item(it, papers, refs, rules, brand)
            elif it["kind"] == "regression":
                acts[it["id"]] = run_regression(it)
        except LookupError as e:
            acts[it["id"]] = result("fixture_error", [str(e)], None)
    return acts


def run_regression(it: dict) -> dict:
    p = subprocess.run([sys.executable, "-m", "pytest", "-q", "-x", "-m", "not network", *it["tests"]], cwd=ROOT,
                       capture_output=True, text=True)
    tail = (p.stdout.strip().splitlines() or [""])[-1]
    return result("regression_test", ["pass" if p.returncode == 0 else "fail"], p.returncode != 0, detail=tail)


# --- live (model-judged) items ---

def run_live(items: list[dict], papers: dict, refs: dict, settings: dict, brand: dict) -> dict:
    from .checks import crosscheck_lines
    from .router import Router

    router = Router(settings)
    acts = {}
    for it in items:
        if not it.get("model_judged"):
            continue
        p = papers[it["paper"]]
        writer = next(c["endpoint"] for c in p["chunks"] if c.get("endpoint"))
        try:
            if it["kind"] == "claim_crosscheck":
                gold = {g["id"]: g for g in _load("gold.json")["claims"]}
                c = {"claim_id": it["id"], "claim_text": it["claim_text"], "quote": gold[it["base"]]["quote"], "chunk": 0}
                r = crosscheck_library([c], [{"endpoint": writer}], router)[0]
            else:
                _, bounds = line_env(it, papers, refs)
                ln = dict(it["line"]) | {"path": "message" if it["line"]["kind"] == "message" else "slides/0/key", "surface": "studio" if it["line"]["kind"] == "message" else "artwork", "unit": 0}
                r = crosscheck_lines([ln], p["claims"], router, writer, study_context(bounds, p["doc"]), brand["cta"])[ln["path"]]
        except LookupError as e:
            acts[it["id"]] = result("fixture_error", [str(e)], None)
            continue
        ok = r.get("independence") in COUNTS and r["verdict"] not in ("crosscheck_unavailable", "not_checked")
        if not ok:
            acts[it["id"]] = result("crosscheck_unavailable", [r["verdict"]], None, independence=r.get("independence"))
            continue
        acts[it["id"]] = result("crosscheck", [r["verdict"]], r["verdict"] != "supported", model=r.get("model"),
                                provider=r.get("provider"), independence=r.get("independence"), reason=r.get("reason"))
    return acts


# --- sections ---

def count_judged(items: list[dict], acts: dict) -> list[dict]:
    rows = []
    for it in items:
        if it["kind"] == "regression":
            continue
        act = acts.get(it["id"])
        rows.append({"id": it["id"], "layer": it["layer"], "type": it["type"], "synthetic": it.get("synthetic", False),
                     "model_judged": bool(it.get("model_judged")), "unsupported": bool(it.get("unsupported")), "note": it.get("note"), "mutation": it["mutation"], "expect": it["expect"],
                     "actual": None if act is None else {k: v for k, v in act.items() if k not in ("stopped", "detail")},
                     "result": judge(it, act)})
    return rows


def section_integrity(rows: list[dict]) -> dict:
    unsup = [r for r in rows if r["unsupported"]]
    run = [r for r in unsup if r["result"] != "not_run"]
    slipped = [r["id"] for r in run if r["result"] == "not_caught"]
    return {"target": 0, "n": len(run), "passed_through": len(slipped), "ids_passed_through": slipped,
            "not_run": [r["id"] for r in unsup if r["result"] == "not_run"],
            "wording": f"{len(slipped)} of {len(run)} planted unsupported items passed a gate in this test set (n = {len(run)})"}


def section_detection(rows: list[dict]) -> dict:
    errs = [r for r in rows if r["type"] == "error"]
    run = [r for r in errs if r["result"] != "not_run"]
    by = lambda res: [r["id"] for r in run if r["result"] == res]
    return {"n_planted_errors": len(errs), "n_run": len(run), "caught_in_expected_category": by("caught_expected"),
            "caught_in_other_category": [{"id": r["id"], "expected": r["expect"], "actual": r["actual"]} for r in run if r["result"] == "caught_other"],
            "not_caught": by("not_caught"), "not_run": [r["id"] for r in errs if r["result"] == "not_run"],
            "known_gaps": [f"{r['id']}: {r['mutation']} -> not caught: no such check yet" for r in run if r["result"] == "not_caught"],
            "notes": [f"{r['id']}: {r['note']}" for r in run if r["result"] == "caught_other" and r.get("note")],
            "wording": f"caught {len(by('caught_expected')) + len(by('caught_other'))} of {len(run)} planted errors that were run in this test set "
                       f"(n = {len(run)}); {len(by('caught_expected'))} in the expected category"}


def section_false_alarms(rows: list[dict], recorded: list[dict]) -> dict:
    ctl = [r for r in rows if r["type"] == "control"]
    run = [r for r in ctl if r["result"] != "not_run"]
    bad = [r["id"] for r in run if r["result"] == "false_alarm"] + [x["id"] for x in recorded if x["blocked"]]
    return {"n": len(run) + len(recorded), "false_alarms": len(bad), "ids": bad, "not_run": [r["id"] for r in ctl if r["result"] == "not_run"],
            "recorded_known": recorded,
            "wording": f"{len(bad)} of {len(run) + len(recorded)} known-good items were blocked or lost their expected flag (n = {len(run) + len(recorded)}; "
                       f"of which {len(recorded)} recorded model verdict(s) from earlier runs, not re-run)"}


def recorded_false_alarms(items: list[dict]) -> list[dict]:
    out = []
    for it in items:
        rec = it.get("recorded")
        if not rec:
            continue
        path = ROOT.parent / rec["trace"]
        verdict = None
        if path.exists():
            for ln in json.loads(path.read_text())["lines"]:
                if ln["path"] == rec["path"]:
                    verdict = (ln.get("crosscheck") or {}).get("verdict")
        out.append({"id": it["id"] + "-recorded", "source": rec["trace"], "recorded_verdict": verdict or rec["verdict"],
                    "file_found": path.exists(), "blocked": (verdict or rec["verdict"]) != "supported"})
    return out


def section_coverage(papers: dict) -> dict:
    rows = []
    for g in _load("gold.json")["claims"]:
        p = papers.get(g["pmcid"])
        if not p:
            rows.append({"id": g["id"], "found": False, "why": "paper not in the store"})
            continue
        cit = p["doc"]["citation"]
        loc = p["prepared"].locate(canonical(g["quote"])[0])
        sids = sorted({s for m in loc for s in m["sids"]})
        want = [n for t in g["numbers"] for n in find_numbers(t)]
        hits = []
        for c in p["claims"].values():
            have = set().union(*(n.keys() for n in find_numbers(c["claim_text"]))) if find_numbers(c["claim_text"]) else set()
            if set(c["sids"]) & set(sids) and all(n.keys() & have for n in want):
                hits.append({"claim_id": c["claim_id"], "status": c["status"]})
        rows.append({"id": g["id"], "pmcid": g["pmcid"], "run_id": p["run_id"], "fixture_ok": sids == [g["sid"]] and str(cit.get("pmid")) == g["pmid"],
                     "gold_sids": sids, "found": bool(hits), "found_clean_passed": any(h["status"] == "passed" for h in hits), "claims": hits})
    n = len(rows)
    return {"n": n, "found": sum(r["found"] for r in rows), "found_passed": sum(r.get("found_clean_passed", False) for r in rows), "per_gold_claim": rows,
            "wording": f"{sum(r['found'] for r in rows)} of {n} gold claims were found in the stored runs (n = {n}: a smoke baseline, not a rate)"}


def section_funnel(con: sqlite3.Connection, papers: dict) -> list[dict]:
    out = []
    for pmcid, p in papers.items():
        run = p["run_id"]
        row = dict(zip(["summary", "timings", "model_calls", "started_at"], con.execute(
            "SELECT summary, timings, model_calls, started_at FROM runs WHERE run_id = ?", (run,)).fetchone()))
        claims = con.execute("SELECT claim_id, status, gate, review_reasons, flags FROM claims WHERE run_id = ?", (run,)).fetchall()
        rej: dict = {}
        need: dict = {}
        for cid, status, gate, reasons, flags in claims:
            if status == "rejected":
                for x in json.loads(gate)["reasons"]:
                    rej[x["code"]] = rej.get(x["code"], 0) + 1
            elif status == "needs_check":
                for code in json.loads(reasons):
                    need[code] = need.get(code, 0) + 1
        latest = {}
        for cid, verdict, ind, model in con.execute("SELECT claim_id, verdict, independence, model FROM crosschecks WHERE run_id = ? ORDER BY checked_at", (run,)):
            latest[cid] = (verdict, ind, model)
        cc: dict = {}
        for verdict, ind, model in latest.values():
            k = f"{verdict} ({ind})"
            cc[k] = cc.get(k, 0) + 1
        verified = [cid for cid, status, gate, reasons, flags in claims
                    if status == "passed" and latest.get(cid, (None, None, None))[:2][0] == "supported" and latest[cid][1] in COUNTS]
        calls = json.loads(row["model_calls"])
        tok = lambda k: sum((c.get("tokens") or {}).get(k, 0) for c in calls)
        covered = {s for c in p["chunks"] for s in c["sids"]}
        out.append({"pmcid": pmcid, "run_id": run, "started_at": row["started_at"], "proposed": len(claims),
                    "rejected": sum(s == "rejected" for _, s, *_ in claims), "rejected_by_reason": rej,
                    "needs_check": sum(s == "needs_check" for _, s, *_ in claims), "needs_check_by_reason": need,
                    "passed": sum(s == "passed" for _, s, *_ in claims), "crosscheck_latest_by_verdict_and_independence": cc,
                    "crosscheck_models": sorted({m for _, _, m in latest.values() if m}),
                    "verified_recomputed": len(verified), "verified_in_run_summary": json.loads(row["summary"]).get("verified"),
                    "sentences": len(p["doc"]["sentences"]), "sentences_covered": len(covered & {s["sid"] for s in p["doc"]["sentences"]}),
                    "model_calls": len(calls), "tokens_as_reported": {"input": tok("input_tokens"), "output": tok("output_tokens")},
                    "seconds_per_step": json.loads(row["timings"])})
    return out


def section_posts(con: sqlite3.Connection, acts: list[dict]) -> dict:
    rows = con.execute("SELECT post_id, run_id, status, record FROM posts ORDER BY written_at, post_id").fetchall()
    by_status: dict = {}
    reasons: dict = {}
    per = []
    for pid, run, status, rec in rows:
        r = json.loads(rec)
        by_status[status] = by_status.get(status, 0) + 1
        codes = [f["code"] for f in r.get("post_failures", [])]
        trace = Path(con.execute("SELECT folder FROM posts WHERE post_id = ?", (pid,)).fetchone()[0]) / "trace.json"
        if trace.exists():
            codes += [f["code"] for ln in json.loads(trace.read_text())["lines"] for f in ln["failures"]]
        if status == "blocked":
            for c in set(codes):
                reasons[c] = reasons.get(c, 0) + 1
        mine = [a for a in acts if a["target_kind"] == "post" and a["target"] == pid]
        stages = {}
        for a in mine:
            stages[a["stage"]] = a["action"]
        per.append({"post_id": pid, "run_id": run, "status": status, "format": r.get("format"), "render": (r.get("render") or {}).get("status"),
                    "reasons": sorted(set(codes)), "review": stages})
    return {"n": len(rows), "by_status": by_status, "blocked_by_reason_posts": reasons, "render": {"passed": sum(p["render"] == "passed" for p in per),
            "failed": sum(p["render"] not in (None, "passed") for p in per), "not_rendered": sum(p["render"] is None for p in per)},
            "editorial_clinical_actions_on_posts": sum(a["target_kind"] == "post" for a in acts), "posts": per}


def section_human_time(con: sqlite3.Connection, papers: dict) -> dict:
    acts = store.actions(con)
    runs = {p["run_id"]: pm for pm, p in papers.items()}
    post_run = dict(con.execute("SELECT post_id, run_id FROM posts").fetchall())
    run_of = lambda a: a["target"].split("/")[0] if a["target_kind"] == "claim" else post_run.get(a["target"])
    minutes = studio.review_minutes(acts, run_of)
    per = []
    for run, pm in runs.items():
        n = sum(run_of(a) == run for a in acts)
        per.append({"pmcid": pm, "run_id": run, "review_actions": n, "estimated_review_minutes": minutes.get(run) if n else None,
                    "status": "timestamp estimate (gaps over 10 minutes excluded)" if n else "not measured yet"})
    return {"n_papers": len(per), "papers": per}


# --- self-test (spec section 4) ---

def mutated_gate(claim, docs, min_words=5):
    """A deliberately broken gate: forgets to check numbers, so a planted changed number passes."""
    r = check_claim(claim, docs, min_words)
    r["reasons"] = [x for x in r["reasons"] if x["code"] != "NUMBER_NOT_IN_QUOTE"]
    r["status"] = "reject" if r["reasons"] else "pass"
    return r


def self_test(items: list[dict], papers: dict, refs: dict, rules: dict, brand: dict) -> dict:
    gate_items = [it for it in items if it["id"] == "E1"]  # the planted changed number, through the broken gate
    rows = count_judged(gate_items, run_offline(gate_items, papers, refs, rules, brand, mutated_gate))
    got = section_integrity(rows)["passed_through"]
    return {"mutation": "gate without the number check (NUMBER_NOT_IN_QUOTE removed)", "integrity_count_with_mutation": got,
            "harness_detects_it": got >= 1,
            "wording": f"With the number check removed, {got} planted unsupported item(s) passed and the harness counted them (integrity count {got}); "
                       "a harness that reports 0 here would be broken."}


# --- the report ---

def evaluate(db: str | Path, out: str | Path, live: bool = False, settings: dict | None = None) -> dict:
    from .router import load_settings

    settings = settings or load_settings()
    con = open_ro(db)
    papers = load_papers(con)
    fx = [*_load("planted.json")["items"], *(posts_fx := _load("posts.json"))["items"]]
    refs = posts_fx["claim_refs"]
    brand = posts.load_brand()
    acts = run_offline(fx, papers, refs, settings["rules"], brand)
    if live:
        acts |= run_live(fx, papers, refs, settings, brand)
    rows = count_judged(fx, acts)
    recorded = recorded_false_alarms(fx)
    now = datetime.now(timezone.utc)
    runs = {pm: p["run_id"] for pm, p in papers.items()}
    reg = [{"id": it["id"], "layer": it["layer"], "tests": it["tests"], "result": acts[it["id"]]["codes"][0], "detail": acts[it["id"]]["detail"]}
           for it in fx if it["kind"] == "regression"]
    report = {
        "title": "Steer eval report", "date": now.date().isoformat(), "generated_at": now.isoformat(timespec="seconds"),
        "code_version": git_version(), "gate_version": GATE_VERSION,
        "rules_version": settings["rules"].get("version"), "mode": "live" if live else "offline (no model)", "store_runs": runs,
        "fixtures": {"items": len(fx), "synthetic": sum(bool(it.get("synthetic")) for it in fx), "real_paper": sum(not it.get("synthetic", False) for it in fx)},
        "1_verification_integrity": section_integrity(rows),
        "2_error_detection": section_detection(rows),
        "3_false_alarms": section_false_alarms(rows, recorded),
        "4_extraction_coverage": section_coverage(papers),
        "5_library_funnel": section_funnel(con, papers),
        "6_post_funnel": section_posts(con, store.actions(con)),
        "7_human_time_per_paper": section_human_time(con, papers),
        "regression_not_ai_accuracy": reg,
        "harness_self_test": self_test([it for it in fx], papers, refs, settings["rules"], brand),
        "items": rows,
        "known_limitations": LIMITS,
    }
    con.close()
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    (out / f"report-{report['date']}.json").write_text(json.dumps(report, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    (out / f"report-{report['date']}.md").write_text(to_markdown(report), encoding="utf-8")
    return report


def to_markdown(r: dict) -> str:
    L = [f"# {r['title']} ({r['date']})", "",
         f"Code version `{r['code_version']}` (gate {r['gate_version']}, rules {r['rules_version']}); mode: {r['mode']}; generated {r['generated_at']}.",
         f"Stored runs read (not re-run): " + ", ".join(f"{k} = {v}" for k, v in r["store_runs"].items()) + ".",
         f"Fixtures: {r['fixtures']['items']} items, {r['fixtures']['synthetic']} synthetic, {r['fixtures']['real_paper']} on real paper text.", ""]
    s1, s2, s3, s4 = r["1_verification_integrity"], r["2_error_detection"], r["3_false_alarms"], r["4_extraction_coverage"]
    tag = f"(code `{r['code_version']}`, {r['date']}, runs above)"
    L += ["## 1. Verification integrity (target 0)", "", tag, "", f"- {s1['wording']}.", f"- Passed through: {s1['ids_passed_through'] or 'none'}. Not run offline (model-judged): {s1['not_run'] or 'none'}.", "",
          "## 2. Error detection", "", tag, "", f"- {s2['wording']}.", f"- Caught in another category (listed): {s2['caught_in_other_category'] or 'none'}", f"- Notes: {s2['notes'] or 'none'}",
          f"- Not caught (known gaps): {s2['known_gaps'] or 'none'}", f"- Not run (needs --live): {s2['not_run'] or 'none'}", "",
          "| id | layer | synthetic | planted error | expected | actual | result |", "|---|---|---|---|---|---|---|"]
    for i in r["items"]:
        if i["type"] == "error":
            a = i["actual"]
            L.append(f"| {i['id']} | {i['layer']} | {'yes' if i['synthetic'] else 'no'} | {i['mutation']} | {i['expect']['category']} {i['expect'].get('code', '')} | "
                     f"{(a['category'] + ' ' + ','.join(a['codes'])) if a else 'not run'} | {i['result']} |")
    L += ["", "## 3. False alarms (known-good items blocked)", "", tag, "", f"- {s3['wording']}.", f"- Blocked or lost flag: {s3['ids'] or 'none'}. Not run offline: {s3['not_run'] or 'none'}.", "",
          "| id | layer | synthetic | known-good item | expected | actual | result |", "|---|---|---|---|---|---|---|"]
    for i in r["items"]:
        if i["type"] == "control":
            a = i["actual"]
            L.append(f"| {i['id']} | {i['layer']} | {'yes' if i['synthetic'] else 'no'} | {i['mutation']} | {i['expect']['category']} {i['expect'].get('code', '')} | "
                     f"{(a['category'] + ' ' + ','.join(a['codes'])) if a else 'not run'} | {i['result']} |")
    for x in s3["recorded_known"]:
        L.append(f"| {x['id']} | recorded model verdict | no | {x['source']} | supported | {x['recorded_verdict']} | {'false_alarm' if x['blocked'] else 'ok'} |")
    L += ["", "## 4. Extraction coverage (gold claims)", "", tag, "", f"- {s4['wording']}.", ""]
    for g in s4["per_gold_claim"]:
        L.append(f"- {g['id']} ({g.get('pmcid')}, run {g.get('run_id')}): found {g['found']}, claims {g.get('claims')}, fixture matches store: {g.get('fixture_ok')}")
    L += ["", "## 5. Library funnel per paper (from stored runs)", "", "Reason counts are occurrences (a claim can have several reasons).", ""]
    for f in r["5_library_funnel"]:
        L += [f"- {f['pmcid']} (run {f['run_id']}, {f['started_at']}): {f['proposed']} proposed; rejected {f['rejected']} {f['rejected_by_reason']}; "
              f"needs check {f['needs_check']} {f['needs_check_by_reason']}; passed {f['passed']}; cross-check {f['crosscheck_latest_by_verdict_and_independence']} "
              f"(models {f['crosscheck_models']}); verified {f['verified_recomputed']} recomputed vs {f['verified_in_run_summary']} in the run summary; "
              f"sentences covered {f['sentences_covered']} of {f['sentences']}; {f['model_calls']} model calls, tokens as reported {f['tokens_as_reported']}; seconds per step {f['seconds_per_step']}."]
    p = r["6_post_funnel"]
    L += ["", "## 6. Post funnel", "", tag, "", f"- {p['n']} posts written: {p['by_status']}; blocked posts by reason (posts naming it): {p['blocked_by_reason_posts']}.",
          f"- Render QA: {p['render']}. Review actions on posts (editorial/clinical): {p['editorial_clinical_actions_on_posts']}.", "",
          "## 7. Human time per paper", "", tag, ""]
    for h in r["7_human_time_per_paper"]["papers"]:
        L.append(f"- {h['pmcid']} (run {h['run_id']}): {h['review_actions']} review actions; "
                 + (f"{h['estimated_review_minutes']} min, {h['status']}; one action alone has no gap to measure" if h["review_actions"] == 1 else f"{h['estimated_review_minutes']} min, {h['status']}" if h["review_actions"] else "not measured yet"))
    L += ["", "## Regression tests (not AI accuracy)", ""]
    L += [f"- {x['id']} ({x['layer']}): {x['result']} ({x['detail']}); tests {x['tests']}" for x in r["regression_not_ai_accuracy"]]
    st = r["harness_self_test"]
    L += ["", "## Harness self-test", "", f"- {st['wording']}", "", "## Known limitations", ""] + [f"- {x}" for x in r["known_limitations"]] + [""]
    return "\n".join(L)
