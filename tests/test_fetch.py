import json
from pathlib import Path

import httpx
import pytest

from steer_pipeline.fetch import (FetchError, RetractedError, UnsupportedSource, classify, fetch, html_to_text,
                                  make_client)
from steer_pipeline.verify import verify

FIX = Path(__file__).parent / "fixtures"
HTTP = FIX / "http"
PDF = (FIX / "qi2026-peerj-single-column.pdf").read_bytes()  # the very PDF PMC serves (MD5 matches the OA record)
EMPTY_LISTING = ('<?xml version="1.0" encoding="UTF-8"?><ListBucketResult><KeyCount>0</KeyCount>'
                 '</ListBucketResult>')
TRICKY_ABSTRACT = (
    "<h4>Results</h4>Weight fell more with the programme (-1.74 kg; 95% CI -2.10 to -1.38; p &lt; 0.00001; "
    "I<sup>2</sup> = 98%), p < 0.00001<h4>Conclusions</h4>Adults with BMI <25 kg/m<sup>2</sup> &amp; "
    "waist &gt; 80 cm changed by &#x2212;0.5 kg.")


def recorded(**overrides):
    """A mock transport serving the responses recorded from the live APIs on 2026-10-18."""
    def handler(request: httpx.Request) -> httpx.Response:
        url, q = str(request.url), request.url.params
        key = (request.url.host, request.url.path)
        for match, resp in overrides.items():
            if match in url:
                return resp(request) if callable(resp) else resp
        if "idconv" in url:
            return httpx.Response(200, content=(HTTP / "idconv-peerj.json").read_bytes())
        if key == ("pmc-oa-opendata.s3.amazonaws.com", "/") and q.get("prefix") == "PMC13564392.":
            return httpx.Response(200, content=(HTTP / "s3-list-PMC13564392.xml").read_bytes())
        if key == ("pmc-oa-opendata.s3.amazonaws.com", "/"):
            return httpx.Response(200, content=EMPTY_LISTING.encode())
        if url.endswith("PMC13564392.1/PMC13564392.1.json"):
            return httpx.Response(200, content=(HTTP / "s3-PMC13564392.1.json").read_bytes())
        if url.endswith("PMC13564392.1/PMC13564392.1.pdf"):
            return httpx.Response(200, content=PDF, headers={"content-type": "application/pdf"})
        if "europepmc" in url:
            return httpx.Response(200, content=(HTTP / "europepmc-peerj.json").read_bytes())
        return httpx.Response(404)
    return make_client(httpx.MockTransport(handler))


def test_classify_inputs():
    assert classify("10.7717/peerj.21713") == ("doi", "10.7717/peerj.21713")
    assert classify("https://doi.org/10.7717/peerj.21713") == ("doi", "10.7717/peerj.21713")
    assert classify("42729958") == ("pmid", "42729958")
    assert classify("https://pubmed.ncbi.nlm.nih.gov/42729958/") == ("pmid", "42729958")
    assert classify("PMC13564392") == ("pmcid", "PMC13564392")
    assert classify("https://pmc.ncbi.nlm.nih.gov/articles/PMC13564392/") == ("pmcid", "PMC13564392")
    assert classify("https://example.org/paper.pdf") == ("url", "https://example.org/paper.pdf")
    with pytest.raises(UnsupportedSource):
        classify("weight regain paper")


@pytest.mark.parametrize("source", ["10.7717/peerj.21713", "42729958", "https://pubmed.ncbi.nlm.nih.gov/42729958/",
                                    "PMC13564392"])
def test_full_text_via_pmc_oa(source, validate):
    doc = fetch(source, "evidence", client=recorded())
    validate(doc)
    src = doc["source"]
    assert src["access"] == "full_text" and src["provenance"]["fetched_via"] == "pmc-oa-s3"
    assert src["provenance"]["resolved_url"] == \
        "https://pmc-oa-opendata.s3.amazonaws.com/PMC13564392.1/PMC13564392.1.pdf"
    c = doc["citation"]
    assert c["title"].startswith("Weight regain following discontinuation of glucagon-like peptide-1")
    assert c["authors"] == ["Qi H", "Gao Q", "Chen H", "Du X", "Pan B"]
    assert (c["journal"], c["year"], c["doi"], c["pmid"], c["pmcid"]) == \
        ("PeerJ", 2026, "10.7717/peerj.21713", "42729958", "PMC13564392")
    assert (c["license"], c["retracted"]) == ("CC BY", False)
    assert not any(w["code"] == "NO_CITATION_METADATA" for w in doc["warnings"])
    # same bytes as the committed fixture -> same source_id
    assert src["content_sha256"] == "a58745aa7a886769b3515ae55f5b0fded2b40412f35074bc06aace021a2150c6"


def test_md5_mismatch_is_an_error():
    bad = httpx.Response(200, content=PDF[:-10], headers={"content-type": "application/pdf"})
    with pytest.raises(FetchError, match="MD5 mismatch"):
        fetch("PMC13564392", "evidence", client=recorded(**{"PMC13564392.1.pdf": bad}))


def test_retracted_is_refused_unless_allowed():
    rec = json.loads((HTTP / "s3-PMC13564392.1.json").read_text()) | {"is_retracted": True}
    over = {"PMC13564392.1.json": httpx.Response(200, json=rec)}
    with pytest.raises(RetractedError, match="retracted"):
        fetch("PMC13564392", "evidence", client=recorded(**over))
    doc = fetch("PMC13564392", "evidence", client=recorded(**over), allow_retracted=True)
    assert {"code": "RETRACTED", "page": None, "detail": "PMC or Europe PMC marks this article as retracted"} in doc["warnings"]


def test_retracted_in_europepmc_on_the_abstract_path():
    # a real record (PMID 39549083): pubType "Retracted Publication" and a "Retraction in" link; not in PMC
    over = {"idconv": httpx.Response(200, json={"records": [{"pmid": 39549083, "doi": "10.1007/s10029-024-03185-8"}]}),
            "europepmc": httpx.Response(200, content=(HTTP / "europepmc-retracted.json").read_bytes())}
    with pytest.raises(RetractedError, match="retracted"):
        fetch("39549083", "evidence", client=recorded(**over))
    epmc = json.loads((HTTP / "europepmc-retracted.json").read_text())
    rec = epmc["resultList"]["result"][0]
    rec["abstractText"] = "This letter comments on case volume and complications in hernia surgery."
    rec["pubTypeList"] = {"pubType": ["Letter"]}  # only the correction link left
    over["europepmc"] = httpx.Response(200, json=epmc)
    with pytest.raises(RetractedError):
        fetch("39549083", "evidence", client=recorded(**over))
    doc = fetch("39549083", "evidence", client=recorded(**over), allow_retracted=True)
    assert doc["citation"]["retracted"] is True
    assert {w["code"] for w in doc["warnings"]} == {"RETRACTED", "ABSTRACT_ONLY"}


def test_abstract_only_when_not_open_access(validate):
    epmc = json.loads((HTTP / "europepmc-peerj.json").read_text())
    epmc["resultList"]["result"][0]["abstractText"] = TRICKY_ABSTRACT
    over = {"idconv": httpx.Response(200, json={"records": [{"pmid": 111, "doi": "10.1234/notopen"}]}),
            "europepmc": httpx.Response(200, json=epmc)}
    doc = fetch("10.1234/notopen", "evidence", client=recorded(**over))
    validate(doc)
    assert doc["source"]["access"] == "abstract_only"
    assert doc["source"]["provenance"]["fetched_via"] == "europepmc-abstract"
    assert any(w["code"] == "ABSTRACT_ONLY" for w in doc["warnings"])
    assert doc["extraction_version"] == "abstract-1"
    # nothing between a literal "<" and the next tag is lost; headings are their own paragraphs
    assert "(-1.74 kg; 95% CI -2.10 to -1.38; p < 0.00001; I2 = 98%), p < 0.00001" in doc["text"]
    assert "\nConclusions\n" in doc["text"]
    # the gate can check quotes and numbers from the abstract, kg/m<sup>2</sup> included
    claim = {"claim_id": "a", "text": "Adults with BMI <25 kg/m² changed by -0.5 kg", "document_id": doc["document_id"],
             "quote": "Adults with BMI <25 kg/m2 & waist > 80 cm changed by −0.5 kg."}
    assert verify([claim], [doc])["results"][0]["status"] == "pass"


def test_html_to_text_keeps_every_character():
    t = html_to_text(TRICKY_ABSTRACT)
    assert t == ("Results\n\nWeight fell more with the programme (-1.74 kg; 95% CI -2.10 to -1.38; p < 0.00001; "
                 "I2 = 98%), p < 0.00001\n\nConclusions\n\nAdults with BMI <25 kg/m2 & waist > 80 cm changed by "
                 "−0.5 kg.")


def test_inaccessible_link_fails_clearly():
    with pytest.raises(FetchError, match=r"https://example.org/missing.pdf answered HTTP 404"):
        fetch("https://example.org/missing.pdf", "evidence", client=recorded())

    def timeout(request):
        raise httpx.ConnectTimeout("timed out", request=request)
    with pytest.raises(FetchError, match=r"cannot reach .*idconv.*ConnectTimeout"):
        fetch("10.7717/peerj.21713", "evidence", client=recorded(idconv=timeout))


def test_not_in_pmc_and_no_abstract():
    over = {"idconv": httpx.Response(200, json={"records": [{"pmid": 111}]}),
            "europepmc": httpx.Response(200, json={"resultList": {"result": []}})}
    with pytest.raises(FetchError, match="no abstract found"):
        fetch("111", "evidence", client=recorded(**over))


def test_direct_pdf_and_html_doi_meta(validate):
    pdf = httpx.Response(200, content=PDF, headers={"content-type": "application/pdf"})
    doc = fetch("https://example.org/paper.pdf", "evidence", client=recorded(**{"example.org/paper.pdf": pdf}))
    validate(doc)
    assert doc["source"]["provenance"]["fetched_via"] == "direct-pdf"
    page = '<html><head><meta name="citation_doi" content="10.7717/peerj.21713"></head></html>'
    html = httpx.Response(200, text=page, headers={"content-type": "text/html"})
    doc = fetch("https://example.org/article", "evidence", client=recorded(**{"example.org/article": html}))
    assert doc["citation"]["pmcid"] == "PMC13564392"
    plain = httpx.Response(200, text="<html>no meta</html>", headers={"content-type": "text/html"})
    with pytest.raises(UnsupportedSource, match="no DOI meta tag"):
        fetch("https://example.org/blog", "evidence", client=recorded(**{"example.org/blog": plain}))


# --- live APIs (opt-in: uv run pytest -m network); the first-run papers ---

LIVE = [
    ("10.1136/bmj-2025-085304", "41500720", "PMC12776922"),
    ("10.1038/s41366-026-02118-y", "42321502", "PMC13461373"),
    ("10.1136/bmjdrc-2020-001927", "33771764", "PMC8006839"),
]


@pytest.mark.network
@pytest.mark.parametrize("doi,pmid,pmcid", LIVE)
def test_live_first_run_papers(doi, pmid, pmcid, validate):
    docs = [fetch(s, "evidence") for s in (doi, pmid, f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/")]
    for doc in docs:
        validate(doc)
        assert doc["citation"]["pmcid"] == pmcid and doc["citation"]["pmid"] == pmid
        assert doc["source"]["access"] == "full_text" and doc["source"]["provenance"]["fetched_via"] == "pmc-oa-s3"
        c = doc["citation"]
        assert c["title"] and c["authors"] and c["journal"] and c["year"] and c["license"]
        assert c["retracted"] is False
        assert len(doc["sentences"]) > 50
    assert len({d["source"]["source_id"] for d in docs}) == 1  # all three inputs fetch the same bytes


def test_full_text_from_pmc_plain_text_when_no_pdf(validate):
    # some OA records (e.g. BMJ's) carry the article as XML/TXT but no article PDF
    rec = json.loads((HTTP / "s3-PMC13564392.1.json").read_text())
    rec.pop("pdf_url")
    body = b"ARTICLE\n\nWeight regain was seen in 8,993 patients after stopping treatment.\n"
    import hashlib
    rec["text_url"] = f"s3://pmc-oa-opendata/PMC13564392.1/PMC13564392.1.txt?md5={hashlib.md5(body).hexdigest()}"
    over = {"PMC13564392.1.json": httpx.Response(200, json=rec), "PMC13564392.1.txt": httpx.Response(200, content=body)}
    doc = fetch("PMC13564392", "evidence", client=recorded(**over))
    validate(doc)
    assert doc["source"]["access"] == "full_text" and doc["extraction_version"] == "pmc-text-1"
    assert doc["source"]["provenance"]["resolved_url"].endswith("PMC13564392.1.txt")
    assert doc["sentences"][-1]["text"] == "Weight regain was seen in 8,993 patients after stopping treatment."
    assert {"code": "NO_PAGE_LAYOUT", "page": None,
            "detail": "PMC plain-text rendering: no pages or bounding boxes; locate claims by sentence"} in doc["warnings"]
