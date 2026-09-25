"""One pass: transcript facts, then git, then links; failures are recorded, never raised."""
from __future__ import annotations

import json
import os
import shutil
import stat
import threading
from pathlib import Path

from typer.testing import CliRunner

from argus.adapters.claude_code.adapter import ClaudeCodeAdapter
from argus.store.db import open_db
from argus.store.repository import Repository
from argus.work import gitscan
from argus.work.collector import WORK_THREAD_NAME, WorkCollector, run_pass
from argus.work.db import open_work_db
from tests.conftest import session_factory, turn_factory
from tests.work.test_gitscan import git, make_repo

SID = "99999999-2222-3333-4444-555555555555"


def _world(tmp_path: Path, cwd: Path) -> tuple[Path, ClaudeCodeAdapter]:
    data = tmp_path / "data"
    r = Repository(open_db(data / "argus.db"))
    r.upsert_session(session_factory(f"claude_code:{SID}", "2026-09-02T09:00:00Z"))
    r.upsert_turn(turn_factory(f"claude_code:{SID}:m1", f"claude_code:{SID}", "2026-09-02T09:20:00Z"))
    r.db.close()
    proj = tmp_path / ".claude" / "projects" / "p"
    proj.mkdir(parents=True)
    line = {"type": "assistant", "sessionId": SID, "cwd": str(cwd), "gitBranch": "main",
            "timestamp": "2026-09-02T09:20:00Z",
            "message": {"id": "m1", "role": "assistant", "model": "claude-opus-5", "content": [],
                        "usage": {"input_tokens": 1, "output_tokens": 1}}}
    (proj / f"{SID}.jsonl").write_text(json.dumps(line) + "\n", encoding="utf-8")
    return data, ClaudeCodeAdapter(tmp_path / ".claude")


def test_end_to_end_pass_links_the_coauthored_commit(tmp_path):
    repo = make_repo(tmp_path / "proj")
    data, adapter = _world(tmp_path, repo)
    res = run_pass(data, adapter, now_iso="2026-09-05T00:00:00Z")
    assert res.repos == 1 and res.commits == 3 and res.errors == {}
    conn = open_work_db(data)
    links = {r["evidence"] for r in conn.execute("SELECT evidence FROM session_commits")}
    assert links == {"coauthored"}   # "fix: second" 09:30Z, 10 min after the 09:20Z turn


def test_session_cwd_in_subdir_maps_to_repo(tmp_path):
    repo = make_repo(tmp_path / "proj")
    (repo / "pkg dir").mkdir()
    data, adapter = _world(tmp_path, repo / "pkg dir")
    run_pass(data, adapter, now_iso="2026-09-05T00:00:00Z")
    conn = open_work_db(data)
    assert conn.execute("SELECT repo_id FROM session_repo").fetchone()[0] is not None


def test_pass_survives_git_missing(tmp_path, monkeypatch):
    repo = make_repo(tmp_path / "proj")
    data, adapter = _world(tmp_path, repo)
    monkeypatch.setattr(gitscan, "run_git", lambda *a, **k: (_ for _ in ()).throw(gitscan.GitError("git not found")))
    res = run_pass(data, adapter, now_iso="2026-09-05T00:00:00Z")
    assert res.commits == 0   # repo_root fails -> nothing mapped, no exception


def _rmtree(path: Path) -> None:
    """git marks object files read-only; Windows refuses to delete those without a chmod."""
    def retry(func, p, _exc):
        os.chmod(p, stat.S_IWRITE)
        func(p)
    shutil.rmtree(path, onerror=retry)
    assert not path.exists()


def test_deleted_repo_keeps_history(tmp_path):
    repo = make_repo(tmp_path / "proj")
    data, adapter = _world(tmp_path, repo)
    run_pass(data, adapter, now_iso="2026-09-05T00:00:00Z")
    _rmtree(repo)
    res = run_pass(data, adapter, now_iso="2026-09-06T00:00:00Z")
    conn = open_work_db(data)
    assert conn.execute("SELECT present FROM repos").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM commits").fetchone()[0] == 3
    assert conn.execute("SELECT COUNT(*) FROM session_commits").fetchone()[0] == 1
    assert res.errors == {}


def test_thread_starts_and_stops(tmp_path):
    make_repo(tmp_path / "proj")
    data, adapter = _world(tmp_path, tmp_path / "proj")
    c = WorkCollector(data, adapter, interval=3600).start()
    assert any(t.name == WORK_THREAD_NAME for t in threading.enumerate())
    assert c.stop(timeout=30) is True


def test_cli_work_scan_and_status(tmp_path):
    from argus.cli import app

    repo = make_repo(tmp_path / "proj")
    data, _ = _world(tmp_path, repo)
    r = CliRunner().invoke(app, ["work", "scan", "--data-dir", str(data),
                                 "--claude-root", str(tmp_path / ".claude")])
    assert r.exit_code == 0 and "1 repo" in r.output
    r = CliRunner().invoke(app, ["work", "status", "--data-dir", str(data)])
    assert r.exit_code == 0 and "proj" in r.output
