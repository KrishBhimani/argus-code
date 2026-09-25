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


def _story(group: list[SessionSummary]) -> dict:
    lead = max(group, key=lambda s: s.active_ms)
    commits = [c for s in group for c in s.commits]
    ev = {"exact": 0, "coauthored": 0, "inferred": 0}
    for c in commits:
        ev[c["evidence"]] += 1
    return {
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
        "commit_list": [{"sha": c["sha"], "subject": c.get("subject", ""), "evidence": c["evidence"]} for c in commits],
        "first_ts": group[0].first_ts,
        "last_ts": max(s.last_ts for s in group),
        "commits": {"total": len(commits), **ev},
        "added": sum(c["added"] for c in commits),
        "deleted": sum(c["deleted"] for c in commits),
        "files": sum(c["files"] for c in commits),
        "skills": sorted({k for s in group for k in s.skills}),
    }


def group_stories(sessions: list[SessionSummary], gap_days: float = 2) -> list[dict]:
    groups: list[list[SessionSummary]] = []
    for s in sorted(sessions, key=lambda x: _t(x.first_ts)):
        prev = groups[-1] if groups else None
        if (prev and prev[-1].branch == s.branch
                and _t(s.first_ts) - max(_t(p.last_ts) for p in prev) <= gap_days * 86_400):
            prev.append(s)
        else:
            groups.append([s])
    return [_story(g) for g in reversed(groups)]
