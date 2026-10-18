import json
import re
import tomllib
from pathlib import Path

import pytest

from steer_pipeline import checks, pipeline, ps1, store
from steer_pipeline.router import load_settings

from test_pipeline import FIX, fake_router

SETTINGS = load_settings()
ROUNDS = tomllib.loads((ps1.PS1 / "rounds.toml").read_text())


def test_a_revealed_number_must_be_in_the_quote():
    assert ps1.number_in_quote("5.3 times", "higher risk of diabetes", "risk of diabetes ... was 5.3 times higher in South Asian")
    assert not ps1.number_in_quote("5.4 times", "higher risk", "risk ... was 5.3 times higher")
    # a magnitude without its minus sign only when the label says which way it went
    assert ps1.number_in_quote("1.74 kg", "Absolute lean mass fell", "a significant decrease ... (−1.74 kg; 95% CI")
    assert not ps1.number_in_quote("1.74 kg", "Absolute lean mass changed", "a significant decrease ... (−1.74 kg; 95% CI")
    assert not ps1.number_in_quote("", "label", "quote 3")


@pytest.fixture
def stored(tmp_path):
    rec = pipeline.run(str(FIX / "gayathri2023-frontiers-two-column.pdf"), "evidence", SETTINGS, router=fake_router())
    db = tmp_path / "s.db"
    con = store.connect(db)
    store.save_run(con, rec)
    doc = json.loads(con.execute("SELECT document FROM documents").fetchone()[0])   # give the fixture a PMCID
    doc["citation"]["pmcid"] = "PMCTEST"
    con.execute("UPDATE documents SET document = ?", (json.dumps(doc),))
    con.commit()
    return db


def rounds_file(tmp_path, extra=(), **override):
    """The real rounds.toml copy with one hook round on the fixture paper (the real rounds' papers are not stored)."""
    hook = {"id": "hook", "doubt": "is_it_for_me", "pmcid": "PMCTEST", "kind": "slider", "context": None,
            "quote_contains": "A total of 352 participants completed the study",
            "question": "Guess how many people finished the study?",
            "stops": [["About a hundred", 100], ["About three hundred", 300], ["About six hundred", 600]], "answer": 352,
            "sentence": "A total of 352 participants completed the study.", "reveal_number": "352",
            "reveal_label": "participants completed the study", "qualifiers": [], "opener": "chai", "design": "single_study"} | override
    spec = dict(ROUNDS)
    spec["round"] = [hook, *extra]
    spec["doubt"] = [d | {"answered_by": ["hook"]} if d["id"] == "is_it_for_me" else
                     d | {"answered_by": [x["id"] for x in extra if x["doubt"] == d["id"]] or ["gap-safety"]}
                     for d in ROUNDS["doubt"]]

    def toml(v):
        if isinstance(v, str):
            return json.dumps(v, ensure_ascii=False)
        if isinstance(v, bool):
            return "true" if v else "false"
        if isinstance(v, (int, float)):
            return str(v)
        if isinstance(v, list):
            return "[" + ", ".join(toml(x) for x in v) + "]"
        return "{" + ", ".join(f"{k} = {toml(x)}" for k, x in v.items() if x is not None) + "}"
    lines = [f"{k} = {toml(v)}" for k, v in spec.items() if not isinstance(v, (dict, list)) or k == "x"]
    for k in ("ui", "readout", "cta", "warm_lead", "doctor_questions"):
        lines.append(f"[{k}]")
        lines += [f"{kk} = {toml(vv)}" for kk, vv in spec[k].items()]
    for k in ("opener", "doubt", "round", "gap"):
        for item in spec[k]:
            lines.append(f"[[{k}]]")
            lines += [f"{kk} = {toml(vv)}" for kk, vv in item.items() if vv is not None]
    p = tmp_path / "rounds.toml"
    p.write_text("\n".join(lines) + "\n")
    return p


def test_the_build_uses_a_verified_claim_and_writes_a_valid_claims_json(stored, tmp_path):
    rep = ps1.build(stored, tmp_path / "site", SETTINGS, rounds_file(tmp_path), playable=False, fonts=False)
    out = json.loads((tmp_path / "site" / "claims.json").read_text())
    assert out["hook"]["reveals"][0]["number"] == "352" and out["hook"]["quote"].startswith("A total of 352")
    src = out["hook"]["source"]   # the source line names the location once: the short cite is author and year only
    assert "," not in src["short"] and src["location"] not in src["short"], src
    assert rep["rounds"][0].startswith("hook: ") and "(verified)" in rep["rounds"][0]
    assert out["cta"]["primary"]["url"] == "https://steerclub.com/?utm_source=steer_quiz&utm_campaign=guess_the_number"
    assert (tmp_path / "site" / "index.html").exists() and "woff2" in (tmp_path / "site" / "style.css").read_text()


def test_the_build_fails_on_an_unusable_claim_and_on_bad_copy(stored, tmp_path):
    needs = {"quote_contains": "total cholesterol (−5.4 mg/dl", "sentence": "Total cholesterol fell by 5.4 mg/dl.",
             "reveal_number": "5.4 mg/dl", "reveal_label": "Total cholesterol fell", "answer": 5.4}
    with pytest.raises(ps1.BuildError, match="not usable"):
        ps1.build(stored, tmp_path / "a", SETTINGS, rounds_file(tmp_path, **needs), playable=False, fonts=False)
    with pytest.raises(ps1.BuildError, match="FRAMING_HAS_NUMBER"):
        ps1.build(stored, tmp_path / "b", SETTINGS, rounds_file(tmp_path, question="Guess how many of 400 finished?"),
                  playable=False, fonts=False)
    with pytest.raises(ps1.BuildError, match="not a number in the claim's quote"):
        ps1.build(stored, tmp_path / "c", SETTINGS, rounds_file(tmp_path, sentence="A total of 325 participants completed the study.",
                                                                 reveal_number="325"), playable=False, fonts=False)
    with pytest.raises(ps1.BuildError, match="copied from its sentence"):
        ps1.build(stored, tmp_path / "d", SETTINGS, rounds_file(tmp_path, reveal_label="people finished"),
                  playable=False, fonts=False)


def test_an_unusable_round_with_a_fallback_becomes_its_gap_card(stored, tmp_path):
    needs = {"quote_contains": "total cholesterol (−5.4 mg/dl", "sentence": "Total cholesterol fell by 5.4 mg/dl.",
             "reveal_number": "5.4 mg/dl", "reveal_label": "Total cholesterol fell", "answer": 5.4}
    with pytest.raises(ps1.BuildError, match="hook round has no usable claim"):   # the hook cannot fall back
        ps1.build(stored, tmp_path / "e", SETTINGS, rounds_file(tmp_path, **needs, fallback_gap="gap-safety"),
                  playable=False, fonts=False)
    chol = {"id": "chol", "doubt": "will_weight_come_back", "pmcid": "PMCTEST", "kind": "choice",
            "question": "Did total cholesterol go up or down?", "stops": [["Up", "up"], ["Down", "down"]], "answer": "down",
            "qualifiers": [], "fallback_gap": "gap-regain", "opener": "bus", "design": "review"} | needs
    rep = ps1.build(stored, tmp_path / "f", SETTINGS, rounds_file(tmp_path, extra=[chol]), playable=False, fonts=False)
    out = json.loads((tmp_path / "f" / "claims.json").read_text())
    assert rep["fallbacks"] and "-> gap card gap-regain" in rep["fallbacks"][0]
    assert next(d for d in out["doubts"] if d["id"] == "will_weight_come_back")["answered_by"] == ["gap-regain"]
    assert [r["id"] for r in out["rounds"]] == [] and "gap-regain" in [g["id"] for g in out["gap_cards"]]


def test_only_the_fixed_strings_get_the_disclaimer_and_qualifier_exemptions(stored, tmp_path):
    def build_with(**top):
        p = rounds_file(tmp_path)
        text = p.read_text()
        for k, v in top.items():
            text = re.sub(rf'^{k} = .*$', f'{k} = {json.dumps(v, ensure_ascii=False)}', text, count=1, flags=re.M)
        p.write_text(text)
        return ps1.build(stored, tmp_path / "x", SETTINGS, p, playable=False, fonts=False)
    assert ROUNDS["footer"] in ps1.DISCLAIMERS
    build_with()                                                                     # the fixed footer passes
    with pytest.raises(ps1.BuildError, match="STEER_TEAM_CLAIM"):                   # any other wording: the usual rules
        build_with(footer="Checked and loved by a Steer doctor, trust us")
    # a qualifier not in the fixed list is checked as framing (avoid phrases and numbers included)
    with pytest.raises(ps1.BuildError, match="FRAMING_HAS_NUMBER"):
        ps1.build(stored, tmp_path / "y", SETTINGS, rounds_file(tmp_path, qualifiers=["Only 3 studies looked at this."]),
                  playable=False, fonts=False)
    assert "A comparison between groups in one study, not a prediction for you." in ps1.FIXED_QUALIFIERS


# --- PS1 v2 ---
ROUND = {r["id"]: r for r in ROUNDS["round"]}
GAP = {g["id"]: g for g in ROUNDS["gap"]}
OPENERS = {o["id"]: o["text"] for o in ROUNDS["opener"]}


def test_the_hook_is_one_tap_choice_with_no_label_equal_to_the_answer():
    h = ROUND["hook"]
    assert h["kind"] == "choice" and 3 <= len(h["stops"]) <= 4
    assert h["question"] == "Think a 'normal' BMI rules out diabetes?"
    assert not any(str(h["answer"]) in label or h["reveal_number"] in label for label, _ in h["stops"])
    assert h["stops"][-1][0] == "5× or more" and h["stops"][-1][1] <= h["answer"]   # 5.3 falls in the last bin
    assert ps1.FORBIDDEN_COPY.search("A slim woman") and ps1.FORBIDDEN_COPY.search("How high is her risk?")
    assert not ps1.FORBIDDEN_COPY.search(json.dumps(ROUNDS, ensure_ascii=False))     # nothing in the real copy


def test_a_forbidden_hook_wording_fails_the_build(stored, tmp_path):
    with pytest.raises(ps1.BuildError, match="FORBIDDEN_COPY"):
        ps1.build(stored, tmp_path / "s", SETTINGS, rounds_file(tmp_path, question="Guess how a slim woman finished?"),
                  playable=False, fonts=False)


def test_every_round_and_card_has_an_opener_and_neighbours_differ():
    for x in [*ROUNDS["round"], *ROUNDS["gap"]]:
        assert x["opener"] in OPENERS, x["id"]
    for text in OPENERS.values():
        assert not re.search(r"\d", text) and not checks.framing_attributes(text, "")
    assert checks.framing_attributes("A morning walk in the park", "")           # "walk" is not stated by any claim
    order = [i for d in ROUNDS["doubt"] for i in d["answered_by"]]
    seq = ["hook"] + [i for i in order if i != "hook"]
    ops = [(ROUND.get(i) or GAP[i])["opener"] for i in seq]
    assert all(a != b for a, b in zip(ops, ops[1:])), ops
    for g, rs in {g: [r for r in ROUNDS["round"] if r.get("fallback_gap") == g] for g in GAP}.items():
        assert not rs or GAP[g]["opener"] in {r["opener"] for r in rs}        # a card keeps the opener of a round it replaces


def test_a_missing_opener_fails_the_build(stored, tmp_path):
    with pytest.raises(ps1.BuildError, match="no opener"):
        ps1.build(stored, tmp_path / "o", SETTINGS, rounds_file(tmp_path, opener="nope"), playable=False, fonts=False)


def test_no_round_uses_laverde_c007_03_and_none_is_a_family_claim():
    tirz = "we were unable to obtain complete data for tirzepatide"
    for r in ROUNDS["round"]:
        assert r["quote_contains"].casefold() not in tirz and tirz not in r["quote_contains"].casefold()
        assert "family" not in r["quote_contains"].casefold()


def test_the_safety_round_has_no_number_and_carries_the_papers_qualifiers():
    r = ROUND["safety"]
    b = checks.load_boundaries("PMC12532569")
    assert r["pmcid"] == "PMC12532569" and r["kind"] == "choice" and r["reveal_number"] == "" and r["fallback_gap"] == "gap-safety"
    assert not ps1.find_numbers(r["sentence"]) and r["answer"] == "stomach"
    assert r["question"] == "Which kind of side effect was most common in these trials?"
    assert not any(l.casefold() == r["answer"] for l, _ in r["stops"])
    assert set(r["qualifiers"]) <= ps1.FIXED_QUALIFIERS
    text = " ".join(r["qualifiers"])
    for rule in b["require_qualifier"]:              # class, in trials, a doctor weighs benefits and side effects
        assert any(checks._has(text, w) for w in rule["any_of"]), rule["why"]
    # no claim of this round or the dose claims is in the quiz; the paper's drug and null-result claims stay out of posts
    assert checks.excluded({"quote": "Only semaglutide was associated with GERD."}, b)
    assert not checks.excluded({"quote": r["quote_contains"]}, b)
    assert all("dose" not in x["quote_contains"] for x in ROUNDS["round"])


def test_a_round_without_a_number_must_have_a_numberless_sentence(stored, tmp_path):
    with pytest.raises(ps1.BuildError, match="without a revealed number"):
        ps1.build(stored, tmp_path / "n", SETTINGS, rounds_file(tmp_path, reveal_number="", reveal_label="participants completed"),
                  playable=False, fonts=False)


def test_the_diabetics_card_is_an_info_card_with_the_gate_checked_context_line():
    g, b = GAP["gap-diabetics"], checks.load_boundaries("PMC12532569")
    assert g["context_phrase"] in b["context_quotes"][0] and g["context_phrase"] in g["context"]
    assert not re.search(r"\d", g["context"]) and "verified" not in json.dumps(g).casefold()
    assert "gap-diabetics" in next(d for d in ROUNDS["doubt"] if d["id"] == "only_for_diabetics")["answered_by"]


def test_progress_and_warm_lead_copy_are_fixed_strings():
    assert ROUNDS["ui"]["progress"] == "Question {n} of {m}"
    note = "Prototype only. Nothing is sent or stored. Use test details, not real contact information."
    assert ROUNDS["warm_lead"]["note"] == note and note in ps1.DISCLAIMERS
    assert ROUNDS["ui"]["progress"].replace("{n}", "").replace("{m}", "")


def test_the_gl1_digit_exception_is_only_the_exact_token():
    assert not ps1.has_number("All weight-management medicines, not only GLP-1.")
    for bad in ("Only GLP-2 was studied.", "Fell by 1 kg.", "GLP-10", "GLP-1 and 2 kg", "GLP-12"):
        assert ps1.has_number(bad), bad


def test_safety_qualifier_wording_and_titles():
    r = ROUND["safety"]
    assert "Measured in clinical trials." in r["qualifiers"] and not any("everyday" in q for q in r["qualifiers"])
    b = checks.load_boundaries("PMC12532569")
    assert any(checks._has("Measured in clinical trials.", w) for w in next(x for x in b["require_qualifier"] if "trials" in x["why"])["any_of"])
    assert ROUNDS["readout"]["meaning_title"] == "What these studies tell us — and what they don't"
    assert r["reveal_label"] == "Mostly stomach and gut side effects"
    assert ROUND["hook"]["prompt"] == "Guess: how did diabetes rates compare?"


def test_every_surface_draws_the_qualifiers_through_one_helper():
    """The reveal, the readout, the form preview and the copied/shared text all take a round's qualifiers from quals()/qualList()
    (the browser play in ps1/test/offline_check.py asserts the rendered text of each surface)."""
    js = (ps1.PS1 / "app.js").read_text()
    body = lambda name: js.split(f"function {name}(", 1)[1].split("\nfunction ", 1)[0]
    assert "qualList(round)" in body("reveal") and "qualList(g.round" in body("readout")
    assert "qualList(g.round" in body("warmLead") and "quals(g.round)" in body("warmLead")   # preview and copy/share text
    for r in ROUNDS["round"]:   # a finding always has qualifiers: its own, or the one-study note for a single study
        assert ps1.merged_qualifiers(r, ROUNDS), r["id"]


def real_built_rounds(drop=None):
    """The real rounds.toml in claims.json form, with stand-in claims whose quote holds the sentence (no store needed)."""
    out, claims = {}, {}
    for r in ROUNDS["round"]:
        cid = f"c-{r['id']}"
        quals = ps1.merged_qualifiers(r, ROUNDS)
        if drop and drop[0] == r["id"]:
            quals = [q for q in quals if q != drop[1]]
        out.setdefault(r["pmcid"], []).append({"id": r["id"], "claim_ids": [f"run/{cid}"], "qualifiers": quals, "context": r.get("context"),
                                               "reveals": [{"sentence": r["sentence"], "label": r["reveal_label"]}]})
        claims[cid] = {"claim_id": cid, "quote": r["quote_contains"] + " " + r["sentence"]}
    return out, claims


def test_the_real_rounds_pass_every_papers_boundaries_with_the_post_engine():
    out, claims = real_built_rounds()
    for pm, rounds in out.items():
        assert ps1.boundary_failures(rounds, claims, checks.load_boundaries(pm)) == [], pm


@pytest.mark.parametrize("round_id,qualifier,paper_rule", [
    ("regain", "An average across studies, not a prediction for you.", "average"),
    ("muscle-a", "Results varied a lot between studies.", "heterogeneity"),
    ("muscle-b", "The authors advise nutrition and exercise alongside treatment.", "nutrition"),
    ("safety", "Measured in clinical trials.", "trials"),
    ("safety", "These findings are about this class of medicines, not one product.", "class"),
    ("safety", "A doctor weighs the benefits against the side effects for each person.", "doctor")])
def test_a_round_missing_a_required_qualifier_fails_by_the_post_engine(round_id, qualifier, paper_rule):
    out, claims = real_built_rounds(drop=(round_id, qualifier))
    pm = ROUND[round_id]["pmcid"]
    fails = ps1.boundary_failures(out[pm], claims, checks.load_boundaries(pm))
    assert [f["code"] for f in fails] == ["QUALIFIER_MISSING"], fails


def test_the_build_fails_naming_the_rule_when_a_required_qualifier_is_missing(stored, tmp_path, monkeypatch):
    rule = {"when_claim_quote_contains": [], "placement": "post", "any_of": ["zebra stripes"], "why": "test rule: needs zebra"}
    monkeypatch.setattr(ps1, "load_boundaries", lambda pm: {"require_qualifier": [rule]})
    with pytest.raises(ps1.BuildError, match="QUALIFIER_MISSING.*test rule: needs zebra"):
        ps1.build(stored, tmp_path / "q", SETTINGS, rounds_file(tmp_path), playable=False, fonts=False)
    # the same rule, met by a qualifier (checked as framing copy like any other)
    ps1.build(stored, tmp_path / "r", SETTINGS, rounds_file(tmp_path, qualifiers=["Zebra stripes are noted."]), playable=False, fonts=False)


def test_an_excluded_claim_cannot_reach_a_round(stored, tmp_path, monkeypatch):
    monkeypatch.setattr(ps1, "load_boundaries", lambda pm: {"exclude_from_posts": [{"quote_any_of": ["352 participants"], "why": "test"}]})
    with pytest.raises(ps1.BuildError, match="not usable"):
        ps1.build(stored, tmp_path / "x", SETTINGS, rounds_file(tmp_path), playable=False, fonts=False)


def test_muscle_b_does_not_carry_the_high_heterogeneity_caveat():
    assert "Results varied a lot between studies." in ROUND["muscle-a"]["qualifiers"]       # the -1.74 kg result (I2 = 98%)
    assert not any("varied" in q or "vary" in q for q in ps1.merged_qualifiers(ROUND["muscle-b"], ROUNDS))   # +1.81% had I2 = 7%


def test_the_one_study_note_is_only_for_single_studies(stored, tmp_path):
    note = ROUNDS["single_study_note"]
    for r in ROUNDS["round"]:
        assert r["design"] in ("single_study", "review")
        assert (note in ps1.merged_qualifiers(r, ROUNDS)) == (r["design"] == "single_study"), r["id"]
    assert {r["id"] for r in ROUNDS["round"] if r["design"] == "single_study"} == {"hook"}   # Narayan, a cohort study
    assert "reveal_note" not in (ps1.PS1 / "app.js").read_text() and "reveal_note" not in json.dumps(ROUNDS)
    ps1.build(stored, tmp_path / "a", SETTINGS, rounds_file(tmp_path), playable=False, fonts=False)
    assert note in json.loads((tmp_path / "a" / "claims.json").read_text())["hook"]["qualifiers"]
    ps1.build(stored, tmp_path / "b", SETTINGS, rounds_file(tmp_path, design="review"), playable=False, fonts=False)
    out = json.loads((tmp_path / "b" / "claims.json").read_text())
    assert all(note not in r["qualifiers"] for r in [out["hook"], *out["rounds"]])      # a review never carries it
    with pytest.raises(ps1.BuildError, match="design must be"):
        ps1.build(stored, tmp_path / "c", SETTINGS, rounds_file(tmp_path, design="cohort"), playable=False, fonts=False)
