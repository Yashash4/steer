import json
from pathlib import Path

import pytest

from steer_pipeline import cli
from steer_pipeline.pdf import ingest_pdf
from steer_pipeline.text import ingest_text
from steer_pipeline.verify import verify

FIX = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="module")
def two():
    return ingest_pdf(FIX / "gayathri2023-frontiers-two-column.pdf", "evidence")


@pytest.fixture(scope="module")
def one():
    return ingest_pdf(FIX / "qi2026-peerj-single-column.pdf", "evidence")


def run(doc, text, quote, docs=None, **kw):
    claim = {"claim_id": "c1", "text": text, "document_id": doc["document_id"], "quote": quote}
    return verify([claim], docs or [doc], **kw)["results"][0]


def codes(result):
    return [r["code"] for r in result["reasons"]]


ABSTRACT = "HOMA IR (−0.4 (−0.7, −0.04; p = 0.03)"


# --- Frontiers (two-column) planted errors, each with a passing control ---

def test_f1_changed_number(two):
    q = "A total of 352 participants completed the study."
    assert codes(run(two, "325 participants completed the study", q)) == ["NUMBER_NOT_IN_QUOTE"]
    ok = run(two, "352 participants completed the study", q)
    assert ok["status"] == "pass" and ok["matches"][0]["sids"] == ["p0002-s0002"]


def test_f2_flipped_sign(two):
    assert codes(run(two, "HOMA IR changed by +0.4 (−0.7, −0.04; p = 0.03)", ABSTRACT)) == ["NUMBER_NOT_IN_QUOTE"]
    assert run(two, "HOMA IR changed by -0.4 (-0.7, -0.04; p = 0.03)", ABSTRACT)["status"] == "pass"


def test_f3_changed_unit(two):
    q = "total cholesterol (−5.4 mg/dl (−10.2, −0.6); p = 0.03)"
    assert codes(run(two, "total cholesterol fell by −5.4 mmol/L", q)) == ["NUMBER_NOT_IN_QUOTE"]
    assert run(two, "total cholesterol fell by −5.4 mg/dL", q)["status"] == "pass"


def test_f4_explicit_plus_sign(two):
    q = "[mean (95% CI) = + 0.7 mmol/L (0.1, 1.3); p = 0.03]"
    assert run(two, "DIo rose by +0.7 mmol/L (95% CI 0.1, 1.3)", q)["status"] == "pass"
    assert codes(run(two, "DIo changed by −0.7 mmol/L", q)) == ["NUMBER_NOT_IN_QUOTE"]


def test_f5_invented_quote(two):
    r = run(two, "Almonds cut weight", "Almond intake reduced body weight by 5 kg in 12 weeks.")
    assert codes(r) == ["QUOTE_NOT_FOUND"]
    assert r["status"] == "reject" and r["matches"] == []


def test_f6_short_quotes(two):
    assert run(two, "352 people completed the study", "352 participants")["status"] == "pass"
    assert codes(run(two, "Most people completed the study", "completed the study")) == ["QUOTE_TOO_SHORT"]


def test_f7_non_evidence_role(two):
    ctx = ingest_pdf(FIX / "gayathri2023-frontiers-two-column.pdf", "contextual")
    q = "A total of 352 participants completed the study."
    r = run(ctx, "352 participants completed the study", q, docs=[two, ctx])
    assert codes(r) == ["SOURCE_NOT_EVIDENCE"]
    assert run(two, "352 participants completed the study", q, docs=[two, ctx])["status"] == "pass"


def test_f8_table_text_is_flagged(two):
    q = "Body weight (kg) 71.8 ± 11.3 71.8 ± 11.5 0.0 ± 1.7 1.00"
    r = run(two, "Control group body weight was 71.8 ± 11.3 at baseline", q)
    assert r["status"] == "pass" and r["from_table_text"] is True
    assert r["matches"][0]["sids"][0].startswith("p0005-")
    body = run(two, "352 participants completed the study", "A total of 352 participants completed the study.")
    assert body["from_table_text"] is False


def test_f9_compound_hyphen_matches_joined_text(two):
    q = "The follow-up of participants every fortnight was conducted by trained research dietitians."
    assert run(two, "Dietitians followed participants every fortnight", q)["status"] == "pass"


# --- PeerJ (single-column) ---

def test_p1_comparator_and_kg_m2(one):
    q = "with a body mass index (BMI) >30 kg/m2 or a BMI ≥27 kg/m2"
    assert codes(run(one, "BMI of 30 kg/m² or more", q)) == ["NUMBER_NOT_IN_QUOTE"]
    assert run(one, "adults with BMI >30 kg/m²", q)["status"] == "pass"


def test_p2_range_end(one):
    q = ("approximately 55%–65% of the weight lost is regained within one year of discontinuation "
         "(Gaesser et al., 2025).")
    assert codes(run(one, "50%–65% of lost weight returns within one year", q)) == ["NUMBER_NOT_IN_QUOTE"]
    assert run(one, "55% to 65% of lost weight returns within one year", q)["status"] == "pass"


def test_p3_thousands_separator(one):
    q = "six studies involving 8,993 patients were included in the quantitative analysis"
    assert run(one, "The analysis covered 8993 patients", q)["status"] == "pass"
    assert codes(run(one, "The analysis covered 8,939 patients", q)) == ["NUMBER_NOT_IN_QUOTE"]


def test_p4_ellipsis(one):
    assert codes(run(one, "Six studies", "six studies involving ... patients")) == ["QUOTE_ELLIPSIS"]
    assert codes(run(one, "Six studies", "six studies involving … patients")) == ["QUOTE_ELLIPSIS"]


# --- synthetic ---

@pytest.fixture()
def note(tmp_path):
    p = tmp_path / "note.txt"
    p.write_text("In the trial, adherence rose by 5 percentage points over the year.\n")
    return ingest_text(p, "evidence")


def test_s1_percentage_points(note):
    q = "adherence rose by 5 percentage points over the year"
    assert codes(run(note, "Adherence rose 5%", q)) == ["NUMBER_NOT_IN_QUOTE"]
    assert run(note, "Adherence rose 5 pp", q)["status"] == "pass"


def test_s2_min_words_setting(two):
    assert codes(run(two, "People completed the study", "completed the study")) == ["QUOTE_TOO_SHORT"]
    assert run(two, "People completed the study", "completed the study", min_words=3)["status"] == "pass"


def test_unknown_document_and_empty_quote(two):
    r = verify([{"claim_id": "x", "text": "t", "document_id": "doc-0000000000000000", "quote": ""}], [two])
    assert codes(r["results"][0]) == ["SOURCE_UNKNOWN", "QUOTE_EMPTY"]


def test_report_counts_and_cli(two, tmp_path):
    doc_path = tmp_path / "doc.json"
    doc_path.write_text(json.dumps(two))
    claims = [
        {"claim_id": "ok", "text": "352 participants", "document_id": two["document_id"],
         "quote": "A total of 352 participants completed the study."},
        {"claim_id": "bad", "text": "325 participants", "document_id": two["document_id"],
         "quote": "A total of 352 participants completed the study."},
    ]
    claims_path = tmp_path / "claims.json"
    claims_path.write_text(json.dumps(claims))
    out = tmp_path / "report.json"
    assert cli.main(["verify", str(claims_path), "--doc", str(doc_path), "-o", str(out)]) == 1
    report = json.loads(out.read_text())
    assert report["summary"] == {"claims": 2, "passed": 1, "rejected": 1}
    assert [r["status"] for r in report["results"]] == ["pass", "reject"]
    assert report["gate_version"] == "gate-1" and report["min_words"] == 5


def test_hedge_dropped_is_a_warning_not_a_rejection(one):
    q = ("approximately 55%–65% of the weight lost is regained within one year of discontinuation "
         "(Gaesser et al., 2025).")
    r = run(one, "55% to 65% of lost weight returns within one year", q)
    assert r["status"] == "pass" and [w["code"] for w in r["warnings"]] == ["HEDGE_DROPPED"]
    r = run(one, "about 55% to 65% of lost weight returns within one year", q)
    assert r["status"] == "pass" and r["warnings"] == []


def test_duration_wording_passes_gate(two):
    q = "The intervention group received 43 g of almonds/day for 12 weeks"
    assert run(two, "Participants ate 43 g of almonds a day over 12 weeks", q)["status"] == "pass"
