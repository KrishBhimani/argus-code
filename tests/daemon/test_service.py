"""Foreground daemon entry point: collision guard, PID lifecycle, signals."""
from __future__ import annotations

import threading
import time

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

    def start(self, *, require_adapters: bool = True):
        self.started = True
        self.require_adapters = require_adapters

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

    # Wait for the PID file to appear.
    for _ in range(100):
        if pidfile.read(tmp_path) is not None:
            break
        time.sleep(0.01)
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
    monkeypatch.setattr(pidfile, "is_ours", lambda rec: rec is not None and rec.pid == 999999)
    monkeypatch.setattr(service, "CoreRuntime", _FakeRuntime)

    with pytest.raises(service.DaemonAlreadyRunning):
        service.run_foreground(
            tmp_path, stop_event=threading.Event(), install_signals=False
        )
