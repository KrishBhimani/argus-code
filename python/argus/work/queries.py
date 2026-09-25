"""Read-only queries for /api/work/*. argus.db is reached only as the attached `core` schema."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

from .db import get_meta
from .linker import mine_emails
from .stories import SessionSummary, group_stories

_ROOT = "CASE WHEN instr(t.session_id, '/') > 0 THEN substr(t.session_id, 1, instr(t.session_id, '/') - 1) ELSE t.session_id END"


def _dt(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _local_day(ts: str, tz: int) -> str:
    return (_dt(ts) + timedelta(minutes=tz)).date().isoformat()


def _days(frm: str, to: str, tz: int) -> list[str]:
    a, b = _dt(_local_day(frm, tz) + "T00:00:00+00:00"), _dt(_local_day(to, tz) + "T00:00:00+00:00")
    out = []
    while a <= b:
        out.append(a.date().isoformat())
        a += timedelta(days=1)
    return out


def _sessions(conn: sqlite3.Connection, repo_id: int) -> list[str]:
    return [r["session_id"] for r in conn.execute("SELECT session_id FROM session_repo WHERE repo_id = ?", (repo_id,))]


def _in(ids: list[str]) -> str:
    return ",".join("?" * len(ids)) or "NULL"


def _active_rows(conn, ids: list[str], frm: str, to: str) -> list[sqlite3.Row]:
    """Active spans in range; a session's estimated rows are used only if it has no measured ones."""
    if not ids:
        return []
    return conn.execute(
        f"""SELECT a.session_id, a.ts, a.ms, a.kind FROM active_spans a
            WHERE a.session_id IN ({_in(ids)}) AND a.ts >= ? AND a.ts < ?
              AND (a.kind = 'measured' OR NOT EXISTS (
                   SELECT 1 FROM active_spans m WHERE m.session_id = a.session_id AND m.kind = 'measured'))""",
        [*ids, frm, to]).fetchall()


def _turn_rows(conn, ids: list[str], frm: str, to: str) -> list[sqlite3.Row]:
    if not ids:
        return []
    return conn.execute(
        f"""SELECT {_ROOT} AS sid, t.id, t.timestamp, t.cost_usd FROM core.turns t
            WHERE {_ROOT} IN ({_in(ids)}) AND t.timestamp >= ? AND t.timestamp < ?""",
        [*ids, frm, to]).fetchall()


def _commits(conn, repo_id: int, frm: str, to: str, scope: str) -> list[sqlite3.Row]:
    rows = conn.execute(
        """SELECT c.*, sc.session_id, sc.evidence FROM commits c
           LEFT JOIN session_commits sc ON sc.repo_id = c.repo_id AND sc.sha = c.sha
           WHERE c.repo_id = ? AND c.authored_at >= ? AND c.authored_at < ? ORDER BY c.authored_at""",
        (repo_id, frm, to)).fetchall()
    if scope == "all":
        return rows
    mine = mine_emails(conn, repo_id)
    return [r for r in rows if r["author_email"] in mine or r["agent_coauthored"] or r["evidence"] == "exact"]


def _tiles(conn, repo_id, ids, frm, to, scope) -> dict:
    spans = _active_rows(conn, ids, frm, to)
    active = sum(r["ms"] for r in spans)
    cost = sum(r["cost_usd"] for r in _turn_rows(conn, ids, frm, to))
    commits = len(_commits(conn, repo_id, frm, to, scope))
    return {"active_ms": active, "active_estimated": any(r["kind"] == "estimated" for r in spans),
            "cost": cost, "commits": commits,
            "cost_per_commit": (cost / commits) if commits else None}


def projects(conn: sqlite3.Connection, now_iso: str, tz: int = 0) -> list[dict]:
    to = now_iso
    frm = _iso(_dt(now_iso) - timedelta(days=30))
    days = _days(_iso(_dt(now_iso) - timedelta(days=29)), now_iso, tz)
    out = []
    for r in conn.execute("SELECT * FROM repos ORDER BY display_name").fetchall():
        ids = _sessions(conn, r["id"])
        spans = _active_rows(conn, ids, frm, to)
        daily = dict.fromkeys(days, 0)
        for s in spans:
            d = _local_day(s["ts"], tz)
            if d in daily:
                daily[d] += s["ms"]
        last_turn = conn.execute(f"SELECT MAX(t.timestamp) AS m FROM core.turns t WHERE {_ROOT} IN ({_in(ids)})", ids).fetchone()["m"] if ids else None
        mine_commits = _commits(conn, r["id"], "0000", to, "mine")
        last_commit = mine_commits[-1]["authored_at"] if mine_commits else None
        out.append({
            "id": r["id"], "display_name": r["display_name"], "root": r["root"], "present": bool(r["present"]),
            "last_error": r["last_error"],
            "active_ms_30d": sum(s["ms"] for s in spans),
            "active_estimated": any(s["kind"] == "estimated" for s in spans),
            "commits_30d": len(_commits(conn, r["id"], frm, to, "mine")),
            "daily_active_ms": list(daily.values()),
            "last_worked_at": max(filter(None, [last_turn, last_commit]), default=None),
        })
    return sorted(out, key=lambda p: p["last_worked_at"] or "", reverse=True)


def _summaries(conn, repo_id, ids, frm, to, scope) -> list[SessionSummary]:
    turns = _turn_rows(conn, ids, frm, to)
    by_sid: dict[str, list[sqlite3.Row]] = {}
    for t in turns:
        by_sid.setdefault(t["sid"], []).append(t)
    active: dict[str, int] = {}
    estimated: set[str] = set()
    for a in _active_rows(conn, list(by_sid), frm, to):
        active[a["session_id"]] = active.get(a["session_id"], 0) + a["ms"]
        if a["kind"] == "estimated":
            estimated.add(a["session_id"])
    commits_by_sid: dict[str, list[dict]] = {}
    for c in _commits(conn, repo_id, frm, to, scope):
        if c["session_id"]:
            commits_by_sid.setdefault(c["session_id"], []).append(
                {"sha": c["sha"], "evidence": c["evidence"], "added": c["added"], "deleted": c["deleted"], "files": c["files"]})
    out = []
    for sid, ts in by_sid.items():
        facts = conn.execute("SELECT title FROM session_facts WHERE session_id = ?", (sid,)).fetchone()
        branch = conn.execute("SELECT git_branch FROM session_repo WHERE session_id = ?", (sid,)).fetchone()["git_branch"]
        skills = [r["skill"] for r in conn.execute(
            f"SELECT DISTINCT skill FROM turn_attribution WHERE skill IS NOT NULL AND turn_id IN ({_in([t['id'] for t in ts])})",
            [t["id"] for t in ts])]
        prs = [r["pr_number"] for r in conn.execute("SELECT pr_number FROM session_prs WHERE session_id = ? AND pr_number IS NOT NULL", (sid,))]
        stamps = sorted(t["timestamp"] for t in ts)
        out.append(SessionSummary(sid, facts["title"] if facts else None, branch, stamps[0], stamps[-1],
                                  active.get(sid, 0), sum(t["cost_usd"] for t in ts), len(ts), skills, prs,
                                  commits_by_sid.get(sid, []), sid in estimated))
    return out


def overview(conn: sqlite3.Connection, repo_id: int, frm: str, to: str, scope: str = "mine", tz: int = 0) -> dict:
    ids = _sessions(conn, repo_id)
    span = _dt(to) - _dt(frm)
    prior = _tiles(conn, repo_id, ids, _iso(_dt(frm) - span), frm, scope)
    tiles = _tiles(conn, repo_id, ids, frm, to, scope)
    days = _days(frm, to, tz)
    active_d, commits_d = dict.fromkeys(days, 0), dict.fromkeys(days, 0)
    for a in _active_rows(conn, ids, frm, to):
        d = _local_day(a["ts"], tz)
        if d in active_d:
            active_d[d] += a["ms"]
    commits = _commits(conn, repo_id, frm, to, scope)
    for c in commits:
        d = _local_day(c["authored_at"], tz)
        if d in commits_d:
            commits_d[d] += 1
    summaries = _summaries(conn, repo_id, ids, frm, to, scope)
    gap = float(get_meta(conn, "story_gap_days") or 2)
    branch_cost: dict[str, float] = {}
    for s in summaries:
        branch_cost[s.branch or "(no branch)"] = branch_cost.get(s.branch or "(no branch)", 0) + s.cost
    turn_ids = [t["id"] for t in _turn_rows(conn, ids, frm, to)]
    turn_cost = {t["id"]: t["cost_usd"] for t in _turn_rows(conn, ids, frm, to)}
    skill_cost: dict[str, float] = {}
    for r in conn.execute(f"SELECT turn_id, skill, mcp_server FROM turn_attribution WHERE turn_id IN ({_in(turn_ids)})", turn_ids):
        name = r["skill"] or (f"{r['mcp_server']} (MCP)" if r["mcp_server"] else None)
        if name:
            skill_cost[name] = skill_cost.get(name, 0) + turn_cost.get(r["turn_id"], 0)
    total_cost = tiles["cost"] or 1
    files: dict[str, int] = {}
    shas = [c["sha"] for c in commits]
    for f in conn.execute(f"SELECT path FROM commit_files WHERE repo_id = ? AND sha IN ({_in(shas)})", [repo_id, *shas]):
        files[f["path"]] = files.get(f["path"], 0) + 1
    top = lambda d, n: [{"name": k, "value": v} for k, v in sorted(d.items(), key=lambda kv: -kv[1])[:n]]  # noqa: E731
    return {
        "tiles": tiles, "prior": prior,
        "daily": {"days": days, "active_ms": list(active_d.values()), "commits": list(commits_d.values())},
        "stories": group_stories(summaries, gap),
        "breakdowns": {
            "branches": top(branch_cost, 6),
            "skills": [{"name": k, "value": v / total_cost} for k, v in sorted(skill_cost.items(), key=lambda kv: -kv[1])[:6]],
            "files": top(files, 8),
        },
    }


def timeline(conn: sqlite3.Connection, repo_id: int, frm: str, to: str, kind: str = "all",
             branch: str | None = None, scope: str = "mine", tz: int = 0) -> dict:
    ids = _sessions(conn, repo_id)
    summaries = [s for s in _summaries(conn, repo_id, ids, frm, to, scope) if branch is None or s.branch == branch]
    items: list[dict] = []
    if kind in ("all", "sessions"):
        for s in summaries:
            core = conn.execute("SELECT primary_model FROM core.sessions WHERE id = ?", (s.session_id,)).fetchone()
            errors = conn.execute(
                "SELECT COALESCE(SUM(is_error), 0) AS e FROM core.tool_calls WHERE session_id = ? OR substr(session_id, 1, ?) = ?",
                (s.session_id, len(s.session_id) + 1, s.session_id + "/")).fetchone()["e"]
            commits = [{**c, **dict(conn.execute("SELECT subject, author_name FROM commits WHERE repo_id = ? AND sha = ?",
                                                    (repo_id, c["sha"])).fetchone())} for c in s.commits]
            items.append({"kind": "session", "session_id": s.session_id, "title": s.title, "branch": s.branch,
                          "first_ts": s.first_ts, "last_ts": s.last_ts, "active_ms": s.active_ms, "active_estimated": s.active_estimated, "cost": s.cost,
                          "turns": s.turns, "model": core["primary_model"] if core else None, "tool_errors": errors,
                          "commits": [] if kind == "sessions" else commits, "sort_ts": s.first_ts})
    if kind in ("all", "commits") and branch is None:
        linked = {c["sha"] for s in summaries for c in s.commits} if kind == "all" else set()
        for c in _commits(conn, repo_id, frm, to, scope):
            if c["sha"] not in linked:
                items.append({"kind": "commit", "sha": c["sha"], "subject": c["subject"], "author_name": c["author_name"],
                              "authored_at": c["authored_at"], "evidence": c["evidence"], "session_id": c["session_id"],
                              "added": c["added"], "deleted": c["deleted"], "sort_ts": c["authored_at"]})
    days: dict[str, list[dict]] = {}
    for it in sorted(items, key=lambda i: i["sort_ts"], reverse=True):
        days.setdefault(_local_day(it["sort_ts"], tz), []).append(it)
    return {"days": [{"day": d, "items": v} for d, v in days.items()]}
