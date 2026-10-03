"""Codex discovery: CODEX_HOME resolution, session-dir containment,
first-record identity, sub-agent / continuation relationships."""
from __future__ import annotations

import json
from pathlib import Path

from argus.adapters.codex.discover import (
    ThreadIndex,
    codex_home,
    contained,
    peek_thread,
)


def _meta(tid, **extra):
    return json.dumps({"timestamp": "2026-10-03T20:00:00.000Z", "ordinal": 0,
                       "type": "session_meta", "payload": {"id": tid, "session_id": tid, **extra}})


def _write(path: Path, *lines: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(l + "\n" for l in lines), encoding="utf-8")
    return path


def test_codex_home_prefers_env(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "ch"))
    assert codex_home() == tmp_path / "ch"
    monkeypatch.delenv("CODEX_HOME")
    assert codex_home() == Path.home() / ".codex"


def test_contained_only_under_session_dirs(tmp_path: Path) -> None:
    root = tmp_path / ".codex"
    ok = _write(root / "sessions" / "2026" / "10" / "04" / "rollout-a.jsonl", _meta("A"))
    arch = _write(root / "archived_sessions" / "rollout-b.jsonl", _meta("B"))
    hist = _write(root / "history.jsonl", "{}")
    assert contained(ok, root) is not None
    assert contained(arch, root) is not None
    assert contained(hist, root) is None
    assert contained(tmp_path / "elsewhere.jsonl", root) is None


def test_peek_reads_identity_from_first_record(tmp_path: Path) -> None:
    child = _write(tmp_path / "c.jsonl", _meta(
        "C", parent_thread_id="P", subagent_history_start_ordinal=37,
        source={"subagent": {"thread_spawn": {"parent_thread_id": "P", "depth": 1}}}))
    info = peek_thread(child)
    assert (info.thread_id, info.parent_thread_id, info.history_start_ordinal) == ("C", "P", 37)
    spawn_only = _write(tmp_path / "s.jsonl", _meta(
        "S", source={"subagent": {"thread_spawn": {"parent_thread_id": "P"}}}))
    assert peek_thread(spawn_only).parent_thread_id == "P"
    cont = _write(tmp_path / "k.jsonl", _meta("K", history_base={"thread_id": "K", "end_ordinal_exclusive": 8}))
    assert peek_thread(cont).history_start_ordinal == 8
    assert peek_thread(_write(tmp_path / "x.jsonl", '{"id":"legacy","instructions":""}')) is None
    partial = tmp_path / "p.jsonl"
    partial.write_text(_meta("Z")[:20], encoding="utf-8")  # first line still being written
    assert peek_thread(partial) is None


def test_index_top_level_and_descendants(tmp_path: Path) -> None:
    root = tmp_path / ".codex"
    day = root / "sessions" / "2026" / "10" / "04"
    parent = _write(day / "rollout-1-P.jsonl", _meta("P"))
    cont = _write(day / "rollout-2-P_X.jsonl", _meta("P"))  # continuation segment, same thread
    child = _write(day / "rollout-3-C.jsonl", _meta("C", parent_thread_id="P"))
    grand = _write(day / "rollout-4-G.jsonl", _meta("G", parent_thread_id="C"))
    idx = ThreadIndex(root)
    idx.refresh()
    assert idx.top_level_files() == sorted([parent, cont])
    assert idx.descendant_files(parent) == sorted([child, grand])  # flattened to the root
    assert idx.descendant_files(cont) == sorted([child, grand])
    assert idx.descendant_files(child) == []


def test_orphan_child_is_not_top_level(tmp_path: Path) -> None:
    """Review focus #2: a child whose parent rollout is gone is never a session."""
    root = tmp_path / ".codex"
    _write(root / "sessions" / "rollout-C.jsonl", _meta("C", parent_thread_id="GONE"))
    idx = ThreadIndex(root)
    idx.refresh()
    assert idx.top_level_files() == []


def test_refresh_forgets_moved_files(tmp_path: Path) -> None:
    root = tmp_path / ".codex"
    a = _write(root / "sessions" / "rollout-A.jsonl", _meta("A"))
    idx = ThreadIndex(root)
    idx.refresh()
    moved = root / "archived_sessions" / "rollout-A.jsonl"
    moved.parent.mkdir(parents=True)
    a.rename(moved)
    idx.refresh()
    assert idx.top_level_files() == [moved]
