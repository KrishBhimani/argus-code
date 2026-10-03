"""One-file ingest: turns from usage records, tool-call -> turn assignment,
model per Codex turn, header, privacy."""
from __future__ import annotations

import json
from pathlib import Path

from argus.adapters.codex.ingest_file import ingest_codex_file
from argus.adapters.codex.state import ContextCache


def E(o, t, p, s=0):
    return json.dumps({"timestamp": f"2026-10-03T20:{o // 60:02d}:{o % 60:02d}.{s:03d}Z",
                       "ordinal": o, "type": t, "payload": p})


def tur(o, rid, tid, inp=100, cached=60, out=10):
    return E(o, "token_usage_record", {"response_id": rid, "turn_id": tid, "thread_id": "T",
                                       "usage": {"input_tokens": inp, "cached_input_tokens": cached,
                                                 "cache_write_input_tokens": 0, "output_tokens": out,
                                                 "reasoning_output_tokens": 3, "total_tokens": inp + out}})


def item(o, it, tid="U1"):
    return E(o, "event_msg", {"type": "item_completed", "thread_id": "T", "turn_id": tid, "item": it})


def session() -> list[str]:
    return [
        E(0, "session_meta", {"id": "T", "session_id": "T", "cwd": "C:\\proj", "cli_version": "0.159.2",
                              "originator": "Codex Desktop", "source": "vscode", "thread_source": "user",
                              "model_provider": "openai", "git": {"branch": "main", "repository_url": "https://x"},
                              "base_instructions": {"text": "SECRET-INSTRUCTIONS"}}),
        E(1, "event_msg", {"type": "task_started", "turn_id": "U1"}),
        E(2, "response_item", {"type": "message", "role": "developer", "id": "d",
                               "content": [{"type": "input_text", "text": "SECRET-DEV"}]}),
        E(3, "turn_context", {"turn_id": "U1", "model": "gpt-6.1-sol", "effort": "medium"}),
        item(4, {"type": "UserMessage", "id": "um", "content": [{"type": "text", "text": "list files"}]}),
        E(5, "response_item", {"type": "custom_tool_call", "name": "exec", "call_id": "x1", "input": "ls",
                               "internal_chat_message_metadata_passthrough": {"turn_id": "U1"}}),
        tur(6, "r1", "U1"),
        item(7, {"type": "CommandExecution", "id": "cmd1", "command": ["ls"], "status": "failed",
                 "exit_code": 2, "aggregated_output": "no such dir"}),
        E(8, "response_item", {"type": "reasoning", "id": "rs", "encrypted_content": "SECRET-REASONING"}),
        E(9, "response_item", {"type": "function_call", "namespace": "collaboration", "name": "spawn_agent",
                               "call_id": "sp1", "arguments": '{"task_name": "helper", "message": "go"}',
                               "internal_chat_message_metadata_passthrough": {"turn_id": "U1"}}),
        tur(10, "r2", "U1", out=20),
        item(11, {"type": "AgentMessage", "id": "am", "content": [{"type": "Text", "text": "done"}]}),
        E(12, "event_msg", {"type": "task_complete", "turn_id": "U1"}),
        E(13, "response_item", {"type": "function_call", "name": "untrusted_input", "call_id": "ui",
                                "arguments": "{}"}),
    ]


def _write(tmp_path: Path, lines: list[str]) -> Path:
    f = tmp_path / "sessions" / "rollout-T.jsonl"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text("".join(l + "\n" for l in lines), encoding="utf-8")
    return f


def test_turns_tokens_model_and_header(tmp_path: Path) -> None:
    f = _write(tmp_path, session())
    r, end = ingest_codex_file(f, 0, ContextCache())
    assert end == f.stat().st_size
    assert [t.native_turn_id for t in r.turns] == ["r1", "r2"]
    t1 = r.turns[0]
    assert (t1.model, t1.fresh_input_tokens, t1.cache_read_tokens, t1.output_tokens) == ("gpt-6.1-sol", 40, 60, 10)
    assert t1.metadata == {"codex_turn_id": "U1", "effort": "medium", "reasoning_output_tokens": 3}
    h = r.header
    assert (h.native_session_id, h.agent, h.agent_version, h.project_path) == ("T", "codex", "0.159.2", "C:\\proj")
    assert h.started_at == t1.timestamp and h.ended_at == r.turns[1].timestamp
    assert h.metadata == {"originator": "Codex Desktop", "source": "vscode", "thread_source": "user",
                          "model_provider": "openai", "git_branch": "main"}


def test_calls_assigned_to_the_right_response(tmp_path: Path) -> None:
    f = _write(tmp_path, session())
    r, _ = ingest_codex_file(f, 0, ContextCache())
    calls = {c.tool_use_id: c for c in r.tool_calls}
    seq = {t.native_turn_id: t.sequence for t in r.turns}
    # item after r1 -> r1; spawn request before r2 -> r2; call outside any task -> unassigned
    assert (calls["cmd1"].native_turn_id, calls["cmd1"].turn_index) == ("r1", seq["r1"])
    assert (calls["sp1"].native_turn_id, calls["sp1"].subagent_type) == ("r2", "helper")
    assert calls["ui"].native_turn_id == "" and calls["ui"].turn_index > seq["r2"]
    assert "x1" not in calls  # exec wrapper
    assert {t.native_turn_id: t.tool_calls_count for t in r.turns} == {"r1": 1, "r2": 1}
    assert calls["cmd1"].is_error == 1 and r.tool_error_ids == ["cmd1"]


def test_transcript_has_no_secrets(tmp_path: Path) -> None:
    f = _write(tmp_path, session())
    r, _ = ingest_codex_file(f, 0, ContextCache())
    texts = " ".join(s.text for s in r.segments)
    assert [s.role for s in r.segments] == ["user", "tool_result", "assistant"]
    assert "SECRET" not in texts


def test_legacy_token_count_only_file(tmp_path: Path) -> None:
    """Older Codex wrote no token_usage_record; token_count is the fallback,
    and a repeated (non-advancing) total is not counted twice."""
    def tc(o, last, total):
        return E(o, "event_msg", {"type": "token_count", "info": {
            "last_token_usage": {"input_tokens": last, "output_tokens": 1},
            "total_token_usage": {"total_tokens": total}}})
    lines = [E(0, "session_meta", {"id": "L", "cwd": "/p"}),
             E(1, "turn_context", {"model": "gpt-5", "turn_id": "U"}),
             tc(2, 10, 11), tc(3, 10, 11), tc(4, 20, 32),
             E(5, "event_msg", {"type": "token_count", "info": None})]
    r, _ = ingest_codex_file(_write(tmp_path, lines), 0, ContextCache())
    assert [(t.model, t.fresh_input_tokens) for t in r.turns] == [("gpt-5", 10), ("gpt-5", 20)]


def test_usage_records_win_over_token_count(tmp_path: Path) -> None:
    lines = session()[:7] + [E(20, "event_msg", {"type": "token_count", "info": {
        "last_token_usage": {"input_tokens": 999, "output_tokens": 9},
        "total_token_usage": {"total_tokens": 99999}}})]
    r, _ = ingest_codex_file(_write(tmp_path, lines), 0, ContextCache())
    assert [t.native_turn_id for t in r.turns] == ["r1"]


def test_not_a_rollout_yields_nothing(tmp_path: Path) -> None:
    r, end = ingest_codex_file(_write(tmp_path, ['{"id":"legacy","instructions":""}']), 0, ContextCache())
    assert r.turns == [] and r.tool_calls == [] and end > 0
