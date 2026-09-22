"""Blocking foreground daemon mode (``argus daemon run``).

Invoked directly by the detached ``daemon start`` subprocess AND by the OS
service managers (systemd / launchd / Scheduled Tasks). Writes the PID file,
runs a read-write CoreRuntime, and blocks until signalled, then cleans up.

The collision guard makes ``run`` independently safe: if a live daemon's PID
is already on file, it refuses to start rather than orphan the original.
"""
from __future__ import annotations

import logging
import os
import signal
import threading
from pathlib import Path

from ..core.runtime import CoreRuntime
from . import pidfile
from .logging import setup_file_logging

logger = logging.getLogger("argus")


class DaemonAlreadyRunning(RuntimeError):
    def __init__(self, pid: int) -> None:
        super().__init__(f"daemon already running, PID {pid}")
        self.pid = pid


def run_foreground(
    data_dir: Path,
    *,
    stop_event: threading.Event | None = None,
    install_signals: bool = True,
    adapter_poll_sec: float = 30.0,
) -> None:
    data_dir = Path(data_dir)
    stop_event = stop_event or threading.Event()
    me = os.getpid()

    existing = pidfile.live_pid(data_dir)
    if existing is not None and existing != me:
        raise DaemonAlreadyRunning(existing)

    if install_signals:
        setup_file_logging(data_dir)

        def _handle(signum, frame):  # noqa: ANN001
            logger.info("argusd received signal %s — stopping.", signum)
            stop_event.set()

        signal.signal(signal.SIGTERM, _handle)
        signal.signal(signal.SIGINT, _handle)
        # Best-effort graceful path on Windows; harmless if unsupported.
        if os.name == "nt" and hasattr(signal, "SIGBREAK"):
            try:
                signal.signal(signal.SIGBREAK, _handle)
            except (ValueError, OSError):
                pass

    pidfile.write(data_dir, me)
    runtime = CoreRuntime(data_dir, read_only=False)
    try:
        # A missing ~/.claude must not kill the service. On a fresh machine, or
        # one where Claude Code simply hasn't been launched yet, argusd comes up
        # idle and waits for the directory instead of exiting (issue #11).
        runtime.start(require_adapters=False)
        logger.info("argusd started (PID %d).", me)
        # Wake periodically so ingest can begin by itself once ~/.claude shows
        # up; try_activate() is a no-op once we're running.
        while not stop_event.wait(adapter_poll_sec):
            if runtime.try_activate():
                logger.info("Agent data appeared — ingest started.")
    finally:
        runtime.stop()
        pidfile.remove(data_dir)
        logger.info("argusd stopped.")
