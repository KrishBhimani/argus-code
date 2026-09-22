"""Build per-turn records from a session's assistant lines."""
from __future__ import annotations

from ...schema.types import RawTurnEvent
from .model import canonicalize_claude_model
from .schemas import AssistantLine


def copied_from(line: AssistantLine) -> str | None:
    """Session a forked transcript copied this line from, if it claims one.

    A fork (``sessionKind: "bg"``) starts with verbatim copies of its parent's
    lines: same ``uuid`` and ``message.id``, ``sessionId`` rewritten to the
    fork, and the parent's id kept in a ``session_id`` field. That field alone
    is only a *claim* — some real, non-copied lines differ on it too — so the
    collector treats a turn as a copy only if the claimed origin session
    actually stores the same message.
    """
    origin = (line.model_extra or {}).get("session_id")
    if isinstance(origin, str) and origin and origin != line.sessionId:
        return origin
    return None


def _metadata(first: AssistantLine) -> dict:
    usage = first.message.usage
    md: dict = {
        "service_tier": usage.service_tier,
        "agentId": first.agentId,
        "attribution_agent": first.attribution_agent,
        "isSidechain": first.isSidechain if first.isSidechain is not None else False,
    }
    origin = copied_from(first)
    if origin is not None:
        md["origin_session_id"] = origin
    return md


def extract_turns(
    lines: list[AssistantLine], offsets: list[int] | None = None
) -> list[RawTurnEvent]:
    """Group assistant lines by message.id, emit one RawTurnEvent per group.

    A single message can be split across multiple JSONL lines (one per
    content block in some Claude Code versions) — all sharing message.id.
    We preserve first-seen order and walk every line in the group.

    ``offsets[i]`` is the byte offset of ``lines[i]`` in the file. A turn's
    ``sequence`` is the offset of its group's first line: monotonic across the
    whole file, so it doesn't depend on which tick read the line (a per-call
    counter restarted at 0 every tick and scrambled the turn order). Without
    offsets (unit tests) the line's position stands in for it.
    """
    if offsets is None:
        offsets = list(range(len(lines)))
    by_id: dict[str, list[AssistantLine]] = {}
    first_offset: dict[str, int] = {}
    order: list[str] = []
    for line, off in zip(lines, offsets):
        mid = line.message.id
        if mid not in by_id:
            by_id[mid] = []
            first_offset[mid] = off
            order.append(mid)
        by_id[mid].append(line)

    turns: list[RawTurnEvent] = []
    for mid in order:
        group = by_id[mid]
        first = group[0]
        usage = first.message.usage
        # While a reply streams, Claude Code appends one line per content
        # block. Early lines carry the API's `message_start` placeholder usage
        # (a handful of output tokens); only the final line has the real
        # count. Input/cache fields are fixed before generation and identical
        # across the group, so `first` is fine for those. Output is a
        # monotonically growing counter, so take the max rather than trusting
        # line order.
        output_tokens = max(line.message.usage.output_tokens for line in group)
        cache_5m = (
            usage.cache_creation.ephemeral_5m_input_tokens
            if usage.cache_creation is not None
            else None
        )
        cache_1h = (
            usage.cache_creation.ephemeral_1h_input_tokens
            if usage.cache_creation is not None
            else None
        )
        tool_calls = sum(
            sum(1 for b in line.message.content if b.get("type") == "tool_use")
            for line in group
        )

        turns.append(
            RawTurnEvent(
                native_turn_id=mid,
                sequence=first_offset[mid],
                timestamp=first.timestamp,
                model=canonicalize_claude_model(first.message.model),
                model_raw=first.message.model,
                fresh_input_tokens=usage.input_tokens,
                output_tokens=output_tokens,
                cache_read_tokens=usage.cache_read_input_tokens,
                cache_write_tokens=usage.cache_creation_input_tokens,
                cache_write_5m_tokens=cache_5m,
                cache_write_1h_tokens=cache_1h,
                tool_calls_count=tool_calls,
                metadata=_metadata(first),
            )
        )
    return turns
