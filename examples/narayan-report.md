# Sample run: paper to checked claims

**Paper:** Narayan KMV, Kondal D, Daya N et al. *Incidence and pathophysiology of diabetes in South Asian adults living in India and Pakistan compared with US blacks and whites* BMJ open diabetes research & care, 2021. DOI 10.1136/bmjdrc-2020-001927 (PMID 33771764, PMC8006839). Licence CC BY-NC; retracted: False.
**Fetched from:** https://pmc-oa-opendata.s3.amazonaws.com/PMC8006839.1/PMC8006839.1.pdf

## Ingest

- 10 pages (6 two_column, 2 mixed, 2 single), 409 sentences, extraction `pdf-1` (pymupdf 1.28.2).
- Warnings: UNCERTAIN_COLUMN_ORDER x1, TABLE_OR_FIGURE_TEXT x10.
- Time (measured by the script): fetch + ingest 3.879 s, verify 0.013 s.

## Claims checked by the gate

7 claims: 4 passed, 3 rejected.

| Claim | Expected | Result | Where (page / sentence / lines) | Notes |
|---|---|---|---|---|
| `a-exact-sentence` | pass | **pass** | p1 / p0001-s0027 / p0001-l0077–p0001-l0081 | — |
| `b-wrong-comparison-group` | reject: QUOTE_NOT_FOUND | **reject** | — | QUOTE_NOT_FOUND: the quote does not occur in the document text |
| `c-changed-number` | reject: NUMBER_NOT_IN_QUOTE | **reject** | p1 / p0001-s0027 / p0001-l0079–p0001-l0081 | NUMBER_NOT_IN_QUOTE: '3.5' (value 3.5, unit None, comparator None) is not in the quote |
| `d-dropped-hedge` | pass with HEDGE_DROPPED | **pass** | p1 / p0001-s0043 / p0001-l0117–p0001-l0118 | HEDGE_DROPPED: the quote hedges '30%' (for example 'approximately'); the claim states it without a hedge |
| `e-table-row` | reject: NUMBER_NOT_IN_QUOTE (1000 is only in the table title) | **reject** | p6 / p0006-s0018 / p0006-l0020–p0006-l0020 | NUMBER_NOT_IN_QUOTE: '1000' (value 1000, unit None, comparator None) is not in the quote |
| `f-table-row-numbers-only` | pass, from_table_text true | **pass** | p6 / p0006-s0018 / p0006-l0020–p0006-l0020 | from_table_text |
| `g-abstract-men-vs-white-men` | pass | **pass** | p1 / p0001-s0025 / p0001-l0071–p0001-l0072 | — |

Every result above was produced by code (no AI). Rejected claims are never repaired.
