"""Pure interpretation of single Codex records (shapes from real rollouts,
cli 0.159.2, profiled 2026-10-04)."""
from __future__ import annotations

from argus.adapters.codex.lines import Record
from argus.adapters.codex.records import (
    call_from_record,
    legacy_total_of,
    legacy_usage,
    segments_from_record,
    tokens_of,
    usage_record,
)


def R(type_, payload, offset=0, ts="2026-10-03T20:00:00.000Z"):
    return Record(offset=offset, ordinal=None, timestamp=ts, type=type_, payload=payload)


def item(item_payload):
    return R("event_msg", {"type": "item_completed", "thread_id": "T", "turn_id": "U", "item": item_payload})


def test_tokens_cached_is_a_subset_of_input() -> None:
    t = tokens_of({"input_tokens": 1000, "cached_input_tokens": 800, "cache_write_input_tokens": 50,
                   "output_tokens": 30, "reasoning_output_tokens": 7, "total_tokens": 1030})
    assert t == {"fresh_input_tokens": 150, "cache_read_tokens": 800, "cache_write_tokens": 50,
                 "output_tokens": 30, "reasoning_output_tokens": 7}
    assert tokens_of({"input_tokens": "x"})["fresh_input_tokens"] == 0


def test_usage_record_keyed_by_response_id() -> None:
    rec = R("token_usage_record", {"response_id": "resp_1", "turn_id": "U",
                                   "usage": {"input_tokens": 10, "output_tokens": 2}})
    assert usage_record(rec) == ("resp_1", tokens_of({"input_tokens": 10, "output_tokens": 2}))
    assert usage_record(R("token_usage_record", {"usage": {}})) is None


def test_legacy_token_count() -> None:
    p = {"type": "token_count", "info": {"last_token_usage": {"input_tokens": 5, "output_tokens": 1},
                                         "total_token_usage": {"total_tokens": 6}}}
    assert legacy_usage(R("event_msg", p))["output_tokens"] == 1
    assert legacy_total_of(p) == 6
    assert legacy_usage(R("event_msg", {"type": "token_count", "info": None})) is None


def test_exec_and_wait_wrappers_are_not_tools_but_spawn_is() -> None:
    assert call_from_record(R("response_item", {"type": "custom_tool_call", "name": "exec",
                                                "call_id": "c1", "input": "x"})) is None
    assert call_from_record(R("response_item", {"type": "function_call", "name": "wait",
                                                "call_id": "c2", "arguments": "{}"})) is None
    spawn = call_from_record(R("response_item", {
        "type": "function_call", "namespace": "collaboration", "name": "spawn_agent", "call_id": "c3",
        "arguments": '{"task_name": "rollout_subagents", "message": "hi"}'}))
    assert (spawn.tool_name, spawn.subagent_type, spawn.side) == ("spawn_agent", "rollout_subagents", "request")


def test_mcp_function_call_is_counted_from_its_item_only() -> None:
    assert call_from_record(R("response_item", {"type": "function_call", "namespace": "mcp__cua_repl",
                                                "name": "js", "call_id": "c4", "arguments": "{}"})) is None
    c = call_from_record(item({"type": "McpToolCall", "id": "i1", "server": "cua_repl", "tool": "js",
                               "status": "failed", "result": {"isError": True, "content": []}}))
    assert (c.tool_name, c.is_error, c.side) == ("mcp__cua_repl__js", 1, "item")


def test_command_and_file_change_items() -> None:
    ok = call_from_record(item({"type": "CommandExecution", "id": "i2", "command": ["ls"],
                                "status": "completed", "exit_code": 0}))
    bad = call_from_record(item({"type": "CommandExecution", "id": "i3", "command": ["x"],
                                 "status": "failed", "exit_code": 1}))
    fc = call_from_record(item({"type": "FileChange", "id": "i4", "changes": {}, "status": "completed"}))
    ws = call_from_record(item({"type": "Extension", "kind": "web.search", "id": "i5", "query": "q"}))
    assert (ok.tool_name, ok.is_error, bad.is_error) == ("shell", 0, 1)
    assert fc.tool_name == "apply_patch" and ws.tool_name == "web.search"
    assert call_from_record(item({"type": "Reasoning", "id": "i6"})) is None
    assert call_from_record(item({"type": "SubAgentActivity", "id": "i7", "kind": "started"})) is None


def test_segments_never_include_reasoning_or_injected_messages() -> None:
    assert segments_from_record(R("response_item", {"type": "reasoning", "id": "r", "summary": [],
                                                    "encrypted_content": "x"}), set()) == []
    assert segments_from_record(R("response_item", {"type": "message", "role": "developer", "id": "m",
                                                    "content": [{"type": "input_text", "text": "x"}]}), set()) == []
    assert segments_from_record(R("response_item", {"type": "message", "role": "user", "id": "m",
                                                    "content": [{"type": "input_text", "text": "x"}]}), set()) == []
    assert segments_from_record(item({"type": "Reasoning", "id": "i", "summary_text": ["s"]}), set()) == []


def test_segments_user_assistant_tool_output_and_inbound_agent_message() -> None:
    u = segments_from_record(item({"type": "UserMessage", "id": "u1",
                                   "content": [{"type": "text", "text": "fix it"}]}), set())
    a = segments_from_record(item({"type": "AgentMessage", "id": "a1",
                                   "content": [{"type": "Text", "text": "done"}]}), set())
    o = segments_from_record(item({"type": "CommandExecution", "id": "i2", "command": ["ls"],
                                   "aggregated_output": "a.txt"}), set())
    m = segments_from_record(R("response_item", {"type": "agent_message", "id": "am",
                                                 "content": [{"type": "input_text", "text": "task"}]}), set())
    assert [(s.role, s.text, s.uid_suffix) for s in u] == [("user", "fix it", "u1:0")]
    assert [(s.role, s.text) for s in a] == [("assistant", "done")]
    assert [(s.role, s.text, s.tool_use_id) for s in o] == [("tool_result", "a.txt", "i2")]
    assert [(s.role, s.text) for s in m] == [("user", "task")]


def test_call_output_only_for_counted_calls() -> None:
    out = R("response_item", {"type": "function_call_output", "call_id": "c3",
                              "output": [{"type": "input_text", "text": "spawned"}]})
    assert segments_from_record(out, set()) == []  # e.g. an exec wrapper's output
    [s] = segments_from_record(out, {"c3"})
    assert (s.role, s.text, s.tool_use_id) == ("tool_result", "spawned", "c3")
