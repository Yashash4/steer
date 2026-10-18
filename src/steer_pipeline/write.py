"""The writer's contract: post briefs and the JSON schema every model-written post must satisfy.

Every text line has a kind. health lines state a fact and must name claim ids from the brief; framing,
brand, cta and signoff lines carry none. There is no "source" kind: sources are built by code, never by
the model. The reel script has ONE list of lines per scene, and every line is both spoken and shown, so the
voice can only say what the screen says.
"""

import jsonschema

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
    return {
        "type": "object",
        "required": ["message", "reasoning", "format", "caption", "reel_script"],
        "additionalProperties": False,
        "properties": {
            "message": {"type": "string", "minLength": 1, "maxLength": 300},
            "reasoning": {"type": "string", "minLength": 1, "maxLength": 1200},
            "format": {"const": brief["format"]},
            "slides": {"type": "array", "minItems": 4, "maxItems": 7, "items": {
                "type": "object", "required": ["role", "lines"], "additionalProperties": False,
                "properties": {"role": {"enum": SLIDE_ROLES}, "lines": lines}}},
            "static": {"type": "object", "required": ["headline", "supporting"], "additionalProperties": False,
                       "properties": {"headline": line, "supporting": {"type": "array", "items": line, "maxItems": 2}}},
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
        "allOf": [  # the format decides which artwork block is present
            {"if": {"properties": {"format": {"const": "carousel"}}},
             "then": {"required": ["slides"], "not": {"required": ["static"]}},
             "else": {"required": ["static"], "not": {"required": ["slides"]}}},
        ],
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
