"""The Codex CLI adapter -- façade wiring discovery, ingest, and history."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from ..base import AdapterIngestResult
from ..registry import register
from .discover import ThreadIndex, _safe_realpath_under, codex_root, is_rollout_path, native_id_for
from .history_jsonl import ingest_history_file
from .ingest_file import empty_result, ingest_codex_file
from .model import canonicalize_codex_model
from .state import TickState

if TYPE_CHECKING:
    from ...store.repository import Repository

logger = logging.getLogger(__name__)


@register
class CodexAdapter:
    agent = "codex"

    def __init__(self, root: Path | None = None) -> None:
        self._root = (root or codex_root()).resolve(strict=False)
        self._index = ThreadIndex(self._root)
        # Cross-tick parse state per rollout (model context, cumulative usage).
        # Rebuilt from the file head on a miss, so losing it is only a cost.
        self._state: dict[Path, TickState] = {}

    def root_path(self) -> Path:
        return self._root

    def is_present(self) -> bool:
        # ~/.codex exists for anyone who ever logged in; sessions/ means data.
        return (self._root / "sessions").is_dir()

    def discover_session_files(self) -> list[Path]:
        return self._index.refresh()

    def native_session_id(self, path: Path) -> str:
        return native_id_for(path)

    def ingest_file(self, path: Path, from_offset: int = 0) -> tuple[AdapterIngestResult, int]:
        # Containment at the one choke point every read passes through (see
        # the Claude adapter for why this must not move to the callers).
        resolved = _safe_realpath_under(path, self._root)
        if resolved is None:
            logger.warning("refusing to ingest a path outside the codex root: %s", path)
            return empty_result(path), from_offset
        return ingest_codex_file(resolved, from_offset, self._index.meta_for(resolved), self._state)

    # ─── Extension-point overrides ─────────────────────────────────────

    def extra_watch_paths(self) -> list[Path]:
        history = self._root / "history.jsonl"
        return [history] if history.exists() else []

    def ingest_extra(self, path: Path, repo: "Repository") -> None:
        if path.name == "history.jsonl":
            ingest_history_file(path, repo)

    def sub_session_files_for(self, session_file: Path) -> list[Path]:
        return self._index.children_of(session_file)

    def should_skip(self, path: Path) -> bool:
        # Anything that is not a rollout under sessions/ or archived_sessions/
        # (history.jsonl, logs, sqlite files) and every child thread: children
        # are walked through their parent, never as standalone sessions.
        if not is_rollout_path(self._root, path):
            return True
        return self._index.is_child(path)

    def normalize_model_name(self, raw: str) -> str:
        return canonicalize_codex_model(raw)
