"""steer batch: the planner's constraints, retry-once-then-unfilled, the .ics file, the S6 order, usable claims, week.md."""

import json
import re
from datetime import date, datetime
from types import SimpleNamespace

import pytest

from steer_pipeline import batch

TOP = ("Planned by `steer batch` from 4 papers; order from the learning loop (benchmark of 11 public posts + our own "
       "rejections); every post pending doctor review.")


def cell(pm, theme, doubt, n=6):
    d = {"is_it_safe": "safe", "will_i_lose_muscle": "muscle", "will_weight_come_back": "regain", "is_it_for_me": "for_me",
         "only_for_diabetics": "only_diabetics"}[doubt]
    return {"pmcid": pm, "run_id": "run-" + pm, "theme": theme, "doubt": doubt, "short": d,
            "claims": [{"claim_id": f"c{i:03d}", "claim_type": "finding", "labels": {"theme": theme, "doubts": [doubt]}} for i in range(n)]}


CELLS = [cell("PMC1", "t_lean", "will_i_lose_muscle"), cell("PMC2", "t_safety", "is_it_safe"), cell("PMC3", "t_ir", "is_it_for_me"),
         cell("PMC3", "t_food", "is_it_for_me"), cell("PMC4", "t_regain", "will_weight_come_back")]
SCORES = {d: {"doubt": d, "opp": o, "k": 1, "n": 11, "c": 0, "m": 52, "flags_rejections": 0}
          for d, o in (("muscle", 0.27), ("safe", 0.09), ("for_me", 0.01), ("regain", 0.0), ("only_diabetics", 0.0))}
START = date(2026, 10, 20)


def test_the_planned_week_keeps_every_constraint():
    p = batch.plan_week(CELLS, SCORES, START)
    s = p["slots"]
    assert p["conflicts"] == [] and len(s) == 7 and batch.check_plan(s, 7) == []
    assert [x["format"] for x in s] == ["carousel", "static", "reel", "carousel", "static", "reel", "carousel"]
    assert all(a["theme"] != b["theme"] for a, b in zip(s, s[1:]))
    assert max(sum(1 for x in s if x["doubt"] == d) for d in {x["doubt"] for x in s}) <= 2
    assert [x["date"] for x in s][:2] == ["2026-10-20", "2026-10-21"] and all(x["reason"] for x in s)
    seen = [c for x in s for c in [(x["pmcid"], x["theme"], tuple(x["claims"]))]]
    assert len({c[2] for c in seen if c[:2] == seen[0][:2]}) == len([c for c in seen if c[:2] == seen[0][:2]])   # a cell's claim sets differ


def test_a_violation_is_rejected_and_an_unsatisfiable_week_is_reported_not_forced():
    mk = lambda i, theme, doubt, fmt: {"slot": i, "date": f"2026-10-{20 + i}", "theme": theme, "doubt": doubt, "format": fmt}
    bad = [mk(0, "a", "x", "reel"), mk(1, "a", "x", "reel"), mk(2, "b", "x", "reel")]
    why = " | ".join(batch.check_plan(bad, 7))
    assert "3 slots for 7 days" in why and "consecutive days" in why and "more than 2 times" in why
    assert "no carousel slot" in why and "no static slot" in why and "3 reels" in why
    one = batch.plan_week([CELLS[0]], SCORES, START)   # one cell: the same theme would sit on consecutive days
    assert one["slots"] == [] and "cannot fill day 2" in one["conflicts"][0]
    five = batch.plan_week(CELLS, SCORES, START, days=5)   # the rotation gives 1 reel in 5 days: reported
    assert five["slots"] == [] and "1 reels, exactly 2 wanted" in five["conflicts"]


def test_the_order_follows_the_s6_scores():
    first = lambda sc: batch.plan_week(CELLS, sc, START)["slots"][0]["short"]
    assert first(SCORES) == "muscle"
    flipped = SCORES | {"muscle": SCORES["muscle"] | {"opp": 0.0}, "regain": SCORES["regain"] | {"opp": 0.9}}
    assert first(flipped) == "regain"
    tie = {d: r | {"opp": 0.0} for d, r in SCORES.items()}   # equal scores: ties by paper id
    assert batch.plan_week(CELLS, tie, START)["slots"][0]["pmcid"] == "PMC1"


def test_scores_come_from_learn_score(tmp_path):
    labels = [{"doubt": "muscle"}] * 3 + [{"doubt": "safe"}] + [{"doubt": "none"}] * 2
    own = {"posts": 4, "per_doubt": {"safe": {"posts": 2, "checked_lines": 10, "flags_rejections": 1}}}
    (tmp_path / "labels.json").write_text(json.dumps(labels))
    (tmp_path / "own_output.json").write_text(json.dumps(own))
    rows, n = batch.scores(tmp_path)
    assert n == 6 and rows["muscle"]["opp"] == 0.5 and rows["safe"]["opp"] == pytest.approx(1 / 6 * 0.5 * 1.1)


def test_only_verified_or_cleared_claims_are_used(monkeypatch):
    ok = {"gate": {"status": "pass"}, "flags": [], "review_reasons": [], "crosscheck": {"verdict": "supported", "independence": "different_provider"},
          "claim_type": "finding", "labels": {"theme": "t", "doubts": ["is_it_safe"]}, "quote": "q", "s4_cleared": False}
    cleared = ok | {"claim_id": "c2", "flags": [{"code": "X"}], "review_reasons": ["POPULATION_NOT_IN_SECTION"], "s4_cleared": True}
    uncleared = cleared | {"claim_id": "c3", "s4_cleared": False}
    unchecked = ok | {"claim_id": "c4", "crosscheck": {"verdict": "unsupported", "independence": "different_provider"}}
    same_model = ok | {"claim_id": "c5", "crosscheck": {"verdict": "supported", "independence": "same_model"}}
    failed_gate = ok | {"claim_id": "c6", "gate": {"status": "fail"}}
    claims = {c["claim_id"]: c for c in (ok | {"claim_id": "c1"}, cleared, uncleared, unchecked, same_model, failed_gate)}
    monkeypatch.setattr(batch, "run_for", lambda con, s: "run-x")
    monkeypatch.setattr(batch.posts, "claims_from_store", lambda con, r: (claims, {"citation": {"pmcid": "PMCNONE"}}))
    cells, papers = batch.usable_cells(None, ["PMCNONE"])
    assert [c["claim_id"] for c in cells[0]["claims"]] == ["c1", "c2"]
    assert papers[0]["usable"] == 2 and papers[0]["cleared_in_studio"] == 1 and papers[0]["stored"] == 6


class FakeOps:
    """A fake write that blocks the first N calls, a fake hinglish; folders hold what the real ones would."""

    def __init__(self, block_first, fail_codes=("NUMBER_NOT_IN_QUOTE",)):
        self.block, self.calls, self.codes = block_first, [], list(fail_codes)

    def write(self, con, run_id, fmt, settings, router=None, brief=None, out_dir=None):
        self.calls.append(brief["claim_ids"])
        folder = out_dir / run_id / f"post-{len(self.calls)}"
        folder.mkdir(parents=True)
        blocked = len(self.calls) <= self.block
        post = {"slides": [{"lines": [{"text": "Will I lose muscle?"}]}], "caption": {"lines": []}}
        rec = {"post_id": f"post-{len(self.calls)}", "status": "blocked" if blocked else batch.READY, "post_failures": [],
               "folder": str(folder), "brief": brief, "post": post, "sources": [{"claim_id": brief["claim_ids"][0]}],
               "render": {"status": "passed", "pngs": ["slide-1.png"]}}
        if blocked:
            (folder / "trace.json").write_text(json.dumps({"lines": [{"failures": [{"code": c} for c in self.codes]}]}))
        else:
            (folder / "caption.txt").write_text("line one\nline two\n\nSources: x")
        return rec

    def ctx(self, tmp_path):
        return {"con": None, "week_dir": tmp_path, "settings": {}, "n_claims": 4, "write": self.write, "broll": [],
                "hinglish": lambda folder, con, settings, router: {"status": "passed"},
                "reel": None, "make_router": lambda: SimpleNamespace(log=[{"outcome": "ok", "tokens": {"input_tokens": 5, "output_tokens": 2}}])}


def slot_for(c):
    return {"slot": 1, "date": "2026-10-20", "weekday": "Tue", "format": "carousel", "pmcid": c["pmcid"], "run_id": c["run_id"],
            "theme": c["theme"], "doubt": c["doubt"], "short": c["short"], "claims": [], "reason": "r"}


def test_a_blocked_slot_is_retried_once_with_the_next_claims(tmp_path):
    ops, c = FakeOps(block_first=1), CELLS[0]
    r = batch.fill_slot(slot_for(c), c, set(), ops.ctx(tmp_path))
    assert r["status"] == "filled" and r["post_id"] == "post-2" and len(ops.calls) == 2
    assert not set(ops.calls[0]) & set(ops.calls[1])        # the retry uses the next claim set
    assert [a.get("codes") for a in r["attempts"]] == [["NUMBER_NOT_IN_QUOTE"], None] and r["hinglish"] is True
    assert (r["model_calls"], r["input_tokens"]) == (1, 5)


def test_a_slot_blocked_twice_is_left_unfilled_with_its_codes(tmp_path):
    ops, c = FakeOps(block_first=5), CELLS[0]
    r = batch.fill_slot(slot_for(c), c, set(), ops.ctx(tmp_path))
    assert r["status"] == "unfilled" and len(ops.calls) == 2 and r["post_id"] is None   # one retry, no third try
    assert r["codes"] == ["NUMBER_NOT_IN_QUOTE"]
    md = batch.week_md("2026-10-20", TOP, [r], [])
    assert "slot unfilled (NUMBER_NOT_IN_QUOTE)" in md


def test_a_cell_with_no_claims_left_is_unfilled(tmp_path):
    ops, c = FakeOps(block_first=0), cell("PMC9", "t", "is_it_safe", n=2)
    r = batch.fill_slot(slot_for(c), c, {x["claim_id"] for x in c["claims"]}, ops.ctx(tmp_path))
    assert r["status"] == "unfilled" and r["codes"] == ["NO_CLAIMS_LEFT"] and ops.calls == []


def test_week_md_top_line_is_exact():
    s = [{"slot": 1, "weekday": "Tue", "date": "2026-10-20", "format": "carousel", "pmcid": "PMC1", "short": "muscle", "status": "filled",
          "post_id": "post-1", "folder": "posts/r/post-1", "hinglish": True, "hook": "Will I lose muscle?", "reason": "r", "codes": []}]
    md = batch.week_md("2026-10-20", batch.top_line(4, 11), s, [])
    assert md.splitlines()[0] == TOP
    assert "| Tue 2026-10-20 19:00 IST | carousel | PMC1 | muscle | Will I lose muscle? | pending doctor review | post-1" in md


def parse_ics(text):
    """A strict structural RFC 5545 check: CRLF, folding, 75 octets, nesting, required properties, TZID resolves, valid dates."""
    assert text.endswith("\r\n") and "\n" not in text.replace("\r\n", "")
    raw = text[:-2].split("\r\n")
    assert all(len(x.encode()) <= 75 for x in raw)
    lines = []
    for x in raw:
        if x.startswith(" "):
            lines[-1] += x[1:]
        else:
            lines.append(x)
    stack, comps, cur = [], [], None
    for x in lines:
        name, _, value = x.partition(":")
        prop, *params = name.split(";")
        assert re.fullmatch(r"[A-Z][A-Z0-9-]*", prop), x
        if prop == "BEGIN":
            stack.append(value)
            cur = {"type": value, "props": []}
            comps.append(cur)
        elif prop == "END":
            assert stack.pop() == value
        else:
            cur["props"].append((prop, dict(p.split("=", 1) for p in params), value))
    assert stack == [] and comps[0]["type"] == "VCALENDAR"
    return comps


def test_the_ics_is_a_valid_calendar():
    ev = [{"date": "2026-10-20", "uid": "post-1", "summary": "Will I lose muscle, on a GLP-1; really?",
           "description": "line one\nline two — " + "long " * 40 + "\nPost folder: posts/r/post-1"},
          {"date": "2026-10-21", "uid": "post-2", "summary": "Is it safe?", "description": "x"}]
    comps = parse_ics(batch.ics(ev, "19:00", datetime(2026, 10, 19, 8, 0)))
    assert [c["type"] for c in comps] == ["VCALENDAR", "VTIMEZONE", "STANDARD", "VEVENT", "VEVENT"]
    cal = {p: v for p, _, v in comps[0]["props"]}
    assert cal["VERSION"] == "2.0" and cal["PRODID"]
    assert ("TZID", {}, "Asia/Kolkata") in comps[1]["props"]
    for c in comps[3:]:
        props = {p: (par, v) for p, par, v in c["props"]}
        assert {"UID", "DTSTAMP", "DTSTART", "DTEND", "SUMMARY", "DESCRIPTION"} <= set(props)
        assert props["DTSTART"][0] == {"TZID": "Asia/Kolkata"} and props["DTEND"][0] == {"TZID": "Asia/Kolkata"}
        assert datetime.strptime(props["DTSTART"][1], "%Y%m%dT%H%M%S").hour == 19
        datetime.strptime(props["DTSTAMP"][1], "%Y%m%dT%H%M%SZ")
    d = dict((p, v) for p, _, v in comps[3]["props"])
    assert d["SUMMARY"] == "Will I lose muscle\\, on a GLP-1\\; really?" and "\\n" in d["DESCRIPTION"]


def curated_cell():
    c = cell("PMC1", "t_lean", "will_i_lose_muscle")
    cur = {"format": "carousel", "theme": "t_lean", "doubt": "will_i_lose_muscle", "claim_ids": ["c000", "c001", "c006"], "why": "hand-made"}
    return c | {"curated": {"carousel": cur}, "paper_usable": {"c000", "c001", "c006", "c002"}}


def test_the_curated_brief_is_chosen_when_present_and_the_automatic_one_otherwise():
    c = curated_cell()
    b = batch.brief_for(c, set(), "carousel")
    assert b["why"] == "hand-made" and b["brief_source"] == "curated brief code/briefs/PMC1.json"
    assert batch.brief_for(c, set(), "static")["brief_source"] == "automatic brief"       # no curated brief for that format
    assert batch.brief_for(c, {"c001"}, "carousel")["brief_source"].startswith("automatic brief (next claim set")  # already published
    assert batch.brief_for(c | {"paper_usable": {"c000"}}, set(), "carousel")["brief_source"] == "automatic brief"  # not all usable
    assert batch.brief_for(CELLS[0], set(), "carousel")["brief_source"] == "automatic brief"
    p = batch.plan_week([c] + CELLS[1:], SCORES, START)["slots"]
    assert p[0]["brief_source"].startswith("curated") and p[2]["brief_source"].startswith("automatic brief")


def test_the_shipped_curated_briefs_are_well_formed():
    for pm in ("PMC8006839", "PMC12776922", "PMC13461373"):
        for fmt, b in batch.load_curated(pm).items():
            assert b["format"] == fmt and b["claim_ids"] and b["theme"] and b["doubt"]


def test_a_curated_brief_gets_three_tries_with_the_same_claims_and_the_automatic_one_keeps_retry_once(tmp_path):
    c = curated_cell()
    ops = FakeOps(block_first=2)
    r = batch.fill_slot(slot_for(c), c, set(), ops.ctx(tmp_path))
    assert r["status"] == "filled" and r["write_try"] == 3 and len(ops.calls) == 3
    assert ops.calls[0] == ops.calls[1] == ops.calls[2] == ["c000", "c001", "c006"]
    assert r["brief_source"].startswith("curated")
    ops = FakeOps(block_first=9)
    r = batch.fill_slot(slot_for(c), c, set(), ops.ctx(tmp_path / "b"))
    assert r["status"] == "unfilled" and len(ops.calls) == 3
    ops = FakeOps(block_first=9)                       # the curated set already published: automatic, retry once
    r = batch.fill_slot(slot_for(c), c, {"c000"}, ops.ctx(tmp_path / "c"))
    assert len(ops.calls) == 2 and not set(ops.calls[0]) & {"c000"}
