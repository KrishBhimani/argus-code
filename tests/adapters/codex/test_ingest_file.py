"""Codex ingest_file: header, holdback across ticks, state rebuild, parse errors."""
from __future__ import annotations

import json
from pathlib import Path

from argus.adapters.codex.discover import peek_meta
from argus.adapters.codex.ingest_file import ingest_codex_file
from argus.store.repository import normalize_project_path

TS0 = "2026-09-01T10:00:00.000Z"
THREAD = "019a0000-0000-7000-8000-000000000001"
CHILD = "019a0000-0000-7000-8000-000000000002"
NAME = f"rollout-2026-09-01T10-00-00-{THREAD}.jsonl"


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


def msg(role: str, text: str, ts: str = TS0) -> dict:
    ctype = "output_text" if role == "assistant" else "input_text"
    return env("response_item", {"type": "message", "role": role, "content": [{"type": ctype, "text": text}]}, ts)


def jsonl(lines: list[dict]) -> str:
    return "\n".join(json.dumps(l) for l in lines) + "\n"


def _ingest(p: Path, off: int = 0, cache=None):
    cache = {} if cache is None else cache
    return ingest_codex_file(p, off, peek_meta(p), cache), cache


def test_header_and_turns_from_fresh_file(tmp_path):
    p = tmp_path / NAME
    u = usage(100, 40, 10, cw=5)
    p.write_text(
        jsonl([meta(git={"branch": "dev"}), ctx("gpt-5.5"), fc("shell", {"command": ["ls"]}, "c1"), fco("c1", "ok"), msg("assistant", "hi"), tc(u, u, ts="2026-09-01T10:00:09.000Z")]),
        encoding="utf-8", newline="\n",
    )
    (result, off), _ = _ingest(p)
    h = result.header
    assert h.native_session_id == THREAD and h.agent == "codex" and h.agent_version == "0.155.0"
    assert h.project_path == normalize_project_path("C:\\proj")
    assert h.started_at == TS0 and h.ended_at == "2026-09-01T10:00:09.000Z"
    assert h.metadata["git_branch"] == "dev" and h.metadata["source"] == "cli" and h.metadata["codex_model"] == "gpt-5.5"
    assert len(result.turns) == 1 and result.turns[0].fresh_input_tokens == 55 and result.turns[0].tool_calls_count == 1
    assert [c.tool_name for c in result.tool_calls] == ["shell"]
    assert [s.role for s in result.segments] == ["tool_result", "assistant"]
    assert off == p.stat().st_size and result.parse_errors == []


def test_holdback_keeps_open_turn_for_next_tick(tmp_path):
    p = tmp_path / NAME
    u1 = usage(10, out=1)
    closed = [meta(), ctx(), tc(u1, u1)]
    p.write_text(jsonl(closed + [fc("shell", {}, "open"), msg("assistant", "partial")]), encoding="utf-8", newline="\n")
    (r1, off1), cache = _ingest(p)
    assert len(r1.turns) == 1 and r1.tool_calls == [] and r1.segments == []
    assert off1 == len(jsonl(closed).encode())
    u2 = usage(30, out=3)
    with p.open("a", encoding="utf-8", newline="\n") as fh:
        fh.write(jsonl([tc(u2, usage(20, out=2), ts="2026-09-01T10:00:05.000Z")]))
    (r2, off2), _ = _ingest(p, off1, cache)
    assert len(r2.turns) == 1 and r2.turns[0].fresh_input_tokens == 20 and r2.turns[0].tool_calls_count == 1
    assert [c.tool_use_id for c in r2.tool_calls] == ["open"]
    assert [s.role for s in r2.segments] == ["assistant"]
    assert off2 == p.stat().st_size and r2.header.ended_at == "2026-09-01T10:00:05.000Z"


def test_meta_only_file_consumes_header_but_creates_no_turns(tmp_path):
    p = tmp_path / NAME
    p.write_text(jsonl([meta()]), encoding="utf-8", newline="\n")
    (r, off), _ = _ingest(p)
    assert r.turns == [] and off == p.stat().st_size


def test_no_new_bytes_returns_placeholder(tmp_path):
    p = tmp_path / NAME
    u = usage(1, out=1)
    p.write_text(jsonl([meta(), ctx(), tc(u, u)]), encoding="utf-8", newline="\n")
    (r1, off), cache = _ingest(p)
    (r2, off2), _ = _ingest(p, off, cache)
    assert r2.turns == [] and off2 == off and r2.header.native_session_id == THREAD


def test_state_rebuilds_after_offset_reset_without_cache(tmp_path):
    p = tmp_path / NAME
    u = usage(10, out=1)
    p.write_text(jsonl([meta(), ctx("gpt-5.3-codex"), tc(u, u)]), encoding="utf-8", newline="\n")
    (_, off), _ = _ingest(p)
    with p.open("a", encoding="utf-8", newline="\n") as fh:
        fh.write(jsonl([tc(usage(30, out=3), None)]))
    (r, _), _ = _ingest(p, off, cache={})  # fresh process: no cache
    assert r.turns[0].model == "gpt-5.3-codex" and r.turns[0].fresh_input_tokens == 20


def test_parse_errors_are_reported_and_ingest_continues(tmp_path):
    p = tmp_path / NAME
    u = usage(1, out=1)
    p.write_text(jsonl([meta(), ctx()]) + "{broken\n" + jsonl([tc(u, u)]), encoding="utf-8", newline="\n")
    (r, _), _ = _ingest(p)
    assert len(r.turns) == 1 and len(r.parse_errors) == 1


def test_legacy_child_burst_end_to_end(tmp_path):
    p = tmp_path / f"rollout-2026-09-01T10-00-01-{CHILD}.jsonl"
    u1 = usage(100, out=10)
    own_ts = "2026-09-01T10:00:05.000Z"
    p.write_text(
        jsonl([meta(CHILD, parent_thread_id=THREAD), ctx(), tc(u1, u1), ctx(ts=own_ts), tc(usage(150, out=15), usage(50, out=5), ts=own_ts)]),
        encoding="utf-8", newline="\n",
    )
    (r, _), _ = _ingest(p)
    assert [t.output_tokens for t in r.turns] == [5]
    assert r.header.native_session_id == CHILD and r.header.metadata["parent_thread_id"] == THREAD


def test_legacy_raw_file_is_consumed_whole(tmp_path):
    p = tmp_path / f"rollout-2026-08-01T10-00-00-{THREAD}.jsonl"
    p.write_text(
        '{"id":"%s","timestamp":"2026-08-01T10:00:00.000Z","instructions":null}\n'
        '{"type":"message","role":"assistant","content":[{"type":"output_text","text":"ok"}]}\n'
        '{"type":"function_call","name":"shell","arguments":"{}","call_id":"c1"}\n' % THREAD,
        encoding="utf-8", newline="\n",
    )
    (r, off), _ = _ingest(p)
    assert len(r.turns) == 1 and r.turns[0].model == "unknown" and off == p.stat().st_size
    assert r.header.started_at == "2026-08-01T10:00:00.000Z" and r.header.agent_version is None
