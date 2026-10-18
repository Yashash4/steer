"""The verification gate: a claim passes only if its quote is in the evidence and its numbers are in the quote.

Code only, no AI. A failing claim is rejected with every reason; it is never repaired.
Claim input: {"claim_id", "text", "document_id", "quote"}.
"""

from .assemble import raw_to_line, text_to_raw
from .normalize import canonical
from .numbers import find_numbers

GATE_VERSION = "gate-1"
MIN_WORDS = 5


class _Doc:
    """A contract document prepared for matching (canonical text built once)."""

    def __init__(self, doc: dict):
        self.doc = doc
        self.canon, self.index = canonical(doc["text"])
        self.lines = {ln["line_id"]: ln for p in doc["pages"] for ln in p["lines"]}
        self.uncertain = [w for w in doc["warnings"] if w["code"] == "UNCERTAIN_COLUMN_ORDER" and w.get("page")]

    def layout_warnings(self, line_ids: list[str]) -> list[dict]:
        """LAYOUT_CHECK_NEEDED for every page the match touches that has an unresolved reading-order warning.

        A warning with a "lines" range applies only when the match overlaps that range (reading-order
        index on the page); a warning without one applies to the whole page.
        """
        by_page: dict[int, list[int]] = {}
        for i in line_ids:
            by_page.setdefault(int(i[1:5]), []).append(int(i[-4:]))
        out = []
        for w in self.uncertain:
            idx = by_page.get(w["page"])
            if not idx:
                continue
            if "lines" in w:
                lo, hi = (int(x[-4:]) for x in w["lines"])
                if max(idx) < lo or min(idx) > hi:
                    continue
            first, last = f"p{w['page']:04d}-l{min(idx):04d}", f"p{w['page']:04d}-l{max(idx):04d}"
            out.append(_reason("LAYOUT_CHECK_NEEDED",
                               f"page {w['page']}, lines {first}-{last}: reading order uncertain ({w['detail']})"))
        return out

    def locate(self, canon_quote: str) -> list[dict]:
        out, start = [], self.canon.find(canon_quote)
        while start != -1:
            ts, te = self.index[start], self.index[start + len(canon_quote) - 1] + 1
            sids = [s["sid"] for s in self.doc["sentences"] if s["text_span"][0] < te and s["text_span"][1] > ts]
            segs, starts, order = self.doc["map"], self.doc["line_starts"], self.doc["line_order"]
            first = raw_to_line(starts, text_to_raw(segs, ts))
            last = raw_to_line(starts, text_to_raw(segs, te - 1))
            line_ids = order[first:last + 1]
            out.append({
                "text_span": [ts, te],
                "sids": sids,
                "line_ids": line_ids,
                "from_table_text": all(self.lines[i].get("table_like", False) for i in line_ids),
            })
            start = self.canon.find(canon_quote, start + 1)
        return out


def _reason(code: str, detail: str) -> dict:
    return {"code": code, "detail": detail}


def check_claim(claim: dict, docs: dict[str, _Doc], min_words: int = MIN_WORDS) -> dict:
    reasons, matches = [], []
    quote = (claim.get("quote") or "").strip()
    d = docs.get(claim.get("document_id"))
    if d is None:
        reasons.append(_reason("SOURCE_UNKNOWN", f"no document {claim.get('document_id')!r} was given"))
    elif d.doc["source"]["role"] != "evidence":
        reasons.append(_reason("SOURCE_NOT_EVIDENCE",
                               f"document role is {d.doc['source']['role']!r}; only 'evidence' can back a claim"))
    if not quote:
        reasons.append(_reason("QUOTE_EMPTY", "the claim has no quote"))
    elif "..." in quote or "…" in quote:
        reasons.append(_reason("QUOTE_ELLIPSIS", "quotes must be contiguous; an ellipsis is not allowed"))
    elif d is not None:
        matches = d.locate(canonical(quote)[0])
        if not matches:
            reasons.append(_reason("QUOTE_NOT_FOUND", "the quote does not occur in the document text"))

    claim_nums, quote_nums = find_numbers(claim.get("text", "")), find_numbers(quote)
    quote_keys = {n.key() for n in quote_nums}
    warnings = []
    for n in claim_nums:
        if n.key() in quote_keys and not n.hedged and any(q.hedged and q.key() == n.key() for q in quote_nums):
            warnings.append(_reason("HEDGE_DROPPED", f"the quote hedges {n.text!r} (for example 'approximately'); "
                                                     "the claim states it without a hedge"))
        if n.key() not in quote_keys:
            reasons.append(_reason("NUMBER_NOT_IN_QUOTE", f"{n.text!r} (value {n.low}"
                                   + (f" to {n.high}" if n.high is not None else "")
                                   + f", unit {n.unit}, comparator {n.comparator}) is not in the quote"))
    if quote and len(quote.split()) < min_words:
        claim_keys = {n.key() for n in claim_nums}
        if not any(n.key() in claim_keys for n in quote_nums):
            reasons.append(_reason("QUOTE_TOO_SHORT",
                                   f"{len(quote.split())} words (minimum {min_words}, unless it holds a number of the claim)"))

    result = {
        "claim_id": claim.get("claim_id"),
        "document_id": claim.get("document_id"),
        "status": "reject" if reasons else "pass",
        "reasons": reasons,
        "warnings": warnings,
        "matches": matches,
        "numbers_checked": [n.text for n in claim_nums],
    }
    if not reasons:
        result["from_table_text"] = all(m["from_table_text"] for m in matches)
        for m in matches:
            for w in d.layout_warnings(m["line_ids"]):
                if w not in warnings:
                    warnings.append(w)
    return result


def verify(claims: list[dict], documents: list[dict], min_words: int = MIN_WORDS) -> dict:
    docs = {doc["document_id"]: _Doc(doc) for doc in documents}
    results = [check_claim(c, docs, min_words) for c in claims]
    passed = sum(r["status"] == "pass" for r in results)
    layout = sum(r["status"] == "pass" and any(w["code"] == "LAYOUT_CHECK_NEEDED" for w in r["warnings"])
                 for r in results)
    return {
        "gate_version": GATE_VERSION,
        "min_words": min_words,
        # a pass that needs a layout check is not a verified claim until a reviewer compares it with the page
        "summary": {"claims": len(results), "passed": passed, "passed_clean": passed - layout,
                    "needs_layout_check": layout, "rejected": len(results) - passed},
        "results": results,
    }
