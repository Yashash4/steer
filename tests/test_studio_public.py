"""The public read-only snapshot (D-031): static files only, true statuses, licence rule, no secrets or local paths."""
import json
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from test_studio import DEMO, OK_WHY, TEST, claim, do, post, refused, store, studio, world  # noqa: E402

REASON = "the population is named in the sentence before the quote, same paragraph"
TEXT_SUFFIXES = {".html", ".js", ".css", ".json", ".txt", ".md", ".svg"}


def doc(pmcid, lic, layout=True):
    return {"citation": {"pmcid": pmcid, "license": lic, "authors": ["Test A"], "year": 2026, "title": "T", "journal": "J",
                         "doi": "10.1/x"}, "warnings": [] if layout else [{"code": "NO_PAGE_LAYOUT"}],
            "source": {"access": "full_text"}, "sentences": [{"sid": "p0001-s0001", "text": "a sentence"}],
            "pages": [{"page": 1, "width": 612, "height": 792, "layout": "single_column",
                       "lines": [{"line_id": "p0001-l0001", "bbox": [10, 20, 300, 34]}]}] if layout else []}


@pytest.fixture
def bundle(tmp_path, monkeypatch):
    con, claims, posts = world(tmp_path)
    p = posts["post-00000001"]
    (Path(p["folder"]) / "s1.png").write_bytes(b"\x89PNG")
    (Path(p["folder"]) / "reel.mp4").write_bytes(b"mp4")
    p["render"]["pngs"] = ["s1.png"]
    claims["r2/c1"] = claim("c1", run_id="r2")
    claims["r3/c1"] = claim("c1", run_id="r3")
    c3 = claims["r1/c3"] | {"status": "needs_check"}
    # a clearance, its revocation and a new clearance on r1/c3 (the history must show all three, in order)
    first = do(con, TEST, claims, posts, reviewer="Test editor", action="check_cleared", target="r1/c3",
               clears="CONTEXT_NOT_IN_QUOTE", reason=REASON)
    do(con, TEST, claims, posts, reviewer="Test editor", action="clearance_revoked", target="r1/c3", revokes=first["id"],
       reason="second look: the sentence before the quote is about another group")
    do(con, TEST, claims, posts, reviewer="Test editor", action="check_cleared", target="r1/c3",
       clears="CONTEXT_NOT_IN_QUOTE", reason=OK_WHY)
    docs = {"r1": doc("PMC1", "CC BY"), "r2": doc("PMC2", "CC BY-NC"), "r3": doc("PMC3", "CC BY", layout=False)}
    con.close()
    monkeypatch.setattr(studio, "load", lambda con, dirs: (docs, claims, posts))
    def fake_page(d, n, cache):
        cache.mkdir(parents=True, exist_ok=True)
        f = cache / f"{d['citation']['pmcid']}-p{n}.jpg"
        f.write_bytes(b"jpg")
        return f
    monkeypatch.setattr(studio, "page_image", fake_page)
    out = tmp_path / "bundle"
    res = studio.export_public(tmp_path / "s.db", [], Path(__file__).parent.parent / "reviewers.toml", out)
    return out, res, tmp_path


def test_the_bundle_is_static_with_no_secrets_paths_or_write_routes(bundle):
    out, res, tmp = bundle
    files = [f for f in out.rglob("*") if f.is_file()]
    assert {"index.html", "ledger.html", "posts.html", "history.html", "data.js", "public.js", "studio.css"} <= {f.name for f in files}
    assert not [f for f in files if f.suffix in (".db", ".sqlite", ".sqlite3", ".toml", ".env")]
    bad = [r"/home/", r"steer\.db", r"/tmp/", str(tmp), r"/api\b", r"\bfetch\s*\(", r"XMLHttpRequest", r"\bsendBeacon", r"<form", r"<input",
           r"<button", r"<textarea", r"localStorage", r"sk-[A-Za-z0-9]{16,}", r"hf_[A-Za-z0-9]{16,}", r"xi-api-key", r"AKIA[0-9A-Z]{12,}",
           r"(?i)api[_-]?key\s*[:=]", r"(?i)bearer\s+[a-z0-9]", r"(?i)doctor approved", r"(?i)approved by a doctor"]
    for f in files:
        if f.suffix in TEXT_SUFFIXES:
            txt = f.read_text()
            for pat in bad:
                assert not re.search(pat, txt), f"{pat!r} found in {f.name}"


def test_status_words_are_true_and_never_claim_a_doctor_approved(bundle):
    out, res, _ = bundle
    pub = json.loads((out / "data.js").read_text().removeprefix("window.STEER_PUBLIC = ").rstrip(";\n"))
    p = pub["posts"][0]
    assert p["stages"]["clinical"]["words"].startswith("not reached") and "approved" not in json.dumps(p["stages"]["clinical"])
    assert pub["clinician_review"] == "no Steer clinician has reviewed anything here"
    assert p["reel"] == "files/post-00000001/reel.mp4" and (out / p["reel"]).read_bytes() == b"mp4"
    assert set(pub) == {"generated_at", "papers", "claims", "posts", "history", "clinician_review"}   # whitelist, no folders
    assert "folder" not in p and "reviewers" not in pub


def test_page_images_only_for_cc_by_papers_with_a_layout(bundle):
    out, res, _ = bundle
    assert res["page_images"] == ["PMC1-p1.jpg"]                            # BY-NC (PMC2) and no-layout CC BY (PMC3): none
    assert [f.name for f in (out / "pages").iterdir()] == ["PMC1-p1.jpg"]
    pub = json.loads((out / "data.js").read_text().removeprefix("window.STEER_PUBLIC = ").rstrip(";\n"))
    assert pub["papers"]["PMC2"]["page_images"] is False and pub["papers"]["PMC1"]["page_images"] is True
    note = next(c for c in pub["claims"] if c["paper"] == "PMC2")["page_note"]
    assert note == "page image not shown: CC BY-NC"


def test_the_review_history_is_shown_as_recorded_append_only(bundle):
    out, _, tmp = bundle
    pub = json.loads((out / "data.js").read_text().removeprefix("window.STEER_PUBLIC = ").rstrip(";\n"))
    assert [h["action"] for h in pub["history"]] == ["check_cleared", "clearance_revoked", "check_cleared"]
    assert pub["history"][1]["revokes"] == pub["history"][0]["id"] and pub["history"][0]["reason"] == REASON   # verbatim
    c3 = next(c for c in pub["claims"] if c["claim_id"] == "c3" and c["paper"] == "PMC1")
    assert [bool(h.get("revoked")) for h in c3["clearance_history"]] == [True, False]
    assert c3["status_word"] == "verified"                                  # the latest clearance counts


@pytest.mark.parametrize("width,height", [(390, 844), (1440, 900)])
def test_the_public_pages_render_without_overflow_or_controls(bundle, width, height):
    from playwright.sync_api import sync_playwright
    out, _, tmp = bundle
    shots = Path(__import__("os").environ.get("STEER_SHOTS", tmp / "shots"))
    shots.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as pw:
        b = pw.chromium.launch()
        pg = b.new_page(viewport={"width": width, "height": height})
        for name in ("index", "ledger", "posts", "history"):
            pg.goto((out / f"{name}.html").as_uri())
            pg.wait_for_selector("h1")
            if name == "ledger":
                pg.evaluate("document.querySelectorAll('details').forEach(d => d.open = true)")
            wide = pg.evaluate("""() => [...document.querySelectorAll('body *')].filter(e => e.getBoundingClientRect().right > window.innerWidth + 1)
                .slice(0, 5).map(e => e.tagName + '.' + e.className + ':' + Math.round(e.getBoundingClientRect().right))""")
            assert not wide and pg.evaluate("document.documentElement.scrollWidth <= window.innerWidth"), (name, width, wide)
            assert pg.locator("button, input, form, textarea, select").count() == 0
            pg.screenshot(path=str(shots / f"public-{name}-{width}.png"), full_page=True)
        pg.goto((out / "history.html").as_uri())
        txt = pg.inner_text("main")
        assert txt.index("check cleared") < txt.index("clearance revoked")
        b.close()


def test_sources_one_md_and_csv_per_paper_with_true_status_and_posts(tmp_path, monkeypatch):
    import csv
    con, claims, posts = world(tmp_path)
    claims["r1/c4"] = claim("c4", "rejected", gate={"reasons": [{"code": "QUOTE_NOT_FOUND"}]})
    do(con, TEST, claims, posts, reviewer="Test editor", action="check_cleared", target="r1/c3",
       clears="CONTEXT_NOT_IN_QUOTE", reason=OK_WHY)
    claims["r1/c3"] = claims["r1/c3"] | {"status": "needs_check"}
    con.close()
    monkeypatch.setattr(studio, "load", lambda con, dirs: ({"r1": doc("PMC1", "CC BY")}, claims, posts))
    out = tmp_path / "src"
    assert studio.export_sources(tmp_path / "s.db", [], Path(__file__).parent.parent / "reviewers.toml", out) == ["PMC1"]
    rows = {r["claim id"]: r for r in csv.DictReader(open(out / "PMC1.csv", encoding="utf-8"))}
    assert rows["c2"]["status"] == "verified" and rows["c2"]["posts using it"] == "post-00000001"
    assert rows["c3"]["status"].startswith("verified; CONTEXT_NOT_IN_QUOTE cleared by Test editor") and rows["c3"]["posts using it"] == ""
    assert rows["c4"]["status"] == "rejected by the gate"
    assert rows["c1"]["location"] == "page 1" and rows["c2"]["exact quote"] == "quote c2"
    assert "| c2 |" in (out / "PMC1.md").read_text(encoding="utf-8")


def test_the_banner_is_the_fixed_demo_copy_wording_and_hackathon_is_nowhere(bundle):
    out, _, _ = bundle
    js = (out / "public.js").read_text()
    assert ("<strong>Read-only demo copy: actions disabled.</strong> An independent prototype, not an official Steer product. Built '"
            " + esc(ist(D.generated_at)) + \".</div>\"") in js
    for f in out.rglob("*"):
        if f.is_file() and f.suffix in TEXT_SUFFIXES:
            assert "hackathon" not in f.read_text().lower(), f.name


def test_the_sources_tables_carry_the_studios_own_status_string(bundle):
    import csv
    out, _, tmp = bundle
    pub = json.loads((out / "data.js").read_text().removeprefix("window.STEER_PUBLIC = ").rstrip(";\n"))
    studio.export_sources(tmp / "s.db", [], Path(__file__).parent.parent / "reviewers.toml", tmp / "src")
    seen = {}
    for f in (tmp / "src").glob("*.csv"):
        for r in csv.DictReader(f.open(encoding="utf-8")):
            seen[f"{f.stem}/{r['claim id']}"] = r["status"]
    assert seen == {c["key"]: c["status"] for c in pub["claims"]}
    assert any(s.startswith("rejected by the gate") or s.startswith("verified") for s in seen.values())
    assert not [s for s in seen.values() if s.endswith("()")]


def test_the_ledger_has_a_per_claim_anchor_and_scrolls_to_it(bundle):
    out, _, _ = bundle
    js = (out / "public.js").read_text()
    assert '"claim-" + String(k).replace("/", "-")' in js and 'id="\' + esc(aid(c.key))' in js
    assert "location.hash" in js and "scrollIntoView" in js and "el.open = true" in js
