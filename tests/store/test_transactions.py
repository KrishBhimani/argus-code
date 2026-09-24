"""Write batches are atomic and the shared connection's transactions don't leak (H4).

REGRESSION: the DB is opened in autocommit mode (``isolation_level=None``), where
``with conn:`` never issues BEGIN. ``executemany`` therefore committed every row
(a failing 4-row batch left 2 rows; 3,000 tool calls took ~2.9 s instead of
~0.03 s in one transaction), and ``__exit__``'s commit ended any transaction a
caller had opened.
"""
from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

import pytest

from argus.schema.types import ToolCall
from tests.conftest import session_factory


def _call(i: int, session: str = "s") -> ToolCall:
    return ToolCall(id=f"{session}:t{i}", session_id=session, turn_index=0, tool_name="Bash",
                    is_error=0, input_size=1, subagent_type=None,
                    timestamp="2026-05-01T00:00:00Z")


def _count(repo) -> int:
    return repo.db.execute("SELECT COUNT(*) FROM tool_calls").fetchone()[0]


def test_failing_batch_leaves_no_rows(repo):
    repo.upsert_session(session_factory("s", "2026-05-01T00:00:00Z"))
    batch = [_call(1), _call(2), _call(3, session="missing-session"), _call(4)]  # 3rd violates FK
    with pytest.raises(sqlite3.IntegrityError):
        repo.upsert_tool_calls(batch)
    assert _count(repo) == 0


def test_batch_inside_caller_transaction_is_not_committed_early(repo):
    repo.upsert_session(session_factory("s", "2026-05-01T00:00:00Z"))
    with pytest.raises(RuntimeError):
        with repo.transaction():
            repo.upsert_tool_calls([_call(1), _call(2)])
            raise RuntimeError("boom")
    assert _count(repo) == 0


def test_nested_failure_rolls_back_only_the_inner_block(repo):
    repo.upsert_session(session_factory("s", "2026-05-01T00:00:00Z"))
    with repo.transaction():
        repo.upsert_tool_calls([_call(1)])
        with pytest.raises(sqlite3.IntegrityError):
            repo.upsert_tool_calls([_call(2), _call(3, session="missing-session")])
    assert [r[0] for r in repo.db.execute("SELECT id FROM tool_calls")] == ["s:t1"]


def test_another_threads_write_is_not_swept_into_a_rollback(repo):
    """The connection is shared by the watcher, first-run, scheduler and request
    threads. A write from thread B must not join thread A's open transaction
    (and vanish when A rolls back) — it waits for A instead."""
    started = threading.Event()

    def writer() -> None:
        started.set()
        repo.set_app_meta("from_thread_b", "1")

    with pytest.raises(RuntimeError):
        with repo.transaction():
            repo.set_app_meta("from_thread_a", "1")
            t = threading.Thread(target=writer)
            t.start()
            started.wait(5)
            t.join(0.3)  # B is blocked on the write lock, not inside A's transaction
            raise RuntimeError("rollback A")
    t.join(10)
    assert not t.is_alive()
    assert repo.get_app_meta("from_thread_a") is None
    assert repo.get_app_meta("from_thread_b") == "1"


def test_ingest_rows_and_offset_are_all_or_nothing(tmp_path: Path, repo, monkeypatch):
    """A crash mid-ingest must not store rows without their offset (or the reverse)."""
    import json

    from argus.adapters.claude_code.adapter import ClaudeCodeAdapter
    from argus.collector.pipeline import ingest_file
    from argus.pricing.load import load_pricing_table

    root = tmp_path / ".claude"
    proj = root / "projects" / "-p"
    proj.mkdir(parents=True)
    f = proj / "s1.jsonl"
    f.write_text(json.dumps({
        "type": "assistant", "sessionId": "s1", "uuid": "u1",
        "timestamp": "2026-05-01T00:00:00Z", "cwd": "/p",
        "message": {"id": "m1", "model": "claude-opus-4-7", "role": "assistant",
                    "content": [{"type": "tool_use", "id": "t1", "name": "Bash", "input": {}}],
                    "usage": {"input_tokens": 1, "output_tokens": 1}},
    }) + "\n", encoding="utf-8")

    def crash(*_a, **_k):
        raise OSError("disk full")

    monkeypatch.setattr(repo, "set_file_offset", crash)
    with pytest.raises(OSError):
        ingest_file(ClaudeCodeAdapter(root), f, repo, load_pricing_table())
    for table in ("sessions", "turns", "tool_calls"):
        assert repo.db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0, table
    assert repo.get_file_offset(str(f)) == 0
