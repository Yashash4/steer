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

    def key(self) -> tuple:
        return (self.comparator, self.plus_minus, self.low, self.high, self.unit)


def _value(digits: str, sign: str | None) -> Decimal:
    v = Decimal(digits.replace(",", ""))
    return -v if sign == "-" else v


def _words_to_digits(text: str) -> str:
    return re.sub(r"\b(" + "|".join(NUMBER_WORDS) + r")\b", lambda m: NUMBER_WORDS[m.group(1).lower()],
                  text, flags=re.IGNORECASE)


def find_numbers(text: str) -> list[Num]:
    out = []
    norm = _words_to_digits(basic(text))
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
        if m["comp"] and m["comp"].lower() in DURATION_WORDS and (unit or "").split("..")[0] in TIME_UNITS:
            comp = None
        low = _value(m["n1"], sign if sign != "±" else None)
        high = _value(m["n2"], m["sign2"]) if m["n2"] else None
        hedged = bool(HEDGE.search(norm[max(0, m.start() - 20):m.start()]))
        out.append(Num(comp, sign == "±", low, high, unit, m.group(0).strip(), hedged))
    return out
