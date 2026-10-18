import json

import jsonschema
import pytest

from steer_pipeline.contract import SCHEMA_PATH


@pytest.fixture(scope="session")
def validate():
    schema = json.loads(SCHEMA_PATH.read_text())
    validator = jsonschema.Draft202012Validator(schema, format_checker=jsonschema.FormatChecker())

    def check(doc):
        validator.validate(json.loads(json.dumps(doc)))  # round-trip: must be plain JSON
    return check


def check_map(doc):
    """Every copy segment's text equals the raw text it points at; segments tile the text."""
    pos = 0
    for ts, te, rs, re_, op in doc["map"]:
        assert ts == pos, (ts, pos)
        if op == "copy":
            assert doc["text"][ts:te] == doc["raw_text"][rs:re_]
        pos = te
    assert pos == len(doc["text"])
