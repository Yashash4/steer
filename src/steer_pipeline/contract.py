"""The normalised document contract (schema_version "1"), shared by every input type."""

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from .assemble import assemble
from .sentences import build_sentences

SCHEMA_VERSION = "1"
SCHEMA_PATH = Path(__file__).parent / "schema" / "document-1.json"
ROLES = ("evidence", "contextual", "editorial")


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def empty_citation() -> dict:
    return {k: None for k in ("title", "journal", "year", "doi", "pmid", "pmcid", "license", "retracted")} | {"authors": []}


def build_document(
    *,
    content: bytes,
    source_type: str,
    role: str,
    access: str,
    provenance: dict,
    extraction_version: str,
    extractor: dict,
    pages: list[dict],
    citation: dict | None = None,
    warnings: list[dict] | None = None,
) -> dict:
    """Assemble a contract document from pages whose lines are in reading order.

    Each page: {page, width, height, layout, lines[]}; each line: {line_id, block,
    column, bbox, raw, furniture, para_break}. Furniture lines are kept on the page
    but left out of raw_text/text.
    """
    if role not in ROLES:
        raise ValueError(f"role must be one of {ROLES}, got {role!r}")
    body = [dict(ln, page=p["page"]) for p in pages for ln in p["lines"] if not ln["furniture"]]
    asm = assemble(body)
    digest = sha256(content)
    return {
        "schema_version": SCHEMA_VERSION,
        "extraction_version": extraction_version,
        "extractor": extractor,
        "source": {
            "source_id": "src-" + digest[:16],
            "source_type": source_type,
            "role": role,
            "access": access,
            "content_sha256": digest,
            "provenance": {"input": None, "resolved_url": None, "file_path": None, "fetched_via": None}
            | provenance
            | {"accessed_at": datetime.now(timezone.utc).isoformat(timespec="seconds")},
        },
        "citation": empty_citation() | (citation or {}),
        "pages": [
            {**p, "lines": [{k: v for k, v in ln.items() if k != "para_break"} for ln in p["lines"]]} for p in pages
        ],
        "raw_text": asm["raw_text"],
        "text": asm["text"],
        "map": asm["map"],
        "line_starts": asm["line_starts"],
        "line_order": [ln["line_id"] for ln in body],
        "sentences": build_sentences(asm, body),
        "warnings": warnings or [],
    }


def save(doc: dict, path: str | Path) -> None:
    Path(path).write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")


def load(path: str | Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))
