"""Group a repo's sessions into pieces of work ("stories")."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class SessionSummary:
    session_id: str
    title: str | None
    branch: str | None
    first_ts: str
    last_ts: str
    active_ms: int
    cost: float
    turns: int
    skills: list[str] = field(default_factory=list)
    prs: list[int] = field(default_factory=list)
    commits: list[dict] = field(default_factory=list)
    active_estimated: bool = False
    output_tokens: int = 0
    whole: dict | None = None   # the whole session's totals when the range cuts it
    model: str | None = None
    repo_id: int | None = None  # the folder the session ran in
    # Project files the session edited, as paths inside the repo. Empty = it edited none;
    # None = no record (its transcript was gone before the file-edit record existed).
    files: frozenset[str] | None = None


def _t(ts: str) -> float:
    return datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()


def _whole(group: list[SessionSummary]) -> dict | None:
    """Totals of the story's sessions end to end, when the range shows only part of one."""
    if not any(s.whole for s in group):
        return None
    parts = [s.whole or {"first_ts": s.first_ts, "last_ts": s.last_ts, "output_tokens": s.output_tokens, "cost": s.cost}
             for s in group]
    return {"first_ts": min(p["first_ts"] for p in parts), "last_ts": max(p["last_ts"] for p in parts),
            "output_tokens": sum(p["output_tokens"] for p in parts), "cost": sum(p["cost"] for p in parts)}


def _files_edited(group: list[SessionSummary]) -> int | None:
    """How many project files the thread edited; None when that can't be said (a session
    without a record might have edited some)."""
    edited = len(frozenset().union(*(s.files for s in group if s.files is not None)))
    return None if not edited and any(s.files is None for s in group) else edited


def _story(group: list[SessionSummary]) -> dict:
    lead = max(group, key=lambda s: s.active_ms)
    commits = [c for s in group for c in s.commits]
    ev = {"exact": 0, "files": 0, "coauthored": 0, "inferred": 0}
    for c in commits:
        ev[c["evidence"]] += 1
    return {
        "key": group[0].session_id,   # stable while grouping rules hold: the thread's first session
        "repo_id": max(group, key=lambda s: s.last_ts).repo_id,
        "title": lead.title or lead.branch or "Untitled session",
        "branch": lead.branch,
        "prs": sorted({p for s in group for p in s.prs}),
        "sessions": len(group),
        "session_ids": [s.session_id for s in group],
        "active_ms": sum(s.active_ms for s in group),
        "active_estimated": any(s.active_estimated for s in group),
        "cost": sum(s.cost for s in group),
        "output_tokens": sum(s.output_tokens for s in group),
        "whole": _whole(group),
        # details on demand: an expanded piece lists its sessions and commits
        "session_list": [{"session_id": s.session_id, "title": s.title, "model": s.model, "turns": s.turns,
                          "first_ts": s.first_ts, "last_ts": s.last_ts, "active_ms": s.active_ms,
                          "active_estimated": s.active_estimated} for s in group],
        "commit_list": [{"sha": c["sha"], "subject": c.get("subject", ""), "evidence": c["evidence"],
                         "on_default": c.get("on_default"), "pushed": c.get("pushed"),
                         "reachable": c.get("reachable"), "landed": c.get("landed")} for c in commits],
        "first_ts": group[0].first_ts,
        "last_ts": max(s.last_ts for s in group),
        "commits": {"total": len(commits), **ev},
        "added": sum(c["added"] for c in commits),
        "deleted": sum(c["deleted"] for c in commits),
        "files": sum(c["files"] for c in commits),
        "skills": sorted({k for s in group for k in s.skills}),
        "files_edited": _files_edited(group),
    }


# Branches many unrelated pieces of work pass through: being on one says nothing about a session.
LONG_LIVED = frozenset({"main", "master", "trunk", "develop", "development", "dev", "HEAD"})
# A session joins a thread when the files they share are at least this part of the smaller side:
# one file everything touches (a changelog, a lockfile) among many is not a shared piece of work.
SHARE = 0.25


def _pull(s: SessionSummary, group: list[SessionSummary], long_lived: frozenset[str]) -> float:
    """How strongly `s` belongs with `group`; 0 = not at all. Shared files are the evidence;
    a feature branch holds its sessions together; without a file record only the branch is left."""
    same_branch = any(p.branch == s.branch for p in group)
    if s.files is None or any(p.files is None for p in group):
        return 0.5 if same_branch else 0.0
    theirs = frozenset().union(*(p.files for p in group))
    shared = len(s.files & theirs)
    if shared and shared / min(len(s.files), len(theirs)) >= SHARE:
        return float(shared)
    return 0.5 if same_branch and s.branch and s.branch not in long_lived else 0.0


def group_stories(sessions: list[SessionSummary], gap_days: float = 2,
                  long_lived: frozenset[str] | set[str] = frozenset()) -> list[dict]:
    """Threads, newest first. A session joins the thread, active within `gap_days` before it,
    that it belongs with most (see `_pull`); the more recent one on a tie. `long_lived` adds
    the project's own default branches to the usual names."""
    shared_branches = LONG_LIVED | frozenset(long_lived)
    groups: list[list[SessionSummary]] = []
    for s in sorted(sessions, key=lambda x: _t(x.first_ts)):
        best, best_key = None, (0.0, 0.0)
        for g in groups:
            last = max(_t(p.last_ts) for p in g)
            if _t(s.first_ts) - last > gap_days * 86_400:
                continue
            key = (_pull(s, g, shared_branches), last)
            if key[0] > 0 and key > best_key:
                best, best_key = g, key
        if best is None:
            groups.append([s])
        else:
            best.append(s)
    return [_story(g) for g in reversed(groups)]
