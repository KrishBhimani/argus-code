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
    assert conn.execute("SELECT COUNT(*) FROM commit_reach").fetchone()[0] == 3
    assert conn.execute("SELECT default_ref FROM repos").fetchone()[0] == "refs/heads/main"


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


def test_archived_session_without_transcript_is_mapped_and_linked(tmp_path):
    """Review I-3: sessions whose transcript Claude Code already deleted exist only in
    argus.db. They were never mapped to a repo (mapping came only from transcripts), so
    the archive's history showed no cost/turns and got no links, contradicting spec §11."""
    from argus.store.repository import normalize_project_path

    repo = make_repo(tmp_path / "proj")
    data = tmp_path / "data"
    r = Repository(open_db(data / "argus.db"))
    sid = "claude_code:archived-0000"
    r.upsert_session(session_factory(sid, "2026-09-01T04:20:00Z", project_path=normalize_project_path(str(repo))))
    r.upsert_turn(turn_factory(f"{sid}:m1", sid, "2026-09-01T04:20:00Z"))   # 10 min before "feat: first"
    r.db.close()
    (tmp_path / ".claude" / "projects").mkdir(parents=True)                    # no transcripts at all
    run_pass(data, ClaudeCodeAdapter(tmp_path / ".claude"), now_iso="2026-09-05T00:00:00Z")
    conn = open_work_db(data)
    assert conn.execute("SELECT repo_id FROM session_repo WHERE session_id = ?", (sid,)).fetchone()[0] is not None
    link = conn.execute("SELECT evidence FROM session_commits WHERE session_id = ?", (sid,)).fetchone()
    assert link is not None and link[0] == "inferred"


def _archive_only(tmp_path, prompts):
    """A session Claude Code already deleted: argus.db has its turns and your prompts, no transcript."""
    from argus.schema.types import Prompt
    from argus.store.repository import normalize_project_path

    repo = make_repo(tmp_path / "proj")
    path = normalize_project_path(str(repo))
    data = tmp_path / "data"
    r = Repository(open_db(data / "argus.db"))
    sid = "claude_code:gone-0000"
    r.upsert_session(session_factory(sid, "2026-09-01T10:00:00Z", project_path=path))
    for i, ts in enumerate(("2026-09-01T10:00:00Z", "2026-09-01T10:02:00Z", "2026-09-01T10:30:00Z")):
        r.upsert_turn(turn_factory(f"{sid}:m{i}", sid, ts))
    r.insert_prompts([Prompt(timestamp_ms=ms, project_path=path, display=text, is_slash=slash) for ms, text, slash in prompts])
    r.db.close()
    (tmp_path / ".claude" / "projects").mkdir(parents=True)
    return data, ClaudeCodeAdapter(tmp_path / ".claude"), sid


START_MS = 1788256800000   # 2026-09-01T10:00:00Z


def test_archive_only_session_gets_estimated_time_and_a_first_prompt_title(tmp_path):
    data, adapter, sid = _archive_only(tmp_path, [
        (START_MS - 3_600_000, "an hour earlier, another session", 0),
        (START_MS - 20_000, "/clear", 1),
        (START_MS - 5_000, "fix the login bug\nand add a test", 0),
        (START_MS + 600_000, "later follow-up", 0),
    ])
    run_pass(data, adapter, now_iso="2026-09-05T00:00:00Z")
    run_pass(data, adapter, now_iso="2026-09-05T00:05:00Z")      # a second pass adds nothing twice
    conn = open_work_db(data)
    spans = conn.execute("SELECT kind, SUM(ms) FROM active_spans WHERE session_id = ? GROUP BY kind", (sid,)).fetchall()
    assert [tuple(s) for s in spans] == [("archive", 120_000 + 300_000)]   # 2 min, then 28 min capped at 5
    f = conn.execute("SELECT title, title_source FROM session_facts WHERE session_id = ?", (sid,)).fetchone()
    assert tuple(f) == ("fix the login bug", "prompt")


def test_no_prompt_near_the_start_leaves_the_session_untitled(tmp_path):
    data, adapter, sid = _archive_only(tmp_path, [(START_MS - 3_600_000, "an hour earlier", 0)])
    run_pass(data, adapter, now_iso="2026-09-05T00:00:00Z")
    conn = open_work_db(data)
    assert conn.execute("SELECT title FROM session_facts WHERE session_id = ?", (sid,)).fetchone() is None


def test_prompt_titles_skip_paste_and_image_placeholders(tmp_path):
    data, adapter, sid = _archive_only(tmp_path, [
        (START_MS - 2_000, "[Pasted text #1 +74 lines]", 0),
        (START_MS + 30_000, "[Image #1] the window shows 235m", 0),
    ])
    run_pass(data, adapter, now_iso="2026-09-05T00:00:00Z")
    conn = open_work_db(data)
    assert conn.execute("SELECT title FROM session_facts WHERE session_id = ?", (sid,)).fetchone()[0] == "the window shows 235m"


def test_the_scan_asks_github_only_through_maybe_refresh(tmp_path, monkeypatch):
    from argus.work import github
    repo = make_repo(tmp_path / "proj")
    data, adapter = _world(tmp_path, repo)
    asked: list = []
    monkeypatch.setattr(github, "maybe_refresh", lambda conn, now: asked.append(now))
    run_pass(data, adapter, now_iso="2026-09-05T00:00:00Z")
    assert asked == ["2026-09-05T00:00:00Z"]


def test_a_squash_that_landed_before_the_first_scan_is_found_in_that_scan(tmp_path):
    """Linking runs before the git-state read, so the session's commits are candidates at once."""
    repo = make_repo(tmp_path / "proj")
    bare = tmp_path / "remote.git"
    git(tmp_path, "init", "-q", "--bare", str(bare))
    git(repo, "remote", "add", "origin", str(bare))
    git(repo, "switch", "-q", "-c", "feat/x", "HEAD~2")                 # branch from "feat: first"
    (repo / "b.py").write_text("work\n")
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "feat: session work\n\nCo-Authored-By: Claude <noreply@anthropic.com>",
        date="2026-09-02T09:30:00Z")                                       # 10 min after the session's turn
    git(repo, "switch", "-q", "main")
    git(repo, "reset", "-q", "--hard", "HEAD~2")
    git(repo, "merge", "-q", "--squash", "feat/x")
    git(repo, "commit", "-q", "-m", "Session work, squashed", date="2026-09-03T10:00:00Z")
    git(repo, "push", "-q", "origin", "main", "feat/x")
    git(repo, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main")
    data, adapter = _world(tmp_path, repo)
    run_pass(data, adapter, now_iso="2026-09-05T00:00:00Z")
    conn = open_work_db(data)
    row = conn.execute("""SELECT r.landed FROM commits c JOIN commit_reach r USING (repo_id, sha)
                          WHERE c.subject = 'feat: session work'""").fetchone()
    assert row["landed"] == 1


def _edit_line(ts: str, cwd: Path, path: Path) -> str:
    return json.dumps({"type": "assistant", "sessionId": SID, "cwd": str(cwd), "timestamp": ts, "message": {
        "id": f"e-{ts}", "role": "assistant", "model": "claude-opus-5",
        "content": [{"type": "tool_use", "id": f"t-{ts}", "name": "Edit", "input": {"file_path": str(path)}}],
        "usage": {"input_tokens": 1, "output_tokens": 1}}}) + "\n"


def _transcript(tmp_path: Path) -> Path:
    return tmp_path / ".claude" / "projects" / "p" / f"{SID}.jsonl"


def _session_project(data: Path) -> str | None:
    conn = open_work_db(data)
    row = conn.execute("SELECT r.display_name FROM session_repo s LEFT JOIN repos r ON r.id = s.repo_id").fetchone()
    conn.close()
    return row[0]


def test_a_session_started_outside_any_repo_joins_the_repo_whose_files_it_edited(tmp_path):
    """Regression: 15 of 81 real sessions were started in a plain parent folder and were
    missing from Work, though the files they edited were inside a repo."""
    parent = tmp_path / "space"
    repo = make_repo(parent / "proj")
    data, adapter = _world(tmp_path, parent)
    run_pass(data, adapter, now_iso="2026-09-05T00:00:00Z")
    assert _session_project(data) is None                       # nothing edited yet: still nowhere
    with _transcript(tmp_path).open("a", encoding="utf-8") as fh:
        fh.write(_edit_line("2026-09-02T09:21:00Z", parent, repo / "a.py"))
    run_pass(data, adapter, now_iso="2026-09-05T00:00:00Z")
    assert _session_project(data) == "proj"


def test_a_session_belongs_to_the_repo_where_it_edited_most(tmp_path):
    here, there = make_repo(tmp_path / "here"), make_repo(tmp_path / "there")
    data, adapter = _world(tmp_path, here)
    with _transcript(tmp_path).open("a", encoding="utf-8") as fh:
        fh.write(_edit_line("2026-09-02T09:21:00Z", here, here / "a.py"))
        fh.write(_edit_line("2026-09-02T09:22:00Z", here, there / "a.py"))
        fh.write(_edit_line("2026-09-02T09:23:00Z", here, there / "b.py"))
    run_pass(data, adapter, now_iso="2026-09-05T00:00:00Z")
    assert _session_project(data) == "there"
    conn = open_work_db(data)
    assert conn.execute("SELECT git_branch FROM session_repo").fetchone()[0] is None   # "main" was the other repo's branch
    conn.close()
    run_pass(data, adapter, now_iso="2026-09-05T00:00:00Z")      # a pass with nothing new keeps the answer
    assert _session_project(data) == "there"


def test_a_tie_or_edits_outside_every_repo_leave_the_session_where_it_ran(tmp_path):
    here, there = make_repo(tmp_path / "here"), make_repo(tmp_path / "there")
    data, adapter = _world(tmp_path, here)
    with _transcript(tmp_path).open("a", encoding="utf-8") as fh:
        fh.write(_edit_line("2026-09-02T09:21:00Z", here, here / "a.py"))
        fh.write(_edit_line("2026-09-02T09:22:00Z", here, there / "a.py"))
        fh.write(_edit_line("2026-09-02T09:23:00Z", here, tmp_path / "scratch" / "notes.md"))
    run_pass(data, adapter, now_iso="2026-09-05T00:00:00Z")
    assert _session_project(data) == "here"
    conn = open_work_db(data)
    assert conn.execute("SELECT git_branch FROM session_repo").fetchone()[0] == "main"
    conn.close()


def test_a_hand_made_commit_of_edited_files_is_linked_by_files(tmp_path):
    repo = make_repo(tmp_path / "proj")
    data, adapter = _world(tmp_path, repo)
    with _transcript(tmp_path).open("a", encoding="utf-8") as fh:
        fh.write(_edit_line("2026-09-02T10:00:00Z", repo, repo / "a.py"))   # after "fix: second" already took a.py
    (repo / "a.py").write_text("print(4)\n")
    git(repo, "commit", "-q", "-am", "feat: by hand, next day", date="2026-09-03T15:00:00Z")
    res = run_pass(data, adapter, now_iso="2026-09-05T00:00:00Z")
    conn = open_work_db(data)
    by_subject = {r["subject"]: r["evidence"] for r in conn.execute(
        "SELECT c.subject, sc.evidence FROM session_commits sc JOIN commits c ON c.sha = sc.sha")}
    conn.close()
    assert by_subject["feat: by hand, next day"] == "files"
    assert res.links["files"] >= 1


def test_a_projects_worktrees_count_together_when_choosing_where_a_session_belongs(tmp_path):
    """Seen on real data: 11 files across a repo's checkout and worktree lost to 34 in another
    repo, rightly; but 4 files split 2 + 2 must not lose to 3 files in another repo."""
    here, there = make_repo(tmp_path / "here"), make_repo(tmp_path / "there")
    git(there, "config", "remote.origin.url", "https://github.com/o/there.git")   # a different project
    wt = tmp_path / "here-wt"
    git(here, "worktree", "add", "-q", str(wt), "-b", "wt")
    data, adapter = _world(tmp_path, here)
    with _transcript(tmp_path).open("a", encoding="utf-8") as fh:
        for i, f in enumerate((here / "a.py", here / "b.py", wt / "a.py", wt / "b.py",
                               there / "a.py", there / "b.py", there / "c.py")):
            fh.write(_edit_line(f"2026-09-02T09:2{i}:00Z", here, f))
    run_pass(data, adapter, now_iso="2026-09-05T00:00:00Z")
    assert _session_project(data) == "here"    # its project has 4 files to the other's 3; the starting folder takes the tie
