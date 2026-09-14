"""Codex rollout discovery: filename parsing, first-line peek, thread index, containment."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from argus.adapters.codex.discover import (
    ThreadIndex,
    codex_root,
    is_rollout_path,
    native_id_for,
    parse_rollout_name,
    peek_meta,
)

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


def jsonl(lines: list[dict]) -> str:
    return "\n".join(json.dumps(l) for l in lines) + "\n"


def _write(root: Path, rel: str, lines: list[dict]) -> Path:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(jsonl(lines), encoding="utf-8")
    return p


def test_parse_rollout_name_variants():
    r = parse_rollout_name(NAME)
    assert r and r.thread_id == THREAD and r.rollout_id == THREAD
    assert r.timestamp == "2026-09-01T10-00-00" and not r.compressed
    r2 = parse_rollout_name(f"rollout-2026-09-01T10-00-00-{THREAD}_{CHILD}.jsonl.zst")
    assert r2 and r2.thread_id == THREAD and r2.rollout_id == CHILD and r2.compressed
    assert parse_rollout_name("history.jsonl") is None
    assert parse_rollout_name("rollout-garbage.jsonl") is None


def test_native_id_for_uses_thread_and_revert_suffix(tmp_path):
    assert native_id_for(tmp_path / NAME) == THREAD
    assert native_id_for(tmp_path / f"rollout-2026-09-01T10-00-00-{THREAD}_{CHILD}.jsonl") == f"{THREAD}_{CHILD}"


def test_codex_root_honours_env(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    assert codex_root() == tmp_path.resolve()
    monkeypatch.delenv("CODEX_HOME")
    assert codex_root() == (Path.home() / ".codex").resolve(strict=False)


def test_peek_meta_envelope_and_legacy(tmp_path):
    p = _write(tmp_path, f"sessions/2026/09/01/{NAME}", [meta(git={"branch": "main"}), ctx()])
    m = peek_meta(p)
    assert m and m.format == "envelope" and m.thread_id == THREAD and m.cli_version == "0.155.0"
    assert m.cwd == "C:\\proj" and m.git_branch == "main"
    assert m.parent_thread_id is None and not m.is_fork and m.skip_before_ordinal is None
    legacy = tmp_path / "sessions/2026/08/01" / f"rollout-2026-08-01T10-00-00-{CHILD}.jsonl"
    legacy.parent.mkdir(parents=True)
    legacy.write_text(
        '{"id":"%s","timestamp":"2026-08-01T10:00:00.000Z","instructions":null}\n'
        '{"type":"message","role":"user","content":[]}\n' % CHILD,
        encoding="utf-8",
    )
    lm = peek_meta(legacy)
    assert lm and lm.format == "legacy" and lm.thread_id == CHILD
    assert lm.started_at == "2026-08-01T10:00:00.000Z"


def test_peek_meta_detects_children_and_forks(tmp_path):
    spawned = _write(
        tmp_path,
        f"sessions/2026/09/01/rollout-2026-09-01T10-00-01-{CHILD}.jsonl",
        [
            meta(
                CHILD,
                source={"subagent": {"thread_spawn": {"parent_thread_id": THREAD, "depth": 1, "agent_path": "/root/w", "agent_role": "worker"}}},
                subagent_history_start_ordinal=40,
            )
        ],
    )
    m = peek_meta(spawned)
    assert m.parent_thread_id == THREAD and m.agent_role == "worker" and m.skip_before_ordinal == 40
    explicit = _write(tmp_path, f"sessions/2026/09/01/rollout-2026-09-01T10-00-02-{CHILD}.jsonl", [meta(CHILD, parent_thread_id=THREAD)])
    assert peek_meta(explicit).parent_thread_id == THREAD
    fork = _write(
        tmp_path,
        f"sessions/2026/09/01/rollout-2026-09-01T10-00-03-{CHILD}.jsonl",
        [meta(CHILD, forked_from_id=THREAD, forked_from_ordinal_exclusive=12)],
    )
    fm = peek_meta(fork)
    assert fm.is_fork and fm.parent_thread_id is None and fm.skip_before_ordinal == 12


def test_index_returns_roots_only_and_links_children(tmp_path):
    root = tmp_path / ".codex"
    parent = _write(root, f"sessions/2026/09/01/{NAME}", [meta()])
    child = _write(root, f"sessions/2026/09/01/rollout-2026-09-01T10-00-01-{CHILD}.jsonl", [meta(CHILD, parent_thread_id=THREAD)])
    archived = _write(root, f"archived_sessions/2026/08/01/rollout-2026-08-01T10-00-00-{CHILD}_{THREAD}.jsonl", [meta(CHILD)])
    _write(root, "sessions/2026/09/01/notes.jsonl", [meta()])  # not a rollout name
    idx = ThreadIndex(root)
    roots = idx.refresh()
    assert set(roots) == {parent, archived}
    assert idx.children_of(parent) == [child]
    assert idx.is_child(child) and not idx.is_child(parent)
    assert is_rollout_path(root, parent)
    assert not is_rollout_path(root, root / "sessions/2026/09/01/notes.jsonl")
    assert not is_rollout_path(root, root / "history.jsonl")


def test_index_learns_new_child_lazily(tmp_path):
    root = tmp_path / ".codex"
    parent = _write(root, f"sessions/2026/09/01/{NAME}", [meta()])
    idx = ThreadIndex(root)
    idx.refresh()
    child = _write(root, f"sessions/2026/09/01/rollout-2026-09-01T10-00-01-{CHILD}.jsonl", [meta(CHILD, parent_thread_id=THREAD)])
    assert idx.is_child(child)  # peeked on demand
    assert idx.children_of(parent) == [child]


def test_index_rejects_escaping_junction(tmp_path):
    """A junction/symlink inside sessions/ that resolves outside the root is dropped."""
    root = tmp_path / ".codex"
    outside = tmp_path / "outside"
    outside.mkdir()
    _write(outside, NAME, [meta()])
    sess = root / "sessions/2026/09/01"
    sess.mkdir(parents=True)
    link = sess / "escape"
    if sys.platform == "win32":
        subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(outside)], check=True, capture_output=True)
    else:
        os.symlink(outside, link, target_is_directory=True)
    idx = ThreadIndex(root)
    assert idx.refresh() == []
    assert not is_rollout_path(root, link / NAME)
