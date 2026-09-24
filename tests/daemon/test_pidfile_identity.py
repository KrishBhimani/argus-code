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


# ─── Review follow-ups: identity that can't be determined ──────────────


def test_own_file_without_start_token_is_still_recognised(tmp_path, monkeypatch):
    """If the start time can't be read (no /proc, no ps), the daemon's own
    file must not look stale — or start would time out and stop would drop
    the PID file of a live daemon."""
    monkeypatch.setattr(pidfile, "process_start_token", lambda pid: None)
    monkeypatch.setattr(pidfile, "_cmdline", lambda pid: "python -P -m argus.cli daemon run --data-dir x")
    pidfile.write(tmp_path, os.getpid())
    assert pidfile.live_pid(tmp_path) == os.getpid()


def test_console_script_command_line_counts_as_argusd(monkeypatch):
    monkeypatch.setattr(pidfile, "process_start_token", lambda pid: None)
    monkeypatch.setattr(pidfile, "_cmdline", lambda pid: "/usr/local/bin/argus daemon run")
    assert pidfile.check(pidfile.PidRecord(os.getpid(), None)) == pidfile.VERIFIED


def test_unverifiable_live_pid_blocks_a_second_daemon_but_is_never_killed(
    tmp_path, monkeypatch, unrelated_process
):
    """Windows upgrade case: an old argusd wrote a bare PID and its identity
    can't be checked. Starting a second writer next to it is worse than asking
    the user, and killing it on a PID alone is what this fix forbids."""
    monkeypatch.setattr(pidfile, "_cmdline", lambda pid: None)
    monkeypatch.setattr(pidfile, "_image_name", lambda pid: None)
    _write_record(tmp_path, unrelated_process.pid, None)

    assert pidfile.check(pidfile.read_record(tmp_path)) == pidfile.UNVERIFIED
    assert pidfile.live_pid(tmp_path) is None
    assert pidfile.running_pid(tmp_path) == unrelated_process.pid
    assert process.stop_daemon(tmp_path, timeout=0.5) is False
    assert unrelated_process.poll() is None
    assert pidfile.read(tmp_path) == unrelated_process.pid  # kept: not provably stale

    from argus.cli import app

    res = CliRunner().invoke(app, ["daemon", "start", "--data-dir", str(tmp_path)])
    assert "already running" in res.output and res.exit_code == 0


def test_windows_image_name_that_is_not_python_is_stale(monkeypatch, unrelated_process):
    monkeypatch.setattr(pidfile, "_cmdline", lambda pid: None)
    monkeypatch.setattr(pidfile, "_image_name", lambda pid: "notepad.exe")
    assert pidfile.check(pidfile.PidRecord(unrelated_process.pid, None)) == pidfile.STALE


class _FakeKernel32Image(_FakeKernel32):
    def QueryFullProcessImageNameW(self, handle, flags, buf, size):  # noqa: N802
        buf.value = "C:\\Python312\\python.exe"
        return 1


def test_windows_image_name_reads_exe_basename(monkeypatch):
    monkeypatch.setattr(pidfile, "_kernel32", lambda: _FakeKernel32Image(creation=1))
    assert pidfile._image_name_windows(4242) == "python.exe"


# ─── Review follow-ups (2): honest stop output, re-verify before force-kill ─


def test_stop_on_unverifiable_pid_says_so_instead_of_not_running(
    tmp_path, monkeypatch, unrelated_process
):
    """`daemon stop` used to print "argusd is not running." here, which it
    hadn't checked; it had just declined to act. The user must learn that
    something may still be running and how to resolve it."""
    from argus.cli import app

    monkeypatch.setattr(pidfile, "_cmdline", lambda pid: None)
    monkeypatch.setattr(pidfile, "_image_name", lambda pid: None)
    _write_record(tmp_path, unrelated_process.pid, None)

    res = CliRunner().invoke(app, ["daemon", "stop", "--data-dir", str(tmp_path)])
    assert "not running" not in res.output
    assert "can't be verified" in res.output and str(pidfile.path(tmp_path)) in res.output
    assert res.exit_code == 1  # nothing was stopped
    assert unrelated_process.poll() is None


def test_force_kill_after_timeout_reverifies_identity(tmp_path, monkeypatch):
    """Between the stop signal and the force-kill (up to `timeout` later) the
    daemon may exit and its PID be reused; the force-kill must not hit that."""
    _write_record(tmp_path, 4242, "tok")
    verdicts = iter([pidfile.VERIFIED])  # first check: ours; afterwards: stale
    monkeypatch.setattr(pidfile, "check", lambda rec: next(verdicts, pidfile.STALE))
    monkeypatch.setattr(pidfile, "is_running", lambda pid: True)  # PID stays taken
    kills = []
    monkeypatch.setattr(process, "_terminate_windows", lambda pid: kills.append(pid))
    monkeypatch.setattr(process.os, "kill", lambda pid, sig: kills.append(pid), raising=False)

    process.stop_daemon(tmp_path, timeout=0.2)
    assert kills == [4242]  # the initial stop only; no force-kill of the reused PID
