"""Codex turns: one per token_count, usage mapping, model context, replay skips."""
from __future__ import annotations

import json
from pathlib import Path

from argus.adapters.codex.discover import MetaPeek
from argus.adapters.codex.extract_turns import extract_turns
from argus.adapters.codex.lines import read_lines
from argus.adapters.codex.state import TickState, rebuild_state

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


def jsonl(lines: list[dict]) -> str:
    return "\n".join(json.dumps(l) for l in lines) + "\n"


def _state(fmt: str = "envelope", **meta_kw) -> TickState:
    m = MetaPeek(format=fmt, thread_id=THREAD, started_at=TS0, **meta_kw)
    return TickState(offset=0, fmt=fmt, meta=m)


def _lines(tmp_path: Path, items: list[dict], fmt: str = "envelope"):
    p = tmp_path / "r.jsonl"
    p.write_text(jsonl(items), encoding="utf-8", newline="\n")
    return read_lines(p, 0, fmt).lines


def test_last_token_usage_maps_to_argus_fields(tmp_path):
    u = usage(1000, 300, 50, 20, cw=100)
    lines = _lines(tmp_path, [meta(), ctx("gpt-5.5"), tc(u, u)])
    turns, bounds = extract_turns(lines, _state())
    assert len(turns) == 1
    t = turns[0]
    assert t.fresh_input_tokens == 600 and t.cache_read_tokens == 300
    assert t.cache_write_tokens == 100 and t.output_tokens == 50
    assert t.model == "gpt-5.5" and t.model_raw == "gpt-5.5"
    assert t.metadata["reasoning_output_tokens"] == 20 and t.metadata["effort"] == "medium"
    assert t.native_turn_id == f"tc@{lines[2].offset}" and bounds == [lines[2].offset]
    assert t.cache_write_5m_tokens is None and t.cache_write_1h_tokens is None


def test_unchanged_total_is_deduped_and_zero_usage_skipped(tmp_path):
    u = usage(10, out=5)
    lines = _lines(tmp_path, [meta(), ctx(), tc(u, u), tc(u, u), tc(usage(0), usage(0))])
    turns, _ = extract_turns(lines, _state())
    assert len(turns) == 1


def test_fallback_to_total_difference_when_last_missing(tmp_path):
    lines = _lines(tmp_path, [meta(), ctx(), tc(usage(10, out=5), None), tc(usage(30, 5, 12), None)])
    turns, _ = extract_turns(lines, _state())
    assert [(t.fresh_input_tokens, t.cache_read_tokens, t.output_tokens) for t in turns] == [(10, 0, 5), (15, 5, 7)]


def test_null_cached_and_reasoning_are_tolerated(tmp_path):
    u = {"input_tokens": 10, "cached_input_tokens": None, "output_tokens": 5, "reasoning_output_tokens": None, "total_tokens": 15}
    lines = _lines(tmp_path, [meta(), ctx(), tc(u, u)])
    turns, _ = extract_turns(lines, _state())
    assert turns[0].fresh_input_tokens == 10 and turns[0].cache_read_tokens == 0
    assert turns[0].metadata["reasoning_output_tokens"] == 0


def test_model_unknown_without_turn_context_and_from_thread_settings(tmp_path):
    u = usage(10, out=1)
    turns, _ = extract_turns(_lines(tmp_path, [meta(), tc(u, u)]), _state())
    assert turns[0].model == "unknown" and turns[0].model_raw == "unknown"
    settings = env("event_msg", {"type": "thread_settings_applied", "thread_settings": {"model": "openai/gpt-5.4", "service_tier": "fast"}})
    turns, _ = extract_turns(_lines(tmp_path, [meta(), settings, tc(u, u)]), _state())
    assert turns[0].model == "gpt-5.4" and turns[0].metadata["service_tier"] == "fast"


def test_state_carries_across_ticks_and_rebuilds_from_disk(tmp_path):
    p = tmp_path / "r.jsonl"
    first = [meta(), ctx("gpt-5.3-codex"), tc(usage(10, out=1), usage(10, out=1))]
    p.write_text(jsonl(first), encoding="utf-8", newline="\n")
    off = p.stat().st_size
    p.write_text(jsonl(first + [tc(usage(30, out=3), None)]), encoding="utf-8", newline="\n")
    m = MetaPeek(format="envelope", thread_id=THREAD, started_at=TS0)
    state = rebuild_state(p, "envelope", m, off)
    assert state.model_raw == "gpt-5.3-codex"
    assert state.prev_total == {"input_tokens": 10, "cached_input_tokens": 0, "cache_write_input_tokens": 0, "output_tokens": 1, "reasoning_output_tokens": 0}
    turns, _ = extract_turns(read_lines(p, off, "envelope").lines, state)
    assert turns[0].model == "gpt-5.3-codex" and turns[0].fresh_input_tokens == 20 and turns[0].output_tokens == 2


def test_turn_id_from_task_started_is_recorded(tmp_path):
    started = env("event_msg", {"type": "task_started", "turn_id": "turn-1", "model_context_window": 272000})
    u = usage(5, out=1)
    turns, _ = extract_turns(_lines(tmp_path, [meta(), ctx(), started, tc(u, u)]), _state())
    assert turns[0].metadata["turn_id"] == "turn-1"


def test_pending_tool_calls_are_counted_on_the_closing_turn(tmp_path):
    u1, u2 = usage(5, out=1), usage(15, out=3)
    lines = _lines(tmp_path, [meta(), ctx(), fc("shell", {}, "c1"), fc("shell", {}, "c2"), tc(u1, u1), tc(u2, usage(10, out=2))])
    turns, _ = extract_turns(lines, _state())
    assert [t.tool_calls_count for t in turns] == [2, 0]


def test_legacy_raw_emits_zero_token_turn_per_assistant_message(tmp_path):
    p = tmp_path / "r.jsonl"
    p.write_text(
        '{"id":"%s","timestamp":"2026-08-01T10:00:00.000Z","instructions":null}\n' % THREAD
        + '{"type":"message","role":"user","content":[{"type":"input_text","text":"do it"}]}\n'
        + '{"type":"message","role":"assistant","content":[{"type":"output_text","text":"ok"}]}\n'
        + '{"type":"function_call","name":"shell","arguments":"{}","call_id":"c1"}\n'
        + '{"type":"message","role":"assistant","content":[{"type":"output_text","text":"done"}]}\n',
        encoding="utf-8",
    )
    lines = read_lines(p, 0, "legacy").lines
    state = TickState(offset=0, fmt="legacy", meta=MetaPeek(format="legacy", thread_id=THREAD, started_at="2026-08-01T10:00:00.000Z"))
    turns, bounds = extract_turns(lines, state)
    assert len(turns) == 2 and all(t.fresh_input_tokens == 0 and t.model == "unknown" for t in turns)
    assert turns[0].timestamp == "2026-08-01T10:00:00.000Z" and turns[0].native_turn_id.startswith("msg@")
    assert [t.tool_calls_count for t in turns] == [0, 1] and len(bounds) == 2


def test_legacy_child_burst_skips_replayed_token_counts(tmp_path):
    """Legacy spawned children copy the parent's rollout (timestamps rewritten
    to spawn time) before their own records. Those replayed token_counts must
    not become turns; the child's own later usage must."""
    u1, u2 = usage(100, out=10), usage(200, out=20)
    replay = [meta(CHILD, parent_thread_id=THREAD), ctx(), tc(u1, u1), tc(u2, usage(100, out=10))]
    own_ts = "2026-09-01T10:00:05.000Z"
    own = [ctx(ts=own_ts), tc(usage(50, out=5), usage(50, out=5), ts=own_ts)]
    turns, _ = extract_turns(_lines(tmp_path, replay + own), _state(parent_thread_id=THREAD))
    assert [t.fresh_input_tokens for t in turns] == [50]


def test_paginated_ordinal_cutoff_skips_inherited_lines(tmp_path):
    items = [
        meta(CHILD, parent_thread_id=THREAD, subagent_history_start_ordinal=3),
        env("turn_context", {"model": "gpt-5.5"}, ordinal=1),
        env("event_msg", {"type": "token_count", "info": {"total_token_usage": usage(9, out=9), "last_token_usage": usage(9, out=9)}}, ordinal=2),
        env("event_msg", {"type": "token_count", "info": {"total_token_usage": usage(19, out=19), "last_token_usage": usage(10, out=10)}}, ordinal=3),
    ]
    turns, _ = extract_turns(_lines(tmp_path, items), _state(parent_thread_id=THREAD, skip_before_ordinal=3))
    assert [t.output_tokens for t in turns] == [10]
