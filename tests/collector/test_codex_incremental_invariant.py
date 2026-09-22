"""Codex: chunked ingest must equal one-pass ingest (H1, Codex adapter).

REGRESSION: turn ``sequence`` restarted at 0 in every tick and a call's
``turn_index`` was its position within the tick, so the timeline order depended
on how the watcher happened to chunk the file; and a tool output landing after
the tick's last ``token_count`` (held back into the next tick) lost its error
flag, because the next tick no longer holds the call.

The write order used here (function_call -> token_count -> function_call_output)
is inferred, not observed: no real Codex rollouts with model responses were
available.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from argus.adapters.codex.adapter import CodexAdapter
from argus.collector.pipeline import ingest_file
from argus.pricing.load import load_pricing_table
from argus.store.db import open_db
from argus.store.repository import Repository

THREAD = "019a0000-0000-7000-8000-00000000abcd"


def env(kind: str, payload: dict, ts: str) -> dict:
    return {"timestamp": ts, "type": kind, "payload": payload}


def usage(inp: int, out: int) -> dict:
    return {"input_tokens": inp, "cached_input_tokens": 0, "output_tokens": out,
            "reasoning_output_tokens": 0, "total_tokens": inp + out}


def _lines() -> list[str]:
    u1, u2, u3 = usage(100, 10), usage(200, 20), usage(300, 30)
    t2 = {k: u1[k] + u2[k] for k in u1}
    t3 = {k: t2[k] + u3[k] for k in u1}
    rows = [
        env("session_meta", {"id": THREAD, "timestamp": "2026-09-01T10:00:00.000Z", "cwd": "/proj",
                             "originator": "codex_cli_rs", "cli_version": "0.155.0", "source": "cli",
                             "model_provider": "openai"}, "2026-09-01T10:00:00.000Z"),
        env("turn_context", {"cwd": "/proj", "model": "gpt-5.5", "effort": "medium"}, "2026-09-01T10:00:01.000Z"),
        env("response_item", {"type": "message", "role": "user",
                              "content": [{"type": "input_text", "text": "go"}]}, "2026-09-01T10:00:02.000Z"),
        env("response_item", {"type": "function_call", "name": "exec", "arguments": "{}", "call_id": "c1"},
            "2026-09-01T10:00:03.000Z"),
        env("event_msg", {"type": "token_count", "info": {"total_token_usage": u1, "last_token_usage": u1}},
            "2026-09-01T10:00:04.000Z"),
        env("response_item", {"type": "function_call_output", "call_id": "c1",
                              "output": "Exit code: 1\nboom"}, "2026-09-01T10:00:09.000Z"),
        env("response_item", {"type": "function_call", "name": "exec", "arguments": "{}", "call_id": "c2"},
            "2026-09-01T10:00:10.000Z"),
        env("event_msg", {"type": "token_count", "info": {"total_token_usage": t2, "last_token_usage": u2}},
            "2026-09-01T10:00:11.000Z"),
        env("response_item", {"type": "function_call_output", "call_id": "c2", "output": "Exit code: 0\nok"},
            "2026-09-01T10:00:15.000Z"),
        env("response_item", {"type": "message", "role": "assistant",
                              "content": [{"type": "output_text", "text": "done"}]}, "2026-09-01T10:00:16.000Z"),
        env("event_msg", {"type": "token_count", "info": {"total_token_usage": t3, "last_token_usage": u3}},
            "2026-09-01T10:00:17.000Z"),
    ]
    return [json.dumps(r) for r in rows]


def _snapshot(repo: Repository) -> dict:
    sid = f"codex:{THREAD}"
    s = dict(repo.db.execute("SELECT * FROM sessions WHERE id = ?", (sid,)).fetchone())
    s.pop("computed_at")
    turns = [dict(r) for r in repo.db.execute("SELECT * FROM turns WHERE session_id = ? ORDER BY id", (sid,))]
    calls = [dict(r) for r in repo.db.execute("SELECT * FROM tool_calls WHERE session_id = ? ORDER BY id", (sid,))]
    return {"session": s, "turns": turns, "calls": calls}


def _ingest(tmp_path: Path, name: str, chunks: list[bytes]) -> dict:
    day = tmp_path / name / ".codex" / "sessions" / "2026" / "09" / "01"
    day.mkdir(parents=True)
    f = day / f"rollout-2026-09-01T10-00-00-{THREAD}.jsonl"
    f.write_bytes(b"")
    adapter = CodexAdapter(tmp_path / name / ".codex")
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
    return ("\n".join(_lines()) + "\n").encode("utf-8")


@pytest.fixture
def one_pass(tmp_path: Path) -> dict:
    return _ingest(tmp_path, "whole", [_whole()])


def test_reference_values(one_pass: dict) -> None:
    seqs = [t["sequence"] for t in sorted(one_pass["turns"], key=lambda t: t["timestamp"])]
    assert len(seqs) == 3 and seqs == sorted(seqs) and len(set(seqs)) == 3
    errs = {c["id"].rsplit(":", 1)[1]: c["is_error"] for c in one_pass["calls"]}
    assert errs == {"c1": 1, "c2": 0}


def test_line_by_line_equals_one_pass(tmp_path: Path, one_pass: dict) -> None:
    chunks = [(ln + "\n").encode("utf-8") for ln in _lines()]
    assert _ingest(tmp_path, "lines", chunks) == one_pass


@pytest.mark.parametrize("step", [9, 101])
def test_byte_chunks_equal_one_pass(tmp_path: Path, one_pass: dict, step: int) -> None:
    data = _whole()
    assert _ingest(tmp_path, f"b{step}", [data[i:i + step] for i in range(0, len(data), step)]) == one_pass
