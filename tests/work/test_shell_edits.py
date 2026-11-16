"""Files a session changed through the shell: Claude Code records them on the command's result."""
from __future__ import annotations

import json
from pathlib import Path

from argus.adapters.claude_code.adapter import ClaudeCodeAdapter
from argus.store.db import open_db
from argus.store.repository import Repository, normalize_project_path
from argus.work import queries
from argus.work.collector import run_pass
from argus.work.db import open_work_db
from argus.work.edits import collect_edits
from argus.work.linker import link_repo
from argus.work.stories import SessionSummary, group_stories
from tests.conftest import session_factory, turn_factory
from tests.work.test_gitscan import git, make_repo

SID = "11111111-2222-3333-4444-555555555555"
KEY = f"claude_code:{SID}"
n = normalize_project_path


def _shell(ts: str, changed, *, cwd: str = "C:\\proj", **diff) -> str:
    """The user line carrying a Bash result whose `bashEditDiff` lists the files it changed."""
    d = {"files": [], "moreFiles": 0, **diff}
    if changed is not None:
        d["changedFiles"] = changed
    return json.dumps({"sessionId": SID, "cwd": cwd, "type": "user", "timestamp": ts,
                       "toolUseResult": {"stdout": "", "stderr": "", "interrupted": False, "bashEditDiff": d},
                       "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": f"t-{ts}", "content": ""}]}}) + "\n"


def _edit(ts: str, path: str, cwd: str = "C:\\proj") -> str:
    return json.dumps({"sessionId": SID, "cwd": cwd, "type": "assistant", "timestamp": ts, "message": {
        "id": f"m-{ts}", "role": "assistant", "model": "claude-opus-5", "usage": {"input_tokens": 1, "output_tokens": 1},
        "content": [{"type": "tool_use", "id": f"e-{ts}", "name": "Edit", "input": {"file_path": path}}]}}) + "\n"


def _setup(tmp_path: Path, lines: list[str]) -> tuple[Path, ClaudeCodeAdapter]:
    proj = tmp_path / ".claude" / "projects" / "C--proj"
    proj.mkdir(parents=True)
    f = proj / f"{SID}.jsonl"
    f.write_text("".join(lines), encoding="utf-8")
    return f, ClaudeCodeAdapter(tmp_path / ".claude")


def _shell_rows(conn) -> dict[str, tuple]:
    return {r["path"]: (r["session_id"], r["changes"], r["first_ts"], r["last_ts"])
            for r in conn.execute("SELECT * FROM session_shell_files")}


def test_files_a_shell_command_changed_are_recorded(tmp_path):
    _, adapter = _setup(tmp_path, [
        _shell("2026-09-01T10:00:00Z", ["C:\\proj\\src\\a.py", "C:\\proj\\dist\\app.js"]),
        _shell("2026-09-01T10:05:00Z", ["C:\\proj\\src\\a.py"]),
        _shell("2026-09-01T10:06:00Z", None, shared=True),                     # nothing listed: no claim
        _shell("2026-09-01T10:07:00Z", [], unavailable=True),
        _shell("2026-09-01T10:08:00Z", ["rel.txt"], cwd="C:\\proj\\pkg"),      # relative: read against the line's folder
        _shell("2026-09-01T10:09:00Z", ["C:\\proj\\x.py", 7, None, ""]),       # junk entries are skipped
        _shell("2026-09-01T10:10:00Z", "C:\\proj\\not-a-list.py"),
    ])
    conn = open_work_db(tmp_path / "data")
    assert collect_edits(conn, adapter) == {KEY}
    assert _shell_rows(conn) == {
        n("C:\\proj\\src\\a.py"): (KEY, 2, "2026-09-01T10:00:00Z", "2026-09-01T10:05:00Z"),
        n("C:\\proj\\dist\\app.js"): (KEY, 1, "2026-09-01T10:00:00Z", "2026-09-01T10:00:00Z"),
        n("C:\\proj\\pkg\\rel.txt"): (KEY, 1, "2026-09-01T10:08:00Z", "2026-09-01T10:08:00Z"),
        n("C:\\proj\\x.py"): (KEY, 1, "2026-09-01T10:09:00Z", "2026-09-01T10:09:00Z"),
    }
    assert conn.execute("SELECT COUNT(*) FROM session_files").fetchone()[0] == 0     # the edit-tool record is its own


def test_a_work_db_that_already_read_its_transcripts_for_edits_still_gains_shell_files(tmp_path):
    """The shell record keeps its own place in each transcript, so sessions read before it
    existed are filled in, and reading twice never counts a change twice."""
    f, adapter = _setup(tmp_path, [_edit("2026-09-01T10:00:00Z", "C:\\proj\\a.py"),
                                   _shell("2026-09-01T10:01:00Z", ["C:\\proj\\b.py"])])
    conn = open_work_db(tmp_path / "data")
    collect_edits(conn, adapter)
    conn.execute("DELETE FROM session_shell_files")
    conn.execute("DELETE FROM file_offsets WHERE path LIKE 'shell:%'")           # as left by a build without the shell record
    assert collect_edits(conn, adapter) == {KEY}
    assert list(_shell_rows(conn)) == [n("C:\\proj\\b.py")]
    assert conn.execute("SELECT edits FROM session_files").fetchone()[0] == 1    # the tool edit was not counted again
    assert collect_edits(conn, adapter) == set()
    with f.open("a", encoding="utf-8") as fh:
        fh.write(_shell("2026-09-01T10:09:00Z", ["C:\\proj\\b.py"]))
    collect_edits(conn, adapter)
    assert _shell_rows(conn)[n("C:\\proj\\b.py")][1] == 2


def test_a_sub_agents_shell_changes_belong_to_the_session_that_started_it(tmp_path):
    f, adapter = _setup(tmp_path, [_edit("2026-09-01T10:00:00Z", "C:\\proj\\a.py")])
    sub = f.parent / SID / "subagents"
    sub.mkdir(parents=True)
    (sub / "agent-x.jsonl").write_text(_shell("2026-09-01T10:02:00Z", ["C:\\proj\\gen.py"]), encoding="utf-8")
    conn = open_work_db(tmp_path / "data")
    collect_edits(conn, adapter)
    assert _shell_rows(conn)[n("C:\\proj\\gen.py")][0] == KEY


# ---- what the record changes ---------------------------------------------------------------

def _world(tmp_path: Path, cwd: Path, lines: list[str]) -> tuple[Path, ClaudeCodeAdapter]:
    data = tmp_path / "data"
    r = Repository(open_db(data / "argus.db"))
    r.upsert_session(session_factory(KEY, "2026-09-02T09:00:00Z"))
    r.upsert_turn(turn_factory(f"{KEY}:m1", KEY, "2026-09-02T09:20:00Z"))
    r.db.close()
    proj = tmp_path / ".claude" / "projects" / "p"
    proj.mkdir(parents=True)
    head = json.dumps({"type": "assistant", "sessionId": SID, "cwd": str(cwd), "gitBranch": "main", "timestamp": "2026-09-02T09:20:00Z",
                       "message": {"id": "m1", "role": "assistant", "model": "claude-opus-5", "content": [],
                                   "usage": {"input_tokens": 1, "output_tokens": 1}}}) + "\n"
    (proj / f"{SID}.jsonl").write_text(head + "".join(lines), encoding="utf-8")
    return data, ClaudeCodeAdapter(tmp_path / ".claude")


def test_a_session_that_changed_files_only_through_the_shell_joins_that_repo(tmp_path):
    parent = tmp_path / "space"
    repo = make_repo(parent / "proj")
    data, adapter = _world(tmp_path, parent, [_shell("2026-09-02T09:21:00Z", [str(repo / "a.py")], cwd=str(parent))])
    run_pass(data, adapter, now_iso="2026-09-05T00:00:00Z")
    conn = open_work_db(data)
    assert conn.execute("SELECT r.display_name FROM session_repo s JOIN repos r ON r.id = s.repo_id").fetchone()[0] == "proj"
    conn.close()


def test_a_commit_of_a_file_the_shell_changed_is_linked_by_files(tmp_path):
    repo = make_repo(tmp_path / "proj")
    data, adapter = _world(tmp_path, repo, [_shell("2026-09-02T10:00:00Z", [str(repo / "a.py")], cwd=str(repo))])
    (repo / "a.py").write_text("print(4)\n")
    git(repo, "commit", "-q", "-am", "feat: via sed, committed next day", date="2026-09-03T15:00:00Z")
    run_pass(data, adapter, now_iso="2026-09-05T00:00:00Z")
    conn = open_work_db(data)
    by_subject = {r["subject"]: r["evidence"] for r in conn.execute(
        "SELECT c.subject, sc.evidence FROM session_commits sc JOIN commits c ON c.sha = sc.sha")}
    conn.close()
    assert by_subject["feat: via sed, committed next day"] == "files"


def test_a_session_with_only_shell_changes_is_not_read_only_for_the_timing_rule(tmp_path):
    r = Repository(open_db(tmp_path / "argus.db"))
    r.upsert_session(session_factory("claude_code:A", "2026-09-01T10:00:00Z"))
    r.upsert_turn(turn_factory("claude_code:A:m0", "claude_code:A", "2026-09-01T10:00:00Z"))
    r.db.close()
    conn = open_work_db(tmp_path)
    conn.execute("INSERT INTO repos (id, root, display_name, user_emails) VALUES (1, '/r', 'r', '[\"me@example.com\"]')")
    conn.execute("INSERT INTO session_repo VALUES ('claude_code:A', 1, '/r', 'main')")
    conn.execute("INSERT INTO edits_seen VALUES ('claude_code:A')")
    conn.execute("INSERT INTO commits VALUES (1, 'aaaaaaa111', 'x', 'me@example.com', '2026-09-01T10:05:00Z', '2026-09-01T10:05:00Z', 's', 1, 0, 0, 0, 0)")
    link_repo(conn, 1)
    assert conn.execute("SELECT COUNT(*) FROM session_commits").fetchone()[0] == 0     # asked only: no timing link
    conn.execute("INSERT INTO session_shell_files VALUES ('claude_code:A', '/elsewhere/gen.py', 1, '2026-09-01T10:01:00Z', '2026-09-01T10:01:00Z')")
    link_repo(conn, 1)
    assert conn.execute("SELECT evidence FROM session_commits").fetchone()[0] == "inferred"


def S(sid, first, last, files, shell):
    s = SessionSummary(sid, sid, "main", first, last, 1000, 1.0, 1)
    s.files, s.shell_files = frozenset(files), frozenset(shell)
    return s


def test_shell_changes_make_a_thread_work_but_never_join_two_threads():
    """Any session that rebuilds touches the same build output; sharing it is not sharing work."""
    a = S("a", "2026-09-01T10:00:00Z", "2026-09-01T11:00:00Z", [], ["dist/index.html", "dist/app.js"])
    b = S("b", "2026-09-01T12:00:00Z", "2026-09-01T13:00:00Z", ["docs/x.md"], ["dist/index.html", "dist/app.js"])
    stories = group_stories([a, b])
    assert sorted(s["session_ids"] for s in stories) == [["a"], ["b"]]
    by = {s["session_ids"][0]: s["files_edited"] for s in stories}
    assert by == {"a": 2, "b": 3}                           # a changed files, so it is not "explored"


def test_a_shell_only_thread_reads_as_work_not_as_a_question(tmp_path):
    r = Repository(open_db(tmp_path / "argus.db"))
    r.upsert_session(session_factory("claude_code:A", "2026-09-20T10:00:00Z"))
    r.upsert_turn(turn_factory("claude_code:A:m1", "claude_code:A", "2026-09-20T10:00:00Z", cost=2.0))
    r.db.close()
    conn = open_work_db(tmp_path)
    conn.execute("INSERT INTO repos (id, root, display_name, user_emails, default_ref, has_remote, dirty) VALUES (1, '/r', 'proj', '[]', 'refs/heads/main', 0, 0)")
    conn.execute("INSERT INTO session_repo VALUES ('claude_code:A', 1, '/r', 'main')")
    conn.execute("INSERT INTO session_facts VALUES ('claude_code:A', 'Rename with sed', 'ai', NULL)")
    conn.execute("INSERT INTO edits_seen VALUES ('claude_code:A')")
    frm, to = "2026-09-01T00:00:00Z", "2026-09-25T00:00:00Z"
    (s,) = queries.overview(conn, 1, frm, to, now_iso=to)["stories"]
    assert (s["state"], s["files_edited"]) == ("explored", 0)
    conn.execute("INSERT INTO session_shell_files VALUES ('claude_code:A', '/r/src/a.py', 1, '2026-09-20T10:01:00Z', '2026-09-20T10:01:00Z')")
    conn.execute("INSERT INTO session_shell_files VALUES ('claude_code:A', '/tmp/scratch.txt', 1, '2026-09-20T10:01:00Z', '2026-09-20T10:01:00Z')")
    (s,) = queries.overview(conn, 1, frm, to, now_iso=to)["stories"]
    assert (s["state"], s["files_edited"], s["reason"]) == ("dropped", 1, "1 file edited, none committed")
