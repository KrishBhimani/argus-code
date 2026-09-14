"""argusd must survive a missing ``~/.claude`` — issue #11.

``CoreRuntime.start()`` raises ``NoAdaptersError`` when no adapter reports
``is_present()``, and ``ClaudeCodeAdapter.is_present()`` is just "does
``~/.claude`` exist". In ``service.run_foreground`` that exception propagates
straight out of ``runtime.start()``, so the ``finally`` tears down and removes
the pidfile and the process exits — on a fresh machine, or one where Claude
Code simply hasn't been launched yet, the daemon refuses to stay up.

A long-running service should tolerate the directory being absent and start
working once it appears. The *foreground* ``argus start`` raising immediately is
useful feedback and is deliberately kept.
"""
from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

import pytest

from argus.adapters.claude_code import adapter as cc_adapter
from argus.adapters.codex import adapter as codex_adapter
from argus.core.runtime import CoreRuntime, NoAdaptersError
from argus.daemon import service


@pytest.fixture
def fake_home(tmp_path: Path, monkeypatch):
    """Point every adapter at a home that has no agent data yet.

    Both roots must be faked: a developer's real ~/.codex/sessions would
    otherwise make "no adapters present" untestable on their machine.
    """
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(cc_adapter, "_default_root", lambda: home / ".claude")
    monkeypatch.setattr(codex_adapter, "codex_root", lambda: home / ".codex")
    return home


def test_foreground_start_still_raises(fake_home, tmp_path: Path):
    """`argus start` keeps its immediate, friendly failure — that's useful."""
    rt = CoreRuntime(tmp_path / "data")
    try:
        with pytest.raises(NoAdaptersError):
            rt.start()
    finally:
        rt.stop()


def test_daemon_start_does_not_raise_when_claude_is_missing(fake_home, tmp_path: Path):
    rt = CoreRuntime(tmp_path / "data")
    try:
        rt.start(require_adapters=False)  # must not raise
        assert rt.adapters == []
        assert rt.repo is not None, "the DB should still be open and usable"
    finally:
        rt.stop()


def test_daemon_activates_once_claude_appears(fake_home, tmp_path: Path):
    """The whole point: come up idle, then start working by itself."""
    rt = CoreRuntime(tmp_path / "data")
    try:
        rt.start(require_adapters=False)
        assert rt.try_activate() is False, "nothing to activate yet"

        proj = fake_home / ".claude" / "projects" / "p"
        proj.mkdir(parents=True)
        (proj / "s1.jsonl").write_text(
            '{"type":"user","message":{"role":"user","content":"hi"}}\n',
            encoding="utf-8",
        )

        assert rt.try_activate() is True, "should pick up the new ~/.claude"
        assert [a.agent for a in rt.adapters] == ["claude_code"]
        # Idempotent: a second call must not start a second watcher/scheduler.
        assert rt.try_activate() is False
    finally:
        rt.stop()


def test_run_foreground_stays_alive_without_claude(fake_home, tmp_path: Path):
    """The daemon process must not exit; it must sit waiting for work."""
    data_dir = tmp_path / "data"
    stop = threading.Event()
    error: list[BaseException] = []

    def run() -> None:
        try:
            service.run_foreground(
                data_dir, stop_event=stop, install_signals=False, adapter_poll_sec=0.05
            )
        except BaseException as e:  # noqa: BLE001
            error.append(e)

    t = threading.Thread(target=run, daemon=True)
    t.start()
    try:
        # Give it long enough to have crashed if it were going to.
        t.join(2.0)
        assert t.is_alive(), f"daemon exited instead of waiting; error={error}"
        assert not error, f"daemon raised: {error}"
    finally:
        stop.set()
        t.join(10)
    assert not error, f"daemon raised on shutdown: {error}"


def test_run_foreground_picks_up_claude_created_while_running(
    fake_home, tmp_path: Path
):
    data_dir = tmp_path / "data"
    stop = threading.Event()
    error: list[BaseException] = []

    def run() -> None:
        try:
            service.run_foreground(
                data_dir, stop_event=stop, install_signals=False, adapter_poll_sec=0.05
            )
        except BaseException as e:  # noqa: BLE001
            error.append(e)

    t = threading.Thread(target=run, daemon=True)
    t.start()
    try:
        # Must be an *assistant* line: the pipeline deliberately creates no
        # session row for a file with no turns, so a user-only line would
        # ingest fine and still leave list_sessions() empty.
        import json

        from tests.conftest import assistant_line

        proj = fake_home / ".claude" / "projects" / "p"
        proj.mkdir(parents=True)
        (proj / "s1.jsonl").write_text(
            json.dumps(assistant_line("m1", 1)) + "\n", encoding="utf-8"
        )
        # Assert on the observable effect — data actually ingested — rather
        # than on the daemon's internals.
        from argus.store.db import open_db
        from argus.store.repository import Repository

        found = False
        for _ in range(200):
            # "The file exists" is not "the schema is ready" — SQLite creates
            # the file the moment it is opened, before migrations run, so a
            # fast probe queries a table-less DB. Treat every not-yet state
            # (missing file, missing table, locked) as "keep waiting" rather
            # than as a failure; the assert below is what decides the outcome.
            try:
                probe = open_db(data_dir / "argus.db", read_only=True)
                try:
                    found = bool(Repository(probe).list_sessions(limit=5))
                finally:
                    probe.close()
            except (sqlite3.OperationalError, sqlite3.DatabaseError, OSError):
                pass
            if found:
                break
            threading.Event().wait(0.05)
        assert found, "daemon never ingested the newly created ~/.claude"
    finally:
        stop.set()
        t.join(10)
    assert not error, f"daemon raised: {error}"
