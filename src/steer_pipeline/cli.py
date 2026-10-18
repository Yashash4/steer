"""Command line: `steer ingest <input> --role evidence -o doc.json` and `steer verify claims.json --doc doc.json -o report.json`."""

import argparse
import sys
from pathlib import Path

import json

from . import contract, verify
from .fetch import FetchError, fetch
from .pdf import OcrRequired, ingest_pdf
from .text import ingest_text


def ingest(source: str, role: str, allow_retracted: bool = False) -> dict:
    path = Path(source)
    if not path.exists():
        return fetch(source, role, allow_retracted=allow_retracted)  # DOI, PubMed/PMC ID or URL
    if path.suffix.lower() in (".txt", ".md"):
        return ingest_text(path, role)
    if path.suffix.lower() == ".pdf":
        return ingest_pdf(path, role)
    raise SystemExit(f"unsupported input: {source} (files: .pdf, .txt, .md; or a DOI, PubMed/PMC ID or URL)")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="steer")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("ingest", help="turn a source into a contract document (JSON)")
    p.add_argument("source")
    p.add_argument("--role", required=True, choices=contract.ROLES)
    p.add_argument("-o", "--out", required=True)
    p.add_argument("--allow-retracted", action="store_true", help="ingest a paper PMC marks as retracted (warned)")
    v = sub.add_parser("verify", help="check claims against contract documents")
    v.add_argument("claims", help="JSON list of {claim_id, text, document_id, quote}")
    v.add_argument("--doc", action="append", required=True, help="a contract document (repeatable)")
    v.add_argument("--min-words", type=int, default=verify.MIN_WORDS)
    v.add_argument("-o", "--out", required=True)
    args = ap.parse_args(argv)

    if args.cmd == "verify":
        claims = json.loads(Path(args.claims).read_text(encoding="utf-8"))
        report = verify.verify(claims, [contract.load(d) for d in args.doc], args.min_words)
        Path(args.out).write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
        s = report["summary"]
        print(f"{s['claims']} claims: {s['passed']} passed ({s['passed_clean']} clean, {s['needs_layout_check']} need a "
              f"layout check, {s['short_quote']} short quote), {s['rejected']} rejected -> {args.out}", file=sys.stderr)
        return 0 if s["rejected"] == 0 else 1

    try:
        doc = ingest(args.source, args.role, args.allow_retracted)
    except (OcrRequired, FetchError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    contract.save(doc, args.out)
    print(f"{doc['source']['source_id']}: {len(doc['sentences'])} sentences, "
          f"{len(doc['warnings'])} warnings -> {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
