import copy

import pytest

from steer_pipeline import write
from steer_pipeline.router import load_settings

TAGS = load_settings()["writer"]["hashtags"]
BRIEF = {"format": "carousel", "theme": "weight_regain", "doubt": "will_weight_come_back", "claim_ids": ["c1", "c2"]}


def L(kind, text, ids=()):
    return {"kind": kind, "text": text, "claim_ids": list(ids)}


CAROUSEL = {  # hand-written fake writer output, valid under the contract
    "message": "After stopping weight medicines, weight tends to come back within about a year and a half.",
    "reasoning": "Answers the doubt 'will the weight come back?' for people who tried diets and heard about GLP-1.",
    "format": "carousel",
    "slides": [
        {"role": "hook", "lines": [L("framing", "Stopped the medicine. Now what?")]},
        {"role": "point", "lines": [L("health", "Weight regain was 0.4 kg a month after stopping.", ["c1"])]},
        {"role": "point", "lines": [L("health", "Weight returned to baseline about 1.7 years later.", ["c2"])]},
        {"role": "brand", "lines": [L("brand", "A planned exit.")]},
        {"role": "cta", "lines": [L("cta", "Talk to a Steer doctor")]},
    ],
    "caption": {"lines": [L("health", "Weight regain was 0.4 kg a month after stopping.", ["c1"]),
                          L("signoff", "steerclub.com")],
                "hashtags": TAGS[:5]},
    "reel_script": {"scenes": [
        {"lines": [L("framing", "Stopped the medicine. Now what?") | {"show_large": True}]},
        {"lines": [L("health", "Weight regain was 0.4 kg a month after stopping.", ["c1"])]},
        {"lines": [L("cta", "Talk to a Steer doctor")]},
    ]},
}


def errors(post, brief=BRIEF):
    return write.validate_post(post, brief, TAGS)


def test_valid_carousel_and_static():
    assert errors(CAROUSEL) == []
    static = copy.deepcopy(CAROUSEL)
    del static["slides"]
    static["format"] = "static"
    static["static"] = {"headline": L("health", "Weight regain was 0.4 kg a month after stopping.", ["c1"]),
                        "supporting": [L("framing", "A planned exit matters.")]}
    assert errors(static, BRIEF | {"format": "static"}) == []


@pytest.mark.parametrize("mutate,expect", [
    (lambda p: p["slides"][1]["lines"][0].update(claim_ids=[]), "should be non-empty"),          # health without ids
    (lambda p: p["slides"][0]["lines"][0].update(claim_ids=["c1"]), "expected to be empty"),     # framing with ids
    (lambda p: p["slides"][1]["lines"][0].update(kind="source"), "is not one of"),             # model-written source
    (lambda p: p["slides"][1]["lines"][0].update(claim_ids=["c9"]), "is not one of"),          # id outside the brief
    (lambda p: p["slides"].extend([p["slides"][1]] * 3), "is too long"),                        # 8 model slides
    (lambda p: p["caption"].update(hashtags=TAGS[:4] + ["#ozempic"]), "is not one of"),        # tag outside vocabulary
    (lambda p: p.pop("reasoning"), "'reasoning' is a required property"),                      # the doc requires both
    (lambda p: p["reel_script"]["scenes"][0].update(voiceover=["x"]), "Additional properties"), # one list per scene
    (lambda p: p.update(static={"headline": L("framing", "x"), "supporting": []}), "should not be valid"),  # both blocks
])
def test_contract_violations(mutate, expect):
    bad = copy.deepcopy(CAROUSEL)
    mutate(bad)
    found = errors(bad)
    assert found and any(expect in e for e in found), found


def test_hashtag_vocabulary_names_no_drug_or_promise():
    s = load_settings()
    drugs = {w.lower() for w in s["rules"]["drug_names"] + s["rules"]["drug_brands"]}
    for tag in TAGS:
        assert tag.startswith("#") and not any(d in tag.lower() for d in drugs)
        assert "loss" not in tag and "lose" not in tag  # no tag reads as a weight-loss promise


def test_brief_picks_the_fullest_theme_doubt_cell():
    claims = [{"claim_id": f"c{i}", "labels": {"theme": t, "doubts": d}} for i, (t, d) in enumerate([
        ("weight_regain", ["will_weight_come_back"]), ("weight_regain", ["will_weight_come_back", "is_it_for_me"]),
        ("weight_regain", ["will_weight_come_back"]), ("lean_mass_during_weight_loss", ["will_i_lose_muscle"]),
        ("insulin_resistance", [])])]
    b = write.pick_brief(claims, "carousel", n=2)
    assert (b["theme"], b["doubt"], b["claim_ids"]) == ("weight_regain", "will_weight_come_back", ["c0", "c1"])
    assert "3" in b["why"]
    with pytest.raises(ValueError):
        write.pick_brief([{"claim_id": "x", "labels": None}], "static")
