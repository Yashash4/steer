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
