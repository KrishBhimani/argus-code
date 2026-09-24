"""Adapter contract.

Adapters know how to find and parse one coding-agent's session logs. The
pipeline, watcher, and server are agent-agnostic — they only see this
protocol. Anything agent-specific (e.g., Claude Code's ``history.jsonl``
sidecar, sub-agent rollup) is expressed through the optional extension
points below; defaults are no-ops so simple adapters stay simple.
"""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from pydantic import BaseModel

from ..schema.types import RawSessionHeader, RawTurnEvent

if TYPE_CHECKING:
    from ..store.repository import Repository


class ParseError(BaseModel):
    """One JSONL line (or whole-file failure) that couldn't be parsed."""

    file: str
    byte_offset: int
    reason: str
    raw_line_truncated: str


class RawToolCall(BaseModel):
    """Adapter-local tool_call shape. Pipeline stamps session_id + composite id."""

    native_turn_id: str
    turn_index: int
    block_index: int
    tool_name: str
    tool_use_id: str
    is_error: int  # 0 | 1
    input_size: int
    subagent_type: str | None
    timestamp: str


class RawSegment(BaseModel):
    """Adapter-local transcript segment. uid_suffix = '{line_uuid}:{block_index}'."""

    uid_suffix: str
    timestamp: str
    role: str  # 'user' | 'assistant' | 'thinking' | 'tool_result'
    text: str
    tool_use_id: str | None = None  # set on role='tool_result' only


class AdapterIngestResult(BaseModel):
    """What an adapter returns from ingesting one file."""

    model_config = {"arbitrary_types_allowed": True}

    header: RawSessionHeader
    turns: list[RawTurnEvent]
    parse_errors: list[ParseError] = []
    # Optional — adapters that don't populate these leave them empty. The
    # pipeline treats empty as "no data", not "delete existing rows".
    tool_calls: list[RawToolCall] = []
    segments: list[RawSegment] = []
    # tool_use_ids whose result in THIS read reports an error. The call itself
    # may have been stored by an earlier tick, so the collector applies these
    # by id rather than trusting ``RawToolCall.is_error`` alone.
    tool_error_ids: list[str] = []


@runtime_checkable
class Adapter(Protocol):
    """The pluggable contract every agent integration implements."""

    agent: str  # e.g., "claude_code", "codex", "openclaw", "hermes"

    def root_path(self) -> Path: ...

    def is_present(self) -> bool:
        """True if this agent's data exists on the current machine."""
        ...

    def discover_session_files(self) -> list[Path]: ...

    def ingest_file(
        self, path: Path, from_offset: int = 0
    ) -> tuple[AdapterIngestResult, int]:
        """Read from ``from_offset``; return (result, new_offset)."""
        ...

    # ─── Optional extension points (default no-op) ─────────────────────

    def extra_watch_paths(self) -> list[Path]:
        """Side-channel files this adapter wants tailed (e.g., history.jsonl)."""
        return []

    def ingest_extra(self, path: Path, repo: "Repository") -> None:
        """Handle an event on one of ``extra_watch_paths()``."""
        return None

    def sub_session_files_for(self, session_file: Path) -> list[Path]:
        """Files that roll up into ``session_file`` (e.g., sub-agent JSONLs)."""
        return []

    def should_skip(self, path: Path) -> bool:
        """Watcher predicate — true to silently ignore events on this path."""
        return False

    def normalize_model_name(self, raw: str) -> str:
        """Map raw model identifier to the form the pricing table keys on."""
        return raw
