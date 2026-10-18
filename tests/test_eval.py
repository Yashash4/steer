import json

from steer_pipeline import cli, evalharness as ev, posts, store
from steer_pipeline.router import load_settings
from steer_pipeline.verify import _Doc, check_claim

SETTINGS = load_settings()
GOLD = {g["id"]: g for g in ev._load("gold.json")["claims"]}
ITEMS = [*ev._load("planted.json")["items"], *ev._load("posts.json")["items"]]
BY_ID = {i["id"]: i for i in ITEMS}


def papers_from_gold():
    """Stand-ins for the stored documents: one synthetic document per gold paper holding the gold sentence."""
    out = {}
    for g in GOLD.values():
        doc = ev.synth_doc(g["quote"] + " " + "The group was followed up for twelve months.")
        doc["citation"]["pmcid"] = g["pmcid"]
        out[g["pmcid"]] = {"doc": doc, "prepared": _Doc(doc)}
    return out


def test_fixtures_declare_expectations_and_label_synthetic():
    assert len(ITEMS) == len(BY_ID) and len(GOLD) == 3
    assert all(i["expect"]["category"] for i in ITEMS) and all(i["type"] in ("error", "control", "regression") for i in ITEMS)
    assert all(BY_ID[i].get("synthetic") for i in ("E5", "E6", "E9", "E2"))
    for i in ITEMS:
        if i["kind"] == "gate" and not i.get("doc_text"):
            assert i.get("base") or i.get("quote")


def test_synthetic_gate_items_and_rules():
    acts = ev.run_offline([BY_ID[i] for i in ("E5", "E6", "E9", "E4", "E4-ctl")], {}, {}, SETTINGS["rules"], {})
    rows = {r["id"]: r for r in ev.count_judged([BY_ID[i] for i in acts], acts)}
    assert rows["E5"]["result"] == "caught_expected" and rows["E4"]["result"] == "caught_expected"
    assert rows["E6"]["result"] == "ok" and rows["E4-ctl"]["result"] == "ok"
    assert rows["E9"]["result"] == "ok" and "HEDGE_DROPPED" in acts["E9"]["codes"]


def test_judge_reports_missed_and_blocked_honestly():
    err = {"type": "error", "expect": {"category": "gate_reject", "code": "X"}}
    assert ev.judge(err, ev.result("gate_pass", [], False)) == "not_caught"
    assert ev.judge(err, ev.result("gate_reject", ["Y"], True)) == "caught_other"
    assert ev.judge(err, ev.result("gate_reject", ["X"], True)) == "caught_expected"
    assert ev.judge(err, None) == "not_run"
    ctl = {"type": "control", "expect": {"category": "gate_pass"}}
    assert ev.judge(ctl, ev.result("gate_reject", ["X"], True)) == "false_alarm"
    assert ev.judge(ctl, ev.result("gate_flag", ["TABLE_TEXT"], True)) == "ok"


def test_a_mutated_gate_lets_a_planted_error_through_and_the_harness_counts_it():
    papers, items = papers_from_gold(), [BY_ID["E1"]]
    real = ev.section_integrity(ev.count_judged(items, ev.run_offline(items, papers, {}, SETTINGS["rules"], {})))
    assert real["passed_through"] == 0 and real["n"] == 1
    bad = ev.section_integrity(ev.count_judged(items, ev.run_offline(items, papers, {}, SETTINGS["rules"], {}, ev.mutated_gate)))
    assert bad["passed_through"] == 1 and bad["ids_passed_through"] == ["E1"]
    st = ev.self_test(ITEMS, papers, {}, SETTINGS["rules"], {})
    assert st["integrity_count_with_mutation"] == 1 and st["harness_detects_it"]


def test_a_missing_check_is_reported_as_not_caught(monkeypatch):
    papers, items = papers_from_gold(), [BY_ID["E2"]]
    monkeypatch.setattr(ev, "check_claim", lambda *a, **k: {"status": "pass", "reasons": [], "warnings": [], "matches": []})
    acts = ev.run_offline(items, papers, {}, SETTINGS["rules"], {}, ev.check_claim)
    rows = ev.count_judged(items, acts)
    assert rows[0]["result"] == "not_caught"
    d = ev.section_detection(rows)
    assert d["not_caught"] == ["E2"] and "not caught: no such check yet" in d["known_gaps"][0]


def test_report_from_a_small_store(tmp_path):
    g = GOLD["G1"]
    doc = papers_from_gold()[g["pmcid"]]["doc"]
    claim = {"claim_id": "c000-00", "claim_text": "Weight came back at 0.4 kg a month on average", "quote": g["quote"], "claim_type": "number",
             "population": None, "hedged": False, "direction": "other", "chunk": 0, "flags": [], "review_reasons": [], "labels": {"theme": "weight_regain", "doubts": []},
             "gate": check_claim({"claim_id": "c000-00", "text": "Weight came back at 0.4 kg a month on average", "document_id": doc["document_id"], "quote": g["quote"]},
                                 {doc["document_id"]: _Doc(doc)})}
    sids = [s["sid"] for s in doc["sentences"]]
    rec = {"run_id": "run-test", "started_at": "2026-10-18T05:00:00+00:00", "finished_at": "2026-10-18T05:01:00+00:00", "input": "x", "document": doc,
           "settings_version": 1, "rules_version": "rules-1", "timings": {"extract_and_verify": 1.0}, "summary": {"verified": 1}, "label_result": {},
           "chunks": [{"index": 0, "sids": sids, "status": "ok", "endpoint": "claude_sonnet"}], "model_calls": [{"tokens": {"input_tokens": 10, "output_tokens": 5}}],
           "passed": [claim], "needs_check": [], "rejected": [],
           "crosschecks": [{"claim_id": "c000-00", "verdict": "supported", "reason": "ok", "endpoint": "opencode_glm", "model": "glm-5.3", "provider": "opencode.ai", "independence": "different_provider"}]}
    con = store.connect(tmp_path / "s.db")
    store.save_run(con, rec)
    con.close()
    rep = ev.evaluate(tmp_path / "s.db", tmp_path / "out", settings=SETTINGS)
    assert rep["4_extraction_coverage"]["found"] == 1 and rep["4_extraction_coverage"]["n"] == 3
    f = rep["5_library_funnel"][0]
    assert (f["proposed"], f["verified_recomputed"], f["sentences_covered"], f["model_calls"]) == (1, 1, len(sids), 1)
    assert rep["7_human_time_per_paper"]["papers"][0]["status"] == "not measured yet"
    assert rep["harness_self_test"]["integrity_count_with_mutation"] == 1
    assert (tmp_path / "out" / f"report-{rep['date']}.md").exists() and json.loads((tmp_path / "out" / f"report-{rep['date']}.json").read_text())["mode"].startswith("offline")


def test_cli_eval_runs(tmp_path, capsys):
    con = store.connect(tmp_path / "e.db")
    con.close()
    assert cli.main(["eval", "--db", str(tmp_path / "e.db"), "--out", str(tmp_path / "o")]) == 0
    assert "1_verification_integrity" in capsys.readouterr().err
