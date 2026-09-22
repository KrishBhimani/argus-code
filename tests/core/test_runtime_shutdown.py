"""Shutdown must not close SQLite under a live writer thread (H5).

REGRESSION: CoreRuntime.stop() joined first-run with a 10 s timeout, ignored the
result, never joined the ``argus-search-backfill`` thread, then closed the
shared connection. Closing a sqlite3 connection another thread is using is
undefined behaviour — reproduced as an access violation / exit 139 in
repository.upsert_turn <- pipeline.ingest_file <- search_backfill.
"""
from __future__ import annotations

import threading
import time
from pathlib import Path

from argus.collector import search_backfill
from argus.collector.search_backfill import (
    SEARCH_BACKFILL_THREAD_NAME,
    join_search_backfill_threads,
    run_segment_backfill,
)
from argus.core.runtime import CoreRuntime


class _DBProxy:
    def __init__(self, calls: list[str]):
        self.calls = calls

    def close(self) -> None:
        self.calls.append("db.close")


def test_stop_waits_for_search_backfill_before_closing(tmp_path: Path) -> None:
    calls: list[str] = []

    def writer() -> None:
        time.sleep(0.3)
        calls.append("backfill done")

    t = threading.Thread(target=writer, name=SEARCH_BACKFILL_THREAD_NAME, daemon=True)
    t.start()
    rt = CoreRuntime(tmp_path)
    rt._db = _DBProxy(calls)
    rt.stop()
    assert calls == ["backfill done", "db.close"]


def test_stop_skips_close_while_a_writer_is_stuck(tmp_path: Path, caplog) -> None:
    calls: list[str] = []
    release = threading.Event()
    t = threading.Thread(target=lambda: release.wait(10), name=SEARCH_BACKFILL_THREAD_NAME,
                         daemon=True)
    t.start()
    try:
        rt = CoreRuntime(tmp_path)
        rt.writer_join_timeout = 0.2
        rt._db = _DBProxy(calls)
        rt.stop()
        assert calls == []  # never closed under the live writer
        assert any("still running" in r.getMessage() for r in caplog.records)
    finally:
        release.set()
        t.join(5)


class _FakeAdapter:
    agent = "claude_code"

    def __init__(self, files: list[Path], discover_hook=None):
        self.files = files
        self.discover_hook = discover_hook
        self.discover_calls = 0

    def discover_session_files(self) -> list[Path]:
        self.discover_calls += 1
        if self.discover_hook:
            self.discover_hook(self.discover_calls)
        return self.files


class _FakeRepo:
    def __init__(self, ids: list[str]):
        self.ids = ids

    def sessions_missing_segments(self, limit: int):
        return [{"id": i} for i in self.ids][:limit]

    def set_file_offset(self, path, offset): ...

    def record_parse_error(self, e): ...


def test_search_backfill_stops_when_asked(monkeypatch) -> None:
    n = 50
    files = [Path(f"/x/s{i}.jsonl") for i in range(n)]
    ingested: list[Path] = []

    def slow_ingest(adapter, file, repo, table):
        time.sleep(0.02)
        ingested.append(file)

    monkeypatch.setattr(search_backfill, "ingest_file", slow_ingest)
    run_segment_backfill([_FakeAdapter(files)], _FakeRepo([f"claude_code:s{i}" for i in range(n)]), None)
    time.sleep(0.1)
    search_backfill.request_search_backfill_stop()
    assert join_search_backfill_threads(timeout=5) == []
    assert 0 < len(ingested) < n
    assert search_backfill.get_search_backfill_status().in_progress is False


def test_concurrent_enable_requests_start_one_worker(monkeypatch) -> None:
    """REGRESSION: in_progress was checked, the lock released, and set only
    after discovery — two concurrent enable requests both started a worker."""
    entered, release = threading.Event(), threading.Event()

    def hook(call: int) -> None:
        if call == 1:  # first request is mid-discovery while the second arrives
            entered.set()
            release.wait(5)

    monkeypatch.setattr(search_backfill, "ingest_file", lambda *a: None)
    adapter = _FakeAdapter([Path("/x/s0.jsonl")], discover_hook=hook)
    repo = _FakeRepo(["claude_code:s0"])
    first = threading.Thread(target=run_segment_backfill, args=([adapter], repo, None))
    first.start()
    assert entered.wait(5)
    run_segment_backfill([adapter], repo, None)  # second, concurrent request
    release.set()
    first.join(5)
    assert join_search_backfill_threads(timeout=5) == []
    assert adapter.discover_calls == 1


def test_first_run_background_stops_when_asked(tmp_path: Path, repo, monkeypatch) -> None:
    import json

    from argus.adapters.claude_code.adapter import ClaudeCodeAdapter
    from argus.collector import first_run
    from argus.pricing.load import load_pricing_table

    root = tmp_path / ".claude"
    proj = root / "projects" / "-p"
    proj.mkdir(parents=True)
    for i in range(40):
        (proj / f"s{i}.jsonl").write_text(json.dumps({"type": "user"}) + "\n", encoding="utf-8")
    seen: list[Path] = []

    def slow_ingest(adapter, file, repo_, table):
        time.sleep(0.02)
        seen.append(file)

    monkeypatch.setattr(first_run, "ingest_file", slow_ingest)
    # recent_days=-1: every file is "older", i.e. handled by the background thread.
    handle = first_run.run_first_pass_ingest([ClaudeCodeAdapter(root)], repo,
                                             load_pricing_table(), recent_days=-1)
    time.sleep(0.1)
    handle.request_stop()
    assert handle.join(timeout=5)
    assert 0 < len(seen) < 40


def test_interrupted_backfill_does_not_mark_one_shot_fixes_done(tmp_path: Path, repo) -> None:
    """A shutdown mid-backfill must leave the one-shot flags unset so the
    remaining work runs next start (the agent-type flag was set before the
    re-reads, so an interrupted run recorded unfinished work as done)."""
    from argus.adapters.claude_code.adapter import ClaudeCodeAdapter
    from argus.collector.first_run import _backfill_missing_derived_data
    from argus.pricing.load import load_pricing_table
    from argus.schema.types import ToolCall
    from tests.conftest import session_factory

    repo.upsert_session(session_factory("claude_code:s1", "2026-05-01T00:00:00Z"))
    repo.upsert_tool_calls([ToolCall(id="claude_code:s1:a", session_id="claude_code:s1",
                                     turn_index=0, tool_name="Agent", is_error=0, input_size=1,
                                     subagent_type=None, timestamp="2026-05-01T00:00:00Z")])
    (tmp_path / ".claude" / "projects").mkdir(parents=True)
    _backfill_missing_derived_data([ClaudeCodeAdapter(tmp_path / ".claude")], repo,
                                   load_pricing_table(), should_stop=lambda: True)
    assert repo.get_app_meta("backfill_agent_subagent_type_v1") is None


class _Handle:
    """Stand-in for a scheduler/watcher handle whose thread won't exit in time."""

    def __init__(self, calls: list[str], name: str, exited: bool):
        self.calls, self.name, self.exited = calls, name, exited

    def stop(self, timeout: float = 10.0) -> bool:
        self.calls.append(f"{self.name}.stop")
        return self.exited


def test_stop_skips_close_while_the_scheduler_thread_is_alive(tmp_path: Path, caplog) -> None:
    """REGRESSION (CI, Windows py3.13): scheduler.stop() joined with a timeout
    and its result was ignored, so the DB could be closed under a detector tick."""
    calls: list[str] = []
    rt = CoreRuntime(tmp_path)
    rt._scheduler = _Handle(calls, "scheduler", exited=False)
    rt._watcher = _Handle(calls, "watcher", exited=True)
    rt._db = _DBProxy(calls)
    rt.stop()
    assert calls == ["scheduler.stop", "watcher.stop"]  # no db.close
    assert any("argus-scheduler" in r.getMessage() for r in caplog.records)


def test_stop_skips_close_while_the_watcher_worker_is_alive(tmp_path: Path) -> None:
    calls: list[str] = []
    rt = CoreRuntime(tmp_path)
    rt._scheduler = _Handle(calls, "scheduler", exited=True)
    rt._watcher = _Handle(calls, "watcher", exited=False)
    rt._db = _DBProxy(calls)
    rt.stop()
    assert "db.close" not in calls


def test_stop_closes_when_every_thread_exited(tmp_path: Path) -> None:
    calls: list[str] = []
    rt = CoreRuntime(tmp_path)
    rt._scheduler = _Handle(calls, "scheduler", exited=True)
    rt._watcher = _Handle(calls, "watcher", exited=True)
    rt._db = _DBProxy(calls)
    rt.stop()
    assert calls == ["scheduler.stop", "watcher.stop", "db.close"]


def test_scheduler_and_watcher_stop_report_whether_their_threads_exited(tmp_path: Path, repo) -> None:
    from argus.collector.scheduler import start_scheduler
    from argus.collector.watcher import start_watcher

    sched = start_scheduler([], repo, interval_sec=600)
    assert sched.stop(timeout=10) is True
    (tmp_path / ".claude").mkdir()
    from argus.adapters.claude_code.adapter import ClaudeCodeAdapter
    from argus.pricing.load import load_pricing_table

    w = start_watcher([ClaudeCodeAdapter(tmp_path / ".claude")], repo, load_pricing_table())
    assert w.stop(timeout=10) is True
