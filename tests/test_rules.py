import pytest

from steer_pipeline import rules
from steer_pipeline.router import load_settings

RULES = load_settings()["rules"]


def codes(text, profile="publication", **kw):
    return [(f["code"], f["action"], f["match"]) for f in rules.check(text, profile, RULES, **kw)]


def test_drug_brand_blocks_in_posts_and_flags_in_library():
    assert codes("Ask about Ozempic today") == [("DRUG_BRAND", "block", "Ozempic")]
    assert codes("Participants used ozempic", "claim_library") == [("DRUG_BRAND", "flag", "ozempic")]


def test_drug_name_is_a_flag_in_both_profiles():
    assert codes("Semaglutide lowers appetite") == [("DRUG_NAME", "flag", "Semaglutide")]
    assert codes("semaglutide 2.4 mg", "claim_library") == [("DRUG_NAME", "flag", "semaglutide")]


def test_promises_words_and_number_patterns():
    assert codes("A guaranteed result") == [("PROMISE", "block", "guaranteed")]
    assert codes("Lose 10 kg with us") == [("PROMISE", "block", "Lose 10 kg")]
    assert codes("In 8 weeks you will feel lighter") == [("PROMISE", "block", "In 8 weeks you will")]
    assert codes("5 kg in 4 weeks") == [("PROMISE", "block", "5 kg in 4 weeks")]


def test_hype_and_before_after():
    assert codes("The secret doctors use") == [("HYPE", "flag", "secret")]
    assert codes("See our before and after photos") == [("BEFORE_AFTER", "block", "before and after")]


def test_steer_team_claims_block_unless_from_a_steer_source():
    assert codes("Steer doctors helped 1000 members") == [("STEER_TEAM_CLAIM", "block", "Steer doctors helped 1000 members")]
    assert codes("Steer doctors review every plan", steer_source=True) == []


def test_causal_wording_needs_a_cause_in_the_source():
    q = "Diabetes incidence was 5.3 times higher in South Asian women than in white women."
    assert codes("Being South Asian causes diabetes", source_quote=q) == [("CAUSAL_WORDING", "flag", "causes")]
    assert codes("Poor sleep causes cravings", source_quote="Poor sleep causes cravings in adults.") == []
    assert codes("Being South Asian causes diabetes") == []  # no source given: not checked


def test_controls_and_profiles():
    clean = "Doctor-led care. Diagnostics first. Rotis, chai and steady habits."
    assert codes(clean) == [] and codes(clean, "claim_library") == []
    assert codes("A secret steering wheel") == [("HYPE", "flag", "secret")]  # "steering" is not "Steer"
    assert codes("cause-and-effect", source_quote="none") == []  # whole words only: no hyphen-joined hits
    for f in rules.check("Ozempic, semaglutide", "publication", RULES):
        assert f["profile"] == "publication" and f["rules_version"] == RULES["version"]
    with pytest.raises(ValueError):
        rules.check("x", "posts", RULES)


def test_list_entries_are_literal_not_regex():
    assert codes("a.b", "publication") == []
    custom = {"version": "t", "hype_words": [".*"]}
    assert rules.check("anything at all", "publication", custom) == []


def test_second_person_and_percentage_promises():
    assert codes("You could lose 15% of your weight")[0][:2] == ("PROMISE", "block")
    assert any(c[0] == "PROMISE" for c in codes("With this you will drop weight"))
    assert any(c[0] == "PROMISE" for c in codes("Lose up to 20% with the programme"))
    assert any(c[0] == "PROMISE" for c in codes("Shed 10% of your body weight"))
    # a reported finding in the third person, past tense, is not a promise
    finding = "Participants in clinical trials of semaglutide and tirzepatide lost 15-20% of their baseline body weight."
    assert [c[0] for c in codes(finding)] == ["DRUG_NAME", "DRUG_NAME"]
    assert codes("Weight fell by 5% in the trial group.") == []


def test_second_person_only_blocks_promises():
    assert any(c[0] == "PROMISE" for c in codes("You could lose 5 kg"))
    assert any(c[0] == "PROMISE" for c in codes("You'll lose weight fast"))
    assert any(c[0] == "PROMISE" for c in codes("You can drop 10% of your weight"))
    assert not any(c[0] == "PROMISE" for c in codes("Will you lose muscle?"))
    assert not any(c[0] == "PROMISE" for c in codes("You could lose muscle strength if you skip protein"))
    assert not any(c[0] == "PROMISE" for c in codes("When you lose weight, hunger hormones rise."))
