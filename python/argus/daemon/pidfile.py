"""``~/.argus/argusd.pid`` lifecycle, liveness, and identity checks.

A PID alone doesn't identify a process: after an unclean argusd exit (Windows
reboot, the hard ``TerminateProcess`` stop) the OS may hand the PID to anything
else, and acting on it would kill that process. So the file records the PID
**and the process's start time** (``{"pid": n, "start": "<token>"}``), and a
PID counts as our daemon only while a live process with that PID has the same
start time.

Start-time tokens: ``/proc/<pid>/stat`` field 22 (clock ticks since boot) on
Linux, ``ps -o lstart=`` on other POSIX systems, ``GetProcessTimes`` creation
time (via ctypes) on Windows. No psutil — dependencies stay minimal.

Files written by older versions hold a bare PID. Those are *unverified*: they
are trusted only when the process's command line is argusd's (POSIX), never
killed on PID alone, and otherwise treated as stale.
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

PID_FILENAME = "argusd.pid"

logger = logging.getLogger("argus")


@dataclass(frozen=True)
class PidRecord:
    pid: int
    start: str | None  # None: legacy bare-PID file, identity unverified


def path(data_dir: Path) -> Path:
    return Path(data_dir) / PID_FILENAME


def write(data_dir: Path, pid: int) -> None:
    p = path(data_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"pid": pid, "start": process_start_token(pid)}), encoding="utf-8")


def read_record(data_dir: Path) -> PidRecord | None:
    try:
        text = path(data_dir).read_text(encoding="utf-8").strip()
    except (FileNotFoundError, OSError):
        return None
    try:
        obj = json.loads(text)
    except ValueError:
        return None
    if isinstance(obj, int) and not isinstance(obj, bool):  # legacy "12345"
        return PidRecord(obj, None)
    if isinstance(obj, dict) and isinstance(obj.get("pid"), int):
        start = obj.get("start")
        return PidRecord(obj["pid"], start if isinstance(start, str) and start else None)
    return None


def read(data_dir: Path) -> int | None:
    """The recorded PID (unverified — use :func:`live_pid` before acting on it)."""
    rec = read_record(data_dir)
    return rec.pid if rec else None


def remove(data_dir: Path) -> None:
    try:
        path(data_dir).unlink()
    except FileNotFoundError:
        pass


def is_ours(rec: PidRecord | None) -> bool:
    """True only if ``rec`` still names the argusd process that wrote it."""
    if rec is None or not is_running(rec.pid):
        return False
    if rec.start is not None:
        return process_start_token(rec.pid) == rec.start
    # Legacy bare PID: accept only a process that is visibly argusd.
    cmd = _cmdline(rec.pid)
    if cmd is not None and "argus.cli" in cmd and "daemon" in cmd:
        return True
    logger.warning(
        "argusd PID file %s holds a bare PID (%d) from an older argus that can't be "
        "verified as argusd; treating it as stale and not acting on that process.",
        PID_FILENAME,
        rec.pid,
    )
    return False


def live_pid(data_dir: Path) -> int | None:
    """PID of the running argusd that wrote the PID file, else None."""
    rec = read_record(data_dir)
    return rec.pid if is_ours(rec) else None


def is_running(pid: int | None) -> bool:
    if pid is None or pid <= 0:
        return False
    if _is_windows():
        return _is_running_windows(pid)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists but owned by another user
    except OSError:
        return False
    return True


def process_start_token(pid: int) -> str | None:
    """Opaque, stable-for-the-process start time; None if unavailable."""
    if pid <= 0:
        return None
    if _is_windows():
        return _start_token_windows(pid)
    try:
        stat = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8", errors="replace")
    except OSError:
        stat = None
    if stat is not None:
        # "pid (comm) state ..." — comm may contain spaces/parens, so split
        # after the LAST ')'; the rest starts at field 3, starttime is field 22.
        fields = stat.rsplit(")", 1)[-1].split()
        return fields[19] if len(fields) > 19 else None
    try:
        out = subprocess.run(
            ["ps", "-o", "lstart=", "-p", str(pid)],
            capture_output=True, text=True, timeout=5, check=False,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None
    return out or None


def _cmdline(pid: int) -> str | None:
    """Space-joined command line of ``pid`` (POSIX); None if unavailable."""
    if _is_windows():
        return None
    try:
        raw = Path(f"/proc/{pid}/cmdline").read_bytes()
        return raw.replace(b"\0", b" ").decode("utf-8", errors="replace").strip() or None
    except OSError:
        pass
    try:
        out = subprocess.run(
            ["ps", "-o", "command=", "-p", str(pid)],
            capture_output=True, text=True, timeout=5, check=False,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None
    return out or None


def _is_windows() -> bool:
    return os.name == "nt"


def _kernel32():  # pragma: no cover - Windows only; tests inject a fake
    import ctypes

    return ctypes.windll.kernel32


def _start_token_windows(pid: int) -> str | None:
    """Process creation time (100 ns FILETIME ticks) via GetProcessTimes."""
    import ctypes
    from ctypes import wintypes

    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    kernel32 = _kernel32()
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return None
    try:
        creation, exit_, kernel, user = (wintypes.FILETIME() for _ in range(4))
        ok = kernel32.GetProcessTimes(
            handle,
            ctypes.byref(creation),
            ctypes.byref(exit_),
            ctypes.byref(kernel),
            ctypes.byref(user),
        )
        if not ok:
            return None
        return str((creation.dwHighDateTime << 32) | creation.dwLowDateTime)
    finally:
        kernel32.CloseHandle(handle)


def _is_running_windows(pid: int) -> bool:
    import ctypes
    from ctypes import wintypes

    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    STILL_ACTIVE = 259

    kernel32 = _kernel32()
    handle = kernel32.OpenProcess(
        PROCESS_QUERY_LIMITED_INFORMATION, False, pid
    )
    if not handle:
        return False
    try:
        code = wintypes.DWORD()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
            return False
        return code.value == STILL_ACTIVE
    finally:
        kernel32.CloseHandle(handle)
