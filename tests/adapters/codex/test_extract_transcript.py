"""Codex transcript segments: prompt sources, replies, reasoning, tool outputs, caps."""
from __future__ import annotations

import json

from argus.adapters.codex.extract_transcript import SEGMENT_CAP_BYTES, extract_transcript_segments
from argus.adapters.codex.lines import read_lines

TS0 = "2026-09-01T10:00:00.000Z"
THREAD = "019a0000-0000-7000-8000-000000000001"


def env(kind: str, payload: dict, ts: str = TS0, ordinal: int | None = None) -> dict:
    line = {"timestamp": ts, "type": kind, "payload": payload}
    if ordinal is not None:
        line["ordinal"] = ordinal
    return line


def meta(thread: str = THREAD, ts: str = TS0, **extra) -> dict:
    payload = {"id": thread, "timestamp": ts, "cwd": "C:\\proj", "originator": "codex_cli_rs", "cli_version": "0.155.0", "source": "cli"}
    payload.update(extra)
    return env("session_meta", payload, ts)


def msg(role: str, text: str, ts: str = TS0) -> dict:
    ctype = "output_text" if role == "assistant" else "input_text"
    return env("response_item", {"type": "message", "role": role, "content": [{"type": ctype, "text": text}]}, ts)


def user_event(text: str, ts: str = TS0) -> dict:
    return env("event_msg", {"type": "user_message", "message": text, "local_images": []}, ts)


def fco(call_id: str, output, ts: str = TS0) -> dict:
    return env("response_item", {"type": "function_call_output", "call_id": call_id, "output": output}, ts)


def jsonl(lines: list[dict]) -> str:
    return "\n".join(json.dumps(l) for l in lines) + "\n"


def _segs(tmp_path, items, skip=None):
    p = tmp_path / "r.jsonl"
    p.write_text(jsonl(items), encoding="utf-8", newline="\n")
    return extract_transcript_segments(read_lines(p, 0, "envelope").lines, TS0, skip)


def test_roles_and_sources(tmp_path):
    reasoning = env("response_item", {
        "type": "reasoning",
        "summary": [{"type": "summary_text", "text": "think A"}, {"type": "summary_text", "text": "think B"}],
        "encrypted_content": "zzz",
    })
    segs = _segs(tmp_path, [
        meta(),
        msg("user", "<environment_context>injected</environment_context>"),
        user_event("real prompt"),
        env("event_msg", {"type": "item_completed", "item": {"type": "UserMessage", "content": [{"type": "text", "text": "paginated prompt"}]}}),
        reasoning,
        msg("assistant", "answer"),
        env("event_msg", {"type": "agent_message", "message": "answer"}),
        fco("c1", "tool says hi"),
        env("response_item", {"type": "custom_tool_call_output", "call_id": "c2", "output": [{"type": "input_text", "text": "patched"}]}),
    ])
    assert [(s.role, s.text) for s in segs] == [
        ("user", "real prompt"),
        ("user", "paginated prompt"),
        ("thinking", "think A\nthink B"),
        ("assistant", "answer"),
        ("tool_result", "tool says hi"),
        ("tool_result", "patched"),
    ]
    assert segs[4].tool_use_id == "c1" and segs[5].tool_use_id == "c2" and segs[0].tool_use_id is None
    assert len({s.uid_suffix for s in segs}) == len(segs)
    assert all(s.timestamp == TS0 for s in segs)


def test_cap_and_blank_skip(tmp_path):
    segs = _segs(tmp_path, [meta(), msg("assistant", "   "), msg("assistant", "x" * (SEGMENT_CAP_BYTES + 10))])
    assert len(segs) == 1
    assert len(segs[0].text.encode()) <= SEGMENT_CAP_BYTES + 3 and segs[0].text.endswith("\u2026")


def test_legacy_lines_use_fallback_timestamp(tmp_path):
    p = tmp_path / "r.jsonl"
    p.write_text(
        '{"id":"%s","timestamp":"2026-08-01T10:00:00.000Z"}\n'
        '{"type":"message","role":"assistant","content":[{"type":"output_text","text":"hi"}]}\n' % THREAD,
        encoding="utf-8", newline="\n",
    )
    segs = extract_transcript_segments(read_lines(p, 0, "legacy").lines, "2026-08-01T10:00:00.000Z", None)
    assert segs and segs[0].timestamp == "2026-08-01T10:00:00.000Z" and segs[0].role == "assistant"


def test_ordinal_cutoff(tmp_path):
    segs = _segs(tmp_path, [
        meta(),
        env("event_msg", {"type": "user_message", "message": "old"}, ordinal=1),
        env("event_msg", {"type": "user_message", "message": "new"}, ordinal=5),
    ], skip=5)
    assert [s.text for s in segs] == ["new"]
