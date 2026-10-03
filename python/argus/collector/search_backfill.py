"""Background search-index backfill.

Re-ingests sessions that don't yet have transcript_segments rows. Caller
sets ``enable_transcript_search`` first; the pipeline then writes
segments as a side effect of the re-ingest.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass

from ..adapters.base import Adapter
from ..pricing.types import PricingTable
from ..store.repository import Repository
from .pipeline import ingest_file
from .session_files import files_by_session


@dataclass
class SearchBackfillStatus:
    in_progress: bool
    processed: int
    total: int
    started_at_ms: int | None
    finished_at_ms: int | None


#: Worker thread name. Public so shutdown can find and join it by name.
SEARCH_BACKFILL_THREAD_NAME = "argus-search-backfill"

# Singleton process state.
_state = SearchBackfillStatus(
    in_progress=False, processed=0, total=0, started_at_ms=None, finished_at_ms=None
)
_lock = threading.Lock()
# Set by shutdown; the worker checks it between sessions.
_stop = threading.Event()


def _snapshot() -> SearchBackfillStatus:
    """Copy of the state. Caller must hold ``_lock``."""
    return SearchBackfillStatus(
        in_progress=_state.in_progress,
        processed=_state.processed,
        total=_state.total,
        started_at_ms=_state.started_at_ms,
        finished_at_ms=_state.finished_at_ms,
    )


def get_search_backfill_status() -> SearchBackfillStatus:
    with _lock:
        return _snapshot()


def request_search_backfill_stop() -> None:
    """Ask a running backfill to stop after the session it is on."""
    _stop.set()


def join_search_backfill_threads(timeout: float = 10.0) -> list[str]:
    """Wait for the backfill worker; return names of threads still alive.

    It writes to the shared SQLite connection, so anything that closes the
    connection must come through here (and ``join_first_run_threads``) first.
    """
    deadline = time.monotonic() + timeout
    for t in [x for x in threading.enumerate() if x.name == SEARCH_BACKFILL_THREAD_NAME]:
        t.join(max(0.0, deadline - time.monotonic()))
    return [
        x.name
        for x in threading.enumerate()
        if x.name == SEARCH_BACKFILL_THREAD_NAME and x.is_alive()
    ]


def run_segment_backfill(
    adapters: list[Adapter], repo: Repository, table: PricingTable
) -> SearchBackfillStatus:
    """Kick off (non-blocking) a backfill of missing transcript segments."""
    # Check and claim in one critical section: checking, releasing the lock
    # for discovery and setting in_progress later let two concurrent enable
    # requests both start a worker.
    with _lock:
        if _state.in_progress:
            return _snapshot()
        _state.in_progress = True
        _state.processed = 0
        _state.total = 0
        _state.started_at_ms = int(time.time() * 1000)
        _state.finished_at_ms = None
        _stop.clear()

    try:
        # "<agent>:<native id>" -> every top-level file of that session.
        files = files_by_session(adapters)

        candidates = [
            c for c in repo.sessions_missing_segments(1000) if "/" not in c["id"]
        ]
    except BaseException:
        with _lock:
            _state.in_progress = False
            _state.finished_at_ms = int(time.time() * 1000)
        raise

    with _lock:
        _state.total = len(candidates)

    def _worker() -> None:
        try:
            for c in candidates:
                if _stop.is_set():
                    break
                matches = files.get(c["id"])
                if not matches:
                    with _lock:
                        _state.processed += 1
                    continue
                for adapter, file in matches:
                    repo.set_file_offset(str(file), 0)
                    try:
                        ingest_file(adapter, file, repo, table)  # type: ignore[arg-type]
                    except Exception as e:  # noqa: BLE001
                        repo.record_parse_error(
                            {
                                "file": str(file),
                                "byte_offset": -1,
                                "reason": f"[search-backfill] {e}",
                                "raw_line_truncated": "",
                            }
                        )
                with _lock:
                    _state.processed += 1
        finally:
            with _lock:
                _state.in_progress = False
                _state.finished_at_ms = int(time.time() * 1000)

    threading.Thread(target=_worker, name=SEARCH_BACKFILL_THREAD_NAME, daemon=True).start()
    return get_search_backfill_status()
