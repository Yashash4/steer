import json
from pathlib import Path

import pytest

from steer_pipeline import extract as ex
from steer_pipeline.pdf import ingest_pdf
from steer_pipeline.router import Router

FIX = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="module")
def two():
    return ingest_pdf(FIX / "gayathri2023-frontiers-two-column.pdf", "evidence")


def claim(text, quote, **kw):
    base = {"claim_text": text, "quote": quote, "claim_type": "finding", "population": None, "hedged": False,
            "direction": "other", "outcome": None, "comparison_group": None, "time_point": None, "limitations": None}
    return base | kw


# hand-written model outputs (not from a model): planted good and bad claims for the Frontiers paper's page 2
PAGE2 = [
    claim("352 participants completed the study.", "A total of 352 participants completed the study.",
          claim_type="number"),
    claim("Total cholesterol changed by −5.4 mg/dl with almonds.",  # "lowered by 5.4" would fail: the sign must match
          "total cholesterol (−5.4 mg/dl (−10.2, −0.6); p = 0.03) in the intervention group compared to the control group",
          direction="decrease", comparison_group="control group"),
    claim("325 participants completed the study.", "A total of 352 participants completed the study."),   # changed number
    claim("Almonds cure diabetes.", "Almonds cured diabetes in all participants."),                         # invented quote
    claim("Eating almonds causes better insulin sensitivity.",                                               # causal wording
          "Daily consumption of almonds increased the intake of MUFA with decrease in carbohydrate calories and "
          "decreases insulin resistance, improves insulin sensitivity and lowers serum cholesterol in Asian Indians "
          "with overweight/obesity."),
    claim("Almonds protected muscle.", "A total of 352 participants completed the study.",                  # context
          comparison_group="white women"),
]


def fake_connector(by_page):
    def connector(ep, system, prompt, schema, **kw):
        page = int(prompt.split("Section (page")[1].split(")")[0].split("-")[0].strip().split()[0]) \
            if "Section (page " in prompt else 0
        return {"claims": by_page.get(page, [])}, None
    return connector


def router_with(by_page):
    settings = {"jobs": {"extract": {"chain": ["f"]}}, "endpoints": {"f": {"connector": "fake", "model": "fake"}}}
    return Router(settings, connectors={"fake": fake_connector(by_page)}, env={})


def test_extract_keeps_only_verified_claims(two):
    run = ex.extract(two, router_with({2: PAGE2}))
    s = run["summary"]
    assert s["proposed"] == 6 and s["chunks_failed"] == 0
    assert s["rejected_by_reason"] == {"NUMBER_NOT_IN_QUOTE": 1, "QUOTE_NOT_FOUND": 1}
    kept = {c["claim_text"]: c for c in run["passed"] + run["needs_check"]}
    assert set(kept) == {PAGE2[0]["claim_text"], PAGE2[1]["claim_text"], PAGE2[4]["claim_text"],
                         PAGE2[5]["claim_text"]}
    first = kept[PAGE2[0]["claim_text"]]
    assert first["gate"]["matches"][0]["sids"] == ["p0002-s0002"] and first["flags"] == []
    assert [f["code"] for f in kept[PAGE2[4]["claim_text"]]["flags"]] == ["CAUSAL_WORDING"]
    ctx = [f["code"] for f in kept[PAGE2[5]["claim_text"]]["flags"]]
    assert ctx == ["CONTEXT_NOT_IN_QUOTE"]
    assert kept[PAGE2[1]["claim_text"]]["flags"] == []  # "control group" is stated in the quote
    # nothing unverified is stored as passed
    assert all(c["gate"]["status"] == "pass" for c in run["passed"] + run["needs_check"])
    assert all(c["gate"]["status"] == "reject" for c in run["rejected"])


def test_chunks_cover_every_sentence_once(two):
    chunks = ex.chunk_sentences(two)
    sids = [s["sid"] for c in chunks for s in c]
    assert sids == [s["sid"] for s in two["sentences"]]
    for c in chunks:  # whole sentences, one start page per chunk
        assert len({s["page"] for s in c}) == 1
    run = ex.extract(two, router_with({}))
    assert run["all_sentences_covered"] and len(run["chunks"]) == len(chunks)


def test_oversize_chunk_is_split_never_sent(two, monkeypatch):
    monkeypatch.setattr(ex, "CHUNK_MAX_CHARS", 2000)
    chunks = ex.chunk_sentences(two)
    assert all(sum(len(s["text"]) + 1 for s in c) <= 2000 or len(c) == 1 for c in chunks)
    seen = []

    def connector(ep, system, prompt, schema, **kw):
        seen.append(len(prompt))
        return {"claims": []}, None
    settings = {"jobs": {"extract": {"chain": ["f"]}}, "endpoints": {"f": {"connector": "fake", "model": "fake"}}}
    ex.extract(two, Router(settings, connectors={"fake": connector}, env={}))
    assert max(seen) <= 2000 + 4000  # chunk text plus the short title/header


def test_hedge_mismatch_and_lean_mass_flags():
    c = claim("Weight training may protect muscle.", "approximately 20% of the lost weight was lean mass", hedged=False)
    codes = [f["code"] for f in ex.code_flags(c)]
    assert codes == ["LEAN_MASS_WORDING", "HEDGE_MISMATCH"]
    ok = claim("About 20% of the lost weight was lean mass.", "approximately 20% of the lost weight was lean mass",
               hedged=True)
    assert ex.code_flags(ok) == []


def test_causal_wording_without_association_word():
    # planted error E3: the quote states a rate ratio, not a cause, and never says "associated"
    q = "and 5.3 times (3.64 to 7.54) in South Asian women than in white women."
    flags = ex.code_flags(claim("South Asian ethnicity causes diabetes, 5.3 times (3.64 to 7.54) more often.", q))
    assert [f["code"] for f in flags] == ["CAUSAL_WORDING"]


def test_failed_chunk_is_recorded_not_hidden(two):
    from steer_pipeline.router import ConnectorError

    def broken(ep, system, prompt, schema, **kw):
        raise ConnectorError("error", "down")
    settings = {"jobs": {"extract": {"chain": ["f"]}}, "endpoints": {"f": {"connector": "fake", "model": "fake"}}}
    run = ex.extract(two, Router(settings, connectors={"fake": broken}, env={}))
    assert run["summary"]["chunks_failed"] == len(run["chunks"])
    assert all(c["status"] == "failed" for c in run["chunks"])


def test_refuses_non_evidence(two):
    doc = json.loads(json.dumps(two))
    doc["source"]["role"] = "editorial"
    with pytest.raises(ValueError, match="evidence"):
        ex.extract(doc, router_with({}))


# --- Narayan (not committed): fetched live, model output still hand-written (fake connector) ---

@pytest.mark.network
def test_narayan_context_fields_and_gate():
    from steer_pipeline.fetch import fetch
    doc = fetch("PMC8006839", "evidence")
    men = ("In normal weight (BMI <25 kg/m2), diabetes incidence adjusted for age was 2.9 times higher (2.09 to 4.28) "
           "in South Asian men, and 5.3 times (3.64 to 7.54) in South Asian women than in white women.")
    out = {1: [
        claim("Normal-weight South Asian men had 2.9 times higher diabetes incidence (2.09 to 4.28).", men),
        claim("Normal-weight South Asian men had 2.9 times higher incidence than white men.", men,
              comparison_group="white men"),
        claim("Normal-weight South Asian women had 5.3 times (3.64 to 7.54) the incidence of white women.", men,
              comparison_group="white women"),
        claim("Normal-weight South Asian women had 3.5 times the incidence of white women.", men),
    ]}
    run = ex.extract(doc, router_with(out))
    kept = {c["claim_text"]: c for c in run["passed"] + run["needs_check"]}
    assert run["summary"]["rejected_by_reason"] == {"NUMBER_NOT_IN_QUOTE": 1}
    assert kept[out[1][0]["claim_text"]]["flags"] == []                      # comparison_group null: fine
    assert [f["code"] for f in kept[out[1][1]["claim_text"]]["flags"]] == ["CONTEXT_NOT_IN_QUOTE"]
    assert kept[out[1][2]["claim_text"]]["flags"] == []                      # "white women" is in the quote
    assert all(c in run["needs_check"] for c in kept.values())                # page 1 needs a layout check
