"""Read Codex rollout records (one JSON envelope per line) from a byte offset."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..base import ParseError

#: Per-read cap (same as the Claude adapter) so a corrupt huge file can't OOM us.
MAX_READ_BYTES = 64 * 1024 * 1024


@dataclass(frozen=True)
class Record:
    """One ``{timestamp, ordinal, type, payload}`` envelope line."""

    offset: int  # file-wide byte offset of the line's first byte
    ordinal: int | None
    timestamp: str
    type: str
    payload: dict[str, Any]


def read_records(
    path: Path, start: int, stop: int | None = None
) -> tuple[list[Record], list[ParseError], int]:
    """Parse every complete line in ``[start, stop)`` (``stop`` = EOF if None).

    Returns ``(records, parse_errors, end)`` where ``end`` is just past the last
    complete line (``start`` when there is none): a trailing partial line waits
    for the next read. Valid JSON that isn't an envelope is skipped silently --
    Codex adds record kinds over time. Parse errors never carry line content.
    """
    with open(path, "rb") as fh:
        fh.seek(0, 2)
        limit = fh.tell() if stop is None else min(fh.tell(), stop)
        if limit <= start:
            return [], [], start
        fh.seek(start)
        raw = fh.read(min(limit - start, MAX_READ_BYTES))
    last_nl = raw.rfind(b"\n")
    if last_nl == -1:
        return [], [], start
    records: list[Record] = []
    errors: list[ParseError] = []
    offset = start
    for line in raw[: last_nl + 1].split(b"\n")[:-1]:
        here = offset
        offset += len(line) + 1
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
        except ValueError as e:  # JSONDecodeError and UnicodeDecodeError
            msg = e.msg if isinstance(e, json.JSONDecodeError) else "invalid utf-8"
            errors.append(
                ParseError(file=str(path), byte_offset=here,
                           reason=f"invalid JSON: {msg}", raw_line_truncated="")
            )
            continue
        if not isinstance(obj, dict):
            continue
        rtype, payload = obj.get("type"), obj.get("payload")
        if not isinstance(rtype, str) or not isinstance(payload, dict):
            continue
        ordinal = obj.get("ordinal")
        ts = obj.get("timestamp")
        records.append(
            Record(
                offset=here,
                ordinal=ordinal if isinstance(ordinal, int) else None,
                timestamp=ts if isinstance(ts, str) else "",
                type=rtype,
                payload=payload,
            )
        )
    return records, errors, start + last_nl + 1
