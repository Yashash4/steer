import copy
import tomllib
from pathlib import Path

import pytest

from steer_pipeline import checks
from steer_pipeline.router import ConnectorError, Router, load_settings

SETTINGS = load_settings()
RULES = SETTINGS["rules"]
BRAND = tomllib.loads((Path(__file__).resolve().parents[1] / "brand.toml").read_text())
EP = SETTINGS["endpoints"]
NARAYAN = checks.load_boundaries("PMC8006839")
LAVERDE = checks.load_boundaries("PMC13461373")
WEST = checks.load_boundaries("PMC12776922")

ABSTRACT = ("In normal weight (BMI <25 kg/m2), diabetes incidence adjusted for age was 2.9 times higher (2.09 to 4.28) "
            "in South Asian men, and 5.3 times (3.64 to 7.54) in South Asian women than in white women.")
RESULTS = ("In those with BMI <25 kg/m2 (table 3), risk of diabetes in men was 3 times higher in South Asians "
           "(IRR (95% CI) 2.99 (2.09 to 4.28)) and 2.1 times higher in US blacks (IRR (95% CI) 2.11 (1.35 to 3.25)) "
           "as compared with US whites.")
FAMILY = "Family history of diabetes was significantly associated with incident diabetes among black and white Americans but not among South Asians."
LEAN_ABS = ("However, a significant decrease was observed in the absolute change in lean mass (−1.74 kg; 95% CI: −3.04 to "
            "−0.45; p < 0.00001; I² = 98%) and in the percentage of lean mass (−3.06%; 95% CI: −5.10 to −1.02; p < 0.00001; I² = 98%).")
LEAN_SHARE = ("GLP1-RA significantly improved lean mass as a proportion of total weight, with an observed increase of 1.81% "
              "(95% CI: 1.1– 2.52; p < 0.00001; I² = 7%).")
REGAIN = "The average monthly rate of weight regain was 0.4 kg (95% confidence interval (CI) 0.3 to 0.5)."


def C(cid, quote, **kw):
    return {"claim_id": cid, "claim_text": quote, "quote": quote, "gate": {"status": "pass"}, "flags": [],
            "review_reasons": [], "comparison_group": None, "population": None,
            "crosscheck": {"verdict": "supported", "independence": "different_provider"}} | kw


CLAIMS = {c["claim_id"]: c for c in [
    C("women", ABSTRACT, comparison_group="white women"), C("men_abs", ABSTRACT),
    C("men_res", RESULTS, comparison_group="US whites"), C("family", FAMILY),
    C("lean_abs", LEAN_ABS), C("lean_share", LEAN_SHARE), C("regain", REGAIN),
]}


def L(kind, text, ids=()):
    return {"kind": kind, "text": text, "claim_ids": list(ids)}


def codes(line, boundaries=NARAYAN, claims=CLAIMS):
    return [f["code"] for f in checks.check_line(dict(line), claims, BRAND, RULES, boundaries)]


# --- line checks, one planted error each ---

def test_structure():
    assert codes(L("health", "South Asian women had higher incidence.", [])) == ["UNKNOWN_CLAIM"]
    assert codes(L("brand", "Weight loss made easy.")) == ["BRAND_NOT_WHITELISTED"]
    assert codes(L("brand", "Lose well.  Live well.")) == []          # whitespace and case only
    assert codes(L("cta", "Book now!")) == ["CTA_NOT_ALLOWED"]
    assert codes(L("framing", "The 3pm slump after chai")) == ["FRAMING_HAS_NUMBER"]   # v1 change A: no exceptions
    assert codes(L("framing", "Afternoon chai and the slump")) == []
    flagged = dict(CLAIMS, x=C("x", FAMILY, flags=[{"code": "CAUSAL_WORDING"}]))
    assert codes(L("health", "Family history was linked to diabetes in US adults.", ["x"]), claims=flagged) == ["CLAIM_NOT_WRITABLE"]


def test_numbers():
    ok = L("health", "In adults aged 45 and over in Chennai, Delhi and Karachi, incidence was 5.3 times (3.64 to 7.54) "
                     "higher in South Asian women than in white women.", ["women"])
    assert codes(ok) == []
    changed = L("health", "Incidence was 3.5 times higher in South Asian women than in white women.", ["women"])
    assert codes(changed) == ["NUMBER_NOT_IN_CLAIM"]


def test_wording_rules():
    assert "DRUG_BRAND" in codes(L("health", "Ozempic users had 5.3 times (3.64 to 7.54) higher incidence.", ["women"]))
    assert "PROMISE" in codes(L("framing", "Lose 10 kg in 12 weeks"))
    assert "LEAN_MASS_WORDING" in codes(L("health", "Muscle fell by −1.74 kg on average.", ["lean_abs"]), LAVERDE)
    assert "CAUSAL_WORDING" in codes(L("health", "Being South Asian causes 5.3 times (3.64 to 7.54) higher diabetes "
                                                  "incidence in women than in white women.", ["women"]))
    # brand and CTA lines are Steer's own: no STEER_TEAM_CLAIM block
    assert codes(L("cta", "Talk to a Steer doctor")) == []


def test_comparison_group_rule():
    assert "COMPARATOR_NOT_IN_CLAIM" in codes(L("health", "Incidence was 2.9 times higher than in white men.", ["men_abs"]))
    assert "COMPARATOR_NOT_IN_CLAIM" not in codes(L("health", "In men, risk was 3 times higher in South Asians than in US whites.",
                                                    ["men_res"]))
    assert "COMPARATOR_NOT_IN_CLAIM" not in codes(L("health", "Incidence was 5.3 times higher than in white women.", ["women"]))
    assert "COMPARATOR_NOT_IN_CLAIM" in codes(L("health", "Incidence was 5.3 times higher than in white men.", ["women"]))


def test_avoid_phrases_are_scoped():
    assert "AVOID_PHRASE" in codes(L("framing", "Every Indian should hear this"))
    assert "AVOID_PHRASE" in codes(L("health", "Indian women had 5.3 times higher incidence than white women.", ["women"]))
    assert "AVOID_PHRASE" in codes(L("framing", "You are at risk if you skip tests"))
    # the paper's own outcome word is allowed in health lines, not in framing
    assert "AVOID_PHRASE" not in codes(L("health", "Family history was not associated with being diagnosed with diabetes "
                                                   "among South Asians.", ["family"]))
    assert "AVOID_PHRASE" in codes(L("framing", "Get diagnosed early"))
    # West: the doubt as a question is fine; a personal prediction is not
    assert codes(L("framing", "Will I gain it all back?"), WEST) == []
    assert "AVOID_PHRASE" in codes(L("framing", "You'll gain it all back"), WEST)
    assert "AVOID_PHRASE" in codes(L("health", "With GLP-1 medicines weight came back at 0.4 kg a month on average.",
                                     ["regain"]), WEST)
    # Laverde: no drug-by-drug comparison
    assert "AVOID_PHRASE" in codes(L("health", "Semaglutide lowered absolute lean mass by −1.74 kg.", ["lean_abs"]), LAVERDE)


def test_drug_name_is_a_flag_not_a_failure():
    line = dict(L("health", "In adults aged 45 and over, semaglutide users ... 5.3 times (3.64 to 7.54) higher incidence in "
                            "South Asian women than in white women.", ["women"]))
    assert "DRUG_NAME" not in [f["code"] for f in checks.check_line(line, CLAIMS, BRAND, RULES, NARAYAN)]
    assert [f["code"] for f in line["flags"]] == ["DRUG_NAME"]


# --- post-level: boundaries ---

def post(slides, caption=(L("signoff", "steerclub.com"),), scenes=None, fmt="carousel"):
    p = {"message": "m", "reasoning": "r", "format": fmt, "slides": [{"role": "point", "lines": s} for s in slides],
         "caption": {"lines": list(caption), "hashtags": []},
         "reel_script": {"scenes": scenes or [{"lines": [L("cta", "Talk to a Steer doctor")]}]}}
    return p


def boundary_codes(p, b):
    return [f["code"] for f in checks.check_boundaries(checks.post_lines(p), CLAIMS, b)]


def test_narayan_qualifiers_same_slide_only_for_incidence():
    women = L("health", "Incidence was 5.3 times (3.64 to 7.54) higher in South Asian women than in white women.", ["women"])
    where = L("health", "Women in this study were aged 45 and over in Chennai, Delhi and Karachi: "
                        "incidence was 5.3 times (3.64 to 7.54) higher in South Asian women than in white women.", ["women"])
    assert "QUALIFIER_MISSING" in boundary_codes(post([[women], [where]]), NARAYAN)     # the first 5.3x slide lacks it
    assert boundary_codes(post([[where]]), NARAYAN) == []
    family = L("health", FAMILY, ["family"])
    assert boundary_codes(post([[family], [where]]), NARAYAN) == []                     # family: elsewhere in the post is enough
    # the qualifier's own number (45) is not a claim number: the health line still passes the numbers check
    assert codes(where) == []


IR = ("South Asians were less insulin resistant than blacks and whites (age-BMI-adjusted HOMA-IR, µIU/mL/mmol/L: "
      "2.30 vs 3.45 vs 2.59, South Asian vs blacks p<0.001; South Asian vs whites p<0.001)")
INC = ("However, we found that across all ages from 45 years up, South Asians had a higher diabetes incidence than "
       "whites, and this was especially marked at lower BMIs.")
BOTH = ("South Asian adults have lower BMI and are less insulin resistant than US blacks and whites, but have higher "
        "diabetes incidence.")
IR_CLAIMS = {c["claim_id"]: c for c in [C("ir", IR), C("inc", INC), C("both", BOTH)]}


def test_narayan_lower_insulin_resistance_needs_higher_incidence_beside_it():
    ir = L("health", "South Asians were less insulin resistant than US blacks and US whites.", ["ir"])
    inc = L("health", "But higher diabetes incidence: South Asians had a higher diabetes incidence than whites.", ["inc"])
    gap = L("framing", "What does that mean at the dinner table?")
    pair = lambda p: [f["code"] for f in checks.check_boundaries(checks.post_lines(p), IR_CLAIMS, NARAYAN)
                      if f["code"] in ("REQUIRE_WITH_MISSING", "PAIR_LABEL_MISSING")]
    assert pair(post([[ir]])) == ["REQUIRE_WITH_MISSING"]                       # the reassuring half alone
    assert pair(post([[ir], [inc]])) == []                                       # next slide is close enough
    assert pair(post([[ir, inc]])) == []
    assert pair(post([[ir], [gap], [inc]])) == ["REQUIRE_WITH_MISSING"]          # two slides away is not
    assert pair(post([[ir], [inc]], caption=(ir,))) == ["REQUIRE_WITH_MISSING"]  # every surface needs its own
    bare = L("health", "South Asians had more diabetes than whites across all ages from 45 years up.", ["inc"])
    assert pair(post([[ir], [bare]])) == ["PAIR_LABEL_MISSING"]
    assert pair(post([[L("health", BOTH, ["both"])]])) == []                     # one claim carrying both halves
    assert pair(post([[inc]])) == []                                             # incidence alone needs nothing
    # the brief gets the incidence claim added when it holds the insulin resistance one
    assert checks.required_claims(["ir"], IR_CLAIMS, NARAYAN) == ["both"]        # lowest matching id
    assert checks.required_claims(["inc"], IR_CLAIMS, NARAYAN) == []


def test_laverde_pair_and_heterogeneity_caveat():
    abs_line = L("health", "Absolute lean mass fell by −1.74 kg on average; results vary a lot between studies.", ["lean_abs"])
    share = L("health", "Lean mass as a share of total weight rose by 1.81%.", ["lean_share"])
    advice = L("framing", "Pair any treatment with nutrition and exercise")
    assert boundary_codes(post([[abs_line], [share, advice]]), LAVERDE) == []
    assert "REQUIRE_WITH_MISSING" in boundary_codes(post([[abs_line, advice]]), LAVERDE)       # the pair is missing
    no_label = L("health", "Lean mass rose by 1.81%.", ["lean_share"])
    assert "PAIR_LABEL_MISSING" in boundary_codes(post([[abs_line], [no_label, advice]]), LAVERDE)
    no_caveat = L("health", "Absolute lean mass fell by −1.74 kg on average.", ["lean_abs"])
    caveat_in_caption = (L("framing", "Results vary a lot between studies"), L("signoff", "steerclub.com"))
    assert "QUALIFIER_MISSING" in boundary_codes(post([[no_caveat], [share, advice]], caption=caveat_in_caption), LAVERDE)


def test_west_average_qualifiers():
    bare = L("health", "Weight came back at 0.4 kg a month after stopping.", ["regain"])
    ok = L("health", "On average weight came back at 0.4 kg a month, not a personal promise.", ["regain"])
    assert boundary_codes(post([[bare]]), WEST).count("QUALIFIER_MISSING") == 2
    assert boundary_codes(post([[ok]]), WEST) == []


# --- line cross-check and post status ---

def fake_router(chain, answer):
    def connector(ep, system, prompt, schema, **kw):
        if answer.get(ep["model"]) is None:
            raise ConnectorError("error", "down")
        hp = schema["properties"]["health"]["items"]["properties"]["line_id"]["enum"]
        fp = schema["properties"]["framing"]["items"]["properties"]["line_id"]["enum"]
        return answer[ep["model"]](hp, fp), None
    return Router({"endpoints": EP, "jobs": {"crosscheck": {"chain": chain}}},
                  connectors={k: connector for k in ("claude_cli", "anthropic_api", "openai_compatible")},
                  env={"OPENCODE_GO_API_KEY": "k"})


def says(verdict="supported", medical=False):
    return lambda hp, fp: {"health": [{"line_id": i, "verdict": verdict, "reason": "r"} for i in hp if i != "-"],
                           "framing": [{"line_id": i, "asserts_medical_fact": medical, "reason": "r"} for i in fp if i != "-"]}


GOOD = post([[L("framing", "Will the weight come back?")],
             [L("health", "On average weight came back at 0.4 kg a month, not a personal promise.", ["regain"])],
             [L("brand", "A planned exit. We help you build habits that last after the medicine stops.")],
             [L("cta", "See if the programme fits you")]])


def test_clean_post_with_independent_checker_is_a_draft():
    r = checks.check_post(GOOD, CLAIMS, BRAND, RULES, WEST, fake_router(["opencode_glm"], {"glm-5.3": says()}), "claude_sonnet")
    assert r["status"] == "draft_pending_review", r
    h = next(x for x in r["lines"] if x["kind"] == "health")
    assert h["crosscheck"]["independence"] == "different_provider"


def test_overstated_verdict_blocks():
    r = checks.check_post(GOOD, CLAIMS, BRAND, RULES, WEST, fake_router(["opencode_glm"], {"glm-5.3": says("overstated")}),
                          "claude_sonnet")
    assert r["status"] == "blocked"


def test_framing_asserting_a_medical_fact_blocks():
    r = checks.check_post(GOOD, CLAIMS, BRAND, RULES, WEST,
                          fake_router(["opencode_glm"], {"glm-5.3": says(medical=True)}), "claude_sonnet")
    assert r["status"] == "blocked"


def test_only_a_self_check_available_is_crosscheck_unavailable():
    # the writer fell back to opus, and opus is the only working checker: it is skipped, nothing else answers
    r = checks.check_post(GOOD, CLAIMS, BRAND, RULES, WEST,
                          fake_router(["opencode_glm", "claude_opus"], {"opus": says()}), "claude_opus")
    assert r["status"] == "crosscheck_unavailable"


def test_a_failed_code_check_blocks_even_with_a_supportive_checker():
    bad = copy.deepcopy(GOOD)
    bad["slides"][0]["lines"][0] = L("framing", "Will the 3pm weight come back?")
    r = checks.check_post(bad, CLAIMS, BRAND, RULES, WEST, fake_router(["opencode_glm"], {"glm-5.3": says()}), "claude_sonnet")
    assert r["status"] == "blocked"
    assert any(f["code"] == "FRAMING_HAS_NUMBER" for x in r["lines"] for f in x["failures"])


def test_sources_never_invent_a_page():
    from steer_pipeline.posts import sources
    doc = {"citation": {"authors": ["West S", "Scragg J", "Aveyard P", "Oke JL"], "title": "T", "journal": "BMJ", "year": 2026,
                        "doi": "10.1136/bmj-2025-085304"}, "warnings": [{"code": "NO_PAGE_LAYOUT"}]}
    s = sources(["c1"], {"c1": {"sids": ["p0001-s0118"]}}, doc)[0]
    assert s["location"] == "section text, no page layout" and s["authors"] == "West S, Scragg J, Aveyard P et al."
    paged = sources(["c1"], {"c1": {"sids": ["p0003-s0002", "p0004-s0001"]}}, doc | {"warnings": []})[0]
    assert paged["location"] == "page 3, 4"


def test_message_and_reasoning_are_checked_as_published_text():
    bad = copy.deepcopy(GOOD) | {"message": "Weight comes back at 0.9 kg a month on average."}
    r = checks.check_post(bad, CLAIMS, BRAND, RULES, WEST, fake_router(["opencode_glm"], {"glm-5.3": says()}), "claude_sonnet")
    assert r["status"] == "blocked"
    msg = next(x for x in r["lines"] if x["path"] == "message")
    assert [f["code"] for f in msg["failures"]] == ["NUMBER_NOT_IN_CLAIM"]
    ids = {"c002-01": C("c002-01", REGAIN)}  # a claim id named in the reasoning is a reference, not a number
    assert codes({"kind": "reasoning", "text": "Uses c002-01 for the regain rate.", "claim_ids": ["c002-01"]}, WEST, ids) == []
    assert codes({"kind": "reasoning", "text": "Uses c002-01 for the regain rate.", "claim_ids": []}, WEST, ids) != []
    bad = copy.deepcopy(GOOD) | {"reasoning": "Slide 2 answers the doubt."}  # no slide numbers either
    r = checks.check_post(bad, CLAIMS, BRAND, RULES, WEST, fake_router(["opencode_glm"], {"glm-5.3": says()}), "claude_sonnet")
    assert r["status"] == "blocked"
    # the message is cross-checked as a health line against every quote of the post; the reasoning is not
    seen = []

    def answer(hp, fp):
        seen.extend(hp)
        out = says()(hp, fp)
        for v in out["health"]:
            if v["line_id"] == "message":
                v["verdict"] = "overstated"
        return out
    r = checks.check_post(GOOD, CLAIMS, BRAND, RULES, WEST, fake_router(["opencode_glm"], {"glm-5.3": answer}), "claude_sonnet")
    assert "message" in seen and "reasoning" not in seen
    assert r["status"] == "blocked"
    assert [f["code"] for f in next(x for x in r["lines"] if x["path"] == "message")["failures"]] == ["OVERSTATED"]


def test_a_message_that_adds_advice_is_blocked():
    advice = copy.deepcopy(GOOD) | {"message": "Weight came back at 0.4 kg a month on average, so never stop a medicine."}

    def answer(hp, fp):  # the checker's fake output: the added advice is unsupported
        out = says()(hp, fp)
        for v in out["health"]:
            if v["line_id"] == "message":
                v |= {"verdict": "unsupported", "reason": "the quotes give no advice about stopping a medicine"}
        return out
    r = checks.check_post(advice, CLAIMS, BRAND, RULES, WEST, fake_router(["opencode_glm"], {"glm-5.3": answer}), "claude_sonnet")
    assert r["status"] == "blocked"
    assert [f["code"] for f in next(x for x in r["lines"] if x["path"] == "message")["failures"]] == ["UNSUPPORTED"]
    assert 'line_id "message"' in checks.LINE_SYSTEM and "advice" in checks.LINE_SYSTEM


def test_artwork_word_budget_and_emphasis_copied_from_its_slide():
    regain = L("health", "On average weight came back at 0.4 kg a month, not a personal promise.", ["regain"])
    art = lambda p: [f["code"] for f in checks.check_artwork(p)]
    assert art(GOOD) == []
    long = post([[regain, L("framing", " ".join(["word"] * 22))]])            # 14 + 22 = 36 words
    assert art(long) == ["SLIDE_TOO_LONG"]
    keyed = post([[regain]])
    keyed["slides"][0] |= {"key_number": "0.4 kg a month", "key_label": "weight came back"}
    assert art(keyed) == []
    keyed["slides"][0]["key_label"] = "weight regain per month"                 # not copied from the line
    assert art(keyed) == ["KEY_NUMBER_NOT_IN_LINE"]
    keyed["slides"][0].pop("key_label")                                        # never a bare number
    assert art(keyed) == ["KEY_NUMBER_NOT_IN_LINE"]
    keyed["slides"][0] |= {"key_number": "weight came back", "key_label": "On average"}
    assert art(keyed) == ["KEY_NUMBER_NOT_A_NUMBER"]
    hook = post([[L("framing", "Will the weight come back?")]])
    hook["slides"][0]["emphasis"] = "come back"
    assert art(hook) == []
    hook["slides"][0]["emphasis"] = "come back for good"
    assert art(hook) == ["EMPHASIS_NOT_IN_LINE"]
    # a failure here blocks the whole post
    bad = copy.deepcopy(GOOD)
    bad["slides"][1] |= {"key_number": "0.4 kg", "key_label": "monthly regain"}
    r = checks.check_post(bad, CLAIMS, BRAND, RULES, WEST, fake_router(["opencode_glm"], {"glm-5.3": says()}), "claude_sonnet")
    assert r["status"] == "blocked" and [f["code"] for f in r["post_failures"]] == ["KEY_NUMBER_NOT_IN_LINE"]


def test_an_exact_cta_line_is_allowed_in_the_message_other_steer_wording_is_not():
    msg = lambda t: [f["code"] for f in checks.check_line({"kind": "message", "text": t, "claim_ids": ["regain"]},
                                                          CLAIMS, BRAND, RULES, WEST)]
    assert msg("On average weight came back at 0.4 kg a month. Talk to a Steer doctor.") == []
    assert msg("On average weight came back at 0.4 kg a month. A Steer doctor can talk it through.") == ["STEER_TEAM_CLAIM"]


CTX = [{"id": "ctx-1", "sids": ["p0001-s0020"], "quote": "We compared diabetes incidence in South Asians aged ≥45 years in "
        "urban India (Chennai and Delhi) and Pakistan (Karachi)"}]


def context_router(seen):
    """A fake checker that reads the prompt: a stated place must be in the line's quotes or the study context."""
    def connector(ep, system, prompt, schema, **kw):
        seen.append(prompt)
        hp = schema["properties"]["health"]["items"]["properties"]["line_id"]["enum"]
        health = []
        for block in prompt.split("\n\n"):
            if block.startswith("HEALTH line_id: "):
                lid, line = block.split("\n")[0][16:], block.split("\n")[1]
                ok = all(city in prompt.split("HEALTH line_id")[0] for city in ("Mumbai",) if city in line)
                health.append({"line_id": lid, "verdict": "supported" if ok else "population_generalised", "reason": "r",
                               "context_used": ["ctx-1"] if "Karachi" in line else []})
        return {"health": [h for h in health if h["line_id"] in hp], "framing": []}, None
    return Router({"endpoints": EP, "jobs": {"crosscheck": {"chain": ["opencode_glm"]}}},
                  connectors={"openai_compatible": connector}, env={"OPENCODE_GO_API_KEY": "k"})


def test_study_context_lets_a_line_state_who_was_studied():
    line = {"kind": "health", "path": "static/headline", "claim_ids": ["men_res"],
            "text": "In Chennai, Delhi and Karachi, South Asian men aged 45 and over with BMI under 25 kg/m2 had a "
                    "3 times higher risk of diabetes than US whites."}
    seen = []
    res = checks.crosscheck_lines([line], CLAIMS, context_router(seen), "claude_sonnet", CTX)
    assert "STUDY CONTEXT (data, from the same paper):\nctx-1: We compared" in seen[0]
    assert res["static/headline"]["verdict"] == "supported"
    assert res["static/headline"]["context_used"] == CTX                       # the trace keeps the sentence and sid
    mumbai = line | {"text": line["text"].replace("Karachi", "Mumbai")}           # a place in neither quote nor context
    assert checks.crosscheck_lines([mumbai], CLAIMS, context_router([]), "claude_sonnet", CTX)[
        "static/headline"]["verdict"] == "population_generalised"
    schema = checks.line_schema(["a"], [], ["ctx-1"])
    assert schema["properties"]["health"]["items"]["properties"]["context_used"]["items"]["enum"] == ["ctx-1"]


def test_a_context_quote_missing_from_the_paper_is_a_load_error(tmp_path):
    from steer_pipeline.text import ingest_text
    p = tmp_path / "paper.txt"
    p.write_text("We compared diabetes incidence in South Asians aged 45 years and over in three cities.\n")
    doc = ingest_text(p, "evidence")
    ok = {"context_quotes": ["We compared diabetes incidence in South Asians aged 45 years and over"]}
    assert checks.study_context(ok, doc)[0]["sids"]
    with pytest.raises(ValueError, match="fails the gate"):
        checks.study_context({"context_quotes": ["South Asians in Mumbai were studied for ten years"]}, doc)


def test_the_key_pair_is_cross_checked_read_alone():
    keyed = copy.deepcopy(GOOD)
    keyed["slides"][1] |= {"key_number": "0.4 kg a month", "key_label": "weight came back"}
    seen = []

    def answer(hp, fp):  # the fake checker: the pair alone drops "on average"
        seen.extend(hp)
        out = says()(hp, fp)
        for v in out["health"]:
            if v["line_id"].endswith("/key"):
                v |= {"verdict": "qualifier_dropped", "reason": "read alone it drops 'on average'"}
        return out
    r = checks.check_post(keyed, CLAIMS, BRAND, RULES, WEST, fake_router(["opencode_glm"], {"glm-5.3": answer}), "claude_sonnet")
    assert "slides/1/key" in seen and r["status"] == "blocked"
    key = next(x for x in r["lines"] if x["path"] == "slides/1/key")
    assert key["text"] == "0.4 kg a month / weight came back" and key["claim_ids"] == ["regain"]
    assert [f["code"] for f in key["failures"]] == ["QUALIFIER_DROPPED"]
    r = checks.check_post(keyed, CLAIMS, BRAND, RULES, WEST, fake_router(["opencode_glm"], {"glm-5.3": says()}), "claude_sonnet")
    assert r["status"] == "draft_pending_review"
    assert 'line_id ends in "/key"' in checks.LINE_SYSTEM


def test_background_claims_never_stand_alone():
    cl = {"pop": C("pop", "A total of 12 983 ARIC participants were included in the analysis.", claim_type="population"),
          "meth": C("meth", "Median follow-up was 8.8 years.", claim_type="method"),
          "find": C("find", REGAIN, claim_type="finding")}
    pop = L("health", "A total of 12 983 ARIC participants were included.", ["pop"])
    meth = L("health", "Median follow-up was 8.8 years.", ["meth"])
    find = L("health", "On average weight came back at 0.4 kg a month, not a personal promise.", ["find"])
    bg = lambda p: [f["code"] for f in checks.check_background(checks.post_lines(p), cl)]
    assert bg(post([[find, pop]])) == []                                         # beside a finding: fine
    assert bg(post([[find], [pop]])) == ["BACKGROUND_ONLY_SLIDE"]                # a slide of its own: not
    assert bg(post([[find], [L("signoff", "steerclub.com")]], caption=(pop,))) == []  # in the caption: fine
    assert bg(post([[find, pop, meth]])) == ["TOO_MANY_BACKGROUND_CLAIMS"]


def test_the_cta_is_not_sent_to_the_checker_with_the_message():
    m = copy.deepcopy(GOOD) | {"message": "Weight came back at 0.4 kg a month on average. See if the programme fits you."}
    seen = []

    def connector(ep, system, prompt, schema, **kw):
        seen.append(prompt)
        hp = schema["properties"]["health"]["items"]["properties"]["line_id"]["enum"]
        fp = schema["properties"]["framing"]["items"]["properties"]["line_id"]["enum"]
        return says()(hp, fp), None
    r = Router({"endpoints": EP, "jobs": {"crosscheck": {"chain": ["opencode_glm"]}}},
               connectors={"openai_compatible": connector}, env={"OPENCODE_GO_API_KEY": "k"})
    out = checks.check_post(m, CLAIMS, BRAND, RULES, WEST, r, "claude_sonnet")
    assert "line: Weight came back at 0.4 kg a month on average.\n" in seen[0] and "programme" not in seen[0].split("message")[1]
    assert out["status"] == "draft_pending_review"
    assert next(x for x in out["lines"] if x["path"] == "message")["text"].endswith("fits you.")   # trace keeps it
