"""Claim extraction: a model proposes claims with exact quotes; the gate keeps only what the paper supports.

Chunks are whole sentences: one chunk per page (a sentence crossing a page break belongs to its start
page, like its sid), or blocks of CHUNK_SENTENCES for documents without pages. A chunk over CHUNK_MAX_CHARS
is split at sentence boundaries and never sent whole. Every chunk's sids are recorded so a run can show
that nothing was skipped. Claims that fail the gate are logged with its reasons and never repaired.
"""

import re
from concurrent.futures import ThreadPoolExecutor

from .normalize import canonical
from .router import Router, RouterError
from .rules import CAUSAL
from .verify import REVIEW_WARNINGS, _Doc, check_claim

CHUNK_SENTENCES = 40      # sentences per chunk when a document has no pages
CHUNK_MAX_CHARS = 30_000  # a chunk prompt above this is split; far below the CLI and API limits
CONCURRENCY = 2           # parallel model calls (the Claude subscription limit is shared)
EXCEEDS_QUOTE_RATIO = 0.30  # share of claim words missing from the quote above which the claim needs review
                            # (measured on the first live run: the lowest of 4 known overreaches scored 0.31)

CLAIM_TYPES = ["finding", "number", "limitation", "population", "method"]
DIRECTIONS = ["increase", "decrease", "no_difference", "association", "other"]
CONTEXT_FIELDS = ("outcome", "comparison_group", "time_point", "limitations")
NSTR = {"type": ["string", "null"]}
SCHEMA = {
    "type": "object",
    "required": ["claims"],
    "additionalProperties": False,
    "properties": {"claims": {"type": "array", "items": {
        "type": "object",
        "additionalProperties": False,
        "required": ["claim_text", "quote", "claim_type", "population", "hedged", "direction", *CONTEXT_FIELDS],
        "properties": {
            "claim_text": {"type": "string", "minLength": 1},
            "quote": {"type": "string", "minLength": 1},
            "claim_type": {"enum": CLAIM_TYPES},
            "population": NSTR,
            "hedged": {"type": "boolean"},
            "direction": {"enum": DIRECTIONS},
            **{f: NSTR for f in CONTEXT_FIELDS},
        },
    }}},
}
SYSTEM = """You extract claims from one section of a scientific paper for a doctor-reviewed health content library.

Rules:
- Each claim is ONE fact stated in this section, rewritten in plain English as claim_text.
- claim_text may only restate what the quote itself says: no added scope, no context taken from elsewhere
  in the section, no interpretation. If the quote does not say what a number measures, choose a longer quote
  that does, or skip the claim.
- quote must be copied character for character from the section text: one contiguous passage, no ellipsis,
  no changes, long enough to show what any number measures (a full sentence is best).
- Every number in claim_text must appear in the quote with the same value, sign, unit and comparison.
  Use digits for numbers. Never round, convert or combine numbers. Keep the sign as printed: a change
  reported as −5.4 mg/dl is written "changed by −5.4 mg/dl", never "fell by 5.4 mg/dl".
- Keep the paper's strength of language: "associated with" stays an association; never write that
  something causes something unless the quote says so. If the paper hedges ("may", "approximately"),
  set hedged to true and keep the hedge in claim_text.
- population: who was studied, as the quote or section says; null if not stated.
- outcome, comparison_group, time_point, limitations: fill ONLY when the quote itself states them,
  using the quote's own words, otherwise null. Never infer a comparison group the quote does not name.
  If claim_text compares with a group ("than ...", "compared with ..."), that group must be the quote's own
  comparison_group; if the quote names no comparison group, claim_text must not name one either.
- Skip references, author lists, funding, figure labels and table headers. Prefer results, numbers,
  limitations and populations.
- If the section has no claims worth keeping, return an empty list."""

# hedges: modal words anywhere; approximation words only directly before a number ("data about diet" is not one)
MODAL_HEDGE = re.compile(r"\b(may|might|could|suggests?|suggested|possibly|likely|appears? to|seems? to)\b", re.I)
NUMBER_HEDGE = re.compile(r"(\b(about|around|roughly|nearly|approximately)\s+|[~≈]\s*)[+\-−]?\d", re.I)
LEAN = re.compile(r"\b(lean (body )?mass|fat-?free mass)\b", re.I)
MUSCLE = re.compile(r"\bmuscles?\b", re.I)


def chunk_sentences(doc: dict) -> list[list[dict]]:
    """Whole-sentence chunks: by start page, or by CHUNK_SENTENCES when there are no pages; size-capped."""
    sents = doc["sentences"]
    one_page = len(doc["pages"]) <= 1 or any(w["code"] == "NO_PAGE_LAYOUT" for w in doc["warnings"])
    groups: list[list[dict]] = []
    if one_page:
        groups = [sents[i:i + CHUNK_SENTENCES] for i in range(0, len(sents), CHUNK_SENTENCES)]
    else:
        by_page: dict[int, list[dict]] = {}
        for s in sents:
            by_page.setdefault(s["page"], []).append(s)
        groups = [by_page[p] for p in sorted(by_page)]
    out = []
    for g in groups:
        cur, size = [], 0
        for s in g:
            if cur and size + len(s["text"]) + 1 > CHUNK_MAX_CHARS:
                out.append(cur)
                cur, size = [], 0
            cur.append(s)
            size += len(s["text"]) + 1
        if cur:
            out.append(cur)
    return out


def chunk_prompt(doc: dict, chunk: list[dict]) -> str:
    title = doc["citation"].get("title") or "untitled"
    pages = sorted({s["page"] for s in chunk})
    where = f"page {pages[0]}" if len(pages) == 1 else f"pages {pages[0]}-{pages[-1]}"
    body = " ".join(s["text"] for s in chunk)
    return f"Paper: {title}\nSection ({where}):\n\n{body}"


STOPWORDS = set("""the and for with that this from were was are been has have had its their there than then also
into over under after before which while who whom what when where why how not but all any each per can may might
could would should will one two three study studies""".split())


def _stem(w: str) -> str:
    for suf in ("ing", "ed", "es", "s", "ly"):
        if len(w) > len(suf) + 3 and w.endswith(suf):
            return w[:-len(suf)]
    return w


def _content_words(text: str) -> list[str]:
    return [_stem(w) for w in re.findall(r"[^\W\d_]{3,}", canonical(text)[0]) if w not in STOPWORDS]


DEFINITION = re.compile(r"((?:[A-Za-z][A-Za-z-]*\s+){1,8})\(([A-Z][A-Z]{1,8})s?\)")


def acronyms(text: str) -> dict[str, set[str]]:
    """The paper's own definitions, "Long Form (ABBR)": ABBR -> stemmed words of its long form.

    Only definitions whose long-form word initials spell the abbreviation count
    ("weight management medications (WMMs)" -> WMM); anything else is ignored.
    """
    out: dict[str, set[str]] = {}
    for m in DEFINITION.finditer(text):
        abbr, words = m.group(2), m.group(1).split()
        long_form = words[-len(abbr):]
        if len(long_form) == len(abbr) and "".join(w[0] for w in long_form).upper() == abbr:
            out.setdefault(abbr, set()).update(_content_words(" ".join(long_form)))
    return out


def exceeds_quote(claim_text: str, quote: str, defined: dict[str, set[str]] | None = None) -> tuple[float, list[str]]:
    """Share of the claim's content words (3+ letters, not stopwords, crudely stemmed) missing from the quote.

    A word also counts as present when the quote uses an abbreviation the paper defines with it.
    """
    claim, have = _content_words(claim_text), set(_content_words(quote))
    for abbr, words in (defined or {}).items():
        if re.search(rf"\b{abbr}s?\b", quote):
            have |= words
    missing = [w for w in claim if w not in have]
    return len(missing) / max(1, len(claim)), missing


def missing_words(value: str, text: str) -> list[str]:
    """Words of `value` (3+ letters, or numbers) that do not occur in `text`, both in canonical form.

    Word-level, so a field may reorder or paraphrase the text's own words ("lean mass as a proportion
    of total weight") but may not add any ("white men" against a quote that says "US whites").
    """
    have = set(re.findall(r"[^\W_]+", canonical(text)[0]))
    return [w for w in re.findall(r"[^\W_]+", canonical(value)[0]) if (len(w) >= 3 or w.isdigit()) and w not in have]


def quote_hedged(quote: str) -> bool:
    return bool(MODAL_HEDGE.search(quote) or NUMBER_HEDGE.search(quote))


COMPARATOR = re.compile(r"\b(?:than|compared (?:with|to)|versus|vs\.?|relative to)\s+([^,;.()\[\]]+)", re.I)


def comparator_phrases(text: str) -> list[str]:
    """The noun phrases after "than / compared with / versus / vs / relative to", up to , ; . or a bracket.

    A phrase starting with a number ("more than 30") is a comparison of values, not of groups: skipped.
    """
    return [m.group(1).strip() for m in COMPARATOR.finditer(text) if not re.match(r"[\d<>≤≥~+\-−]", m.group(1).strip())]


def _in_sequence(value: str, text: str) -> bool:
    """True when the value's content words occur next to each other, in order, among the text's content words."""
    want, have = _content_words(value), _content_words(text)
    return any(have[i:i + len(want)] == want for i in range(len(have) - len(want) + 1)) if want else True


def code_flags(claim: dict, section: str | None = None, defined: dict[str, set[str]] | None = None) -> list[dict]:
    """Flags code adds to a passed claim (never rejections): wording the quote does not support.

    `section` is the chunk text; population is the one field allowed from the section, not the quote.
    """
    text, quote = claim["claim_text"], claim["quote"]
    flags = []
    if MUSCLE.search(text) and LEAN.search(quote) and not MUSCLE.search(quote):
        flags.append({"code": "LEAN_MASS_WORDING", "detail": "the claim says muscle; the quote says lean or fat-free mass"})
    ratio, extra = exceeds_quote(text, quote, defined)
    if ratio > EXCEEDS_QUOTE_RATIO:
        flags.append({"code": "CLAIM_EXCEEDS_QUOTE",
                      "detail": f"{ratio:.0%} of the claim's words are not in the quote: {', '.join(extra[:12])}"})
    group = claim.get("comparison_group")
    for phrase in comparator_phrases(text):
        words = _content_words(phrase)
        if words and (not group or not set(words) <= set(_content_words(group))):
            flags.append({"code": "COMPARATOR_NOT_IN_QUOTE",
                          "detail": f"the claim compares with {phrase!r}; comparison_group is {group!r}"})
    if CAUSAL.search(text) and not CAUSAL.search(quote):
        flags.append({"code": "CAUSAL_WORDING",
                      "detail": f"the claim says {CAUSAL.search(text).group(0)!r}; the quote states no cause"})
    if quote_hedged(quote) and not claim["hedged"]:  # only the dangerous direction: a hedge the model missed
        flags.append({"code": "HEDGE_MISMATCH", "detail": "the quote is hedged; the model says hedged=false"})
    for f in CONTEXT_FIELDS:
        v = claim.get(f)
        if v and (missing := missing_words(v, quote)):
            flags.append({"code": "CONTEXT_NOT_IN_QUOTE", "detail": f"{f} {v!r}: {', '.join(missing)} not in the quote"})
        elif v and f == "comparison_group" and not _in_sequence(v, quote):
            # a group must appear as a phrase: "white men" is not stated by "...South Asian men ... white women"
            flags.append({"code": "CONTEXT_NOT_IN_QUOTE", "detail": f"{f} {v!r} is not stated as a phrase in the quote"})
    pop = claim.get("population")
    if pop and section is not None and (missing := missing_words(pop, section)):
        flags.append({"code": "POPULATION_NOT_IN_SECTION",
                      "detail": f"population {pop!r}: {', '.join(missing)} not found in the section"})
    return flags


def hedge_note(claim: dict) -> str | None:
    """A note (not a flag) when the model is more cautious than the quote's wording."""
    if claim["hedged"] and not quote_hedged(claim["quote"]):
        return "the model marked this hedged; the quote's wording has no hedge"
    return None


def extract(doc: dict, router: Router, concurrency: int = CONCURRENCY) -> dict:
    """Propose claims chunk by chunk, verify each against the document, and return the run record."""
    if doc["source"]["role"] != "evidence":
        raise ValueError(f"extraction needs an evidence document, got role {doc['source']['role']!r}")
    chunks = chunk_sentences(doc)
    prepared = _Doc(doc)
    defined = acronyms(doc["text"])

    def run_chunk(i: int) -> dict:
        prompt = chunk_prompt(doc, chunks[i])
        rec = {"index": i, "sids": [s["sid"] for s in chunks[i]], "chars": len(prompt)}
        if len(prompt) > CHUNK_MAX_CHARS + 2000:  # a single giant sentence: never sent, recorded instead
            return rec | {"status": "skipped_oversize", "claims": []}
        try:
            r = router.call("extract", SYSTEM, prompt, SCHEMA)
        except RouterError as e:
            return rec | {"status": "failed", "claims": [], "error": str(e)}
        return rec | {"status": "ok", "endpoint": r["endpoint"], "model": r["model"], "usage": r["usage"],
                      "claims": r["output"]["claims"]}

    with ThreadPoolExecutor(max_workers=max(1, concurrency)) as pool:
        records = list(pool.map(run_chunk, range(len(chunks))))

    passed, needs_check, rejected = [], [], []
    for rec in records:
        for k, c in enumerate(rec["claims"]):
            cid = f"c{rec['index']:03d}-{k:02d}"
            gate = check_claim({"claim_id": cid, "text": c["claim_text"], "document_id": doc["document_id"],
                                "quote": c["quote"]}, {doc["document_id"]: prepared})
            item = c | {"claim_id": cid, "chunk": rec["index"], "gate": gate}
            if gate["status"] == "reject":
                rejected.append(item)
                continue
            item["flags"] = code_flags(c, " ".join(s["text"] for s in chunks[rec["index"]]), defined)
            if note := hedge_note(c):
                item["note"] = note
            # passed means clean: no review warning from the gate and no code flag
            item["review_reasons"] = sorted({w["code"] for w in gate["warnings"]} & set(REVIEW_WARNINGS)
                                            | {f["code"] for f in item["flags"]})
            (needs_check if item["review_reasons"] else passed).append(item)
    reasons: dict[str, int] = {}
    for r in rejected:
        for x in r["gate"]["reasons"]:
            reasons[x["code"]] = reasons.get(x["code"], 0) + 1
    by_review: dict[str, int] = {}
    for c in needs_check:
        for code in c["review_reasons"]:
            by_review[code] = by_review.get(code, 0) + 1
    covered = [sid for rec in records for sid in rec["sids"]]
    return {
        "document_id": doc["document_id"],
        "chunks": [{k: v for k, v in rec.items() if k != "claims"} | {"proposed": len(rec["claims"])} for rec in records],
        "all_sentences_covered": covered == [s["sid"] for s in doc["sentences"]],
        "summary": {"proposed": sum(len(r["claims"]) for r in records), "passed": len(passed),
                    "needs_check": len(needs_check), "needs_check_by_reason": by_review,
                    "rejected": len(rejected), "rejected_by_reason": reasons,
                    "flagged": sum(bool(c["flags"]) for c in passed + needs_check),
                    "chunks_failed": sum(r["status"] != "ok" for r in records)},
        "passed": passed,
        "needs_check": needs_check,
        "rejected": rejected,
        "attempts": router.log,
    }
