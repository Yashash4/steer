# Sample run: paper to checked claims

**Paper:** Narayan KMV, Kondal D, Daya N et al. *Incidence and pathophysiology of diabetes in South Asian adults living in India and Pakistan compared with US blacks and whites* BMJ open diabetes research & care, 2021. DOI 10.1136/bmjdrc-2020-001927 (PMID 33771764, PMC8006839). Licence CC BY-NC; retracted: False.
**Fetched from:** https://pmc-oa-opendata.s3.amazonaws.com/PMC8006839.1/PMC8006839.1.pdf

## Ingest

- 10 pages (6 two_column, 2 mixed, 2 single), 428 sentences, extraction `pdf-1` (pymupdf 1.28.2).
- Warnings: UNCERTAIN_COLUMN_ORDER x3, TABLE_OR_FIGURE_TEXT x10.
- Time (measured by the script): fetch + ingest 13.497 s, verify 0.013 s.

## Claims checked by the gate

7 claims: 4 passed (0 clean; 4 need a layout check, 0 short quote, 1 dropped hedge: not verified until a reviewer looks), 3 rejected.

| Claim | Expected | Result | Where (page / sentence / lines) | Notes |
|---|---|---|---|---|
| `a-exact-sentence` | pass | **pass** | p1 / p0001-s0027 / p0001-l0077–p0001-l0081 | LAYOUT_CHECK_NEEDED: page 1, lines p0001-l0077-p0001-l0081: reading order uncertain (2 possible column splits; ordered by the strongest)<br>LAYOUT_CHECK_NEEDED: page 1, lines p0001-l0077-p0001-l0081: reading order uncertain (inner column split in the right column, y 23-742) |
| `b-invented-quote` | reject: QUOTE_NOT_FOUND (the quote is invented) | **reject** | — | QUOTE_NOT_FOUND: the quote does not occur in the document text |
| `c-changed-number` | reject: NUMBER_NOT_IN_QUOTE | **reject** | p1 / p0001-s0027 / p0001-l0079–p0001-l0081 | NUMBER_NOT_IN_QUOTE: '3.5' (value 3.5, unit None, comparator None) is not in the quote |
| `d-dropped-hedge` | pass with HEDGE_DROPPED | **pass** | p1 / p0001-s0043 / p0001-l0117–p0001-l0118 | HEDGE_DROPPED: the quote hedges '30%' (for example 'approximately'); the claim states it without a hedge<br>LAYOUT_CHECK_NEEDED: page 1, lines p0001-l0117-p0001-l0118: reading order uncertain (2 possible column splits; ordered by the strongest)<br>LAYOUT_CHECK_NEEDED: page 1, lines p0001-l0117-p0001-l0118: reading order uncertain (inner column split in the right column, y 23-742) |
| `e-table-row` | reject: NUMBER_NOT_IN_QUOTE (1000 is only in the table title) | **reject** | p6 / p0006-s0018 / p0006-l0020–p0006-l0020 | NUMBER_NOT_IN_QUOTE: '1000' (value 1000, unit None, comparator None) is not in the quote |
| `f-table-row-numbers-only` | pass, from_table_text true | **pass** | p6 / p0006-s0018 / p0006-l0020–p0006-l0020 | LAYOUT_CHECK_NEEDED: page 6, lines p0006-l0020-p0006-l0020: reading order uncertain (number-dense page: single-column reading not confirmed)<br>from_table_text |
| `g-abstract-men-vs-white-men` | pass | **pass** | p1 / p0001-s0025 / p0001-l0071–p0001-l0072 | LAYOUT_CHECK_NEEDED: page 1, lines p0001-l0071-p0001-l0072: reading order uncertain (2 possible column splits; ordered by the strongest)<br>LAYOUT_CHECK_NEEDED: page 1, lines p0001-l0071-p0001-l0072: reading order uncertain (inner column split in the right column, y 23-742) |

Every result above was produced by code (no AI). Rejected claims are never repaired.
