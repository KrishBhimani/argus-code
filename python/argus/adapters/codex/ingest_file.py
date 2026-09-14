"""Parse one Codex rollout from a byte offset; return (result, new_offset).

Holdback rule: a model response's ``function_call`` lines land *before* its
``token_count``. So a tick consumes only up to the last ``token_count`` line
and keeps the tail for the next tick -- every tool call and segment then
belongs to a turn that exists. A tick with no ``token_count`` consumes
nothing (except a lone ``session_meta`` at offset 0, so a fresh file records
its offset). Legacy raw files and immutable ``.zst`` files consume everything.
"""
from __future__ import annotations

from pathlib import Path

from ...schema.types import RawSessionHeader
from ...store.repository import normalize_project_path
from ..base import AdapterIngestResult
from .discover import MetaPeek, native_id_for
from .extract_tool_calls import extract_tool_calls
from .extract_transcript import extract_transcript_segments
from .extract_turns import extract_turns
from .lines import Line, read_lines
from .state import TickState, rebuild_state

AGENT = "codex"
_EPOCH = "1970-01-01T00:00:00.000Z"


def empty_result(path: Path) -> AdapterIngestResult:
    return AdapterIngestResult(
        header=RawSessionHeader(
            native_session_id=native_id_for(path),
            agent=AGENT,
            agent_version=None,
            project_path="",
            started_at="",
            ended_at=None,
            agent_reported_cost_usd=None,
            metadata={},
        ),
        turns=[],
        tool_calls=[],
        segments=[],
        parse_errors=[],
    )


def _cut(lines: list[Line], from_offset: int, whole: bool, fmt: str) -> tuple[list[Line], int | None]:
    """Apply the holdback rule.

    Returns ``(consumable lines, offset to persist)`` where a ``None`` offset
    means "use the reader's new_offset" (everything was consumed).
    """
    if whole or fmt == "legacy":
        return lines, None
    last_tc = -1
    for i, line in enumerate(lines):
        if line.kind == "event_msg" and line.payload.get("type") == "token_count":
            last_tc = i
    if last_tc >= 0:
        if last_tc == len(lines) - 1:
            return lines, None
        return lines[: last_tc + 1], lines[last_tc + 1].offset
    if lines and lines[0].kind == "session_meta" and lines[0].offset == 0:
        return lines[:1], lines[1].offset if len(lines) > 1 else None
    return [], from_offset


def _header(state: TickState, path: Path) -> RawSessionHeader:
    m = state.meta
    return RawSessionHeader(
        native_session_id=native_id_for(path),
        agent=AGENT,
        agent_version=m.cli_version,
        project_path=normalize_project_path(m.cwd or state.cwd or ""),
        started_at=m.started_at or _EPOCH,
        ended_at=state.last_timestamp or m.started_at or None,
        agent_reported_cost_usd=None,
        metadata={
            "source": m.source,
            "originator": m.originator,
            "model_provider": m.model_provider,
            "git_branch": m.git_branch,
            "parent_thread_id": m.parent_thread_id,
            "agent_role": m.agent_role,
            "history_mode": m.history_mode,
            "codex_model": state.model_raw,
        },
    )


def ingest_codex_file(
    path: Path,
    from_offset: int,
    meta: MetaPeek | None,
    state_cache: dict[Path, TickState],
) -> tuple[AdapterIngestResult, int]:
    """Read new bytes after ``from_offset``; return (result, new_offset).

    ``meta`` is the first-line peek from discovery; ``state_cache`` is the
    adapter's per-path cross-tick state (rebuilt from disk on a miss).
    """
    if meta is None or meta.format is None:
        # Undecodable head (e.g. .zst without a decoder): let read_lines record why.
        r = read_lines(path, from_offset, "envelope")
        res = empty_result(path)
        res.parse_errors = r.parse_errors
        return res, r.new_offset

    fmt = meta.format
    state = state_cache.get(path)
    if state is None or state.offset != from_offset or state.fmt != fmt:
        state = rebuild_state(path, fmt, meta, from_offset)

    r = read_lines(path, from_offset, fmt)
    if not r.lines and not r.parse_errors:
        state.offset = r.new_offset
        state_cache[path] = state
        return empty_result(path), r.new_offset

    lines, cut_offset = _cut(r.lines, from_offset, r.whole_file, fmt)
    new_offset = r.new_offset if cut_offset is None else cut_offset
    # Errors past the cut are re-read (and re-reported) next tick; keep only consumed ones.
    errors = [e for e in r.parse_errors if e.byte_offset < new_offset]

    if not lines:
        res = empty_result(path)
        res.parse_errors = errors
        return res, new_offset

    turns, bounds = extract_turns(lines, state)
    tool_calls = extract_tool_calls(lines, turns, bounds, meta.skip_before_ordinal)
    segments = extract_transcript_segments(lines, meta.started_at or _EPOCH, meta.skip_before_ordinal)

    state.offset = new_offset
    state_cache[path] = state
    return (
        AdapterIngestResult(
            header=_header(state, path),
            turns=turns,
            tool_calls=tool_calls,
            segments=segments,
            parse_errors=errors,
        ),
        new_offset,
    )
