"""Fetch a paper from a DOI, PubMed ID or URL, PMC ID or URL, or a direct PDF link.

Route:
1. DOI / PMID / PubMed URL -> NCBI PMC ID Converter -> PMCID.
2. PMCID -> PMC Article Datasets on AWS (public bucket `pmc-oa-opendata`): newest version's JSON
   (licence, retraction, title, citation) and its PDF (MD5-checked) -> the normal PDF path.
3. Not in the open-access set -> Europe PMC abstract -> an `abstract_only` document.
4. Any other URL: a PDF response is ingested; an HTML page is followed only through its DOI meta tag.
"""

import hashlib
import re
from html.parser import HTMLParser
from urllib.parse import parse_qs, quote, urlparse

import httpx

from .contract import build_document
from .pdf import ingest_pdf_bytes
from .text import text_lines

USER_AGENT = "steer-pipeline/0.1 (hackathon prototype)"
TIMEOUT = 30.0
IDCONV = "https://pmc.ncbi.nlm.nih.gov/tools/idconv/api/v1/articles/"
S3 = "https://pmc-oa-opendata.s3.amazonaws.com/"
EUROPEPMC = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
ABSTRACT_VERSION = "abstract-1"
PMC_TEXT_VERSION = "pmc-text-1"

PMCID_RE = re.compile(r"\b(PMC\d+)\b", re.I)
DOI_RE = re.compile(r"(10\.\d{4,9}/\S+)")
PUBMED_URL_RE = re.compile(r"pubmed\.ncbi\.nlm\.nih\.gov/(\d+)")


class FetchError(Exception):
    pass


class RetractedError(FetchError):
    pass


class UnsupportedSource(FetchError):
    pass


def make_client(transport: httpx.BaseTransport | None = None) -> httpx.Client:
    return httpx.Client(
        timeout=TIMEOUT,
        follow_redirects=True,
        headers={"User-Agent": USER_AGENT},
        transport=transport or httpx.HTTPTransport(retries=2),  # retries connection errors only
    )


def _get(client: httpx.Client, url: str, **params) -> httpx.Response:
    try:
        r = client.get(url, params=params or None)
    except httpx.HTTPError as e:
        raise FetchError(f"cannot reach {url}: {type(e).__name__}: {e}") from e
    if r.status_code != 200:
        raise FetchError(f"{url} answered HTTP {r.status_code}")
    return r


def classify(source: str) -> tuple[str, str]:
    """('pmcid' | 'pmid' | 'doi' | 'url', value) for a user input."""
    s = source.strip()
    if m := PUBMED_URL_RE.search(s):
        return "pmid", m.group(1)
    if re.fullmatch(r"\d{1,9}", s):
        return "pmid", s
    if (m := PMCID_RE.search(s)) and ("ncbi.nlm.nih.gov" in s or not s.lower().startswith("http")):
        return "pmcid", m.group(1).upper()
    if s.lower().startswith(("http://doi.org/", "https://doi.org/", "doi:")) or s.startswith("10."):
        m = DOI_RE.search(s)
        if m:
            return "doi", m.group(1).rstrip(".")
    if s.lower().startswith(("http://", "https://")):
        return "url", s
    raise UnsupportedSource(f"not a DOI, PubMed ID, PMC ID or URL: {source!r}")


def convert_id(client: httpx.Client, value: str) -> dict:
    """{'pmcid', 'pmid', 'doi'} from the NCBI ID Converter (pmcid is None when not in PMC)."""
    data = _get(client, IDCONV, ids=value, format="json", tool="steer-pipeline").json()
    rec = (data.get("records") or [{}])[0]
    pmid = rec.get("pmid")
    return {"pmcid": rec.get("pmcid"), "pmid": str(pmid) if pmid else None, "doi": rec.get("doi")}


def oa_record(client: httpx.Client, pmcid: str) -> dict | None:
    """The newest PMC OA JSON record for a PMCID, or None when it is not in the open-access set."""
    listing = _get(client, S3, **{"list-type": "2", "prefix": f"{pmcid}."}).text
    versions = sorted({int(v) for v in re.findall(rf"<Key>{pmcid}\.(\d+)/", listing)})
    if not versions:
        return None
    v = versions[-1]
    rec = _get(client, f"{S3}{pmcid}.{v}/{pmcid}.{v}.json").json()
    return rec if rec.get("pdf_url") or rec.get("text_url") else None


def _s3_https(s3_url: str) -> tuple[str, str | None]:
    """'s3://pmc-oa-opendata/KEY?md5=X' -> (https URL, md5)."""
    u = urlparse(s3_url)
    return S3 + quote(u.path.lstrip("/")), (parse_qs(u.query).get("md5") or [None])[0]


def europepmc_record(client: httpx.Client, ids: dict) -> dict | None:
    if ids.get("pmid"):
        query = f"EXT_ID:{ids['pmid']} AND SRC:MED"
    elif ids.get("doi"):
        query = f'DOI:"{ids["doi"]}"'
    else:
        return None
    data = _get(client, EUROPEPMC, query=query, resultType="core", format="json", pageSize="1").json()
    results = data.get("resultList", {}).get("result", [])
    return results[0] if results else None


class _HtmlText(HTMLParser):
    """Text of an HTML fragment: entities decoded, <sup>/<sub> text kept inline, block tags as paragraph breaks.

    A literal "<" in science ("p < 0.05", "<25 kg") is not a tag to html.parser and stays text.
    """
    BLOCK = {"p", "div", "br", "li", "h1", "h2", "h3", "h4", "h5", "h6", "section", "title"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag in self.BLOCK:
            self.parts.append("\n\n")

    def handle_endtag(self, tag):
        if tag in self.BLOCK:
            self.parts.append("\n\n")

    def handle_data(self, data):
        self.parts.append(data)


def html_to_text(fragment: str) -> str:
    p = _HtmlText()
    p.feed(fragment or "")
    p.close()
    text = "".join(p.parts)
    paras = [re.sub(r"[ \t\r\f\v]+", " ", x).strip() for x in re.split(r"\n\s*\n", text)]
    return "\n\n".join(x.replace("\n", " ") for x in paras if x)


def europepmc_retracted(epmc: dict | None) -> bool:
    """Europe PMC marks a retracted paper by publication type or by a 'Retraction in' correction link."""
    if not epmc:
        return False
    types = (epmc.get("pubTypeList") or {}).get("pubType") or []
    links = (epmc.get("commentCorrectionList") or {}).get("commentCorrection") or []
    return "Retracted Publication" in types or any(c.get("type") == "Retraction in" for c in links)


def _citation_from(oa: dict | None, epmc: dict | None, ids: dict) -> dict:
    c = {"title": None, "authors": [], "journal": None, "year": None,
         "doi": ids.get("doi"), "pmid": ids.get("pmid"), "pmcid": ids.get("pmcid"), "license": None, "retracted": None}
    if epmc:
        c["title"] = html_to_text(epmc.get("title", "")) or None
        c["authors"] = [a.strip() for a in (epmc.get("authorString") or "").rstrip(".").split(",") if a.strip()]
        c["journal"] = (epmc.get("journalInfo") or {}).get("journal", {}).get("title")
        c["year"] = int(epmc["pubYear"]) if str(epmc.get("pubYear", "")).isdigit() else None
        c["license"] = epmc.get("license")
        c["doi"] = c["doi"] or epmc.get("doi")
        c["pmid"] = c["pmid"] or epmc.get("pmid")
        c["pmcid"] = c["pmcid"] or epmc.get("pmcid")
        c["retracted"] = europepmc_retracted(epmc)
    if oa:  # the OA record is the authority for what we actually downloaded
        c["title"] = oa.get("title") or c["title"]
        c["license"] = oa.get("license_code") or c["license"]
        c["retracted"] = bool(oa.get("is_retracted")) or bool(c["retracted"])
        c["doi"] = oa.get("doi") or c["doi"]
        c["pmid"] = str(oa["pmid"]) if oa.get("pmid") else c["pmid"]
        c["pmcid"] = oa.get("pmcid") or c["pmcid"]
        if c["journal"] is None and oa.get("citation"):
            c["journal"] = oa["citation"].split(".")[0].strip() or None
        if c["year"] is None and (m := re.search(r"\b(19|20)\d{2}\b", oa.get("citation", ""))):
            c["year"] = int(m.group(0))
    return c


def fetch(source: str, role: str, client: httpx.Client | None = None, allow_retracted: bool = False) -> dict:
    """Fetch and ingest a paper. Raises FetchError (and subclasses) with the URL and status on failure."""
    own = client is None
    client = client or make_client()
    try:
        return _fetch(source, role, client, allow_retracted)
    finally:
        if own:
            client.close()


def _fetch(source: str, role: str, client: httpx.Client, allow_retracted: bool) -> dict:
    kind, value = classify(source)
    if kind == "url":
        return _fetch_url(source, role, client, allow_retracted)
    ids = {"pmcid": value, "pmid": None, "doi": None} if kind == "pmcid" else convert_id(client, value)
    if kind == "doi":
        ids["doi"] = ids["doi"] or value
    if kind == "pmid":
        ids["pmid"] = ids["pmid"] or value
    source_type = "pmc" if kind == "pmcid" else kind
    oa = oa_record(client, ids["pmcid"]) if ids.get("pmcid") else None
    if oa:  # a bare PMCID input learns its PMID and DOI here, for the metadata lookup
        ids["pmid"] = ids["pmid"] or (str(oa["pmid"]) if oa.get("pmid") else None)
        ids["doi"] = ids["doi"] or oa.get("doi")
    try:
        epmc = europepmc_record(client, ids)
    except FetchError:
        epmc = None  # metadata only; the OA record still carries title and licence
    citation = _citation_from(oa, epmc, ids)
    warnings = []
    if epmc is None:
        warnings.append({"code": "RETRACTION_CHECK_PARTIAL", "page": None,
                         "detail": "Europe PMC metadata unavailable; retraction checked against PMC OA only"})
    if citation["retracted"]:
        if not allow_retracted:
            raise RetractedError(f"{source} is marked retracted (PMC or Europe PMC); refusing to ingest it")
        warnings.append({"code": "RETRACTED", "page": None, "detail": "PMC or Europe PMC marks this article as retracted"})

    if oa:
        # the article PDF when PMC has one; otherwise PMC's plain-text rendering of the full article
        url, md5 = _s3_https(oa.get("pdf_url") or oa["text_url"])
        content = _get(client, url).content
        if md5 and hashlib.md5(content).hexdigest() != md5:
            raise FetchError(f"{url}: MD5 mismatch (expected {md5}); download corrupted")
        provenance = {"input": source, "resolved_url": url, "fetched_via": "pmc-oa-s3"}
        if oa.get("pdf_url"):
            doc = ingest_pdf_bytes(content, role, source_type=source_type, citation=citation, provenance=provenance)
            doc["warnings"] += warnings
            return doc
        return build_document(
            content=content, source_type=source_type, role=role, access="full_text", provenance=provenance,
            extraction_version=PMC_TEXT_VERSION, extractor={"name": "steer_pipeline.fetch", "version": PMC_TEXT_VERSION},
            pages=[{"page": 1, "width": None, "height": None, "layout": "single",
                    "lines": text_lines(content.decode("utf-8-sig"))}],
            citation=citation, warnings=warnings + [{
                "code": "NO_PAGE_LAYOUT", "page": None,
                "detail": "PMC plain-text rendering: no pages or bounding boxes; locate claims by sentence"}])

    if not epmc or not epmc.get("abstractText"):
        raise FetchError(f"{source}: not in the PMC open-access set and no abstract found in Europe PMC")
    text = html_to_text(epmc["abstractText"])
    # hash only what we ingest (title + abstract), not volatile fields like citation counts
    content = f"{citation['title'] or ''}\n\n{text}".encode()
    lines = text_lines(text)
    return build_document(
        content=content,
        source_type=source_type,
        role=role,
        access="abstract_only",
        provenance={"input": source, "resolved_url": f"{EUROPEPMC}?query={quote(str(ids.get('pmid') or ids.get('doi')))}",
                    "fetched_via": "europepmc-abstract"},
        extraction_version=ABSTRACT_VERSION,
        extractor={"name": "steer_pipeline.fetch", "version": ABSTRACT_VERSION},
        pages=[{"page": 1, "width": None, "height": None, "layout": "single", "lines": lines}],
        citation=citation,
        warnings=warnings + [{"code": "ABSTRACT_ONLY", "page": None,
                              "detail": "full text not open access; only the Europe PMC abstract was ingested"}],
    )


class _DoiMeta(HTMLParser):
    def __init__(self):
        super().__init__()
        self.doi = None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "meta" and (a.get("name") or "").lower() in ("citation_doi", "dc.identifier") and not self.doi:
            m = DOI_RE.search(a.get("content") or "")
            self.doi = m.group(1) if m else None


def _fetch_url(url: str, role: str, client: httpx.Client, allow_retracted: bool) -> dict:
    r = _get(client, url)
    ctype = r.headers.get("content-type", "").lower()
    if "pdf" in ctype or r.content[:5] == b"%PDF-":
        return ingest_pdf_bytes(r.content, role, source_type="url",
                                provenance={"input": url, "resolved_url": str(r.url), "fetched_via": "direct-pdf"})
    if "html" in ctype:
        meta = _DoiMeta()
        meta.feed(r.text)
        if meta.doi:
            return _fetch(meta.doi, role, client, allow_retracted)
    raise UnsupportedSource(f"{url}: not a PDF and no DOI meta tag found (content-type {ctype or 'unknown'})")
