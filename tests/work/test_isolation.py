"""HARD RULE: the trial never changes argus.db."""
from __future__ import annotations

import sqlite3

from argus.work.collector import run_pass
from tests.work.test_collector import _world as collector_world
from tests.work.test_gitscan import make_repo


def _fingerprint(path):
    c = sqlite3.connect(path)
    schema = c.execute("SELECT type, name, sql FROM sqlite_master ORDER BY name").fetchall()
    counts = {n: c.execute(f"SELECT COUNT(*) FROM {n}").fetchone()[0]
              for (t, n, _) in schema if t == "table" and not n.startswith("sqlite_") and "_fts" not in n}
    ver = c.execute("SELECT value FROM app_meta WHERE key = 'schema_version'").fetchone()
    c.close()
    return schema, counts, ver


def test_full_pass_leaves_argus_db_unchanged(tmp_path):
    repo = make_repo(tmp_path / "proj")
    data, adapter = collector_world(tmp_path, repo)
    before = _fingerprint(data / "argus.db")
    run_pass(data, adapter, now_iso="2026-09-05T00:00:00Z")
    run_pass(data, adapter, now_iso="2026-09-06T00:00:00Z")
    assert _fingerprint(data / "argus.db") == before
    assert (data / "work.db").exists()
