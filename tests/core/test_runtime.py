"""CoreRuntime — shared watcher+scheduler+first-pass lifecycle.

The regression test is the safety net for the refactor: it asserts that
read_only=False (no daemon) wires the SAME work the old inline
cli.py:start() did — same paths watched, first-pass runs, scheduler ticks,
shutdown order preserved.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from argus.adapters.claude_code.adapter import ClaudeCodeAdapter
from argus.core.runtime import CoreRuntime, NoAdaptersError


def _line(sid: str, uid: str, mid: str) -> str:
    return json.dumps(
        {
            "type": "assistant",
            "sessionId": sid,
            "uuid": uid,
            "timestamp": "2026-05-01T00:00:00Z",
            "cwd": "C:/proj",
            "version": "2.1.94",
            "userType": "external",
            "entrypoint": "cli",
            "message": {
                "id": mid,
                "model": "claude-opus-4-7",
                "role": "assistant",
                "content": [],
                "usage": {
                    "input_tokens": 1,
                    "output_tokens": 1,
                    "cache_read_input_tokens": 0,
                    "cache_creation_input_tokens": 0,
                },
            },
        }
    )


@pytest.fixture
def claude_root(tmp_path: Path) -> Path:
    root = tmp_path / ".claude"
    proj = root / "projects" / "C--proj"
    proj.mkdir(parents=True)
    (proj / "s1.jsonl").write_text(_line("s1", "u1", "m1") + "\n", encoding="utf-8")
    return root


@pytest.fixture
def patched_adapters(monkeypatch, claude_root: Path):
    """Point available_adapters() at a tmp ~/.claude so tests are hermetic."""
    adapter = ClaudeCodeAdapter(claude_root)
    monkeypatch.setattr(
        "argus.core.runtime.available_adapters", lambda: [adapter]
    )
    return adapter


def test_start_read_write_ingests_and_wires_threads(
    tmp_path, patched_adapters
):
    rt = CoreRuntime(tmp_path, read_only=False)
    rt.start()
    try:
        # First-pass foreground ingest ran: the recent session is in the DB.
        turns = rt.repo.get_turns_for_session("claude_code:s1")
        assert len(turns) == 1
        # Watcher + scheduler threads are live.
        assert rt._watcher is not None
        assert rt._scheduler is not None
        # ingest_status delegates to the real first-run handle.
        assert rt.ingest_status().foreground_complete is True
    finally:
        rt.stop()


def test_start_raises_when_no_adapters(tmp_path, monkeypatch):
    monkeypatch.setattr("argus.core.runtime.available_adapters", lambda: [])
    rt = CoreRuntime(tmp_path, read_only=False)
    with pytest.raises(NoAdaptersError):
        rt.start()


def test_stop_order_scheduler_then_watcher_then_db(tmp_path, patched_adapters):
    """Shutdown order must match the old inline finally-block:
    scheduler.stop() -> watcher.stop() -> db.close()."""
    rt = CoreRuntime(tmp_path, read_only=False)
    rt.start()

    calls: list[str] = []
    sched = rt._scheduler
    watch = rt._watcher
    orig_sched_stop = sched.stop
    orig_watch_stop = watch.stop

    # stop() takes a timeout and reports whether its thread exited; forward both.
    def sched_stop(*a, **k):
        calls.append("scheduler")
        return orig_sched_stop(*a, **k)

    def watch_stop(*a, **k):
        calls.append("watcher")
        return orig_watch_stop(*a, **k)

    # sqlite3.Connection.close is a read-only C attribute; wrap it in a proxy
    # that records the call and delegates to the real connection.
    class _DBProxy:
        def __init__(self, real):
            self._real = real

        def close(self):
            calls.append("db")
            self._real.close()

    sched.stop = sched_stop  # type: ignore[method-assign]
    watch.stop = watch_stop  # type: ignore[method-assign]
    rt._db = _DBProxy(rt._db)

    rt.stop()
    assert calls == ["scheduler", "watcher", "db"]


def test_scheduler_uses_default_600s_interval(tmp_path, patched_adapters, monkeypatch):
    """Regression: scheduler must be started with the same 600s cadence."""
    seen: dict[str, int] = {}
    import argus.core.runtime as runtime_mod

    real_start_scheduler = runtime_mod.start_scheduler

    def spy(detectors, repo, *, interval_sec=600):
        seen["interval"] = interval_sec
        return real_start_scheduler(detectors, repo, interval_sec=interval_sec)

    monkeypatch.setattr(runtime_mod, "start_scheduler", spy)
    rt = CoreRuntime(tmp_path, read_only=False)
    rt.start()
    try:
        assert seen["interval"] == 600
    finally:
        rt.stop()


def test_stop_is_idempotent(tmp_path, patched_adapters):
    rt = CoreRuntime(tmp_path, read_only=False)
    rt.start()
    rt.stop()
    rt.stop()  # must not raise


def test_read_only_skips_ingestion_and_blocks_writes(tmp_path, patched_adapters):
    # Seed the DB read-write first (a live daemon would have done this).
    seed = CoreRuntime(tmp_path, read_only=False)
    seed.start()
    seed.stop()

    rt = CoreRuntime(tmp_path, read_only=True)
    rt.start()
    try:
        # No ingestion subsystems started.
        assert rt._first_run is None
        assert rt._watcher is None
        assert rt._scheduler is None
        # Static completed status.
        st = rt.ingest_status()
        assert st.foreground_complete is True
        assert st.processed == 0 and st.total == 0
        # The connection is genuinely read-only.
        import sqlite3

        with pytest.raises(sqlite3.OperationalError):
            rt.repo.set_search_indexing_enabled(True)
    finally:
        rt.stop()
