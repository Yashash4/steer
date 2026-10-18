import copy

from steer_pipeline import posts, render

BRAND = posts.load_brand()


def L(kind, text, ids=()):
    return {"kind": kind, "text": text, "claim_ids": list(ids)}


def src(cid, doi="10.1/x", location="page 3"):
    return {"claim_id": cid, "authors": "Narayan KMV, Kondal D, Daya N et al.", "title": "A study", "journal": "A journal",
            "year": 2021, "doi": doi, "location": location, "sids": ["p0003-s0034"]}


HEALTH = L("health", "On average weight came back at 0.4 kg a month, not a personal promise.", ["c1"])
RECORD = {"sources": [src("c1")], "post": {
    "format": "carousel",
    "slides": [{"role": "hook", "lines": [L("framing", "Will the weight come back?")], "emphasis": "come back"},
               {"role": "point", "lines": [HEALTH], "key_number": "0.4 kg a month", "key_label": "weight came back"},
               {"role": "brand", "lines": [L("brand", "Stop Googling. Ask a doctor.")]},
               {"role": "cta", "lines": [L("cta", "Talk to a Steer doctor"), L("signoff", "steerclub.com")]}],
    "caption": {"lines": [], "hashtags": []}, "reel_script": {"scenes": []}}}


def test_a_clean_carousel_renders_and_passes_qa(tmp_path):
    r = render.render(RECORD, BRAND, tmp_path)
    assert r["status"] == "passed", r
    assert [p["png"] for p in r["pages"]] == [f"slide-0{i}.png" for i in range(1, 6)]   # 4 slides + sources
    for p in r["pages"]:
        assert render.png_size(tmp_path / p["png"]) == (1080, 1350)
        assert p["min_body_px"] is None or p["min_body_px"] >= 28
    assert r["pages"][-1]["kind"] == "sources" and r["pages"][-1]["min_sources_px"] >= 24


def test_every_page_has_the_draft_mark_and_emphasis_only_repeats_text():
    pages = render.build_pages(RECORD, BRAND)
    assert all(render.DRAFT in p["html"] for p in pages)
    assert "Will the weight <em>come back</em>?" in pages[0]["html"]
    assert 'class="key"' in pages[1]["html"] and "weight came back" in pages[1]["html"]
    assert "Pending review by a Steer doctor" in pages[-1]["html"]


def test_an_overflowing_slide_fails_render_qa(tmp_path):
    # synthetic fixture: under the word budget, but the words are too long to fit even at the 28 px floor
    long = copy.deepcopy(RECORD)
    long["post"]["slides"][1] = {"role": "point", "lines": [L("health", " ".join(["Pneumonoultramicroscopicsilicovolcanoconiosis"] * 30), ["c1"])]}
    r = render.render(long, BRAND, tmp_path)
    assert r["status"] == "failed"
    bad = r["pages"][1]
    assert bad["words"] == 30 and "TEXT_OVERFLOW" in bad["failures"] and bad["min_body_px"] == 28


def test_four_sources_make_two_sources_slides_and_west_shows_no_page():
    four = copy.deepcopy(RECORD)
    four["sources"] = [src(f"c{i}", doi=f"10.1/{i}") for i in range(1, 5)]
    pages = render.build_pages(four, BRAND)
    assert [p.get("entries") for p in pages if p["kind"] == "sources"] == [3, 1]
    west = copy.deepcopy(RECORD)
    west["sources"] = [src("c1", location="section text, no page layout")]
    html = render.build_pages(west, BRAND)[-1]["html"]
    assert "Section text, no page layout" in html and "Page" not in html


def test_a_claim_missing_from_the_sources_fails(tmp_path):
    lost = copy.deepcopy(RECORD)
    lost["sources"] = []
    r = render.render(lost, BRAND, tmp_path)
    assert r["status"] == "failed" and r["missing_sources"] == ["c1"]


def test_static_carries_a_one_line_source(tmp_path):
    st = copy.deepcopy(RECORD)
    st["post"] = {"format": "static", "static": {"headline": L("framing", "Will the weight come back?"),
                                                 "supporting": [HEALTH], "emphasis": "come back"},
                  "caption": {"lines": [], "hashtags": []}, "reel_script": {"scenes": []}}
    r = render.render(st, BRAND, tmp_path)
    assert r["status"] == "passed" and [p["png"] for p in r["pages"]] == ["static.png"]
    assert "Source: Narayan et al. 2021, page 3" in render.build_pages(st, BRAND)[0]["html"]


def test_a_number_stays_on_the_line_of_its_unit():
    assert render._esc("under 25 kg/m2, 12 983 people, 45 and over") == "under 25\u00a0kg/m², 12\u00a0983 people, 45 and over"


def test_one_sources_entry_per_paper_with_pages_merged():
    two = copy.deepcopy(RECORD)
    two["sources"] = [src("c1", location="page 5"), src("c2", location="page 2")]
    pages = render.build_pages(two, BRAND)
    assert pages[-1]["entries"] == 1 and "Pages 2 and 5." in pages[-1]["html"]
    assert render.merge_locations(["section text, no page layout"] * 2) == "section text, no page layout"


def test_squared_unit_is_a_display_transform_only():
    sq = copy.deepcopy(RECORD)
    sq["post"]["slides"][1]["lines"] = [L("health", "Body mass index under 25 kg/m2 on average.", ["c1"])]
    sq["post"]["slides"][1].pop("key_number"), sq["post"]["slides"][1].pop("key_label")
    html = render.build_pages(sq, BRAND)[1]["html"]
    assert "kg/m²" in html and "kg/m2" not in html
    assert sq["post"]["slides"][1]["lines"][0]["text"].endswith("kg/m2 on average.")   # the checked text is untouched
