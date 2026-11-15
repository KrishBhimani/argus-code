"""Link sessions to commits with labelled evidence (exact > files > coauthored > inferred)."""
from __future__ import annotations

import bisect
import json
import sqlite3
from datetime import datetime

from ..store.repository import normalize_project_path
from .db import get_meta

WINDOW_MS = 30 * 60 * 1000
FILES_WINDOW_MS = 7 * 86_400_000   # a commit carries a session's edit only this long after it
_RANK = {"exact": 0, "files": 1, "coauthored": 2, "inferred": 3}


def _ms(ts: str) -> float:
    return datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp() * 1000


def mine_emails(conn: sqlite3.Connection, repo_id: int) -> set[str]:
    row = conn.execute("SELECT user_emails FROM repos WHERE id = ?", (repo_id,)).fetchone()
    emails = set(json.loads(row["user_emails"])) if row else set()
    g = get_meta(conn, "global_user_email")
    return {e.lower() for e in emails | ({g} if g else set()) if e}


def _turn_times(conn: sqlite3.Connection, sid: str) -> list[float]:
    rows = conn.execute(
        "SELECT timestamp FROM core.turns WHERE session_id = ? OR substr(session_id, 1, ?) = ?",
        (sid, len(sid) + 1, sid + "/"),
    ).fetchall()
    return sorted(_ms(r["timestamp"]) for r in rows)


def _gap_after_turn(times: list[float], t: float) -> float | None:
    """ms between the latest turn at/before t and t, if within the window."""
    i = bisect.bisect_right(times, t)
    if i == 0:
        return None
    gap = t - times[i - 1]
    return gap if gap <= WINDOW_MS else None


def _edits_by_file(conn: sqlite3.Connection, repo_id: int, sessions: list[str]) -> dict[str, list[tuple[str, float, float]]]:
    """repo-relative path -> (session, first edit, last edit), for this folder's sessions."""
    row = conn.execute("SELECT root FROM repos WHERE id = ?", (repo_id,)).fetchone()
    prefix = normalize_project_path(row["root"]) + "/"
    out: dict[str, list[tuple[str, float, float]]] = {}
    for sid in sessions:
        for f in conn.execute("SELECT path, first_ts, last_ts FROM session_files WHERE session_id = ?", (sid,)):
            if f["path"].startswith(prefix):
                out.setdefault(f["path"][len(prefix):], []).append((sid, _ms(f["first_ts"]), _ms(f["last_ts"])))
    return out


def link_repo(conn: sqlite3.Connection, repo_id: int) -> dict[str, int]:
    sessions = [r["session_id"] for r in conn.execute("SELECT session_id FROM session_repo WHERE repo_id = ?", (repo_id,))]
    times = {s: _turn_times(conn, s) for s in sessions}
    mine = mine_emails(conn, repo_id)
    commits = conn.execute("SELECT sha, author_email, authored_at, agent_coauthored, parents FROM commits WHERE repo_id = ? ORDER BY authored_at",
                           (repo_id,)).fetchall()
    best: dict[str, tuple[int, int, float, str, str]] = {}  # sha -> (rank, -shared files, gap, session, evidence)

    def offer(sha: str, sid: str, evidence: str, gap: float, shared: int = 0) -> None:
        cand = (_RANK[evidence], -shared, gap, sid, evidence)
        if sha not in best or cand[:3] < best[sha][:3]:
            best[sha] = cand

    for sid in sessions:
        for claim in conn.execute("SELECT short_sha FROM commit_claims WHERE session_id = ?", (sid,)):
            for c in conn.execute("SELECT sha FROM commits WHERE repo_id = ? AND sha LIKE ?",
                                  (repo_id, claim["short_sha"] + "%")):
                offer(c["sha"], sid, "exact", 0.0)
    edits = _edits_by_file(conn, repo_id, sessions)
    # Sessions read for edits that used no edit tool at all: they asked, read or reviewed.
    # The user committing something within 30 minutes of one is a coincidence, not its work.
    read_only = {r["session_id"] for r in conn.execute(
        """SELECT session_id FROM edits_seen e
           WHERE NOT EXISTS (SELECT 1 FROM session_files f WHERE f.session_id = e.session_id)""")} & set(sessions)
    # Commits no ref reaches any more (as of the last scan): a stash entry an older scan stored
    # has two parents like a merge, and must not pass for one.
    gone = {r["sha"] for r in conn.execute("SELECT sha FROM commit_reach WHERE repo_id = ? AND reachable = 0", (repo_id,))}
    changed: dict[str, list[str]] = {}
    if edits:
        for f in conn.execute("SELECT sha, path FROM commit_files WHERE repo_id = ?", (repo_id,)):
            changed.setdefault(f["sha"], []).append(normalize_project_path(f["path"]))
    last_commit_of: dict[str, float] = {}   # path -> when a commit (anyone's) last changed it
    for c in commits:   # oldest first, so last_commit_of is always "before this commit"
        t = _ms(c["authored_at"])
        paths = changed.get(c["sha"], ())
        # Who wrote a commit and whether an agent helped are separate facts: only a commit I
        # authored can be tied to my session by its files or by the clock.
        if c["author_email"].lower() in mine:
            shared: dict[str, int] = {}
            nearest: dict[str, float] = {}
            for p in paths:
                for sid, first, last in edits.get(p, ()):
                    edited = min(last, t)   # the session's latest edit this commit could hold
                    # The edit must come before the commit, not long before, and no earlier
                    # commit may already have taken it.
                    if first <= t and t - edited <= FILES_WINDOW_MS and edited > last_commit_of.get(p, float("-inf")):
                        shared[sid] = shared.get(sid, 0) + 1
                        nearest[sid] = min(nearest.get(sid, float("inf")), t - edited)
            for sid, n in shared.items():
                offer(c["sha"], sid, "files", nearest[sid], n)
            for sid in sessions:
                gap = _gap_after_turn(times[sid], t)
                if gap is None:
                    continue
                if c["agent_coauthored"]:   # the commit itself says an agent made it
                    offer(c["sha"], sid, "coauthored", gap)
                elif sid not in read_only or (c["parents"] > 1 and c["sha"] not in gone):   # a merge needs no edit
                    offer(c["sha"], sid, "inferred", gap)
        for p in paths:
            last_commit_of[p] = t
    conn.execute("BEGIN")
    try:
        conn.execute("DELETE FROM session_commits WHERE repo_id = ?", (repo_id,))
        conn.executemany("INSERT INTO session_commits VALUES (?, ?, ?, ?)",
                         [(sid, repo_id, sha, ev) for sha, (*_, sid, ev) in best.items()])
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    counts = {"exact": 0, "files": 0, "coauthored": 0, "inferred": 0}
    for *_, ev in best.values():
        counts[ev] += 1
    return counts
