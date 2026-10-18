import copy
import io
import json
import threading
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

import pytest

from steer_pipeline import posts as posts_mod, render, store, studio

HERE = Path(__file__).parent
DEMO = studio.load_reviewers(HERE.parent / "reviewers.toml")
TEST = studio.load_reviewers(HERE / "fixtures" / "reviewers-test.toml")
BRAND = posts_mod.load_brand()
OK_WHY = "compared the highlighted lines on the page with the quote; they say the same thing"
SUPPORTED = {"verdict": "supported", "independence": "different_provider"}


def claim(cid, status="passed", reasons=(), cc=SUPPORTED, **kw):
    return {"run_id": "r1", "claim_id": cid, "status": status, "review_reasons": list(reasons), "crosscheck": cc,
            "claim_text": f"text {cid}", "quote": f"quote {cid}", "sids": ["p0001-s0001"], "line_ids": ["p0001-l0001"]} | kw


def L(kind, text, ids=()):
    return {"kind": kind, "text": text, "claim_ids": list(ids)}


def post(pid="post-00000001", status="draft_pending_review", needs=("c1",), render_status="passed"):
    health = L("health", "On average weight came back at 0.4 kg a month, not a personal promise.", ["c1", "c2"])
    return {"post_id": pid, "run_id": "r1", "status": status, "format": "carousel", "message": "m", "reasoning": "r",
            "needs_layout_check": list(needs), "post_failures": [] if status != "blocked" else [{"code": "SLIDE_TOO_LONG"}],
            "render": {"status": render_status, "pngs": []}, "folder": "",
            "sources": [{"claim_id": i, "authors": "West S, Scragg J, Aveyard P et al.", "title": "T", "journal": "BMJ",
                         "year": 2026, "doi": "10.1/w", "location": "page 3", "sids": ["p0003-s0001"]} for i in ("c1", "c2")],
            "post": {"format": "carousel",
                     "slides": [{"role": "hook", "lines": [L("framing", "Will the weight come back?")]},
                                {"role": "point", "lines": [health]},
                                {"role": "cta", "lines": [L("cta", "Talk to a Steer doctor"), L("signoff", "steerclub.com")]}],
                     "caption": {"lines": [health], "hashtags": []}, "reel_script": {"scenes": [{"lines": [health]}]}}}


def world(tmp_path):
    con = store.connect(tmp_path / "s.db")
    claims = {f"r1/{c['claim_id']}": c for c in [claim("c1", reasons=["LAYOUT_CHECK_NEEDED"]), claim("c2"),
                                                 claim("c3", "needs_check", ["CONTEXT_NOT_IN_QUOTE"])]}
    p = post()
    p["folder"] = str(tmp_path / "p1")
    Path(p["folder"]).mkdir()
    return con, claims, {p["post_id"]: p}


def do(con, reviewers, claims, posts, **body):
    return studio.act(con, reviewers, body, claims, posts)


def refused(con, reviewers, claims, posts, **body):
    with pytest.raises(studio.Refused) as e:
        studio.act(con, reviewers, body, claims, posts)
    return e.value


def test_the_demo_has_no_clinician_and_roles_come_from_the_server(tmp_path):
    assert "clinician" not in DEMO.values() and "clinician" in TEST.values()
    con, claims, posts = world(tmp_path)
    e = refused(con, DEMO, claims, posts, reviewer="Somebody", action="layout_checked", target="r1/c1")
    assert e.http == 403
    do(con, DEMO, claims, posts, reviewer="Steer editor (demo)", action="layout_checked", target="r1/c1", reason=OK_WHY)
    do(con, DEMO, claims, posts, reviewer="Steer editor (demo)", action="editorial_approved", target="post-00000001")
    # the browser claiming to be a clinician changes nothing: the server reads the role from reviewers.toml
    e = refused(con, DEMO, claims, posts, reviewer="Steer editor (demo)", role="clinician", action="clinically_approved",
                target="post-00000001")
    assert e.http == 403 and e.stage == "clinical"
    assert store.actions(con)[-1]["role"] == "editor"


def test_each_stage_waits_for_the_one_before_it(tmp_path):
    con, claims, posts = world(tmp_path)
    ed, cl = "Test editor", "Test clinician"
    e = refused(con, TEST, claims, posts, reviewer=ed, action="editorial_approved", target="post-00000001")
    assert (e.http, e.stage) == (409, "layout")                                # c1 still needs its layout check
    e = refused(con, TEST, claims, posts, reviewer=cl, action="clinically_approved", target="post-00000001")
    assert (e.http, e.stage) == (409, "layout")
    row = do(con, TEST, claims, posts, reviewer=ed, action="layout_checked", target="r1/c1", reason=OK_WHY)
    assert row["before_status"].startswith("needs check") and row["after_status"].startswith("verified; layout checked by Test editor")
    e = refused(con, TEST, claims, posts, reviewer=cl, action="clinically_approved", target="post-00000001")
    assert (e.http, e.stage) == (409, "editorial")
    do(con, TEST, claims, posts, reviewer=ed, action="editorial_approved", target="post-00000001")
    st = studio.post_stages(posts["post-00000001"], claims, store.actions(con))
    assert st["words"] == "pending doctor review" and st["first_open"] == "clinical"
    do(con, TEST, claims, posts, reviewer=cl, action="clinically_approved", target="post-00000001")
    st = studio.post_stages(posts["post-00000001"], claims, store.actions(con))
    assert st["first_open"] is None and st["words"].startswith("clinically approved by Test clinician (clinician)")
    # a blocked post never reaches editorial review
    blocked = post("post-00000002", status="blocked", needs=())
    e = refused(con, TEST, claims, {blocked["post_id"]: blocked}, reviewer=ed, action="editorial_approved",
                target="post-00000002")
    assert (e.http, e.stage) == (409, "code")


def test_rejections_need_a_reason_and_layout_unresolved_blocks_the_post(tmp_path):
    con, claims, posts = world(tmp_path)
    ed = "Test editor"
    assert refused(con, TEST, claims, posts, reviewer=ed, action="layout_unresolved", target="r1/c1").http == 400
    do(con, TEST, claims, posts, reviewer=ed, action="layout_unresolved", target="r1/c1", reason="lines from two columns")
    st = studio.post_stages(posts["post-00000001"], claims, store.actions(con))
    assert st["stages"]["code"]["words"] == "blocked (layout unresolved: c1)" and st["first_open"] == "code"
    assert studio.claim_status(claims["r1/c1"], store.actions(con))["words"] == "layout unresolved (lines from two columns)"
    # editorial rejection: reason AND category
    do(con, TEST, claims, posts, reviewer=ed, action="layout_checked", target="r1/c1", reason=OK_WHY)
    assert refused(con, TEST, claims, posts, reviewer=ed, action="editorial_rejected", target="post-00000001",
                   reason="tone").http == 400
    do(con, TEST, claims, posts, reviewer=ed, action="editorial_rejected", target="post-00000001", reason="too alarming",
       reason_category="tone/brand")
    assert studio.post_stages(posts["post-00000001"], claims, store.actions(con))["words"] == \
        "rejected by Test editor (editor): too alarming"


def test_claim_status_words_say_exactly_what_was_checked():
    w = lambda c: studio.claim_status(c, [])["words"]
    assert w(claim("x", "rejected")) == "rejected by the gate"
    assert w(claim("x")) == "verified"
    assert w(claim("x", cc={"verdict": "supported", "independence": "same_model"})) == "passed code checks"
    assert w(claim("x", "needs_check", ["CONTEXT_NOT_IN_QUOTE"])) == "needs check (CONTEXT_NOT_IN_QUOTE)"
    assert w(claim("x", reasons=["LAYOUT_CHECK_NEEDED"])) == "needs check (LAYOUT_CHECK_NEEDED)"


def test_draft_pack_for_any_post_publishable_only_when_every_stage_is_done(tmp_path):
    con, claims, posts = world(tmp_path)
    p = posts["post-00000001"]
    r = render.render(p, BRAND, Path(p["folder"]))                            # the draft PNGs, as `steer write` makes them
    p["render"]["pngs"] = [x["png"] for x in r["pages"]]
    pack = zipfile.ZipFile(io.BytesIO(studio.export_draft_pack(con, [p["post_id"]], claims, posts)))
    names = pack.namelist()
    assert "manifest.json" in names and any(n.endswith(".png") for n in names)
    m = json.loads(pack.read("manifest.json"))
    assert set(m["posts"][0]["stages"]) == {"code", "layout", "editorial", "clinical"}
    assert all(render.DRAFT in pg["html"] for pg in render.build_pages(p, BRAND))   # every draft image has the mark
    for stage, step in [("layout", ("Test editor", "layout_checked", "r1/c1")),
                        ("editorial", ("Test editor", "editorial_approved", p["post_id"])),
                        ("clinical", ("Test clinician", "clinically_approved", p["post_id"]))]:
        with pytest.raises(studio.Refused) as e:
            studio.export_publishable(con, p["post_id"], claims, posts, BRAND)
        assert (e.value.http, e.value.stage) == (409, stage)                  # the endpoint names the missing stage
        do(con, TEST, claims, posts, reviewer=step[0], action=step[1], target=step[2],
           reason=OK_WHY if step[1] == "layout_checked" else None)
    pub = zipfile.ZipFile(io.BytesIO(studio.export_publishable(con, p["post_id"], claims, posts, BRAND)))
    assert json.loads(pub.read("manifest.json"))["kind"] == "publishable"
    clean = render.build_pages(p, BRAND, draft=False)
    assert all(render.DRAFT not in pg["html"] for pg in clean)
    assert render.PUBLISHED_FOOTNOTE in clean[-1]["html"]


def test_review_time_is_timestamp_based_with_long_gaps_left_out():
    acts = [{"target_kind": "claim", "target": "r1/c1", "at": t} for t in
            ("2026-10-18T10:00:00", "2026-10-18T10:03:00", "2026-10-18T10:30:00", "2026-10-18T10:31:00")]
    assert studio.review_minutes(acts, lambda a: "r1") == {"r1": 4.0}            # 3 + 1; the 27-minute gap is excluded


def test_page_images_in_a_public_copy_only_for_cc_by(tmp_path):
    assert studio.cc_by({"license": "CC BY"}) and studio.cc_by({"license": "cc-by-4.0"})
    assert not studio.cc_by({"license": "CC BY-NC"}) and not studio.cc_by({"license": None})
    con, claims, posts = world(tmp_path)
    for lic, shown in (("CC BY", True), ("CC BY-NC", False)):
        docs = {"r1": {"citation": {"pmcid": "PMC1", "license": lic}, "warnings": [], "source": {"access": "full_text"},
                       "pages": [{"page": 1, "width": 612, "height": 792, "layout": "single_column",
                                  "lines": [{"line_id": "p0001-l0001", "bbox": [10, 20, 300, 34]}]}]}}
        snap = studio.data(con, docs, claims, posts, DEMO, public=True)
        assert snap["papers"]["PMC1"]["page_images"] is shown
        assert studio.data(con, docs, claims, posts, DEMO, public=False)["papers"]["PMC1"]["page_images"] is True
        c1 = next(c for c in snap["claims"] if c["target"] == "r1/c1")
        assert c1["page_note"] == (None if shown else "page image not shown: CC BY-NC")
        assert c1["boxes"] == {"1": [{"line_id": "p0001-l0001", "bbox": [10, 20, 300, 34]}]}   # S1 line boxes only
    assert "/api/" not in json.dumps(snap) and snap["reviewers"]["has_clinician"] is False


def test_the_server_ignores_a_browser_role_and_answers_with_the_gate(tmp_path):
    from http.server import HTTPServer
    _, claims, posts = world(tmp_path)
    con = store.connect(tmp_path / "s.db", check_same_thread=False)   # served from a second thread, one at a time
    docs = {"r1": {"citation": {"pmcid": "PMC1", "license": "CC BY"}, "warnings": [], "pages": [],
                   "source": {"access": "full_text"}}}
    srv = HTTPServer(("127.0.0.1", 0), studio.make_handler(con, lambda: (docs, claims, posts), DEMO, BRAND, tmp_path, None))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"

    def post_json(path, body):
        req = urllib.request.Request(base + path, json.dumps(body).encode(), {"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())
    try:
        with urllib.request.urlopen(base + "/api/data") as r:
            assert json.loads(r.read())["reviewers"]["note"] == "self-identified (prototype, no login)"
            assert r.headers["Cache-Control"] == "no-store"          # a reload always shows the latest statuses
        assert post_json("/api/action", {"reviewer": "Steer editor (demo)", "role": "clinician",
                                         "action": "clinically_approved", "target": "post-00000001"})[0] == 409
        code, body = post_json("/api/action", {"reviewer": "Steer editor (demo)", "action": "layout_checked", "target": "r1/c1", "reason": OK_WHY})
        assert code == 200 and body["role"] == "editor"
        code, body = post_json("/api/export/publishable", {"post_id": "post-00000001"})
        assert code == 409 and body["stage"] == "editorial"
    finally:
        srv.shutdown()


@pytest.mark.network
def test_layout_check_highlight_is_the_s1_line_boxes_of_narayan_p1_s0027(tmp_path):
    from steer_pipeline.fetch import fetch
    doc = fetch("PMC8006839", "evidence")
    sent = next(s for s in doc["sentences"] if s["sid"] == "p0001-s0027")
    lines = {ln["line_id"]: ln for pg in doc["pages"] for ln in pg["lines"]}
    con = store.connect(tmp_path / "s.db")
    c = claim("c9", reasons=["LAYOUT_CHECK_NEEDED"], line_ids=sent["line_ids"], sids=["p0001-s0027"])
    snap = studio.data(con, {"r1": doc}, {"r1/c9": c}, {}, DEMO)
    boxes = snap["claims"][0]["boxes"]["1"]
    assert [b["line_id"] for b in boxes] == sent["line_ids"]
    assert all(b["bbox"] == lines[b["line_id"]]["bbox"] for b in boxes)
    assert any(w["code"] == "UNCERTAIN_COLUMN_ORDER" and w.get("page") == 1 for w in doc["warnings"])


def test_a_post_that_fails_todays_rules_is_not_reviewable(tmp_path):
    con, claims, posts = world(tmp_path)
    p = posts["post-00000001"] | {"current_failures": ["STATIC_NO_SIGNOFF"]}
    st = studio.post_stages(p, claims, [])
    assert st["first_open"] == "code" and st["words"] == "blocked under the current rules (STATIC_NO_SIGNOFF)"
    b = post("post-00000003", status="blocked") | {"write_failures": ["QUALIFIER_DROPPED"], "current_failures": ["NUMBER_NOT_IN_CLAIM"]}
    assert studio.post_stages(b, claims, [])["words"] == "blocked (NUMBER_NOT_IN_CLAIM, QUALIFIER_DROPPED)"
    ex = dict(claims) | {"r1/c2": claims["r1/c2"] | {"excluded": "harmful under family framing"}}
    assert studio.claim_status(ex["r1/c2"], [])["words"] == "excluded from posts (harmful under family framing)"


def test_a_layout_check_does_not_clear_other_reasons(tmp_path):
    con = store.connect(tmp_path / "s.db")
    c = claim("c5", "needs_check", ["CONTEXT_NOT_IN_QUOTE", "LAYOUT_CHECK_NEEDED"])
    do(con, TEST, {"r1/c5": c}, {}, reviewer="Test editor", action="layout_checked", target="r1/c5", reason=OK_WHY)
    st = studio.claim_status(c, store.actions(con))
    assert st["words"].startswith("needs check (CONTEXT_NOT_IN_QUOTE); layout checked by Test editor") and not st["usable"]


def test_check_cleared_needs_a_reason_and_every_flag_cleared(tmp_path):
    from steer_pipeline.checks import writable
    con = store.connect(tmp_path / "s.db")
    lean = claim("c7", "needs_check", ["CONTEXT_NOT_IN_QUOTE", "POPULATION_NOT_IN_SECTION"],
                 flags=[{"code": "CONTEXT_NOT_IN_QUOTE"}, {"code": "POPULATION_NOT_IN_SECTION"}], gate={"status": "pass"})
    cs = {"r1/c7": lean}
    e = refused(con, TEST, cs, {}, reviewer="Test editor", action="check_cleared", target="r1/c7", clears="CONTEXT_NOT_IN_QUOTE")
    assert e.http == 400                                                        # no reason, no clearance
    e = refused(con, TEST, cs, {}, reviewer="Test editor", action="check_cleared", target="r1/c7", clears="CAUSAL_WORDING",
                reason="x")
    assert e.http == 400                                                        # only the claim's own open reasons
    do(con, TEST, cs, {}, reviewer="Test editor", action="check_cleared", target="r1/c7", clears="CONTEXT_NOT_IN_QUOTE",
       reason="the population is stated in the sentence before, same paragraph")
    acts = store.actions(con)
    st = studio.claim_status(lean, acts)
    assert not st["usable"] and st["open"] == ["POPULATION_NOT_IN_SECTION"]   # partly cleared: still not writable
    assert not writable(lean | {"s4_cleared": studio.fully_cleared(lean, acts)})
    assert refused(con, TEST, cs, {}, reviewer="Test editor", action="check_cleared", target="r1/c7",
                   clears="CONTEXT_NOT_IN_QUOTE", reason="again").http == 409
    do(con, TEST, cs, {}, reviewer="Test editor", action="check_cleared", target="r1/c7", clears="POPULATION_NOT_IN_SECTION",
       reason="the methods section names the population")
    acts = store.actions(con)
    st = studio.claim_status(lean, acts)
    assert st["usable"] and st["words"].startswith("verified; CONTEXT_NOT_IN_QUOTE cleared by Test editor (editor)")
    assert writable(lean | {"s4_cleared": studio.fully_cleared(lean, acts)})   # S3 may now write with it
    no_cc = lean | {"crosscheck": {"verdict": "supported", "independence": "same_model"}}
    assert not writable(no_cc | {"s4_cleared": studio.fully_cleared(no_cc, acts)})   # still needs the independent cross-check


def test_a_thin_reason_is_refused_and_nothing_is_appended(tmp_path):
    con = store.connect(tmp_path / "s.db")
    c = claim("c7", "needs_check", ["CONTEXT_NOT_IN_QUOTE"], flags=[{"code": "CONTEXT_NOT_IN_QUOTE"}])
    cs = {"r1/c7": c}
    kw = dict(reviewer="Test editor", action="check_cleared", target="r1/c7", clears="CONTEXT_NOT_IN_QUOTE")
    for thin in ("sd", "x" * 60, "one two three four five"):    # too short / one 60-char word / five words
        e = refused(con, TEST, cs, {}, reason=thin, **kw)
        assert e.http == 400 and "40 characters and 6 words" in str(e)
    assert store.actions(con) == []
    assert refused(con, TEST, {"r1/c1": claim("c1", reasons=["LAYOUT_CHECK_NEEDED"])}, {}, reviewer="Test editor",
                   action="layout_checked", target="r1/c1", reason="sd").http == 400
    do(con, TEST, cs, {}, reason=OK_WHY, **kw)
    assert len(store.actions(con)) == 1


def test_excluded_or_rejected_claims_cannot_be_cleared(tmp_path):
    con = store.connect(tmp_path / "s.db")
    fam = claim("c8", "needs_check", ["CONTEXT_NOT_IN_QUOTE"], excluded="harmful under family framing")
    rej = claim("c9", "rejected", ["NUMBER_NOT_IN_QUOTE"])
    cs = {"r1/c8": fam, "r1/c9": rej}
    for key, code in (("r1/c8", "CONTEXT_NOT_IN_QUOTE"), ("r1/c9", "NUMBER_NOT_IN_QUOTE")):
        e = refused(con, TEST, cs, {}, reviewer="Test editor", action="check_cleared", target=key, clears=code, reason="r")
        assert (e.http, e.stage) == (409, "check")
    assert not studio.claim_status(fam, store.actions(con))["usable"]


def test_a_v3_store_upgrades_to_v4_keeping_every_action(tmp_path):
    import sqlite3
    path = tmp_path / "v3.db"
    old = store.REVIEW_ACTION.replace("'layout_unresolved', 'check_cleared', ", "'layout_unresolved', ").replace(
        "    clears TEXT,                     -- check_cleared: the review reason code this clearance clears\n", "")
    con = sqlite3.connect(path)
    con.executescript(store.SCHEMA + store.SCHEMA_V3.replace(store.REVIEW_ACTION, old))
    con.execute("INSERT INTO review_action (at, reviewer, role, target_kind, target, stage, action) "
                "VALUES ('t', 'Ed', 'editor', 'claim', 'r/c', 'layout', 'layout_checked')")
    con.commit()
    con.close()
    con = store.connect(path)
    assert [a["action"] for a in store.actions(con)] == ["layout_checked"]
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        con.execute("DELETE FROM review_action")


def test_later_stages_read_not_reached_while_an_earlier_one_is_open(tmp_path):
    con, claims, posts = world(tmp_path)
    st = studio.post_stages(posts["post-00000001"], claims, [])                # c1 still needs its layout check
    assert st["first_open"] == "layout" and st["stages"]["editorial"]["words"] == "not reached (layout stage first)"
    assert st["stages"]["clinical"]["words"] == "not reached (layout stage first)"


@pytest.mark.parametrize("width,height", [(390, 844), (1440, 900)])
def test_a_400_character_clearance_wraps_inside_its_card(tmp_path, width, height):
    import shutil

    from playwright.sync_api import sync_playwright

    con = store.connect(tmp_path / "s.db")
    c = claim("c1", "needs_check", ["CONTEXT_NOT_IN_QUOTE"], flags=[{"code": "CONTEXT_NOT_IN_QUOTE"}], gate={"status": "pass"})
    cs = {"r1/c1": c}
    long = ("checked the sentence before the quote and the methods section on page 3 " * 6)[:400].strip()
    assert len(long) >= 399
    do(con, TEST, cs, {}, reviewer="Test editor", action="check_cleared", target="r1/c1", clears="CONTEXT_NOT_IN_QUOTE", reason=long)
    doc = {"citation": {"pmcid": "PMC1", "authors": ["West S"], "year": 2026, "title": "T", "journal": "J", "doi": "10.1/x",
                        "license": "CC BY"},
           "warnings": [{"code": "NO_PAGE_LAYOUT"}], "source": {}, "sentences": [{"sid": "p0001-s0001", "text": "a sentence"}],
           "pages": []}
    snap = studio.data(con, {"r1": doc}, cs, {}, TEST, public=True)
    out = tmp_path / "site"
    shutil.copytree(studio.Path(studio.__file__).parent / "studio_ui" / "static", out)
    (out / "data.js").write_text("window.STEER_DATA = " + json.dumps(snap, default=str) + ";\n")
    claim_row = snap["claims"][0]
    assert claim_row["status_word"] == "verified"
    with sync_playwright() as pw:
        b = pw.chromium.launch()
        pg = b.new_page(viewport={"width": width, "height": height})
        pg.goto((out / "claim.html").as_uri() + "#" + "PMC1%2Fc1")
        pg.wait_for_selector(".review-status .clearances p")
        assert pg.inner_text(".review-status .chip") == "verified"
        assert " IST" in pg.inner_text(".review-status .clearances")
        status_row = pg.evaluate("""() => [...document.querySelectorAll('dt')].find(d => d.textContent === 'Status').nextElementSibling.innerText""")
        assert " IST" in status_row and "+00:00" not in status_row and "T11:" not in status_row, status_row
        r = pg.evaluate("""() => { const card = document.querySelector('.review-status'), blk = document.querySelector('.review-status .clearances');
            const a = card.getBoundingClientRect(), x = blk.getBoundingClientRect();
            return {sw: blk.scrollWidth, cw: blk.clientWidth, inside: x.left >= a.left && x.right <= a.right && x.top >= a.top && x.bottom <= a.bottom}; }""")
        b.close()
    assert r["sw"] <= r["cw"] and r["inside"], r
