"""Theme and doubt labels for passed claims, from controlled vocabularies in the settings.

The model assigns one theme and zero or more doubts per claim. The JSON schema lists the vocabulary as
enums, so a label outside it fails validation (and the router retries or falls back). Claims the model
skips are recorded as unlabeled, never guessed.
"""

from .router import Router, RouterError

SYSTEM = """You label health claims for a doctor-led metabolic health club in India.

For each claim give exactly one theme and the reader doubts (zero or more) it helps answer.
Use only the codes given in the schema. Themes:
- glp1_how_it_works: how GLP-1 medicines act in the body
- insulin_resistance: insulin resistance, insulin secretion, blood sugar, diabetes risk
- lean_mass_during_weight_loss: muscle, lean or fat-free mass during weight loss
- weight_regain: weight coming back after stopping treatment or diets
- indian_food_and_life: Indian food, routines, ethnicity-specific findings
Doubts:
- is_it_safe: side effects, safety
- will_i_lose_muscle: muscle or lean mass loss
- will_weight_come_back: regain after stopping
- only_for_diabetics: whether treatment or risk applies to people without diabetes
- is_it_for_me: who it applies to (body size, ethnicity, age, sex)
Label what the claim says, not what it implies. Return one entry per claim_id given."""


def schema(themes: list[str], doubts: list[str], claim_ids: list[str]) -> dict:
    return {
        "type": "object",
        "required": ["labels"],
        "additionalProperties": False,
        "properties": {"labels": {"type": "array", "items": {
            "type": "object",
            "required": ["claim_id", "theme", "doubts"],
            "additionalProperties": False,
            "properties": {
                "claim_id": {"enum": claim_ids},
                "theme": {"enum": themes},
                "doubts": {"type": "array", "items": {"enum": doubts}, "uniqueItems": True},
            },
        }}},
    }


def label(claims: list[dict], router: Router, vocab: dict) -> dict:
    """Labels for the claims ({claim_id, claim_text, ...}); returns {labels, unlabeled, failed_batches}."""
    themes, doubts, size = vocab["themes"], vocab["doubts"], vocab.get("batch", 20)
    labels: dict[str, dict] = {}
    failed = []
    for i in range(0, len(claims), size):
        batch = claims[i:i + size]
        ids = [c["claim_id"] for c in batch]
        prompt = "\n".join(f"{c['claim_id']}: {c['claim_text']}" for c in batch)
        try:
            out = router.call("label", SYSTEM, prompt, schema(themes, doubts, ids))["output"]
        except RouterError as e:
            failed.append({"claim_ids": ids, "error": str(e)})
            continue
        for item in out["labels"]:
            labels.setdefault(item["claim_id"], {"theme": item["theme"], "doubts": item["doubts"]})
    unlabeled = [c["claim_id"] for c in claims if c["claim_id"] not in labels]
    return {"labels": labels, "unlabeled": unlabeled, "failed_batches": failed}
