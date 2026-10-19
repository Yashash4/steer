import json
from pathlib import Path

import pytest

from steer_pipeline import sitepages

FIX = Path(__file__).parent / "fixtures" / "sitepages"


def test_learned_page_renders_only_records_and_no_internal_references(tmp_path):
    page = sitepages.learned_page(FIX, tmp_path).read_text()
    cards = json.loads((FIX / "lessons.json").read_text())
    framing = next(c for c in cards if c["id"] == "framing-attribute")
    assert framing["failed"]["text"] in page.replace("&#x27;", "'") and "FRAMING_ADDS_ATTRIBUTE" in page
    assert sitepages.DISCLAIMER in page and "re-checked with today" in page and sitepages.LEARNED_INTRO in page
    assert 'url("ps1/fonts/Fraunces.woff2")' in page and "https://" not in page   # the site's own fonts, no network
    assert (tmp_path / "learned" / "pending-tick-before.jpg").exists()
    assert "trials/" not in page and "/home/" not in page


def test_a_full_hash_or_internal_path_fails_the_build(tmp_path):
    bad = json.loads((FIX / "lessons.json").read_text())
    bad[0]["rule"]["subject"] = "see trials/s7-reel-6"
    (tmp_path / "r").mkdir()
    (tmp_path / "r" / "lessons.json").write_text(json.dumps(bad))
    for f in FIX.glob("*.jpg"):
        (tmp_path / "r" / f.name).write_bytes(f.read_bytes())
    with pytest.raises(ValueError):
        sitepages.learned_page(tmp_path / "r", tmp_path / "o")


import re

from steer_pipeline import studio

POSTS = Path(__file__).parent / "fixtures" / "site_posts"
STATUS = "verified; CONTEXT_NOT_IN_QUOTE cleared by Test editor (editor)"


def posts_html(monkeypatch, **kw):
    snap = {"papers": {"PMC1": {"short": "Narayan 2021"}},
            "claims": [{"key": "PMC1/c004-01", "status": STATUS}],
            "posts": [{"id": "post-aaaa0001", "paper": "PMC1", "format": "carousel", "has_reel": True, "folder": str(POSTS / "post-aaaa0001"),
                       "run_id": "run-1", "pngs": ["files/post-aaaa0001/slide-01.png"],
                       "lines": [{"kind": "health", "claim_ids": ["c004-01"]}]}]}
    monkeypatch.setattr(studio, "public_data", lambda db, dirs, rev: snap)
    return sitepages.posts_page("x.db", [POSTS / "post-aaaa0001"], "r.toml", ["post-aaaa0001"], blocked_dir=POSTS / "post-bbbb0002", **kw)


def test_every_health_line_has_its_records(monkeypatch):
    h = posts_html(monkeypatch, notes={"post-aaaa0001": "brief targeted by the learning loop"})
    assert h.count("<details") == 2 and "Normal BMI?" not in h.split("Why this claim")[1].split("A post the checks blocked")[0]
    assert 'href="../studio/ledger.html#claim-PMC1-c004-01"' in h and "BMI &lt;25 kg/m2 was 5.3 times higher (IRR 5.27)" in h
    assert "page 5" in h and STATUS in h and "supported; model glm-5.3; different provider" in h and "pending doctor review" in h
    assert "slide 2, caption" in h                      # the same line on two surfaces is one block
    assert "Narayan 2021 — carousel + reel (v5), brief targeted by the learning loop" in h
    assert sitepages.DISCLAIMER in h and 'url("../ps1/fonts/Fraunces.woff2")' in h and 'href="../index.html"' in h


def test_the_blocked_example_and_a_clean_posts_html(monkeypatch):
    h = posts_html(monkeypatch)
    assert "NUMBER_NOT_IN_CLAIM" in h and "never rendered and never published" in h and "post-bbbb0002" in h
    assert not re.search(r"trials/|/home|/tmp|<script|https?://", h)
