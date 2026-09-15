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


@dataclass
class SearchBackfillStatus:
    in_progress: bool
    processed: int
    total: int
    started_at_ms: int | None
    finished_at_ms: int | None


# Singleton process state.
_state = SearchBackfillStatus(
    in_progress=False, processed=0, total=0, started_at_ms=None, finished_at_ms=None
)
_lock = threading.Lock()


def get_search_backfill_status() -> SearchBackfillStatus:
    with _lock:
        return SearchBackfillStatus(
            in_progress=_state.in_progress,
            processed=_state.processed,
            total=_state.total,
            started_at_ms=_state.started_at_ms,
            finished_at_ms=_state.finished_at_ms,
        )


def run_segment_backfill(
    adapters: list[Adapter], repo: Repository, table: PricingTable
) -> SearchBackfillStatus:
    """Kick off (non-blocking) a backfill of missing transcript segments."""
    with _lock:
        if _state.in_progress:
            return get_search_backfill_status()

    # Build native-id → (adapter, file) map for every adapter's top-level files.
    file_by_basename: dict[str, tuple[Adapter, "object"]] = {}
    for a in adapters:
        for f in a.discover_session_files():
            file_by_basename[a.native_session_id(f)] = (a, f)

    candidates = [
        c for c in repo.sessions_missing_segments(1000) if "/" not in c["id"]
    ]

    with _lock:
        _state.in_progress = True
        _state.processed = 0
        _state.total = len(candidates)
        _state.started_at_ms = int(time.time() * 1000)
        _state.finished_at_ms = None

    def _worker() -> None:
        try:
            for c in candidates:
                id_ = c["id"]
                colon = id_.find(":")
                if colon < 0:
                    with _lock:
                        _state.processed += 1
                    continue
                native = id_[colon + 1 :]
                match = file_by_basename.get(native)
                if match is None:
                    with _lock:
                        _state.processed += 1
                    continue
                adapter, file = match
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

    threading.Thread(target=_worker, name="argus-search-backfill", daemon=True).start()
    return get_search_backfill_status()
