import json

from steer_pipeline.label import label, schema
from steer_pipeline.router import ConnectorError, Router, load_settings

VOCAB = load_settings()["labels"]
CLAIMS = [{"claim_id": "c1", "claim_text": "Weight came back within a year of stopping."},
          {"claim_id": "c2", "claim_text": "Lean mass fell by 0.4 kg."},
          {"claim_id": "c3", "claim_text": "South Asians had higher diabetes incidence at low BMI."}]


def router(outputs):
    seq = list(outputs)

    def connector(ep, system, prompt, sch, **kw):
        item = seq.pop(0)
        if isinstance(item, Exception):
            raise item
        return item, None
    settings = {"jobs": {"label": {"chain": ["a", "b"]}},
                "endpoints": {"a": {"connector": "fake", "model": "a"}, "b": {"connector": "fake", "model": "b"}}}
    return Router(settings, connectors={"fake": connector}, env={})


def test_labels_from_the_vocabulary():
    out = {"labels": [{"claim_id": "c1", "theme": "weight_regain", "doubts": ["will_weight_come_back"]},
                      {"claim_id": "c2", "theme": "lean_mass_during_weight_loss", "doubts": ["will_i_lose_muscle"]},
                      {"claim_id": "c3", "theme": "indian_food_and_life", "doubts": ["is_it_for_me"]}]}
    r = label(CLAIMS, router([out]), VOCAB)
    assert r["labels"]["c2"] == {"theme": "lean_mass_during_weight_loss", "doubts": ["will_i_lose_muscle"]}
    assert r["unlabeled"] == [] and r["failed_batches"] == []


def test_unknown_label_is_rejected_by_the_schema():
    bad = {"labels": [{"claim_id": "c1", "theme": "miracle_cures", "doubts": []}]}
    good = {"labels": [{"claim_id": "c1", "theme": "weight_regain", "doubts": []}]}
    rt = router([bad, bad, good])  # endpoint a: invalid twice -> endpoint b
    r = label(CLAIMS[:1], rt, VOCAB)
    assert r["labels"]["c1"]["theme"] == "weight_regain"
    assert [a["outcome"] for a in rt.log] == ["schema_invalid", "schema_invalid", "ok"]
    s = schema(VOCAB["themes"], VOCAB["doubts"], ["c1"])
    assert s["properties"]["labels"]["items"]["properties"]["theme"]["enum"] == VOCAB["themes"]


def test_skipped_and_failed_claims_are_recorded():
    partial = {"labels": [{"claim_id": "c1", "theme": "weight_regain", "doubts": []}]}
    r = label(CLAIMS, router([partial]), VOCAB)
    assert r["unlabeled"] == ["c2", "c3"]
    r = label(CLAIMS, router([ConnectorError("error", "x")] * 4), VOCAB)
    assert r["labels"] == {} and r["unlabeled"] == ["c1", "c2", "c3"] and len(r["failed_batches"]) == 1


def test_unknown_claim_id_is_rejected():
    out = {"labels": [{"claim_id": "zzz", "theme": "weight_regain", "doubts": []}]}
    rt = router([out, out, out, out])
    r = label(CLAIMS[:1], rt, VOCAB)
    assert r["unlabeled"] == ["c1"] and json.dumps(rt.log).count("schema_invalid") == 4
