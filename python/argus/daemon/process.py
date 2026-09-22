"""Spawn the daemon as a detached process; stop a running daemon.

The detached child runs ``<python> -m argus.cli daemon run`` — absolute
interpreter + module entry, never the ``argus`` console script (service/PATH
robustness). The child writes its own PID via run_foreground; the parent does
not pre-write the PID file (that would trip the child's collision guard).
"""
from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

from . import pidfile


def daemon_argv(data_dir: Path) -> list[str]:
    return [
        sys.executable,
        # -P (3.11+) keeps the *caller's* cwd off the child's sys.path. Without
        # it, `-m` prepends cwd, so running `argus daemon start` from inside a
        # checkout that ships its own `argus/` directory would run that code
        # instead of the installed package -- arbitrary execution in a detached
        # process that outlives the shell.
        "-P",
        "-m",
        "argus.cli",
        "daemon",
        "run",
        "--data-dir",
        str(Path(data_dir)),
    ]


def spawn_daemon(data_dir: Path) -> int:
    """Launch the foreground daemon detached; return the child PID."""
    kwargs: dict = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
    }
    if os.name == "nt":
        DETACHED_PROCESS = 0x00000008
        CREATE_NEW_PROCESS_GROUP = 0x00000200
        kwargs["creationflags"] = DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True

    proc = subprocess.Popen(daemon_argv(data_dir), **kwargs)
    return proc.pid


def wait_for_pidfile(data_dir: Path, timeout: float = 3.0) -> int | None:
    """Poll until the child writes its PID file; return the PID or None."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        pid = pidfile.live_pid(data_dir)
        if pid is not None:
            return pid
        time.sleep(0.05)
    return None


def stop_daemon(data_dir: Path, timeout: float = 10.0) -> bool:
    """Stop a running daemon. Returns True if one was running, else False.

    Only a process verified as the argusd that wrote the PID file is signalled
    (``pidfile.live_pid``: same PID *and* same start time). A PID reused by
    another process after an unclean exit is never touched; the stale file is
    just cleared.
    """
    pid = pidfile.live_pid(data_dir)
    if pid is None:
        pidfile.remove(data_dir)  # clear any stale file
        return False

    if os.name == "nt":
        _terminate_windows(pid)
    else:
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pidfile.remove(data_dir)
            return True

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not pidfile.is_running(pid):
            pidfile.remove(data_dir)
            return True
        time.sleep(0.1)

    # Still alive after timeout — force kill.
    if os.name == "nt":
        _terminate_windows(pid)
    else:
        try:
            os.kill(pid, signal.SIGKILL)
        except (ProcessLookupError, AttributeError):
            pass
    pidfile.remove(data_dir)
    return True


def _terminate_windows(pid: int) -> None:
    """Hard-terminate on Windows (no critical in-memory state to flush)."""
    import ctypes

    PROCESS_TERMINATE = 0x0001
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.OpenProcess(PROCESS_TERMINATE, False, pid)
    if handle:
        try:
            kernel32.TerminateProcess(handle, 1)
        finally:
            kernel32.CloseHandle(handle)
