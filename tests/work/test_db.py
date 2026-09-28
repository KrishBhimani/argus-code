"""work.db is the trial's own file; argus.db is only ever attached read-only."""
from __future__ import annotations

import sqlite3

import pytest

from argus.store.db import open_db
from argus.work.db import get_meta, open_work_db, set_meta

TABLES = {"meta", "repos", "commits", "commit_files", "session_repo", "session_facts",
          "active_spans", "session_prs", "turn_attribution", "commit_claims",
          "session_commits", "file_offsets", "commit_reach"}


def _snapshot(path):
    c = sqlite3.connect(path)
    schema = c.execute("SELECT type, name, sql FROM sqlite_master ORDER BY name").fetchall()
    ver = c.execute("SELECT value FROM app_meta WHERE key='schema_version'").fetchone()
    c.close()
    return schema, ver


def test_creates_its_own_file_with_all_tables(tmp_path):
    conn = open_work_db(tmp_path)
    names = {r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert TABLES <= names
    assert (tmp_path / "work.db").exists()
    conn.close()


def test_reopen_is_idempotent(tmp_path):
    open_work_db(tmp_path).close()
    conn = open_work_db(tmp_path)
    set_meta(conn, "k", "v")
    assert get_meta(conn, "k") == "v"
    conn.close()


def test_argus_db_is_attached_read_only_and_never_changed(tmp_path):
    core = open_db(tmp_path / "argus.db")
    core.close()
    before = _snapshot(tmp_path / "argus.db")

    conn = open_work_db(tmp_path)
    assert conn.execute("SELECT COUNT(*) FROM core.sessions").fetchone()[0] == 0
    with pytest.raises(sqlite3.OperationalError, match="readonly"):
        conn.execute("INSERT INTO core.app_meta (key, value) VALUES ('x', 'y')")
    conn.close()

    assert _snapshot(tmp_path / "argus.db") == before


def test_works_without_argus_db(tmp_path):
    conn = open_work_db(tmp_path)
    attached = {r["name"] for r in conn.execute("PRAGMA database_list")}
    assert "core" not in attached
    conn.close()


def test_an_existing_work_db_gains_project_key_without_losing_rows(tmp_path):
    c = sqlite3.connect(tmp_path / "work.db")
    c.execute("""CREATE TABLE repos (id INTEGER PRIMARY KEY AUTOINCREMENT, root TEXT NOT NULL UNIQUE,
                 display_name TEXT NOT NULL, user_emails TEXT NOT NULL DEFAULT '[]', last_scanned_at TEXT,
                 last_error TEXT, present INTEGER NOT NULL DEFAULT 1, ref_tips TEXT)""")
    c.execute("INSERT INTO repos (root, display_name) VALUES ('/r', 'r')")
    c.commit()
    c.close()
    open_work_db(tmp_path).close()
    conn = open_work_db(tmp_path)   # twice: adding the column is idempotent
    assert tuple(conn.execute("SELECT root, project_key FROM repos").fetchone()) == ("/r", None)
    conn.close()


NEW_REPO_COLUMNS = ("default_ref", "has_remote", "reach_tips", "dirty", "dirty_checked_at", "remote_as_of")


def test_an_existing_work_db_gains_git_state_without_losing_rows(tmp_path):
    c = sqlite3.connect(tmp_path / "work.db")
    c.execute("""CREATE TABLE repos (id INTEGER PRIMARY KEY AUTOINCREMENT, root TEXT NOT NULL UNIQUE,
                 display_name TEXT NOT NULL, user_emails TEXT NOT NULL DEFAULT '[]', last_scanned_at TEXT,
                 last_error TEXT, present INTEGER NOT NULL DEFAULT 1, ref_tips TEXT)""")
    c.execute("INSERT INTO repos (root, display_name) VALUES ('/r', 'r')")
    c.commit()
    c.close()
    open_work_db(tmp_path).close()
    conn = open_work_db(tmp_path)   # twice: idempotent
    row = conn.execute(f"SELECT root, {', '.join(NEW_REPO_COLUMNS)} FROM repos").fetchone()
    assert tuple(row) == ("/r", None, None, None, None, None, None)
    conn.execute("INSERT INTO commit_reach (repo_id, sha, on_default, pushed) VALUES (1, 'abc', 1, NULL)")
    assert tuple(conn.execute("SELECT * FROM commit_reach").fetchone()) == (1, "abc", 1, None, None)
    conn.close()


def test_an_early_commit_reach_gains_reachable_without_losing_rows(tmp_path):
    open_work_db(tmp_path).close()
    c = sqlite3.connect(tmp_path / "work.db")
    c.execute("DROP TABLE commit_reach")
    c.execute("""CREATE TABLE commit_reach (repo_id INTEGER NOT NULL, sha TEXT NOT NULL,
                 on_default INTEGER NOT NULL, pushed INTEGER, PRIMARY KEY (repo_id, sha))""")
    c.execute("INSERT INTO commit_reach VALUES (1, 'abc', 1, 1)")
    c.commit()
    c.close()
    conn = open_work_db(tmp_path)
    assert tuple(conn.execute("SELECT sha, on_default, pushed, reachable FROM commit_reach").fetchone()) == ("abc", 1, 1, None)
    conn.close()
