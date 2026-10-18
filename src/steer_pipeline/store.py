"""SQLite storage: documents, every claim of every run (passed, needs check, rejected), and the run record.

One file (path in steer.toml, [storage] db). JSON columns hold the nested parts as written by the
pipeline, so nothing is lost between a run and its export.
"""

import json
import sqlite3
from pathlib import Path

SCHEMA_VERSION = 4
SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS documents (
    document_id TEXT PRIMARY KEY,
    source_id TEXT NOT NULL,
    role TEXT NOT NULL,
    extraction_version TEXT NOT NULL,
    citation TEXT NOT NULL,          -- JSON
    warnings TEXT NOT NULL,          -- JSON
    document TEXT NOT NULL,          -- the full contract document, JSON
    stored_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    input TEXT NOT NULL,
    document_id TEXT REFERENCES documents(document_id),
    settings_version INTEGER,
    rules_version TEXT,
    timings TEXT,                    -- JSON: seconds per step
    summary TEXT,                    -- JSON
    chunks TEXT,                     -- JSON: chunk sids, status, endpoint, usage
    model_calls TEXT,                -- JSON: every attempt (tokens only as reported, never credentials)
    label_result TEXT                -- JSON: unlabeled ids, failed batches
);
CREATE TABLE IF NOT EXISTS claims (
    run_id TEXT NOT NULL REFERENCES runs(run_id),
    claim_id TEXT NOT NULL,
    document_id TEXT NOT NULL REFERENCES documents(document_id),
    status TEXT NOT NULL CHECK (status IN ('passed', 'needs_check', 'rejected')),
    claim_text TEXT NOT NULL,
    quote TEXT NOT NULL,
    claim_type TEXT, population TEXT, hedged INTEGER, direction TEXT,
    outcome TEXT, comparison_group TEXT, time_point TEXT, limitations TEXT,
    sids TEXT NOT NULL,              -- JSON
    line_ids TEXT NOT NULL,          -- JSON
    from_table_text INTEGER,
    gate TEXT NOT NULL,              -- JSON: the gate result (reasons, warnings, matches)
    flags TEXT NOT NULL,             -- JSON: code flags
    review_reasons TEXT NOT NULL,    -- JSON
    theme TEXT,
    doubts TEXT,                     -- JSON
    rule_flags TEXT NOT NULL,        -- JSON: claim_library profile
    PRIMARY KEY (run_id, claim_id)
);
CREATE INDEX IF NOT EXISTS claims_by_status ON claims (status, document_id);
"""
# version 2 adds this table only (an add-only migration: version 1 files keep every row)
SCHEMA_V2 = """
CREATE TABLE IF NOT EXISTS crosschecks (
    run_id TEXT NOT NULL,
    claim_id TEXT NOT NULL,
    verdict TEXT NOT NULL,
    reason TEXT,
    endpoint TEXT, model TEXT, provider TEXT,
    independence TEXT,               -- different_provider | different_model_same_provider | same_model | NULL
    checked_at TEXT NOT NULL,
    PRIMARY KEY (run_id, claim_id, checked_at),
    FOREIGN KEY (run_id, claim_id) REFERENCES claims (run_id, claim_id)
);
"""
# version 3 adds these tables only (add-only again). review_action is append-only, enforced by the database: a
# reviewer's decision is never edited or deleted; statuses are derived from the latest action (studio.py).
REVIEW_ACTION = """CREATE TABLE IF NOT EXISTS review_action (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    at TEXT NOT NULL,
    reviewer TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('editor', 'clinician')),   -- resolved by the server from reviewers.toml
    target_kind TEXT NOT NULL CHECK (target_kind IN ('claim', 'post')),
    target TEXT NOT NULL,            -- '<run_id>/<claim_id>' or '<post_id>'
    stage TEXT NOT NULL CHECK (stage IN ('layout', 'check', 'editorial', 'clinical')),
    action TEXT NOT NULL CHECK (action IN ('layout_checked', 'layout_unresolved', 'check_cleared', 'editorial_approved',
                                           'editorial_rejected', 'clinically_approved', 'clinically_rejected')),
    clears TEXT,                     -- check_cleared: the review reason code this clearance clears
    reason TEXT,
    reason_category TEXT CHECK (reason_category IS NULL OR reason_category IN
                                ('inaccurate', 'overstated', 'missing context', 'tone/brand', 'other')),
    before_status TEXT,
    after_status TEXT
);
"""
SCHEMA_V3 = """
CREATE TABLE IF NOT EXISTS posts (
    post_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    folder TEXT NOT NULL,            -- out/<run_id>/<post_id>: post.json and trace.json are the files of record
    status TEXT NOT NULL,            -- the status the checks gave when the post was written
    written_at TEXT NOT NULL,
    record TEXT NOT NULL             -- post.json as written, JSON
);
{REVIEW_ACTION}
CREATE INDEX IF NOT EXISTS review_action_by_target ON review_action (target_kind, target, id);
CREATE TRIGGER IF NOT EXISTS review_action_no_update BEFORE UPDATE ON review_action
    BEGIN SELECT RAISE(ABORT, 'review_action is append-only'); END;
CREATE TRIGGER IF NOT EXISTS review_action_no_delete BEFORE DELETE ON review_action
    BEGIN SELECT RAISE(ABORT, 'review_action is append-only'); END;
""".replace("{REVIEW_ACTION}", REVIEW_ACTION)
STATUSES = {"passed": "passed", "needs_check": "needs_check", "rejected": "rejected"}


def _upgrade_review_action(con: sqlite3.Connection) -> None:
    """v3 -> v4: review_action gains the check_cleared action and the clears column. SQLite cannot change a CHECK
    constraint, so the table is rebuilt: every row is copied first, then the append-only triggers are restored."""
    row = con.execute("SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'review_action'").fetchone()
    if not row or "check_cleared" in row[0]:
        return
    cols = "id, at, reviewer, role, target_kind, target, stage, action, reason, reason_category, before_status, after_status"
    con.executescript(f"""
        BEGIN;
        DROP TRIGGER IF EXISTS review_action_no_update;
        DROP TRIGGER IF EXISTS review_action_no_delete;
        ALTER TABLE review_action RENAME TO review_action_v3;
        {REVIEW_ACTION}
        INSERT INTO review_action ({cols}) SELECT {cols} FROM review_action_v3;
        DROP TABLE review_action_v3;
        COMMIT;
    """)


def connect(path: str | Path, check_same_thread: bool = True) -> sqlite3.Connection:
    con = sqlite3.connect(str(path), check_same_thread=check_same_thread)
    con.execute("PRAGMA foreign_keys = ON")
    con.executescript(SCHEMA)
    con.execute("INSERT OR IGNORE INTO meta VALUES ('schema_version', '1')")
    con.executescript(SCHEMA_V2)
    _upgrade_review_action(con)
    con.executescript(SCHEMA_V3)
    with con:
        con.execute("UPDATE meta SET value = ? WHERE key = 'schema_version'", (str(SCHEMA_VERSION),))
    return con


def save_post(con: sqlite3.Connection, record: dict, folder: str | Path, written_at: str) -> None:
    """Register a written post (its folder holds the files of record)."""
    with con:
        con.execute("INSERT OR REPLACE INTO posts VALUES (?, ?, ?, ?, ?, ?)",
                    (record["post_id"], record["run_id"], str(folder), record["status"], written_at, _j(record)))


ACTION_COLUMNS = ("at", "reviewer", "role", "target_kind", "target", "stage", "action", "clears", "reason",
                  "reason_category", "before_status", "after_status")


def add_action(con: sqlite3.Connection, action: dict) -> int:
    """Append one reviewer action; returns its id. There is no way to change or remove one."""
    with con:
        cur = con.execute(f"INSERT INTO review_action ({', '.join(ACTION_COLUMNS)}) VALUES ({', '.join('?' * len(ACTION_COLUMNS))})",
                          tuple(action.get(k) for k in ACTION_COLUMNS))
    return cur.lastrowid


def actions(con: sqlite3.Connection) -> list[dict]:
    con.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in con.execute("SELECT * FROM review_action ORDER BY id")]
    finally:
        con.row_factory = None


def save_crosschecks(con: sqlite3.Connection, run_id: str, results: list[dict], checked_at: str) -> None:
    with con:
        for r in results:
            con.execute("INSERT INTO crosschecks VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (run_id, r["claim_id"], r["verdict"], r["reason"], r["endpoint"], r["model"], r["provider"],
                         r["independence"], checked_at))


def _j(value) -> str:
    return json.dumps(value, ensure_ascii=False)


def save_run(con: sqlite3.Connection, record: dict) -> None:
    """Store one pipeline run (see pipeline.run): its document, run row, every claim and every library
    cross-check result, in one transaction. There is no partial save."""
    doc = record["document"]
    with con:
        con.execute(
            "INSERT OR REPLACE INTO documents VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (doc["document_id"], doc["source"]["source_id"], doc["source"]["role"], doc["extraction_version"],
             _j(doc["citation"]), _j(doc["warnings"]), _j(doc), record["finished_at"]))
        con.execute(
            "INSERT INTO runs VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (record["run_id"], record["started_at"], record["finished_at"], record["input"], doc["document_id"],
             record["settings_version"], record["rules_version"], _j(record["timings"]), _j(record["summary"]),
             _j(record["chunks"]), _j(record["model_calls"]), _j(record["label_result"])))
        for status, claims in (("passed", record["passed"]), ("needs_check", record["needs_check"]),
                               ("rejected", record["rejected"])):
            for c in claims:
                matches = c["gate"]["matches"]
                con.execute(
                    "INSERT INTO claims VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (record["run_id"], c["claim_id"], doc["document_id"], status, c["claim_text"], c["quote"],
                     c.get("claim_type"), c.get("population"), int(bool(c.get("hedged"))), c.get("direction"),
                     c.get("outcome"), c.get("comparison_group"), c.get("time_point"), c.get("limitations"),
                     _j(sorted({s for m in matches for s in m["sids"]})),
                     _j([i for m in matches for i in m["line_ids"]]),
                     None if status == "rejected" else int(bool(c["gate"].get("from_table_text"))),
                     _j(c["gate"]), _j(c.get("flags", [])), _j(c.get("review_reasons", [])),
                     (c.get("labels") or {}).get("theme"), _j((c.get("labels") or {}).get("doubts", [])),
                     _j(c.get("rule_flags", []))))
        for r in record.get("crosschecks", []):  # the run's library cross-check, in the same transaction
            con.execute("INSERT INTO crosschecks VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (record["run_id"], r["claim_id"], r["verdict"], r["reason"], r["endpoint"], r["model"],
                         r["provider"], r["independence"], record["finished_at"]))


def export_run(con: sqlite3.Connection, run_id: str) -> dict:
    """The run row and all its claims as plain JSON (what was stored, nothing recomputed)."""
    con.row_factory = sqlite3.Row
    try:
        run = con.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
        if run is None:
            raise KeyError(f"no run {run_id!r}")
        json_cols = {"timings", "summary", "chunks", "model_calls", "label_result", "sids", "line_ids", "gate",
                     "flags", "review_reasons", "doubts", "rule_flags"}
        decode = lambda row: {k: (json.loads(row[k]) if k in json_cols and row[k] is not None else row[k])
                              for k in row.keys()}
        claims = [decode(r) for r in con.execute(
            "SELECT * FROM claims WHERE run_id = ? ORDER BY status, claim_id", (run_id,))]
        checks = [dict(r) for r in con.execute(
            "SELECT * FROM crosschecks WHERE run_id = ? ORDER BY claim_id, checked_at", (run_id,))]
        return {"run": decode(run), "claims": claims, "crosschecks": checks}
    finally:
        con.row_factory = None
