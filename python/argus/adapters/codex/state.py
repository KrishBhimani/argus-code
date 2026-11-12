"""Per-file running state, folded record by record.

The same ``FileState.apply`` drives the head scan (state at an offset, nothing
emitted) and the real read in ``ingest_file``, so both always agree -- that is
what makes a chunked ingest equal a one-pass ingest. ``ContextCache`` only
remembers the state where the last read ended; a miss rescans the head and
yields the identical state.
"""
from __future__ import annotations

import copy
import threading
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .discover import thread_info_from_meta
from .lines import Record, read_records
from .records import call_from_record, is_task_end, is_task_start, legacy_total_of

#: Re-parse at most this much of a still-open task per read (bounded cost on
#: pathological multi-hour tasks; beyond it reads resume at the offset).
MAX_REPARSE_BYTES = 16 * 1024 * 1024


@dataclass
class FileState:
    meta: dict[str, Any] | None = None
    cutoff: int | None = None  # skip records with ordinal < cutoff (copied history)
    models: dict[str, tuple[str, str | None]] = field(default_factory=dict)
    last_model: str | None = None
    last_effort: str | None = None
    saw_usage_record: bool = False
    legacy_total: int = -1
    counted_call_ids: set[str] = field(default_factory=set)
    open_task_offset: int | None = None

    def copy(self) -> "FileState":
        return copy.deepcopy(self)

    def skips(self, rec: Record) -> bool:
        if self.meta is None and rec.type == "session_meta":
            return False  # the file's own header (ordinal 0) is never skipped
        return self.cutoff is not None and rec.ordinal is not None and rec.ordinal < self.cutoff

    def model_for(self, codex_turn_id: str | None) -> tuple[str, str | None]:
        if codex_turn_id is not None and codex_turn_id in self.models:
            return self.models[codex_turn_id]
        return (self.last_model or "unknown", self.last_effort)

    def apply(self, rec: Record) -> None:
        p, t = rec.payload, rec.type
        if t == "session_meta":
            if self.meta is None:
                self.meta = p
                info = thread_info_from_meta(p)
                self.cutoff = info.history_start_ordinal if info else None
            return
        if t == "turn_context":
            model, effort = p.get("model"), p.get("effort")
            if isinstance(model, str) and model:
                self.last_model = model
                self.last_effort = effort if isinstance(effort, str) else None
                tid = p.get("turn_id")
                if isinstance(tid, str):
                    self.models[tid] = (model, self.last_effort)
            return
        if t == "token_usage_record":
            self.saw_usage_record = True
            return
        if is_task_start(rec):
            self.open_task_offset = rec.offset
        elif is_task_end(rec):
            self.open_task_offset = None
        elif t == "event_msg" and p.get("type") == "thread_settings_applied" and self.last_model is None:
            ts = p.get("thread_settings")
            m = ts.get("model") if isinstance(ts, dict) else None
            if isinstance(m, str) and m:
                self.last_model = m
        elif t == "event_msg" and p.get("type") == "token_count":
            total = legacy_total_of(p)
            if total is not None and total > self.legacy_total:
                self.legacy_total = total
        call = call_from_record(rec)
        if call is not None:
            self.counted_call_ids.add(call.call_id)


def scan(path: Path, upto: int) -> tuple[FileState, FileState | None]:
    """Fold ``[0, upto)``: (state at ``upto``, state just before the still-open
    task's ``task_started`` -- or None when no task is open)."""
    state = FileState()
    open_state: FileState | None = None
    pos = 0
    while pos < upto:
        recs, _errs, end = read_records(path, pos, stop=upto)
        if end <= pos:
            break
        for r in recs:
            if state.skips(r):
                continue
            if is_task_start(r):
                open_state = state.copy()
            state.apply(r)
        pos = end
    return state, (open_state if state.open_task_offset is not None else None)


class ContextCache:
    """Per-file ``(offset, state, open_state)`` from the last read. Thread-safe;
    returns copies so concurrent readers never share mutable state."""

    def __init__(self, max_files: int = 256) -> None:
        self._lock = threading.Lock()
        self._d: OrderedDict[str, tuple[int, FileState, FileState | None]] = OrderedDict()
        self._max = max_files

    def get(self, path: Path, offset: int) -> tuple[FileState, FileState | None] | None:
        with self._lock:
            hit = self._d.get(str(path))
            if hit is None or hit[0] != offset:
                return None
            self._d.move_to_end(str(path))
            return hit[1].copy(), (hit[2].copy() if hit[2] is not None else None)

    def put(self, path: Path, offset: int, state: FileState, open_state: FileState | None) -> None:
        with self._lock:
            self._d[str(path)] = (offset, state.copy(), open_state.copy() if open_state else None)
            self._d.move_to_end(str(path))
            while len(self._d) > self._max:
                self._d.popitem(last=False)


def window_for(path: Path, from_offset: int, cache: ContextCache) -> tuple[int, FileState]:
    """Where this read starts and the state just before it.

    A task still open at ``from_offset`` is re-parsed from its ``task_started``
    so its usage records, tool items and counts are always seen together."""
    if from_offset <= 0:
        return 0, FileState()
    hit = cache.get(path, from_offset)
    state, open_state = hit if hit is not None else scan(path, from_offset)
    if (
        open_state is not None
        and state.open_task_offset is not None
        and from_offset - state.open_task_offset <= MAX_REPARSE_BYTES
    ):
        return state.open_task_offset, open_state
    return from_offset, state
