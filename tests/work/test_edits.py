"""Which files a session edited: read from the transcript's tool calls, sub-agents rolled up."""
from __future__ import annotations

import json
from pathlib import Path

from argus.adapters.claude_code.adapter import ClaudeCodeAdapter
from argus.store.repository import normalize_project_path
from argus.work.db import open_work_db
from argus.work.edits import collect_edits
from argus.work.facts import collect_facts

SID = "11111111-2222-3333-4444-555555555555"
KEY = f"claude_code:{SID}"


def _use(ts: str, name: str, cwd: str = "C:\\proj", **inp) -> str:
    return json.dumps({"sessionId": SID, "cwd": cwd, "type": "assistant", "timestamp": ts, "message": {
        "id": f"m-{ts}", "role": "assistant", "model": "claude-opus-5",
        "content": [{"type": "tool_use", "id": f"t-{ts}", "name": name, "input": inp}],
        "usage": {"input_tokens": 1, "output_tokens": 1}}}) + "\n"


def _setup(tmp_path: Path, lines: list[str]) -> tuple[Path, ClaudeCodeAdapter]:
    proj = tmp_path / ".claude" / "projects" / "C--proj"
    proj.mkdir(parents=True)
    f = proj / f"{SID}.jsonl"
    f.write_text("".join(lines), encoding="utf-8")
    return f, ClaudeCodeAdapter(tmp_path / ".claude")


def _rows(conn) -> dict[str, tuple]:
    return {r["path"]: (r["session_id"], r["edits"], r["first_ts"], r["last_ts"])
            for r in conn.execute("SELECT * FROM session_files")}


def test_records_every_editing_tool_and_ignores_reads(tmp_path):
    _, adapter = _setup(tmp_path, [
        _use("2026-09-01T10:00:00Z", "Read", file_path="C:\\proj\\README.md"),
        _use("2026-09-01T10:01:00Z", "Edit", file_path="C:\\proj\\src\\a.py", old_string="x", new_string="y"),
        _use("2026-09-01T10:02:00Z", "Write", file_path="C:\\proj\\src\\b.py", content="z"),
        _use("2026-09-01T10:03:00Z", "MultiEdit", file_path="C:\\proj\\src\\a.py", edits=[]),
        _use("2026-09-01T10:04:00Z", "NotebookEdit", notebook_path="C:\\proj\\n.ipynb", new_source=""),
        _use("2026-09-01T10:05:00Z", "Bash", command="echo hi > C:\\proj\\c.txt"),
    ])
    conn = open_work_db(tmp_path / "data")
    assert collect_edits(conn, adapter) == {KEY}
    n = normalize_project_path
    assert _rows(conn) == {
        n("C:\\proj\\src\\a.py"): (KEY, 2, "2026-09-01T10:01:00Z", "2026-09-01T10:03:00Z"),
        n("C:\\proj\\src\\b.py"): (KEY, 1, "2026-09-01T10:02:00Z", "2026-09-01T10:02:00Z"),
        n("C:\\proj\\n.ipynb"): (KEY, 1, "2026-09-01T10:04:00Z", "2026-09-01T10:04:00Z"),
    }


def test_a_relative_path_is_read_against_the_lines_folder(tmp_path):
    _, adapter = _setup(tmp_path, [_use("2026-09-01T10:01:00Z", "Edit", cwd="C:\\proj\\pkg", file_path="mod.py")])
    conn = open_work_db(tmp_path / "data")
    collect_edits(conn, adapter)
    assert list(_rows(conn)) == [normalize_project_path("C:\\proj\\pkg\\mod.py")]


def test_sub_agent_edits_belong_to_the_session_that_started_it(tmp_path):
    f, adapter = _setup(tmp_path, [_use("2026-09-01T10:01:00Z", "Edit", file_path="C:\\proj\\a.py")])
    sub = f.parent / SID / "subagents"
    sub.mkdir(parents=True)
    (sub / "agent-abc.jsonl").write_text(
        _use("2026-09-01T10:02:00Z", "Edit", file_path="C:\\proj\\a.py")
        + _use("2026-09-01T10:03:00Z", "Write", file_path="C:\\proj\\sub.py"), encoding="utf-8")
    conn = open_work_db(tmp_path / "data")
    assert collect_edits(conn, adapter) == {KEY}
    rows = _rows(conn)
    assert rows[normalize_project_path("C:\\proj\\a.py")] == (KEY, 2, "2026-09-01T10:01:00Z", "2026-09-01T10:02:00Z")
    assert rows[normalize_project_path("C:\\proj\\sub.py")][0] == KEY


def test_reading_again_counts_only_new_lines(tmp_path):
    f, adapter = _setup(tmp_path, [_use("2026-09-01T10:01:00Z", "Edit", file_path="C:\\proj\\a.py")])
    conn = open_work_db(tmp_path / "data")
    collect_edits(conn, adapter)
    assert collect_edits(conn, adapter) == set()
    with f.open("a", encoding="utf-8") as fh:
        fh.write(_use("2026-09-01T10:09:00Z", "Edit", file_path="C:\\proj\\a.py"))
    assert collect_edits(conn, adapter) == {KEY}
    assert _rows(conn)[normalize_project_path("C:\\proj\\a.py")] == (KEY, 2, "2026-09-01T10:01:00Z", "2026-09-01T10:09:00Z")


def test_transcripts_the_facts_pass_already_read_are_still_read_for_edits(tmp_path):
    """A work.db made before this table existed has every transcript's offset at its end;
    the edits pass keeps its own place, so those sessions are not lost."""
    _, adapter = _setup(tmp_path, [_use("2026-09-01T10:01:00Z", "Edit", file_path="C:\\proj\\a.py")])
    conn = open_work_db(tmp_path / "data")
    collect_facts(conn, adapter)
    collect_edits(conn, adapter)
    assert len(_rows(conn)) == 1
    collect_facts(conn, adapter)   # and the two passes never move each other's place
    assert conn.execute("SELECT COUNT(*) FROM active_spans").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM turn_attribution").fetchone()[0] == 0


def test_a_malformed_tool_call_is_skipped(tmp_path):
    bad = json.dumps({"type": "assistant", "timestamp": "2026-09-01T10:00:00Z",
                      "message": {"content": [{"type": "tool_use", "name": "Edit", "input": "oops"},
                                              {"type": "tool_use", "name": "Edit", "input": {"file_path": 7}}]}}) + "\n"
    _, adapter = _setup(tmp_path, [bad, _use("2026-09-01T10:01:00Z", "Edit", file_path="C:\\proj\\a.py")])
    conn = open_work_db(tmp_path / "data")
    collect_edits(conn, adapter)
    assert list(_rows(conn)) == [normalize_project_path("C:\\proj\\a.py")]


def test_a_session_read_for_edits_is_marked_even_when_it_made_none(tmp_path):
    """"Read, edited nothing" and "never read" must differ: only the first lets a thread be
    called explored."""
    _, adapter = _setup(tmp_path, [_use("2026-09-01T10:00:00Z", "Read", file_path="C:\proj\README.md")])
    conn = open_work_db(tmp_path / "data")
    assert collect_edits(conn, adapter) == set()
    assert [r[0] for r in conn.execute("SELECT session_id FROM edits_seen")] == [KEY]
    assert _rows(conn) == {}
