"""`steer run`: ingest -> extract (with the gate) -> label -> rules -> store, with the time of every step."""

import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from . import rules
from .extract import extract
from .fetch import fetch
from .label import label
from .pdf import ingest_pdf
from .router import Router
from .text import ingest_text

REVIEW_RULES = {"PROMISE", "HYPE", "BEFORE_AFTER", "STEER_TEAM_CLAIM"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def ingest_source(source: str, role: str) -> dict:
    path = Path(source)
    if path.exists():
        return ingest_pdf(path, role) if path.suffix.lower() == ".pdf" else ingest_text(path, role)
    return fetch(source, role)


def run(source: str, role: str, settings: dict, router: Router | None = None) -> dict:
    """One full run on one source; returns the record that store.save_run writes."""
    router = router or Router(settings)
    record = {"run_id": f"run-{uuid.uuid4().hex[:12]}", "started_at": _now(), "input": source,
              "settings_version": settings.get("version"), "rules_version": settings["rules"].get("version")}
    timings = {}

    t = time.perf_counter()
    doc = ingest_source(source, role)
    timings["ingest"] = round(time.perf_counter() - t, 2)

    t = time.perf_counter()
    ex = extract(doc, router)
    timings["extract_and_verify"] = round(time.perf_counter() - t, 2)

    t = time.perf_counter()
    kept = ex["passed"] + ex["needs_check"]  # a claim a reviewer clears later still needs its labels; rejected never
    labelled = label(kept, router, settings["labels"]) if kept else \
        {"labels": {}, "unlabeled": [], "failed_batches": []}
    for c in kept:
        c["labels"] = labelled["labels"].get(c["claim_id"])
    timings["label"] = round(time.perf_counter() - t, 2)

    t = time.perf_counter()
    for c in kept:
        c["rule_flags"] = rules.check(c["claim_text"], "claim_library", settings["rules"], source_quote=c["quote"])
        # passed stays clean: a promise, hype, before/after or team-claim hit needs a reviewer (drug names are facts
        # papers state; causal wording is already an extraction flag)
        extra = {f["code"] for f in c["rule_flags"]} & REVIEW_RULES
        c["review_reasons"] = sorted(set(c.get("review_reasons", [])) | extra)
    ex["needs_check"] = ex["needs_check"] + [c for c in ex["passed"] if c["review_reasons"]]
    ex["passed"] = [c for c in ex["passed"] if not c["review_reasons"]]
    by_reason: dict[str, int] = {}
    for c in ex["needs_check"]:
        for code in c["review_reasons"]:
            by_reason[code] = by_reason.get(code, 0) + 1
    ex["summary"] |= {"passed": len(ex["passed"]), "needs_check": len(ex["needs_check"]), "needs_check_by_reason": by_reason}
    timings["rules"] = round(time.perf_counter() - t, 2)

    summary = ex["summary"] | {
        "labelled": len(labelled["labels"]), "unlabeled": len(labelled["unlabeled"]),
        "rule_flags": _count(f["code"] for c in kept for f in c["rule_flags"]),
        "model_calls": sum(a["outcome"] != "skipped_no_key" for a in router.log),
    }
    return record | {"finished_at": _now(), "document": doc, "timings": timings, "summary": summary,
                     "chunks": ex["chunks"], "model_calls": router.log, "label_result": labelled,
                     "passed": ex["passed"], "needs_check": ex["needs_check"], "rejected": ex["rejected"]}


def _count(codes) -> dict[str, int]:
    out: dict[str, int] = {}
    for c in codes:
        out[c] = out.get(c, 0) + 1
    return out
