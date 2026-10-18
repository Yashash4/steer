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
from .verify import REVIEW_WARNINGS, _Doc, check_claim

CHUNK_SENTENCES = 40      # sentences per chunk when a document has no pages
CHUNK_MAX_CHARS = 30_000  # a chunk prompt above this is split; far below the CLI and API limits
CONCURRENCY = 2           # parallel model calls (the Claude subscription limit is shared)

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
  otherwise null. Never infer a comparison group the quote does not name.
- Skip references, author lists, funding, figure labels and table headers. Prefer results, numbers,
  limitations and populations.
- If the section has no claims worth keeping, return an empty list."""

CAUSAL = re.compile(r"\b(causes?|caused|causing|leads? to|led to|results? in|resulted in|makes? you|because of|"
                    r"due to|triggers?|prevents?)\b", re.I)
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


def quote_hedged(quote: str) -> bool:
    return bool(MODAL_HEDGE.search(quote) or NUMBER_HEDGE.search(quote))


def code_flags(claim: dict, section: str | None = None) -> list[dict]:
    """Flags code adds to a passed claim (never rejections): wording the quote does not support.

    `section` is the chunk text; population is the one field allowed from the section, not the quote.
    """
    text, quote = claim["claim_text"], claim["quote"]
    flags = []
    if MUSCLE.search(text) and LEAN.search(quote) and not MUSCLE.search(quote):
        flags.append({"code": "LEAN_MASS_WORDING", "detail": "the claim says muscle; the quote says lean or fat-free mass"})
    if CAUSAL.search(text) and not CAUSAL.search(quote):
        flags.append({"code": "CAUSAL_WORDING",
                      "detail": f"the claim says {CAUSAL.search(text).group(0)!r}; the quote states no cause"})
    if quote_hedged(quote) and not claim["hedged"]:  # only the dangerous direction: a hedge the model missed
        flags.append({"code": "HEDGE_MISMATCH", "detail": "the quote is hedged; the model says hedged=false"})
    cq = canonical(quote)[0]
    for f in CONTEXT_FIELDS:
        v = claim.get(f)
        if v and canonical(v)[0] not in cq:
            flags.append({"code": "CONTEXT_NOT_IN_QUOTE", "detail": f"{f} {v!r} is not stated in the quote"})
    pop = claim.get("population")
    if pop and section is not None:
        # word-level: every word of 3+ letters (and every number) in the population must occur in the section,
        # so "South Asian men with BMI <25 kg/m2" passes a section that says "BMI <25 kg/m2 ... South Asian men"
        sec = set(re.findall(r"[^\W_]+", canonical(section)[0]))
        missing = [w for w in re.findall(r"[^\W_]+", canonical(pop)[0]) if (len(w) >= 3 or w.isdigit()) and w not in sec]
        if missing:
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
            item["flags"] = code_flags(c, " ".join(s["text"] for s in chunks[rec["index"]]))
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
