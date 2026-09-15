"""CodexAdapter façade: presence, discovery, sub-sessions, skip rules, containment."""
from __future__ import annotations

import json
from pathlib import Path

from argus.adapters.codex.adapter import CodexAdapter

TS0 = "2026-09-01T10:00:00.000Z"
THREAD = "019a0000-0000-7000-8000-000000000001"
CHILD = "019a0000-0000-7000-8000-000000000002"
NAME = f"rollout-2026-09-01T10-00-00-{THREAD}.jsonl"


def env(kind: str, payload: dict, ts: str = TS0) -> dict:
    return {"timestamp": ts, "type": kind, "payload": payload}


def meta(thread: str = THREAD, ts: str = TS0, **extra) -> dict:
    payload = {"id": thread, "timestamp": ts, "cwd": "C:\\proj", "originator": "codex_cli_rs", "cli_version": "0.155.0", "source": "cli"}
    payload.update(extra)
    return env("session_meta", payload, ts)


def ctx(model: str = "gpt-5.5") -> dict:
    return env("turn_context", {"cwd": "C:\\proj", "model": model, "summary": "auto"})


def tc(total: dict, last: dict) -> dict:
    return env("event_msg", {"type": "token_count", "info": {"total_token_usage": total, "last_token_usage": last}})


def usage(inp: int, out: int = 0) -> dict:
    return {"input_tokens": inp, "cached_input_tokens": 0, "output_tokens": out, "reasoning_output_tokens": 0, "total_tokens": inp + out}


def jsonl(lines: list[dict]) -> str:
    return "\n".join(json.dumps(l) for l in lines) + "\n"


def _root(tmp_path: Path) -> Path:
    root = tmp_path / ".codex"
    (root / "sessions" / "2026" / "09" / "01").mkdir(parents=True)
    return root


def test_present_only_with_sessions_dir(tmp_path):
    assert not CodexAdapter(tmp_path / "nope").is_present()
    assert not CodexAdapter(tmp_path).is_present()
    assert CodexAdapter(_root(tmp_path)).is_present()


def test_discover_and_sub_sessions_and_skip(tmp_path):
    root = _root(tmp_path)
    day = root / "sessions/2026/09/01"
    parent = day / NAME
    parent.write_text(jsonl([meta()]), encoding="utf-8", newline="\n")
    child = day / f"rollout-2026-09-01T10-00-01-{CHILD}.jsonl"
    child.write_text(jsonl([meta(CHILD, parent_thread_id=THREAD)]), encoding="utf-8", newline="\n")
    (root / "history.jsonl").write_text("", encoding="utf-8")
    a = CodexAdapter(root)
    assert a.discover_session_files() == [parent]
    assert a.sub_session_files_for(parent) == [child]
    assert a.should_skip(child) and not a.should_skip(parent)
    assert a.should_skip(root / "history.jsonl") and a.should_skip(root / "logs" / "x.jsonl")
    assert a.native_session_id(parent) == THREAD
    assert a.normalize_model_name("openai/gpt-5.5") == "gpt-5.5"
    assert a.extra_watch_paths() == [root / "history.jsonl"]


def test_ingest_refuses_paths_outside_root(tmp_path):
    root = _root(tmp_path)
    outside = tmp_path / NAME
    outside.write_text(jsonl([meta(), ctx(), tc(usage(1, 1), usage(1, 1))]), encoding="utf-8", newline="\n")
    result, off = CodexAdapter(root).ingest_file(outside, 0)
    assert result.turns == [] and off == 0


def test_ingest_through_adapter(tmp_path):
    root = _root(tmp_path)
    f = root / "sessions/2026/09/01" / NAME
    f.write_text(jsonl([meta(), ctx(), tc(usage(10, 2), usage(10, 2))]), encoding="utf-8", newline="\n")
    result, off = CodexAdapter(root).ingest_file(f, 0)
    assert len(result.turns) == 1 and off == f.stat().st_size
