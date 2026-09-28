"""A thread's state from its story and its folder's git facts: first matching rule wins."""
from __future__ import annotations

import pytest

from argus.work.threads import OPEN_STATES, thread_state

NOW = "2026-09-29T12:00:00Z"
FOLDER = {"last_error": None, "default_ref": "refs/remotes/origin/main", "dirty": 0}


def story(commits=(), prs=(), last_ts="2026-09-29T10:00:00Z", cost=0.44, branch="feat/x"):
    return {"commit_list": [{"on_default": d, "pushed": p} for d, p in commits], "prs": list(prs),
            "last_ts": last_ts, "cost": cost, "branch": branch}


CASES = [
    # name, story, folder overrides, newest_in_folder, expected state, expected reason
    ("git error", story(), {"last_error": "boom"}, True, "unknown", "git scan failed: boom"),
    ("no main branch", story(), {"default_ref": None}, True, "unknown", "no main branch found"),
    ("not scanned yet", story([(None, None)]), {}, True, "unknown", "git state not read yet"),
    ("shipped", story([(1, 1), (1, 1)]), {}, True, "shipped", "2 commits on main"),
    ("work on main, local repo", story([(1, None)], branch="main"), {"default_ref": "refs/heads/main"}, True,
     "shipped", "1 commit on main"),
    ("not pushed beats PR", story([(0, 0), (0, 1)], prs=[212]), {}, True, "not_pushed", "1 commit not pushed"),
    ("no remote is not unpushed", story([(0, None)]), {"default_ref": "refs/heads/main"}, True,
     "in_progress", "1 commit on feat/x"),
    ("pr open", story([(0, 1)], prs=[3, 212]), {}, True, "pr_open", "PR #212 open"),
    ("uncommitted", story(), {"dirty": 12}, True, "uncommitted", "folder has 12 changed files"),
    ("older thread can't claim the folder", story(), {"dirty": 12}, False, "dropped", "no commit · $0.44 spent"),
    ("clean folder, no commit", story(), {}, True, "dropped", "no commit · $0.44 spent"),
    ("dirty but a week quiet", story(last_ts="2026-09-21T12:00:00Z"), {"dirty": 1}, True,
     "dropped", "no commit · $0.44 spent"),
    ("stalled at 3 days", story([(0, 1)], last_ts="2026-09-26T12:00:00Z"), {}, True, "stalled", "quiet for 3 days"),
    ("stalled at 7 days", story([(0, 1)], last_ts="2026-09-22T12:00:00Z"), {}, True, "stalled", "quiet for 7 days"),
    ("dropped past 7 days", story([(0, 1)], last_ts="2026-09-21T11:00:00Z"), {}, True,
     "dropped", "quiet for 8 days, not on main"),
    ("in progress", story([(0, 1), (0, 1)]), {}, True, "in_progress", "2 commits on feat/x"),
    # Squash merges put a new commit on main: the thread's own commits never reach it.
    ("squash-merged PR", story([(0, 0), (0, 0)], prs=[3, 191]), {"landed_prs": {191}}, True,
     "shipped", "PR #191 merged into main"),
    ("squash-merged PR, branch deleted", story([(0, 0)], prs=[191], last_ts="2026-09-01T10:00:00Z"),
     {"landed_prs": {191}}, True, "shipped", "PR #191 merged into main"),
    ("another PR landed, not this one", story([(0, 1)], prs=[212]), {"landed_prs": {191}}, True, "pr_open", "PR #212 open"),
]


@pytest.mark.parametrize("name,s,over,newest,state,reason", CASES, ids=[c[0] for c in CASES])
def test_thread_state(name, s, over, newest, state, reason):
    assert thread_state(s, {**FOLDER, **over}, NOW, newest_in_folder=newest) == (state, reason)


def test_open_states():
    assert OPEN_STATES == {"uncommitted", "not_pushed", "pr_open", "stalled", "in_progress"}
