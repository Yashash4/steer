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
]


def fake_router():
    def connector(ep, system, prompt, schema, **kw):
        if "claims" in schema["properties"]:   # extraction
            return {"claims": PAGE2 if "Section (page 2)" in prompt else []}, {"input_tokens": 10, "output_tokens": 5}
        ids = schema["properties"]["labels"]["items"]["properties"]["claim_id"]["enum"]   # labels
        return {"labels": [{"claim_id": i, "theme": "insulin_resistance", "doubts": ["is_it_for_me"]} for i in ids]}, None
    settings = SETTINGS | {"jobs": {"extract": {"chain": ["f"]}, "label": {"chain": ["f"]}},
                           "endpoints": {"f": {"connector": "fake", "model": "fake"}}}
    return Router(settings, connectors={"fake": connector}, env={})


def test_run_and_store(tmp_path):
    rec = pipeline.run(str(FIX / "gayathri2023-frontiers-two-column.pdf"), "evidence", SETTINGS, router=fake_router())
    s = rec["summary"]
    assert (s["proposed"], s["passed"], s["rejected"]) == (4, 2, 2)
    assert s["labelled"] == 2 and s["unlabeled"] == 0
    assert set(rec["timings"]) == {"ingest", "extract_and_verify", "label", "rules"}
    assert all(c["labels"] == {"theme": "insulin_resistance", "doubts": ["is_it_for_me"]} for c in rec["passed"])

    db = tmp_path / "steer.db"
    con = store.connect(db)
    store.save_run(con, rec)
    rows = con.execute("SELECT status, count(*) FROM claims GROUP BY status ORDER BY status").fetchall()
    assert rows == [("passed", 2), ("rejected", 2)]
    # nothing unverified is stored as passed
    for status, gate in con.execute("SELECT status, gate FROM claims"):
        assert (json.loads(gate)["status"] == "pass") == (status != "rejected")
    sids = json.loads(con.execute("SELECT sids FROM claims WHERE claim_text LIKE 'A total of 352%'").fetchone()[0])
    assert sids == ["p0002-s0002"]
    run_row = con.execute("SELECT input, settings_version, rules_version, model_calls FROM runs").fetchone()
    assert run_row[1] == SETTINGS["version"] and run_row[2] == SETTINGS["rules"]["version"]
    assert len(json.loads(run_row[3])) == len(rec["model_calls"]) > 0

    exported = store.export_run(con, rec["run_id"])
    assert exported["run"]["summary"]["passed"] == 2 and len(exported["claims"]) == 4
    assert {c["status"] for c in exported["claims"]} == {"passed", "rejected"}

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
    assert sqlite3.connect(db).execute("SELECT count(*) FROM claims").fetchone()[0] == 4


@pytest.mark.network
def test_live_run_on_narayan(tmp_path):
    # the real chain from steer.toml (Claude Code headless), real labels; counts depend on the model
    rec = pipeline.run("PMC8006839", "evidence", SETTINGS)
    con = store.connect(tmp_path / "live.db")
    store.save_run(con, rec)
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
