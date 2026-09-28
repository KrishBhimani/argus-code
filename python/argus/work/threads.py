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
    # An amended or rebased-away commit is neither work nor unpushed: its rewrite carries the thread.
    commits = [c for c in story["commit_list"] if c.get("reachable") != 0]
    if any(c.get("on_default") is None for c in commits):
        return "unknown", "git state not read yet"
    base = folder["default_ref"].rsplit("/", 1)[-1]
    idle = (_dt(now_iso) - _dt(story["last_ts"])).total_seconds() / 86_400
    if commits and all(c["on_default"] or c.get("landed") for c in commits):
        return "shipped", f"{_n(len(commits), 'commit')} on {base}"
    gh = folder.get("pr_states") or {}
    merged = sorted(p for p in story["prs"] if gh.get(p) == "MERGED")
    if merged:
        return "shipped", f"PR #{merged[-1]} merged"
    # A squash merge lands a new commit on main, so the thread's own commits never reach it
    # (and its branch is often deleted): the PR's number on main is the evidence.
    landed = sorted(set(story["prs"]) & set(folder.get("landed_prs") or ()))
    if landed:
        return "shipped", f"PR #{landed[-1]} merged into {base}"
    # A release PR (development -> main) completed as a fast-forward leaves no merge commit and
    # the session none of its own: its branch being in main is the evidence.
    branch = story.get("branch")
    if not commits and story["prs"] and branch and branch != base and branch in (folder.get("merged_branches") or ()):
        return "shipped", f"PR #{max(story['prs'])} merged ({branch} is in {base})"
    unpushed = sum(1 for c in commits if c.get("pushed") == 0)   # None = no remote: unknowable, not unpushed
    if unpushed:
        return "not_pushed", f"{_n(unpushed, 'commit')} not pushed"
    if story["prs"] and all(gh.get(p) == "CLOSED" for p in story["prs"]):
        return "dropped", f"PR #{max(story['prs'])} closed without merging"
    if story["prs"]:
        open_prs = [p for p in story["prs"] if gh.get(p) != "CLOSED"]
        # Say it is a guess only when it is one: no GitHub answer for this PR and checking is off.
        answered = gh.get(max(open_prs)) is not None
        hint = "" if folder.get("github_on") or answered else " · from git; turn on GitHub check for exact status"
        return "pr_open", f"PR #{max(open_prs)} open{hint}"
    if not commits:
        if newest_in_folder and (folder.get("dirty") or 0) > 0 and idle <= DROP_DAYS:
            return "uncommitted", f"folder has {_n(folder['dirty'], 'changed file')}"
        return "dropped", f"no commit · ${story['cost']:.2f} spent"
    if idle > DROP_DAYS:
        return "dropped", f"quiet for {int(idle)} days, not on {base}"
    if idle >= STALL_DAYS:
        return "stalled", f"quiet for {int(idle)} days"
    return "in_progress", f"{_n(len(commits), 'commit')} on {story.get('branch') or 'a branch'}"
