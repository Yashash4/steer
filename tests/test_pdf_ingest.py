import json
import re
from pathlib import Path

import pymupdf
import pytest

from conftest import check_map
from steer_pipeline import cli
from steer_pipeline.pdf import OcrRequired, ingest_pdf

FIX = Path(__file__).parent / "fixtures"
SINGLE = FIX / "qi2026-peerj-single-column.pdf"
TWO = FIX / "gayathri2023-frontiers-two-column.pdf"


@pytest.fixture(scope="module")
def single():
    return ingest_pdf(SINGLE, "evidence")


@pytest.fixture(scope="module")
def two():
    return ingest_pdf(TWO, "evidence")


def test_contract_valid_and_traceable(single, two, validate):
    for doc in (single, two):
        validate(doc)
        check_map(doc)
        sids = [s["sid"] for s in doc["sentences"]]
        assert len(sids) == len(set(sids)), "sentence IDs must be unique within an extraction version"
        line_ids = {ln["line_id"] for p in doc["pages"] for ln in p["lines"]}
        for s in doc["sentences"]:
            assert set(s["line_ids"]) <= line_ids
            assert s["sid"].startswith(s["line_ids"][0][:5])  # sid page = page of its first line
            assert doc["text"][slice(*s["text_span"])] == s["text"]
        assert doc["extraction_version"] == "pdf-1"
        assert doc["extractor"]["name"] == "pymupdf"


def test_two_column_reading_order(two):
    p2 = two["pages"][1]
    assert p2["layout"] == "mixed"
    assert {two["pages"][i]["layout"] for i in (2, 3, 7, 8, 9, 10)} == {"two_column"}
    t = two["text"]
    # the left column's last line continues into the right column's first line
    assert "Thus, it is reasonable to hypothesize that providing healthier foods could improve the cardio-metabolic" in t
    # the offset single-column abstract comes before the two-column introduction
    assert t.index("A total of 352 participants completed the study.") < t.index("Obesity has become a global epidemic")
    # the left column is read top to bottom before the right column
    assert t.index("Obesity has become a global epidemic") < t.index("Insulin resistance and pancreatic")
    s = next(s for s in two["sentences"] if s["text"].startswith("Thus, it is reasonable to hypothesize"))
    cols = {ln["line_id"]: ln["column"] for ln in p2["lines"]}
    assert [cols[i] for i in s["line_ids"]][:2] == ["left", "right"]


def test_single_column_layout(single):
    assert {p["layout"] for p in single["pages"][1:5]} == {"single"}
    assert "Databases searched: PubMed, Embase, Cochrane Library and Scopus." in single["text"]


def test_furniture_removed_but_kept_on_page(single, two):
    assert "3/19" not in single["text"]
    assert "Qi et al. (2026), PeerJ, DOI" not in single["text"]
    furn2 = sorted(ln["raw"] for ln in two["pages"][1]["lines"] if ln["furniture"])
    # header and footer pieces sit far apart on their baselines, so they stay separate rows
    assert furn2 == ["02", "10.3389/fnut.2022.1055923", "Frontiers in Nutrition", "Gayathri et al.", "frontiersin.org"]
    furn = [ln["raw"] for ln in single["pages"][2]["lines"] if ln["furniture"]]
    assert sorted(furn) == ["3/19", "Qi et al. (2026), PeerJ, DOI 10.7717/peerj.21713"]


def test_hyphen_joins_keep_mapping(single, two):
    # "GLP-" at a line end before "1RA" keeps its hyphen
    assert "GLP-\n1RA" in single["raw_text"]
    i = single["raw_text"].index("GLP-\n1RA")
    seg = next(s for s in single["map"] if s[4] == "keep_hyphen" and s[2] == i + 4)
    assert single["text"][seg[0] - 4:seg[0] + 3] == "GLP-1RA"
    # a lowercase-lowercase break is de-hyphenated, and the raw "-\n" is recorded in the map
    j = two["raw_text"].index("follow-\nup")
    seg = next(s for s in two["map"] if s[4] == "dehyphenate" and s[2] == j + 6)
    assert two["raw_text"][seg[2]:seg[3]] == "-\n"
    assert two["text"][seg[0] - 6:seg[0] + 2] == "followup"  # documented limit; the gate ignores it


def test_sentence_crosses_page(two):
    s = next(s for s in two["sentences"] if s["text"].startswith("Hence, the present study was conducted"))
    assert s["sid"].startswith("p0002-")
    assert {i[:5] for i in s["line_ids"]} == {"p0002", "p0003"}


def test_rotated_table_page_kept_and_flagged(two):
    assert two["pages"][4]["layout"] != "empty"
    assert "Body weight (kg)" in two["text"]
    codes = {(w["code"], w["page"]) for w in two["warnings"]}
    assert ("TABLE_OR_FIGURE_TEXT", 5) in codes


def test_doi_from_first_page(single, two):
    assert single["citation"]["doi"] == "10.7717/peerj.21713"
    assert two["citation"]["doi"] == "10.3389/fnut.2022.1055923"
    assert any(w["code"] == "NO_CITATION_METADATA" for w in single["warnings"])


def test_deterministic(single):
    again = ingest_pdf(SINGLE, "evidence")
    assert again["sentences"] == single["sentences"]
    assert again["source"]["source_id"] == single["source"]["source_id"]


def _image_page(doc):
    page = doc.new_page()
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 200, 200), False)
    pix.clear_with(200)
    page.insert_image(pymupdf.Rect(50, 50, 400, 400), pixmap=pix)


def test_scanned_pdf_fails_clearly(tmp_path):
    doc = pymupdf.open()
    _image_page(doc)
    _image_page(doc)
    path = tmp_path / "scan.pdf"
    doc.save(path)
    with pytest.raises(OcrRequired, match="OCR required: no extractable text on any page"):
        ingest_pdf(path, "evidence")
    assert cli.main(["ingest", str(path), "--role", "evidence", "-o", str(tmp_path / "x.json")]) == 2


def test_partly_scanned_pdf_warns(tmp_path, validate):
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 72), "A real sentence on page one.")
    _image_page(doc)
    path = tmp_path / "part.pdf"
    doc.save(path)
    out = ingest_pdf(path, "evidence")
    validate(out)
    assert {"code": "OCR_REQUIRED_PAGE", "page": 2, "detail": "page has images but no extractable text"} in out["warnings"]
    assert out["sentences"][0]["text"] == "A real sentence on page one."


def test_cli_ingest_pdf(tmp_path, validate):
    out = tmp_path / "doc.json"
    assert cli.main(["ingest", str(TWO), "--role", "evidence", "-o", str(out)]) == 0
    doc = json.loads(out.read_text())
    validate(doc)
    assert re.fullmatch(r"src-[0-9a-f]{16}", doc["source"]["source_id"])


def test_unclosed_bracket_in_real_abstract(two):
    # the abstract has "HOMA IR (−0.4 (−0.7, −0.04; p = 0.03)" with one ")" missing
    p2 = [s["text"] for s in two["sentences"] if s["page"] == 2]
    assert any(s.startswith("Signiﬁcant improvement was seen in DIo") and s.endswith("compared to the control group.")
               for s in p2)
    assert any(s.startswith("Incremental area under the curve (IAUC)") for s in p2)
    assert any(s.startswith("Dietary 24-h recalls showed") for s in p2)


def test_font_size_change_splits_headings(single):
    p3 = [s["text"] for s in single["sentences"] if s["page"] == 3]
    assert "DATA AND METHODS" in p3
    assert "Inclusion and exclusion criteria" in p3
    assert any(s.startswith("Study population: All included studies") for s in p3)


def test_document_id_differs_by_role(two):
    ctx = ingest_pdf(TWO, "contextual")
    assert ctx["source"]["source_id"] == two["source"]["source_id"]  # same bytes
    assert ctx["document_id"] != two["document_id"]                  # different reading
    assert re.fullmatch(r"doc-[0-9a-f]{16}", two["document_id"])
    assert ingest_pdf(TWO, "evidence")["document_id"] == two["document_id"]


def test_two_columns_in_one_block_stay_apart(tmp_path):
    # TextWriter puts both columns into ONE PyMuPDF block, on shared baselines
    doc = pymupdf.open()
    page = doc.new_page()
    tw = pymupdf.TextWriter(page.rect)
    left = [f"Left line {i} tells the first story." for i in range(1, 9)]
    right = [f"Right line {i} tells another story." for i in range(1, 9)]
    for i, (lt, rt) in enumerate(zip(left, right)):
        tw.append((50, 100 + i * 14), lt, fontsize=10)
        tw.append((320, 100 + i * 14), rt, fontsize=10)
    tw.write_text(page)
    assert len([b for b in page.get_text("dict")["blocks"] if b["type"] == 0]) == 1
    path = tmp_path / "oneblock.pdf"
    doc.save(path)
    out = ingest_pdf(path, "evidence")
    assert out["pages"][0]["layout"] == "two_column"
    assert [s["text"] for s in out["sentences"]] == left + right
