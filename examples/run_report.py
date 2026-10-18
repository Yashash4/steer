"""Sample run: fetch one paper, ingest it, check a claims file, write a JSON report and a Markdown summary.

    uv run python examples/run_report.py PMC8006839 examples/narayan-claims.json examples/narayan
"""

import json
import sys
import time
from collections import Counter
from pathlib import Path

from steer_pipeline.fetch import fetch
from steer_pipeline.verify import verify


def main(source: str, claims_path: str, out_prefix: str) -> None:
    claims = json.loads(Path(claims_path).read_text(encoding="utf-8"))
    t0 = time.perf_counter()
    doc = fetch(source, "evidence")
    t1 = time.perf_counter()
    for c in claims:
        c["document_id"] = doc["document_id"]
    report = verify(claims, [doc])
    t2 = time.perf_counter()

    pages = {p["page"]: p for p in doc["pages"]}
    for r, c in zip(report["results"], claims):
        r["expect"] = c.get("expect")
        for m in r["matches"]:
            m["pages"] = sorted({int(i[1:5]) for i in m["line_ids"]})
            m["layouts"] = sorted({pages[p]["layout"] for p in m["pages"]})
    summary = {
        "source": source,
        "document_id": doc["document_id"],
        "source_id": doc["source"]["source_id"],
        "citation": doc["citation"],
        "resolved_url": doc["source"]["provenance"]["resolved_url"],
        "extraction_version": doc["extraction_version"],
        "extractor": doc["extractor"],
        "pages": len(doc["pages"]),
        "layouts": dict(Counter(p["layout"] for p in doc["pages"])),
        "sentences": len(doc["sentences"]),
        "warnings": doc["warnings"],
        "timing_seconds": {"fetch_and_ingest": round(t1 - t0, 3), "verify": round(t2 - t1, 3)},
    }
    out = {"ingest": summary, "gate": report}
    Path(f"{out_prefix}-report.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    Path(f"{out_prefix}-report.md").write_text(markdown(summary, report), encoding="utf-8")
    print(f"{report['summary']} -> {out_prefix}-report.json, {out_prefix}-report.md")


def markdown(s: dict, report: dict) -> str:
    c = s["citation"]
    lines = [
        "# Sample run: paper to checked claims",
        "",
        f"**Paper:** {', '.join(c['authors'][:3])} et al. *{c['title']}* {c['journal']}, {c['year']}. "
        f"DOI {c['doi']} (PMID {c['pmid']}, {c['pmcid']}). Licence {c['license']}; retracted: {c['retracted']}.",
        f"**Fetched from:** {s['resolved_url']}",
        "",
        "## Ingest",
        "",
        f"- {s['pages']} pages ({', '.join(f'{v} {k}' for k, v in s['layouts'].items())}), {s['sentences']} sentences, "
        f"extraction `{s['extraction_version']}` ({s['extractor']['name']} {s['extractor']['version']}).",
        f"- Warnings: " + (", ".join(f"{k} x{v}" for k, v in Counter(w['code'] for w in s['warnings']).items()) or "none") + ".",
        f"- Time (measured by the script): fetch + ingest {s['timing_seconds']['fetch_and_ingest']} s, "
        f"verify {s['timing_seconds']['verify']} s.",
        "",
        "## Claims checked by the gate",
        "",
        f"{report['summary']['claims']} claims: {report['summary']['passed']} passed, "
        f"{report['summary']['rejected']} rejected.",
        "",
        "| Claim | Expected | Result | Where (page / sentence / lines) | Notes |",
        "|---|---|---|---|---|",
    ]
    for r in report["results"]:
        where = "; ".join(f"p{','.join(map(str, m['pages']))} / {','.join(m['sids'])} / "
                          f"{m['line_ids'][0]}–{m['line_ids'][-1]}" for m in r["matches"]) or "—"
        notes = [f"{x['code']}: {x['detail']}" for x in r["reasons"] + r["warnings"]]
        if r.get("from_table_text"):
            notes.append("from_table_text")
        lines.append(f"| `{r['claim_id']}` | {r['expect']} | **{r['status']}** | {where} | {'<br>'.join(notes) or '—'} |")
    lines += ["", "Every result above was produced by code (no AI). Rejected claims are never repaired.", ""]
    return "\n".join(lines)


if __name__ == "__main__":
    main(*sys.argv[1:4])
