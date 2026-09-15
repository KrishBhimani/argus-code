"""Per-file state that must survive between ingest ticks.

The Codex adapter reads a rollout incrementally by byte offset, but the model
for a ``token_count`` is announced earlier by ``turn_context``, and usage
fallbacks need the previous cumulative total. This object carries that
context. It is cached per path on the adapter instance and rebuilt from disk
on a miss (process restart, or an offset reset by a backfill).
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .discover import MetaPeek
from .lines import Line, parse_text

USAGE_KEYS = (
    "input_tokens",
    "cached_input_tokens",
    "cache_write_input_tokens",
    "output_tokens",
    "reasoning_output_tokens",
)
# Substrings that mark the only lines a state rebuild needs to decode.
_PREFILTER = (
    b'"session_meta"',
    b'"turn_context"',
    b'"thread_settings_applied"',
    b'"token_count"',
    b'"task_started"',
)


def usage_dict(raw: Any) -> dict[str, int] | None:
    """Coerce a TokenUsage object to ints on the five keys; None if not an object.

    Older files carry ``null`` for ``cached_input_tokens`` /
    ``reasoning_output_tokens``; those read as 0.
    """
    if not isinstance(raw, dict):
        return None
    out: dict[str, int] = {}
    for k in USAGE_KEYS:
        v = raw.get(k, 0)
        out[k] = max(0, int(v)) if isinstance(v, (int, float)) and not isinstance(v, bool) else 0
    return out


@dataclass
class TickState:
    offset: int
    fmt: str
    meta: MetaPeek
    model_raw: str | None = None
    effort: str | None = None
    service_tier: str | None = None
    cwd: str | None = None
    prev_total: dict[str, int] | None = None
    turn_id: str | None = None
    last_timestamp: str | None = None
    burst_seen: int = 0  # token_count lines attributed to a legacy replay burst

    def apply_context(self, line: Line) -> None:
        """Update model/effort/tier/cwd/turn_id/last_timestamp from one line."""
        p = line.payload
        if line.timestamp:
            self.last_timestamp = line.timestamp
        if line.kind == "turn_context":
            if isinstance(p.get("model"), str) and p["model"]:
                self.model_raw = p["model"]
            if isinstance(p.get("effort"), str):
                self.effort = p["effort"]
            if isinstance(p.get("cwd"), str) and p["cwd"]:
                self.cwd = p["cwd"]
            return
        if line.kind == "event_msg":
            t = p.get("type")
            if t == "thread_settings_applied":
                ts = p.get("thread_settings")
                if isinstance(ts, dict):
                    # turn_context is the authoritative per-turn model; settings
                    # only fill the gap before the first turn_context.
                    if isinstance(ts.get("model"), str) and ts["model"] and self.model_raw is None:
                        self.model_raw = ts["model"]
                    if isinstance(ts.get("service_tier"), str):
                        self.service_tier = ts["service_tier"]
            elif t == "task_started" and isinstance(p.get("turn_id"), str):
                self.turn_id = p["turn_id"]


def rebuild_state(path: Path, fmt: str, meta: MetaPeek, upto_offset: int) -> TickState:
    """Scan ``[0, upto_offset)`` for context lines only and return a fresh state.

    A substring prefilter keeps this to one pass of byte checks plus a JSON
    decode of the handful of lines that matter, so a cache miss on a large
    rollout stays cheap.
    """
    state = TickState(offset=upto_offset, fmt=fmt, meta=meta)
    if upto_offset <= 0 or fmt != "envelope" or path.name.endswith(".zst"):
        return state
    with open(path, "rb") as fh:
        raw = fh.read(upto_offset)
    keep = [chunk for chunk in raw.split(b"\n") if any(m in chunk for m in _PREFILTER)]
    if not keep:
        return state
    text = b"\n".join(keep).decode("utf-8", errors="replace") + "\n"
    lines, _ = parse_text(text, 0, fmt, path)
    for line in lines:
        state.apply_context(line)
        if line.kind == "event_msg" and line.payload.get("type") == "token_count":
            info = line.payload.get("info")
            total = usage_dict(info.get("total_token_usage")) if isinstance(info, dict) else None
            if total is not None:
                state.prev_total = total
    return state
