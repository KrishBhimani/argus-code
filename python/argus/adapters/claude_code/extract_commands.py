"""Shell commands among an assistant message's tool_use blocks."""
from __future__ import annotations

from ..base import RawCommand
from .schemas import AssistantLine

#: tool name -> the shell it runs
SHELL_TOOLS = {"Bash": "bash", "PowerShell": "powershell"}


def extract_commands(assistant_lines: list[AssistantLine]) -> list[RawCommand]:
    """One RawCommand per shell tool_use with a non-empty string command. Anything else
    (a list, a missing field, a malformed input) is skipped: its tool call still counts."""
    out: list[RawCommand] = []
    for line in assistant_lines:
        for block in line.message.content:
            if not isinstance(block, dict) or block.get("type") != "tool_use":
                continue
            shell = SHELL_TOOLS.get(block.get("name") or "")
            inp, use_id = block.get("input"), block.get("id")
            if not shell or not isinstance(inp, dict) or not isinstance(use_id, str) or not use_id:
                continue
            command = inp.get("command")
            if not isinstance(command, str) or not command.strip():
                continue
            out.append(RawCommand(
                tool_use_id=use_id, shell=shell, command=command, cwd=line.cwd or "",
                background=inp.get("run_in_background") is True, timestamp=line.timestamp,
            ))
    return out
