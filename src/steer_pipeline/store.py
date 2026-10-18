"""SQLite storage: documents, every claim of every run (passed, needs check, rejected), and the run record.

One file (path in steer.toml, [storage] db). JSON columns hold the nested parts as written by the
pipeline, so nothing is lost between a run and its export.
"""

import json
import sqlite3
from pathlib import Path

SCHEMA_VERSION = 1
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
STATUSES = {"passed": "passed", "needs_check": "needs_check", "rejected": "rejected"}


def connect(path: str | Path) -> sqlite3.Connection:
    con = sqlite3.connect(str(path))
    con.execute("PRAGMA foreign_keys = ON")
    con.executescript(SCHEMA)
    con.execute("INSERT OR IGNORE INTO meta VALUES ('schema_version', ?)", (str(SCHEMA_VERSION),))
    return con


def _j(value) -> str:
    return json.dumps(value, ensure_ascii=False)


def save_run(con: sqlite3.Connection, record: dict) -> None:
    """Store one pipeline run (see pipeline.run): its document, run row and every claim, in one transaction."""
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
        return {"run": decode(run), "claims": claims}
    finally:
        con.row_factory = None
