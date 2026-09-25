"""work.db is the trial's own file; argus.db is only ever attached read-only."""
from __future__ import annotations

import sqlite3

import pytest

from argus.store.db import open_db
from argus.work.db import get_meta, open_work_db, set_meta

TABLES = {"meta", "repos", "commits", "commit_files", "session_repo", "session_facts",
          "active_spans", "session_prs", "turn_attribution", "commit_claims",
          "session_commits", "file_offsets"}


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
