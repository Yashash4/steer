import copy
import json

from steer_pipeline import lineart, posts, render
from test_render import BRAND, RECORD, L

FOOD = {e["id"] for e in lineart.index() if e["food"]}


def slide(*texts, health=False):
    return {"texts": list(texts), "has_health": health, "eligible": True}


def test_choice_is_deterministic_by_tags_and_never_a_model():
    s = [slide("Ask a doctor about your checkup"), slide("Nothing relevant here")]
    assert lineart.choose(s, "x", "y") == lineart.choose(s, "x", "y") == {0: "doctor_tablet"}   # ties go by id


def test_food_never_on_a_health_line_or_a_number():
    tea = "Chai, tea and roti at lunch"
    assert lineart.choose([slide(tea)]) != {}                                     # a food slide does get food ...
    assert set(lineart.choose([slide(tea)]).values()) <= FOOD
    assert not (set(lineart.choose([slide(tea, health=True)]).values()) & FOOD)   # ... a health line: never
    assert not (set(lineart.choose([slide(tea + " 2 times a day")]).values()) & FOOD)   # ... a number: never


def test_place_clears_every_text_box_and_goes_to_an_edge():
    text = [[96, 600, 900, 760], [96, 1240, 200, 1290], [940, 1240, 990, 1290]]
    box = lineart.place(1.4, text)
    assert box and not lineart.overlaps(box, text, lineart.PAD)
    assert box[3] > 1350 * 0.6 or box[0] < 0 or box[2] > 1080 - 1                  # bottom or a side
    assert lineart.place(1.0, [[0, 0, 1080, 1350]]) is None                         # no room: no drawing


def _record(theme="doctor"):
    r = copy.deepcopy(RECORD)
    r["brief"] = {"theme": theme, "doubt": ""}
    return r


def test_render_places_art_not_on_sources_not_over_text_in_the_slide_ink(tmp_path):
    r = render.render(_record(), BRAND, tmp_path)
    assert r["status"] == "passed", r
    drawn = {p["png"]: p["lineart"] for p in r["pages"] if p["lineart"]}
    assert drawn and r["pages"][-1]["kind"] == "sources" and r["pages"][-1]["lineart"] is None
    for p in r["pages"]:
        d = p["lineart"]
        if d:
            assert d["id"] not in FOOD or p["kind"] == "slide"
            assert d["ink"] == (BRAND["palette"]["cream"] if p["png"] == "slide-03.png" else BRAND["palette"]["ink"])  # brand slide is dark
    assert "slide-02.png" not in drawn or drawn["slide-02.png"]["id"] not in FOOD   # health + number slide: no food


def test_art_over_text_fails_render_qa(tmp_path, monkeypatch):
    monkeypatch.setattr(lineart, "place", lambda aspect, boxes, w=1080, h=1350: [0, 0, 1080, 1350])
    r = render.render(_record(), BRAND, tmp_path)
    assert r["status"] == "failed"
    assert any("LINEART_OVER_TEXT" in p["failures"] for p in r["pages"])


def test_food_on_a_health_slide_fails_render_qa(tmp_path, monkeypatch):
    monkeypatch.setattr(lineart, "choose", lambda slides, theme="", doubt="": {1: "chai"})   # slide 2 has a health line
    r = render.render(_record(), BRAND, tmp_path)
    assert "LINEART_FOOD_ON_HEALTH" in r["pages"][1]["failures"] and r["status"] == "failed"


def test_the_drawing_is_recorded_in_post_json_and_adds_no_text(tmp_path):
    (tmp_path / "post.json").write_text(json.dumps(_record()))
    rec = posts.render_post(tmp_path, BRAND)
    assert rec["lineart"] and all(set(v) == {"id", "box", "ink"} for v in rec["lineart"].values())
    assert json.loads((tmp_path / "post.json").read_text())["render"]["lineart"] == rec["lineart"]
    pages = render.build_pages(_record(), BRAND)
    assert all("lineart" not in pg["html"] for pg in pages)      # the HTML (the checked text) carries no drawing
