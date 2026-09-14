"""One RawTurnEvent per model response (``token_count``), or per assistant
message for legacy raw files that carry no usage at all.

Unverified against a real rollout (no Codex data was available when this was
written): the assumption that ``token_count`` fires once per model response.
ccusage makes the same assumption; ``tests/adapters/codex/test_real_root.py``
is the gate for the first real dataset.
"""
from __future__ import annotations

from datetime import datetime, timezone

from ...schema.types import RawTurnEvent
from .lines import Line
from .model import canonicalize_codex_model
from .state import USAGE_KEYS, TickState, usage_dict

BURST_WINDOW_MS = 1000
_CALL_KINDS = ("function_call", "custom_tool_call", "local_shell_call")


def _ts_ms(iso: str | None) -> int | None:
    if not iso:
        return None
    try:
        d = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        return int(d.timestamp() * 1000)
    except (ValueError, TypeError):
        return None


def _in_replay_burst(line: Line, state: TickState) -> bool:
    """Legacy child/fork rollouts start with a copy of the parent's history whose
    timestamps were rewritten to spawn time. Usage events inside that first
    second are replayed parent usage, not this thread's. Paginated files
    carry an explicit ordinal cutoff instead, which takes precedence."""
    if state.meta.skip_before_ordinal is not None:
        return False
    if not (state.meta.parent_thread_id or state.meta.is_fork):
        return False
    start = _ts_ms(state.meta.started_at)
    now = _ts_ms(line.timestamp)
    if start is None or now is None:
        return False
    return now - start <= BURST_WINDOW_MS


def _usage_for(line: Line, state: TickState) -> dict[str, int] | None:
    info = line.payload.get("info")
    if not isinstance(info, dict):
        return None
    total = usage_dict(info.get("total_token_usage"))
    last = usage_dict(info.get("last_token_usage"))
    if total is not None and state.prev_total is not None and total == state.prev_total:
        return None  # rate-limit-only refresh: nothing new happened
    if last is not None:
        u = last
    elif total is not None and state.prev_total is not None:
        u = {k: max(0, total[k] - state.prev_total[k]) for k in total}
    elif total is not None:
        u = total
    else:
        return None
    if total is not None:
        state.prev_total = total
    if not any(u.values()):
        return None  # e.g. the context-window-exceeded rewrite (only total_tokens set)
    return u


def _turn(native_id: str, seq: int, ts: str, u: dict[str, int], state: TickState, tool_calls: int) -> RawTurnEvent:
    raw = state.model_raw or "unknown"
    cached = min(u["input_tokens"], u["cached_input_tokens"])
    cw = min(u["input_tokens"] - cached, u["cache_write_input_tokens"])
    return RawTurnEvent(
        native_turn_id=native_id,
        sequence=seq,
        timestamp=ts,
        model=canonicalize_codex_model(raw),
        model_raw=raw,
        fresh_input_tokens=max(0, u["input_tokens"] - cached - cw),
        output_tokens=u["output_tokens"],
        cache_read_tokens=cached,
        cache_write_tokens=cw,
        cache_write_5m_tokens=None,
        cache_write_1h_tokens=None,
        tool_calls_count=tool_calls,
        metadata={
            "effort": state.effort,
            "service_tier": state.service_tier,
            "turn_id": state.turn_id,
            "reasoning_output_tokens": u["reasoning_output_tokens"],
        },
    )


def extract_turns(lines: list[Line], state: TickState) -> tuple[list[RawTurnEvent], list[int]]:
    """Return (turns, boundaries). ``boundaries[i]`` is the byte offset of the
    line that closed turn ``i``; tool calls before it (and after
    ``boundaries[i-1]``) belong to turn ``i``. Mutates ``state``."""
    turns: list[RawTurnEvent] = []
    bounds: list[int] = []
    pending_calls = 0
    seq = 0
    cutoff = state.meta.skip_before_ordinal
    zero = {k: 0 for k in USAGE_KEYS}

    for line in lines:
        if cutoff is not None and line.ordinal is not None and line.ordinal < cutoff:
            continue
        state.apply_context(line)
        if line.kind == "response_item" and line.payload.get("type") in _CALL_KINDS:
            pending_calls += 1
        if state.fmt == "legacy":
            if (
                line.kind == "response_item"
                and line.payload.get("type") == "message"
                and line.payload.get("role") == "assistant"
            ):
                ts = state.meta.started_at or "1970-01-01T00:00:00.000Z"
                turns.append(_turn(f"msg@{line.offset}", seq, ts, zero, state, pending_calls))
                bounds.append(line.offset)
                pending_calls = 0
                seq += 1
            continue
        if line.kind != "event_msg" or line.payload.get("type") != "token_count":
            continue
        if _in_replay_burst(line, state):
            state.burst_seen += 1
            info = line.payload.get("info")
            if isinstance(info, dict):
                state.prev_total = usage_dict(info.get("total_token_usage")) or state.prev_total
            continue
        u = _usage_for(line, state)
        if u is None:
            continue
        turns.append(_turn(f"tc@{line.offset}", seq, line.timestamp or state.meta.started_at, u, state, pending_calls))
        bounds.append(line.offset)
        pending_calls = 0
        seq += 1
    return turns, bounds
