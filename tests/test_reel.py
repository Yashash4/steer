import io
import json
import wave

import pytest

from steer_pipeline import posts, reel

BRAND = posts.load_brand()


def test_spoken_form_expands_units_and_symbols_by_code_only():
    say = lambda t: reel.spoken(t)[0]
    assert say("0.4 kg a month") == "0.4 kilograms a month"
    assert say("BMI under 25 kg/m2.") == "BMI under 25 kilograms per square metre."
    assert say("rose by 1.81%") == "rose by 1.81 per cent"
    assert say("fell by −1.74 kg") == "fell by minus 1.74 kilograms"
    assert say("aged ≥45 years") == "aged at least 45 years"
    assert say("BMI <25") == "BMI less than 25"
    assert say("±0.3") == "plus or minus 0.3"
    assert say("(95% CI 2.09–4.28)") == "(95 per cent confidence interval 2.09 to 4.28)"
    assert say("I² = 98%") == "I squared = 98 per cent"
    assert say("120 mm Hg") == "120 millimetres of mercury"
    assert say("steerclub.com") == "steer club dot com"
    assert say("Talk to a Steer doctor") == "Talk to a Steer doctor"          # words are never reworded
    text, per_word = reel.spoken("0.4 kg a month")
    assert per_word == ["0.4", "kilograms", "a", "month"]                     # one entry per display word


def test_transcript_number_words_become_digits_and_mismatch_fails():
    assert reel.words_to_digits("two point nine nine times higher") == "2.99 times higher"
    assert reel.words_to_digits("twenty five kilograms") == "25 kilograms"
    assert reel.words_to_digits("twenty-five, minus 1.74") == "25 -1.74"
    assert reel.audio_numbers_match("fell by −1.74 kg", "fell by minus 1.74 kilograms")["ok"]
    ok = reel.audio_numbers_match("weight came back at 0.4 kg a month", "Weight came back at 0.4 kilograms a month.")
    assert ok["ok"]
    bad = reel.audio_numbers_match("weight came back at 0.4 kg a month", "Weight came back at 0.8 kilograms a month.")
    assert not bad["ok"] and bad["code"] == "AUDIO_NUMBER_MISMATCH" and bad["heard"] == ["0.8"]
    assert reel.audio_numbers_match("fell by −1.74 kg", "fell by -1.74 kg")["ok"]
    extra = reel.audio_numbers_match("Talk to a Steer doctor", "Talk to 2 Steer doctors")
    assert not extra["ok"]                                                    # a number heard but not shown fails too
    for empty in (None, "", "   "):                                           # nothing to compare: never a pass
        assert reel.audio_numbers_match("0.4 kg", empty)["code"] == "AUDIO_TRANSCRIPT_MISSING"


def test_captions_fit_two_lines_and_stay_long_enough():
    words = "Aged 45 and over in Chennai, Delhi and Karachi: South Asian men with body mass index under 25".split()
    for a, b in reel.chunk_caption(words):
        lines, cur = [[]], 0
        for w in words[a:b]:
            if cur + len(w) + (1 if cur else 0) > reel.CAPTION_CHARS:
                lines.append([]), cur
                cur = 0
            lines[-1].append(w)
            cur += len(w) + (1 if cur else 0)
        assert len(lines) <= 2
    text = "One two three four five six seven eight"
    caps = reel.captions_for_line(text, [0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7], 10.0, 11.5)
    assert all(c["end"] - c["start"] >= reel.MIN_CAPTION for c in caps) and caps[0]["start"] == 10.0
    assert " ".join(c["text"] for c in caps) == text
    srt = reel.srt([{"text": "Hello", "start": 0.5, "end": 2.0}])
    assert srt.startswith("1\n00:00:00,500 --> 00:00:02,000\nHello")


def test_west_end_card_shows_no_page_and_templates_pass_the_lint():
    west = [{"claim_id": "c1", "authors": "West S et al.", "title": "Weight regain", "journal": "J", "year": 2026,
             "doi": "10.1/w", "location": "section text, no page layout"}]
    card = reel.end_card_html(reel.rd.source_entries(west), BRAND["palette"])
    assert "Section text, no page layout" in card and "Page" not in card and "steerclub.com" in card
    assert reel.lint_template(card) == []
    scene = {"index": 0, "kind": "point", "start": 0.0, "end": 3.0, "captions": [{"text": "0.4 kg", "start": 0.5, "end": 2.0}],
             "lines": [{"kind": "health", "text": "On average 0.4 kg a month.", "claim_ids": ["c1"], "start": 0.5}]}
    page = reel.scene_html(scene, BRAND["palette"], {"c1": west[0]}, None)
    assert reel.lint_template(page) == [] and reel.rd.DRAFT in page
    assert reel.lint_template(page + "<script>el.textContent = '0.8'</script>") == ["<script", "textContent"]
    assert reel.lint_template("@property --n { syntax: '<integer>' } .x::after { content: counter(n) }")


def test_a_blocked_or_unrendered_post_is_refused(tmp_path):
    (tmp_path / "post.json").write_text(json.dumps({"status": "blocked", "render": None}))
    (tmp_path / "trace.json").write_text(json.dumps({"lines": []}))
    with pytest.raises(posts.ReelRefused) as e:
        posts.reel_post(tmp_path, None, {})
    assert [r["code"] for r in e.value.reasons] == ["REFUSED_STATUS", "REFUSED_NOT_RENDERED"]


def fake_tts(text, voice):
    n = len(text.split())
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1), w.setsampwidth(2), w.setframerate(24000), w.writeframes(b"\0\0" * int(24000 * (0.3 * n + 0.2)))
    return buf.getvalue(), [(word, 0.1 + 0.3 * i, 0.25) for i, word in enumerate(text.split())]


def test_a_short_reel_encodes_with_captions_and_the_draft_mark(tmp_path):
    record = {"post_id": "p1", "run_id": "r1", "sources": [
        {"claim_id": "c1", "authors": "West S et al.", "title": "Weight regain", "journal": "J", "year": 2026,
         "doi": "10.1/w", "location": "section text, no page layout"}]}
    script = {"scenes": [{"lines": [{"kind": "framing", "text": "Will the weight come back?", "claim_ids": []}]},
                         {"lines": [{"kind": "health", "text": "On average 0.4 kg a month, not a personal promise.",
                                     "claim_ids": ["c1"]}]}]}
    said = []
    tts = lambda t, v: (said.append(t), fake_tts(t, v))[1]
    t = reel.make_reel(record, script, BRAND, tmp_path, {"provider": "fake", "name": "fake"}, tts=tts,
                       transcribe=lambda wav: said.pop(0))
    assert t["probe"]["width"] == 1080 and t["probe"]["height"] == 1920 and t["probe"]["fps"] == 30.0
    assert t["probe"]["audio"]
    # the only failure of this 2-scene test reel is its length (under the 20 s floor)
    assert [f["code"] for f in t["failures"]] == ["DURATION_OUT_OF_RANGE"]
    assert all(r["ok"] for r in t["audio_round_trip"])
    cap = t["captions"][1]
    assert reel.box_has_content(tmp_path / "reel.mp4", (cap["start"] + cap["end"]) / 2, reel.SUBS_BOX)
    assert (tmp_path / "reel.srt").read_text().count("-->") == len(t["captions"])
    assert t["scenes"][-1]["kind"] == "sources" and t["scenes"][1]["lines"][0]["spoken"].startswith("On average 0.4 kilograms")


def test_display_tokens_keep_a_number_with_its_unit_and_beats_take_two_to_four():
    text = "Risk was 2.99 times higher with BMI under 25 kg/m2, in 12 983 people."
    toks = reel.display_tokens(text, [i * 0.3 for i in range(len(text.split()))])
    assert [t for t, _ in toks] == ["Risk", "was", "2.99 times", "higher", "with", "BMI", "under", "25 kg/m²,",
                                    "in", "12 983", "people."]
    assert toks[3][1] == 1.2                                                  # "higher" keeps its own time
    beats = reel.make_beats(toks)
    assert all(2 <= len(b["words"]) <= 4 for b in beats)
    assert " ".join(b["text"] for b in beats) == reel.display(text)           # nothing added, nothing lost
    assert any(b["text"].endswith("25 kg/m²,") for b in beats)          # cut after the comma


def kinetic_scenes():
    record = {"post_id": "p1", "run_id": "r1", "sources": [
        {"claim_id": "c1", "authors": "West S, Scragg J, Aveyard P et al.", "title": "Weight regain", "journal": "BMJ (Clinical research ed.)",
         "year": 2026, "doi": "10.1/w", "location": "section text, no page layout"}]}
    script = {"scenes": [{"lines": [{"kind": "framing", "text": "Will the weight come back?", "claim_ids": []}]},
                         {"lines": [{"kind": "health", "text": "On average weight came back at 0.4 kg a month.", "claim_ids": ["c1"]}]},
                         {"lines": [{"kind": "cta", "text": "Talk to a Steer doctor", "claim_ids": []}]}]}
    scenes, pcm, _ = reel.build_timeline(script, {"name": "x"}, fake_tts, reel.HEALTH_HOLD, reel.REMOTION_END_CARD)
    keys = [{"number": "0.4 kg a month", "label": "weight came back", "claim_ids": {"c1"}}]
    return record, script, scenes, keys


def test_props_carry_only_checked_text_and_the_pool_check_catches_anything_else():
    record, script, scenes, keys = kinetic_scenes()
    props = reel.build_props(scenes, record, BRAND, "come back", keys)
    assert reel.pool_check(props, script, record, "come back", keys) == []
    hook, point = props["scenes"][0]["lines"][0], props["scenes"][1]["lines"][0]
    assert hook["emphasis"] == "come back" and props["scenes"][0]["ground"] == "cream"
    assert point["key"]["number"] == "0.4 kg a month" and point["key"]["label"] == "weight came back"
    assert point["hold_end"] - point["end"] >= reel.HEALTH_HOLD - 1e-9        # the whole line holds >= 0.8 s
    assert point["source_label"] == "West et al. 2026, section text, no page layout"
    end = props["scenes"][-1]
    assert end["kind"] == "sources" and end["sources"]["entries"][0]["journal"] == "BMJ"
    assert [s["transition_in"] for s in props["scenes"]] == ["none", "wipe", "wipe", "wipe"]
    import copy as cp
    for path, value in [(("lines", 0, "text"), "On average weight came back at 0.8 kg a month."),   # a changed number
                        (("lines", 0, "source_label"), "Smith et al. 2020, page 1"),
                        (("lines", 0, "key"), {"number": "0.4 kg", "label": "per month", "at": 1.0})]:
        bad = cp.deepcopy(props)
        node = bad["scenes"][1]
        for k in path[:-1]:
            node = node[k]
        node[path[-1]] = value
        found = reel.pool_check(bad, script, record, "come back", keys)
        assert found and {f["code"] for f in found} == {"TEXT_NOT_FROM_SCRIPT"}
    bad = cp.deepcopy(props)
    bad["scenes"][1]["lines"][0]["beats"][0]["text"] = "Doctors agree"                 # a beat not in its line
    assert reel.pool_check(bad, script, record, "come back", keys)
    bad = cp.deepcopy(props)
    bad["scenes"][1]["extra"] = "x"                                                    # anything outside the schema
    assert [f["code"] for f in reel.pool_check(bad, script, record, "come back", keys)] == ["PROPS_SCHEMA"]


def test_checked_keys_come_from_the_posts_slides_and_static():
    line = {"kind": "health", "text": "On average weight came back at 0.4 kg a month.", "claim_ids": ["c1"]}
    post = {"slides": [{"role": "point", "lines": [line], "key_number": "0.4 kg a month", "key_label": "weight came back"}],
            "static": None}
    assert reel.checked_keys(post) == [{"number": "0.4 kg a month", "label": "weight came back", "claim_ids": {"c1"}}]
    post = {"static": {"headline": {"kind": "framing", "text": "Will it come back?", "claim_ids": []}, "supporting": [line],
                       "key_number": "0.4 kg a month", "key_label": "nothing like this"}}
    assert reel.checked_keys(post) == []                                       # a label not in its line is not a key
