"""Interpret single Codex records. Pure functions, no state, no I/O.

Shapes verified on real rollouts (Codex Desktop, cli 0.159.2):
- usage: ``token_usage_record`` (one per model response, unique response_id).
  ``input_tokens`` includes cached; ``output_tokens`` includes reasoning.
- actions: ``event_msg/item_completed`` items (CommandExecution, FileChange,
  McpToolCall, Extension, ImageView). The model-side ``exec``/``wait`` calls are
  code-mode plumbing whose effects are those items, so they are not counted.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from ..base import RawSegment
from .lines import Record

WRAPPER_TOOLS = frozenset({"exec", "wait"})
_CALL_TYPES = ("function_call", "custom_tool_call", "local_shell_call")
_ERROR_STATUSES = frozenset({"failed", "error"})
SEGMENT_CAP_BYTES = 16 * 1024  # same cap as the Claude adapter


def _n(d: dict[str, Any], k: str) -> int:
    v = d.get(k)
    return v if isinstance(v, int) and v > 0 else 0


def tokens_of(usage: dict[str, Any]) -> dict[str, int]:
    inp, cached = _n(usage, "input_tokens"), _n(usage, "cached_input_tokens")
    cw, out = _n(usage, "cache_write_input_tokens"), _n(usage, "output_tokens")
    return {
        # cached and cache-write tokens are parts of input_tokens
        # (total_tokens == input + output on every real record).
        "fresh_input_tokens": max(0, inp - cached - cw),
        "cache_read_tokens": cached,
        "cache_write_tokens": cw,
        "output_tokens": out,
        "reasoning_output_tokens": _n(usage, "reasoning_output_tokens"),
    }


def usage_record(rec: Record) -> tuple[str, dict[str, int]] | None:
    if rec.type != "token_usage_record":
        return None
    rid, usage = rec.payload.get("response_id"), rec.payload.get("usage")
    if not isinstance(rid, str) or not rid or not isinstance(usage, dict):
        return None
    return rid, tokens_of(usage)


def legacy_total_of(payload: dict[str, Any]) -> int | None:
    info = payload.get("info")
    tot = info.get("total_token_usage") if isinstance(info, dict) else None
    v = tot.get("total_tokens") if isinstance(tot, dict) else None
    return v if isinstance(v, int) else None


def legacy_usage(rec: Record) -> dict[str, int] | None:
    """Per-response usage from ``token_count`` (older Codex, no usage records)."""
    if rec.type != "event_msg" or rec.payload.get("type") != "token_count":
        return None
    info = rec.payload.get("info")
    last = info.get("last_token_usage") if isinstance(info, dict) else None
    return tokens_of(last) if isinstance(last, dict) else None


def is_task_start(rec: Record) -> bool:
    return rec.type == "event_msg" and rec.payload.get("type") == "task_started"


def is_task_end(rec: Record) -> bool:
    return rec.type == "event_msg" and rec.payload.get("type") in ("task_complete", "turn_aborted")


@dataclass(frozen=True)
class CallEvent:
    call_id: str
    tool_name: str
    offset: int
    timestamp: str
    is_error: int
    input_size: int
    subagent_type: str | None
    # "request": the model asked (its usage record follows);
    # "item": an execution finished (after the usage record that asked for it).
    side: str


def _size(v: Any) -> int:
    if v is None:
        return 0
    s = v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)
    return len(s.encode("utf-8"))


def _spawn_type(args: Any) -> str | None:
    if not isinstance(args, str):
        return None
    try:
        obj = json.loads(args)
    except ValueError:
        return None
    if not isinstance(obj, dict):
        return None
    for k in ("agent_type", "agent_role", "task_name"):
        v = obj.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()[:64]
    return None


def call_from_record(rec: Record) -> CallEvent | None:
    p = rec.payload
    if rec.type == "response_item" and p.get("type") in _CALL_TYPES:
        name = p.get("name")
        if not isinstance(name, str) or not name:
            name = "local_shell" if p.get("type") == "local_shell_call" else ""
        ns = p.get("namespace")
        call_id = p.get("call_id") or p.get("id")
        if not name or not isinstance(call_id, str) or not call_id:
            return None
        if name in WRAPPER_TOOLS and not ns:
            return None
        if isinstance(ns, str) and ns.startswith("mcp__"):
            return None  # the McpToolCall item for the same call is what we count
        args = p.get("arguments", p.get("input", p.get("action")))
        return CallEvent(call_id, name, rec.offset, rec.timestamp, 0, _size(args),
                         _spawn_type(args) if name == "spawn_agent" else None, "request")
    if rec.type == "event_msg" and p.get("type") == "item_completed":
        it = p.get("item")
        if not isinstance(it, dict) or not isinstance(it.get("id"), str):
            return None
        kind, failed = it.get("type"), it.get("status") in _ERROR_STATUSES
        if kind == "CommandExecution":
            ec = it.get("exit_code")
            name, err, size = "shell", failed or (isinstance(ec, int) and ec != 0), _size(it.get("command"))
        elif kind == "FileChange":
            name, err, size = "apply_patch", failed, _size(it.get("changes"))
        elif kind == "McpToolCall":
            server, tool, res = it.get("server"), it.get("tool"), it.get("result")
            if not isinstance(server, str) or not isinstance(tool, str):
                return None
            name = f"mcp__{server}__{tool}"
            err = failed or (isinstance(res, dict) and res.get("isError") is True)
            size = _size(it.get("arguments"))
        elif kind == "Extension":
            k = it.get("kind")
            name, err, size = (k if isinstance(k, str) and k else "extension"), failed, _size(it.get("query"))
        elif kind == "ImageView":
            name, err, size = "view_image", failed, 0
        else:
            return None
        return CallEvent(it["id"], name, rec.offset, rec.timestamp, int(bool(err)), size, None, "item")
    return None


def _cap(s: str) -> str:
    b = s.encode("utf-8")
    if len(b) <= SEGMENT_CAP_BYTES:
        return s
    return b[:SEGMENT_CAP_BYTES].decode("utf-8", errors="ignore") + "…"


def _texts(blocks: Any, kinds: tuple[str, ...]) -> list[str]:
    if isinstance(blocks, str):
        return [blocks] if blocks.strip() else []
    if not isinstance(blocks, list):
        return []
    out = []
    for b in blocks:
        if isinstance(b, dict) and b.get("type") in kinds and isinstance(b.get("text"), str) and b["text"].strip():
            out.append(b["text"])
    return out


def _seg(uid: str, ts: str, role: str, text: str, tool_use_id: str | None = None) -> RawSegment:
    return RawSegment(uid_suffix=uid, timestamp=ts, role=role, text=_cap(text), tool_use_id=tool_use_id)


def segments_from_record(rec: Record, counted_call_ids: set[str]) -> list[RawSegment]:
    p, ts = rec.payload, rec.timestamp
    if rec.type == "event_msg" and p.get("type") == "item_completed":
        it = p.get("item")
        if not isinstance(it, dict) or not isinstance(it.get("id"), str):
            return []
        iid, kind = it["id"], it.get("type")
        if kind == "UserMessage":
            return [_seg(f"{iid}:{i}", ts, "user", t) for i, t in enumerate(_texts(it.get("content"), ("text",)))]
        if kind == "AgentMessage":
            return [_seg(f"{iid}:{i}", ts, "assistant", t)
                    for i, t in enumerate(_texts(it.get("content"), ("Text", "text")))]
        if kind == "CommandExecution":
            out = it.get("aggregated_output") or it.get("formatted_output")
            return [_seg(f"{iid}:out", ts, "tool_result", out, iid)] if isinstance(out, str) and out.strip() else []
        if kind == "McpToolCall":
            res = it.get("result")
            text = "\n".join(_texts(res.get("content") if isinstance(res, dict) else None, ("text",)))
            return [_seg(f"{iid}:out", ts, "tool_result", text, iid)] if text.strip() else []
        if kind == "FileChange":
            out = it.get("stdout")
            return [_seg(f"{iid}:out", ts, "tool_result", out, iid)] if isinstance(out, str) and out.strip() else []
        return []
    if rec.type == "event_msg" and p.get("type") in ("user_message", "agent_message"):
        # Older Codex. user_message with a non-plain kind is injected context.
        msg = p.get("message")
        if p.get("type") == "user_message" and p.get("kind") not in (None, "plain"):
            return []
        if not isinstance(msg, str) or not msg.strip():
            return []
        role = "user" if p["type"] == "user_message" else "assistant"
        return [_seg(f"{p['type']}@{rec.offset}", ts, role, msg)]
    if rec.type == "response_item" and p.get("type") == "agent_message" and isinstance(p.get("id"), str):
        # Inter-agent message delivered TO this thread (outbound ones are send_message calls).
        return [_seg(f"{p['id']}:{i}", ts, "user", t)
                for i, t in enumerate(_texts(p.get("content"), ("input_text", "text")))]
    if rec.type == "response_item" and p.get("type") in ("function_call_output", "custom_tool_call_output"):
        cid = p.get("call_id")
        if not isinstance(cid, str) or cid not in counted_call_ids:
            return []
        text = "\n".join(_texts(p.get("output"), ("input_text", "output_text", "text")))
        return [_seg(f"{cid}:out", ts, "tool_result", text, cid)] if text.strip() else []
    return []
