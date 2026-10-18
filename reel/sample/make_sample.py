"""Writes sample/props.json: a synthetic reel_props.json (evenly spaced words, West lines) for developing the
composition without the voice. Run: uv run --project .. python sample/make_sample.py"""
import json
from pathlib import Path

import jsonschema

HERE = Path(__file__).parent
PAL = {"cream": "#F5EBDF", "rust": "#C04E2A", "maroon": "#661414", "ink": "#351D1D", "clay": "#B58777",
       "blush": "#E6CBBD", "paper": "#F8F2EB"}
NB = " "
t = 0.3


def line(kind, text, beats_n=3, per=0.32, hold=0.8, key=None, emph=None, src=None):
    global t
    words = text.split(" ")
    starts = []
    for _ in words:
        starts.append(round(t, 3))
        t += per
    end = round(t, 3)
    beats, i = [], 0
    while i < len(words):
        n = min(beats_n, len(words) - i)
        beats.append({"text": " ".join(words[i:i + n]), "start": starts[i],
                      "words": [{"text": words[j], "start": starts[j]} for j in range(i, i + n)]})
        i += n
    hold_end = round(end + hold, 3)
    t = hold_end + 0.25
    k = None
    if key:
        at = next(starts[j] for j, w in enumerate(words) if w.startswith(key[0]))
        k = {"number": key[0], "label": key[1], "at": at}
    return {"kind": kind, "text": text, "start": starts[0], "end": end, "hold_end": hold_end, "beats": beats, "cards": None, "evidence": None,
            "emphasis": emph, "key": k, "source_label": src}


scenes = []


def scene(kind, ground, lines, trans="wipe"):
    s0 = max(0.0, lines[0]["start"] - 0.3) if scenes else 0.0
    scenes.append({"kind": kind, "ground": ground, "start": round(s0, 3), "end": round(t, 3), "transition_in": trans,
                   "broll": None, "presenter": None, "captions": False, "zoom": [], "checklist": None, "pullback": None, "lines": lines, "sources": None})


SRC = "West et al. 2026, section text, no page layout"
scene("hook", "cream", [line("framing", "Will the weight come back when the medicine stops?", emph="come back")], trans="none")
scene("point", "rust", [line("health", "After stopping weight management medicines, the time to no difference between treatment "
                             f"and control was 1.4{NB}years.", key=(f"1.4{NB}years", "time to no difference between treatment and control"), src=SRC)])
scene("point", "cream", [line("health", "Improvements in weight and cardiometabolic health fade soon after treatment ends, with "
                              f"no evidence of benefit 1.7{NB}years after stopping treatment.", src=SRC)])
scene("brand", "maroon", [line("brand", "A planned exit. We help you build habits that last after the medicine stops.", hold=0.35)])
scene("cta", "cream", [line("cta", "Talk to a Steer doctor", hold=0.3), line("signoff", "steerclub.com", hold=0.5)])
start = t
t += 5.0
scenes.append({"kind": "sources", "ground": "cream", "start": round(start, 3), "end": round(t, 3), "transition_in": "wipe",
               "broll": None, "presenter": None, "captions": False, "zoom": [], "checklist": None, "pullback": None, "lines": [], "sources": {
                   "heading": "Sources",
                   "entries": [{"before": "West S, Scragg J, Aveyard P et al. Weight regain after cessation of medication for "
                                          "weight management: systematic review and meta-analysis.",
                                "journal": "BMJ", "after": ", 2026. doi:10.1136/bmj-2025-085304. Section text, no page layout."}],
                   "footnote": "Every claim on these slides is linked to the paper. Pending review by a Steer doctor before publishing.",
                   "url": "steerclub.com"}})
props = {"version": 3, "fps": 30, "width": 1080, "height": 1920, "durationInFrames": int(round(t * 30)), "audio": "voice.wav",
         "fonts": {"serif": "Fraunces.ttf", "serif_italic": "Fraunces-Italic.ttf", "sans": "Inter.ttf"}, "palette": PAL,
         "chrome": {"draft_mark": "draft · pending doctor review", "wordmark": "steer"}, "scenes": scenes}
jsonschema.validate(props, json.loads((HERE.parent / "props.schema.json").read_text()))
(HERE / "props.json").write_text(json.dumps(props, ensure_ascii=False, indent=1))
print("sample ok", round(t, 2), "s,", props["durationInFrames"], "frames")
