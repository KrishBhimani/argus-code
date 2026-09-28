"""A thread's state, from its story and its folder's git facts. Pure: no git, no SQL.

The rules are ordered: the first match wins. `unknown` comes first so a folder git can't
read never shows a confident wrong state."""
from __future__ import annotations

from datetime import datetime

STALL_DAYS = 3
DROP_DAYS = 7
OPEN_STATES = frozenset({"uncommitted", "not_pushed", "pr_open", "stalled", "in_progress"})


def _dt(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def _n(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def thread_state(story: dict, folder: dict, now_iso: str, *, newest_in_folder: bool) -> tuple[str, str]:
    """(state, reason in plain words). Only the newest thread in a folder may claim the
    folder's uncommitted changes; an older no-commit thread there is dropped."""
    if folder.get("last_error"):
        return "unknown", f"git scan failed: {folder['last_error']}"
    if not folder.get("default_ref"):
        return "unknown", "no main branch found"
    commits = story["commit_list"]
    if any(c.get("on_default") is None for c in commits):
        return "unknown", "git state not read yet"
    base = folder["default_ref"].rsplit("/", 1)[-1]
    idle = (_dt(now_iso) - _dt(story["last_ts"])).total_seconds() / 86_400
    if commits and all(c["on_default"] for c in commits):
        return "shipped", f"{_n(len(commits), 'commit')} on {base}"
    # A squash merge lands a new commit on main, so the thread's own commits never reach it
    # (and its branch is often deleted): the PR's number on main is the evidence.
    landed = sorted(set(story["prs"]) & set(folder.get("landed_prs") or ()))
    if landed:
        return "shipped", f"PR #{landed[-1]} merged into {base}"
    unpushed = sum(1 for c in commits if c.get("pushed") == 0)   # None = no remote: unknowable, not unpushed
    if unpushed:
        return "not_pushed", f"{_n(unpushed, 'commit')} not pushed"
    if story["prs"]:
        return "pr_open", f"PR #{max(story['prs'])} open"
    if not commits:
        if newest_in_folder and (folder.get("dirty") or 0) > 0 and idle <= DROP_DAYS:
            return "uncommitted", f"folder has {_n(folder['dirty'], 'changed file')}"
        return "dropped", f"no commit · ${story['cost']:.2f} spent"
    if idle > DROP_DAYS:
        return "dropped", f"quiet for {int(idle)} days, not on {base}"
    if idle >= STALL_DAYS:
        return "stalled", f"quiet for {int(idle)} days"
    return "in_progress", f"{_n(len(commits), 'commit')} on {story.get('branch') or 'a branch'}"
