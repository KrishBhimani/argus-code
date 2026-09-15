"""Ingest ``~/.codex/history.jsonl`` -- one line per user prompt:
``{"session_id":"<uuid>","ts":<unix seconds>,"text":"..."}``.

No project path is recorded, so the row carries ``session_id`` and its
``project_path`` is filled from the session row (now, or later through
``Repository.resolve_prompt_projects`` once the session has been ingested).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel

from ...schema.types import Prompt

if TYPE_CHECKING:
    from ...store.repository import Repository

DISPLAY_CAP_BYTES = 8 * 1024
MAX_TICK_BYTES = 64 * 1024 * 1024


class HistoryIngestStats(BaseModel):
    inserted: int
    skipped_empty: int
    parse_errors: int
    new_offset: int
    resolved: int


def line_to_prompt(entry: dict[str, Any], project_path: str) -> Prompt | None:
    """Pure transformation: history entry -> Prompt row, or None if skipped."""
    text = entry.get("text")
    sid = entry.get("session_id")
    ts = entry.get("ts")
    if not isinstance(text, str) or not isinstance(sid, str) or not sid:
        return None
    if not isinstance(ts, (int, float)) or isinstance(ts, bool):
        return None
    trimmed = text.strip()
    if not trimmed:
        return None
    display = text
    if len(display.encode("utf-8")) > DISPLAY_CAP_BYTES:
        display = display.encode("utf-8")[: DISPLAY_CAP_BYTES - 1].decode("utf-8", errors="ignore") + "…"
    return Prompt(
        timestamp_ms=int(ts * 1000),
        project_path=project_path,
        display=display,
        pasted_chars=0,
        is_slash=1 if trimmed.startswith("/") else 0,
        session_id=f"codex:{sid.lower()}",
    )


def ingest_history_file(file_path: Path, repo: "Repository") -> HistoryIngestStats:
    """Read ``file_path`` from the stored offset, append new prompts, heal projects."""
    offset_key = f"history:{file_path}"
    from_offset = repo.get_file_offset(offset_key)
    with open(file_path, "rb") as fh:
        fh.seek(0, 2)
        size = fh.tell()
        if size < from_offset:  # rotation / truncation
            from_offset = 0
        if size <= from_offset:
            resolved = repo.resolve_prompt_projects()
            return HistoryIngestStats(
                inserted=0, skipped_empty=0, parse_errors=0, new_offset=from_offset, resolved=resolved
            )
        fh.seek(from_offset)
        raw = fh.read(min(size - from_offset, MAX_TICK_BYTES))

    text = raw.decode("utf-8", errors="replace")
    last_nl = text.rfind("\n")
    consumable = text[: last_nl + 1] if last_nl != -1 else ""
    new_offset = from_offset + len(consumable.encode("utf-8"))

    rows: list[Prompt] = []
    parse_errors = skipped = 0
    project_cache: dict[str, str] = {}
    for line in consumable.split("\n"):
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            parse_errors += 1
            continue
        if not isinstance(obj, dict):
            parse_errors += 1
            continue
        sid = obj.get("session_id")
        project = ""
        if isinstance(sid, str) and sid:
            key = f"codex:{sid.lower()}"
            if key not in project_cache:
                s = repo.get_session(key)
                project_cache[key] = s.project_path if s else ""
            project = project_cache[key]
        row = line_to_prompt(obj, project)
        if row is None:
            skipped += 1
            continue
        rows.append(row)

    repo.insert_prompts(rows)
    repo.set_file_offset(offset_key, new_offset)
    resolved = repo.resolve_prompt_projects()
    return HistoryIngestStats(
        inserted=len(rows),
        skipped_empty=skipped,
        parse_errors=parse_errors,
        new_offset=new_offset,
        resolved=resolved,
    )
