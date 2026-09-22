"""A stale PID file must never make argus act on an unrelated process (H6).

REGRESSION: ``is_running(pid)`` only checked that *some* process had the PID.
After an unclean argusd exit (Windows reboot, hard TerminateProcess) the OS can
reuse the PID; ``argus daemon stop`` then killed whatever process got it,
``argus start`` went read-only, and ``daemon run`` refused to start.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time

import pytest
from typer.testing import CliRunner

from argus.daemon import pidfile, process


@pytest.fixture
def unrelated_process():
    """A live process that is NOT argusd (stands in for a reused PID)."""
    p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        yield p
    finally:
        p.kill()
        p.wait(10)


def _write_record(data_dir, pid: int, start: str | None) -> None:
    pidfile.path(data_dir).parent.mkdir(parents=True, exist_ok=True)
    body = str(pid) if start is None else json.dumps({"pid": pid, "start": start})
    pidfile.path(data_dir).write_text(body, encoding="utf-8")


def test_write_records_start_token_and_verifies_self(tmp_path):
    pidfile.write(tmp_path, os.getpid())
    rec = pidfile.read_record(tmp_path)
    assert rec is not None and rec.pid == os.getpid()
    if sys.platform.startswith("linux"):
        assert rec.start  # /proc start time is always available on Linux
    assert pidfile.read(tmp_path) == os.getpid()  # old API still works
    assert pidfile.live_pid(tmp_path) == os.getpid()


def test_reused_pid_is_not_ours(tmp_path, unrelated_process):
    _write_record(tmp_path, unrelated_process.pid, "not-its-start-time")
    assert pidfile.is_running(unrelated_process.pid)
    assert pidfile.live_pid(tmp_path) is None


def test_stop_never_kills_a_process_with_a_reused_pid(tmp_path, unrelated_process):
    _write_record(tmp_path, unrelated_process.pid, "not-its-start-time")
    assert process.stop_daemon(tmp_path, timeout=0.5) is False
    time.sleep(0.2)
    assert unrelated_process.poll() is None  # still alive
    assert pidfile.read(tmp_path) is None  # the stale file is cleared


def test_legacy_plain_pid_of_unrelated_process_is_not_trusted(tmp_path, unrelated_process):
    """Old-format files carry no start time: unverified, so never killed."""
    _write_record(tmp_path, unrelated_process.pid, None)
    assert pidfile.live_pid(tmp_path) is None
    assert process.stop_daemon(tmp_path, timeout=0.5) is False
    assert unrelated_process.poll() is None


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="reads /proc/<pid>/cmdline")
def test_legacy_plain_pid_of_a_real_argusd_is_still_recognised(tmp_path):
    """Upgrading while an old argusd runs: its plain-PID file is accepted when
    the process's command line is argusd's, so it can still be stopped."""
    p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)",
                          "-m", "argus.cli", "daemon", "run"])
    try:
        _write_record(tmp_path, p.pid, None)
        assert pidfile.live_pid(tmp_path) == p.pid
    finally:
        p.kill()
        p.wait(10)


def test_status_and_start_ignore_a_reused_pid(tmp_path, unrelated_process):
    from argus.cli import app

    _write_record(tmp_path, unrelated_process.pid, "not-its-start-time")
    res = CliRunner().invoke(app, ["daemon", "status", "--data-dir", str(tmp_path)])
    assert "not running" in res.output


def test_run_foreground_collision_guard_ignores_a_reused_pid(tmp_path, monkeypatch, unrelated_process):
    import threading

    from argus.daemon import service

    class _Rt:
        def __init__(self, *a, **k): ...
        def start(self, *, require_adapters: bool = True): ...
        def try_activate(self) -> bool: return False
        def stop(self): ...

    monkeypatch.setattr(service, "CoreRuntime", _Rt)
    _write_record(tmp_path, unrelated_process.pid, "not-its-start-time")
    stop = threading.Event()
    stop.set()
    service.run_foreground(tmp_path, stop_event=stop, install_signals=False, adapter_poll_sec=0.01)


# ─── Windows branch (mocked; not run on Windows in CI here) ────────────


class _FakeKernel32:
    def __init__(self, creation: int | None):
        self.creation = creation
        self.closed = False

    def OpenProcess(self, access, inherit, pid):  # noqa: N802
        return 0 if self.creation is None else 1234

    def GetProcessTimes(self, handle, creation, exit_, kernel, user):  # noqa: N802
        ft = creation._obj
        ft.dwLowDateTime = self.creation & 0xFFFFFFFF
        ft.dwHighDateTime = self.creation >> 32
        return 1

    def CloseHandle(self, handle):  # noqa: N802
        self.closed = True
        return 1


def test_windows_start_token_reads_creation_time(monkeypatch):
    k = _FakeKernel32(creation=(7 << 32) | 99)
    monkeypatch.setattr(pidfile, "_kernel32", lambda: k)
    assert pidfile._start_token_windows(4242) == str((7 << 32) | 99)
    assert k.closed


def test_windows_start_token_none_when_process_cannot_be_opened(monkeypatch):
    monkeypatch.setattr(pidfile, "_kernel32", lambda: _FakeKernel32(creation=None))
    assert pidfile._start_token_windows(4242) is None


def test_windows_dispatch_uses_creation_time(monkeypatch):
    monkeypatch.setattr(pidfile, "_is_windows", lambda: True)
    monkeypatch.setattr(pidfile, "_start_token_windows", lambda pid: "tok")
    assert pidfile.process_start_token(1) == "tok"
