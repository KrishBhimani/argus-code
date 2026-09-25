"""One collection pass (facts -> git -> links) and the argus-work background thread."""
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from ..store.repository import normalize_project_path
from . import gitscan
from .db import open_work_db, set_meta
from .facts import collect_facts
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
