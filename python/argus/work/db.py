"""The trial's own SQLite file. argus.db is attached read-only as ``core``."""
from __future__ import annotations

import sqlite3
from pathlib import Path

WORK_DB_NAME = "work.db"
WORK_SCHEMA_VERSION = "1"

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS repos (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  root TEXT NOT NULL UNIQUE,
  display_name TEXT NOT NULL,
  user_emails TEXT NOT NULL DEFAULT '[]',
  last_scanned_at TEXT,
  last_error TEXT,
  present INTEGER NOT NULL DEFAULT 1,
  ref_tips TEXT,
  project_key TEXT
);
CREATE TABLE IF NOT EXISTS commits (
  repo_id INTEGER NOT NULL, sha TEXT NOT NULL,
  author_name TEXT NOT NULL, author_email TEXT NOT NULL,
  authored_at TEXT NOT NULL, committed_at TEXT NOT NULL,
  subject TEXT NOT NULL, parents INTEGER NOT NULL,
  agent_coauthored INTEGER NOT NULL DEFAULT 0,
  added INTEGER NOT NULL DEFAULT 0, deleted INTEGER NOT NULL DEFAULT 0, files INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (repo_id, sha)
);
CREATE INDEX IF NOT EXISTS idx_commits_time ON commits(repo_id, authored_at);
CREATE TABLE IF NOT EXISTS commit_files (
  repo_id INTEGER NOT NULL, sha TEXT NOT NULL, path TEXT NOT NULL,
  added INTEGER, deleted INTEGER,
  PRIMARY KEY (repo_id, sha, path)
);
CREATE TABLE IF NOT EXISTS session_repo (
  session_id TEXT PRIMARY KEY, repo_id INTEGER, cwd TEXT, git_branch TEXT
);
CREATE TABLE IF NOT EXISTS session_facts (
  session_id TEXT PRIMARY KEY, title TEXT, title_source TEXT, last_line_ts TEXT
);
CREATE TABLE IF NOT EXISTS active_spans (
  session_id TEXT NOT NULL, ts TEXT NOT NULL, ms INTEGER NOT NULL, kind TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_active_spans ON active_spans(session_id, ts);
CREATE TABLE IF NOT EXISTS session_prs (
  session_id TEXT NOT NULL, pr_url TEXT NOT NULL, pr_number INTEGER, pr_repository TEXT, seen_at TEXT,
  PRIMARY KEY (session_id, pr_url)
);
CREATE TABLE IF NOT EXISTS turn_attribution (
  turn_id TEXT PRIMARY KEY, skill TEXT, mcp_server TEXT, mcp_tool TEXT, plugin TEXT
);
CREATE TABLE IF NOT EXISTS commit_claims (
  session_id TEXT NOT NULL, short_sha TEXT NOT NULL, seen_at TEXT,
  PRIMARY KEY (session_id, short_sha)
);
CREATE TABLE IF NOT EXISTS session_commits (
  session_id TEXT NOT NULL, repo_id INTEGER NOT NULL, sha TEXT NOT NULL, evidence TEXT NOT NULL,
  PRIMARY KEY (session_id, repo_id, sha)
);
CREATE TABLE IF NOT EXISTS file_offsets (path TEXT PRIMARY KEY, byte_offset INTEGER NOT NULL);
"""


def open_work_db(data_dir: Path) -> sqlite3.Connection:
    data_dir = Path(data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(
        (data_dir / WORK_DB_NAME).resolve().as_uri(), uri=True,
        check_same_thread=False, isolation_level=None,
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 5000")
    conn.executescript(SCHEMA)
    # Columns added after the first trial build; a work.db made before them gains them here.
    for table, column in (("repos", "project_key TEXT"),):
        name = column.split()[0]
        if not conn.execute("SELECT 1 FROM pragma_table_info(?) WHERE name = ?", (table, name)).fetchone():
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column}")
    conn.execute(
        "INSERT OR IGNORE INTO meta (key, value) VALUES ('work_schema_version', ?)",
        (WORK_SCHEMA_VERSION,),
    )
    core = data_dir / "argus.db"
    if core.exists():
        conn.execute("ATTACH DATABASE ? AS core", (core.resolve().as_uri() + "?mode=ro",))
    return conn


def get_meta(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return None if row is None else row["value"]


def set_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", (key, value))
