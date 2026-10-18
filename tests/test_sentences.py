from steer_pipeline.assemble import assemble
from steer_pipeline.sentences import build_sentences, split_spans


def sents(text):
    return [text[s:e] for s, e in split_spans(text)]


def test_basic_and_abbreviations():
    t = "Smith et al. found a drop. Values fell (e.g. HOMA IR fell by 0.4). Fig. 2 shows it! Is it? Yes."
    assert sents(t) == ["Smith et al. found a drop.", "Values fell (e.g. HOMA IR fell by 0.4).",
                        "Fig. 2 shows it!", "Is it?", "Yes."]


def test_decimals_initials_brackets_and_newlines():
    t = "Mean was 15.09 years (range 14. To 17) per J. Smith. 352 people took part\nHeading\nNext one."
    assert sents(t) == ["Mean was 15.09 years (range 14. To 17) per J. Smith.", "352 people took part",
                        "Heading", "Next one."]


def test_no_split_before_lowercase():
    assert sents("p < 0.05. vs. control it was lower. Then more.") == [
        "p < 0.05. vs. control it was lower.", "Then more."]


def test_sids_restart_per_page_and_use_start_page():
    ls = [
        {"line_id": "p0001-l0001", "page": 1, "raw": "First sentence. Second sentence starts", "para_break": False},
        {"line_id": "p0002-l0001", "page": 2, "raw": "on page two. Third.", "para_break": False},
    ]
    out = build_sentences(assemble(ls), ls)
    assert [(s["sid"], s["text"]) for s in out] == [
        ("p0001-s0001", "First sentence."),
        ("p0001-s0002", "Second sentence starts on page two."),
        ("p0002-s0001", "Third."),
    ]
    assert out[1]["line_ids"] == ["p0001-l0001", "p0002-l0001"]
    assert out[2]["line_ids"] == ["p0002-l0001"]
