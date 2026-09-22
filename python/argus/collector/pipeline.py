"""Orchestrate one ingest: read new bytes, upsert turns/calls/segments, recompute session."""
from __future__ import annotations

from pathlib import Path

from ..adapters.base import Adapter, AdapterIngestResult, RawSegment, RawToolCall
from ..pricing.types import PricingTable
from ..schema.types import (
    RawSessionHeader,
    RawTurnEvent,
    Session,
    ToolCall,
    TranscriptSegment,
    Turn,
)
from ..store.repository import Repository
from .aggregate import build_session, build_turn
from .rollup_subagents import rollup_subagents


def _header_for_recompute(
    header: RawSessionHeader,
    existing: Session | None,
    has_new_turns: bool,
    authoritative: bool = False,
) -> RawSessionHeader:
    """Header to use when (re)building a session row.

    A tick's ``header`` describes only the lines that tick read. It is the
    whole truth only for a brand-new session or a read that began at byte 0
    (``authoritative``: a first read or a backfill re-read). On any later tick:

    - ``project_path`` and ``started_at`` are per-file facts fixed by the
      file's start, so keep the stored ones (the user may ``cd`` or open a
      worktree mid-session; that must not move the session).
    - ``agent_version`` is "latest seen", so a new tick may update it.
    - With NO new turns (e.g. a zero-byte re-read) the adapter returns a blank
      placeholder header, so everything comes from the stored row.
    """
    if existing is None or (authoritative and has_new_turns):
        return header
    if not has_new_turns:
        return header.model_copy(
            update={
                "project_path": existing.project_path,
                "agent_version": existing.agent_version,
                "started_at": existing.started_at,
                "ended_at": existing.ended_at,
                "agent_reported_cost_usd": existing.agent_reported_cost_usd,
                "metadata": existing.metadata,
            }
        )
    return header.model_copy(
        update={
            "project_path": existing.project_path,
            "started_at": existing.started_at,
            "agent_version": header.agent_version or existing.agent_version,
        }
    )


def _merge_split_turn(raw: RawTurnEvent, stored: Turn, new_calls: int) -> RawTurnEvent:
    """Fold this tick's tail of a streamed message into the stored head.

    One message is written as several lines (one per content block) sharing
    ``message.id``; a tick boundary can fall between them. The stored row saw
    the first lines, this tick sees the rest. Keep the head's position and
    time, the larger output count (a monotonically growing counter), the
    head's input/cache fields (fixed before generation, identical on every
    line), and add this tick's tool_use blocks to the head's — only the ones
    not stored yet (``new_calls``), so re-reading the same tail after a lost
    offset write can't count them twice.
    """
    return raw.model_copy(
        update={
            "sequence": stored.sequence,
            "timestamp": stored.timestamp,
            "model": stored.model,
            "model_raw": stored.model_raw,
            "fresh_input_tokens": stored.fresh_input_tokens,
            "output_tokens": max(stored.output_tokens, raw.output_tokens),
            "cache_read_tokens": stored.cache_read_tokens,
            "cache_write_tokens": stored.cache_write_tokens,
            "cache_write_5m_tokens": stored.cache_write_5m_tokens,
            "cache_write_1h_tokens": stored.cache_write_1h_tokens,
            "tool_calls_count": stored.tool_calls_count + new_calls,
            "metadata": stored.metadata,
        }
    )


def _apply_result(
    repo: Repository,
    session_id: str,
    result: AdapterIngestResult,
    from_offset: int,
    table: PricingTable,
) -> None:
    """Persist one tick's turns, tool calls, error flags and segments."""
    # Final sequence per message after merging, to re-point this tick's calls.
    seq_for: dict[str, int] = {}
    unstored_calls: dict[str, int] = {}
    if from_offset > 0 and result.tool_calls:
        stored_ids = repo.existing_tool_call_ids(
            [f"{session_id}:{r.tool_use_id}" for r in result.tool_calls]
        )
        for r in result.tool_calls:
            if f"{session_id}:{r.tool_use_id}" not in stored_ids:
                unstored_calls[r.native_turn_id] = unstored_calls.get(r.native_turn_id, 0) + 1
    for raw in result.turns:
        # A stored row with the same id and an EARLIER sequence means this
        # tick holds the tail of a message whose head an earlier tick stored.
        # A read from offset 0 is authoritative (it sees every line of every
        # message, and it is how pre-fix rows get rewritten); an equal
        # sequence means the same lines are being re-read — overwrite both.
        if from_offset > 0:
            stored = repo.get_turn(f"{session_id}:{raw.native_turn_id}")
            if stored is not None and stored.sequence < raw.sequence:
                raw = _merge_split_turn(
                    raw, stored, unstored_calls.get(raw.native_turn_id, 0)
                )
        seq_for[raw.native_turn_id] = raw.sequence
        repo.upsert_turn(build_turn(raw, session_id, table))

    if result.tool_calls:
        repo.upsert_tool_calls(
            [
                _to_tool_call(
                    r.model_copy(
                        update={"turn_index": seq_for.get(r.native_turn_id, r.turn_index)}
                    ),
                    session_id,
                )
                for r in result.tool_calls
            ]
        )
    # After the upsert, so a call and its error in the same tick both land.
    repo.mark_tool_calls_errored(session_id, result.tool_error_ids)

    if result.segments and repo.is_search_indexing_enabled():
        repo.upsert_transcript_segments(
            [_to_segment(r, session_id) for r in result.segments]
        )


def _to_tool_call(r: RawToolCall, session_id: str) -> ToolCall:
    return ToolCall(
        id=f"{session_id}:{r.tool_use_id}",
        session_id=session_id,
        turn_index=r.turn_index,
        tool_name=r.tool_name,
        is_error=r.is_error,
        input_size=r.input_size,
        subagent_type=r.subagent_type,
        timestamp=r.timestamp,
    )


def _to_segment(r: RawSegment, session_id: str) -> TranscriptSegment:
    return TranscriptSegment(
        uid=f"{session_id}:{r.uid_suffix}",
        session_id=session_id,
        timestamp=r.timestamp,
        role=r.role,  # type: ignore[arg-type]
        text=r.text,
        tool_use_id=r.tool_use_id,
    )


def ingest_file(
    adapter: Adapter, file_path: Path, repo: Repository, table: PricingTable
) -> None:
    """Read new bytes from ``file_path`` via ``adapter`` and upsert into ``repo``."""
    file_str = str(file_path)
    from_offset = repo.get_file_offset(file_str)
    result, new_offset = adapter.ingest_file(file_path, from_offset)

    for e in result.parse_errors:
        repo.record_parse_error(
            {
                "file": e.file,
                "byte_offset": e.byte_offset,
                "reason": e.reason,
                "raw_line_truncated": e.raw_line_truncated,
            }
        )

    session_id = f"{result.header.agent}:{result.header.native_session_id}"
    existing = repo.get_session(session_id)

    # No new parent turns AND no session yet:
    #   (b) first ingest of a file with only metadata / user lines / hooks
    #   (c) Codex stub (binary launched, no prompt sent)
    # Record the new offset, but MUST NOT create an empty session row that
    # would clutter the dashboard. (A no-new-turns tick on an *existing*
    # session falls through — its sub-agents may still need reconciling.)
    if not result.turns and existing is None:
        if new_offset > from_offset:
            repo.set_file_offset(file_str, new_offset)
        return

    # Ensure a session row exists (FK target for turns + tool_calls).
    if existing is None:
        repo.upsert_session(build_session(result.header, session_id, [], table.version))

    _apply_result(repo, session_id, result, from_offset, table)

    # Sub-agents: each sub-agent JSONL becomes its own session under
    # <sessionId>/<filename>. We reconcile them even on a tick with no new
    # parent turns (so sessions ingested before sub-agent support backfill
    # without a wipe), but only re-read/rebuild a sub-file that has actually
    # grown since its recorded offset — a cheap stat keeps steady-state
    # restarts from re-reading and rewriting unchanged sub-sessions.
    sub_sessions: list[Session] = []
    subs_changed = False
    if not adapter.should_skip(file_path):
        sub_files = adapter.sub_session_files_for(file_path)
        grown: set[Path] = set()
        for sub in sub_files:
            sub_from_offset = repo.get_file_offset(str(sub))
            try:
                if sub.stat().st_size > sub_from_offset:
                    grown.add(sub)
            except OSError:
                continue

        for sub in grown:
            subs_changed = True
            sub_session_id = f"{session_id}/{sub.stem}"
            sub_from_offset = repo.get_file_offset(str(sub))
            sub_result, sub_new_offset = adapter.ingest_file(sub, sub_from_offset)

            for e in sub_result.parse_errors:
                repo.record_parse_error(
                    {
                        "file": e.file,
                        "byte_offset": e.byte_offset,
                        "reason": e.reason,
                        "raw_line_truncated": e.raw_line_truncated,
                    }
                )

            existing_sub = repo.get_session(sub_session_id)
            if existing_sub is None and sub_result.turns:
                repo.upsert_session(
                    build_session(sub_result.header, sub_session_id, [], table.version)
                )
            _apply_result(repo, sub_session_id, sub_result, sub_from_offset, table)

            existing_sub = repo.get_session(sub_session_id)
            if existing_sub:
                all_sub_turns = repo.get_turns_for_session(sub_session_id)
                recomputed = build_session(
                    _header_for_recompute(
                        sub_result.header,
                        existing_sub,
                        bool(sub_result.turns),
                        authoritative=sub_from_offset == 0,
                    ),
                    sub_session_id,
                    all_sub_turns,
                    table.version,
                )
                repo.upsert_session(recomputed)
                sub_sessions.append(recomputed)
            repo.set_file_offset(str(sub), sub_new_offset)

        # If we're going to recompute the parent, the rollup needs every
        # sub-session, not just the ones that changed this tick — pull the
        # unchanged ones from their stored rows (no re-read).
        if result.turns or subs_changed or existing is None:
            for sub in sub_files:
                if sub in grown:
                    continue
                stored_sub = repo.get_session(f"{session_id}/{sub.stem}")
                if stored_sub is not None:
                    sub_sessions.append(stored_sub)

    # Recompute the parent (and re-apply the rollup) only when something
    # actually changed: new parent turns, a grown sub-file, or a brand-new
    # session. Otherwise the stored row is already correct — skip the work.
    if result.turns or subs_changed or existing is None:
        header = _header_for_recompute(
            result.header, existing, bool(result.turns), authoritative=from_offset == 0
        )
        all_turns = repo.get_turns_for_session(session_id)
        session = build_session(header, session_id, all_turns, table.version)
        if sub_sessions:
            session = rollup_subagents(session, sub_sessions)
        repo.upsert_session(session)

    if new_offset > from_offset:
        repo.set_file_offset(file_str, new_offset)
