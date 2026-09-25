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


def _in(ids: list) -> str:
    return ",".join("?" * len(ids)) or "NULL"


def project_ids(conn: sqlite3.Connection, repo_id: int) -> list[int]:
    """Every folder of the project `repo_id` belongs to (clones and worktrees share a project_key)."""
    row = conn.execute("SELECT project_key FROM repos WHERE id = ?", (repo_id,)).fetchone()
    if row is None or row["project_key"] is None:
        return [repo_id]
    return [r["id"] for r in conn.execute("SELECT id FROM repos WHERE project_key = ? ORDER BY id", (row["project_key"],))]


def first_activity(conn: sqlite3.Connection, repo_id: int) -> str | None:
    """Earliest commit or active span of a project: where an all-time range starts."""
    rids = project_ids(conn, repo_id)
    r = conn.execute(
        f"""SELECT MIN(ts) FROM (
             SELECT MIN(authored_at) AS ts FROM commits WHERE repo_id IN ({_in(rids)})
             UNION ALL
             SELECT MIN(a.ts) FROM active_spans a JOIN session_repo s ON s.session_id = a.session_id
             WHERE s.repo_id IN ({_in(rids)}))""",
        [*rids, *rids]).fetchone()
    return r[0]


def _sessions(conn: sqlite3.Connection, rids: list[int]) -> list[str]:
    return [r["session_id"] for r in conn.execute(
        f"SELECT session_id FROM session_repo WHERE repo_id IN ({_in(rids)}) ORDER BY session_id", rids)]


_KIND_RANK = "CASE kind WHEN 'measured' THEN 0 WHEN 'estimated' THEN 1 ELSE 2 END"


def _active_rows(conn, ids: list[str], frm: str, to: str) -> list[sqlite3.Row]:
    """Active spans in range, one source per session: measured (transcript turn durations),
    else estimated from transcript gaps, else estimated from the archive's turn times."""
    if not ids:
        return []
    return conn.execute(
        f"""WITH best AS (
              SELECT session_id, MIN({_KIND_RANK}) AS r FROM active_spans
              WHERE session_id IN ({_in(ids)}) GROUP BY session_id)
            SELECT a.session_id, a.ts, a.ms, a.kind FROM active_spans a JOIN best b ON b.session_id = a.session_id
            WHERE a.ts >= ? AND a.ts < ? AND {_KIND_RANK.replace('kind', 'a.kind')} = b.r""",
        [*ids, frm, to]).fetchall()


def _estimated(kind: str) -> bool:
    return kind != "measured"


def _turn_rows(conn, ids: list[str], frm: str, to: str) -> list[sqlite3.Row]:
    if not ids:
        return []
    return conn.execute(
        f"""SELECT {_ROOT} AS sid, t.id, t.timestamp, t.cost_usd, t.output_tokens FROM core.turns t
            WHERE {_ROOT} IN ({_in(ids)}) AND t.timestamp >= ? AND t.timestamp < ?""",
        [*ids, frm, to]).fetchall()


_EVIDENCE_RANK = {"exact": 0, "coauthored": 1, "inferred": 2, None: 3}


def _commits(conn, rids: list[int], frm: str, to: str, scope: str) -> list[sqlite3.Row]:
    """A project's commits in range, each sha once: folders of one repo hold the same commits,
    and the strongest link any folder found wins."""
    best: dict[str, sqlite3.Row] = {}
    for r in conn.execute(
        f"""SELECT c.*, sc.session_id, sc.evidence FROM commits c
            LEFT JOIN session_commits sc ON sc.repo_id = c.repo_id AND sc.sha = c.sha
            WHERE c.repo_id IN ({_in(rids)}) AND c.authored_at >= ? AND c.authored_at < ?
            ORDER BY c.repo_id""", [*rids, frm, to]):
        seen = best.get(r["sha"])
        if seen is None or _EVIDENCE_RANK[r["evidence"]] < _EVIDENCE_RANK[seen["evidence"]]:
            best[r["sha"]] = r
    rows = sorted(best.values(), key=lambda r: (r["authored_at"], r["sha"]))
    if scope == "all":
        return rows
    mine = set().union(*(mine_emails(conn, rid) for rid in rids))
    return [r for r in rows if r["author_email"] in mine or r["agent_coauthored"] or r["evidence"] == "exact"]


def _tiles(conn, rids, ids, frm, to, scope) -> dict:
    spans = _active_rows(conn, ids, frm, to)
    active = sum(r["ms"] for r in spans)
    cost = sum(r["cost_usd"] for r in _turn_rows(conn, ids, frm, to))
    commits = len(_commits(conn, rids, frm, to, scope))
    return {"active_ms": active, "active_estimated": any(_estimated(r["kind"]) for r in spans),
            "cost": cost, "commits": commits,
            "cost_per_commit": (cost / commits) if commits else None}


def _project_name(key: str, primary: sqlite3.Row, n_folders: int) -> str:
    """One folder keeps its folder name; several are named after the repo they share."""
    if n_folders > 1 and key.startswith("remote:"):
        return key.rsplit("/", 1)[-1]
    return primary["display_name"]


def projects(conn: sqlite3.Connection, now_iso: str, tz: int = 0) -> list[dict]:
    to = now_iso
    frm = _iso(_dt(now_iso) - timedelta(days=30))
    days = _days(_iso(_dt(now_iso) - timedelta(days=29)), now_iso, tz)
    groups: dict[str, list[sqlite3.Row]] = {}
    for r in conn.execute("SELECT * FROM repos ORDER BY id").fetchall():
        groups.setdefault(r["project_key"] or f"id:{r['id']}", []).append(r)
    out = []
    for key, folders in groups.items():
        rids = [f["id"] for f in folders]
        r = next((f for f in folders if f["present"]), folders[0])   # the folder shown as the project's root
        ids = _sessions(conn, rids)
        spans = _active_rows(conn, ids, frm, to)
        daily = dict.fromkeys(days, 0)
        for s in spans:
            d = _local_day(s["ts"], tz)
            if d in daily:
                daily[d] += s["ms"]
        last_turn = conn.execute(f"SELECT MAX(t.timestamp) AS m FROM core.turns t WHERE {_ROOT} IN ({_in(ids)})", ids).fetchone()["m"] if ids else None
        mine_commits = _commits(conn, rids, "0000", to, "mine")
        last_commit = mine_commits[-1]["authored_at"] if mine_commits else None
        out.append({
            "id": rids[0], "display_name": _project_name(key, r, len(folders)), "root": r["root"],
            "present": any(f["present"] for f in folders),
            "last_error": next((f["last_error"] for f in folders if f["last_error"]), None),
            "folder_ids": rids,
            "folders": [{"id": f["id"], "root": f["root"], "present": bool(f["present"])} for f in folders],
            "active_ms_30d": sum(s["ms"] for s in spans),
            "active_estimated": any(_estimated(s["kind"]) for s in spans),
            "commits_30d": len(_commits(conn, rids, frm, to, "mine")),
            "daily_active_ms": list(daily.values()),
            "last_worked_at": max(filter(None, [last_turn, last_commit]), default=None),
        })
    return sorted(out, key=lambda p: p["last_worked_at"] or "", reverse=True)


def _summaries(conn, rids, ids, frm, to, scope) -> list[SessionSummary]:
    turns = _turn_rows(conn, ids, frm, to)
    by_sid: dict[str, list[sqlite3.Row]] = {}
    for t in turns:
        by_sid.setdefault(t["sid"], []).append(t)
    active: dict[str, int] = {}
    estimated: set[str] = set()
    for a in _active_rows(conn, list(by_sid), frm, to):
        active[a["session_id"]] = active.get(a["session_id"], 0) + a["ms"]
        if _estimated(a["kind"]):
            estimated.add(a["session_id"])
    commits_by_sid: dict[str, list[dict]] = {}
    for c in _commits(conn, rids, frm, to, scope):
        if c["session_id"]:
            commits_by_sid.setdefault(c["session_id"], []).append(
                {"sha": c["sha"], "evidence": c["evidence"], "added": c["added"], "deleted": c["deleted"], "files": c["files"]})
    out = []
    for sid, ts in by_sid.items():
        facts = conn.execute("SELECT title FROM session_facts WHERE session_id = ?", (sid,)).fetchone()
        branch = conn.execute("SELECT git_branch FROM session_repo WHERE session_id = ?", (sid,)).fetchone()["git_branch"]
        skills = [r["skill"] for r in conn.execute(
            f"""SELECT DISTINCT ta.skill FROM turn_attribution ta JOIN core.turns t ON t.id = ta.turn_id
                WHERE ta.skill IS NOT NULL AND {_ROOT} = ? AND t.timestamp >= ? AND t.timestamp < ?""",
            (sid, frm, to))]
        prs = [r["pr_number"] for r in conn.execute("SELECT pr_number FROM session_prs WHERE session_id = ? AND pr_number IS NOT NULL", (sid,))]
        stamps = sorted(t["timestamp"] for t in ts)
        out.append(SessionSummary(sid, facts["title"] if facts else None, branch, stamps[0], stamps[-1],
                                  active.get(sid, 0), sum(t["cost_usd"] for t in ts), len(ts), skills, prs,
                                  commits_by_sid.get(sid, []), sid in estimated,
                                  sum(t["output_tokens"] or 0 for t in ts)))
    return out


def overview(conn: sqlite3.Connection, repo_id: int, frm: str, to: str, scope: str = "mine", tz: int = 0) -> dict:
    rids = project_ids(conn, repo_id)
    ids = _sessions(conn, rids)
    span = _dt(to) - _dt(frm)
    prior = _tiles(conn, rids, ids, _iso(_dt(frm) - span), frm, scope)
    tiles = _tiles(conn, rids, ids, frm, to, scope)
    days = _days(frm, to, tz)
    active_d, commits_d = dict.fromkeys(days, 0), dict.fromkeys(days, 0)
    tokens_d, cost_d = dict.fromkeys(days, 0), dict.fromkeys(days, 0.0)
    for a in _active_rows(conn, ids, frm, to):
        d = _local_day(a["ts"], tz)
        if d in active_d:
            active_d[d] += a["ms"]
    # Tokens and cost come from the archive, so every session counts, transcript or not.
    for t in _turn_rows(conn, ids, frm, to):
        d = _local_day(t["timestamp"], tz)
        if d in tokens_d:
            tokens_d[d] += t["output_tokens"] or 0
            cost_d[d] += t["cost_usd"] or 0
    commits = _commits(conn, rids, frm, to, scope)
    for c in commits:
        d = _local_day(c["authored_at"], tz)
        if d in commits_d:
            commits_d[d] += 1
    summaries = _summaries(conn, rids, ids, frm, to, scope)
    gap = float(get_meta(conn, "story_gap_days") or 2)
    branch_cost: dict[str, float] = {}
    for s in summaries:
        branch_cost[s.branch or "(no branch)"] = branch_cost.get(s.branch or "(no branch)", 0) + s.cost
    # Joined in SQL: binding every turn id in range hit SQLite's variable limit on busy projects.
    skill_cost: dict[str, float] = {}
    for r in conn.execute(
        f"""SELECT ta.skill, ta.mcp_server, SUM(t.cost_usd) AS cost
            FROM turn_attribution ta JOIN core.turns t ON t.id = ta.turn_id
            WHERE {_ROOT} IN ({_in(ids)}) AND t.timestamp >= ? AND t.timestamp < ?
            GROUP BY ta.skill, ta.mcp_server""", [*ids, frm, to]):
        name = r["skill"] or (f"{r['mcp_server']} (MCP)" if r["mcp_server"] else None)
        if name:
            skill_cost[name] = skill_cost.get(name, 0) + (r["cost"] or 0)
    total_cost = tiles["cost"] or 1
    files: dict[str, int] = {}
    shas = [c["sha"] for c in commits]
    for f in conn.execute(f"""SELECT DISTINCT sha, path FROM commit_files
                               WHERE repo_id IN ({_in(rids)}) AND sha IN ({_in(shas)})""", [*rids, *shas]):
        files[f["path"]] = files.get(f["path"], 0) + 1
    top = lambda d, n: [{"name": k, "value": v} for k, v in sorted(d.items(), key=lambda kv: -kv[1])[:n]]  # noqa: E731
    return {
        "tiles": tiles, "prior": prior,
        "daily": {"days": days, "active_ms": list(active_d.values()), "commits": list(commits_d.values()),
                  "output_tokens": list(tokens_d.values()), "cost": list(cost_d.values())},
        "stories": group_stories(summaries, gap),
        "breakdowns": {
            "branches": top(branch_cost, 6),
            "skills": [{"name": k, "value": v / total_cost} for k, v in sorted(skill_cost.items(), key=lambda kv: -kv[1])[:6]],
            "files": top(files, 8),
        },
    }


def timeline(conn: sqlite3.Connection, repo_id: int, frm: str, to: str, kind: str = "all",
             branch: str | None = None, scope: str = "mine", tz: int = 0) -> dict:
    rids = project_ids(conn, repo_id)
    ids = _sessions(conn, rids)
    summaries = [s for s in _summaries(conn, rids, ids, frm, to, scope) if branch is None or s.branch == branch]
    items: list[dict] = []
    if kind in ("all", "sessions"):
        for s in summaries:
            core = conn.execute("SELECT primary_model FROM core.sessions WHERE id = ?", (s.session_id,)).fetchone()
            errors = conn.execute(
                "SELECT COALESCE(SUM(is_error), 0) AS e FROM core.tool_calls WHERE session_id = ? OR substr(session_id, 1, ?) = ?",
                (s.session_id, len(s.session_id) + 1, s.session_id + "/")).fetchone()["e"]
            commits = [{**c, **dict(conn.execute(
                f"SELECT subject, author_name FROM commits WHERE repo_id IN ({_in(rids)}) AND sha = ? LIMIT 1",
                [*rids, c["sha"]]).fetchone())} for c in s.commits]
            items.append({"kind": "session", "session_id": s.session_id, "title": s.title, "branch": s.branch,
                          "first_ts": s.first_ts, "last_ts": s.last_ts, "active_ms": s.active_ms, "active_estimated": s.active_estimated, "cost": s.cost,
                          "turns": s.turns, "model": core["primary_model"] if core else None, "tool_errors": errors,
                          "commits": [] if kind == "sessions" else commits, "sort_ts": s.first_ts})
    if kind in ("all", "commits") and branch is None:
        linked = {c["sha"] for s in summaries for c in s.commits} if kind == "all" else set()
        for c in _commits(conn, rids, frm, to, scope):
            if c["sha"] not in linked:
                items.append({"kind": "commit", "sha": c["sha"], "subject": c["subject"], "author_name": c["author_name"],
                              "authored_at": c["authored_at"], "evidence": c["evidence"], "session_id": c["session_id"],
                              "added": c["added"], "deleted": c["deleted"], "sort_ts": c["authored_at"]})
    days: dict[str, list[dict]] = {}
    for it in sorted(items, key=lambda i: i["sort_ts"], reverse=True):
        days.setdefault(_local_day(it["sort_ts"], tz), []).append(it)
    return {"days": [{"day": d, "items": v} for d, v in days.items()]}
