"""Link sessions to commits with labelled evidence (exact > coauthored > inferred)."""
from __future__ import annotations

import bisect
import json
import sqlite3
from datetime import datetime

from .db import get_meta

WINDOW_MS = 30 * 60 * 1000
_RANK = {"exact": 0, "coauthored": 1, "inferred": 2}


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


def link_repo(conn: sqlite3.Connection, repo_id: int) -> dict[str, int]:
    sessions = [r["session_id"] for r in conn.execute("SELECT session_id FROM session_repo WHERE repo_id = ?", (repo_id,))]
    times = {s: _turn_times(conn, s) for s in sessions}
    mine = mine_emails(conn, repo_id)
    commits = conn.execute("SELECT sha, author_email, authored_at, agent_coauthored FROM commits WHERE repo_id = ?",
                           (repo_id,)).fetchall()
    best: dict[str, tuple[int, float, str, str]] = {}  # sha -> (rank, gap, session, evidence)

    def offer(sha: str, sid: str, evidence: str, gap: float) -> None:
        cand = (_RANK[evidence], gap, sid, evidence)
        if sha not in best or cand[:2] < best[sha][:2]:
            best[sha] = cand

    for sid in sessions:
        for claim in conn.execute("SELECT short_sha FROM commit_claims WHERE session_id = ?", (sid,)):
            for c in conn.execute("SELECT sha FROM commits WHERE repo_id = ? AND sha LIKE ?",
                                  (repo_id, claim["short_sha"] + "%")):
                offer(c["sha"], sid, "exact", 0.0)
    for c in commits:
        t = _ms(c["authored_at"])
        is_mine = c["author_email"].lower() in mine
        for sid in sessions:
            gap = _gap_after_turn(times[sid], t)
            if gap is None:
                continue
            if c["agent_coauthored"]:
                offer(c["sha"], sid, "coauthored", gap)
            elif is_mine:
                offer(c["sha"], sid, "inferred", gap)
    conn.execute("BEGIN")
    try:
        conn.execute("DELETE FROM session_commits WHERE repo_id = ?", (repo_id,))
        conn.executemany("INSERT INTO session_commits VALUES (?, ?, ?, ?)",
                         [(sid, repo_id, sha, ev) for sha, (_, _, sid, ev) in best.items()])
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    counts = {"exact": 0, "coauthored": 0, "inferred": 0}
    for _, _, _, ev in best.values():
        counts[ev] += 1
    return counts
