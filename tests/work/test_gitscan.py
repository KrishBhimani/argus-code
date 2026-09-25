"""git history is read with the local git CLI only, stored for every author."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from argus.work import gitscan
from argus.work.db import open_work_db


def git(cwd: Path, *args: str, date: str = "2026-09-01T10:00:00+05:30", **env) -> str:
    e = {**os.environ, "GIT_AUTHOR_DATE": date, "GIT_COMMITTER_DATE": date,
         "GIT_CONFIG_NOSYSTEM": "1", "HOME": str(cwd), **env}
    return subprocess.run(["git", *args], cwd=cwd, env=e, check=True,
                          capture_output=True, text=True).stdout


def make_repo(path: Path) -> Path:
    path.mkdir(parents=True)
    git(path, "init", "-q", "-b", "main")
    git(path, "config", "user.email", "me@example.com")
    git(path, "config", "user.name", "Me")
    (path / "a.py").write_text("print(1)\n")
    git(path, "add", ".")
    git(path, "commit", "-q", "-m", "feat: first")
    (path / "a.py").write_text("print(2)\nprint(3)\n")
    (path / "logo.png").write_bytes(b"\x89PNG\x00\x01")
    git(path, "add", ".")
    git(path, "commit", "-q", "-m", "fix: second\n\nCo-Authored-By: Claude Opus 5 <noreply@anthropic.com>",
        date="2026-09-02T09:30:00Z")
    git(path, "commit", "-q", "--allow-empty", "-m", "chore: teammate",
        date="2026-09-03T08:00:00Z", GIT_AUTHOR_EMAIL="mate@example.com", GIT_AUTHOR_NAME="Mate")
    return path


def test_parse_and_scan_store_every_author(tmp_path):
    repo = make_repo(tmp_path / "proj")
    conn = open_work_db(tmp_path / "data")
    root = gitscan.repo_root(str(repo))
    rid = gitscan.ensure_repo(conn, root)
    assert gitscan.scan_repo(conn, rid, root, "2026-09-05T00:00:00Z") == 3

    rows = {r["subject"]: r for r in conn.execute("SELECT * FROM commits")}
    assert rows["feat: first"]["authored_at"] == "2026-09-01T04:30:00Z"  # +05:30 → UTC
    assert rows["fix: second"]["agent_coauthored"] == 1
    assert rows["fix: second"]["added"] == 2 and rows["fix: second"]["files"] == 2
    assert rows["chore: teammate"]["author_email"] == "mate@example.com"
    binary = conn.execute("SELECT added, deleted FROM commit_files WHERE path='logo.png'").fetchone()
    assert (binary["added"], binary["deleted"]) == (None, None)
    repo_row = conn.execute("SELECT * FROM repos WHERE id=?", (rid,)).fetchone()
    assert "me@example.com" in repo_row["user_emails"] and repo_row["last_error"] is None


def test_rescan_is_idempotent(tmp_path):
    repo = make_repo(tmp_path / "proj")
    conn = open_work_db(tmp_path / "data")
    root = gitscan.repo_root(str(repo))
    rid = gitscan.ensure_repo(conn, root)
    gitscan.scan_repo(conn, rid, root, "2026-09-05T00:00:00Z")
    gitscan.scan_repo(conn, rid, root, "2026-09-06T00:00:00Z")
    assert conn.execute("SELECT COUNT(*) FROM commits").fetchone()[0] == 3


def test_repo_root_with_space_in_path(tmp_path):
    repo = make_repo(tmp_path / "XO Builders" / "proj")
    (repo / "sub dir").mkdir()
    assert gitscan.repo_root(str(repo / "sub dir")).endswith("XO Builders/proj")


def test_repo_root_of_missing_dir_is_none(tmp_path):
    assert gitscan.repo_root(str(tmp_path / "nope")) is None


def test_first_scan_timeout_falls_back_to_180_days(tmp_path, monkeypatch):
    repo = make_repo(tmp_path / "proj")
    conn = open_work_db(tmp_path / "data")
    root = gitscan.repo_root(str(repo))
    rid = gitscan.ensure_repo(conn, root)
    real = gitscan.run_git
    calls = []

    def flaky(args, cwd, timeout=60):
        calls.append(args)
        if args[0] == "log" and not any(a.startswith("--since=") for a in args):
            raise gitscan.GitError("timed out")
        return real(args, cwd, timeout)

    monkeypatch.setattr(gitscan, "run_git", flaky)
    gitscan.scan_repo(conn, rid, root, "2026-09-05T00:00:00Z")
    logs = [c for c in calls if c[0] == "log"]
    assert len(logs) == 2 and any(a.startswith("--since=") for a in logs[-1])
    assert conn.execute("SELECT COUNT(*) FROM commits").fetchone()[0] == 3
    row = conn.execute("SELECT last_error FROM repos WHERE id=?", (rid,)).fetchone()
    assert "180 days" in row["last_error"]


def test_rescan_picks_up_late_arriving_old_commits(tmp_path):
    """Review I-2: incremental scans used --since=<last scan - 2 days> on committer
    date, so commits that arrive later with an older date (a pull of work done on
    another machine, a fetched teammate branch) were never read."""
    repo = make_repo(tmp_path / "proj")
    conn = open_work_db(tmp_path / "data")
    root = gitscan.repo_root(str(repo))
    rid = gitscan.ensure_repo(conn, root)
    gitscan.scan_repo(conn, rid, root, "2026-09-05T00:00:00Z")
    (repo / "b.py").write_text("x = 1\n")
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "feat: done last month elsewhere", date="2026-08-01T10:00:00Z")
    gitscan.scan_repo(conn, rid, root, "2026-09-06T00:00:00Z")
    subjects = {r["subject"] for r in conn.execute("SELECT subject FROM commits")}
    assert "feat: done last month elsewhere" in subjects


def test_rescan_with_unchanged_refs_skips_git_log(tmp_path, monkeypatch):
    repo = make_repo(tmp_path / "proj")
    conn = open_work_db(tmp_path / "data")
    root = gitscan.repo_root(str(repo))
    rid = gitscan.ensure_repo(conn, root)
    gitscan.scan_repo(conn, rid, root, "2026-09-05T00:00:00Z")
    real, logs = gitscan.run_git, []
    monkeypatch.setattr(gitscan, "run_git", lambda args, cwd, timeout=60, **kw: (logs.append(args[0]), real(args, cwd, timeout, **kw))[1])
    assert gitscan.scan_repo(conn, rid, root, "2026-09-06T00:00:00Z") == 0
    assert "log" not in logs


SAME = "github.com/krishbhimani/argus-code"


@pytest.mark.parametrize("url", [
    "https://github.com/KrishBhimani/argus-code",
    "https://github.com/KrishBhimani/argus-code.git",
    "https://github.com/KrishBhimani/argus-code/",
    "git@github.com:KrishBhimani/argus-code.git",
    "ssh://git@github.com/KrishBhimani/argus-code.git",
    "https://someone:ghp_secret@github.com/KrishBhimani/argus-code.git",
])
def test_remote_urls_of_one_repo_normalize_alike_without_credentials(url):
    assert gitscan.normalize_remote(url) == SAME


def test_a_local_path_remote_is_not_an_identity():
    assert gitscan.normalize_remote("C:/code/other-clone") is None
    assert gitscan.normalize_remote("/home/me/other-clone") is None
    assert gitscan.normalize_remote("file:///home/me/other-clone") is None


def test_project_key_prefers_remote_then_first_commit_then_folder(tmp_path):
    a = make_repo(tmp_path / "a")
    git(a, "remote", "add", "origin", "https://github.com/Me/Proj.git")
    assert gitscan.project_key(str(a)) == "remote:github.com/me/proj"
    b = tmp_path / "b"
    git(tmp_path, "clone", "-q", str(a), str(b))
    git(b, "remote", "remove", "origin")
    first = git(a, "rev-list", "--max-parents=0", "HEAD").split()[0]
    assert gitscan.project_key(str(b)) == f"root:{first}"
    w = tmp_path / "wt"
    git(a, "worktree", "add", "-q", "-b", "side", str(w))
    assert gitscan.project_key(str(w)) == gitscan.project_key(str(a))
    e = tmp_path / "empty"
    e.mkdir()
    git(e, "init", "-q")
    assert gitscan.project_key(str(e)).startswith("path:")


def test_scan_records_the_project_key(tmp_path):
    a = make_repo(tmp_path / "a")
    git(a, "remote", "add", "origin", "git@github.com:Me/Proj.git")
    conn = open_work_db(tmp_path / "data")
    rid = gitscan.ensure_repo(conn, str(a).replace("\\", "/"))
    gitscan.scan_repo(conn, rid, str(a), "2026-09-05T00:00:00Z")
    assert conn.execute("SELECT project_key FROM repos WHERE id = ?", (rid,)).fetchone()[0] == "remote:github.com/me/proj"
