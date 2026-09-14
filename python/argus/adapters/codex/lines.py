"""Read a Codex rollout from a byte offset and normalize every line.

Two on-disk formats:

- **Envelope** (v0.34+, Sept 2025): ``{"timestamp","ordinal"?,"type","payload"}``.
- **Legacy raw** (v0.20-v0.30): line 1 is ``{"id","timestamp","instructions"}``
  followed by bare Responses API items with no timestamps.

Both become ``Line`` records so the extractors never care which they got.
Partial trailing lines are held back until the next tick (same rule as the
Claude reader). ``.jsonl.zst`` cold files are immutable and read whole when a
decoder is available; otherwise they are skipped with one recorded reason.
"""
from __future__ import annotations

import io
import json
import logging
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO, Any, Iterator

from ..base import ParseError

logger = logging.getLogger(__name__)

MAX_TICK_BYTES = 64 * 1024 * 1024
MAX_ZST_BYTES = 256 * 1024 * 1024
_warned_no_zstd = False


def _zstd_decompress(data: bytes) -> bytes | None:
    """Decompress with the stdlib module (3.14+) or the ``zstandard`` package."""
    try:
        from compression import zstd  # type: ignore[import-not-found]

        return zstd.decompress(data)
    except ImportError:
        pass
    try:
        import zstandard  # type: ignore[import-not-found]

        return zstandard.ZstdDecompressor().decompressobj().decompress(data)
    except ImportError:
        return None


def zstd_available() -> bool:
    try:
        from compression import zstd  # noqa: F401  # type: ignore[import-not-found]

        return True
    except ImportError:
        pass
    try:
        import zstandard  # noqa: F401  # type: ignore[import-not-found]

        return True
    except ImportError:
        return False


@contextmanager
def open_rollout(path: Path) -> Iterator[IO[bytes] | None]:
    """Binary stream over a rollout (decompressed for .zst); None if undecodable."""
    if path.name.endswith(".zst"):
        raw = path.read_bytes()
        data = _zstd_decompress(raw) if len(raw) <= MAX_ZST_BYTES else None
        yield io.BytesIO(data) if data is not None else None
        return
    with open(path, "rb") as fh:
        yield fh


@dataclass
class Line:
    """One normalized rollout record. ``offset`` is the byte position of the
    line start and is the stable id for anything derived from it."""

    offset: int
    timestamp: str | None
    kind: str
    payload: dict[str, Any]
    ordinal: int | None = None


@dataclass
class ReadResult:
    lines: list[Line] = field(default_factory=list)
    new_offset: int = 0
    parse_errors: list[ParseError] = field(default_factory=list)
    whole_file: bool = False  # True for immutable .zst reads (no holdback applies)


def _normalize(obj: Any, offset: int, fmt: str, path: Path, line: str) -> Line | ParseError | None:
    if not isinstance(obj, dict):
        return ParseError(
            file=str(path), byte_offset=offset, reason="line is not a JSON object", raw_line_truncated=line[:200]
        )
    if fmt == "legacy":
        if offset == 0 and isinstance(obj.get("id"), str) and "type" not in obj:
            ts = obj.get("timestamp")
            return Line(offset, ts if isinstance(ts, str) else None, "session_meta", obj)
        if "record_type" in obj or not isinstance(obj.get("type"), str):
            return None
        return Line(offset, None, "response_item", obj)
    kind = obj.get("type")
    payload = obj.get("payload")
    if not isinstance(kind, str) or not isinstance(payload, dict):
        return None  # e.g. a realtime_item with a non-object payload; not an error
    ordinal = obj.get("ordinal")
    ts = obj.get("timestamp")
    return Line(
        offset,
        ts if isinstance(ts, str) else None,
        kind,
        payload,
        ordinal if isinstance(ordinal, int) and not isinstance(ordinal, bool) else None,
    )


def parse_text(text: str, base_offset: int, fmt: str, path: Path) -> tuple[list[Line], list[ParseError]]:
    """Normalize newline-separated JSON ``text`` whose first byte sits at ``base_offset``."""
    lines: list[Line] = []
    errors: list[ParseError] = []
    offset = base_offset
    for raw in text.split("\n"):
        if not raw:
            offset += 1
            continue
        nbytes = len(raw.encode("utf-8"))
        try:
            obj = json.loads(raw)
        except json.JSONDecodeError as e:
            errors.append(ParseError(file=str(path), byte_offset=offset, reason=str(e), raw_line_truncated=raw[:200]))
            offset += nbytes + 1
            continue
        out = _normalize(obj, offset, fmt, path, raw)
        if isinstance(out, Line):
            lines.append(out)
        elif isinstance(out, ParseError):
            errors.append(out)
        offset += nbytes + 1
    return lines, errors


def read_lines(path: Path, from_offset: int, fmt: str) -> ReadResult:
    """Read new bytes after ``from_offset`` and normalize them.

    ``fmt`` is ``"envelope"`` or ``"legacy"`` (decided once per file from its
    first line by discovery). ``new_offset`` is the offset to persist.
    """
    global _warned_no_zstd
    if path.name.endswith(".zst"):
        size = path.stat().st_size
        if size <= from_offset:
            return ReadResult(new_offset=from_offset, whole_file=True)
        raw = path.read_bytes()
        data = _zstd_decompress(raw) if len(raw) <= MAX_ZST_BYTES else None
        if data is None:
            reason = (
                "zstd decoder unavailable (install the 'zstandard' package or use Python 3.14+)"
                if len(raw) <= MAX_ZST_BYTES
                else f"compressed rollout exceeds {MAX_ZST_BYTES} bytes; skipped"
            )
            if not _warned_no_zstd:
                logger.warning("codex: %s - %s", reason, path)
                _warned_no_zstd = True
            return ReadResult(
                new_offset=size,
                parse_errors=[ParseError(file=str(path), byte_offset=0, reason=reason, raw_line_truncated="")],
                whole_file=True,
            )
        lines, errors = parse_text(data.decode("utf-8", errors="replace"), 0, fmt, path)
        return ReadResult(lines=lines, new_offset=size, parse_errors=errors, whole_file=True)

    with open(path, "rb") as fh:
        fh.seek(0, 2)
        size = fh.tell()
        if size <= from_offset:
            return ReadResult(new_offset=from_offset)
        read_len = min(size - from_offset, MAX_TICK_BYTES)
        fh.seek(from_offset)
        raw = fh.read(read_len)

    text = raw.decode("utf-8", errors="replace")
    last_nl = text.rfind("\n")
    consumable = text[: last_nl + 1] if last_nl != -1 else ""
    new_offset = from_offset + len(consumable.encode("utf-8"))
    lines, errors = parse_text(consumable, from_offset, fmt, path)
    return ReadResult(lines=lines, new_offset=new_offset, parse_errors=errors)
