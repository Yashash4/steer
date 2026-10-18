import json

from steer_pipeline import posts

EN = [{"kind": "health", "text": "On average, weight came back at 0.4 kg a month, not a prediction for you.", "claim_ids": ["c1"]},
      {"kind": "signoff", "text": "steerclub.com", "claim_ids": []}]
BOUNDS = {"require_qualifier": [{"any_of": ["on average", "an average"]}, {"any_of": ["not a prediction for you"]}]}


class FakeRouter:
    def __init__(self, lines):
        self.lines, self.prompts = lines, []

    def call(self, job, system, prompt, schema, **kw):
        self.prompts.append(prompt)
        return {"output": {"lines": self.lines}, "endpoint": "fake", "model": "fake", "usage": {}}


def setup(tmp_path, monkeypatch, verdict="supported"):
    (tmp_path / "post.json").write_text(json.dumps({"post_id": "p1", "run_id": "r1", "post": {"caption": {"lines": EN, "hashtags": []}}}))
    (tmp_path / "caption.txt").write_text("x\n\nSources: West S (2026) BMJ, doi:10.1/x\n\n#steer")
    monkeypatch.setattr(posts, "claims_from_store", lambda con, run: ({}, {"citation": {"pmcid": "PMC1"}}))
    monkeypatch.setattr(posts, "load_boundaries", lambda pmcid: BOUNDS)
    monkeypatch.setattr(posts, "load_brand", lambda: {})
    monkeypatch.setattr(posts, "study_context", lambda b, d: [])
    monkeypatch.setattr(posts, "check_post", lambda post, *a, **k: {"post_failures": [], "lines": [
        {"path": f"caption/lines/{i}", "kind": ln["kind"], "text": ln["text"], "failures": [],
         "crosscheck": {"verdict": verdict} if ln["kind"] == "health" else None} for i, ln in enumerate(post["caption"]["lines"])]})


def test_a_hinglish_caption_keeps_numbers_and_qualifiers_and_passes_the_same_checks(tmp_path, monkeypatch):
    setup(tmp_path, monkeypatch)
    hi = [EN[0] | {"text": "On average, medicine band karne ke baad weight 0.4 kg a month wapas aaya, not a prediction for you."}, EN[1]]
    r = posts.hinglish_caption(tmp_path, None, {"rules": {}}, FakeRouter(hi))
    assert r["status"] == "passed" and r["keep_verbatim"] == ["not a prediction for you", "on average"]
    out = (tmp_path / "caption-hinglish.txt").read_text()
    assert "0.4 kg" in out and out.endswith("Sources: West S (2026) BMJ, doi:10.1/x\n\n#steer")


def test_a_changed_number_a_dropped_qualifier_or_an_unsupported_verdict_fails(tmp_path, monkeypatch):
    setup(tmp_path, monkeypatch)
    bad = [EN[0] | {"text": "Average mein weight 0.5 kg a month wapas aaya."}, EN[1]]
    r = posts.hinglish_caption(tmp_path, None, {"rules": {}}, FakeRouter(bad))
    assert r["status"] == "failed" and not (tmp_path / "caption-hinglish.txt").exists()
    assert any("numbers" in f for f in r["failures"]) and any("not a prediction for you" in f for f in r["failures"])
    setup(tmp_path, monkeypatch, verdict="unsupported")
    ok = [EN[0] | {"text": "On average, weight 0.4 kg a month wapas aaya, not a prediction for you."}, EN[1]]
    assert posts.hinglish_caption(tmp_path, None, {"rules": {}}, FakeRouter(ok))["status"] == "failed"


def test_capitalise_and_the_native_return_wording_passes_west_boundaries():
    from steer_pipeline import checks
    assert posts.capitalise("on average, 0.4 kg") == "On average, 0.4 kg" and posts.capitalise("") == ""
    west = checks.load_boundaries("PMC12776922")
    rule = next(r for r in west["require_qualifier"] if r.get("hinglish"))
    line = "28 randomised controlled trials mein, treatment groups ka weight control groups ke level par wapas aa gaya."
    assert any(checks._has(line, p) for p in rule["any_of"])                       # the native wording satisfies the rule
    assert not any(checks._has("28 trials mein treatment aur control groups ka weight barabar tha.", p) for p in rule["any_of"])
