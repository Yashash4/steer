"""Join extracted lines into reading text while keeping a map back to the raw text.

Input lines are already in reading order. Each line is a dict with at least
`line_id`, `page`, `raw` and `para_break` (True when a new paragraph starts at
this line). `raw_text` is the lines' raw strings joined by "\n", unchanged.

`map` holds segments [text_start, text_end, raw_start, raw_end, op]:
  copy         text and raw are identical over the span (same length)
  join_space   one space in text stands for the raw line break
  dehyphenate  nothing in text stands for the raw "-\n"
  keep_hyphen  nothing in text stands for the raw "\n" after a kept hyphen
  paragraph    "\n" in text stands for the raw line break
"""

from bisect import bisect_right

HYPHENS = ("-", "‐", "­")


def join_op(line: str, next_line: str) -> str:
    """How to join the end of `line` to the start of `next_line` (same paragraph)."""
    if line and line[-1] in HYPHENS:
        before = line[-2] if len(line) > 1 else ""
        after = next_line[:1]
        if before.isalpha() and after.islower():
            return "dehyphenate"
        if after.isdigit() or after.isupper():
            return "keep_hyphen"
    return "join_space"


def assemble(lines: list[dict]) -> dict:
    raw_parts, text_parts, segs, line_starts = [], [], [], []
    raw_pos = text_pos = 0
    for i, ln in enumerate(lines):
        raw = ln["raw"]
        line_starts.append(raw_pos)
        body = raw.rstrip()
        lead = len(body) - len(body.lstrip())
        body = body.lstrip()
        nxt = lines[i + 1] if i + 1 < len(lines) else None
        op = None
        if nxt is not None:
            op = "paragraph" if nxt["para_break"] else join_op(body, nxt["raw"].strip())
        keep = body[:-1] if op == "dehyphenate" else body
        if keep:
            segs.append([text_pos, text_pos + len(keep), raw_pos + lead, raw_pos + lead + len(keep), "copy"])
            text_parts.append(keep)
            text_pos += len(keep)
        line_end = raw_pos + len(raw)  # index of the "\n" after this line in raw_text
        if op == "dehyphenate":
            segs.append([text_pos, text_pos, raw_pos + lead + len(keep), line_end + 1, op])
        elif op == "keep_hyphen":
            segs.append([text_pos, text_pos, line_end, line_end + 1, op])
        elif op in ("join_space", "paragraph"):
            ch = " " if op == "join_space" else "\n"
            if text_parts and text_parts[-1].endswith((" ", "\n")):
                ch = ""  # an empty line body must not double the separator
            segs.append([text_pos, text_pos + len(ch), line_end, line_end + 1, op])
            text_parts.append(ch)
            text_pos += len(ch)
        raw_parts.append(raw)
        raw_pos = line_end + 1
    return {
        "raw_text": "\n".join(raw_parts),
        "text": "".join(text_parts),
        "map": segs,
        "line_starts": line_starts,
    }


def text_to_raw(segs: list[list], pos: int) -> int:
    """Raw offset for a text offset (start of the segment for non-copy ops)."""
    starts = [s[0] for s in segs]
    k = bisect_right(starts, pos) - 1
    while k > 0 and segs[k][0] == segs[k][1] and segs[k][0] == pos and segs[k][4] != "copy":
        k -= 1  # prefer the copy segment that contains pos over zero-width joins at it
    ts, te, rs, re, op = segs[max(k, 0)]
    if op == "copy":
        return rs + min(pos - ts, re - rs)
    return rs


def raw_to_line(line_starts: list[int], raw_pos: int) -> int:
    return max(bisect_right(line_starts, raw_pos) - 1, 0)
