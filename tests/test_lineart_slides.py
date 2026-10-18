import copy
import json

from steer_pipeline import lineart, posts, render
from test_render import BRAND, RECORD, L

FOOD = {e["id"] for e in lineart.index() if e["food"]}


def slide(*texts, health=False):
    return {"texts": list(texts), "has_health": health, "eligible": True}


def test_choice_is_deterministic_by_tags_and_never_a_model():
    s = [slide("Ask a doctor about your checkup"), slide("Nothing relevant here")]
    assert lineart.choose(s, "x", "y") == lineart.choose(s, "x", "y") == {0: "stethoscope"}   # doctor + checkup + ask beat doctor_tablet; ties go by id


def test_food_never_on_a_health_line_or_a_number():
    tea = "Chai, tea and roti at lunch"
    assert lineart.choose([slide(tea)]) != {}                                     # a food slide does get food ...
    assert set(lineart.choose([slide(tea)]).values()) <= FOOD
    assert not (set(lineart.choose([slide(tea, health=True)]).values()) & FOOD)   # ... a health line: never
    assert not (set(lineart.choose([slide(tea + " 2 times a day")]).values()) & FOOD)   # ... a number: never


def test_food_never_beside_a_line_naming_a_health_outcome():
    for line in ("Normal BMI, so diabetes is far away?", "Chai and lunch: what about your cholesterol?",
                 "Tea in the morning and your blood sugar", "Is it a heart risk? Roti for dinner", "Side effects of a family meal"):
        assert lineart.names_outcome(line)
        assert not (set(lineart.choose([slide(line)], "chai roti meal", "family").values()) & FOOD), line
        assert not lineart.food_allowed([line], False)
    for line in ("Chai with the family at home", "Wondering what to cook tonight?"):   # no outcome named: food may
        assert not lineart.names_outcome(line) and lineart.food_allowed([line], False)
    assert set(lineart.choose([slide("Chai with the family at home")]).values()) & FOOD


def test_more_framing_and_cta_words_find_a_drawing_deterministically():
    for text, want in [("See if the programme fits you", "two_hands"), ("Wondering what to ask your doctor?", None),
                       ("Start with a walk after dinner", "walking_shoes"), ("Check your genes and DNA", "dna")]:
        got = lineart.choose([slide(text)])
        assert 0 in got and (want is None or got[0] == want), (text, got)


def test_the_min_gap_is_48px_to_every_text_box_footer_included():
    foot = [96, 1240, 200, 1290]                                  # the wordmark
    assert lineart.PAD == 48
    assert lineart.overlaps([300, 1100, 600, 1150], [foot], lineart.PAD) == []        # far above
    assert lineart.overlaps([200 + 40, 1100, 600, 1350], [foot], lineart.PAD)                           # 40 px right of it: too close
    assert not lineart.overlaps([200 + 49, 1100, 600, 1350], [foot], lineart.PAD)                       # 49 px: fine
    box = lineart.place(1.0, [foot])
    assert box and not lineart.overlaps(box, [foot], lineart.PAD)


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
