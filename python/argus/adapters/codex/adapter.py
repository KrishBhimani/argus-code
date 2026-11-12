"""The Codex adapter -- façade over discovery and the per-file reader."""
from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import TYPE_CHECKING

from ..base import AdapterIngestResult
from ..registry import register
from .discover import SESSION_DIRS, ThreadIndex, codex_home, contained
from .ingest_file import empty_result, ingest_codex_file
from .state import ContextCache

if TYPE_CHECKING:
    from ...store.repository import Repository

logger = logging.getLogger(__name__)

#: Minimum gap between sessions-tree rescans triggered by the pipeline's
#: per-tick sub-agent lookup. A new child file is found on a later parent tick
#: (or at the next startup's discovery, which always rescans).
SUB_REFRESH_INTERVAL_SEC = 2.0


@register
class CodexAdapter:
    agent = "codex"

    def __init__(self, root: Path | None = None) -> None:
        self._root = (root or codex_home()).resolve(strict=False)
        self._index = ThreadIndex(self._root)
        self._cache = ContextCache()
        self._last_refresh = float("-inf")

    def root_path(self) -> Path:
        return self._root

    def is_present(self) -> bool:
        return any((self._root / d).is_dir() for d in SESSION_DIRS)

    def _refresh(self) -> None:
        self._index.refresh()
        self._last_refresh = time.monotonic()

    def discover_session_files(self) -> list[Path]:
        self._refresh()
        return self._index.top_level_files()

    def ingest_file(self, path: Path, from_offset: int = 0) -> tuple[AdapterIngestResult, int]:
        # Containment at the one choke point every read passes through
        # (discovery, watcher events, the pipeline's sub-agent walk).
        if contained(path, self._root) is None:
            logger.warning("refusing to ingest a path outside the codex session dirs: %s", path)
            return empty_result(path), from_offset
        return ingest_codex_file(path, from_offset, self._cache)

    # The collector calls these hooks directly and Protocol bodies are not
    # inherited, so they must be defined even as no-ops.
    def extra_watch_paths(self) -> list[Path]:
        return []  # history.jsonl is deferred (it needs a prompts migration)

    def ingest_extra(self, path: Path, repo: "Repository") -> None:
        return None

    def sub_session_files_for(self, session_file: Path) -> list[Path]:
        # Called on every parent ingest tick: a full rglob of the user's whole
        # Codex history each time would make live ticks pay for it, so rescans
        # are throttled. Already-indexed children are returned regardless.
        if time.monotonic() - self._last_refresh >= SUB_REFRESH_INTERVAL_SEC:
            self._refresh()
        return self._index.descendant_files(session_file)

    def should_skip(self, path: Path) -> bool:
        if path.suffix != ".jsonl":
            return True
        try:
            rel = path.relative_to(self._root)
        except ValueError:
            return True
        if not rel.parts or rel.parts[0] not in SESSION_DIRS:
            return True
        info = self._index.info(path)
        # Unknown identity (first line mid-write) or a sub-agent child: skip.
        # Children are read through their parent; a partial head retries on
        # the next fs event.
        return info is None or info.parent_thread_id is not None

    def native_session_id(self, path: Path) -> str:
        info = self._index.info(path)
        return info.thread_id if info is not None else path.stem

    def normalize_model_name(self, raw: str) -> str:
        return raw
