"""Codex rollout reader: offsets, holdback of partial lines, legacy/envelope, zstd."""
from __future__ import annotations

import json

import pytest

from argus.adapters.codex.lines import read_lines, zstd_available

TS0 = "2026-09-01T10:00:00.000Z"
THREAD = "019a0000-0000-7000-8000-000000000001"


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


def jsonl(lines: list[dict]) -> str:
    return "\n".join(json.dumps(l) for l in lines) + "\n"


def test_envelope_lines_carry_offset_timestamp_kind(tmp_path):
    p = tmp_path / "r.jsonl"
    p.write_text(jsonl([meta(), ctx(), tc(usage(10), usage(10, out=5))]), encoding="utf-8")
    r = read_lines(p, 0, "envelope")
    assert [l.kind for l in r.lines] == ["session_meta", "turn_context", "event_msg"]
    assert r.lines[0].offset == 0 and r.lines[1].offset == len(json.dumps(meta())) + 1
    assert r.lines[2].payload["type"] == "token_count" and r.lines[2].timestamp == TS0
    assert r.new_offset == p.stat().st_size and not r.whole_file and r.parse_errors == []


def test_partial_trailing_line_is_held_back(tmp_path):
    p = tmp_path / "r.jsonl"
    p.write_text(jsonl([meta()]) + '{"timestamp":"x","type":"turn_con', encoding="utf-8")
    r = read_lines(p, 0, "envelope")
    assert len(r.lines) == 1
    assert r.new_offset == len(jsonl([meta()]).encode())


def test_reads_only_after_offset(tmp_path):
    p = tmp_path / "r.jsonl"
    p.write_text(jsonl([meta(), ctx()]), encoding="utf-8")
    off = len(jsonl([meta()]).encode())
    r = read_lines(p, off, "envelope")
    assert [l.kind for l in r.lines] == ["turn_context"] and r.lines[0].offset == off


def test_no_new_bytes(tmp_path):
    p = tmp_path / "r.jsonl"
    p.write_text(jsonl([meta()]), encoding="utf-8")
    r = read_lines(p, p.stat().st_size, "envelope")
    assert r.lines == [] and r.new_offset == p.stat().st_size


def test_malformed_line_becomes_parse_error(tmp_path):
    p = tmp_path / "r.jsonl"
    p.write_text(jsonl([meta()]) + "{nope}\n" + jsonl([ctx()]), encoding="utf-8")
    r = read_lines(p, 0, "envelope")
    assert len(r.lines) == 2 and len(r.parse_errors) == 1
    assert r.parse_errors[0].byte_offset == len(jsonl([meta()]).encode())


def test_legacy_format_wraps_bare_items(tmp_path):
    p = tmp_path / "r.jsonl"
    p.write_text(
        '{"id":"%s","timestamp":"2026-08-01T10:00:00.000Z","instructions":null}\n' % THREAD
        + '{"record_type":"state"}\n'
        + '{"type":"message","role":"assistant","content":[{"type":"output_text","text":"hi"}]}\n',
        encoding="utf-8",
    )
    r = read_lines(p, 0, "legacy")
    assert r.lines[0].kind == "session_meta" and r.lines[0].payload["id"] == THREAD
    assert r.lines[0].timestamp == "2026-08-01T10:00:00.000Z"
    assert [l.kind for l in r.lines[1:]] == ["response_item"] and r.lines[1].timestamp is None
    assert r.lines[1].payload["type"] == "message"


def test_ordinal_is_carried(tmp_path):
    p = tmp_path / "r.jsonl"
    p.write_text(jsonl([meta(), env("turn_context", {"model": "gpt-5.5"}, ordinal=7)]), encoding="utf-8")
    r = read_lines(p, 0, "envelope")
    assert r.lines[1].ordinal == 7 and r.lines[0].ordinal is None


@pytest.mark.skipif(not zstd_available(), reason="no zstd decoder available")
def test_zst_file_is_read_whole(tmp_path):
    try:
        from compression import zstd as z  # py3.14+

        comp = z.compress(jsonl([meta(), ctx()]).encode())
    except ImportError:
        import zstandard

        comp = zstandard.ZstdCompressor().compress(jsonl([meta(), ctx()]).encode())
    p = tmp_path / "r.jsonl.zst"
    p.write_bytes(comp)
    r = read_lines(p, 0, "envelope")
    assert [l.kind for l in r.lines] == ["session_meta", "turn_context"]
    assert r.whole_file and r.new_offset == p.stat().st_size


@pytest.mark.skipif(zstd_available(), reason="decoder present")
def test_zst_without_decoder_is_skipped_once(tmp_path):
    p = tmp_path / "r.jsonl.zst"
    p.write_bytes(b"\x28\xb5\x2f\xfd")
    r = read_lines(p, 0, "envelope")
    assert r.lines == [] and r.new_offset == p.stat().st_size
    assert "zstd" in r.parse_errors[0].reason
