"""The writer's contract: post briefs and the JSON schema every model-written post must satisfy.

Every text line has a kind. health lines state a fact and must name claim ids from the brief; framing,
brand, cta and signoff lines carry none. There is no "source" kind: sources are built by code, never by
the model. The reel script has ONE list of lines per scene, and every line is both spoken and shown, so the
voice can only say what the screen says.
"""

import jsonschema

from .checks import split_rule, frags

KINDS = ["health", "framing", "brand", "cta", "signoff"]
SLIDE_ROLES = ["hook", "point", "meaning", "brand", "cta"]


def _line(claim_ids: list[str], extra: dict | None = None) -> dict:
    props = {"kind": {"enum": KINDS}, "text": {"type": "string", "minLength": 1, "maxLength": 300},
             "claim_ids": {"type": "array", "items": {"enum": claim_ids}, "uniqueItems": True}}
    return {
        "type": "object", "required": ["kind", "text", "claim_ids"], "additionalProperties": False,
        "properties": props | (extra or {}),
        "allOf": [  # health lines name at least one claim of the brief; other kinds name none
            {"if": {"properties": {"kind": {"const": "health"}}},
             "then": {"properties": {"claim_ids": {"minItems": 1}}},
             "else": {"properties": {"claim_ids": {"maxItems": 0}}}},
        ],
    }


def post_schema(brief: dict, hashtags: list[str]) -> dict:
    ids = brief["claim_ids"]
    line = _line(ids)
    lines = {"type": "array", "items": line, "minItems": 1}
    # the brief fixes the format, so only that artwork block exists (no top-level allOf: the API refuses it)
    artwork = "slides" if brief["format"] == "carousel" else "static"
    blocks = {
        "slides": {"type": "array", "minItems": 4, "maxItems": 7, "items": {
            "type": "object", "required": ["role", "lines"], "additionalProperties": False,
            "properties": {"role": {"enum": SLIDE_ROLES}, "lines": lines}}},
        "static": {"type": "object", "required": ["headline", "supporting"], "additionalProperties": False,
                   "properties": {"headline": line, "supporting": {"type": "array", "items": line, "maxItems": 2}}},
    }
    return {
        "type": "object",
        "required": ["message", "reasoning", "format", artwork, "caption", "reel_script"],
        "additionalProperties": False,
        "properties": {
            "message": {"type": "string", "minLength": 1, "maxLength": 300},
            "reasoning": {"type": "string", "minLength": 1, "maxLength": 1200},
            "format": {"const": brief["format"]},
            artwork: blocks[artwork],
            "caption": {"type": "object", "required": ["lines", "hashtags"], "additionalProperties": False,
                        "properties": {"lines": lines,
                                       "hashtags": {"type": "array", "items": {"enum": hashtags}, "minItems": 5,
                                                    "maxItems": 10, "uniqueItems": True}}},
            "reel_script": {"type": "object", "required": ["scenes"], "additionalProperties": False,
                            "properties": {"scenes": {"type": "array", "minItems": 3, "maxItems": 10, "items": {
                                "type": "object", "required": ["lines"], "additionalProperties": False,
                                "properties": {"lines": {"type": "array", "minItems": 1,
                                                         "items": _line(ids, {"show_large": {"type": "boolean"}})}}}}}},
        },
    }


def validate_post(post: dict, brief: dict, hashtags: list[str]) -> list[str]:
    """Every schema violation as a readable message (empty when the post fits the contract)."""
    v = jsonschema.Draft202012Validator(post_schema(brief, hashtags))
    return sorted(f"{'/'.join(map(str, e.absolute_path)) or '(post)'}: {e.message}" for e in v.iter_errors(post))


def pick_brief(claims: list[dict], fmt: str, n: int = 4) -> dict:
    """The theme x doubt cell with the most writable claims; the reason is recorded.

    `claims` are writable (verified) claims with labels {theme, doubts}. Ties go to the cell named first
    in sorted order, so the choice is deterministic.
    """
    cells: dict[tuple[str, str | None], list[str]] = {}
    for c in claims:
        lab = c.get("labels") or {}
        if not lab.get("theme"):
            continue
        for d in lab.get("doubts") or [None]:
            cells.setdefault((lab["theme"], d), []).append(c["claim_id"])
    if not cells:
        raise ValueError("no writable claim has a theme label")
    (theme, doubt), ids = sorted(cells.items(), key=lambda kv: (-len(kv[1]), kv[0][0], kv[0][1] or ""))[0]
    return {"format": fmt, "theme": theme, "doubt": doubt, "claim_ids": ids[:n],
            "why": f"most writable claims ({len(ids)}) in theme {theme!r} x doubt {doubt!r}; first {min(n, len(ids))} used"}


SYSTEM = """You write draft social media posts for steer, India's doctor-led metabolic health club, from verified
research claims. A Steer doctor reviews every post before anything is published.

Voice: confident, warm, short lines, sentence case (never ALL CAPS). Indian daily life (rotis, chai, desk jobs,
family dinners). Long-term habits, not quick fixes. No fear, no shame, no hype, no promises of weight loss,
no before/after, no drug promotion, no brand names of medicines. Explain biology simply.

Every line has a kind:
- health: states a fact. It may ONLY restate what its claims say, with their exact numbers, units, signs and
  comparison groups, and must list those claim_ids. Never add a number, a group, a cause or a promise. Expand
  abbreviations for the public.
- framing: the reader's doubt as a question, a transition, or a scene from Indian daily life. No numbers at
  all: no digits and no number words ("one", "four o'clock", "a dozen"), and no health assertion. Every framing line must make sense on its own: no "it", "this" or
  "that" whose meaning sits on another line or slide.
- brand: copy one of the brand lines given, exactly.
- cta: copy one of the call-to-action lines given, exactly.
- signoff: copy the sign-off given, exactly.
Never write sources: the sources slide is added by code.
State the message (the one thing the audience should take away) and the reasoning (why this post, for whom,
which doubt it answers). Both are published with the post and checked like health lines: the message may only
say what the claims used say, and neither may hold a number that is not in those claims: no claim ids, no
slide numbers, no counts, no number words such as "one study" or "three cities". Follow every editorial requirement given for the paper."""


def write_prompt(brief: dict, claims: list[dict], brand: dict, boundaries: dict) -> str:
    lines = [f"Format: {brief['format']}. Theme: {brief['theme']}. Reader doubt: {brief['doubt']}.", "", "Claims you may use:"]
    for c in claims:
        ctx = {k: c.get(k) for k in ("population", "comparison_group", "outcome", "time_point") if c.get(k)}
        lines.append(f"- {c['claim_id']}: {c['claim_text']}\n  quote: {c['quote']}\n  context: {ctx or 'none'}")
    lines += ["", "Brand lines (copy exactly): " + " | ".join(brand["whitelist"]),
              "Call-to-action lines (copy exactly): " + " | ".join(brand["cta"]),
              "Sign-off: " + brand["signoff"][0], "", "Editorial requirements for this paper:"]
    for r in boundaries.get("require_qualifier", []):
        words = r.get("all_of") or r.get("any_of")
        how = "all of" if "all_of" in r else "one of"
        where = {"same_slide": "on the same slide/scene as", "post": "somewhere in the post with",
                 "caption": "in the caption with"}[r.get("placement", "post")]
        scope = f"claims whose quote mentions {r['when_claim_quote_contains']}" if r.get("when_claim_quote_contains") else "any claim of this paper"
        lines.append(f"- include {how} {words} {where} {scope}, inside a health line that states one of those claims, "
                     f"never as a line of its own ({r.get('why', '')})")
    for r in boundaries.get("require_with", []):
        trig, need = split_rule(r)
        label = lambda e: f", with one of {e['label_any_of']} beside it" if e.get("label_any_of") else ""
        where = (" on the same slide/scene or the next one, in each part (slides, caption, reel script) where the "
                 "first claim appears") if r.get("placement") == "same_or_next_unit" else ""
        if trig is need:
            pair = [f"a claim whose quote has one of {frags(e)}{label(e)}" for e in need]
            lines.append(f"- if you use one of these, use all of them: {pair} ({r.get('why', '')})")
        else:
            lines.append(f"- if you use a claim whose quote has one of {[f for e in trig for f in frags(e)]}, also use "
                         + " and ".join(f"a claim whose quote has one of {frags(e)}{label(e)}" for e in need)
                         + f"{where} ({r.get('why', '')})")
    for r in boundaries.get("avoid_phrase", []):
        lines.append(f"- never write {r['text']!r} in {r.get('kinds', ['health'])} lines ({r.get('why', '')})")
    return "\n".join(lines)


def write_post(brief: dict, claims: list[dict], brand: dict, boundaries: dict, hashtags: list[str], router) -> dict:
    """One model-written post, schema-validated by the router; returns {post, endpoint, model, usage}."""
    use = [c for c in claims if c["claim_id"] in brief["claim_ids"]]
    r = router.call("write", SYSTEM, write_prompt(brief, use, brand, boundaries), post_schema(brief, hashtags))
    return {"post": r["output"], "endpoint": r["endpoint"], "model": r["model"], "usage": r["usage"]}
