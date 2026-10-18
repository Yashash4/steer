import sqlite3

import pytest

from steer_pipeline import store


def test_review_actions_are_append_only_by_the_database(tmp_path):
    con = store.connect(tmp_path / "s.db")
    assert con.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone() == ("3",)
    a = {"at": "2026-10-18T13:30:00", "reviewer": "Ed", "role": "editor", "target_kind": "claim", "target": "r1/c1",
         "stage": "layout", "action": "layout_checked", "reason": None, "reason_category": None,
         "before_status": "needs check (LAYOUT_CHECK_NEEDED)", "after_status": "layout checked"}
    i = store.add_action(con, a)
    assert store.actions(con)[0]["id"] == i
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        con.execute("UPDATE review_action SET action = 'layout_unresolved' WHERE id = ?", (i,))
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        con.execute("DELETE FROM review_action WHERE id = ?", (i,))
    with pytest.raises(sqlite3.IntegrityError):                       # the vocabulary is fixed in the schema
        store.add_action(con, a | {"role": "admin"})
    assert len(store.actions(con)) == 1


def test_a_v2_file_upgrades_without_losing_rows(tmp_path):
    con = store.connect(tmp_path / "s.db")
    con.execute("INSERT INTO meta VALUES ('probe', 'kept')")
    con.commit()
    con.close()
    con = store.connect(tmp_path / "s.db")
    assert con.execute("SELECT value FROM meta WHERE key = 'probe'").fetchone() == ("kept",)
