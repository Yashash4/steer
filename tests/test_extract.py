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
    claim("Total cholesterol changed by −5.4 mg/dl in the intervention group compared to the control group.",
          # "lowered by 5.4" would fail: the sign must match
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
    assert [f["code"] for f in kept[PAGE2[4]["claim_text"]]["flags"]] == ["CLAIM_EXCEEDS_QUOTE", "CAUSAL_WORDING"]
    ctx = [f["code"] for f in kept[PAGE2[5]["claim_text"]]["flags"]]
    assert ctx == ["CLAIM_EXCEEDS_QUOTE", "CONTEXT_NOT_IN_QUOTE"]
    # passed means clean: flagged claims are in needs_check with their reasons, never in passed
    passed = {c["claim_text"] for c in run["passed"]}
    assert PAGE2[4]["claim_text"] not in passed and PAGE2[5]["claim_text"] not in passed
    nc = {c["claim_text"]: c["review_reasons"] for c in run["needs_check"]}
    assert nc[PAGE2[4]["claim_text"]] == ["CAUSAL_WORDING", "CLAIM_EXCEEDS_QUOTE"]
    assert nc[PAGE2[5]["claim_text"]] == ["CLAIM_EXCEEDS_QUOTE", "CONTEXT_NOT_IN_QUOTE"]
    assert s["needs_check_by_reason"] == {"CAUSAL_WORDING": 1, "CLAIM_EXCEEDS_QUOTE": 2, "CONTEXT_NOT_IN_QUOTE": 1}
    assert passed == {PAGE2[0]["claim_text"], PAGE2[1]["claim_text"]}
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


def others(flags):
    """Flag codes other than CLAIM_EXCEEDS_QUOTE (tests of the other flags use short paraphrases)."""
    return [f["code"] for f in flags if f["code"] != "CLAIM_EXCEEDS_QUOTE"]


def test_hedge_mismatch_and_lean_mass_flags():
    c = claim("Weight training may protect muscle.", "approximately 20% of the lost weight was lean mass", hedged=False)
    assert others(ex.code_flags(c)) == ["LEAN_MASS_WORDING", "HEDGE_MISMATCH"]
    ok = claim("About 20% of the lost weight was lean mass.", "approximately 20% of the lost weight was lean mass",
               hedged=True)
    assert ex.code_flags(ok) == []


def test_hedge_words_only_before_numbers():
    assert not ex.quote_hedged("We collected data about diet in 12% of homes.")
    assert ex.quote_hedged("about 12% of homes")
    assert ex.quote_hedged("This may reduce appetite.")
    # the model more cautious than the quote: a note, not a flag
    cautious = claim("Weight fell by 12%.", "Weight fell by 12% in the group.", hedged=True)
    assert ex.code_flags(cautious) == [] and ex.hedge_note(cautious)
    plain = claim("Diet data covered 12% of homes.", "We collected data about diet in 12% of homes.")
    assert ex.code_flags(plain) == []


def test_population_must_come_from_the_section():
    c = claim("352 adults completed the study.", "A total of 352 participants completed the study.",
              population="Asian Indian adults")
    assert others(ex.code_flags(c, "Among Asian Indian adults with overweight, a total of 352 participants completed.")) == []
    assert others(ex.code_flags(c, "Adults (all Asian Indian) took part; a total of 352 participants completed.")) == []
    assert others(ex.code_flags(c, "A total of 352 participants completed the study.")) == ["POPULATION_NOT_IN_SECTION"]


def test_causal_wording_without_association_word():
    # planted error E3: the quote states a rate ratio, not a cause, and never says "associated"
    q = "and 5.3 times (3.64 to 7.54) in South Asian women than in white women."
    flags = ex.code_flags(claim("South Asian ethnicity causes diabetes, 5.3 times (3.64 to 7.54) more often.", q))
    assert others(flags) == ["CAUSAL_WORDING"]


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


@pytest.mark.network
def test_narayan_table_row_is_not_clean():
    from steer_pipeline.fetch import fetch
    from steer_pipeline.verify import verify
    doc = fetch("PMC8006839", "evidence")
    q = "Log HOMA-­IR (per SD) 2.67 (2.05 to 3.48) 14.55 (11.11 to 19.07) 34.64 (27.63 to 43.42)"
    c = {"claim_id": "t", "text": "Log HOMA-IR per SD: 2.67 (2.05 to 3.48), 14.55 (11.11 to 19.07), 34.64 (27.63 to 43.42).",
         "document_id": doc["document_id"], "quote": q}
    r = verify([c], [doc])
    assert r["results"][0]["status"] == "pass" and r["results"][0]["from_table_text"] is True
    assert "TABLE_TEXT" in [w["code"] for w in r["results"][0]["warnings"]]
    assert r["summary"]["passed_clean"] == 0


@pytest.mark.network
def test_narayan_same_statistic_from_two_sentences():
    # the men's 2.09 to 4.28 interval: the abstract sentence names no comparator for men; the results
    # sentence does ("as compared with US whites"). comparison_group may only come from the quoted sentence.
    from steer_pipeline.fetch import fetch
    doc = fetch("PMC8006839", "evidence")
    abstract = ("In normal weight (BMI <25 kg/m2), diabetes incidence adjusted for age was 2.9 times higher "
                "(2.09 to 4.28) in South Asian men, and 5.3 times (3.64 to 7.54) in South Asian women than in white women.")
    results = ("In those with BMI <25 kg/m2 (table 3), risk of diabetes in men was 3 times higher in South Asians "
               "(IRR (95% CI) 2.99 (2.09 to 4.28)) and 2.1 times higher in US blacks (IRR (95% CI) 2.11 (1.35 to 3.25)) "
               "as compared with US whites.")
    out = {1: [claim("Normal-weight South Asian men: 2.9 times higher diabetes incidence (2.09 to 4.28).", abstract,
                     population="South Asian men with BMI <25 kg/m2"),
               claim("Normal-weight South Asian men: 2.9 times higher incidence (2.09 to 4.28) than white men.", abstract,
                     comparison_group="white men")],
           5: [claim("Normal-weight South Asian men had 2.99 (2.09 to 4.28) times the diabetes risk of US whites.", results,
                     comparison_group="US whites", outcome="risk of diabetes")]}
    run = ex.extract(doc, router_with(out))
    kept = {c["claim_text"]: c for c in run["passed"] + run["needs_check"]}
    assert run["summary"]["rejected"] == 0 and len(kept) == 3
    a0, a1, r0 = (kept[c["claim_text"]] for c in (out[1][0], out[1][1], out[5][0]))
    assert a0["comparison_group"] is None and a0["flags"] == []
    assert [f["code"] for f in a1["flags"]] == ["CONTEXT_NOT_IN_QUOTE"]       # the abstract does not name white men
    assert r0["comparison_group"] == "US whites" and r0["flags"] == []       # the results sentence does
    assert a0["gate"]["matches"][0]["sids"] == ["p0001-s0027"]
    assert r0["gate"]["matches"][0]["sids"] == ["p0005-s0003"]


def test_context_fields_are_checked_word_by_word():
    q = "After 52 weeks, the change in lean mass as a proportion of total weight was higher 2.53%."
    ok = claim("Lean mass share rose 2.53%.", q, outcome="lean mass as a proportion of total weight", time_point="52 weeks")
    assert others(ex.code_flags(ok)) == []
    made_up = claim("Lean mass share rose 2.53%.", q, time_point="median 8.8 years")
    assert others(ex.code_flags(made_up)) == ["CONTEXT_NOT_IN_QUOTE"]
    # the D-018 case: "white men" against a quote that says "US whites" (the word "men" is missing)
    d018 = claim("Men had 2.99 times the risk.", "risk of diabetes in men was 3 times higher in South Asians "
                 "(IRR 2.99 (2.09 to 4.28)) as compared with US whites.", comparison_group="white men")
    assert others(ex.code_flags(d018)) == ["CONTEXT_NOT_IN_QUOTE"]


def test_claim_exceeding_its_quote_is_flagged():
    q = "We suggest that lean mass loss should not be considered a limitation for the use of these drugs in patients with obesity."
    faithful = claim("Lean mass loss should not be considered a limitation for the use of these drugs in patients with obesity.",
                     q, hedged=True)
    assert ex.code_flags(faithful) == []
    added = claim("The authors suggest lean mass loss should not be considered a limitation for using these drugs in "
                  "patients with obesity, but that drug treatment should be accompanied by nutritional and physical "
                  "exercise interventions.", q, hedged=True)  # Laverde 2 from the first live run
    assert "CLAIM_EXCEEDS_QUOTE" in [f["code"] for f in ex.code_flags(added)]


def test_defined_acronyms_count_as_their_long_form():
    text = ("Real world use of weight management medications (WMMs) is rising. Behavioural weight management "
            "programmes (BWMPs) are common. Fake things here (FTX) do not match.")
    defined = ex.acronyms(text)
    assert defined.keys() == {"WMM", "BWMP"}  # FTX: the initials of "Fake things here" do not spell it
    q = "Real world observations estimate that around 50% of people with obesity discontinue WMMs within 12 months"
    c = "Real world observations estimate that around 50% of people with obesity discontinue weight management medications within 12 months"
    assert ex.exceeds_quote(c, q, defined)[1] == []
    assert ex.exceeds_quote(c, q)[1] == ["weight", "management", "medication"]  # without the definitions
    undefined = "discontinue XYZ within 12 months"
    assert ex.exceeds_quote("discontinue weight management medications within 12 months", undefined, defined)[1] == [
        "weight", "management", "medication"]


def test_comparator_must_be_the_quotes_comparison_group():
    abstract = ("In normal weight (BMI <25 kg/m2), diabetes incidence adjusted for age was 2.9 times higher (2.09 to 4.28) "
                "in South Asian men, and 5.3 times (3.64 to 7.54) in South Asian women than in white women.")
    men = claim("Diabetes incidence was 2.9 times higher (2.09 to 4.28) in South Asian men than in white men.", abstract)
    assert "COMPARATOR_NOT_IN_QUOTE" in [f["code"] for f in ex.code_flags(men)]
    women = claim("Diabetes incidence was 5.3 times (3.64 to 7.54) higher in South Asian women than in white women.",
                  abstract, comparison_group="white women")
    assert "COMPARATOR_NOT_IN_QUOTE" not in [f["code"] for f in ex.code_flags(women)]
    results = ("In those with BMI <25 kg/m2 (table 3), risk of diabetes in men was 3 times higher in South Asians "
               "(IRR (95% CI) 2.99 (2.09 to 4.28)) as compared with US whites.")
    r = claim("Risk of diabetes in men was 2.99 (2.09 to 4.28) times higher in South Asians compared with US whites.",
              results, comparison_group="US whites")
    assert "COMPARATOR_NOT_IN_QUOTE" not in [f["code"] for f in ex.code_flags(r)]
    # control: a comparison of values, not of groups
    values = claim("Adults with BMI more than 30 kg/m2 were included.", "Adults with BMI more than 30 kg/m2 were included.")
    assert "COMPARATOR_NOT_IN_QUOTE" not in [f["code"] for f in ex.code_flags(values)]


def test_comparison_group_must_be_a_phrase_of_the_quote():
    abstract = ("In normal weight (BMI <25 kg/m2), diabetes incidence adjusted for age was 2.9 times higher (2.09 to 4.28) "
                "in South Asian men, and 5.3 times (3.64 to 7.54) in South Asian women than in white women.")
    bad = claim("2.9 times higher (2.09 to 4.28) in South Asian men than in white men.", abstract, comparison_group="white men")
    assert "CONTEXT_NOT_IN_QUOTE" in [f["code"] for f in ex.code_flags(bad)]
    ok = claim("5.3 times (3.64 to 7.54) in South Asian women than in white women.", abstract, comparison_group="white women")
    assert ex.code_flags(ok) == []
