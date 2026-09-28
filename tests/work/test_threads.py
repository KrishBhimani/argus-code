"""A thread's state from its story and its folder's git facts: first matching rule wins."""
from __future__ import annotations

import pytest

from argus.work.threads import OPEN_STATES, thread_state

NOW = "2026-09-29T12:00:00Z"
FOLDER = {"last_error": None, "default_ref": "refs/remotes/origin/main", "dirty": 0}


def story(commits=(), prs=(), last_ts="2026-09-29T10:00:00Z", cost=0.44, branch="feat/x"):
    # each commit: (on_default, pushed[, reachable[, landed]])
    return {"commit_list": [{"on_default": c[0], "pushed": c[1], **({"reachable": c[2]} if len(c) > 2 else {}),
                             **({"landed": c[3]} if len(c) > 3 else {})} for c in commits], "prs": list(prs),
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
    ("pr open", story([(0, 1)], prs=[3, 212]), {}, True, "pr_open", "PR #212 open · from git; turn on GitHub check for exact status"),
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
    # An amended / rebased commit is left behind unreachable: it is neither work nor unpushed.
    ("rewritten commit is ignored", story([(0, 0, 0), (1, 1, 1)]), {}, True, "shipped", "1 commit on main"),
    ("only rewritten commits", story([(0, 0, 0)]), {}, True, "dropped", "no commit · $0.44 spent"),
    ("another PR landed, not this one", story([(0, 1)], prs=[212]), {"landed_prs": {191}}, True, "pr_open",
     "PR #212 open · from git; turn on GitHub check for exact status"),
    # A: a squash found by content; a release branch already in main.
    ("squash found by content", story([(0, 1, 1, 1), (0, 1, 1, 1)]), {}, True, "shipped", "2 commits on main"),
    ("release PR, branch in main", story(prs=[134], branch="development"), {"merged_branches": ["development"]}, True,
     "shipped", "PR #134 merged (development is in main)"),
    ("release rule never fires for the default branch", story(prs=[193], branch="main"),
     {"merged_branches": ["main"], "github_on": True}, True, "pr_open", "PR #193 open"),
    # B: what GitHub says.
    ("github says merged", story([(0, 1)], prs=[141]), {"pr_states": {141: "MERGED"}, "github_on": True}, True,
     "shipped", "PR #141 merged"),
    ("merged beats closed", story(prs=[5, 6]), {"pr_states": {5: "CLOSED", 6: "MERGED"}, "github_on": True}, True,
     "shipped", "PR #6 merged"),
    ("closed without merging", story([(0, 1)], prs=[9]), {"pr_states": {9: "CLOSED"}, "github_on": True}, True,
     "dropped", "PR #9 closed without merging"),
    ("one closed, one open", story([(0, 1)], prs=[9, 10]), {"pr_states": {9: "CLOSED", 10: "OPEN"}, "github_on": True},
     True, "pr_open", "PR #10 open"),
    ("github on: no hint", story([(0, 1)], prs=[3]), {"github_on": True}, True, "pr_open", "PR #3 open"),
]


@pytest.mark.parametrize("name,s,over,newest,state,reason", CASES, ids=[c[0] for c in CASES])
def test_thread_state(name, s, over, newest, state, reason):
    assert thread_state(s, {**FOLDER, **over}, NOW, newest_in_folder=newest) == (state, reason)


def test_open_states():
    assert OPEN_STATES == {"uncommitted", "not_pushed", "pr_open", "stalled", "in_progress"}
