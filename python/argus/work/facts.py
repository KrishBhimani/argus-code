"""Collect transcript facts the main pipeline skips. Reads ~/.claude read-only."""
from __future__ import annotations

import json
import logging
import re
import sqlite3
from datetime import datetime
from pathlib import Path

from .db import set_meta

logger = logging.getLogger("argus.work")
GAP_CAP_MS = 300_000
COMMIT_CMD = re.compile(r"\bgit\b[^|;&\n]*\bcommit\b")
COMMIT_OUT = re.compile(r"^\[[^\]\n]*? (?:\(root-commit\) )?([0-9a-f]{7,40})\]", re.MULTILINE)
PR_URL = re.compile(r"https?://[^\s/]+/([^\s/]+/[^\s/]+)/pull/(\d+)")


def _ms(ts) -> float | None:
    """Epoch ms, or None for a value that isn't an ISO timestamp (never raises)."""
    try:
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00")).timestamp() * 1000
    except ValueError:
        return None


def _int(v) -> int | None:
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def read_new_lines(conn: sqlite3.Connection, path: Path) -> list[dict]:
    """Complete JSON lines past this file's stored offset (bytes, so bad UTF-8 can't drift it)."""
    row = conn.execute("SELECT byte_offset FROM file_offsets WHERE path = ?", (str(path),)).fetchone()
    start = row["byte_offset"] if row else 0
    with open(path, "rb") as fh:
        fh.seek(0, 2)
        size = fh.tell()
        if size < start:  # file replaced or truncated
            start = 0
        fh.seek(start)
        raw = fh.read(size - start)
    end = raw.rfind(b"\n")
    if end < 0:
        return []
    out = []
    for chunk in raw[: end + 1].split(b"\n"):
        if not chunk.strip():
            continue
        try:
            obj = json.loads(chunk.decode("utf-8", errors="replace"))
        except ValueError:
            continue
        if isinstance(obj, dict):
            out.append(obj)
    conn.execute("INSERT OR REPLACE INTO file_offsets (path, byte_offset) VALUES (?, ?)",
                 (str(path), start + end + 1))
    return out


def _text(block_content) -> str:
    if isinstance(block_content, str):
        return block_content
    if isinstance(block_content, list):
        return "\n".join(b.get("text", "") for b in block_content if isinstance(b, dict))
    return ""


def _apply(conn: sqlite3.Connection, sid: str, lines: list[dict], top_level: bool) -> None:
    facts = conn.execute("SELECT title, title_source, last_line_ts FROM session_facts WHERE session_id = ?",
                         (sid,)).fetchone()
    title, source, last_ts = (facts["title"], facts["title_source"], facts["last_line_ts"]) if facts else (None, None, None)
    repo = conn.execute("SELECT cwd, git_branch FROM session_repo WHERE session_id = ?", (sid,)).fetchone()
    cwd, branch = (repo["cwd"], repo["git_branch"]) if repo else (None, None)
    pending: dict[str, str] = {}  # tool_use id -> "commit" | "pr" (same read only)
    for o in lines:
        t = o.get("type")
        ts = o.get("timestamp")
        if top_level:
            cwd = cwd or o.get("cwd")
            branch = o.get("gitBranch") or branch
            if t == "custom-title" and o.get("customTitle"):
                title, source = o["customTitle"], "custom"
            elif t == "ai-title" and o.get("aiTitle") and source != "custom":
                title, source = o["aiTitle"], "ai"
            elif t == "system" and o.get("subtype") == "turn_duration" and _ms(ts) is not None                     and _int(o.get("durationMs")) is not None:
                conn.execute("INSERT INTO active_spans VALUES (?, ?, ?, 'measured')",
                             (sid, ts, _int(o.get("durationMs"))))
            elif t == "pr-link" and o.get("prUrl"):
                conn.execute("INSERT OR IGNORE INTO session_prs VALUES (?, ?, ?, ?, ?)",
                             (sid, o["prUrl"], o.get("prNumber"), o.get("prRepository"), ts))
            if ts and t in ("user", "assistant", "system") and _ms(ts) is not None:
                if last_ts and _ms(last_ts) is not None:
                    gap = _ms(ts) - _ms(last_ts)
                    if gap > 0:
                        conn.execute("INSERT INTO active_spans VALUES (?, ?, ?, 'estimated')",
                                     (sid, ts, int(min(gap, GAP_CAP_MS))))
                last_ts = ts
        msg = o.get("message")
        if not isinstance(msg, dict):
            continue
        if t == "assistant" and msg.get("id"):
            attr = [o.get(k) for k in ("attributionSkill", "attributionMcpServer", "attributionMcpTool", "attributionPlugin")]
            if any(attr):
                conn.execute("INSERT OR REPLACE INTO turn_attribution VALUES (?, ?, ?, ?, ?)",
                             (f"{sid}:{msg['id']}", *attr))
        content = msg.get("content")
        if not isinstance(content, list) or not top_level:
            continue
        for b in content:
            if not isinstance(b, dict):
                continue
            if b.get("type") == "tool_use" and b.get("name") in ("Bash", "PowerShell"):
                cmd = (b.get("input") or {}).get("command") or ""
                if COMMIT_CMD.search(cmd):
                    pending[b.get("id")] = "commit"
                elif "gh pr create" in cmd:
                    pending[b.get("id")] = "pr"
            elif b.get("type") == "tool_result" and b.get("tool_use_id") in pending:
                text = _text(b.get("content"))
                if pending[b["tool_use_id"]] == "commit":
                    for m in COMMIT_OUT.finditer(text):
                        conn.execute("INSERT OR IGNORE INTO commit_claims VALUES (?, ?, ?)", (sid, m.group(1), ts))
                else:
                    for m in PR_URL.finditer(text):
                        conn.execute("INSERT OR IGNORE INTO session_prs VALUES (?, ?, ?, ?, ?)",
                                     (sid, m.group(0), int(m.group(2)), m.group(1), ts))
    if top_level:
        conn.execute("INSERT OR REPLACE INTO session_facts VALUES (?, ?, ?, ?)", (sid, title, source, last_ts))
        conn.execute(
            """INSERT INTO session_repo (session_id, repo_id, cwd, git_branch) VALUES (?, NULL, ?, ?)
               ON CONFLICT(session_id) DO UPDATE SET cwd = excluded.cwd, git_branch = excluded.git_branch""",
            (sid, cwd, branch),
        )


def collect_facts(conn: sqlite3.Connection, adapter) -> set[str]:
    touched: set[str] = set()
    conn.execute("DELETE FROM meta WHERE key = 'facts_error'")  # reset per pass
    for f in adapter.discover_session_files():
        if adapter.should_skip(f):
            continue
        sid = f"claude_code:{f.stem}"
        groups = [(sid, f, True)] + [(f"{sid}/{s.stem}", s, False) for s in adapter.sub_session_files_for(f)]
        for session_id, path, top in groups:
            conn.execute("BEGIN")
            try:
                lines = read_new_lines(conn, path)
                if lines:
                    _apply(conn, session_id, lines, top)
                    touched.add(sid)
                conn.execute("COMMIT")
            except Exception as e:  # noqa: BLE001  one bad file must not stop the others
                conn.execute("ROLLBACK")
                logger.warning("work: skipped facts from %s: %s", path, e)
                set_meta(conn, "facts_error", f"{path.name}: {e}")
            except BaseException:
                conn.execute("ROLLBACK")
                raise
    return touched
