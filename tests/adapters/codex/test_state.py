"""The fold that makes chunked ingest equal one-pass ingest."""
from __future__ import annotations

import json
from pathlib import Path

from argus.adapters.codex.state import ContextCache, FileState, scan, window_for


def _env(o, t, p):
    return json.dumps({"timestamp": f"2026-10-03T20:00:{o:02d}.000Z", "ordinal": o, "type": t, "payload": p})


def _file(tmp_path: Path, lines: list[str]) -> tuple[Path, list[int]]:
    f = tmp_path / "r.jsonl"
    data = "".join(l + "\n" for l in lines).encode()
    f.write_bytes(data)
    offs, pos = [], 0
    for l in lines:
        offs.append(pos)
        pos += len(l.encode()) + 1
    return f, offs


LINES = [
    _env(0, "session_meta", {"id": "T", "cwd": "/p", "cli_version": "0.159.2"}),
    _env(1, "event_msg", {"type": "task_started", "turn_id": "U1"}),
    _env(2, "turn_context", {"turn_id": "U1", "model": "gpt-6.1-sol", "effort": "medium"}),
    _env(3, "token_usage_record", {"response_id": "r1", "turn_id": "U1", "usage": {"input_tokens": 1}}),
    _env(4, "event_msg", {"type": "task_complete", "turn_id": "U1"}),
    _env(5, "event_msg", {"type": "task_started", "turn_id": "U2"}),
    _env(6, "turn_context", {"turn_id": "U2", "model": "gpt-6-luna", "effort": "high"}),
]


def test_scan_tracks_models_open_task_and_snapshot(tmp_path: Path) -> None:
    f, offs = _file(tmp_path, LINES)
    end = f.stat().st_size
    state, open_state = scan(f, end)
    assert state.meta["id"] == "T" and state.saw_usage_record
    assert state.model_for("U1") == ("gpt-6.1-sol", "medium")
    assert state.model_for("U2") == ("gpt-6-luna", "high")
    assert state.open_task_offset == offs[5]
    assert open_state is not None and "U2" not in open_state.models  # taken BEFORE task_started


def test_window_reparses_the_open_task(tmp_path: Path) -> None:
    f, offs = _file(tmp_path, LINES)
    end = f.stat().st_size
    start, st = window_for(f, end, ContextCache())
    assert start == offs[5] and "U2" not in st.models
    start0, st0 = window_for(f, 0, ContextCache())
    assert start0 == 0 and st0.meta is None


def test_cache_hit_equals_rescan(tmp_path: Path) -> None:
    f, offs = _file(tmp_path, LINES)
    cache = ContextCache()
    state, open_state = scan(f, offs[5])
    cache.put(f, offs[5], state, open_state)
    hit = cache.get(f, offs[5])
    assert hit is not None and hit[0] == state and hit[0] is not state  # a copy
    assert cache.get(f, offs[4]) is None


def test_child_cutoff_skips_copied_parent_history(tmp_path: Path) -> None:
    lines = [
        _env(0, "session_meta", {"id": "C", "parent_thread_id": "P", "subagent_history_start_ordinal": 3}),
        _env(1, "session_meta", {"id": "P"}),  # the parent's copied header
        _env(2, "turn_context", {"turn_id": "PU", "model": "parent-model"}),
        _env(3, "turn_context", {"turn_id": "CU", "model": "child-model"}),
    ]
    f, _ = _file(tmp_path, lines)
    state, _ = scan(f, f.stat().st_size)
    assert state.meta["id"] == "C" and state.cutoff == 3
    assert "PU" not in state.models and state.model_for("CU")[0] == "child-model"
