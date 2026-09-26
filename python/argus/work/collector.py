"""One collection pass (facts -> git -> links) and the argus-work background thread."""
from __future__ import annotations

import logging
import re
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from ..store.repository import normalize_project_path
from . import gitscan
from .db import open_work_db, set_meta
from .facts import GAP_CAP_MS, collect_facts
from .linker import link_repo

logger = logging.getLogger("argus.work")
WORK_THREAD_NAME = "argus-work"


@dataclass
class PassResult:
    repos: int = 0
    commits: int = 0
    links: dict[str, int] = field(default_factory=lambda: {"exact": 0, "coauthored": 0, "inferred": 0})
    errors: dict[str, str] = field(default_factory=dict)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _seed_archived_sessions(conn) -> None:
    """Sessions that exist only in the archive (Claude Code already deleted their
    transcript) get their folder from argus.db's stored project path (read-only)."""
    if not conn.execute("SELECT 1 FROM pragma_database_list WHERE name = 'core'").fetchone():
        return
    conn.execute(
        """INSERT INTO session_repo (session_id, repo_id, cwd, git_branch)
           SELECT s.id, NULL, s.project_path, NULL FROM core.sessions s
           WHERE s.id LIKE 'claude_code:%' AND instr(s.id, '/') = 0 AND s.project_path != ''
             AND s.id NOT IN (SELECT session_id FROM session_repo)"""
    )


PROMPT_WINDOW_MS = 120_000   # a session's first prompt is logged within seconds of its start


def _ms(ts: str) -> float:
    return datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp() * 1000


_PLACEHOLDER = re.compile(r"\[(?:Image|Pasted text) #\d+[^\]]*\]")


def _prompt_title(display: str) -> str | None:
    """A prompt's first words as a title, without Claude Code's [Image #1] / [Pasted text #1] markers."""
    for line in display.splitlines():
        text = " ".join(_PLACEHOLDER.sub("", line).split())
        if text:
            return text[:120]
    return None


def _fill_from_archive(conn) -> None:
    """Sessions whose transcript Claude Code deleted still have their turns and your prompts
    in argus.db (read-only). Estimate their time from turn gaps, capped like transcript gaps,
    and title them with the prompt that started them. Transcript facts, when present, win."""
    if not conn.execute("SELECT 1 FROM pragma_database_list WHERE name = 'core'").fetchone():
        return
    rows = conn.execute(
        """SELECT sr.session_id, cs.project_path, cs.started_at FROM session_repo sr
           JOIN core.sessions cs ON cs.id = sr.session_id
           WHERE sr.repo_id IS NOT NULL AND instr(sr.session_id, '/') = 0
             AND NOT EXISTS (SELECT 1 FROM session_facts f
                             WHERE f.session_id = sr.session_id AND f.last_line_ts IS NOT NULL)""").fetchall()
    conn.execute("BEGIN")
    try:
        for r in rows:
            sid = r["session_id"]
            if not conn.execute("SELECT 1 FROM active_spans WHERE session_id = ? LIMIT 1", (sid,)).fetchone():
                times = [t["timestamp"] for t in conn.execute(
                    "SELECT timestamp FROM core.turns WHERE session_id = ? ORDER BY timestamp", (sid,))]
                for prev, ts in zip(times, times[1:]):
                    gap = _ms(ts) - _ms(prev)
                    if gap > 0:
                        conn.execute("INSERT INTO active_spans VALUES (?, ?, ?, 'archive')",
                                     (sid, ts, int(min(gap, GAP_CAP_MS))))
            titled = conn.execute("SELECT 1 FROM session_facts WHERE session_id = ? AND title IS NOT NULL", (sid,)).fetchone()
            if not titled and r["started_at"]:
                start = _ms(r["started_at"])
                candidates = conn.execute(
                    """SELECT display FROM core.prompts
                       WHERE project_path = ? AND timestamp_ms BETWEEN ? AND ?
                       ORDER BY is_slash, ABS(timestamp_ms - ?)""",
                    (r["project_path"], start - PROMPT_WINDOW_MS, start + PROMPT_WINDOW_MS, start)).fetchall()
                title = next(filter(None, (_prompt_title(p["display"]) for p in candidates)), None)
                if title:
                    conn.execute(
                        """INSERT INTO session_facts (session_id, title, title_source) VALUES (?, ?, 'prompt')
                           ON CONFLICT(session_id) DO UPDATE SET title = excluded.title, title_source = 'prompt'
                           WHERE session_facts.title IS NULL""", (sid, title))
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise


def _map_sessions(conn) -> None:
    _seed_archived_sessions(conn)
    known = {normalize_project_path(r["root"]): r["id"] for r in conn.execute("SELECT id, root FROM repos")}
    for s in conn.execute("SELECT session_id, cwd FROM session_repo WHERE repo_id IS NULL AND cwd IS NOT NULL").fetchall():
        root = gitscan.repo_root(s["cwd"])
        if root:
            rid = gitscan.ensure_repo(conn, root)
            known[normalize_project_path(root)] = rid
        else:  # cwd gone: fall back to a known repo that contains it
            cwd = normalize_project_path(s["cwd"])
            rid = next((i for r, i in sorted(known.items(), key=lambda kv: -len(kv[0]))
                        if cwd == r or cwd.startswith(r + "/")), None)
        if rid is not None:
            conn.execute("UPDATE session_repo SET repo_id = ? WHERE session_id = ?", (rid, s["session_id"]))


def run_pass(data_dir: Path, adapter, now_iso: str | None = None) -> PassResult:
    now = now_iso or _now()
    res = PassResult()
    conn = open_work_db(data_dir)
    try:
        try:
            collect_facts(conn, adapter)
        except Exception as e:  # noqa: BLE001
            logger.warning("work: transcript facts failed: %s", e)
            res.errors["facts"] = str(e)
        _map_sessions(conn)
        try:
            _fill_from_archive(conn)
        except Exception as e:  # noqa: BLE001  never stop the pass
            logger.warning("work: archive fill failed: %s", e)
            res.errors["archive"] = str(e)
        g = gitscan.global_user_email()
        if g:
            set_meta(conn, "global_user_email", g.lower())
        for r in conn.execute("SELECT id, root FROM repos").fetchall():
            res.repos += 1
            if not Path(r["root"]).is_dir():
                conn.execute("UPDATE repos SET present = 0 WHERE id = ?", (r["id"],))
            else:
                try:
                    res.commits += gitscan.scan_repo(conn, r["id"], r["root"], now)
                except gitscan.GitError as e:
                    conn.execute("UPDATE repos SET last_error = ? WHERE id = ?", (str(e), r["id"]))
                    res.errors[r["root"]] = str(e)
            for k, v in link_repo(conn, r["id"]).items():
                res.links[k] += v
        set_meta(conn, "last_scan_at", now)
    finally:
        conn.close()
    return res


class WorkCollector:
    def __init__(self, data_dir: Path, adapter, interval: float = 300) -> None:
        self._data_dir, self._adapter, self._interval = Path(data_dir), adapter, interval
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, name=WORK_THREAD_NAME, daemon=True)

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                r = run_pass(self._data_dir, self._adapter)
                logger.info("work: scanned %d repos, %d new commits, links (total) %s", r.repos, r.commits, r.links)
            except Exception:  # noqa: BLE001  never take argus start down
                logger.exception("work: pass failed")
            self._stop.wait(self._interval)

    def start(self) -> "WorkCollector":
        self._thread.start()
        return self

    def stop(self, timeout: float = 10.0) -> bool:
        self._stop.set()
        self._thread.join(timeout)
        return not self._thread.is_alive()
