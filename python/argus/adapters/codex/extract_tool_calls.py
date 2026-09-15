"""One RawToolCall per function_call / custom_tool_call / local_shell_call.

Error attribution is best effort: Codex does not persist ``exec_command_end``
in rollouts, so exit codes come from whatever the file does carry (MCP and
patch end events, paginated ``item_completed`` items, and the exec output text
the model saw). The exec-output markers are unverified against real data.
"""
from __future__ import annotations

import bisect
import json
import re
from typing import Any

from ...schema.types import RawTurnEvent
from ..base import RawToolCall
from .lines import Line

_CALL_KINDS = ("function_call", "custom_tool_call", "local_shell_call")
_OUTPUT_KINDS = ("function_call_output", "custom_tool_call_output")
_EXIT_RE = re.compile(r"^\s*Exit code:\s*(-?\d+)", re.MULTILINE)


def _output_text(output: Any) -> str:
    if isinstance(output, str):
        return output
    if isinstance(output, list):
        return "\n".join(
            x.get("text", "") for x in output if isinstance(x, dict) and isinstance(x.get("text"), str)
        )
    return ""


def _is_int(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _output_is_error(text: str) -> bool:
    if not text:
        return False
    stripped = text.lstrip()
    if stripped.startswith("{"):
        try:
            obj = json.loads(stripped)
        except json.JSONDecodeError:
            obj = None
        if isinstance(obj, dict):
            md = obj.get("metadata")
            code = md.get("exit_code") if isinstance(md, dict) else obj.get("exit_code")
            if _is_int(code):
                return code != 0
    m = _EXIT_RE.search(text[:200])
    return bool(m and int(m.group(1)) != 0)


def _tool_name(p: dict[str, Any]) -> str:
    if p.get("type") == "local_shell_call":
        return "local_shell"
    name = str(p.get("name") or "?")
    ns = p.get("namespace")
    if isinstance(ns, str) and ns and ns != "functions" and not name.startswith("mcp__"):
        return f"{ns}__{name}"
    return name


def _input_size(p: dict[str, Any]) -> int:
    t = p.get("type")
    if t == "function_call":
        return len(str(p.get("arguments") or "").encode("utf-8"))
    if t == "custom_tool_call":
        return len(str(p.get("input") or "").encode("utf-8"))
    try:
        return len(json.dumps(p.get("action") or {}, separators=(",", ":")))
    except (TypeError, ValueError):
        return 0


def _subagent_type(p: dict[str, Any]) -> str | None:
    if p.get("name") != "spawn_agent":
        return None
    try:
        args = json.loads(p.get("arguments") or "{}")
    except (json.JSONDecodeError, TypeError):
        return None
    t = args.get("agent_type") if isinstance(args, dict) else None
    return t if isinstance(t, str) and t else None


def extract_tool_calls(
    lines: list[Line],
    turns: list[RawTurnEvent],
    bounds: list[int],
    skip_before_ordinal: int | None,
) -> list[RawToolCall]:
    """Emit one RawToolCall per call item, attributed to the turn whose
    ``token_count`` follows it. Calls after the last boundary belong to a turn
    that has not closed yet and are left for the next tick (the holdback rule
    in ``ingest_file`` guarantees they will be re-read)."""
    if not bounds:
        return []
    errors: set[str] = set()
    calls: list[tuple[Line, dict[str, Any], str]] = []

    for line in lines:
        if skip_before_ordinal is not None and line.ordinal is not None and line.ordinal < skip_before_ordinal:
            continue
        p = line.payload
        t = p.get("type")
        if line.kind == "response_item":
            if t in _CALL_KINDS:
                call_id = p.get("call_id") or p.get("id") or f"anon@{line.offset}"
                calls.append((line, p, str(call_id)))
            elif t in _OUTPUT_KINDS:
                cid = p.get("call_id")
                if isinstance(cid, str) and _output_is_error(_output_text(p.get("output"))):
                    errors.add(cid)
        elif line.kind == "event_msg":
            cid = p.get("call_id")
            if t == "mcp_tool_call_end" and isinstance(cid, str):
                r = p.get("result")
                if isinstance(r, dict) and "Err" in r:
                    errors.add(cid)
            elif t == "patch_apply_end" and isinstance(cid, str) and p.get("success") is False:
                errors.add(cid)
            elif t == "item_completed":
                item = p.get("item")
                if isinstance(item, dict):
                    iid = item.get("call_id") or item.get("id")
                    code = item.get("exit_code")
                    failed = (_is_int(code) and code != 0) or item.get("error") not in (None, "")
                    if isinstance(iid, str) and failed:
                        errors.add(iid)

    out: list[RawToolCall] = []
    block_by_turn: dict[int, int] = {}
    for line, p, call_id in calls:
        idx = bisect.bisect_left(bounds, line.offset)
        if idx >= len(turns):
            continue
        block = block_by_turn.get(idx, 0)
        block_by_turn[idx] = block + 1
        out.append(
            RawToolCall(
                native_turn_id=turns[idx].native_turn_id,
                turn_index=idx,
                block_index=block,
                tool_name=_tool_name(p),
                tool_use_id=call_id,
                is_error=1 if call_id in errors else 0,
                input_size=_input_size(p),
                subagent_type=_subagent_type(p),
                timestamp=line.timestamp or turns[idx].timestamp,
            )
        )
    return out
