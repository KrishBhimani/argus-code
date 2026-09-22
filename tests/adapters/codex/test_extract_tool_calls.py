"""Codex tool calls: attribution to turns, MCP naming, spawn_agent type, error signals."""
from __future__ import annotations

import json
from pathlib import Path

from argus.adapters.codex.discover import MetaPeek
from argus.adapters.codex.extract_tool_calls import extract_tool_calls
from argus.adapters.codex.extract_turns import extract_turns
from argus.adapters.codex.lines import read_lines
from argus.adapters.codex.state import TickState

TS0 = "2026-09-01T10:00:00.000Z"
THREAD = "019a0000-0000-7000-8000-000000000001"
CHILD = "019a0000-0000-7000-8000-000000000002"


def env(kind: str, payload: dict, ts: str = TS0, ordinal: int | None = None) -> dict:
    line = {"timestamp": ts, "type": kind, "payload": payload}
    if ordinal is not None:
        line["ordinal"] = ordinal
    return line


def meta(thread: str = THREAD, ts: str = TS0, **extra) -> dict:
    payload = {
        "id": thread,
        "timestamp": ts,
        "cwd": "C:\\proj",
        "originator": "codex_cli_rs",
        "cli_version": "0.155.0",
        "source": "cli",
        "model_provider": "openai",
    }
    payload.update(extra)
    return env("session_meta", payload, ts)


def ctx(model: str = "gpt-5.5", ts: str = TS0, **extra) -> dict:
    payload = {"cwd": "C:\\proj", "approval_policy": "on-request", "model": model, "effort": "medium", "summary": "auto"}
    payload.update(extra)
    return env("turn_context", payload, ts)


def tc(total: dict, last: dict | None, ts: str = TS0) -> dict:
    info = {"total_token_usage": total}
    if last is not None:
        info["last_token_usage"] = last
    return env("event_msg", {"type": "token_count", "info": info, "rate_limits": None}, ts)


def usage(inp: int, cached: int = 0, out: int = 0, reasoning: int = 0, cw: int | None = None) -> dict:
    u = {"input_tokens": inp, "cached_input_tokens": cached, "output_tokens": out, "reasoning_output_tokens": reasoning, "total_tokens": inp + out}
    if cw is not None:
        u["cache_write_input_tokens"] = cw
    return u


def fc(name: str, args: dict, call_id: str, ts: str = TS0, namespace: str | None = None) -> dict:
    payload = {"type": "function_call", "name": name, "arguments": json.dumps(args), "call_id": call_id}
    if namespace:
        payload["namespace"] = namespace
    return env("response_item", payload, ts)


def fco(call_id: str, output, ts: str = TS0) -> dict:
    return env("response_item", {"type": "function_call_output", "call_id": call_id, "output": output}, ts)


def jsonl(lines: list[dict]) -> str:
    return "\n".join(json.dumps(l) for l in lines) + "\n"


def _run(tmp_path: Path, items: list[dict], **meta_kw):
    p = tmp_path / "r.jsonl"
    p.write_text(jsonl(items), encoding="utf-8", newline="\n")
    lines = read_lines(p, 0, "envelope").lines
    m = MetaPeek(format="envelope", thread_id=THREAD, started_at=TS0, **meta_kw)
    state = TickState(offset=0, fmt="envelope", meta=m)
    turns, bounds = extract_turns(lines, state)
    return turns, extract_tool_calls(lines, turns, bounds, m.skip_before_ordinal)


def test_calls_are_attributed_to_the_turn_that_follows(tmp_path):
    t1, t2 = usage(10, out=1), usage(20, out=2)
    turns, calls = _run(tmp_path, [
        meta(), ctx(),
        fc("shell", {"command": ["ls"]}, "c1"), fco("c1", "ok"),
        tc(t1, t1),
        fc("apply_patch", {"input": "x"}, "c2"),
        env("response_item", {"type": "custom_tool_call", "name": "apply_patch", "input": "*** Begin Patch", "call_id": "c3"}),
        env("response_item", {"type": "local_shell_call", "call_id": "c4", "status": "completed", "action": {"type": "exec", "command": ["bash", "-lc", "pwd"]}}),
        tc(t2, usage(10, out=1)),
    ])
    assert [c.tool_use_id for c in calls] == ["c1", "c2", "c3", "c4"]
    # turn_index is the owning turn's file-wide sequence (its token_count's offset).
    assert [c.turn_index for c in calls] == [turns[0].sequence] + [turns[1].sequence] * 3
    assert turns[0].sequence < turns[1].sequence
    assert [c.block_index for c in calls] == [0, 0, 1, 2]
    assert calls[0].native_turn_id == turns[0].native_turn_id
    assert calls[1].native_turn_id == turns[1].native_turn_id
    assert calls[3].tool_name == "local_shell"
    assert calls[0].input_size == len(json.dumps({"command": ["ls"]}))
    assert [c.is_error for c in calls] == [0, 0, 0, 0]
    assert turns[0].tool_calls_count == 1 and turns[1].tool_calls_count == 3


def test_mcp_namespace_prefixing(tmp_path):
    u = usage(1, out=1)
    _, calls = _run(tmp_path, [
        meta(), ctx(),
        fc("mcp__github__list_prs", {}, "c1", namespace="mcp__github"),
        fc("search", {}, "c2", namespace="mcp__docs"),
        fc("shell", {}, "c3", namespace="functions"),
        tc(u, u),
    ])
    assert [c.tool_name for c in calls] == ["mcp__github__list_prs", "mcp__docs__search", "shell"]


def test_spawn_agent_type_becomes_subagent_type(tmp_path):
    u = usage(1, out=1)
    _, calls = _run(tmp_path, [
        meta(), ctx(),
        fc("spawn_agent", {"message": "go", "agent_type": "reviewer"}, "c1", namespace="collaboration"),
        fc("spawn_agent", {"message": "go"}, "c2"),
        tc(u, u),
    ])
    assert calls[0].subagent_type == "reviewer" and calls[1].subagent_type is None
    assert calls[0].tool_name == "collaboration__spawn_agent"


def test_error_signals(tmp_path):
    u = usage(1, out=1)
    _, calls = _run(tmp_path, [
        meta(), ctx(),
        fc("mcp__x__y", {}, "m1"),
        env("event_msg", {"type": "mcp_tool_call_end", "call_id": "m1", "invocation": {"server": "x", "tool": "y"}, "result": {"Err": "boom"}}),
        fc("apply_patch", {}, "p1"),
        env("event_msg", {"type": "patch_apply_end", "call_id": "p1", "success": False}),
        fc("shell", {}, "s1"), fco("s1", '{"output":"x","metadata":{"exit_code":2,"duration_seconds":0.1}}'),
        fc("shell", {}, "s2"), fco("s2", "Exit code: 127\nOutput:\nnot found"),
        fc("shell", {}, "s3"), fco("s3", [{"type": "input_text", "text": "Exit code: 0\nok"}]),
        fc("exec_command", {}, "s4"),
        env("event_msg", {"type": "item_completed", "item": {"type": "CommandExecution", "id": "s4", "exit_code": 1}}),
        fc("mcp__x__ok", {}, "m2"),
        env("event_msg", {"type": "mcp_tool_call_end", "call_id": "m2", "invocation": {"server": "x", "tool": "ok"}, "result": {"Ok": {}}}),
        tc(u, u),
    ])
    by_id = {c.tool_use_id: c.is_error for c in calls}
    assert by_id == {"m1": 1, "p1": 1, "s1": 1, "s2": 1, "s3": 0, "s4": 1, "m2": 0}


def test_trailing_calls_without_a_closing_turn_are_not_emitted(tmp_path):
    u = usage(1, out=1)
    _, calls = _run(tmp_path, [meta(), ctx(), tc(u, u), fc("shell", {}, "late")])
    assert calls == []


def test_inherited_prefix_is_skipped(tmp_path):
    items = [
        meta(CHILD, parent_thread_id=THREAD, subagent_history_start_ordinal=2),
        env("response_item", {"type": "function_call", "name": "shell", "arguments": "{}", "call_id": "old"}, ordinal=1),
        env("response_item", {"type": "function_call", "name": "shell", "arguments": "{}", "call_id": "new"}, ordinal=2),
        env("event_msg", {"type": "token_count", "info": {"total_token_usage": usage(1, out=1), "last_token_usage": usage(1, out=1)}}, ordinal=3),
    ]
    _, calls = _run(tmp_path, items, parent_thread_id=THREAD, skip_before_ordinal=2)
    assert [c.tool_use_id for c in calls] == ["new"]
