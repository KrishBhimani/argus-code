"""Find Codex rollout files and the thread each belongs to.

- Home = ``$CODEX_HOME`` when set (a missing dir means "absent"; never fall back
  to ~/.codex then), else ``~/.codex``.
- Only ``sessions/`` and ``archived_sessions/`` are read. auth.json, sqlite
  state, config, memories, logs, plugins, skills, history.jsonl: never opened.
- Identity comes from the first record (``session_meta``), not the filename.
- Every path is realpath-contained under a session dir (symlinks/junctions).
"""
from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..claude_code.discover import _safe_realpath_under

SESSION_DIRS = ("sessions", "archived_sessions")
#: The first record carries the full base instructions (tens of KB).
_MAX_HEAD_BYTES = 4 * 1024 * 1024


def codex_home() -> Path:
    env = os.environ.get("CODEX_HOME")
    return Path(env).expanduser() if env else Path.home() / ".codex"


def contained(path: Path, root: Path) -> Path | None:
    """Resolved ``path`` if it is a file inside one of ``root``'s session dirs."""
    for d in SESSION_DIRS:
        try:
            base = (root / d).resolve(strict=True)
        except (OSError, RuntimeError):
            continue
        hit = _safe_realpath_under(path, base)
        if hit is not None and hit != base:
            return hit
    return None


@dataclass(frozen=True)
class ThreadInfo:
    thread_id: str
    parent_thread_id: str | None
    history_start_ordinal: int | None
    meta: dict[str, Any] = field(compare=False, hash=False, repr=False)


def parent_of(meta: dict[str, Any]) -> str | None:
    p = meta.get("parent_thread_id")
    if isinstance(p, str) and p:
        return p
    src = meta.get("source")
    sub = src.get("subagent") if isinstance(src, dict) else None
    spawn = sub.get("thread_spawn") if isinstance(sub, dict) else None
    p = spawn.get("parent_thread_id") if isinstance(spawn, dict) else None
    return p if isinstance(p, str) and p else None


def _history_start(meta: dict[str, Any]) -> int | None:
    v = meta.get("subagent_history_start_ordinal")
    if isinstance(v, int):
        return v
    base = meta.get("history_base")
    v = base.get("end_ordinal_exclusive") if isinstance(base, dict) else None
    return v if isinstance(v, int) else None


def thread_info_from_meta(meta: dict[str, Any]) -> ThreadInfo | None:
    tid = meta.get("id")
    if not isinstance(tid, str) or not tid:
        return None
    return ThreadInfo(tid, parent_of(meta), _history_start(meta), meta)


def peek_thread(path: Path) -> ThreadInfo | None:
    """Identity from the first line; None if it isn't (yet) a rollout."""
    try:
        with open(path, "rb") as fh:
            head = fh.read(_MAX_HEAD_BYTES)
    except OSError:
        return None
    nl = head.find(b"\n")
    if nl == -1:
        return None  # first line still being written
    try:
        obj = json.loads(head[:nl])
    except ValueError:
        return None
    if not isinstance(obj, dict) or obj.get("type") != "session_meta":
        return None
    payload = obj.get("payload")
    return thread_info_from_meta(payload) if isinstance(payload, dict) else None


class ThreadIndex:
    """``path -> ThreadInfo`` for every rollout under the session dirs.

    Successful peeks are cached (a first line never changes); failures are
    retried on the next refresh, so a file caught mid-write is picked up later.
    """

    def __init__(self, root: Path) -> None:
        self._root = root
        self._lock = threading.Lock()
        self._info: dict[Path, ThreadInfo] = {}

    def refresh(self) -> None:
        found: set[Path] = set()
        for d in SESSION_DIRS:
            base = self._root / d
            if base.is_dir():
                found.update(p for p in base.rglob("*.jsonl") if p.is_file())
        with self._lock:
            known = set(self._info)
        new: dict[Path, ThreadInfo] = {}
        for p in found - known:
            if contained(p, self._root) is None:
                continue
            info = peek_thread(p)
            if info is not None:
                new[p] = info
        with self._lock:
            self._info = {p: i for p, i in self._info.items() if p in found}
            self._info.update(new)

    def info(self, path: Path) -> ThreadInfo | None:
        with self._lock:
            hit = self._info.get(path)
        if hit is not None:
            return hit
        if contained(path, self._root) is None:
            return None
        info = peek_thread(path)
        if info is not None:
            with self._lock:
                self._info[path] = info
        return info

    def _snapshot(self) -> dict[Path, ThreadInfo]:
        with self._lock:
            return dict(self._info)

    def top_level_files(self) -> list[Path]:
        return sorted(p for p, i in self._snapshot().items() if i.parent_thread_id is None)

    def descendant_files(self, parent_file: Path) -> list[Path]:
        """Every sub-agent file whose top-level ancestor is ``parent_file``'s
        thread, at any depth (Argus sub-sessions are one level: flattened)."""
        root_info = self.info(parent_file)
        if root_info is None or root_info.parent_thread_id is not None:
            return []
        snap = self._snapshot()
        parent_by_thread: dict[str, str] = {}
        for i in snap.values():
            if i.parent_thread_id:
                parent_by_thread.setdefault(i.thread_id, i.parent_thread_id)

        def top(tid: str) -> str:
            seen: set[str] = set()
            while tid in parent_by_thread and tid not in seen:
                seen.add(tid)
                tid = parent_by_thread[tid]
            return tid

        return sorted(
            p for p, i in snap.items()
            if i.parent_thread_id is not None and top(i.thread_id) == root_info.thread_id
        )
