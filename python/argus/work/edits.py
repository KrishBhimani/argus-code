"""Which files each session edited, read from the transcripts' tool calls (read-only).

This is the record the rest of the work trial leans on for "what did this session do":
it says which repo a session really worked in and which commits carry its changes, where
the starting folder and the clock can only guess.

Limits, on purpose: an attempted edit counts even if the tool then failed (a retry nearly
always follows), and files changed only through a shell command are not seen.
"""
from __future__ import annotations

import logging
import posixpath
import re
import sqlite3

from ..store.repository import normalize_project_path
from .db import set_meta
from .facts import _ms, read_new_lines

logger = logging.getLogger("argus.work")

# tool name -> the input field that holds the file it changes
EDIT_TOOLS = {"Edit": "file_path", "Write": "file_path", "MultiEdit": "file_path", "NotebookEdit": "notebook_path"}
# The facts pass keeps its place per transcript under the bare path; this pass keeps its own,
# so a work.db that already read every transcript for facts still reads them here.
OFFSET_PREFIX = "edits:"
_ABSOLUTE = re.compile(r"^(?:[A-Za-z]:[\\/]|[\\/])")


def edited_path(raw: object, cwd: object) -> str | None:
    """The edited file as a normalized absolute path, or None when it can't be one."""
    if not isinstance(raw, str) or not raw.strip():
        return None
    if not _ABSOLUTE.match(raw):
        if not isinstance(cwd, str) or not cwd:
            return None
        raw = cwd.rstrip("\\/") + "/" + raw
    return normalize_project_path(posixpath.normpath(raw.replace("\\", "/")))


def _apply(conn: sqlite3.Connection, sid: str, lines: list[dict]) -> int:
    n = 0
    for o in lines:
        msg = o.get("message")
        content = msg.get("content") if isinstance(msg, dict) else None
        ts = o.get("timestamp")
        if o.get("type") != "assistant" or not isinstance(content, list) or not ts or _ms(ts) is None:
            continue
        for b in content:
            if not isinstance(b, dict) or b.get("type") != "tool_use" or b.get("name") not in EDIT_TOOLS:
                continue
            inp = b.get("input")
            path = edited_path(inp.get(EDIT_TOOLS[b["name"]]), o.get("cwd")) if isinstance(inp, dict) else None
            if path is None:
                continue
            conn.execute(
                """INSERT INTO session_files (session_id, path, edits, first_ts, last_ts) VALUES (?, ?, 1, ?, ?)
                   ON CONFLICT(session_id, path) DO UPDATE SET edits = edits + 1,
                     first_ts = MIN(first_ts, excluded.first_ts), last_ts = MAX(last_ts, excluded.last_ts)""",
                (sid, path, ts, ts))
            n += 1
    return n


def collect_edits(conn: sqlite3.Connection, adapter) -> set[str]:
    """Read new transcript lines for file edits. Returns the sessions that gained any.
    A sub-agent's edits belong to the session that started it."""
    touched: set[str] = set()
    conn.execute("DELETE FROM meta WHERE key = 'edits_error'")  # reset per pass
    for f in adapter.discover_session_files():
        if adapter.should_skip(f):
            continue
        sid = f"claude_code:{f.stem}"
        for path in [f, *adapter.sub_session_files_for(f)]:
            conn.execute("BEGIN")
            try:
                lines = read_new_lines(conn, path, key=OFFSET_PREFIX + str(path))
                if lines and _apply(conn, sid, lines):
                    touched.add(sid)
                conn.execute("COMMIT")
            except Exception as e:  # noqa: BLE001  one bad file must not stop the others
                conn.execute("ROLLBACK")
                logger.warning("work: skipped edits from %s: %s", path, e)
                set_meta(conn, "edits_error", f"{path.name}: {e}")
            except BaseException:
                conn.execute("ROLLBACK")
                raise
    return touched
