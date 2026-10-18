from steer_pipeline.assemble import assemble, join_op, text_to_raw


def lines(*raws, breaks=()):
    return [{"line_id": f"p0001-l{i + 1:04d}", "page": 1, "raw": r, "para_break": i in breaks} for i, r in enumerate(raws)]


def test_join_rules():
    assert join_op("weight manage-", "ment works") == "dehyphenate"
    assert join_op("GLP-", "1RA users") == "keep_hyphen"
    assert join_op("anti-", "Inflammatory") == "keep_hyphen"
    assert join_op("the end", "next line") == "join_space"
    assert join_op("ends with -", "next") == "join_space"  # no letter before the hyphen
    assert join_op("soft­hy­", "phen") == "dehyphenate"


def test_assemble_text_and_map():
    a = assemble(lines("Weight manage-", "ment after GLP-", "1RA stops.", "New para here.", breaks={3}))
    assert a["text"] == "Weight management after GLP-1RA stops.\nNew para here."
    assert a["raw_text"] == "Weight manage-\nment after GLP-\n1RA stops.\nNew para here."
    ops = [s[4] for s in a["map"]]
    assert ops == ["copy", "dehyphenate", "copy", "keep_hyphen", "copy", "paragraph", "copy"]
    for ts, te, rs, re_, op in a["map"]:
        if op == "copy":
            assert a["text"][ts:te] == a["raw_text"][rs:re_]
    # "management" starts at text 7 and maps into the raw first line
    assert a["raw_text"][text_to_raw(a["map"], 7):].startswith("manage-")
    # "1RA" maps to the start of raw line 3
    i = a["text"].index("1RA")
    assert text_to_raw(a["map"], i) == a["raw_text"].index("1RA")


def test_whitespace_trimmed_inside_lines_keeps_mapping():
    a = assemble(lines("  indented line  ", "next"))
    assert a["text"] == "indented line next"
    seg = a["map"][0]
    assert a["raw_text"][seg[2]:seg[3]] == "indented line"
