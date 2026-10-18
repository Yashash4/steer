# Steer Content Intelligence Pipeline

> **Hackathon prototype.** Built for the Steer Club hackathon (October 2026). This is not an official
> Steer product.

The pipeline turns a research paper into a structured, traceable document. Every sentence gets a stable
ID and can be traced back to the exact line and position in the source. Later steps use those IDs to
check every claim against the paper before anything is published. No AI model is used in this stage.

## Install

Requires Python 3.12+ and [uv](https://docs.astral.sh/uv/).

```bash
cd code
uv sync
uv run pytest            # offline tests
```

## Use

```bash
uv run steer ingest paper.pdf --role evidence -o paper.json
uv run steer ingest protocol.md --role contextual -o protocol.json
uv run steer ingest brief.txt --role editorial -o brief.json
```

`--role` says what the source may be used for. Only `evidence` (a research paper) can back a claim.
`contextual` is for material like a clinical protocol, and `editorial` is for a theme brief.

## The document (schema version 1)

The JSON Schema is in `src/steer_pipeline/schema/document-1.json`.

- `source`: the ID (`src-` plus the first 16 hex characters of the SHA-256 of the input bytes), type, role,
  access (`full_text` or `abstract_only`) and provenance (input, file path or URL, access time).
- `citation`: title, authors, journal, year, DOI, PMID, PMCID, licence and retraction status, where known.
- `pages[].lines[]`: every text line, with its `line_id` (`p0002-l0031`), column, bounding box and raw text.
  Running headers, footers and page numbers are kept with `furniture: true` and left out of the text.
- `raw_text`: the body lines in reading order, unchanged.
- `text`: the reading text, with line breaks joined.
- `map`: segments `[text_start, text_end, raw_start, raw_end, op]` linking every part of `text` to `raw_text`.
- `sentences[]`: `sid` (`p0004-s0017`, numbered per page, taking the page where the sentence starts), its text,
  its spans in `text` and `raw_text`, and the lines it covers.
- `warnings[]`: limitations, never hidden. The codes are `ABSTRACT_ONLY`, `OCR_REQUIRED_PAGE`, `EMPTY_PAGE`,
  `UNCERTAIN_COLUMN_ORDER`, `NO_CITATION_METADATA`, `RETRACTED` and `TABLE_OR_FIGURE_TEXT`.
- `extraction_version`: changes whenever the parser's output can change. Sentence IDs are only comparable
  within one extraction version.

## How PDFs are read

- Text lines come from PyMuPDF with their coordinates. Fragments of one justified line are merged.
- **Columns.** A page has two columns when a group of lines starts 20–75% of the way across the text, and the
  lines starting at the left edge end before it. Lines that cross the gutter (titles, abstracts, wide
  figures) split the page into bands. Bands are read top to bottom; inside a band the left column comes
  before the right one. Unclear cases add `UNCERTAIN_COLUMN_ORDER` for that page instead of guessing.
- **Rotated text** (landscape tables) is ordered in its own upright frame, read after the page's upright text,
  and flagged `TABLE_OR_FIGURE_TEXT`.
- **Line joins.**
  - A line ending in a hyphen, with a letter before it and a lowercase letter after the break, is joined
    without the hyphen.
  - Before a digit or an uppercase letter the hyphen is kept ("GLP-" + "1RA" → "GLP-1RA").
  - Other line breaks become one space.
  - Paragraphs break on a new block with a short previous line, sentence-ending punctuation, indentation or
    a vertical gap.
  - Known limit: a real compound split across lines ("follow-" / "up") reads as "followup" in `text`. The raw
    text keeps the true form, and claim checking ignores hyphens between letters.
- A PDF with no extractable text fails with "OCR required". Text-free pages inside an otherwise readable
  PDF are listed as warnings.

## Licence

AGPL-3.0-only (see `LICENSE`). PDF reading uses [PyMuPDF](https://github.com/pymupdf/PyMuPDF), which is
licensed under the GNU AGPL-3.0 (or a commercial licence from Artifex). Other dependencies and their
licences are listed in `THIRD_PARTY_NOTICES.md`. The test papers are CC BY 4.0; credits are in
`tests/fixtures/ATTRIBUTION.md`.
