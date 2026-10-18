"""Plain-text input: theme briefs and clinical protocols (one page, no coordinates)."""

from pathlib import Path

from .contract import build_document

EXTRACTION_VERSION = "text-1"


def text_lines(text: str) -> list[dict]:
    """One contract line per non-blank input line; blank lines start a new paragraph."""
    lines, block, gap = [], 0, False
    for raw in text.splitlines():
        if not raw.strip():
            gap = True
            continue
        if gap and lines:
            block += 1
        lines.append({
            "line_id": f"p0001-l{len(lines) + 1:04d}",
            "block": block,
            "column": "full",
            "bbox": None,
            "raw": raw,
            "furniture": False,
            "para_break": gap and len(lines) > 0,
        })
        gap = False
    return lines


def ingest_text(path: str | Path, role: str, title: str | None = None) -> dict:
    path = Path(path)
    content = path.read_bytes()
    lines = text_lines(content.decode("utf-8-sig"))
    warnings = [] if lines else [{"code": "EMPTY_PAGE", "page": 1, "detail": "the text input has no content"}]
    return build_document(
        content=content,
        source_type="text",
        role=role,
        access="full_text",
        provenance={"input": str(path), "file_path": str(path.resolve()), "fetched_via": "file"},
        extraction_version=EXTRACTION_VERSION,
        extractor={"name": "steer_pipeline.text", "version": EXTRACTION_VERSION},
        pages=[{"page": 1, "width": None, "height": None, "layout": "single" if lines else "empty", "lines": lines}],
        citation={"title": title or path.stem},
        warnings=warnings,
    )
