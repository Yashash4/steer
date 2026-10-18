"""Find numbers in a claim or quote, with sign, comparator, range and unit, for exact comparison.

A number is checked as a whole token: two tokens are equal only when the comparator, the "±"
marker, both signed values (Decimal, so 0.7 == 0.70) and the unit are all equal. Numbers glued
to letters ("GLP-1", "T2D", "HbA1c") are identifiers, not values, and are skipped.
"""

import re
from decimal import Decimal
from typing import NamedTuple

from .normalize import basic

NUMBER_WORDS = {w: str(i) for i, w in enumerate(
    ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten"])}
COMPARATORS = {
    "<=": "≤", "≤": "≤", "up to": "≤",
    ">=": "≥", "≥": "≥", "at least": "≥",
    "<": "<", "less than": "<", "fewer than": "<", "under": "<", "below": "<",
    ">": ">", "more than": ">", "greater than": ">", "over": ">", "above": ">",
}
UNITS = {  # spelling (lowercase) -> canonical unit; case is ignored for every unit
    "percentage points": "pp", "percentage point": "pp", "pp": "pp",
    "per cent": "%", "percent": "%", "%": "%",
    "kg/m^2": "kg/m2", "kg/m2": "kg/m2",
    "mg/dl": "mg/dl", "mmol/l": "mmol/l", "mmol/mol": "mmol/mol", "pmol/ml": "pmol/ml", "mmhg": "mmhg",
    "kcal": "kcal", "kg": "kg", "mg": "mg", "μg": "μg", "g": "g", "lb": "lb", "lbs": "lb", "oz": "oz",
    "cm": "cm", "mm": "mm", "ml": "ml", "iu": "iu",
    "years": "year", "year": "year", "yrs": "year", "yr": "year",
    "months": "month", "month": "month", "weeks": "week", "week": "week", "wks": "week", "wk": "week",
    "days": "day", "day": "day", "hours": "hour", "hour": "hour", "hrs": "hour", "h": "hour", "min": "min",
}
TIME_UNITS = {"year", "month", "week", "day", "hour", "min"}
DURATION_WORDS = {"over", "under", "above", "below"}  # "over 12 weeks" is a duration, not "> 12 weeks"
HEDGE = re.compile(r"(?:\b(?:approximately|about|around|roughly|nearly)|~|≈)\s*$", re.IGNORECASE)
_unit_alt = "|".join(re.escape(u) for u in sorted(UNITS, key=len, reverse=True))
_comp_alt = "|".join(  # symbols anywhere ("p<0.05"); words only as whole words ("over", not "turnover")
    re.escape(c) if not c[0].isalpha() else rf"(?<![A-Za-z]){re.escape(c)}"
    for c in sorted(COMPARATORS, key=len, reverse=True))
_num = r"\d{1,3}(?:,\d{2,3})+(?:\.\d+)?(?![\d,]\d)|\d+(?:\.\d+)?|\.\d+"
TOKEN = re.compile(
    rf"(?:(?P<comp>{_comp_alt})\s?)?"
    rf"(?P<sign>±|\+/-|[+-])?\s?"
    rf"(?<![\w.])(?<![A-Za-z]-)(?P<n1>{_num})"
    rf"(?:\s?(?P<u1>{_unit_alt})(?![A-Za-z]))?"
    rf"(?:\s?(?:-|\bto\b)\s?(?P<sign2>[+-])?(?P<n2>{_num})(?:\s?(?P<u2>{_unit_alt})(?![A-Za-z]))?)?"
    rf"(?P<ci>\s?CI\b)?"
    rf"(?P<post>\s(?:or|and) (?:older|more|above|over|greater|higher|younger|less|below|under|fewer|lower)\b)?"
    rf"(?![\w])",
    re.IGNORECASE,
)


class Num(NamedTuple):
    comparator: str | None
    plus_minus: bool
    low: Decimal
    high: Decimal | None
    unit: str | None
    text: str
    hedged: bool = False  # "approximately", "about", "~"... right before the number (not part of equality)
    alt_comparator: str | None = None  # "under 52 weeks": also equal to "<52 weeks" (a duration word)

    def key(self) -> tuple:
        return (self.comparator, self.plus_minus, self.low, self.high, self.unit)

    def keys(self) -> set[tuple]:
        """Every key this token is equal under: its own, and the comparator form of a duration word."""
        k = {self.key()}
        if self.alt_comparator:
            k.add((self.alt_comparator, *self.key()[1:]))
        return k


def _value(digits: str, sign: str | None) -> Decimal:
    v = Decimal(digits.replace(",", ""))
    return -v if sign == "-" else v


_PRONOUN_ONE = re.compile(r"\b(?:any|no|every|each)\s+one\b", re.IGNORECASE)   # "one of" stays a number (checks.PRONOUNS)


def _words_to_digits(text: str) -> str:
    """Number words as digits, except "one" used as a pronoun ("any one person", "no one"): not a count."""
    keep = [m.span() for m in _PRONOUN_ONE.finditer(text)]
    return re.sub(r"\b(" + "|".join(NUMBER_WORDS) + r")\b",
                  lambda m: m.group(1) if any(a <= m.start() < b for a, b in keep) else NUMBER_WORDS[m.group(1).lower()],
                  text, flags=re.IGNORECASE)


POSTFIX = {"older": "≥", "more": "≥", "above": "≥", "over": "≥", "greater": "≥", "higher": "≥",
           "younger": "≤", "less": "≤", "below": "≤", "under": "≤", "fewer": "≤", "lower": "≤"}
_n = r"[+-]?\d+(?:\.\d+)?"
# exact notation equivalences, applied to claim and quote alike:
CI_WORDS = re.compile(r"confidence intervals?(?:\s*\(CI\))?", re.I)           # "95% confidence interval" = "95% CI"
# "(1.56, 2.74)" right after a number (optionally with its unit: "0.7 mmol/L (0.1, 1.3)") or after "CI"
CI_PAIR = re.compile(rf"((?:\d(?:\s?[^\s\d(),;]{{1,10}})?|CI:?)\s*\(\s*|CI:?\s+)({_n})\s*,\s*({_n})(?=\s*[);])")


def equivalent_notation(text: str) -> str:
    text = CI_WORDS.sub("CI", text)
    return CI_PAIR.sub(lambda m: f"{m.group(1)}{m.group(2)} to {m.group(3)}", text)


def find_numbers(text: str) -> list[Num]:
    out = []
    norm = equivalent_notation(_words_to_digits(basic(text)))
    for m in TOKEN.finditer(norm):
        sign = m["sign"]
        if sign and sign.startswith("+/"):
            sign = "±"
        u1, u2 = (UNITS[u.lower()] if u else None for u in (m["u1"], m["u2"]))
        unit = u2 or u1
        if m["n2"] and u1 and u2 and u1 != u2:
            unit = f"{u1}..{u2}"  # mixed units in a range never match a single unit
        if m["ci"]:
            unit = f"{unit or ''} ci"
        comp = COMPARATORS[m["comp"].lower()] if m["comp"] else None
        alt = None
        if m["comp"] and m["comp"].lower() in DURATION_WORDS and (unit or "").split("..")[0] in TIME_UNITS:
            comp, alt = None, comp  # "over 12 weeks" equals "12 weeks", and also ">12 weeks"
        if m["post"] and comp is None:
            comp = POSTFIX[m["post"].split()[-1].lower()]  # "45 years or older" equals "≥45 years"
        low = _value(m["n1"], sign if sign != "±" else None)
        high = _value(m["n2"], m["sign2"]) if m["n2"] else None
        hedged = bool(HEDGE.search(norm[max(0, m.start() - 20):m.start()]))
        out.append(Num(comp, sign == "±", low, high, unit, m.group(0).strip(), hedged, alt))
    return out
