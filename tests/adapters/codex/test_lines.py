"""Codex rollout envelope reader: byte-exact offsets, partial-line holdback,
tolerance of unknown shapes, no line content in parse errors."""
from __future__ import annotations

import json
from pathlib import Path

from argus.adapters.codex.lines import read_records


def _env(ordinal, type_, payload, ts="2026-10-03T20:00:00.000Z"):
    return json.dumps({"timestamp": ts, "ordinal": ordinal, "type": type_, "payload": payload})


def test_offsets_are_file_wide_and_partial_line_is_held_back(tmp_path: Path) -> None:
    a = _env(0, "session_meta", {"id": "T"})
    b = _env(1, "event_msg", {"type": "task_started"})
    f = tmp_path / "r.jsonl"
    f.write_bytes((a + "\n" + b + "\n" + '{"partial').encode())
    recs, errs, end = read_records(f, 0)
    assert [r.offset for r in recs] == [0, len(a) + 1]
    assert [r.ordinal for r in recs] == [0, 1]
    assert end == len(a) + 1 + len(b) + 1
    assert errs == []
    # resume mid-file: same offsets
    recs2, _, end2 = read_records(f, len(a) + 1)
    assert [r.offset for r in recs2] == [len(a) + 1] and end2 == end


def test_stop_limits_the_read(tmp_path: Path) -> None:
    a = _env(0, "session_meta", {"id": "T"})
    b = _env(1, "turn_context", {"model": "m"})
    f = tmp_path / "r.jsonl"
    f.write_bytes((a + "\n" + b + "\n").encode())
    recs, _, end = read_records(f, 0, stop=len(a) + 1)
    assert [r.type for r in recs] == ["session_meta"] and end == len(a) + 1


def test_unknown_shapes_skipped_and_bad_json_reported_without_content(tmp_path: Path) -> None:
    f = tmp_path / "r.jsonl"
    f.write_bytes(b'[1,2]\n{"type":"x"}\n{"type":"world_state","payload":{"a":1}}\nSECRET{\n')
    recs, errs, _ = read_records(f, 0)
    assert [r.type for r in recs] == ["world_state"]
    assert len(errs) == 1 and errs[0].raw_line_truncated == "" and "SECRET" not in errs[0].reason


def test_empty_or_no_newline(tmp_path: Path) -> None:
    f = tmp_path / "r.jsonl"
    f.write_bytes(b"")
    assert read_records(f, 0) == ([], [], 0)
    f.write_bytes(b'{"no newline yet"')
    assert read_records(f, 0) == ([], [], 0)
