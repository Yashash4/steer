"""Command line: `steer ingest <input> --role evidence -o doc.json`."""

import argparse
import sys
from pathlib import Path

from . import contract
from .pdf import OcrRequired, ingest_pdf
from .text import ingest_text


def ingest(source: str, role: str) -> dict:
    path = Path(source)
    if path.suffix.lower() in (".txt", ".md"):
        return ingest_text(path, role)
    if path.suffix.lower() == ".pdf":
        return ingest_pdf(path, role)
    raise SystemExit(f"unsupported input: {source} (supported in this version: .pdf, .txt, .md)")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="steer")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("ingest", help="turn a source into a contract document (JSON)")
    p.add_argument("source")
    p.add_argument("--role", required=True, choices=contract.ROLES)
    p.add_argument("-o", "--out", required=True)
    args = ap.parse_args(argv)

    try:
        doc = ingest(args.source, args.role)
    except OcrRequired as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    contract.save(doc, args.out)
    print(f"{doc['source']['source_id']}: {len(doc['sentences'])} sentences, "
          f"{len(doc['warnings'])} warnings -> {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
