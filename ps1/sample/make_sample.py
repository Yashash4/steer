"""Writes sample/claims.json: a schema-valid claims.json for developing the PS1 page. Real claim texts from the stored
runs, but NOT a build: `steer ps1 build` makes the real one and fails on any unverified claim (the Laverde pair here
is a layout sample only; its -1.74 kg claim is not yet usable). Run: uv run python ps1/sample/make_sample.py"""
import json
from pathlib import Path

import jsonschema

HERE = Path(__file__).parent
PAL = {"cream": "#F5EBDF", "rust": "#C04E2A", "maroon": "#661414", "ink": "#351D1D", "clay": "#B58777", "blush": "#E6CBBD",
       "paper": "#F8F2EB"}
NARAYAN = {"short": "Narayan et al. 2021", "doi": "10.1136/bmjdrc-2020-001927", "location": "page 5",
           "full": "Narayan KMV, Kondal D, Daya N et al. Incidence and pathophysiology of diabetes in South Asian adults living in "
                   "India and Pakistan compared with US blacks and whites. BMJ Open Diabetes Research & Care, 2021."}
WEST = {"short": "West et al. 2026", "doi": "10.1136/bmj-2025-085304", "location": "section text, no page layout",
        "full": "West S, Scragg J, Aveyard P et al. Weight regain after cessation of medication for weight management: "
                "systematic review and meta-analysis. BMJ, 2026."}
LAVERDE = {"short": "Laverde et al. 2026", "doi": "10.1038/s41366-026-00000-0", "location": "page 1",
           "full": "Laverde et al. GLP-1 receptor agonists and lean mass: systematic review and meta-analysis. "
                   "International Journal of Obesity, 2026."}
times = [{"label": f"{n}×", "value": n} for n in range(1, 11)]
hook_sentence = ("South Asian women aged 45 and over in Chennai, Delhi and Karachi with BMI under 25 kg/m2 had 5.3 times "
                 "higher diabetes risk than US white women.")
claims = {
    "version": "1.1", "built_at": "2026-10-18T14:00:00+00:00",
    "brand": {"palette": PAL, "fonts": {"serif": "Fraunces.ttf", "serif_italic": "Fraunces-Italic.ttf", "sans": "Inter.ttf"},
              "wordmark": "steer", "signoff": "steerclub.com", "footer": "Educational hackathon prototype · content pending review by a Steer doctor · not a medical assessment"},
    "ui": {"lock": "Lock my guess", "you_guessed": "You guessed", "see_quote": "See the exact sentence",
           "hide_quote": "Hide the sentence", "next": "Next", "pick_worries": "Pick your worries",
           "pick_hint": "Tap the questions you have. Each one is a short round.", "start_rounds": "Start",
           "done": "Already answered", "readout_title": "Your readout", "questions_title": "Questions to take to a doctor",
           "restart": "Play again"},
    "hook": {"id": "hook", "doubt": "is_it_for_me", "kind": "slider",
             "context": "Adults aged 45+ in Chennai, Delhi and Karachi · BMI below 25 · South Asian women vs US white women",
             "question": "Guess the difference. How did diabetes incidence compare in this study?",
             "stops": times, "answer": 5.3,
             "reveals": [{"number": "5.3 times", "label": "higher diabetes risk", "sentence": hook_sentence}],
             "qualifiers": [],
             "quote": "Similarly, in women, the risk of diabetes in those with BMI <25 kg/m2 was 5.3 times higher in South Asian "
                      "(IRR (95% CI) 5.27 (3.64 to 7.54)) than in white women.",
             "source": NARAYAN, "claim_ids": ["run-244d0fbd0c04/c004-01"]},
    "doubts": [{"id": "is_it_safe", "label": "Is it safe?", "answered_by": ["gap-safety"]},
               {"id": "will_i_lose_muscle", "label": "Will I lose muscle?", "answered_by": ["muscle-a", "muscle-b"]},
               {"id": "will_weight_come_back", "label": "Will the weight come back?", "answered_by": ["regain"]},
               {"id": "only_for_diabetics", "label": "Is this only for diabetics?", "answered_by": ["gap-diabetics"]},
               {"id": "is_it_for_me", "label": "Is it even for me?", "answered_by": ["hook"]}],
    "rounds": [
        {"id": "regain", "doubt": "will_weight_come_back", "kind": "slider", "context": None,
         "question": "After stopping a weight management medicine, how fast does weight come back each month?",
         "stops": [{"label": "None", "value": 0}, {"label": "0.1 kg", "value": 0.1}, {"label": "0.4 kg", "value": 0.4},
                   {"label": "1 kg", "value": 1}, {"label": "2 kg", "value": 2}], "answer": 0.4,
         "reveals": [{"number": "0.4 kg", "label": "average monthly rate of weight regain",
                      "sentence": "The average monthly rate of weight regain was 0.4 kg (95% CI 0.3 to 0.5) after stopping."}],
         "qualifiers": ["An average across studies, not a prediction for you.", "All weight-management medicines, not only GLP-1."],
         "quote": "The average monthly rate of weight regain was 0.4 kg (95% confidence interval (CI) 0.3 to 0.5)",
         "source": WEST, "claim_ids": ["run-a7cddabed62e/c000-01"]},
        {"id": "muscle-a", "doubt": "will_i_lose_muscle", "kind": "choice", "context": None,
         "question": "On these medicines, does the amount of lean mass in kilograms go up, down or stay the same?",
         "stops": [{"label": "Up", "value": "up"}, {"label": "Down", "value": "down"}, {"label": "No change", "value": "same"}],
         "answer": "down",
         "reveals": [{"number": "1.74 kg", "label": "Absolute lean mass fell",
                      "sentence": "Absolute lean mass fell by 1.74 kg on average."}],
         "qualifiers": ["Results varied a lot between studies."],
         "quote": "(layout sample only: needs check_cleared before it is usable)", "source": LAVERDE, "claim_ids": []},
        {"id": "muscle-b", "doubt": "will_i_lose_muscle", "kind": "choice", "context": None,
         "question": "And lean mass as a share of body weight: up, down or the same?",
         "stops": [{"label": "Up", "value": "up"}, {"label": "Down", "value": "down"}, {"label": "No change", "value": "same"}],
         "answer": "up",
         "reveals": [{"number": "1.81%", "label": "Lean mass as a share of total weight rose",
                      "sentence": "Lean mass as a share of total weight rose by 1.81% on average."}],
         "qualifiers": [], "quote": "GLP1-RA significantly improved lean mass as a proportion of total weight, with an observed "
                                    "increase of 1.81% (95% CI: 1.1– 2.52; p < 0.00001; I² = 7%).",
         "source": LAVERDE, "claim_ids": ["run-e4d5ed8559e6/c000-00"]}],
    "gap_cards": [{"id": "gap-safety", "title": "Is it safe?",
                   "body": "Our papers don't answer this well. This is one to ask a doctor, with your own history."},
                  {"id": "gap-diabetics", "title": "Is this only for diabetics?",
                   "body": "Our papers don't answer this well. This is one to ask a doctor, with your own history."}],
    "readout": {"under": "You guessed below the study on {n} of {m}", "over": "You guessed above the study on {n} of {m}",
                "close": "You were close on {n} of {m}", "direction": "You got the direction right on {n} of {m}",
                "meaning_title": "What these studies tell us — and what they don't"},
    "doctor_questions": {"is_it_for_me": ["Should my lab tests be read differently because I am South Asian?"],
                         "will_weight_come_back": ["What would a planned exit look like for me?"],
                         "will_i_lose_muscle": ["How do we protect my muscle while I lose weight?"],
                         "is_it_safe": ["Which screening tests should come before any medicine?"],
                         "only_for_diabetics": ["Does my risk call for care before diabetes?"]},
    "cta": {"primary": {"label": "See if the programme fits you", "url": "https://steerclub.com/?utm_source=steer_quiz&utm_campaign=guess_the_number"},
            "secondary": {"label": "Talk to a Steer doctor", "url": "https://steerclub.com/?utm_source=steer_quiz&utm_campaign=guess_the_number"}},
    "warm_lead": {"title": "What Steer's team would see", "note": "Shown, not sent: this page makes no network call and has no form.",
                  "doubts_label": "Questions picked", "guesses_label": "Guesses and the real numbers"},
}
# v2: one-tap hook (bins), openers on every round and card, progress strings, the demo form's strings
claims["ui"] |= {"progress": "Question {n} of {m}", "progress_first": "Question {n}"}
claims["hook"] |= {"kind": "choice", "question": "Think a 'normal' BMI rules out diabetes?",
                   "stops": [{"label": "About the same", "value": 1}, {"label": "About 2×", "value": 2},
                             {"label": "About 3×", "value": 3}, {"label": "5× or more", "value": 5}]}
for r, o in zip([claims["hook"], *claims["rounds"]], ["Chai break at the office, and someone brings up blood sugar.",
                "On the bus home, a reel about weight medicines pops up on the phone.",
                "Morning in the neighbourhood park, and a friend asks about sugar tests.", "A cousin's wedding, and the aunties are comparing health reports."]):
    r["opener"] = o
for g in claims["gap_cards"]:
    g["opener"] = "Family dinner at home, and the talk turns to health check-ups."
claims["gap_cards"][1] |= {"context": "This review looked at non-diabetic subjects with overweight or obesity.",
                           "opener": "Tea with the neighbours on the balcony, and someone asks who these medicines are for."}
claims["warm_lead"] = {"title": "What your first conversation could start from",
    "note": "Prototype only. Nothing is sent or stored. Use test details, not real contact information.",
    "doubts_label": "Questions picked", "guesses_label": "Guesses and the real numbers", "name_label": "Name",
    "phone_label": "Phone", "consent_label": "I agree to be contacted (demo only)", "show_label": "Show what Steer would receive",
    "preview_title": "What Steer would receive", "copy_label": "Copy my questions", "share_label": "Share my questions",
    "copied": "Copied", "consent_short": "Consent", "yes_label": "yes", "your_guess": "Your guess", "the_study": "The study", "share_title": "Questions to take to a doctor"}
for r in [claims["hook"], *claims["rounds"]]:
    r["prompt"] = None
claims["hook"]["prompt"] = "Guess: how did diabetes rates compare?"
jsonschema.validate(claims, json.loads((HERE.parent / "claims.schema.json").read_text()))
for r in [claims["hook"], *claims["rounds"]]:
    for rv in r["reveals"]:
        assert rv["number"] in rv["sentence"] and rv["label"] in rv["sentence"], rv
(HERE / "claims.json").write_text(json.dumps(claims, ensure_ascii=False, indent=1))
print("sample claims.json ok")
