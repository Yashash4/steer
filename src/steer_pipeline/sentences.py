"""Deterministic, rule-based sentence splitting with stable IDs (pPPPP-sSSSS)."""

from .assemble import raw_to_line, text_to_raw

ABBREVIATIONS = {
    "al", "e.g", "i.e", "vs", "fig", "figs", "ref", "refs", "no", "dr", "approx", "ca", "eq", "vol", "etc", "cf",
}
OPENERS = "\"'(["
END = ".?!"


def _is_abbreviation(text: str, dot: int) -> bool:
    start = dot
    while start > 0 and not text[start - 1].isspace():
        start -= 1
    word = text[start:dot].lstrip("([\"'").lower()
    return word in ABBREVIATIONS or (len(word) == 1 and word.isalpha())


def _unmatched_openers(text: str) -> set[int]:
    """Positions of "(" or "[" that are never closed within their paragraph (a typo in the source).

    They are ignored when deciding sentence ends, so one missing ")" cannot merge many sentences.
    """
    unmatched, stack = set(), []
    for i, ch in enumerate(text):
        if ch in "([":
            stack.append(i)
        elif ch in ")]" and stack:
            stack.pop()
        elif ch == "\n":
            unmatched.update(stack)
            stack = []
    unmatched.update(stack)
    return unmatched


def split_spans(text: str) -> list[tuple[int, int]]:
    """Sentence spans [start, end) over `text`, whitespace-trimmed, never empty."""
    unmatched = _unmatched_openers(text)
    spans, start, depth = [], 0, 0
    for i, ch in enumerate(text):
        if ch in "([" and i not in unmatched:
            depth += 1
        elif ch in ")]":
            depth = max(depth - 1, 0)
        cut = None
        if ch == "\n":
            cut, depth = i, 0
        elif ch in END and depth == 0 and i + 2 < len(text) and text[i + 1] == " ":
            nxt = text[i + 2]
            if (nxt.isupper() or nxt.isdigit() or nxt in OPENERS) and not (ch == "." and _is_abbreviation(text, i)):
                cut = i + 1
        if cut is not None:
            spans.append((start, cut))
            start = cut
    spans.append((start, len(text)))
    out = []
    for s, e in spans:
        while s < e and text[s].isspace():
            s += 1
        while e > s and text[e - 1].isspace():
            e -= 1
        if e > s:
            out.append((s, e))
    return out


def build_sentences(assembled: dict, lines: list[dict]) -> list[dict]:
    text, segs, starts = assembled["text"], assembled["map"], assembled["line_starts"]
    per_page: dict[int, int] = {}
    out = []
    for s, e in split_spans(text):
        rs, re = text_to_raw(segs, s), text_to_raw(segs, e - 1) + 1
        first, last = raw_to_line(starts, rs), raw_to_line(starts, re - 1)
        page = lines[first]["page"]
        n = per_page[page] = per_page.get(page, 0) + 1
        out.append({
            "sid": f"p{page:04d}-s{n:04d}",
            "page": page,
            "text": text[s:e],
            "text_span": [s, e],
            "raw_span": [rs, re],
            "line_ids": [lines[k]["line_id"] for k in range(first, last + 1)],
        })
    return out
