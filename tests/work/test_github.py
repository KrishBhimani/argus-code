"""Opt-in GitHub PR status: one batched gh query per repo, final states cached, one shared clock."""
from __future__ import annotations

import json

import pytest

from argus.work import github
from argus.work.db import get_meta, open_work_db

NOW = "2026-09-29T12:00:00Z"


@pytest.fixture(autouse=True)
def _gh_on_path(monkeypatch, request):
    if request.node.name != "test_a_missing_gh_is_reported_not_raised":
        monkeypatch.setattr(github.shutil, "which", lambda _n: "gh")


@pytest.fixture
def conn(tmp_path):
    c = open_work_db(tmp_path)
    c.executemany("INSERT INTO session_prs VALUES (?, ?, ?, ?, NULL)", [
        ("s1", "https://github.com/quirq-ai/xo-space/pull/134", 134, "quirq-ai/xo-space"),
        ("s2", "https://github.com/quirq-ai/xo-space/pull/141", 141, "quirq-ai/xo-space"),
        ("s3", "https://github.com/me/tool/pull/7", 7, "me/tool"),
    ])
    return c


def fake_gh(answers: dict, calls: list):
    """answers: owner/repo -> {number: state or None}, or None for a repo gh can't read."""
    def run(args, timeout=20):
        q = args[-1].removeprefix("query=")
        calls.append(q)
        repo = next(r for r in answers if f'owner: "{r.split("/")[0]}", name: "{r.split("/")[1]}"' in q)
        if answers[repo] is None:
            raise github.GhError("Could not resolve to a Repository")
        data = {f"p{n}": ({"state": s, "mergedAt": "2026-09-20T00:00:00Z" if s == "MERGED" else None} if s else None)
                for n, s in answers[repo].items() if f"p{n}:" in q}
        return json.dumps({"data": {"repository": data}})
    return run


def test_one_batched_query_per_repo_and_final_states_cached(conn, monkeypatch):
    calls: list = []
    monkeypatch.setattr(github, "run_gh", fake_gh({"quirq-ai/xo-space": {134: "MERGED", 141: "OPEN"}, "me/tool": {7: "CLOSED"}}, calls))
    assert github.refresh(conn, NOW) == {"checked": 3, "error": None}
    assert len(calls) == 2 and "p134:" in calls[1] and "p141:" in calls[1]
    assert github.pr_states(conn, "quirq-ai/xo-space") == {134: "MERGED", 141: "OPEN"}
    calls.clear()
    github.refresh(conn, NOW)
    assert len(calls) == 1 and "p141:" in calls[0] and "p134:" not in calls[0]   # MERGED / CLOSED never asked again


def test_one_bad_repo_does_not_stop_the_others(conn, monkeypatch):
    monkeypatch.setattr(github, "run_gh", fake_gh({"quirq-ai/xo-space": None, "me/tool": {7: "MERGED"}}, []))
    r = github.refresh(conn, NOW)
    assert r["checked"] == 1 and "quirq-ai/xo-space" in r["error"]
    assert github.pr_states(conn, "me/tool") == {7: "MERGED"}
    assert "quirq-ai/xo-space" in github.status(conn)["last_error"]


def test_odd_repository_names_are_never_queried(conn, monkeypatch):
    conn.execute("""INSERT INTO session_prs VALUES ('s9', 'https://github.com/a/b/pull/1', 1, 'a") { x } #/b', NULL)""")
    calls: list = []
    monkeypatch.setattr(github, "run_gh", fake_gh({"quirq-ai/xo-space": {134: "OPEN", 141: "OPEN"}, "me/tool": {7: "OPEN"}}, calls))
    github.refresh(conn, NOW)
    assert all("x }" not in q for q in calls) and len(calls) == 2


def test_the_clock_is_shared_and_every_refresh_resets_it(conn, monkeypatch):
    calls: list = []
    monkeypatch.setattr(github, "run_gh", fake_gh({"quirq-ai/xo-space": {134: "OPEN", 141: "OPEN"}, "me/tool": {7: "OPEN"}}, calls))
    assert github.maybe_refresh(conn, NOW) is None and calls == []            # off: the scan never asks
    github.enable(conn, NOW)                                                   # on: asks at once
    assert len(calls) == 2 and get_meta(conn, "github_checked_at") == NOW
    assert github.maybe_refresh(conn, "2026-09-29T12:29:00Z") is None          # 29 min: not due
    github.refresh(conn, "2026-09-29T12:20:00Z")                               # a manual refresh resets the clock
    assert github.maybe_refresh(conn, "2026-09-29T12:45:00Z") is None          # 25 min after the manual one
    assert github.maybe_refresh(conn, "2026-09-29T12:50:00Z") is not None      # 30 min after it
    github.disable(conn)
    assert github.maybe_refresh(conn, "2026-09-29T20:00:00Z") is None and github.status(conn)["enabled"] is False


def test_refresh_works_while_off_and_does_not_turn_it_on(conn, monkeypatch):
    monkeypatch.setattr(github, "run_gh", fake_gh({"quirq-ai/xo-space": {134: "MERGED", 141: "OPEN"}, "me/tool": {7: "OPEN"}}, []))
    github.refresh(conn, NOW)
    assert github.status(conn)["enabled"] is False and github.pr_states(conn, "quirq-ai/xo-space")[134] == "MERGED"


def test_a_missing_gh_is_reported_not_raised(conn, monkeypatch):
    monkeypatch.setattr(github.shutil, "which", lambda _name: None)
    r = github.refresh(conn, NOW)
    assert r["checked"] == 0 and "gh" in r["error"] and github.status(conn)["gh_available"] is False
