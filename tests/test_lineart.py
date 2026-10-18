import json
from pathlib import Path

LA = Path(__file__).resolve().parents[1] / "lineart"
FOOD = {"chai", "roti_stack", "thali", "family_table"}
DRAWINGS = sorted(p for p in LA.glob("*.json") if p.name != "index.json")


def test_index_lists_every_drawing():
    index = json.loads((LA / "index.json").read_text())
    assert sorted(e["file"] for e in index) == [p.name for p in DRAWINGS]
    assert all(e["file"] == e["id"] + ".json" and e["topic_tags"] for e in index)
    assert {e["id"] for e in index if e["food"]} == FOOD


def test_drawings_valid_and_in_box():
    for p in DRAWINGS:
        d = json.loads(p.read_text())
        assert d["box"] == 1000 and d["paths"], p.name
        for path in d["paths"]:
            assert len(path) >= 2, p.name
            assert all(0 <= x <= 1000 and 0 <= y <= 1000 for x, y in path), p.name
