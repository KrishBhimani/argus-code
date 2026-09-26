"""Facts Argus's main pipeline skips: titles, active time, branch, PRs, attribution, commit SHAs."""
from __future__ import annotations

import json
from pathlib import Path

from argus.adapters.claude_code.adapter import ClaudeCodeAdapter
from argus.work.db import open_work_db
from argus.work.facts import collect_facts

SID = "11111111-2222-3333-4444-555555555555"


def L(**o) -> str:
    return json.dumps({"sessionId": SID, "cwd": "C:\\proj", **o}) + "\n"


def _setup(tmp_path: Path, lines: list[str]) -> tuple[Path, ClaudeCodeAdapter]:
    proj = tmp_path / ".claude" / "projects" / "C--proj"
    proj.mkdir(parents=True)
    f = proj / f"{SID}.jsonl"
    f.write_text("".join(lines), encoding="utf-8")
    return f, ClaudeCodeAdapter(tmp_path / ".claude")


def _asst(mid: str, ts: str, content: list, **extra) -> str:
    return L(type="assistant", timestamp=ts, gitBranch="feat/x",
             message={"id": mid, "model": "claude-opus-5", "role": "assistant", "content": content,
                      "usage": {"input_tokens": 1, "output_tokens": 1}}, **extra)


def _result(tool_id: str, ts: str, text: str) -> str:
    return L(type="user", timestamp=ts, message={"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": tool_id, "content": text}]})


def test_collects_every_fact(tmp_path):
    lines = [
        L(type="user", timestamp="2026-09-01T10:00:00Z", message={"role": "user", "content": "hi"}),
        _asst("m1", "2026-09-01T10:00:30Z",
              [{"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": 'git commit -m "x"'}}],
              attributionSkill="superpowers:brainstorming", attributionMcpServer="drawio"),
        _result("t1", "2026-09-01T10:00:40Z", "[feat/x 1a2b3c4] x\n 1 file changed"),
        _asst("m2", "2026-09-01T10:01:00Z",
              [{"type": "tool_use", "id": "t2", "name": "Bash", "input": {"command": "gh pr create --fill"}}]),
        _result("t2", "2026-09-01T10:01:10Z", "https://github.com/o/r/pull/42\n"),
        L(type="system", subtype="turn_duration", durationMs=90_000, timestamp="2026-09-01T10:01:30Z"),
        L(type="ai-title", aiTitle="Fix the widget"),
        L(type="pr-link", prNumber=7, prUrl="https://github.com/o/r/pull/7", prRepository="o/r",
          timestamp="2026-09-01T10:02:00Z"),
    ]
    _, adapter = _setup(tmp_path, lines)
    conn = open_work_db(tmp_path / "data")
    sid = f"claude_code:{SID}"
    assert collect_facts(conn, adapter) == {sid}

    facts = conn.execute("SELECT * FROM session_facts WHERE session_id=?", (sid,)).fetchone()
    assert (facts["title"], facts["title_source"]) == ("Fix the widget", "ai")
    repo = conn.execute("SELECT * FROM session_repo WHERE session_id=?", (sid,)).fetchone()
    assert (repo["cwd"], repo["git_branch"]) == ("C:\\proj", "feat/x")
    assert conn.execute("SELECT SUM(ms) FROM active_spans WHERE session_id=? AND kind='measured'", (sid,)).fetchone()[0] == 90_000
    assert {r["pr_number"] for r in conn.execute("SELECT pr_number FROM session_prs")} == {7, 42}
    attr = conn.execute("SELECT * FROM turn_attribution WHERE turn_id=?", (f"{sid}:m1",)).fetchone()
    assert (attr["skill"], attr["mcp_server"]) == ("superpowers:brainstorming", "drawio")
    assert [r["short_sha"] for r in conn.execute("SELECT short_sha FROM commit_claims")] == ["1a2b3c4"]


def test_custom_title_beats_ai_title(tmp_path):
    _, adapter = _setup(tmp_path, [L(type="custom-title", customTitle="mine"), L(type="ai-title", aiTitle="ai")])
    conn = open_work_db(tmp_path / "data")
    collect_facts(conn, adapter)
    assert tuple(conn.execute("SELECT title, title_source FROM session_facts").fetchone()) == ("mine", "custom")


def test_estimated_active_time_caps_gaps(tmp_path):
    lines = [L(type="user", timestamp="2026-09-01T10:00:00Z", message={"role": "user", "content": "a"}),
             L(type="user", timestamp="2026-09-01T10:02:00Z", message={"role": "user", "content": "b"}),
             L(type="user", timestamp="2026-09-01T13:00:00Z", message={"role": "user", "content": "c"})]
    _, adapter = _setup(tmp_path, lines)
    conn = open_work_db(tmp_path / "data")
    collect_facts(conn, adapter)
    est = conn.execute("SELECT SUM(ms) FROM active_spans WHERE kind='estimated'").fetchone()[0]
    assert est == 120_000 + 300_000  # 2 min + (3 h capped at 5 min)


def test_incremental_read_does_not_double_count(tmp_path):
    f, adapter = _setup(tmp_path, [L(type="system", subtype="turn_duration", durationMs=1000,
                                     timestamp="2026-09-01T10:00:00Z")])
    conn = open_work_db(tmp_path / "data")
    collect_facts(conn, adapter)
    collect_facts(conn, adapter)
    with f.open("a", encoding="utf-8") as fh:
        fh.write(L(type="system", subtype="turn_duration", durationMs=500, timestamp="2026-09-01T10:05:00Z"))
    collect_facts(conn, adapter)
    assert conn.execute("SELECT SUM(ms) FROM active_spans WHERE kind='measured'").fetchone()[0] == 1500


def test_partial_last_line_is_held_back(tmp_path):
    whole = L(type="ai-title", aiTitle="later")
    f, adapter = _setup(tmp_path, [whole[:20]])
    conn = open_work_db(tmp_path / "data")
    collect_facts(conn, adapter)
    assert conn.execute("SELECT COUNT(*) FROM session_facts").fetchone()[0] == 0
    f.write_text(whole, encoding="utf-8")
    collect_facts(conn, adapter)
    assert conn.execute("SELECT title FROM session_facts").fetchone()[0] == "later"


def test_invalid_utf8_does_not_drift_offset(tmp_path):
    f, adapter = _setup(tmp_path, [])
    f.write_bytes(b'{"type":"user","note":"\xff\xfe"}\n' + L(type="ai-title", aiTitle="after").encode())
    conn = open_work_db(tmp_path / "data")
    collect_facts(conn, adapter)
    assert conn.execute("SELECT title FROM session_facts").fetchone()[0] == "after"
    assert conn.execute("SELECT byte_offset FROM file_offsets").fetchone()[0] == f.stat().st_size


def test_bad_line_does_not_block_other_files_or_other_facts(tmp_path):
    """Review I-1: one malformed line (bad timestamp, non-numeric duration) used to
    raise out of collect_facts, roll back, and stop facts for EVERY file on every pass."""
    _, adapter = _setup(tmp_path, [
        L(type="user", timestamp="not-a-date", message={"role": "user", "content": "x"}),
        L(type="system", subtype="turn_duration", durationMs="lots", timestamp="2026-09-01T10:00:00Z"),
        L(type="ai-title", aiTitle="still collected"),
    ])
    other = tmp_path / ".claude" / "projects" / "C--other" / "22222222-2222-3333-4444-555555555555.jsonl"
    other.parent.mkdir(parents=True)
    other.write_text(json.dumps({"type": "ai-title", "aiTitle": "other file"}) + "\n", encoding="utf-8")
    conn = open_work_db(tmp_path / "data")
    collect_facts(conn, adapter)
    titles = {r["title"] for r in conn.execute("SELECT title FROM session_facts")}
    assert titles == {"still collected", "other file"}
