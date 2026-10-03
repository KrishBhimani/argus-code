"""Parse one Codex rollout from a byte offset into an AdapterIngestResult.

The read starts at ``window_for`` (the open task's start when a task is still
running at ``from_offset``), so a task's usage records and tool items are
always interpreted together and re-emitted until the task ends. Writes are
keyed by Codex ids, so re-emitting is an idempotent overwrite.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from ...schema.types import RawSessionHeader, RawTurnEvent
from ..base import AdapterIngestResult, RawSegment, RawToolCall
from .discover import parent_of
from .lines import read_records
from .records import (
    CallEvent,
    call_from_record,
    is_task_start,
    legacy_total_of,
    legacy_usage,
    segments_from_record,
    usage_record,
)
from .state import ContextCache, FileState, window_for

AGENT = "codex"


def empty_result(path: Path, native_id: str | None = None) -> AdapterIngestResult:
    return AdapterIngestResult(
        header=RawSessionHeader(
            native_session_id=native_id or path.stem, agent=AGENT, agent_version=None,
            project_path="", started_at="", ended_at=None, agent_reported_cost_usd=None, metadata={},
        ),
        turns=[],
    )


def _metadata(meta: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k in ("originator", "thread_source", "model_provider", "agent_nickname", "agent_role", "agent_path"):
        v = meta.get(k)
        if isinstance(v, str) and v:
            out[k] = v
    src = meta.get("source")
    if isinstance(src, str) and src:
        out["source"] = src
    elif isinstance(src, dict):
        out["source"] = "subagent" if "subagent" in src else "other"
        sub = src.get("subagent")
        spawn = sub.get("thread_spawn") if isinstance(sub, dict) else None
        if isinstance(spawn, dict):
            if isinstance(spawn.get("depth"), int):
                out["depth"] = spawn["depth"]
            for k in ("agent_nickname", "agent_role", "agent_path"):
                if k not in out and isinstance(spawn.get(k), str) and spawn[k]:
                    out[k] = spawn[k]
    git = meta.get("git")
    if isinstance(git, dict) and isinstance(git.get("branch"), str) and git["branch"]:
        out["git_branch"] = git["branch"]
    parent = parent_of(meta)
    if parent:
        out["parent_thread_id"] = parent
    return out


def _turn(rec_offset: int, ts: str, native_id: str, tokens: dict[str, int],
          state: FileState, codex_turn_id: str | None) -> RawTurnEvent:
    model, effort = state.model_for(codex_turn_id)
    meta: dict[str, Any] = {"reasoning_output_tokens": tokens["reasoning_output_tokens"]}
    if codex_turn_id:
        meta["codex_turn_id"] = codex_turn_id
    if effort:
        meta["effort"] = effort
    return RawTurnEvent(
        native_turn_id=native_id, sequence=rec_offset, timestamp=ts, model=model, model_raw=model,
        fresh_input_tokens=tokens["fresh_input_tokens"], output_tokens=tokens["output_tokens"],
        cache_read_tokens=tokens["cache_read_tokens"], cache_write_tokens=tokens["cache_write_tokens"],
        metadata=meta,
    )


def _assign(call: CallEvent, pool: list[tuple[int, str]]) -> str | None:
    """Pick the response a call belongs to, within its own task.

    A request (model-side call) is answered by the usage record that follows
    it; an item (finished execution) follows the usage record that asked."""
    before = [nid for off, nid in pool if off < call.offset]
    after = [nid for off, nid in pool if off > call.offset]
    if call.side == "request":
        return after[0] if after else (before[-1] if before else None)
    return before[-1] if before else (after[0] if after else None)


def ingest_codex_file(
    path: Path, from_offset: int, cache: ContextCache
) -> tuple[AdapterIngestResult, int]:
    start, state = window_for(path, from_offset, cache)
    records, errors, end = read_records(path, start)
    if end <= from_offset:
        return empty_result(path), from_offset

    turns: list[RawTurnEvent] = []
    turn_task: dict[str, int | None] = {}
    calls: list[tuple[CallEvent, int | None]] = []  # (call, task offset)
    segments: list[RawSegment] = []
    open_state: FileState | None = None

    for rec in records:
        if state.skips(rec):
            continue
        if is_task_start(rec):
            open_state = state.copy()
        task = rec.offset if is_task_start(rec) else state.open_task_offset
        p = rec.payload
        tid = p.get("turn_id") if isinstance(p.get("turn_id"), str) else None
        u = usage_record(rec)
        if u is not None:
            rid, tokens = u
            if any(tokens[k] for k in ("fresh_input_tokens", "cache_read_tokens",
                                       "cache_write_tokens", "output_tokens")):
                turns.append(_turn(rec.offset, rec.timestamp, rid, tokens, state, tid))
                turn_task[rid] = task
        elif not state.saw_usage_record:
            lu = legacy_usage(rec)
            total = legacy_total_of(p) if lu is not None else None
            if lu is not None and total is not None and total > state.legacy_total:
                nid = f"tc@{rec.offset}"
                turns.append(_turn(rec.offset, rec.timestamp, nid, lu, state, None))
                turn_task[nid] = task
        call = call_from_record(rec)
        if call is not None:
            calls.append((call, task))
        segments.extend(segments_from_record(
            rec, state.counted_call_ids | ({call.call_id} if call else set())))
        state.apply(rec)

    cache.put(path, end, state, open_state if state.open_task_offset is not None else None)

    pools: dict[int | None, list[tuple[int, str]]] = {}
    for t in turns:
        pools.setdefault(turn_task[t.native_turn_id], []).append((t.sequence, t.native_turn_id))
    seq_of = {t.native_turn_id: t.sequence for t in turns}
    counts: dict[str, int] = {}
    raw_calls: list[RawToolCall] = []
    for c, task in calls:
        nid = _assign(c, pools.get(task, [])) if task is not None or None in pools else None
        if nid:
            counts[nid] = counts.get(nid, 0) + 1
        raw_calls.append(RawToolCall(
            native_turn_id=nid or "", turn_index=seq_of[nid] if nid else c.offset, block_index=0,
            tool_name=c.tool_name, tool_use_id=c.call_id, is_error=c.is_error,
            input_size=c.input_size, subagent_type=c.subagent_type, timestamp=c.timestamp,
        ))
    turns = [t.model_copy(update={"tool_calls_count": counts.get(t.native_turn_id, 0)}) for t in turns]

    meta = state.meta or {}
    native = meta.get("id") if isinstance(meta.get("id"), str) and meta.get("id") else path.stem
    version = meta.get("cli_version")
    cwd = meta.get("cwd")
    stamps = sorted(t.timestamp for t in turns if t.timestamp)
    header = RawSessionHeader(
        native_session_id=native, agent=AGENT,
        agent_version=version if isinstance(version, str) else None,
        project_path=cwd if isinstance(cwd, str) else "",
        started_at=stamps[0] if stamps else "", ended_at=stamps[-1] if stamps else None,
        agent_reported_cost_usd=None, metadata=_metadata(meta),
    )
    return AdapterIngestResult(
        header=header,
        turns=turns,
        tool_calls=raw_calls,
        tool_error_ids=[c.call_id for c, _ in calls if c.is_error],
        segments=segments,
        parse_errors=[e for e in errors if e.byte_offset >= from_offset],
    ), end
