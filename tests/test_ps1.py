import json
import re
import tomllib
from pathlib import Path

import pytest

from steer_pipeline import pipeline, ps1, store
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
            "reveal_label": "participants completed the study", "qualifiers": []} | override
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
    for k in ("doubt", "round", "gap"):
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
            "qualifiers": [], "fallback_gap": "gap-regain"} | needs
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
