"""The Claude Code adapter — façade that wires the package together."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from ..base import AdapterIngestResult
from ..registry import register
from .discover import _safe_realpath_under, discover_session_files, sub_agent_files_for
from .history_jsonl import ingest_history_file
from .ingest_file import _empty_result, ingest_claude_code_file
from .model import canonicalize_claude_model

if TYPE_CHECKING:
    from ...store.repository import Repository

logger = logging.getLogger(__name__)


def _default_root() -> Path:
    return Path.home() / ".claude"


@register
class ClaudeCodeAdapter:
    agent = "claude_code"

    def __init__(self, root: Path | None = None) -> None:
        self._root = (root or _default_root()).resolve(strict=False)

    def root_path(self) -> Path:
        return self._root

    def is_present(self) -> bool:
        return self._root.exists()

    def discover_session_files(self) -> list[Path]:
        return discover_session_files(self._root)

    def ingest_file(
        self, path: Path, from_offset: int = 0
    ) -> tuple[AdapterIngestResult, int]:
        # Containment lives here, at the one choke point every read passes
        # through -- discovery, the watcher's fs events, and the sub-agent
        # walk in the pipeline all land on this method. Checking here rather
        # than at each caller is what stops a future call site from quietly
        # reopening the arbitrary-file-read hole (see discover.py's rules).
        if _safe_realpath_under(path, self._root) is None:
            logger.warning("refusing to ingest a path outside the claude root: %s", path)
            return _empty_result(path), from_offset
        return ingest_claude_code_file(path, from_offset)

    # ─── Extension-point overrides ─────────────────────────────────────

    def extra_watch_paths(self) -> list[Path]:
        history = self._root / "history.jsonl"
        return [history] if history.exists() else []

    def ingest_extra(self, path: Path, repo: "Repository") -> None:
        # Only the history file is interesting for this adapter.
        if path.name == "history.jsonl":
            ingest_history_file(path, repo)

    def sub_session_files_for(self, session_file: Path) -> list[Path]:
        return sub_agent_files_for(session_file, self._root)

    def should_skip(self, path: Path) -> bool:
        # The subagents/ tree is walked as part of the parent's ingest,
        # never as standalone sessions.
        return "subagents" in path.parts

    def normalize_model_name(self, raw: str) -> str:
        return canonicalize_claude_model(raw)

    def native_session_id(self, path: Path) -> str:
        # Claude Code names each transcript <session-uuid>.jsonl.
        return path.stem
