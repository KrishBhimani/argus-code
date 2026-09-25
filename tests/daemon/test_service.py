"""Foreground daemon entry point: collision guard, PID lifecycle, signals."""
from __future__ import annotations

import threading

import pytest

from argus.daemon import pidfile, service


class _FakeRuntime:
    """Stand-in for CoreRuntime — keep this signature-compatible with the real
    one, or these tests pass while the daemon is broken."""

    def __init__(self, *a, **k):
        self.started = False
        self.stopped = False
        self.require_adapters = None
        self.activate_calls = 0
        self.started_event = threading.Event()

    def start(self, *, require_adapters: bool = True):
        self.require_adapters = require_adapters
        self.started = True
        self.started_event.set()

    def try_activate(self) -> bool:
        self.activate_calls += 1
        return False

    def stop(self):
        self.stopped = True


def test_run_foreground_writes_pid_runs_and_cleans_up(tmp_path, monkeypatch):
    fake = _FakeRuntime()
    monkeypatch.setattr(service, "CoreRuntime", lambda *a, **k: fake)
    stop = threading.Event()

    t = threading.Thread(
        target=service.run_foreground,
        args=(tmp_path,),
        kwargs={"stop_event": stop, "install_signals": False},
        daemon=True,
    )
    t.start()

    # Wait for the thing asserted on — the runtime having started — not a proxy.
    # run_foreground writes the PID file *before* runtime.start(), so polling
    # for the file and then asserting `started` raced on slow CI runners
    # (failed on macOS py3.11). Generous budget: a claim about correctness,
    # not machine speed (tests/AGENTS.md).
    assert fake.started_event.wait(10), "runtime.start() was never called"
    assert pidfile.read(tmp_path) is not None
    assert fake.started is True
    # The daemon must tolerate a missing ~/.claude rather than exiting (issue #11).
    assert fake.require_adapters is False

    stop.set()
    t.join(timeout=3)
    assert fake.stopped is True
    assert pidfile.read(tmp_path) is None  # cleaned up on exit


def test_run_foreground_refuses_when_live_daemon_exists(tmp_path, monkeypatch):
    pidfile.write(tmp_path, 999999)  # a PID that isn't us
    # The guard trusts only a *verified* argusd (same PID and start time).
    monkeypatch.setattr(
        pidfile, "check",
        lambda rec: pidfile.VERIFIED if rec is not None and rec.pid == 999999 else pidfile.STALE,
    )
    monkeypatch.setattr(service, "CoreRuntime", _FakeRuntime)

    with pytest.raises(service.DaemonAlreadyRunning):
        service.run_foreground(
            tmp_path, stop_event=threading.Event(), install_signals=False
        )
