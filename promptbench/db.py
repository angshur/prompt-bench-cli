"""SQLite run log. One row per (prompt, model, case, repetition). Analysis lives in queries.sql."""
import os
import sqlite3

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.environ.get("PROMPTBENCH_DB", os.path.join(HERE, "evals.db"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS batches (
  batch_id   TEXT PRIMARY KEY,
  created_at TEXT NOT NULL,
  label      TEXT,
  git_sha    TEXT,
  reps       INTEGER,
  note       TEXT
);
CREATE TABLE IF NOT EXISTS runs (
  run_id      INTEGER PRIMARY KEY AUTOINCREMENT,
  batch_id    TEXT NOT NULL REFERENCES batches(batch_id),
  prompt_name TEXT NOT NULL,
  prompt_hash TEXT NOT NULL,
  model       TEXT NOT NULL,
  case_id     TEXT NOT NULL,
  rep         INTEGER NOT NULL,
  output      TEXT,
  error       TEXT,
  latency_ms  INTEGER,
  tokens_in   INTEGER,
  tokens_out  INTEGER,
  cost_usd    REAL,
  passed      INTEGER,
  score       REAL,
  ungrounded  INTEGER,
  checks_json TEXT,
  judge_score REAL,
  judge_reason TEXT
);
CREATE INDEX IF NOT EXISTS idx_runs_batch ON runs(batch_id);
CREATE INDEX IF NOT EXISTS idx_runs_pm ON runs(prompt_name, model);
CREATE VIEW IF NOT EXISTS run_checks AS
SELECT r.run_id, r.batch_id, r.prompt_name, r.model, r.case_id, r.rep, j.key AS check_name, j.value AS check_result
FROM runs r, json_each(r.checks_json) j
WHERE j.value IS NOT NULL;
"""


def connect(path=None):
    con = sqlite3.connect(path or DB_PATH)
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
    return con
