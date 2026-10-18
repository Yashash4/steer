import json
import sqlite3
from pathlib import Path

import pytest

from steer_pipeline import pipeline, store
from steer_pipeline.router import Router, load_settings

FIX = Path(__file__).parent / "fixtures"
SETTINGS = load_settings()


def claim(text, quote, **kw):
    base = {"claim_text": text, "quote": quote, "claim_type": "finding", "population": None, "hedged": False,
            "direction": "other", "outcome": None, "comparison_group": None, "time_point": None, "limitations": None}
    return base | kw


PAGE2 = [  # hand-written model output (fake connector), Frontiers page 2
    claim("A total of 352 participants completed the study.", "A total of 352 participants completed the study."),
    claim("325 participants completed the study.", "A total of 352 participants completed the study."),
    claim("Almonds cure diabetes.", "Almonds cured diabetes in all participants."),
    claim("Insulin resistance (HOMA IR) and oral insulin disposition index (DIo) were calculated.",
          "Insulin resistance (homeostasis assessment model-HOMA IR) and oral insulin disposition index (DIo) were calculated."),
    claim("Total cholesterol changed by −5.4 mg/dl in the intervention group compared to the control group.",   # needs_check:
          "total cholesterol (−5.4 mg/dl (−10.2, −0.6); p = 0.03) in the intervention group compared to the control group"),
]
MIRACLE = claim("A total of 352 participants completed the study: a miracle.",  # planted hype, otherwise clean
                "A total of 352 participants completed the study.")


EXTRA: list = []


def fake_router():
    def connector(ep, system, prompt, schema, **kw):
        if "verdicts" in schema["properties"]:   # library cross-check: everything supported
            ids = schema["properties"]["verdicts"]["items"]["properties"]["claim_id"]["enum"]
            return {"verdicts": [{"claim_id": i, "verdict": "supported", "reason": "restates the quote"} for i in ids]}, None
        if "claims" in schema["properties"]:   # extraction
            return {"claims": PAGE2 + EXTRA if "Section (page 2)" in prompt else []}, {"input_tokens": 10, "output_tokens": 5}
        ids = schema["properties"]["labels"]["items"]["properties"]["claim_id"]["enum"]   # labels
        return {"labels": [{"claim_id": i, "theme": "insulin_resistance", "doubts": ["is_it_for_me"]} for i in ids]}, None
    settings = SETTINGS | {"jobs": {"extract": {"chain": ["f"]}, "label": {"chain": ["f"]}, "crosscheck": {"chain": ["chk"]}},
                           "endpoints": {"f": {"connector": "fake", "model": "fake"},
                                         "chk": {"connector": "fake", "model": "other", "base_url": "https://checker.example"}}}
    return Router(settings, connectors={"fake": connector}, env={})


def test_run_and_store(tmp_path):
    rec = pipeline.run(str(FIX / "gayathri2023-frontiers-two-column.pdf"), "evidence", SETTINGS, router=fake_router())
    s = rec["summary"]
    assert (s["proposed"], s["passed"], s["needs_check"], s["rejected"]) == (5, 2, 1, 2)
    assert s["labelled"] == 3 and s["unlabeled"] == 0  # passed AND needs_check are labelled, rejected never
    assert set(rec["timings"]) == {"ingest", "extract_and_verify", "label", "rules", "crosscheck"}
    # verified = clean + an independent "supported" cross-check; needs_check claims are checked but not verified
    assert s["verified"] == 2 and s["crosscheck"] == {"supported (different_provider)": 3}
    for c in rec["passed"] + rec["needs_check"]:
        assert c["labels"] == {"theme": "insulin_resistance", "doubts": ["is_it_for_me"]}
    assert all("labels" not in c for c in rec["rejected"])

    db = tmp_path / "steer.db"
    con = store.connect(db)
    store.save_run(con, rec)
    rows = con.execute("SELECT status, count(*) FROM claims GROUP BY status ORDER BY status").fetchall()
    assert rows == [("needs_check", 1), ("passed", 2), ("rejected", 2)]
    # nothing unverified is stored as passed
    for status, gate in con.execute("SELECT status, gate FROM claims"):
        assert (json.loads(gate)["status"] == "pass") == (status != "rejected")
    sids = json.loads(con.execute("SELECT sids FROM claims WHERE claim_text LIKE 'A total of 352%'").fetchone()[0])
    assert sids == ["p0002-s0002"]
    run_row = con.execute("SELECT input, settings_version, rules_version, model_calls FROM runs").fetchone()
    assert run_row[1] == SETTINGS["version"] and run_row[2] == SETTINGS["rules"]["version"]
    assert len(json.loads(run_row[3])) == len(rec["model_calls"]) > 0

    exported = store.export_run(con, rec["run_id"])
    assert len(exported["crosschecks"]) == 3 and {c["independence"] for c in exported["crosschecks"]} == {"different_provider"}
    assert exported["run"]["summary"]["passed"] == 2 and len(exported["claims"]) == 5
    assert {c["status"] for c in exported["claims"]} == {"passed", "needs_check", "rejected"}

    # a second run of the same paper adds a run; the document row is replaced, not duplicated
    rec2 = pipeline.run(str(FIX / "gayathri2023-frontiers-two-column.pdf"), "evidence", SETTINGS, router=fake_router())
    store.save_run(con, rec2)
    assert con.execute("SELECT count(*) FROM documents").fetchone()[0] == 1
    assert con.execute("SELECT count(*) FROM runs").fetchone()[0] == 2


def test_status_must_be_known(tmp_path):
    con = store.connect(tmp_path / "x.db")
    with pytest.raises(sqlite3.IntegrityError):
        con.execute("INSERT INTO claims (run_id, claim_id, document_id, status, claim_text, quote, sids, line_ids, gate, "
                    "flags, review_reasons, rule_flags) VALUES ('r', 'c', 'd', 'approved', 't', 'q', '[]', '[]', '{}', "
                    "'[]', '[]', '[]')")


def test_cli_run(tmp_path, monkeypatch):
    from steer_pipeline import cli
    monkeypatch.setattr(pipeline, "Router", lambda settings: fake_router())
    db, out = tmp_path / "s.db", tmp_path / "export.json"
    assert cli.main(["run", str(FIX / "gayathri2023-frontiers-two-column.pdf"), "--db", str(db), "--export", str(out)]) == 0
    data = json.loads(out.read_text())
    assert data["run"]["summary"]["passed"] == 2
    assert sqlite3.connect(db).execute("SELECT count(*) FROM claims").fetchone()[0] == 5


@pytest.mark.network
def test_live_run_on_narayan(tmp_path):
    # the real chain from steer.toml (Claude Code headless), real labels; counts depend on the model
    rec = pipeline.run("PMC8006839", "evidence", SETTINGS)
    con = store.connect(tmp_path / "live.db")
    store.save_run(con, rec)
    kept = len(rec["passed"]) + len(rec["needs_check"])
    # the library cross-check is stored with the run, one row per kept claim
    assert con.execute("SELECT count(*) FROM crosschecks").fetchone()[0] == kept == len(rec["crosschecks"])
    s = rec["summary"]
    assert s["chunks_failed"] == 0 and s["proposed"] > 0
    for status, gate, sids in con.execute("SELECT status, gate, sids FROM claims"):
        g = json.loads(gate)
        assert (g["status"] == "pass") == (status != "rejected")
        if status != "rejected":
            assert json.loads(sids)  # every kept claim has sentence locations
    passed = con.execute("SELECT theme FROM claims WHERE status = 'passed'").fetchall()
    assert all(t[0] in SETTINGS["labels"]["themes"] or t[0] is None for t in passed)
    print(json.dumps({"summary": s, "timings": rec["timings"]}))


def test_library_rule_hits_move_a_clean_claim_to_needs_check():
    EXTRA.append(MIRACLE)
    try:
        rec = pipeline.run(str(FIX / "gayathri2023-frontiers-two-column.pdf"), "evidence", SETTINGS, router=fake_router())
    finally:
        EXTRA.clear()
    m = next(c for c in rec["needs_check"] if c["claim_text"].endswith("a miracle."))
    assert m["review_reasons"] == ["PROMISE"] and m["flags"] == []  # clean at extraction, moved by the rules check
    assert not any(c["claim_text"].endswith("a miracle.") for c in rec["passed"])
    assert rec["summary"]["needs_check_by_reason"].get("PROMISE") == 1


def test_write_and_check_from_a_stored_run(tmp_path):
    from steer_pipeline import posts
    rec = pipeline.run(str(FIX / "gayathri2023-frontiers-two-column.pdf"), "evidence", SETTINGS, router=fake_router())
    con = store.connect(tmp_path / "s.db")
    store.save_run(con, rec)
    good = next(c["claim_id"] for c in rec["passed"] if c["claim_text"].startswith("A total of 352"))

    def L(kind, text, ids=()):
        return {"kind": kind, "text": text, "claim_ids": list(ids)}

    def connector(ep, system, prompt, schema, **kw):
        if "health" in schema["properties"]:  # line cross-check
            hp = schema["properties"]["health"]["items"]["properties"]["line_id"]["enum"]
            fp = schema["properties"]["framing"]["items"]["properties"]["line_id"]["enum"]
            return {"health": [{"line_id": i, "verdict": "supported", "reason": "r"} for i in hp if i != "-"],
                    "framing": [{"line_id": i, "asserts_medical_fact": False, "reason": "r"} for i in fp if i != "-"]}, None
        return {"message": "m", "reasoning": "r", "format": "carousel", "slides": [
            {"role": "hook", "lines": [L("framing", "Did people stay with the almond plan?")]},
            {"role": "point", "lines": [L("health", "A total of 352 participants completed the study.", [good])]},
            {"role": "brand", "lines": [L("brand", "Built for India. Local meals, culture and appetite.")]},
            {"role": "cta", "lines": [L("cta", "Talk to a Steer doctor"), L("signoff", "steerclub.com")]}],
            "caption": {"lines": [L("health", "A total of 352 participants completed the study.", [good]),
                                  L("signoff", "steerclub.com")], "hashtags": SETTINGS["writer"]["hashtags"][:5]},
            "reel_script": {"scenes": [{"lines": [L("framing", "Did people stay with the almond plan?") | {"show_large": True}]},
                                       {"lines": [L("health", "A total of 352 participants completed the study.", [good])]},
                                       {"lines": [L("cta", "Talk to a Steer doctor")]}]}}, None
    settings = SETTINGS | {"jobs": {"write": {"chain": ["f"]}, "crosscheck": {"chain": ["chk"]}},
                           "endpoints": {"f": {"connector": "fake", "model": "fake"},
                                         "chk": {"connector": "fake", "model": "other", "base_url": "https://checker.example"}}}
    router = Router(settings, connectors={"fake": connector}, env={})
    brief = {"format": "carousel", "theme": "insulin_resistance", "doubt": "is_it_for_me", "claim_ids": [good], "why": "test"}
    out = posts.write_and_check(con, rec["run_id"], "carousel", settings, router=router, brief=brief, out_dir=tmp_path / "out")
    assert out["status"] == "draft_pending_review", out["post_failures"]
    folder = Path(out["folder"])
    files = {p.name for p in folder.iterdir()}
    assert {"post.json", "trace.json", "caption.txt", "reel_script.json", "render_qa.json"} <= files
    assert out["render"]["status"] == "passed" and set(out["render"]["pngs"]) <= files  # drafts get their PNGs
    trace = json.loads((folder / "trace.json").read_text())
    health = [ln for ln in trace["lines"] if ln["kind"] == "health"]
    assert health and all(ln["claims"][0]["sids"] == ["p0002-s0002"] for ln in health)
    assert all(ln["crosscheck"]["independence"] == "different_provider" for ln in health)
    assert out["sources"][0]["location"] == "page 2" and "doi:" in (folder / "caption.txt").read_text()
