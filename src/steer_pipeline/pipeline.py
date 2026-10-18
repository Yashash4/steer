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
    labelled = label(ex["passed"], router, settings["labels"]) if ex["passed"] else \
        {"labels": {}, "unlabeled": [], "failed_batches": []}
    for c in ex["passed"]:
        c["labels"] = labelled["labels"].get(c["claim_id"])
    timings["label"] = round(time.perf_counter() - t, 2)

    t = time.perf_counter()
    for c in ex["passed"] + ex["needs_check"]:
        c["rule_flags"] = rules.check(c["claim_text"], "claim_library", settings["rules"], source_quote=c["quote"])
    timings["rules"] = round(time.perf_counter() - t, 2)

    summary = ex["summary"] | {
        "labelled": len(labelled["labels"]), "unlabeled": len(labelled["unlabeled"]),
        "rule_flags": _count(f["code"] for c in ex["passed"] + ex["needs_check"] for f in c["rule_flags"]),
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
