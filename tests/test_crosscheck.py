import pytest

from steer_pipeline import crosscheck as cc
from steer_pipeline.router import ConnectorError, Router, load_settings

EP = load_settings()["endpoints"]


def test_independence_is_computed_from_the_models_actually_used():
    assert cc.independence(EP["claude_sonnet"], EP["opencode_glm"]) == "different_provider"
    assert cc.independence(EP["claude_sonnet"], EP["claude_opus"]) == "different_model_same_provider"
    assert cc.independence(EP["claude_opus"], EP["claude_opus"]) == "same_model"
    # one model behind two connectors is still the same model
    assert cc.independence(EP["claude_sonnet"], EP["anthropic_sonnet"]) == "same_model"
    assert cc.independence(EP["opencode_glm"], EP["opencode_deepseek"]) == "different_model_same_provider"


ABSTRACT = ("In normal weight (BMI <25 kg/m2), diabetes incidence adjusted for age was 2.9 times higher (2.09 to 4.28) "
            "in South Asian men, and 5.3 times (3.64 to 7.54) in South Asian women than in white women.")


def claim(cid, text, quote, chunk=0, **kw):
    return {"claim_id": cid, "claim_text": text, "quote": quote, "chunk": chunk, "gate": {"status": "pass"},
            "flags": [], "review_reasons": [], "comparison_group": None} | kw


def router(chain, answers):
    """The real endpoint settings (so independence uses real providers and models); endpoints whose model is in
    `answers` reply with that function's output, the others fail."""
    def connector(ep, system, prompt, schema, **kw):
        fn = answers.get(ep["model"])
        if fn is None:
            raise ConnectorError("error", "down")
        ids = schema["properties"]["verdicts"]["items"]["properties"]["claim_id"]["enum"]
        return fn(ids), None
    settings = {"endpoints": EP, "jobs": {"crosscheck": {"chain": chain}}}
    return Router(settings, connectors={k: connector for k in ("claude_cli", "anthropic_api", "openai_compatible")},
                  env={"OPENCODE_GO_API_KEY": "k", "ANTHROPIC_API_KEY": "k"})


def all_say(verdict):
    return lambda ids: {"verdicts": [{"claim_id": i, "verdict": verdict, "reason": "r"} for i in ids]}


CHUNKS_SONNET = [{"endpoint": "claude_sonnet"}]
CHUNKS_OPUS = [{"endpoint": "claude_opus"}]


def test_wrong_comparison_group_is_not_verified():
    c = claim("c1", "2.9 times higher in South Asian men than in white men.", ABSTRACT)
    r = router(["opencode_glm"], {"glm-5.3": lambda ids: {"verdicts": [
        {"claim_id": "c1", "verdict": "wrong_comparison_group", "reason": "the quote names no comparator for men"}]}})
    res = cc.crosscheck_library([c], CHUNKS_SONNET, r)
    assert res[0]["verdict"] == "wrong_comparison_group" and res[0]["independence"] == "different_provider"
    assert not cc.verified(c, res[0])


def test_supported_by_an_independent_model_is_verified():
    c = claim("c1", "5.3 times (3.64 to 7.54) higher in South Asian women than in white women.", ABSTRACT,
              comparison_group="white women")
    res = cc.crosscheck_library([c], CHUNKS_SONNET, router(["opencode_glm"], {"glm-5.3": all_say("supported")}))
    assert cc.verified(c, res[0])
    # a self-check result never counts, even when it says supported
    assert not cc.verified(c, res[0] | {"independence": "same_model"})
    # a flagged or review-reason claim is never verified
    assert not cc.verified(c | {"flags": [{"code": "CAUSAL_WORDING"}]}, res[0])


def test_writer_fell_back_to_opus_and_only_opus_works_is_unavailable():
    c = claim("c1", "x", ABSTRACT)
    r = router(["opencode_glm", "claude_opus"], {"opus": all_say("supported")})  # glm down, opus would answer
    res = cc.crosscheck_library([c], CHUNKS_OPUS, r)
    assert res[0]["verdict"] == "crosscheck_unavailable"
    assert not cc.verified(c, res[0])
    outcomes = [(a["endpoint"], a["outcome"]) for a in r.log]
    assert ("claude_opus", "skipped_ineligible") in outcomes  # the self-check was never even called


def test_missing_verdict_is_not_checked_and_batches_are_per_chunk():
    cs = [claim("a", "x", ABSTRACT, chunk=0), claim("b", "y", ABSTRACT, chunk=0), claim("c", "z", ABSTRACT, chunk=1)]
    calls = []

    def answer(ids):
        calls.append(ids)
        return {"verdicts": [{"claim_id": ids[0], "verdict": "supported", "reason": "r"}]}
    res = {x["claim_id"]: x for x in cc.crosscheck_library(cs, CHUNKS_SONNET * 2, router(["opencode_glm"], {"glm-5.3": answer}))}
    assert calls == [["a", "b"], ["c"]]
    assert res["b"]["verdict"] == "not_checked" and not cc.verified(cs[1], res["b"])


def test_unknown_verdict_fails_the_schema_and_falls_back():
    r = router(["opencode_glm", "opencode_deepseek"], {"glm-5.3": all_say("probably fine"), "deepseek-v4-pro": all_say("overstated")})
    res = cc.crosscheck_library([claim("c1", "x", ABSTRACT)], CHUNKS_SONNET, r)
    assert res[0]["verdict"] == "overstated" and res[0]["model"] == "deepseek-v4-pro"
    assert [a["outcome"] for a in r.log][:2] == ["schema_invalid", "schema_invalid"]


@pytest.mark.network
def test_live_glm_crosscheck_on_real_narayan_claims():
    import json
    import os
    if not os.environ.get("OPENCODE_GO_API_KEY"):
        pytest.skip("OPENCODE_GO_API_KEY is not set")
    from steer_pipeline.fetch import fetch
    doc = fetch("PMC8006839", "evidence")
    results = ("In those with BMI <25 kg/m2 (table 3), risk of diabetes in men was 3 times higher in South Asians "
               "(IRR (95% CI) 2.99 (2.09 to 4.28)) and 2.1 times higher in US blacks (IRR (95% CI) 2.11 (1.35 to 3.25)) "
               "as compared with US whites.")
    cs = [claim("good", "Normal-weight South Asian women had 5.3 times (3.64 to 7.54) the diabetes incidence of white women.",
                ABSTRACT, comparison_group="white women"),
          claim("trap", "Normal-weight South Asian men had 2.9 times (2.09 to 4.28) the diabetes incidence of white men.",
                ABSTRACT, comparison_group=None),
          claim("results", "In men with BMI <25 kg/m2, diabetes risk was 2.99 (2.09 to 4.28) times higher in South Asians "
                "than in US whites.", results, comparison_group="US whites"),
          claim("cause", "Being South Asian causes a 5.3 times higher diabetes incidence.", ABSTRACT)]
    r = Router(load_settings())
    res = {x["claim_id"]: x for x in cc.crosscheck_library(cs, CHUNKS_SONNET, r)}
    print(json.dumps(res, indent=1))
    assert res["good"]["verdict"] == "supported" and res["good"]["model"] == "glm-5.3"
    assert res["trap"]["verdict"] != "supported" and res["cause"]["verdict"] != "supported"
    assert {x["independence"] for x in res.values()} == {"different_provider"}
