"""Chunked ingest must equal one-pass ingest.

REGRESSION (H1): the watcher ingests a live transcript in many small ticks
(100 ms debounce, sometimes one line at a time). Every value that describes the
whole file (session start/duration/project, turn order, a streamed message's
tool-call count, a tool call's error flag) used to be computed from the current
tick's lines only, so the archive depended on how the file happened to be
chunked. This test pins the invariant: whatever the chunking, the stored rows
are identical to a single read of the finished file.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from argus.adapters.claude_code.adapter import ClaudeCodeAdapter
from argus.collector.pipeline import ingest_file
from argus.pricing.load import load_pricing_table
from argus.store.db import open_db
from argus.store.repository import Repository

SID = "sess-inv"


def _asst(mid: str, ts: str, content: list[dict], *, out: int, cwd: str, uuid: str,
          version: str = "2.1.94") -> dict:
    return {
        "type": "assistant",
        "sessionId": SID,
        "uuid": uuid,
        "timestamp": ts,
        "cwd": cwd,
        "version": version,
        "message": {
            "id": mid,
            "model": "claude-opus-4-7",
            "role": "assistant",
            "content": content,
            "usage": {
                "input_tokens": 100,
                "output_tokens": out,
                "cache_read_input_tokens": 1000,
                "cache_creation_input_tokens": 10,
            },
        },
    }


def _user(ts: str, content, *, cwd: str, uuid: str) -> dict:
    return {
        "type": "user",
        "sessionId": SID,
        "uuid": uuid,
        "timestamp": ts,
        "cwd": cwd,
        "message": {"role": "user", "content": content},
    }


def _result(tool_use_id: str, is_error: bool) -> dict:
    return {"type": "tool_result", "tool_use_id": tool_use_id, "is_error": is_error, "content": "x"}


def _tool_use(tid: str, name: str = "Bash") -> dict:
    return {"type": "tool_use", "id": tid, "name": name, "input": {"command": "ls"}}


def _transcript() -> list[str]:
    """A session that exercises every chunk-sensitive value.

    - m1 streams over three lines (placeholder output first), two tool_uses.
    - t2's result is an error and arrives later than its call.
    - the user ``cd``s into a sub-directory before m2 (cwd changes mid-file).
    - m3 lands a day later, so a chunk-local start would shrink the duration.
    """
    lines = [
        _user("2026-05-01T10:00:00.000Z", "do the thing", cwd="/proj", uuid="u0"),
        _asst("m1", "2026-05-01T10:00:01.000Z", [{"type": "text", "text": "ok"}],
              out=3, cwd="/proj", uuid="u1"),
        _asst("m1", "2026-05-01T10:00:02.000Z", [_tool_use("t1")],
              out=3, cwd="/proj", uuid="u2"),
        _asst("m1", "2026-05-01T10:00:03.000Z", [_tool_use("t2", "Read")],
              out=250, cwd="/proj", uuid="u3"),
        _user("2026-05-01T10:00:04.000Z", [_result("t1", False)], cwd="/proj", uuid="u4"),
        _user("2026-05-01T10:00:20.000Z", [_result("t2", True)], cwd="/proj", uuid="u5"),
        _asst("m2", "2026-05-01T10:01:00.000Z", [_tool_use("t3")],
              out=40, cwd="/proj/sub", uuid="u6"),
        _user("2026-05-01T10:01:30.000Z", [_result("t3", True)], cwd="/proj/sub", uuid="u7"),
        _asst("m3", "2026-05-02T10:00:00.000Z", [{"type": "text", "text": "done"}],
              out=70, cwd="/proj/sub", uuid="u8", version="2.1.95"),
    ]
    return [json.dumps(x) for x in lines]


def _snapshot(repo: Repository) -> dict:
    s = dict(repo.db.execute("SELECT * FROM sessions WHERE id = ?", (f"claude_code:{SID}",)).fetchone())
    s.pop("computed_at")
    turns = [dict(r) for r in repo.db.execute(
        "SELECT * FROM turns WHERE session_id = ? ORDER BY id", (f"claude_code:{SID}",))]
    calls = [dict(r) for r in repo.db.execute(
        "SELECT * FROM tool_calls WHERE session_id = ? ORDER BY id", (f"claude_code:{SID}",))]
    return {"session": s, "turns": turns, "calls": calls}


def _ingest_in_chunks(tmp_path: Path, name: str, chunks: list[bytes]) -> dict:
    root = tmp_path / name / ".claude"
    proj = root / "projects" / "-proj"
    proj.mkdir(parents=True)
    f = proj / f"{SID}.jsonl"
    f.write_bytes(b"")
    adapter = ClaudeCodeAdapter(root)
    table = load_pricing_table()
    conn = open_db(tmp_path / name / "argus.db")
    try:
        repo = Repository(conn)
        for c in chunks:
            with f.open("ab") as fh:
                fh.write(c)
            ingest_file(adapter, f, repo, table)
        return _snapshot(repo)
    finally:
        conn.close()


def _whole() -> bytes:
    return ("\n".join(_transcript()) + "\n").encode("utf-8")


def _by_line() -> list[bytes]:
    return [(ln + "\n").encode("utf-8") for ln in _transcript()]


def _by_bytes(step: int) -> list[bytes]:
    data = _whole()
    return [data[i : i + step] for i in range(0, len(data), step)]


@pytest.fixture
def one_pass(tmp_path: Path) -> dict:
    return _ingest_in_chunks(tmp_path, "whole", [_whole()])


def test_one_pass_reference_values(one_pass: dict) -> None:
    """Sanity-check the reference itself so the invariant compares good data."""
    s = one_pass["session"]
    assert s["started_at"] == "2026-05-01T10:00:01.000Z"
    assert s["ended_at"] == "2026-05-02T10:00:00.000Z"
    assert s["duration_sec"] == 86_399
    assert s["project_path"] == "/proj"
    turns = {t["id"].rsplit(":", 1)[1]: t for t in one_pass["turns"]}
    assert turns["m1"]["tool_calls_count"] == 2
    assert turns["m1"]["output_tokens"] == 250
    assert turns["m1"]["timestamp"] == "2026-05-01T10:00:01.000Z"
    seqs = [turns[m]["sequence"] for m in ("m1", "m2", "m3")]
    assert seqs == sorted(seqs) and len(set(seqs)) == 3
    errs = {c["id"].rsplit(":", 1)[1]: c["is_error"] for c in one_pass["calls"]}
    assert errs == {"t1": 0, "t2": 1, "t3": 1}
    calls = {c["id"].rsplit(":", 1)[1]: c for c in one_pass["calls"]}
    assert calls["t1"]["turn_index"] == turns["m1"]["sequence"]
    assert calls["t3"]["turn_index"] == turns["m2"]["sequence"]


def test_line_by_line_equals_one_pass(tmp_path: Path, one_pass: dict) -> None:
    assert _ingest_in_chunks(tmp_path, "lines", _by_line()) == one_pass


@pytest.mark.parametrize("step", [7, 64, 333])
def test_byte_chunks_equal_one_pass(tmp_path: Path, one_pass: dict, step: int) -> None:
    assert _ingest_in_chunks(tmp_path, f"bytes{step}", _by_bytes(step)) == one_pass


def test_offset_zero_reread_after_chunked_ingest_is_stable(tmp_path: Path, one_pass: dict) -> None:
    """A backfill re-read (offset reset to 0) must reproduce, not double, the rows."""
    root = tmp_path / "reread" / ".claude"
    proj = root / "projects" / "-proj"
    proj.mkdir(parents=True)
    f = proj / f"{SID}.jsonl"
    f.write_bytes(b"")
    adapter = ClaudeCodeAdapter(root)
    table = load_pricing_table()
    conn = open_db(tmp_path / "reread" / "argus.db")
    try:
        repo = Repository(conn)
        for c in _by_line():
            with f.open("ab") as fh:
                fh.write(c)
            ingest_file(adapter, f, repo, table)
        repo.set_file_offset(str(f), 0)
        ingest_file(adapter, f, repo, table)
        assert _snapshot(repo) == one_pass
    finally:
        conn.close()


def test_offset_zero_reread_rewrites_prefix_rows(tmp_path: Path, one_pass: dict) -> None:
    """REGRESSION (H1c/H1d): rows written by the pre-fix ingest (sequence and
    turn_index restarting at 0 per tick, a split message's count/timestamp from
    its tail) must be overwritten — not merged — by a backfill re-read."""
    root = tmp_path / "legacy" / ".claude"
    proj = root / "projects" / "-proj"
    proj.mkdir(parents=True)
    f = proj / f"{SID}.jsonl"
    f.write_bytes(_whole())
    adapter = ClaudeCodeAdapter(root)
    table = load_pricing_table()
    conn = open_db(tmp_path / "legacy" / "argus.db")
    try:
        repo = Repository(conn)
        ingest_file(adapter, f, repo, table)
        sid = f"claude_code:{SID}"
        conn.execute("UPDATE turns SET sequence = 0, tool_calls_count = 1,"
                     " timestamp = '2026-05-01T10:00:03.000Z' WHERE session_id = ?", (sid,))
        conn.execute("UPDATE tool_calls SET turn_index = 0, is_error = 0 WHERE session_id = ?", (sid,))
        conn.execute("UPDATE sessions SET project_path = '/proj/sub' WHERE id = ?", (sid,))

        repo.set_file_offset(str(f), 0)
        ingest_file(adapter, f, repo, table)
        assert _snapshot(repo) == one_pass
    finally:
        conn.close()


def test_zero_turn_tick_keeps_stored_project_and_start(tmp_path: Path, one_pass: dict) -> None:
    """A tick holding only user lines (another cwd) must not move the session."""
    extra = json.dumps(_user("2026-05-03T00:00:00.000Z", "hi", cwd="/elsewhere", uuid="u9")) + "\n"
    snap = _ingest_in_chunks(tmp_path, "zero", [_whole(), extra.encode()])
    assert snap["session"]["project_path"] == "/proj"
    assert snap["session"] == one_pass["session"]


def test_subagent_error_in_later_tick_is_recorded(tmp_path: Path) -> None:
    """REGRESSION (H1b): a sub-agent's tool_result arriving in a later tick must
    flag the sub-session's call, the same as for a parent."""
    root = tmp_path / ".claude"
    proj = root / "projects" / "-proj"
    sub_dir = proj / SID / "subagents"
    sub_dir.mkdir(parents=True)
    parent = proj / f"{SID}.jsonl"
    sub = sub_dir / "agent-x.jsonl"
    parent.write_text(_transcript()[1] + "\n", encoding="utf-8")
    sub.write_text(json.dumps(_asst("s-m1", "2026-05-01T10:00:05.000Z", [_tool_use("st1")],
                                    out=5, cwd="/proj", uuid="s1")) + "\n", encoding="utf-8")
    adapter = ClaudeCodeAdapter(root)
    table = load_pricing_table()
    conn = open_db(tmp_path / "argus.db")
    try:
        repo = Repository(conn)
        ingest_file(adapter, parent, repo, table)
        with sub.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(_user("2026-05-01T10:00:09.000Z", [_result("st1", True)],
                                      cwd="/proj", uuid="s2")) + "\n")
        ingest_file(adapter, parent, repo, table)
        row = conn.execute("SELECT is_error FROM tool_calls WHERE id = ?",
                           (f"claude_code:{SID}/agent-x:st1",)).fetchone()
        assert row["is_error"] == 1
    finally:
        conn.close()


def test_rereading_a_split_message_tail_does_not_double_count_calls(tmp_path: Path, one_pass: dict) -> None:
    """If the offset write is lost (crash between rows and offset), the same
    tail is read again from the same non-zero offset; the merge must be
    idempotent, not add the tail's tool_use blocks a second time."""
    root = tmp_path / "crash" / ".claude"
    proj = root / "projects" / "-proj"
    proj.mkdir(parents=True)
    f = proj / f"{SID}.jsonl"
    lines = _by_line()
    f.write_bytes(b"".join(lines[:3]))  # m1's head (text + t1)
    adapter = ClaudeCodeAdapter(root)
    table = load_pricing_table()
    conn = open_db(tmp_path / "crash" / "argus.db")
    try:
        repo = Repository(conn)
        ingest_file(adapter, f, repo, table)
        tail_offset = repo.get_file_offset(str(f))
        with f.open("ab") as fh:
            fh.write(b"".join(lines[3:]))
        ingest_file(adapter, f, repo, table)
        repo.set_file_offset(str(f), tail_offset)  # simulate the lost offset write
        ingest_file(adapter, f, repo, table)
        assert _snapshot(repo) == one_pass
    finally:
        conn.close()
