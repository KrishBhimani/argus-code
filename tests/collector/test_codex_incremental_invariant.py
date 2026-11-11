"""Chunked Codex ingest must equal one-pass ingest (the H1 invariant, which
collector/AGENTS.md requires for every adapter). The fixture is a real-shaped
two-task thread where tool items land after their usage record, a request
lands before its own, a command fails, and the second task is still being
written when chunks end."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from argus.adapters.codex.adapter import CodexAdapter
from argus.collector.pipeline import ingest_file
from argus.pricing.load import load_pricing_table
from argus.store.db import open_db
from argus.store.repository import Repository

TID = "01a10342-2700-7791-9a7a-298bd80e3d69"
SID = f"codex:{TID}"


def E(o, t, p):
    return {"timestamp": f"2026-10-03T20:{o // 60:02d}:{o % 60:02d}.000Z", "ordinal": o, "type": t, "payload": p}


def TUR(o, rid, turn, out):
    return E(o, "token_usage_record", {"response_id": rid, "turn_id": turn, "thread_id": TID, "usage": {
        "input_tokens": 1000, "cached_input_tokens": 800, "cache_write_input_tokens": 0,
        "output_tokens": out, "reasoning_output_tokens": 1, "total_tokens": 1000 + out}})


def IT(o, turn, it):
    return E(o, "event_msg", {"type": "item_completed", "thread_id": TID, "turn_id": turn, "item": it})


def _lines() -> list[str]:
    rows = [
        E(0, "session_meta", {"id": TID, "cwd": "/proj", "cli_version": "0.159.2", "source": "vscode"}),
        E(1, "event_msg", {"type": "task_started", "turn_id": "U1"}),
        E(2, "turn_context", {"turn_id": "U1", "model": "gpt-6.1-sol", "effort": "medium"}),
        IT(3, "U1", {"type": "UserMessage", "id": "um1", "content": [{"type": "text", "text": "go"}]}),
        E(4, "response_item", {"type": "custom_tool_call", "name": "exec", "call_id": "x1", "input": "ls"}),
        TUR(5, "r1", "U1", 10),
        IT(6, "U1", {"type": "CommandExecution", "id": "c1", "command": ["ls"], "status": "failed", "exit_code": 1}),
        IT(7, "U1", {"type": "McpToolCall", "id": "m1", "server": "s", "tool": "t", "status": "completed",
                     "result": {"isError": False, "content": []}}),
        TUR(8, "r2", "U1", 20),
        E(9, "event_msg", {"type": "task_complete", "turn_id": "U1"}),
        E(10, "event_msg", {"type": "task_started", "turn_id": "U2"}),
        E(11, "turn_context", {"turn_id": "U2", "model": "gpt-6-luna", "effort": "high"}),
        E(12, "response_item", {"type": "function_call", "namespace": "collaboration", "name": "spawn_agent",
                                "call_id": "sp", "arguments": '{"task_name": "helper"}',
                                "internal_chat_message_metadata_passthrough": {"turn_id": "U2"}}),
        TUR(13, "r3", "U2", 30),
        IT(14, "U2", {"type": "FileChange", "id": "f1", "changes": {}, "status": "completed"}),
        IT(15, "U2", {"type": "CommandExecution", "id": "c2", "command": ["x"], "status": "completed", "exit_code": 0}),
        TUR(16, "r4", "U2", 40),
        E(17, "event_msg", {"type": "task_complete", "turn_id": "U2"}),
    ]
    return [json.dumps(r) for r in rows]


def _whole() -> bytes:
    return ("\n".join(_lines()) + "\n").encode()


def _snapshot(repo: Repository) -> dict:
    s = dict(repo.db.execute("SELECT * FROM sessions WHERE id = ?", (SID,)).fetchone())
    s.pop("computed_at")
    q = lambda t: [dict(r) for r in repo.db.execute(f"SELECT * FROM {t} WHERE session_id = ? ORDER BY id", (SID,))]  # noqa: E731
    return {"session": s, "turns": q("turns"), "calls": q("tool_calls")}


def _ingest(tmp_path: Path, name: str, chunks: list[bytes]) -> dict:
    root = tmp_path / name / ".codex"
    f = root / "sessions" / "2026" / "10" / "04" / f"rollout-2026-10-04T01-03-49-{TID}.jsonl"
    f.parent.mkdir(parents=True)
    f.write_bytes(b"")
    adapter, table = CodexAdapter(root), load_pricing_table()
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


@pytest.fixture
def one_pass(tmp_path: Path) -> dict:
    return _ingest(tmp_path, "whole", [_whole()])


def test_reference_values(one_pass: dict) -> None:
    s = one_pass["session"]
    assert (s["agent"], s["turn_count"], s["project_path"]) == ("codex", 4, "/proj")
    assert s["total_output_tokens"] == 100 and s["total_cache_read_tokens"] == 3200
    assert s["total_cost_usd"] > 0  # gpt-6.1-sol and gpt-6-luna are priced
    turns = {t["id"].rsplit(":", 1)[1]: t for t in one_pass["turns"]}
    # items follow the usage record that asked for them; the request precedes its own
    assert {k: t["tool_calls_count"] for k, t in turns.items()} == {"r1": 2, "r2": 0, "r3": 3, "r4": 0}
    assert (turns["r1"]["model"], turns["r3"]["model"]) == ("gpt-6.1-sol", "gpt-6-luna")
    calls = {c["id"].rsplit(":", 1)[1]: c for c in one_pass["calls"]}
    assert set(calls) == {"c1", "m1", "sp", "f1", "c2"}
    assert calls["c1"]["is_error"] == 1 and calls["c2"]["is_error"] == 0
    assert calls["sp"]["turn_index"] == turns["r3"]["sequence"]
    assert calls["c2"]["turn_index"] == turns["r3"]["sequence"]


def test_line_by_line_equals_one_pass(tmp_path: Path, one_pass: dict) -> None:
    assert _ingest(tmp_path, "lines", [(l + "\n").encode() for l in _lines()]) == one_pass


@pytest.mark.parametrize("step", [7, 64, 333, 1000])
def test_byte_chunks_equal_one_pass(tmp_path: Path, one_pass: dict, step: int) -> None:
    data = _whole()
    assert _ingest(tmp_path, f"b{step}", [data[i:i + step] for i in range(0, len(data), step)]) == one_pass


def test_cold_cache_mid_task_equals_one_pass(tmp_path: Path, one_pass: dict) -> None:
    """A restart mid-task (new adapter, empty cache) rebuilds state from the head."""
    root = tmp_path / "cold" / ".codex"
    f = root / "sessions" / f"rollout-x-{TID}.jsonl"
    f.parent.mkdir(parents=True)
    lines = [(l + "\n").encode() for l in _lines()]
    table = load_pricing_table()
    conn = open_db(tmp_path / "cold" / "argus.db")
    try:
        repo = Repository(conn)
        f.write_bytes(b"".join(lines[:14]))  # stops inside task U2
        ingest_file(CodexAdapter(root), f, repo, table)
        with f.open("ab") as fh:
            fh.write(b"".join(lines[14:]))
        ingest_file(CodexAdapter(root), f, repo, table)  # fresh adapter
        assert _snapshot(repo) == one_pass
    finally:
        conn.close()


def test_offset_zero_reread_is_stable(tmp_path: Path, one_pass: dict) -> None:
    root = tmp_path / "rr" / ".codex"
    f = root / "sessions" / f"rollout-x-{TID}.jsonl"
    f.parent.mkdir(parents=True)
    f.write_bytes(_whole())
    adapter, table = CodexAdapter(root), load_pricing_table()
    conn = open_db(tmp_path / "rr" / "argus.db")
    try:
        repo = Repository(conn)
        ingest_file(adapter, f, repo, table)
        repo.set_file_offset(str(f), 0)
        ingest_file(adapter, f, repo, table)
        assert _snapshot(repo) == one_pass
    finally:
        conn.close()


def test_partial_first_line_then_complete(tmp_path: Path, one_pass: dict) -> None:
    """Review focus #1 at the collector level: a tick that sees only part of the
    header line creates nothing and loses nothing."""
    data = _whole()
    assert _ingest(tmp_path, "head", [data[:25], data[25:]]) == one_pass
