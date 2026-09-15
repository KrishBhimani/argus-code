"""Discover Codex rollout files under ``$CODEX_HOME`` (default ``~/.codex``).

Layout: ``sessions/YYYY/MM/DD/rollout-<local ts>-<thread uuid>[_<rollout uuid>].jsonl``
plus the same under ``archived_sessions/``; cold files may be ``.jsonl.zst``.
A spawned sub-agent is its own rollout in the same tree whose first line names
its parent, so discovery peeks line 1 of every file to build a thread index.

Path-safety mirrors the Claude adapter: every candidate is realpath'd and must
canonicalize under the root (Windows compares case-insensitively).
"""
from __future__ import annotations

import json
import logging
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_IS_WIN = sys.platform == "win32"
SESSION_DIRS = ("sessions", "archived_sessions")
_UUID = r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
ROLLOUT_RE = re.compile(
    rf"^rollout-(\d{{4}}-\d{{2}}-\d{{2}}T\d{{2}}-\d{{2}}-\d{{2}})-({_UUID})(?:_({_UUID}))?\.jsonl(\.zst)?$"
)
PEEK_BYTES = 1 * 1024 * 1024


def _norm(p: str) -> str:
    return p.lower() if _IS_WIN else p


def _safe_realpath_under(candidate: Path, canonical_root: Path) -> Path | None:
    """Resolved path if it is the root or a descendant, else None."""
    try:
        resolved = candidate.resolve(strict=True)
    except (OSError, RuntimeError):
        return None
    a = _norm(str(resolved))
    b = _norm(str(canonical_root))
    if a == b or a.startswith(b + os.sep):
        return resolved
    return None


def codex_root() -> Path:
    configured = (os.getenv("CODEX_HOME") or "").strip()
    base = Path(configured).expanduser() if configured else Path.home() / ".codex"
    return base.resolve(strict=False)


@dataclass(frozen=True)
class RolloutName:
    timestamp: str
    thread_id: str
    rollout_id: str
    compressed: bool


def parse_rollout_name(name: str) -> RolloutName | None:
    m = ROLLOUT_RE.match(name)
    if not m:
        return None
    ts, thread, rollout, zst = m.groups()
    return RolloutName(ts, thread.lower(), (rollout or thread).lower(), zst is not None)


def native_id_for(path: Path) -> str:
    """``<thread>`` or ``<thread>_<rollout>`` for revert rollouts; stem fallback."""
    r = parse_rollout_name(path.name)
    if r is None:
        return path.name.split(".jsonl", 1)[0]
    return r.thread_id if r.rollout_id == r.thread_id else f"{r.thread_id}_{r.rollout_id}"


def is_rollout_path(root: Path, path: Path) -> bool:
    """True for a rollout-named file under sessions/ or archived_sessions/ of ``root``."""
    if parse_rollout_name(path.name) is None:
        return False
    canonical = root.resolve(strict=False)
    try:
        rel = path.resolve(strict=False).relative_to(canonical)
    except ValueError:
        return False
    if not rel.parts or rel.parts[0] not in SESSION_DIRS:
        return False
    return _safe_realpath_under(path, canonical) is not None


@dataclass
class MetaPeek:
    """What line 1 of a rollout tells us, in adapter terms."""

    format: str | None  # "envelope" | "legacy" | None
    thread_id: str
    parent_thread_id: str | None = None
    is_fork: bool = False
    started_at: str = ""
    cwd: str = ""
    cli_version: str | None = None
    originator: str | None = None
    source: Any = None
    model_provider: str | None = None
    git_branch: str | None = None
    agent_role: str | None = None
    history_mode: str | None = None
    skip_before_ordinal: int | None = None


def _parent_from_source(source: Any) -> tuple[str | None, str | None]:
    """(parent_thread_id, agent_role) from a ``source`` value, if it is a spawn."""
    if isinstance(source, dict):
        sub = source.get("subagent")
        if isinstance(sub, dict):
            spawn = sub.get("thread_spawn")
            if isinstance(spawn, dict):
                pid = spawn.get("parent_thread_id")
                role = spawn.get("agent_role") or spawn.get("agent_type")
                return (
                    pid if isinstance(pid, str) else None,
                    role if isinstance(role, str) else None,
                )
    return None, None


def _str(payload: dict[str, Any], key: str) -> str | None:
    v = payload.get(key)
    return v if isinstance(v, str) else None


def meta_from_payload(payload: dict[str, Any], *, fallback_thread: str, fmt: str) -> MetaPeek:
    """Build a MetaPeek from a ``session_meta`` payload (envelope) or a legacy header."""
    thread = _str(payload, "id") or fallback_thread
    src_parent, src_role = _parent_from_source(payload.get("source"))
    role = _str(payload, "agent_role") or _str(payload, "agent_type") or src_role
    git = payload.get("git") if isinstance(payload.get("git"), dict) else {}
    cutoff: int | None = None
    for key in ("subagent_history_start_ordinal", "forked_from_ordinal_exclusive"):
        v = payload.get(key)
        if isinstance(v, int) and not isinstance(v, bool):
            cutoff = v
            break
    if cutoff is None:
        hb = payload.get("history_base")
        if isinstance(hb, dict):
            v = hb.get("end_ordinal_exclusive")
            if isinstance(v, int) and not isinstance(v, bool):
                cutoff = v
    return MetaPeek(
        format=fmt,
        thread_id=str(thread).lower(),
        parent_thread_id=_str(payload, "parent_thread_id") or src_parent,
        is_fork=isinstance(payload.get("forked_from_id"), str),
        started_at=_str(payload, "timestamp") or "",
        cwd=_str(payload, "cwd") or "",
        cli_version=_str(payload, "cli_version"),
        originator=_str(payload, "originator"),
        source=payload.get("source"),
        model_provider=_str(payload, "model_provider"),
        git_branch=git.get("branch") if isinstance(git.get("branch"), str) else None,
        agent_role=role,
        history_mode=_str(payload, "history_mode"),
        skip_before_ordinal=cutoff,
    )


def classify_first_line(obj: Any) -> str | None:
    """'envelope' | 'legacy' | None from the parsed first JSON line of a rollout."""
    if not isinstance(obj, dict):
        return None
    if isinstance(obj.get("type"), str):
        return "envelope"
    if isinstance(obj.get("id"), str) and isinstance(obj.get("timestamp"), str):
        return "legacy"
    return None


def peek_meta(path: Path) -> MetaPeek | None:
    """Read line 1 of a rollout and classify it. None if unreadable/undecodable."""
    from .lines import open_rollout  # lines.py is the single place that knows about .zst

    fallback = native_id_for(path).split("_", 1)[0]
    try:
        with open_rollout(path) as fh:
            if fh is None:
                return None
            first = fh.readline(PEEK_BYTES)
    except OSError:
        return None
    try:
        obj = json.loads(first.decode("utf-8", errors="replace"))
    except (json.JSONDecodeError, ValueError):
        return None
    fmt = classify_first_line(obj)
    if fmt == "envelope":
        if obj.get("type") != "session_meta" or not isinstance(obj.get("payload"), dict):
            return MetaPeek(format="envelope", thread_id=fallback)
        return meta_from_payload(obj["payload"], fallback_thread=fallback, fmt="envelope")
    if fmt == "legacy":
        return meta_from_payload(obj, fallback_thread=fallback, fmt="legacy")
    return None


class ThreadIndex:
    """thread_id -> file, parent -> children, with a cheap first-line peek cache."""

    def __init__(self, root: Path) -> None:
        self._root = root
        self._meta: dict[Path, MetaPeek | None] = {}
        self._sig: dict[Path, tuple[int, int]] = {}
        self._children: dict[str, list[Path]] = {}

    def _canonical_root(self) -> Path | None:
        try:
            return self._root.resolve(strict=True)
        except (OSError, RuntimeError):
            return None

    def _peek(self, path: Path) -> MetaPeek | None:
        try:
            st = path.stat()
        except OSError:
            self._meta.pop(path, None)
            self._sig.pop(path, None)
            return None
        sig = (st.st_size, st.st_mtime_ns)
        if path in self._meta and self._sig.get(path) == sig:
            return self._meta[path]
        m = peek_meta(path)
        self._meta[path] = m
        self._sig[path] = sig
        if m is not None and m.parent_thread_id:
            kids = self._children.setdefault(m.parent_thread_id, [])
            if path not in kids:
                kids.append(path)
                kids.sort()
        return m

    def _walk(self) -> list[Path]:
        canonical = self._canonical_root()
        if canonical is None:
            return []
        out: list[Path] = []
        for sub in SESSION_DIRS:
            d = self._root / sub
            if not d.is_dir():
                continue
            for f in d.rglob("rollout-*.jsonl*"):
                if not f.is_file() or parse_rollout_name(f.name) is None:
                    continue
                if _safe_realpath_under(f, canonical) is None:
                    logger.warning("skipping rollout that escapes the codex root: %s", f)
                    continue
                out.append(f)
        return sorted(out)

    def refresh(self) -> list[Path]:
        """Re-scan the tree; return top-level (non-child) rollouts."""
        self._children.clear()
        files = self._walk()
        for f in files:
            self._peek(f)
        return [f for f in files if not self.is_child(f)]

    def meta_for(self, path: Path) -> MetaPeek | None:
        return self._peek(path)

    def is_child(self, path: Path) -> bool:
        m = self._peek(path)
        return bool(m and m.parent_thread_id)

    def children_of(self, path: Path) -> list[Path]:
        m = self._peek(path)
        if m is None:
            return []
        canonical = self._canonical_root()
        if canonical is None:
            return []
        return sorted(
            k for k in self._children.get(m.thread_id, [])
            if _safe_realpath_under(k, canonical) is not None
        )
