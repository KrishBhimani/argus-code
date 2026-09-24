"""Watchdog-based live file tailing.

Per-path debouncer mirrors chokidar's ``awaitWriteFinish``: each fs event
cancels and replaces the path's timer; when it fires (100ms quiet
window), the path is handed to the ingest worker.
"""
from __future__ import annotations

import logging
import queue
import threading
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

from ..adapters.base import Adapter
from ..pricing.types import PricingTable
from ..store.repository import Repository
from .pipeline import ingest_file

logger = logging.getLogger(__name__)

# Sentinel pushed onto the ingest queue to tell the worker to exit.
_STOP = object()


class _DebouncedHandler(FileSystemEventHandler):
    """Coalesce a burst of fs events on one path into a single ingest call."""

    def __init__(
        self,
        adapter: Adapter,
        ingest_queue: "queue.Queue[Any]",
        stability_ms: int,
        is_extra: bool = False,
    ) -> None:
        self._adapter = adapter
        self._queue = ingest_queue
        self._stability = stability_ms / 1000.0
        self._timers: dict[Path, threading.Timer] = {}
        self._lock = threading.Lock()
        self._is_extra = is_extra

    def _on_path(self, raw_path: str) -> None:
        path = Path(raw_path)
        if self._is_extra:
            # Extras (history.jsonl) — pass through unconditionally; adapter
            # decides what to do with the path.
            pass
        else:
            if path.suffix != ".jsonl":
                return
            if self._adapter.should_skip(path):
                return

        with self._lock:
            existing = self._timers.pop(path, None)
            if existing is not None:
                existing.cancel()
            t = threading.Timer(
                self._stability,
                self._enqueue,
                args=(path,),
            )
            t.daemon = True
            self._timers[path] = t
            t.start()

    def _enqueue(self, path: Path) -> None:
        with self._lock:
            self._timers.pop(path, None)
        self._queue.put(("extra" if self._is_extra else "session", self._adapter, path))

    def on_created(self, event: FileSystemEvent) -> None:
        if not event.is_directory:
            self._on_path(event.src_path)

    def on_modified(self, event: FileSystemEvent) -> None:
        if not event.is_directory:
            self._on_path(event.src_path)


def _ingest_worker(
    ingest_queue: "queue.Queue[Any]", repo: Repository, table: PricingTable
) -> None:
    while True:
        item = ingest_queue.get()
        if item is _STOP:
            return
        kind, adapter, path = item
        try:
            if kind == "extra":
                adapter.ingest_extra(path, repo)
            else:
                ingest_file(adapter, path, repo, table)
        except Exception as e:  # noqa: BLE001
            repo.record_parse_error(
                {
                    "file": str(path),
                    "byte_offset": -1,
                    "reason": (
                        f"[history] {e}" if kind == "extra" else f"[ingest] {e}"
                    ),
                    "raw_line_truncated": "",
                }
            )


class WatcherHandle:
    """Owns the Observer, the ingest worker thread, and shutdown."""

    def __init__(
        self,
        observers: list[Observer],
        ingest_queue: "queue.Queue[Any]",
        worker: threading.Thread,
    ) -> None:
        self._observers = observers
        self._queue = ingest_queue
        self._worker = worker

    def stop(self, timeout: float = 10.0) -> bool:
        """Stop observers and the ingest worker; True once all have exited.

        The worker ingests through the shared DB connection, so callers must
        not close the connection while this returns False. Bounded so a wedged
        ingest can't hang shutdown forever.
        """
        deadline = time.monotonic() + timeout
        for o in self._observers:
            o.stop()
        for o in self._observers:
            o.join(max(0.0, deadline - time.monotonic()))
        self._queue.put(_STOP)
        self._worker.join(max(0.0, deadline - time.monotonic()))
        return not self._worker.is_alive() and not any(o.is_alive() for o in self._observers)


def start_watcher(
    adapters: list[Adapter],
    repo: Repository,
    table: PricingTable,
    *,
    stability_threshold_ms: int = 100,
) -> WatcherHandle:
    """Start an Observer per adapter root + per extra-watch path."""
    ingest_queue: "queue.Queue[Any]" = queue.Queue()
    worker = threading.Thread(
        target=_ingest_worker,
        args=(ingest_queue, repo, table),
        name="argus-ingest",
        daemon=True,
    )
    worker.start()

    observers: list[Observer] = []
    for a in adapters:
        root = a.root_path()
        if root.exists():
            obs = Observer()
            obs.schedule(
                _DebouncedHandler(a, ingest_queue, stability_threshold_ms),
                str(root),
                recursive=True,
            )
            obs.start()
            observers.append(obs)
        for extra in a.extra_watch_paths():
            if not extra.exists():
                continue
            # Watch the containing dir; filter to the filename in handler.
            obs = Observer()
            obs.schedule(
                _DebouncedHandler(
                    a, ingest_queue, stability_threshold_ms, is_extra=True
                ),
                str(extra.parent),
                recursive=False,
            )
            obs.start()
            observers.append(obs)

    return WatcherHandle(observers, ingest_queue, worker)
