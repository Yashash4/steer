"""The canonical form the verification gate compares quotes in (documented in the README).

Applied to both the quote and the source text, in this order:
1. Unicode NFKC (ligatures, superscript digits, full-width forms).
2. Curly and prime quotes to straight quotes.
3. Every dash (U+2010-U+2015) and the minus sign (U+2212) to "-"; soft hyphens removed.
4. A hyphen between two letters removed ("follow-up" = "followup").
5. All whitespace runs to one space; leading and trailing space dropped.
6. Case folded.

`canonical()` also returns, for every canonical character, the index of the source
character it came from, so a match can be traced back to the original text.
"""

import unicodedata

QUOTES = {"‘": "'", "’": "'", "‚": "'", "‛": "'", "′": "'",
          "“": '"', "”": '"', "„": '"', "‟": '"', "″": '"'}
DASHES = {chr(c): "-" for c in range(0x2010, 0x2016)} | {"−": "-"}
SOFT_HYPHEN = "­"


def basic(text: str) -> str:
    """Steps 1-3 only: the form numbers are parsed from (the minus sign survives as "-")."""
    out = []
    for ch in text:
        if ch == SOFT_HYPHEN:
            continue
        for c in unicodedata.normalize("NFKC", ch):
            out.append(DASHES.get(c, QUOTES.get(c, c)))
    return "".join(out)


def canonical(text: str) -> tuple[str, list[int]]:
    chars: list[str] = []
    index: list[int] = []
    for i, ch in enumerate(text):
        if ch == SOFT_HYPHEN:
            continue
        for c in unicodedata.normalize("NFKC", ch):
            c = DASHES.get(c, QUOTES.get(c, c))
            if c.isspace():
                if chars and chars[-1] != " ":
                    chars.append(" ")
                    index.append(i)
                continue
            for f in c.casefold():
                chars.append(f)
                index.append(i)
    # step 4: drop a hyphen between two letters
    keep = [not (c == "-" and 0 < k < len(chars) - 1 and chars[k - 1].isalpha() and chars[k + 1].isalpha())
            for k, c in enumerate(chars)]
    chars = [c for c, ok in zip(chars, keep) if ok]
    index = [i for i, ok in zip(index, keep) if ok]
    while chars and chars[-1] == " ":
        chars.pop()
        index.pop()
    while chars and chars[0] == " ":
        chars.pop(0)
        index.pop(0)
    return "".join(chars), index
