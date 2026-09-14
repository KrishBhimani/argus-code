"""Searchable transcript segments from a Codex rollout.

User prompts come from ``user_message`` events (legacy history mode) or
``item_completed`` user items (paginated mode) -- never from raw user
``response_item`` messages, which carry injected environment context and
AGENTS.md text the person did not type.
"""
from __future__ import annotations

from typing import Any

from ..base import RawSegment
from .lines import Line

# Same cap as the Claude adapter; kept local because adapters never import
# each other.
SEGMENT_CAP_BYTES = 16 * 1024


def _cap_text(s: str) -> str:
    encoded = s.encode("utf-8")
    if len(encoded) <= SEGMENT_CAP_BYTES:
        return s
    return encoded[:SEGMENT_CAP_BYTES].decode("utf-8", errors="ignore") + "…"


def _texts(content: Any, kinds: tuple[str, ...]) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts = [
        b.get("text")
        for b in content
        if isinstance(b, dict) and b.get("type") in kinds and isinstance(b.get("text"), str)
    ]
    return "\n".join(p for p in parts if p)


def _item_completed_user_text(item: Any) -> str:
    """Text of a paginated ``item_completed`` user item; '' for other items.

    The wire casing of ``item.type`` is unverified (serde default would be
    ``UserMessage``); both PascalCase and snake_case are accepted.
    """
    if not isinstance(item, dict):
        return ""
    if str(item.get("type", "")).lower().replace("_", "") != "usermessage":
        return ""
    if isinstance(item.get("message"), str):
        return item["message"]
    return _texts(item.get("content"), ("text", "input_text"))


def extract_transcript_segments(
    lines: list[Line], fallback_ts: str, skip_before_ordinal: int | None
) -> list[RawSegment]:
    out: list[RawSegment] = []

    def add(line: Line, block: int, role: str, text: str, tool_use_id: str | None = None) -> None:
        if not text.strip():
            return
        out.append(
            RawSegment(
                uid_suffix=f"{line.offset}:{block}",
                timestamp=line.timestamp or fallback_ts,
                role=role,
                text=_cap_text(text),
                tool_use_id=tool_use_id,
            )
        )

    for line in lines:
        if skip_before_ordinal is not None and line.ordinal is not None and line.ordinal < skip_before_ordinal:
            continue
        p = line.payload
        t = p.get("type")
        if line.kind == "event_msg":
            if t == "user_message" and isinstance(p.get("message"), str):
                add(line, 0, "user", p["message"])
            elif t == "item_completed":
                add(line, 0, "user", _item_completed_user_text(p.get("item")))
            continue
        if line.kind != "response_item":
            continue
        if t == "message" and p.get("role") == "assistant":
            add(line, 0, "assistant", _texts(p.get("content"), ("output_text", "text")))
        elif t == "reasoning":
            add(line, 0, "thinking", _texts(p.get("summary"), ("summary_text",)))
        elif t in ("function_call_output", "custom_tool_call_output"):
            cid = p.get("call_id")
            add(
                line,
                0,
                "tool_result",
                _texts(p.get("output"), ("input_text", "output_text", "text")),
                cid if isinstance(cid, str) else None,
            )
    return out
