"""Detached spawn + stop. Subprocess/Win32 calls are mocked."""
from __future__ import annotations

import os

import pytest

from argus.daemon import pidfile, process


class _FakePopen:
    def __init__(self, args, **kwargs):
        self.args = args
        self.kwargs = kwargs
        self.pid = 54321


def test_spawn_daemon_uses_python_module_invocation(tmp_path, monkeypatch):
    captured = {}

    def fake_popen(args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return _FakePopen(args, **kwargs)

    monkeypatch.setattr(process.subprocess, "Popen", fake_popen)
    pid = process.spawn_daemon(tmp_path)

    assert pid == 54321
    # Absolute python + module entry, not the 'argus' console script.
    # -P keeps the caller's cwd off the child's sys.path (see
    # tests/daemon/test_spawn_safe_path.py).
    assert captured["args"][0] == process.sys.executable
    assert captured["args"][1:6] == ["-P", "-m", "argus.cli", "daemon", "run"]
    assert "--data-dir" in captured["args"]
    # Detached stdio.
    assert captured["kwargs"]["stdout"] == process.subprocess.DEVNULL


def test_stop_daemon_returns_false_when_not_running(tmp_path, monkeypatch):
    monkeypatch.setattr(pidfile, "read", lambda d: None)
    assert process.stop_daemon(tmp_path) is False


def test_stop_daemon_signals_live_pid_posix(tmp_path, monkeypatch):
    if os.name == "nt":
        pytest.skip("POSIX-only signal path")

    pidfile.write(tmp_path, 4242)
    liveness = {"alive": True}
    sent = {}

    monkeypatch.setattr(pidfile, "is_running", lambda pid: liveness["alive"])
    # Identity verified (same start time) — only then may stop signal it.
    monkeypatch.setattr(pidfile, "is_ours", lambda rec: liveness["alive"])

    def fake_kill(pid, sig):
        sent["pid"] = pid
        sent["sig"] = sig
        liveness["alive"] = False  # process dies after SIGTERM

    monkeypatch.setattr(process.os, "kill", fake_kill)
    result = process.stop_daemon(tmp_path, timeout=1.0)

    assert result is True
    assert sent["pid"] == 4242
    assert pidfile.read(tmp_path) is None  # cleaned up


def test_stop_daemon_terminates_live_pid_windows(tmp_path, monkeypatch):
    if os.name != "nt":
        pytest.skip("Windows-only terminate path")

    pidfile.write(tmp_path, 4242)
    liveness = {"alive": True}
    terminated = {}

    monkeypatch.setattr(pidfile, "is_running", lambda pid: liveness["alive"])
    # Identity verified (same start time) — only then may stop signal it.
    monkeypatch.setattr(pidfile, "is_ours", lambda rec: liveness["alive"])

    def fake_terminate(pid):
        terminated["pid"] = pid
        liveness["alive"] = False

    monkeypatch.setattr(process, "_terminate_windows", fake_terminate)
    result = process.stop_daemon(tmp_path, timeout=1.0)

    assert result is True
    assert terminated["pid"] == 4242
    assert pidfile.read(tmp_path) is None
