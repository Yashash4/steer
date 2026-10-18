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
uv run pytest -m network # also hit the live public APIs
```

## Use

```bash
uv run steer ingest paper.pdf --role evidence -o paper.json
uv run steer ingest 10.1136/bmj-2025-085304 --role evidence -o bmj.json      # DOI
uv run steer ingest https://pubmed.ncbi.nlm.nih.gov/41500720/ --role evidence -o bmj.json
uv run steer ingest protocol.md --role contextual -o protocol.json
uv run steer ingest brief.txt --role editorial -o brief.json
uv run steer verify claims.json --doc paper.json -o report.json   # exit code 1 if any claim is rejected
```

`claims.json` is a list of `{"claim_id", "text", "document_id", "quote"}`.

`--role` says what the source may be used for. Only `evidence` (a research paper) can back a claim.
`contextual` is for material like a clinical protocol, and `editorial` is for a theme brief.

## Fetching papers

A DOI, PubMed ID or URL, PMC ID or URL, or a direct PDF link is resolved like this:

1. NCBI's PMC ID Converter finds the PubMed Central ID.
2. If the article is in PMC's open-access set, it is downloaded from the
   [PMC Article Datasets on AWS](https://registry.opendata.aws/ncbi-pmc/) (public bucket `pmc-oa-opendata`).
   - The article PDF is MD5-checked against the record and then read like any PDF.
   - When PMC has no article PDF, PMC's plain-text version of the full article is used instead
     (`extraction_version` `pmc-text-1`, warning `NO_PAGE_LAYOUT`: no pages or coordinates, claims are located by sentence).
   - The licence comes from the record.
   - An article marked retracted, by PMC's record or by Europe PMC ("Retracted Publication", or a
     "Retraction in" link), is refused on every route unless `--allow-retracted` is given; then it carries a
     `RETRACTED` warning.
3. Otherwise only the abstract is taken from Europe PMC (`access: abstract_only`, warning `ABSTRACT_ONLY`).
   The abstract's HTML is parsed with an HTML parser:
   - entities are decoded and `<sup>`/`<sub>` text is kept (kg/m<sup>2</sup> → kg/m2);
   - headings become paragraph breaks;
   - a literal "<" in the science ("p < 0.00001") is never mistaken for a tag.
4. Any other URL: a PDF response is read directly. An HTML page is followed only through its `citation_doi`
   meta tag; there is no scraping.

Authors, journal and year come from Europe PMC. Errors (HTTP status, timeouts, MD5 mismatch) stop with the
URL named. Publisher websites usually block scripted downloads, which is why the public PMC route comes first.

## The document (schema version 1)

The JSON Schema is in `src/steer_pipeline/schema/document-1.json`.

- `document_id`: `doc-` plus 16 hex characters of a SHA-256 over the source ID, role and extraction version.
  Later stages key on it. The same file read with another role, or by another parser version, gets a new one.
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
  `UNCERTAIN_COLUMN_ORDER`, `NO_CITATION_METADATA`, `RETRACTED`, `TABLE_OR_FIGURE_TEXT` and `NO_PAGE_LAYOUT`.
- `extraction_version`: changes whenever the parser's output can change. Sentence IDs are only comparable
  within one extraction version.

## How PDFs are read

- Text lines come from PyMuPDF with their coordinates. Fragments on one baseline are merged left to right
  (justified words, table cells). They are never merged across a detected text-column gutter, or across a
  gap wider than a quarter of the text width. That keeps side-by-side columns apart even when the PDF
  stores them in one block. A gutter counts only for justified text columns: most lines end at one x
  with no table-like hole inside. Tables are not split into columns.
- **Columns.** A page has two columns when a group of lines starts 20–75% of the way across the text, and the
  lines starting at the left edge end before it. Lines that cross the gutter (titles, abstracts, wide
  figures) split the page into bands. Bands are read top to bottom; inside a band the left column comes
  before the right one. Each column is searched again for a column inside it, so a sidebar beside two
  columns reads as three. Wide title lines are left out of the column test, and so are number-heavy
  rows (tables). Unclear cases add `UNCERTAIN_COLUMN_ORDER` for that page instead of guessing.
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
- **Sentences.**
  - A sentence ends at `.`, `?` or `!` followed by a space and an uppercase letter, a digit or an opening
    bracket or quote; also at every paragraph break.
  - There is no split inside brackets. A bracket the source never closes within its paragraph is ignored.
  - There is no split after common abbreviations ("et al.", "e.g.", "Fig.").
  - Known limit: a single-letter word before a full stop counts as an initial, so "...in group B. Results..."
    does not split. That is the safe direction: sentences merge, and quotes still match.
  - A change of more than 1 pt in the main font size between lines (a heading) starts a new paragraph.
- Known limit: ragged-right columns stored in one block are guarded only by the gap rule above. A table row
  with a wide gap between label and values may split into two lines; reading order holds and quotes still match.
- A PDF with no extractable text fails with "OCR required". Text-free pages inside an otherwise readable
  PDF are listed as warnings.

## The verification gate

A claim passes only when **both** checks hold. A failing claim is rejected with every reason and is never
repaired. No AI is involved.

**1. The quote is in the evidence.** The document's role must be `evidence`. The quote, after the canonical
form below, must occur in the document text after the same canonical form. The quote must be contiguous
(an ellipsis is rejected). It needs at least 5 words (`--min-words`), unless it contains a number that the
claim also states. Every match is reported with its sentence IDs and lines, and `from_table_text: true`
when it comes only from table or rotated text.

The canonical form, applied in this order:
1. Unicode NFKC (ligatures, superscript digits such as "²", full-width forms).
2. Curly and prime quotes become straight quotes.
3. Every dash (U+2010–U+2015) and the minus sign (U+2212) becomes "-"; soft hyphens are removed.
4. A hyphen between two letters is removed ("follow-up" = "followup").
5. Whitespace, including line breaks, becomes single spaces.
6. Case is folded.

**2. Every number in the claim is in the quote, exactly.** A number is read as a whole token:
- comparator (`<`, `>`, `≤`, `≥`, and "less than", "more than", "at least", "up to", "under", "over", "below",
  "above");
- sign (`-` or `−`; `+` means the same as no sign; `±` is kept as a marker);
- value (thousands separators removed, including 1,35,000; `0.7` equals `0.70`);
- range (`55%–65%`, `14 to 17 years`);
- unit.

Units ignore case (mg/dL = mg/dl, kg/m² = kg/m2 = kg/m^2). "Percentage points" (`pp`) is not "%". "95% CI" is
a confidence label. A claim token must equal a quote token in comparator, sign, value(s) and unit. There is
no rounding: "about 1 in 5" never matches "21%".

"over", "under", "above" and "below" before a duration ("over 12 weeks") mean a time span, not a
comparison, so they are not comparators there.

Numbers glued to letters are identifiers, not values (GLP-1, T2D, HbA1c). The words one to ten count as
numbers. Hedges ("approximately", "about", "around", "roughly", "nearly", "~") are not part of the
comparison. When the quote hedges a number and the claim drops the hedge, the claim still passes, with
a `HEDGE_DROPPED` warning for the reviewer.

## Licence

AGPL-3.0-only (see `LICENSE`). PDF reading uses [PyMuPDF](https://github.com/pymupdf/PyMuPDF), which is
licensed under the GNU AGPL-3.0 (or a commercial licence from Artifex). Other dependencies and their
licences are listed in `THIRD_PARTY_NOTICES.md`. The test papers are CC BY 4.0; credits are in
`tests/fixtures/ATTRIBUTION.md`.
