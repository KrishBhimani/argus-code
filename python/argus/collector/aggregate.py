"""Build Session and Turn rows from adapter-supplied raw events."""
from __future__ import annotations

from datetime import datetime, timezone

from ..adapters.base import AdapterIngestResult
from ..pricing.compute import compute_turn_cost
from ..pricing.types import PricingTable
from ..schema.types import RawSessionHeader, RawTurnEvent, Session, Turn


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_iso(ts: str) -> datetime | None:
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _earliest(stamps: list[str | None]) -> str | None:
    """Earliest parseable ISO timestamp, returned in its original spelling."""
    parsed = [(d, s) for s in stamps if s and (d := _parse_iso(s)) is not None]
    return min(parsed, key=lambda p: p[0])[1] if parsed else None


def _latest(stamps: list[str | None]) -> str | None:
    parsed = [(d, s) for s in stamps if s and (d := _parse_iso(s)) is not None]
    return max(parsed, key=lambda p: p[0])[1] if parsed else None


def build_turn(raw: RawTurnEvent, session_id: str, table: PricingTable) -> Turn:
    return Turn(
        id=f"{session_id}:{raw.native_turn_id}",
        session_id=session_id,
        sequence=raw.sequence,
        timestamp=raw.timestamp,
        model=raw.model,
        model_raw=raw.model_raw,
        fresh_input_tokens=raw.fresh_input_tokens,
        output_tokens=raw.output_tokens,
        cache_read_tokens=raw.cache_read_tokens,
        cache_write_tokens=raw.cache_write_tokens,
        cache_write_5m_tokens=raw.cache_write_5m_tokens,
        cache_write_1h_tokens=raw.cache_write_1h_tokens,
        tool_calls_count=raw.tool_calls_count,
        cost_usd=compute_turn_cost(raw, table),
        metadata=raw.metadata,
    )


def build_session(
    header: RawSessionHeader,
    session_id: str,
    all_turns: list[Turn],
    pricing_version: str,
) -> Session:
    computed_at = _iso_now()
    fresh = sum(t.fresh_input_tokens for t in all_turns)
    out = sum(t.output_tokens for t in all_turns)
    cr = sum(t.cache_read_tokens for t in all_turns)
    cw = sum(t.cache_write_tokens for t in all_turns)
    cost = sum(t.cost_usd for t in all_turns)

    # primary_model = the model with the most (input + output) tokens.
    model_tokens: dict[str, int] = {}
    for t in all_turns:
        model_tokens[t.model] = (
            model_tokens.get(t.model, 0) + t.fresh_input_tokens + t.output_tokens
        )
    primary = (
        sorted(model_tokens.items(), key=lambda kv: kv[1], reverse=True)[0][0]
        if model_tokens
        else "unknown"
    )

    # The header only describes the lines of the tick that produced it; the
    # stored turns cover the whole file. Widen to both so a late tick can't
    # move the start forward (which is how 29-day sessions ended up "3 s").
    turn_ts = [t.timestamp for t in all_turns if t.timestamp]
    started_at = _earliest([header.started_at, *turn_ts]) or computed_at
    ended_at = _latest([header.ended_at, *turn_ts])

    duration: int | None = None
    if ended_at:
        try:
            s = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
            e = datetime.fromisoformat(ended_at.replace("Z", "+00:00"))
            duration = max(0, int((e - s).total_seconds()))
        except (ValueError, TypeError):
            duration = None

    return Session(
        id=session_id,
        agent=header.agent,
        agent_version=header.agent_version,
        project_path=header.project_path,
        started_at=started_at,
        ended_at=ended_at,
        duration_sec=duration,
        total_fresh_input_tokens=fresh,
        total_output_tokens=out,
        total_cache_read_tokens=cr,
        total_cache_write_tokens=cw,
        total_cost_usd=cost,
        primary_model=primary,
        turn_count=len(all_turns),
        pricing_table_version=pricing_version,
        computed_at=computed_at,
        agent_reported_cost_usd=header.agent_reported_cost_usd,
        metadata=header.metadata,
    )


def aggregate_adapter_result(
    r: AdapterIngestResult, table: PricingTable
) -> tuple[Session, list[Turn]]:
    """Backward-compat helper: build a (session, turns) pair from a fresh result."""
    session_id = f"{r.header.agent}:{r.header.native_session_id}"
    turns = [build_turn(t, session_id, table) for t in r.turns]
    session = build_session(r.header, session_id, turns, table.version)
    return session, turns
