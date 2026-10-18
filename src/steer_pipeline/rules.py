"""Rules check (code only): content Steer must not publish, as flags with a code and the matched text.

Two profiles, kept separate:
- claim_library: source-backed facts. Nothing blocks; every hit is a flag (a paper may name a drug).
- publication: post text. Brands, promises, before/after wording and unsourced team claims block;
  generic drug names, hype and causal wording the source does not support are flags.

Word lists come from the [rules] section of the settings and are matched as whole words or phrases,
case-insensitive; the few patterns with numbers, and the causal list shared with extraction, are fixed here. No regular expression is ever read
from settings.
"""

import re

PROFILES = ("claim_library", "publication")
# code -> action per profile
ACTIONS = {
    "DRUG_BRAND": {"claim_library": "flag", "publication": "block"},
    "DRUG_NAME": {"claim_library": "flag", "publication": "flag"},
    "PROMISE": {"claim_library": "flag", "publication": "block"},
    "HYPE": {"claim_library": "flag", "publication": "flag"},
    "STEER_TEAM_CLAIM": {"claim_library": "flag", "publication": "block"},
    "BEFORE_AFTER": {"claim_library": "flag", "publication": "block"},
    "CAUSAL_WORDING": {"claim_library": "flag", "publication": "flag"},
}
_UNIT = r"(kg|kgs|kilos?|kilograms?|lbs?|pounds?|stone|inches|cm|sizes?|dress sizes?)"
PROMISE_PATTERNS = [  # fixed, simple patterns with numbers
    re.compile(rf"\b(lose|losing|drop|dropping|shed|shedding|burn|melt)\s+(up to\s+)?\d+(\.\d+)?\s*{_UNIT}\b", re.I),
    re.compile(r"\bin (just )?\d+\s*(days?|weeks?|months?),? you('ll| will)\b", re.I),
    re.compile(rf"\b\d+(\.\d+)?\s*{_UNIT} in (just )?\d+\s*(days?|weeks?|months?)\b", re.I),
    # second person: a weight-loss outcome promised to the reader ("you could lose ...")
    re.compile(r"\byou(?:'ll| will| could| can| may| might| would)?\s+(?:easily\s+)?(?:lose|drop|shed|burn)\b", re.I),
    # percentages promised to the reader ("lose up to 15%", "lose 15% of your weight")
    re.compile(r"\b(lose|drop|shed)\s+up to\s+\d+(\.\d+)?\s*%", re.I),
    re.compile(r"\b(lose|drop|shed)\s+\d+(\.\d+)?\s*%\s+of\s+your\b", re.I),
]
# causal wording: one list for claims (extract) and posts (here)
CAUSAL = re.compile(r"(?<![\w-])(causes?|caused|causing|leads? to|led to|results? in|resulted in|makes? you|"
                    r"because of|due to|triggers?|prevents?)(?![\w-])", re.I)
STEER_TEAM = re.compile(r"\bsteer('s)?\b[^.!?]{0,80}\b(doctors?|team|members?|patients?|clients?|results?|"
                        r"success|outcomes?|programme|program)\b", re.I)


def _words(items: list[str]) -> re.Pattern | None:
    """A whole-word/phrase matcher for a literal word list (escaped: list entries are never regexes)."""
    if not items:
        return None
    alts = "|".join(re.escape(w) for w in sorted(items, key=len, reverse=True))
    return re.compile(rf"(?<![\w-])({alts})(?![\w-])", re.I)


def check(text: str, profile: str, rules: dict, source_quote: str | None = None,
          steer_source: bool = False) -> list[dict]:
    """Every rule hit in `text` as {code, profile, action, match, rules_version}.

    Steer's own brand and call-to-action lines ("Talk to a Steer doctor") must be checked with
    steer_source=True, or they block as STEER_TEAM_CLAIM.
    """
    if profile not in PROFILES:
        raise ValueError(f"profile must be one of {PROFILES}, got {profile!r}")
    hits: list[tuple[str, str]] = []

    def add(code: str, pattern: re.Pattern | None):
        if pattern:
            hits.extend((code, m.group(0)) for m in pattern.finditer(text))

    add("DRUG_BRAND", _words(rules.get("drug_brands", [])))
    add("DRUG_NAME", _words(rules.get("drug_names", [])))
    add("PROMISE", _words(rules.get("promise_words", [])))
    for p in PROMISE_PATTERNS:
        add("PROMISE", p)
    add("HYPE", _words(rules.get("hype_words", [])))
    add("BEFORE_AFTER", _words(rules.get("before_after", [])))
    if not steer_source:
        add("STEER_TEAM_CLAIM", STEER_TEAM)
    if source_quote is not None and not CAUSAL.search(source_quote):
        add("CAUSAL_WORDING", CAUSAL)  # a cause stated in the text that its source quote does not state
    return [{"code": code, "profile": profile, "action": ACTIONS[code][profile], "match": match,
             "rules_version": rules.get("version")} for code, match in hits]


def blocked(flags: list[dict]) -> bool:
    return any(f["action"] == "block" for f in flags)
