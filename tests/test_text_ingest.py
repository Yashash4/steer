import json

from conftest import check_map
from steer_pipeline import cli
from steer_pipeline.text import ingest_text

BRIEF = """Theme: why weight comes back after diets.

Audience: adults in India who tried diets.
Tone: warm, doctor-led. No promises.
"""


def test_text_ingest_contract(tmp_path, validate):
    p = tmp_path / "brief.txt"
    p.write_text(BRIEF, encoding="utf-8")
    doc = ingest_text(p, role="editorial")
    validate(doc)
    check_map(doc)
    assert doc["source"]["role"] == "editorial"
    assert doc["source"]["source_type"] == "text"
    assert doc["source"]["source_id"] == "src-" + doc["source"]["content_sha256"][:16]
    assert doc["extraction_version"] == "text-1"
    assert doc["text"] == ("Theme: why weight comes back after diets.\n"
                           "Audience: adults in India who tried diets. Tone: warm, doctor-led. No promises.")
    assert [s["sid"] for s in doc["sentences"]] == [f"p0001-s{i:04d}" for i in range(1, 5)]
    assert doc["sentences"][1]["line_ids"] == ["p0001-l0002"]


def test_same_bytes_same_ids(tmp_path):
    p = tmp_path / "a.txt"
    p.write_text(BRIEF)
    a, b = ingest_text(p, "editorial"), ingest_text(p, "editorial")
    assert a["source"]["source_id"] == b["source"]["source_id"]
    assert a["sentences"] == b["sentences"]


def test_empty_text_warns(tmp_path, validate):
    p = tmp_path / "empty.txt"
    p.write_text("\n\n")
    doc = ingest_text(p, "contextual")
    validate(doc)
    assert doc["sentences"] == []
    assert doc["warnings"][0]["code"] == "EMPTY_PAGE"


def test_bad_role_rejected(tmp_path):
    p = tmp_path / "a.txt"
    p.write_text("x")
    try:
        ingest_text(p, "doctor")
    except ValueError as e:
        assert "role must be one of" in str(e)
    else:
        raise AssertionError("bad role accepted")


def test_cli_ingest_text(tmp_path, validate):
    p = tmp_path / "protocol.md"
    p.write_text("Screening and blood tests come first.\n")
    out = tmp_path / "doc.json"
    assert cli.main(["ingest", str(p), "--role", "contextual", "-o", str(out)]) == 0
    doc = json.loads(out.read_text())
    validate(doc)
    assert doc["sentences"][0]["text"] == "Screening and blood tests come first."
